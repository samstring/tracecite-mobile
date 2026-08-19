"""iOS/Android device collection, session, capture, and archive commands."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..shared.constants import (
    DEFAULT_ARCHIVE_INTERVAL_SEC,
    DEFAULT_CAPTURE_OUTPUT_DIR,
    DEFAULT_HOT_WINDOW_SEC,
    DEFAULT_LOG_OUTPUT_DIR,
    DEFAULT_OUTPUT_ROOT_DIR,
    DEFAULT_RUN_OUTPUT_DIR,
)

from ..shared.config import load_project_profile
from ..shared.command_run import CommandRun
from ..shared.log_paths import resolve_runs_dir
from ..platforms.base import BackendError, UnsupportedCapabilityError
from ..platforms.registry import get_backend
from ..platforms.models import (
    Capabilities,
    DeviceRef,
)

_DEFAULT_LOG_OUTPUT_DIR_STR = str(DEFAULT_LOG_OUTPUT_DIR)
_DEFAULT_CAPTURE_OUTPUT_DIR_STR = str(DEFAULT_CAPTURE_OUTPUT_DIR)


def _json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _new_device_run(
    operation: str,
    *,
    platform: str,
    parameters: Optional[dict[str, Any]] = None,
    run_root: Optional[Path] = None,
) -> CommandRun:
    command_run = CommandRun(
        name=operation,
        kind="device_collection",
        platform=platform,
        run_root=run_root or DEFAULT_RUN_OUTPUT_DIR,
        parameters={"operation": operation, **(parameters or {})},
    )
    command_run.freeze_project_context(Path.cwd(), platform=platform)
    return command_run


def _run_root_for_output(output_dir: Path, *, platform: str = "ios") -> Path:
    """默认 layout runs；显式重定向输出目录时 colocate .runs。"""
    resolved = Path(output_dir).expanduser().resolve()
    default_log = DEFAULT_LOG_OUTPUT_DIR.expanduser().resolve()
    if resolved == default_log or default_log in resolved.parents:
        return resolve_runs_dir(platform)
    default_root = DEFAULT_OUTPUT_ROOT_DIR.expanduser().resolve()
    if resolved == default_root or default_root in resolved.parents:
        return resolve_runs_dir(platform)
    return resolved / ".runs"


def _finish_device_run(
    command_run: CommandRun,
    payload: Any,
    *,
    artifacts: tuple[tuple[Optional[Path | str], str], ...] = (),
) -> dict[str, Any]:
    ready = _json_ready(payload)
    for path, role in artifacts:
        command_run.add_artifact(path, role=role)
    report_path = command_run.write_json_artifact(
        "operation_result.json", ready, role="operation_result"
    )
    fields = command_run.complete(
        metrics={"artifact_count": len(command_run.run.artifacts)}
    )
    result = dict(ready) if isinstance(ready, dict) else {"result": ready}
    result.update(fields)
    result["report_path"] = str(report_path)
    return result


def _add_device_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", metavar="NAME", help="按设备名（模糊匹配）选择")
    parser.add_argument("-u", "--udid", metavar="UDID", help="按 UDID 选择设备")
    parser.add_argument("-i", "--index", type=int, metavar="N", help="按 list 输出的序号选择设备")
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="多台设备时不交互选择，必须指定 --device / --udid / --index",
    )


def _add_capture_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        help=f"输出目录，默认取 profile 或 {_DEFAULT_CAPTURE_OUTPUT_DIR_STR}",
    )
    _add_device_args(parser)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")



def register_device_commands(sub: argparse._SubParsersAction) -> None:
    """注册设备域命令参数。"""
    list_parser = sub.add_parser("list", help="列出已连接的真机")
    list_parser.add_argument("--json", action="store_true", help="以 JSON 数组输出")

    stream_parser = sub.add_parser("stream", help="前台采集设备日志")
    stream_parser.add_argument("process_name", nargs="?", help="进程名过滤，默认取 profile")
    stream_parser.add_argument(
        "output_dir",
        nargs="?",
        help=f"输出目录，默认取 profile 或 {_DEFAULT_LOG_OUTPUT_DIR_STR}",
    )
    stream_parser.add_argument(
        "subsystem",
        nargs="?",
        help="subsystem 过滤，默认取 profile；all 表示不过滤",
    )
    stream_parser.add_argument("-d", "--date", action="store_true", help="文件名带时间戳")
    _add_device_args(stream_parser)
    stream_parser.add_argument("-o", "--output-file", metavar="PATH", help="指定输出文件路径")
    stream_parser.add_argument("--no-stdout", action="store_true", help="不镜像输出到终端，仅写文件")
    stream_parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    stream_parser.add_argument(
        "--hot-window-sec",
        type=int,
        default=None,
        metavar="SEC",
        help=f"hot 日志保留秒数，更早 rewind 到 .archive（默认取项目配置 hot_window_sec，否则 {DEFAULT_HOT_WINDOW_SEC}）",
    )

    capture_parser = sub.add_parser(
        "capture", help="性能采集兼容命令：start 开始 / stop 结束"
    )
    capture_sub = capture_parser.add_subparsers(dest="capture_command", required=True)

    start_parser = capture_sub.add_parser("start", help="开始录制（后台运行）")
    start_parser.add_argument(
        "-t", "--template", help="兼容别名；等价于 performance start --profile"
    )
    start_parser.add_argument("--attach", help="attach 进程名，默认取 profile")
    start_parser.add_argument(
        "--launch",
        metavar="BUNDLE_ID",
        help="显式重启并 attach Bundle ID；未传时始终默认 attach 已在运行的 App",
    )
    start_parser.add_argument("--no-summarize", action="store_true", help="stop 时不做 hang 自动总结")
    start_parser.add_argument(
        "--prompt", action="store_true", help="允许平台采集器弹出权限或确认对话框"
    )
    _add_capture_common_args(start_parser)

    stop_parser = capture_sub.add_parser("stop", help="结束录制并导出、总结")
    stop_parser.add_argument("--no-summarize", action="store_true", help="不做 hang 自动总结")
    _add_capture_common_args(stop_parser)

    status_parser = capture_sub.add_parser("status", help="查看当前是否在录制")
    _add_capture_common_args(status_parser)

    performance_parser = sub.add_parser(
        "performance", help="性能 profile 采集：start / status / stop"
    )
    performance_sub = performance_parser.add_subparsers(
        dest="performance_command", required=True
    )

    performance_profiles = performance_sub.add_parser(
        "profiles", help="列出平台支持的性能 profile"
    )
    performance_profiles.add_argument("--json", action="store_true", help="以 JSON 输出")

    performance_start = performance_sub.add_parser(
        "start", help="按 profile 开始性能采集（后台运行）"
    )
    performance_start.add_argument("--profile", required=True, help="性能 profile 名称")
    performance_start.add_argument("--attach", help="可选进程名")
    performance_start.add_argument("--launch", metavar="APP", help="可选应用标识")
    performance_start.add_argument("--prompt", action="store_true")
    performance_start.add_argument("--no-summarize", action="store_true")
    _add_capture_common_args(performance_start)

    performance_stop = performance_sub.add_parser(
        "stop", help="结束性能采集并导出结果"
    )
    performance_stop.add_argument("--no-summarize", action="store_true")
    _add_capture_common_args(performance_stop)

    performance_status = performance_sub.add_parser(
        "status", help="查看性能采集状态"
    )
    _add_capture_common_args(performance_status)

    session_parser = sub.add_parser("session", help="后台日志 session 管理")
    session_sub = session_parser.add_subparsers(dest="session_command", required=True)

    session_start = session_sub.add_parser("start", help="后台启动日志采集")
    _add_device_args(session_start)
    session_start.add_argument(
        "--all",
        action="store_true",
        dest="all_devices",
        help="对所有已连接真机启动 session",
    )
    session_start.add_argument(
        "--indices",
        metavar="LIST",
        help="逗号分隔的设备序号，如 1,2",
    )
    session_start.add_argument(
        "--hot-window-sec",
        type=int,
        default=None,
        metavar="SEC",
        help=f"hot 日志保留秒数（默认取项目配置 hot_window_sec，否则 {DEFAULT_HOT_WINDOW_SEC}）",
    )
    session_start.add_argument(
        "--output-dir",
        help=f"日志输出目录，默认取 profile 或 {_DEFAULT_LOG_OUTPUT_DIR_STR}",
    )
    session_start.add_argument("-d", "--date", action="store_true", help="文件名带时间戳")
    session_start.add_argument("-o", "--output-file", metavar="PATH", help="指定输出文件路径（仅单设备）")
    session_start.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    session_stop = session_sub.add_parser("stop", help="停止后台日志采集")
    session_stop.add_argument(
        "--output-dir",
        help=f"日志输出目录，默认取 profile 或 {_DEFAULT_LOG_OUTPUT_DIR_STR}",
    )
    session_stop.add_argument("--udid", metavar="UDID", help="只停止指定 UDID 的 session")
    session_stop.add_argument("--device", metavar="NAME", help="按设备名选择 session")
    session_stop.add_argument("--index", type=int, metavar="N", help="按设备序号选择 session")
    session_stop.add_argument("--indices", metavar="LIST", help="逗号分隔的设备序号，如 1,2")
    session_stop.add_argument(
        "--no-interactive", action="store_true", help="禁止交互式设备选择"
    )
    session_stop.add_argument(
        "--all",
        action="store_true",
        dest="stop_all",
        help="停止全部 session",
    )
    session_stop.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    session_status = session_sub.add_parser("status", help="查看后台日志 session 状态")
    session_status.add_argument(
        "--output-dir",
        help=f"日志输出目录，默认取 profile 或 {_DEFAULT_LOG_OUTPUT_DIR_STR}",
    )
    session_status.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    archive_parser = sub.add_parser(
        "archive",
        help="长监听 archive：list / pull / rotate（查 >hot 窗口的旧日志）",
    )
    archive_sub = archive_parser.add_subparsers(dest="archive_command", required=True)

    archive_list = archive_sub.add_parser("list", help="列出 archive 段")
    archive_list.add_argument(
        "--output-dir",
        help=f"日志输出目录，默认取 profile 或 {_DEFAULT_LOG_OUTPUT_DIR_STR}",
    )
    archive_list.add_argument("--device", metavar="NAME", help="设备名（sanitize 后目录名也可）")
    archive_list.add_argument("--json", action="store_true", help="以 JSON 输出")

    archive_pull = archive_sub.add_parser(
        "pull",
        help="按时间窗拼出只读文件（供 filter）；可含当前 hot",
    )
    archive_pull.add_argument(
        "--output-dir",
        help=f"日志输出目录，默认取 profile 或 {_DEFAULT_LOG_OUTPUT_DIR_STR}",
    )
    archive_pull.add_argument("--device", metavar="NAME", required=True, help="设备名")
    archive_pull.add_argument("--since", metavar="TIME", required=True, help="起始时间")
    archive_pull.add_argument("--until", metavar="TIME", required=True, help="结束时间")
    archive_pull.add_argument(
        "--hot",
        metavar="PATH",
        help="一并纳入的 hot 日志路径；默认尝试 session 中该设备的 output_path",
    )
    archive_pull.add_argument("--out", metavar="PATH", help="指定拼窗输出路径")
    archive_pull.add_argument("--json", action="store_true", help="以 JSON 输出")

    archive_rotate = archive_sub.add_parser(
        "rotate",
        help="手动将 hot 日志中超出窗口的部分 rewind 到 archive",
    )
    archive_rotate.add_argument("hot_path", help="hot 日志文件路径")
    archive_rotate.add_argument("--device", metavar="NAME", help="设备名（默认从文件名推断）")
    archive_rotate.add_argument(
        "--hot-window-sec",
        type=int,
        default=None,
        metavar="SEC",
        help=f"hot 窗口秒数（默认 {DEFAULT_HOT_WINDOW_SEC}）",
    )
    archive_rotate.add_argument("--json", action="store_true", help="以 JSON 输出")


def _print_backend_payload(payload: Any) -> None:
    """把插件后端返回的 dataclass / Path 转为稳定 JSON。"""
    print(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2, default=str))


def _resolve_backend_device(backend: Any, args: argparse.Namespace):
    return backend.resolve_device(
        udid=getattr(args, "udid", None),
        name=getattr(args, "device", None),
        index=getattr(args, "index", None),
        interactive=not getattr(args, "no_interactive", False),
    )


def _parse_indices(raw: Optional[str]) -> Optional[list[int]]:
    if not raw:
        return None
    indices: list[int] = []
    for item in str(raw).split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) <= 0:
            raise BackendError(f"非法 --indices 项: {item!r}")
        indices.append(int(item))
    return indices or None


def _resolve_backend_devices(backend: Any, args: argparse.Namespace) -> list[Any]:
    """Resolve every selector through the backend, including multi-device ones."""

    indices = _parse_indices(getattr(args, "indices", None))
    index = getattr(args, "index", None)
    if index is not None:
        indices = (indices or []) + [int(index)]
    udids = [args.udid] if getattr(args, "udid", None) else None
    return backend.resolve_devices(
        udids=udids,
        name=getattr(args, "device", None),
        indices=indices,
        all_devices=bool(getattr(args, "all_devices", False)),
        interactive=not getattr(args, "no_interactive", False),
    )


def _backend_capabilities(backend: Any) -> Capabilities:
    """Read capabilities once per command and reject legacy backends explicitly."""

    capabilities = backend.capabilities()
    if not isinstance(capabilities, Capabilities):
        raise BackendError(
            f"平台 {getattr(backend, 'platform', '?')!r} 返回了无效 capabilities；"
            "请升级到 PlatformBackend 能力协议"
        )
    return capabilities


def _require_capability(
    backend: Any, capabilities: Capabilities, field: str, operation: str
) -> None:
    if not bool(getattr(capabilities, field, False)):
        raise UnsupportedCapabilityError(f"{operation}（{field}）")


def _require_method(backend: Any, name: str, operation: str):
    method = getattr(backend, name, None)
    if not callable(method):
        raise BackendError(
            f"平台 {getattr(backend, 'platform', '?')!r} 未实现 {operation}；"
            f"请迁移到 PlatformBackend.{name}"
        )
    return method


def _backend_output_path(
    output_dir: Path,
    device: DeviceRef,
    *,
    include_date: bool = False,
    output_file: Optional[Path] = None,
) -> Path:
    """Build a stable path without importing an iOS/Android path helper."""

    if output_file is not None:
        output_file = Path(output_file).expanduser()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        return output_file
    identity = device.name if device.platform == "ios" else device.identifier
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", identity or device.name).strip("_")
    stamp = f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}" if include_date else ""
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    if device.platform == "ios":
        filename = f"ios_live_{safe}{stamp}.log"
    elif device.platform == "android":
        filename = f"android_live_{safe}{stamp}.log"
    else:
        filename = f"tracecite_{device.platform}_{safe}{stamp}.log"
    return output_dir / filename


def _profile_for_backend(platform: str):
    return load_project_profile(Path.cwd(), platform=platform)


def _profile_package(profile: Any) -> str:
    return str(
        getattr(profile, "package_name", "")
        or getattr(profile, "process_name", "")
        or ""
    )


def _session_payload(result: Any, *, started: Optional[bool] = None) -> dict[str, Any]:
    ready = _json_ready(result)
    if isinstance(ready, dict):
        payload = dict(ready)
    else:
        payload = {"result": ready}
    if started is not None:
        payload.setdefault("started", started)
    sessions = payload.get("sessions")
    if isinstance(sessions, list):
        flattened = []
        for session in sessions:
            if not isinstance(session, dict):
                flattened.append(session)
                continue
            item = dict(session)
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    item.setdefault(str(key), value)
            device = item.get("device")
            if isinstance(device, dict):
                item.setdefault("device_name", device.get("name", ""))
                item.setdefault(
                    "device_udid", device.get("identifier", device.get("udid", ""))
                )
                item.setdefault("device_model", device.get("model", ""))
            flattened.append(item)
        payload["sessions"] = flattened
        sessions = flattened
        payload.setdefault("session_count", len(sessions))
        if len(sessions) == 1:
            payload.setdefault("session", sessions[0])
    return payload


def _performance_payload(result: Any, *, active: Optional[bool] = None) -> dict[str, Any]:
    ready = _json_ready(result)
    payload = dict(ready) if isinstance(ready, dict) else {"result": ready}
    if active is not None:
        payload.setdefault("active", active)
    if isinstance(payload.get("session"), dict):
        payload["session"].setdefault("alive", bool(active))
    return payload


def _flatten_device_compatibility(payload: dict[str, Any]) -> dict[str, Any]:
    """Add legacy top-level device keys while retaining stable nested models."""

    device = payload.get("device")
    if isinstance(device, dict):
        identifier = device.get("identifier", device.get("udid", ""))
        payload.setdefault("device_udid", identifier)
        payload.setdefault("device_name", device.get("name", ""))
        payload.setdefault("model", device.get("model", ""))
        if device.get("platform") == "android":
            payload.setdefault("serial", identifier)
    return payload


def _canonical_performance_profile(
    capabilities: Capabilities, requested: str
) -> str:
    """Map a historical template alias to a public profile name."""

    name = str(requested)
    if name in capabilities.performance_profiles:
        return name
    options = capabilities.platform_options.get("performance_profiles", {})
    if isinstance(options, dict):
        for public_name, alias in options.items():
            if name == alias:
                return str(public_name)
    # Android's first-generation config used perfetto-* names; the backend
    # advertises the corresponding public profile names in capabilities.
    if name.startswith("perfetto-") and name.removeprefix("perfetto-") in capabilities.performance_profiles:
        return name.removeprefix("perfetto-")
    return name


def _backend_archive_device(backend: Any, args: argparse.Namespace):
    # Archive owners may be offline; the backend receives the stable selector
    # as a name/UDID instead of forcing a live device lookup.
    name = getattr(args, "device", None)
    udid = getattr(args, "udid", None)
    if not name and not udid:
        return None
    identifier = str(udid or name)
    return DeviceRef(
        platform=str(getattr(backend, "platform", "")),
        identifier=identifier,
        name=str(name or identifier),
    )


def _dispatch_backend(args: argparse.Namespace) -> int:
    """统一通过 PlatformBackend 驱动 iOS、Android 和第三方平台。"""

    platform = str(getattr(args, "platform", "ios") or "ios")
    command = args.command
    command_run: Optional[CommandRun] = None
    try:
        backend = get_backend(platform)
        capabilities = _backend_capabilities(backend)
        if command == "list":
            _require_capability(backend, capabilities, "device", "device.list")
            devices = _require_method(backend, "list_devices", "device.list")()
            if args.json:
                if platform == "ios":
                    payload = [
                        {
                            "name": device.name,
                            "udid": device.identifier,
                            "model": device.model,
                        }
                        for device in devices
                    ]
                elif platform == "android":
                    payload = [
                        {
                            "platform": "android",
                            "serial": device.identifier,
                            "name": device.name,
                            "model": device.model,
                            "state": device.state,
                        }
                        for device in devices
                    ]
                else:
                    payload = _json_ready(devices)
                _print_backend_payload(payload)
            elif not devices:
                print("没有已连接的设备。")
                return 1
            else:
                print("已连接设备：\n")
                for index, device in enumerate(devices, 1):
                    print(device.display(index))
            return 0

        if command == "stream":
            _require_capability(backend, capabilities, "log", "log.stream")
            profile = _profile_for_backend(platform)
            device = _resolve_backend_device(backend, args)
            output_dir = (
                Path(args.output_dir).expanduser()
                if args.output_dir
                else profile.log_output_dir
            )
            output_path = _backend_output_path(
                output_dir,
                device,
                include_date=bool(args.date),
                output_file=Path(args.output_file).expanduser()
                if args.output_file
                else None,
            )
            command_run = _new_device_run(
                "stream",
                platform=platform,
                run_root=_run_root_for_output(output_dir, platform=platform),
                parameters={
                    "device_udid": device.identifier,
                    "device_name": device.name,
                    "output_path": str(output_path),
                },
            )
            result = _require_method(backend, "stream_logs", "log.stream")(
                device,
                package=args.process_name or _profile_package(profile),
                output_path=output_path,
                also_stdout=not args.no_stdout,
                subsystem=args.subsystem or getattr(profile, "subsystem", "all"),
                hot_window_sec=(
                    getattr(args, "hot_window_sec", None)
                    or getattr(profile, "hot_window_sec", None)
                    or DEFAULT_HOT_WINDOW_SEC
                ),
                archive_interval_sec=DEFAULT_ARCHIVE_INTERVAL_SEC,
            )
            payload = _finish_device_run(
                command_run,
                _json_ready(result),
                artifacts=((output_path, "device_log"),),
            )
            if getattr(args, "json", False):
                _print_backend_payload(payload)
            else:
                print(f"日志采集完成: {output_path}")
                print(f"manifest: {payload['manifest_path']}")
            return 0

        if command == "session":
            _require_capability(backend, capabilities, "log", "log.session")
            profile = _profile_for_backend(platform)
            output_dir = (
                Path(args.output_dir).expanduser()
                if getattr(args, "output_dir", None)
                else profile.log_output_dir
            )
            subcommand = args.session_command
            if subcommand == "status":
                result = _require_method(backend, "list_sessions", "log.list_sessions")(
                    output_dir=output_dir
                )
                payload = _session_payload(result)
                if args.json:
                    _print_backend_payload(payload)
                else:
                    sessions = payload.get("sessions") or []
                    print(f"日志 Session 状态（{len(sessions)} 台）：")
                    for session in sessions:
                        print(
                            f"  - {session.get('device', {}).get('name', session.get('device_name', ''))}: "
                            f"{session.get('state', 'unknown')}"
                        )
                return 0

            selected_devices = None
            if subcommand == "start":
                selected_devices = _resolve_backend_devices(backend, args)
                if len(selected_devices) > 1 and not capabilities.multi_device_session:
                    raise UnsupportedCapabilityError("log.multi_device_session")
                if len(selected_devices) > 1 and getattr(args, "output_file", None):
                    raise BackendError("多设备 session start 不能使用 --output-file")
            elif subcommand == "stop":
                has_selector = any(
                    getattr(args, name, None) is not None
                    for name in ("udid", "device", "index", "indices")
                )
                if has_selector:
                    # A stopped device need not still be connected.  A bare UDID
                    # is already a stable DeviceRef; name/index selectors still
                    # require live backend resolution.
                    if (
                        getattr(args, "udid", None)
                        and not getattr(args, "device", None)
                        and getattr(args, "index", None) is None
                        and not getattr(args, "indices", None)
                    ):
                        selected_devices = [
                            DeviceRef(
                                platform=platform,
                                identifier=str(args.udid),
                                name=str(args.udid),
                            )
                        ]
                    else:
                        selected_devices = _resolve_backend_devices(backend, args)
                    if len(selected_devices) > 1 and not capabilities.multi_device_session:
                        raise UnsupportedCapabilityError("log.multi_device_session")
                elif getattr(args, "stop_all", False) and not capabilities.multi_device_session:
                    raise UnsupportedCapabilityError("log.multi_device_session")
            else:
                raise BackendError(f"未知 session 子命令: {subcommand}")

            operation = f"session-{subcommand}"
            command_run = _new_device_run(
                operation,
                platform=platform,
                run_root=_run_root_for_output(output_dir, platform=platform),
                parameters={
                    "output_dir": str(output_dir),
                    "device_udids": [d.identifier for d in selected_devices or []],
                    "all_devices": bool(getattr(args, "all_devices", False)
                                         or getattr(args, "stop_all", False)),
                },
            )
            if subcommand == "start":
                result = _require_method(backend, "start_sessions", "log.start_sessions")(
                    selected_devices or [],
                    package=_profile_package(profile),
                    output_dir=output_dir,
                    include_date=bool(getattr(args, "date", False)),
                    output_file=Path(args.output_file).expanduser()
                    if getattr(args, "output_file", None)
                    else None,
                    hot_window_sec=getattr(args, "hot_window_sec", None),
                    archive_interval_sec=DEFAULT_ARCHIVE_INTERVAL_SEC,
                )
                payload = _session_payload(result, started=True)
            else:
                result = _require_method(backend, "stop_sessions", "log.stop_sessions")(
                    devices=selected_devices,
                    all_devices=bool(getattr(args, "stop_all", False)),
                    output_dir=output_dir,
                )
                payload = _session_payload(result, started=False)
                payload["stopped"] = True
            artifacts: list[tuple[Optional[Path | str], str]] = []
            if subcommand == "start":
                # Both logs are live writers at session start.  Registering
                # their current size/hash would make a passed manifest
                # unverifiable as soon as the collector receives another
                # record.  They are intentionally delivered by session-stop,
                # after the backend has confirmed the collector exited and the
                # files are stable.
                payload.setdefault("warnings", []).append(
                    "session start manifest omits live device/collector logs; "
                    "they are registered after a successful session stop"
                )
            else:
                for session in payload.get("sessions") or []:
                    if isinstance(session, dict):
                        artifacts.append((session.get("output_path"), "device_log"))
                        artifacts.append((session.get("stream_log_path"), "collector_log"))
            payload.update(_finish_device_run(command_run, payload, artifacts=tuple(artifacts)))
            if args.json:
                _print_backend_payload(payload)
            else:
                print(
                    f"日志 session {'已启动' if subcommand == 'start' else '已停止'} "
                    f"（{len(payload.get('sessions') or [])} 台）。"
                )
                print(f"manifest: {payload['manifest_path']}")
            return 0

        if command in {"performance", "capture"}:
            subcommand = (
                args.performance_command
                if command == "performance"
                else args.capture_command
            )
            _require_capability(backend, capabilities, "performance", "performance")
            if subcommand == "profiles":
                profiles = _require_method(
                    backend,
                    "list_performance_profiles",
                    "performance.profiles",
                )()
                if args.json:
                    _print_backend_payload(profiles)
                else:
                    for item in profiles:
                        print(f"{item.name}: {item.description}".rstrip())
                return 0
            profile = _profile_for_backend(platform)
            output_dir = (
                Path(args.output_dir).expanduser()
                if args.output_dir
                else profile.capture_output_dir
            )
            if subcommand == "status":
                result = _require_method(
                    backend, "get_performance_status", "performance.status"
                )(output_dir=output_dir)
                payload = _performance_payload(result)
                if command == "capture":
                    # Keep the historical capture status envelope while the
                    # public performance command uses the stable status model.
                    session = payload.get("session")
                    payload.setdefault("active", payload.get("state") in {"running", "active"})
                    if isinstance(session, dict):
                        for key, value in session.items():
                            payload.setdefault(key, value)
                        payload.setdefault("template", session.get("profile"))
                    _flatten_device_compatibility(payload)
                if args.json:
                    _print_backend_payload(payload)
                else:
                    print(f"性能采集状态: {payload.get('state', 'unknown')}")
                return 0
            command_run = _new_device_run(
                f"{command}-{subcommand}",
                platform=platform,
                run_root=_run_root_for_output(output_dir, platform=platform),
                parameters={"output_dir": str(output_dir)},
            )
            if subcommand == "start":
                if getattr(args, "attach", None) and getattr(args, "launch", None):
                    raise BackendError("--attach 与 --launch 不能同时使用")
                device = _resolve_backend_device(backend, args)
                requested_profile = (
                    getattr(args, "profile", None)
                    or getattr(args, "template", None)
                    or getattr(profile, "capture_template", None)
                    or "default"
                )
                if command == "capture":
                    requested_profile = _canonical_performance_profile(
                        capabilities, requested_profile
                    )
                result = _require_method(
                    backend, "start_performance", "performance.start"
                )(
                    device,
                    profile=requested_profile,
                    output_dir=output_dir,
                    attach=getattr(args, "attach", None),
                    launch=getattr(args, "launch", None),
                    prompt=bool(getattr(args, "prompt", False)),
                    no_summarize=bool(getattr(args, "no_summarize", False)),
                )
                payload = _performance_payload(result, active=True)
            elif subcommand == "stop":
                context_log_path: Optional[Path] = None
                if capabilities.log:
                    try:
                        status = _require_method(
                            backend, "list_sessions", "log.list_sessions"
                        )(output_dir=Path(profile.log_output_dir))
                        status_ready = _json_ready(status)
                        status_items = (
                            status_ready.get("sessions", [])
                            if isinstance(status_ready, dict)
                            else []
                        )
                        for item in status_items:
                            candidate = (
                                item.get("output_path")
                                if isinstance(item, dict)
                                else None
                            )
                            if candidate and Path(candidate).is_file():
                                context_log_path = Path(candidate)
                                command_run.freeze_input(
                                    context_log_path, role="context_log_snapshot"
                                )
                                break
                    except Exception:
                        # Performance stop remains valid when log status is
                        # unavailable; the backend itself decides whether the
                        # optional context is required.
                        context_log_path = None
                result = _require_method(
                    backend, "stop_performance", "performance.stop"
                )(
                    output_dir=output_dir,
                    context_log_path=context_log_path,
                    no_summarize=bool(getattr(args, "no_summarize", False)),
                )
                payload = _performance_payload(result, active=False)
                if command == "capture" and payload.get("context_path"):
                    payload.setdefault("log_path", payload["context_path"])
            else:
                raise BackendError(f"未知 performance 子命令: {subcommand}")
            if command == "capture" and subcommand == "start":
                legacy_session = dict(payload)
                legacy_session.pop("active", None)
                legacy_session["alive"] = True
                legacy_session.setdefault("template", requested_profile)
                if legacy_session.get("output_path"):
                    legacy_session.setdefault("local_trace_path", legacy_session["output_path"])
                    legacy_session.setdefault("trace_path", legacy_session["output_path"])
                _flatten_device_compatibility(legacy_session)
                payload = {
                    "active": True,
                    "session": legacy_session,
                    "platform": platform,
                }
            _flatten_device_compatibility(payload)
            trace_path = payload.get("trace_path") or payload.get("output_path")
            artifact_items: list[tuple[Optional[Path | str], str]] = []
            if subcommand == "stop":
                artifact_items.append((trace_path, "performance_trace"))
            metadata_role = "trace_metadata" if command == "capture" else "performance_metadata"
            summary_role = "capture_summary" if command == "capture" else "performance_summary"
            artifact_items.extend(
                [
                    (payload.get("metadata_path"), metadata_role),
                    (payload.get("summary_path"), summary_role),
                ]
            )
            payload.update(
                _finish_device_run(
                    command_run,
                    payload,
                    artifacts=tuple(artifact_items),
                )
            )
            if args.json:
                _print_backend_payload(payload)
            else:
                print(
                    f"性能采集{'已启动' if subcommand == 'start' else '已停止'}。"
                )
                print(f"manifest: {payload['manifest_path']}")
            return 0

        if command == "archive":
            profile = _profile_for_backend(platform)
            output_dir = (
                Path(args.output_dir).expanduser()
                if getattr(args, "output_dir", None)
                else profile.log_output_dir
            )
            subcommand = args.archive_command
            if subcommand in {"list", "pull"}:
                _require_capability(backend, capabilities, "archive", "archive")
            if subcommand == "list":
                device = _backend_archive_device(backend, args)
                segments = _require_method(
                    backend, "list_archive_segments", "archive.list_archive_segments"
                )(
                    device=device,
                    device_name=getattr(args, "device", None),
                    output_dir=output_dir,
                )
                ready_segments = _json_ready(segments)
                grouped: dict[str, dict[str, Any]] = {}
                for segment in ready_segments:
                    if not isinstance(segment, dict):
                        continue
                    device_info = segment.get("device")
                    device_name = (
                        device_info.get("name")
                        if isinstance(device_info, dict)
                        else None
                    ) or getattr(args, "device", None) or "device"
                    group = grouped.setdefault(
                        str(device_name),
                        {"segment_count": 0, "segments": [], "archive_dir": ""},
                    )
                    group["segments"].append(segment)
                    group["segment_count"] += 1
                    if not group["archive_dir"]:
                        path = str(segment.get("path") or "")
                        group["archive_dir"] = str(Path(path).parent) if path else ""
                payload = {
                    "segments": ready_segments,
                    "segment_count": len(segments),
                }
                if grouped:
                    payload["devices"] = grouped
                if args.json:
                    _print_backend_payload(payload)
                else:
                    for segment in payload["segments"]:
                        print(
                            f"{segment.get('start', '')} → {segment.get('end', '')} "
                            f"{segment.get('path', '')}"
                        )
                return 0
            if subcommand == "pull":
                _require_capability(backend, capabilities, "log_window", "archive.fetch_log_window")
                device = _backend_archive_device(backend, args)
                command_run = _new_device_run(
                    "archive-pull",
                    platform=platform,
                    run_root=_run_root_for_output(output_dir, platform=platform),
                    parameters={
                        "device": getattr(args, "device", None),
                        "since": args.since,
                        "until": args.until,
                    },
                )
                result = _require_method(
                    backend, "fetch_log_window", "archive.fetch_log_window"
                )(
                    device=device,
                    device_name=getattr(args, "device", None),
                    time_from=args.since,
                    time_to=args.until,
                    output_dir=output_dir,
                    log_output_dir=output_dir,
                    hot_path=Path(args.hot).expanduser()
                    if getattr(args, "hot", None)
                    else None,
                    output_path=Path(args.out).expanduser()
                    if getattr(args, "out", None)
                    else None,
                )
                payload = _json_ready(result)
                payload = _finish_device_run(
                    command_run,
                    payload,
                    artifacts=((payload.get("output_path"), "archive_pull"),),
                )
            elif subcommand == "rotate":
                _require_capability(backend, capabilities, "archive", "archive")
                rotate = getattr(backend, "rotate_log", None)
                if not callable(rotate):
                    raise UnsupportedCapabilityError("archive.rotate_log")
                device = _backend_archive_device(backend, args)
                hot_path = Path(args.hot_path).expanduser()
                command_run = _new_device_run(
                    "archive-rotate",
                    platform=platform,
                    run_root=_run_root_for_output(hot_path.parent, platform=platform),
                    parameters={"hot_path": str(hot_path)},
                )
                result = rotate(
                    hot_path=hot_path,
                    device=device,
                    device_name=getattr(args, "device", None),
                    hot_window_sec=getattr(args, "hot_window_sec", None),
                )
                payload = _json_ready(result)
                payload = _finish_device_run(
                    command_run,
                    payload,
                    artifacts=tuple(
                        (item.get("path"), "archive_segment")
                        for item in payload.get("archived", [])
                        if isinstance(item, dict)
                    ),
                )
            else:
                raise BackendError(f"未知 archive 子命令: {subcommand}")
            if args.json:
                _print_backend_payload(payload)
            else:
                print(f"archive {subcommand} 完成。")
                if command_run is not None:
                    print(f"manifest: {payload['manifest_path']}")
            return 0

        return None  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001 - public CLI must not leak traceback
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: 平台 {platform!r}: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def dispatch_device_command(args: argparse.Namespace) -> Optional[int]:
    """设备域统一入口；所有平台都经过公开 PlatformBackend。"""

    if getattr(args, "command", None) in {
        "list",
        "stream",
        "session",
        "capture",
        "performance",
        "archive",
    }:
        return _dispatch_backend(args)
    return None

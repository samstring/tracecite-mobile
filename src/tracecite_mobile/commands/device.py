"""iOS/Android device collection, session, capture, and archive commands."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional
from tracecite_core.run import RunIntegrityError

from ..shared.constants import (
    DEFAULT_CAPTURE_OUTPUT_DIR,
    DEFAULT_HOT_WINDOW_SEC,
    DEFAULT_LOG_OUTPUT_DIR,
    DEFAULT_OUTPUT_ROOT_DIR,
    DEFAULT_RUN_OUTPUT_DIR,
)

from ..device.archive import (
    ArchiveError,
    list_archive_segments,
    pull_archive_window,
    rotate_hot_log,
)
from ..device.capture import CaptureError, get_capture_status, start_capture, stop_capture
from ..device.devices import (
    DeviceError,
    ensure_process_running,
    list_connected_devices,
    resolve_device,
    resolve_devices,
)
from ..device.session import (
    SessionError,
    get_stream_session_status,
    load_all_sessions,
    load_stream_session,
    start_stream_session,
    start_stream_sessions,
    stop_stream_sessions,
)
from ..device.stream import StreamError, build_output_path, stream_logs
from ..shared.config import ProfileError, load_project_profile
from ..shared.command_run import CommandRun

_DEFAULT_LOG_OUTPUT_DIR_STR = str(DEFAULT_LOG_OUTPUT_DIR)
_DEFAULT_CAPTURE_OUTPUT_DIR_STR = str(DEFAULT_CAPTURE_OUTPUT_DIR)


def _device_name_from_session_path(hot_path: Path, sessions) -> str:
    """Resolve a hot-log owner from session metadata, with a generic fallback."""
    resolved = hot_path.expanduser().resolve()
    for session in sessions:
        if Path(session.output_path).expanduser().resolve() == resolved:
            return session.device_name
    return hot_path.stem or "device"


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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


def _run_root_for_output(output_dir: Path) -> Path:
    """Keep default device runs central; colocate explicitly redirected runs."""
    resolved = Path(output_dir).expanduser().resolve()
    default_root = DEFAULT_OUTPUT_ROOT_DIR.expanduser().resolve()
    if resolved == default_root or default_root in resolved.parents:
        return DEFAULT_RUN_OUTPUT_DIR
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


def _profile_from_cwd(args: argparse.Namespace | None = None):
    return load_project_profile(
        Path.cwd(), platform=getattr(args, "platform", "ios") or "ios"
    )


_PROFILE_SETUP_HINT = """\
提示: 当前目录没有项目配置 `.tracecite/config.json`，正在使用内置默认（未绑定业务进程）。
建议先配置本项目后再采集：
  1) tracecite-mobile profile init
  2) 编辑生成的 `.tracecite/config.json`，至少确认：
     - process_name      # 进程名，默认空=采集全部进程；填你的 App 进程名
     - subsystem         # 如 YourApp.debug.dylib；all=不过滤
     - attach_process    # capture attach，通常同 process_name
     - log_output_dir / capture_output_dir  # 必填输出目录
  3) 排查知识在 `.tracecite/knowledge.<platform>.json`（grow …；目录已写入 .gitignore）
  4) tracecite-mobile profile show / grow show
"""


def _warn_if_using_builtin_profile(profile, *, quiet: bool = False) -> None:
    """无项目 profile 时优先提示用户配置（不阻断命令）。"""
    if quiet or profile.source_path is not None:
        return
    print(_PROFILE_SETUP_HINT.rstrip(), file=sys.stderr)


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
    stream_parser.add_argument(
        "--hot-window-sec",
        type=int,
        default=None,
        metavar="SEC",
        help=f"hot 日志保留秒数，更早 rewind 到 .archive（默认取项目配置 hot_window_sec，否则 {DEFAULT_HOT_WINDOW_SEC}）",
    )

    capture_parser = sub.add_parser("capture", help="Instruments 录制：start 开始 / stop 结束")
    capture_sub = capture_parser.add_subparsers(dest="capture_command", required=True)

    start_parser = capture_sub.add_parser("start", help="开始录制（后台运行）")
    start_parser.add_argument("-t", "--template", help="Instruments 模板，默认取 profile")
    start_parser.add_argument("--attach", help="attach 进程名，默认取 profile")
    start_parser.add_argument(
        "--launch",
        metavar="BUNDLE_ID",
        help="显式重启并 attach Bundle ID；未传时始终默认 attach 已在运行的 App",
    )
    start_parser.add_argument("--no-summarize", action="store_true", help="stop 时不做 hang 自动总结")
    start_parser.add_argument("--prompt", action="store_true", help="允许 xctrace 弹出权限/确认对话框")
    _add_capture_common_args(start_parser)

    stop_parser = capture_sub.add_parser("stop", help="结束录制并导出、总结")
    stop_parser.add_argument("--no-summarize", action="store_true", help="不做 hang 自动总结")
    _add_capture_common_args(stop_parser)

    status_parser = capture_sub.add_parser("status", help="查看当前是否在录制")
    _add_capture_common_args(status_parser)

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
    session_start.add_argument("-d", "--date", action="store_true", help="文件名带时间戳")
    session_start.add_argument("-o", "--output-file", metavar="PATH", help="指定输出文件路径（仅单设备）")
    session_start.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    session_stop = session_sub.add_parser("stop", help="停止后台日志采集")
    session_stop.add_argument(
        "--output-dir",
        help=f"日志输出目录，默认取 profile 或 {_DEFAULT_LOG_OUTPUT_DIR_STR}",
    )
    session_stop.add_argument("--udid", metavar="UDID", help="只停止指定 UDID 的 session")
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


def cmd_list(args: argparse.Namespace) -> int:
    try:
        devices = list_connected_devices()
    except DeviceError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _print_json(
            [
                {"name": device.name, "udid": device.udid, "model": device.model}
                for device in devices
            ]
        )
        return 0

    if not devices:
        print("没有已连接的真机。")
        return 1

    print("已连接真机：\n")
    for index, device in enumerate(devices, start=1):
        print(device.display(index))
    return 0


def _resolve_hot_window_sec(args: argparse.Namespace, profile) -> int:
    """hot 窗口解析优先级：命令行 --hot-window-sec > 项目配置 hot_window_sec > 默认。"""
    cli_val = getattr(args, "hot_window_sec", None)
    if cli_val is not None:
        return max(60, int(cli_val))
    profile_val = getattr(profile, "hot_window_sec", None)
    if profile_val is not None:
        return max(60, int(profile_val))
    return DEFAULT_HOT_WINDOW_SEC


def cmd_stream(args: argparse.Namespace) -> int:
    command_run: Optional[CommandRun] = None
    try:
        platform = getattr(args, "platform", "ios")
        profile = load_project_profile(Path.cwd(), platform=platform)
        _warn_if_using_builtin_profile(profile)
        device = resolve_device(
            udid=args.udid,
            name=args.device,
            index=args.index,
            interactive=not args.no_interactive,
        )
        output_dir = Path(args.output_dir).expanduser() if args.output_dir else profile.log_output_dir
        output_path = build_output_path(
            output_dir,
            device,
            args.date,
            Path(args.output_file).expanduser() if args.output_file else None,
        )
        command_run = _new_device_run(
            "stream",
            platform=platform,
            parameters={
                "device_udid": device.udid,
                "device_name": device.name,
                "process_name": args.process_name or profile.process_name,
                "subsystem": args.subsystem or profile.subsystem,
                "output_path": str(output_path),
            },
        )
        stream_logs(
            device,
            process_name=args.process_name or profile.process_name,
            subsystem_filter=args.subsystem or profile.subsystem,
            output_path=output_path,
            also_stdout=not args.no_stdout,
            hot_window_sec=_resolve_hot_window_sec(args, profile),
        )
        result = _finish_device_run(
            command_run,
            {"output_path": str(output_path), "device": device.name},
            artifacts=((output_path, "device_log"),),
        )
        print(f"manifest: {result['manifest_path']}")
        return 0
    except (DeviceError, ProfileError, StreamError, RunIntegrityError, OSError) as exc:
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def _parse_indices(raw: Optional[str]) -> Optional[list[int]]:
    if not raw:
        return None
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise DeviceError(f"非法 --indices 项: {part!r}")
        out.append(int(part))
    return out or None


def cmd_session(args: argparse.Namespace) -> int:
    command_run: Optional[CommandRun] = None
    try:
        profile = _profile_from_cwd()
        log_output_dir = Path(args.output_dir).expanduser() if getattr(args, "output_dir", None) else profile.log_output_dir

        if args.session_command == "start":
            _warn_if_using_builtin_profile(profile, quiet=bool(getattr(args, "json", False)))

        if args.session_command == "status":
            payload = get_stream_session_status(log_output_dir)
            if args.json:
                _print_json(payload)
            elif not payload.get("sessions"):
                print("当前没有进行中的日志 session。")
            else:
                print(f"日志 Session 状态（{payload['session_count']} 台）：")
                for session in payload["sessions"]:
                    print(f"  - {session['device_name']} ({session.get('device_model', '')})")
                    if session.get("stalled"):
                        state = "否（假存活/已停更，建议 session stop 后重开或直接 session start）"
                    elif session["alive"]:
                        state = "是"
                    else:
                        state = "否"
                    print(f"      进行中: {state}")
                    if session.get("process_alive") and session.get("stalled"):
                        age = session.get("heartbeat_age_sec")
                        age_s = f"{age}s" if age is not None else "无 heartbeat"
                        print(f"      心跳:   停滞 ({age_s})")
                    print(f"      PID:    {session['pid']}")
                    print(f"      UDID:   {session['device_udid']}")
                    print(f"      开始:   {session['started_at']}")
                    print(f"      日志:   {session['output_path']}")
                    print(f"      hot:    {session.get('hot_window_sec', DEFAULT_HOT_WINDOW_SEC)}s")
                    if session.get("archive_dir"):
                        print(f"      archive: {session['archive_dir']}")
                if payload["capture"]:
                    capture = payload["capture"]
                    print(f"  Capture: 进行中（PID {capture['pid']}，模板 {capture['template']}）")
            return 0

        if args.session_command == "stop":
            command_run = _new_device_run(
                "session-stop",
                platform=getattr(args, "platform", "ios") or "ios",
                parameters={"output_dir": str(log_output_dir)},
            )
            stopped = stop_stream_sessions(
                log_output_dir,
                udid=getattr(args, "udid", None),
                stop_all=bool(getattr(args, "stop_all", False)),
            )
            payload = {
                "stopped": True,
                "sessions": [s.to_dict() for s in stopped],
            }
            artifacts: list[tuple[Optional[Path | str], str]] = []
            for session in stopped:
                artifacts.extend(
                    [
                        (session.output_path, "device_log"),
                        (session.stream_log_path, "collector_log"),
                    ]
                )
            payload.update(
                _finish_device_run(
                    command_run,
                    payload,
                    artifacts=tuple(artifacts),
                )
            )
            if args.json:
                _print_json(payload)
            else:
                print(f"已停止 {len(stopped)} 个日志 session：")
                for session in stopped:
                    print(f"  - {session.device_name}: {session.output_path}")
                print(f"manifest: {payload['manifest_path']}")
            return 0

        # start
        indices = _parse_indices(getattr(args, "indices", None))
        if getattr(args, "index", None) is not None:
            indices = (indices or []) + [int(args.index)]
        udids = [args.udid] if getattr(args, "udid", None) else None
        devices = resolve_devices(
            udids=udids,
            name=args.device,
            indices=indices,
            all_devices=bool(getattr(args, "all_devices", False)),
            interactive=not args.no_interactive,
        )
        hot_window = _resolve_hot_window_sec(args, profile)
        platform = getattr(args, "platform", "ios") or "ios"
        if len(devices) > 1 and getattr(args, "output_file", None):
            raise SessionError("多设备 session start 不能使用 --output-file")
        if len(devices) == 1:
            command_run = _new_device_run(
                "session-start",
                platform=platform,
                parameters={
                    "output_dir": str(log_output_dir),
                    "device_udids": [device.udid for device in devices],
                    "hot_window_sec": hot_window,
                },
            )
            sessions = [
                start_stream_session(
                    devices[0],
                    profile,
                    include_date=args.date,
                    output_file=Path(args.output_file) if args.output_file else None,
                    hot_window_sec=hot_window,
                    platform=platform,
                )
            ]
        else:
            command_run = _new_device_run(
                "session-start",
                platform=platform,
                parameters={
                    "output_dir": str(log_output_dir),
                    "device_udids": [device.udid for device in devices],
                    "hot_window_sec": hot_window,
                },
            )
            sessions = start_stream_sessions(
                devices,
                profile,
                include_date=args.date,
                hot_window_sec=hot_window,
                platform=platform,
            )
        payload = {
            "started": True,
            "sessions": [s.to_dict() for s in sessions],
        }
        payload.update(_finish_device_run(command_run, payload))
        if args.json:
            _print_json(payload)
        else:
            print(f"日志 session 已启动（{len(sessions)} 台）：")
            for session in sessions:
                print(f"  - {session.device_name}: {session.output_path}")
                print(f"    管理日志: {session.stream_log_path}")
            print(f"manifest: {payload['manifest_path']}")
        return 0
    except (DeviceError, ProfileError, SessionError, RunIntegrityError, OSError) as exc:
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def cmd_archive(args: argparse.Namespace) -> int:
    command_run: Optional[CommandRun] = None
    try:
        profile = _profile_from_cwd()
        log_output_dir = (
            Path(args.output_dir).expanduser()
            if getattr(args, "output_dir", None)
            else profile.log_output_dir
        )

        if args.archive_command == "list":
            payload = list_archive_segments(
                log_output_dir, device_name=getattr(args, "device", None)
            )
            if args.json:
                _print_json(payload)
            else:
                devices = payload.get("devices") or {}
                if not devices:
                    print("尚无 archive 段。")
                for name, info in devices.items():
                    print(f"{name}: {info['segment_count']} 段 → {info['archive_dir']}")
                    for seg in info.get("segments") or []:
                        print(
                            f"  - {seg['start']} → {seg['end']}  "
                            f"({seg['lines']} lines)  {seg['path']}"
                        )
            return 0

        if args.archive_command == "rotate":
            hot = Path(args.hot_path).expanduser()
            device_name = args.device or _device_name_from_session_path(
                hot, load_all_sessions(log_output_dir).values()
            )
            command_run = _new_device_run(
                "archive-rotate",
                platform=getattr(args, "platform", "ios") or "ios",
                parameters={
                    "device_name": device_name,
                    "hot_window_sec": _resolve_hot_window_sec(args, None),
                },
            )
            command_run.freeze_input(hot, role="hot_log_before_rotate")
            result = rotate_hot_log(
                hot,
                device_name=device_name,
                hot_window_sec=_resolve_hot_window_sec(args, None),
            )
            payload = result.to_dict()
            payload.update(
                _finish_device_run(
                    command_run,
                    payload,
                    artifacts=tuple(
                        (segment.path, "archive_segment")
                        for segment in result.archived
                    ),
                )
            )
            if args.json:
                _print_json(payload)
            else:
                print(f"rotated: {result.rotated}")
                print(f"hot: {result.hot_path} ({result.hot_lines} lines)")
                for seg in result.archived:
                    print(f"archived: {seg.path}")
                print(f"manifest: {payload['manifest_path']}")
            return 0

        # pull
        hot_path = Path(args.hot).expanduser() if getattr(args, "hot", None) else None
        if hot_path is None:
            for session in load_all_sessions(log_output_dir).values():
                if args.device.lower() in session.device_name.lower():
                    hot_path = Path(session.output_path)
                    break
        command_run = _new_device_run(
            "archive-pull",
            platform=getattr(args, "platform", "ios") or "ios",
            parameters={
                "device_name": args.device,
                "since": args.since,
                "until": args.until,
            },
        )
        result = pull_archive_window(
            log_output_dir,
            device_name=args.device,
            since=args.since,
            until=args.until,
            hot_path=hot_path,
            output_path=Path(args.out).expanduser() if getattr(args, "out", None) else None,
        )
        for source in result.segments:
            command_run.freeze_input(Path(source), role="archive_source_snapshot")
        payload = result.to_dict()
        payload.update(
            _finish_device_run(
                command_run,
                payload,
                artifacts=((result.output_path, "archive_pull"),),
            )
        )
        if args.json:
            _print_json(payload)
        else:
            print("archive pull 完成（内部文件，供 filter 使用）：")
            print(f"  output_path: {result.output_path}")
            print(f"  time: {result.time_from} → {result.time_to}")
            print(f"  segments: {len(result.segments)}")
            print(f"  lines: {result.lines}")
            print(f"manifest: {payload['manifest_path']}")
        return 0
    except (ArchiveError, ProfileError, SessionError, RunIntegrityError, OSError) as exc:
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def cmd_capture(args: argparse.Namespace) -> int:
    command_run: Optional[CommandRun] = None
    try:
        profile = _profile_from_cwd()
        if args.capture_command == "start":
            _warn_if_using_builtin_profile(profile, quiet=bool(getattr(args, "json", False)))
        output_dir = Path(args.output_dir).expanduser() if args.output_dir else profile.capture_output_dir

        if args.capture_command == "status":
            payload = get_capture_status(output_dir)
            if args.json:
                _print_json(payload)
            else:
                if payload["session"] is None:
                    print("当前没有进行中的 capture 录制。")
                else:
                    session = payload["session"]
                    print("Capture 状态：")
                    print(f"  进行中: {'是' if session['alive'] else '否（进程已结束，请 capture stop 收尾）'}")
                    print(f"  PID:    {session['pid']}")
                    print(f"  开始:   {session['started_at']}")
                    print(f"  trace:  {session['trace_path']}")
                    print(f"  模板:   {session['template']}")
                    print(f"  Attach: {session['attach']}")
            return 0

        if args.capture_command == "stop":
            command_run = _new_device_run(
                "capture-stop",
                platform=getattr(args, "platform", "ios") or "ios",
                parameters={"output_dir": str(output_dir)},
            )
            # 联合分析指引必须指向真实 stream 输出，而非默认命名推测
            stream_session = load_stream_session(profile.log_output_dir)
            result = stop_capture(
                output_dir,
                summarize=not args.no_summarize,
                quiet=args.json,
                log_path=(
                    Path(stream_session.output_path) if stream_session else None
                ),
            )
            if result.log_path is not None and result.log_path.is_file():
                command_run.freeze_input(result.log_path, role="context_log_snapshot")
            payload = result.to_dict()
            payload.update(
                _finish_device_run(
                    command_run,
                    payload,
                    artifacts=(
                        (result.trace_path, "performance_trace"),
                        (result.toc_path, "trace_toc"),
                    ),
                )
            )
            if args.json:
                _print_json(payload)
            else:
                print(f"manifest: {payload['manifest_path']}")
            return 0

        if args.launch and args.attach:
            print("错误: --launch 与 --attach 不能同时使用。", file=sys.stderr)
            return 1

        device = resolve_device(
            udid=args.udid,
            name=args.device,
            index=args.index,
            interactive=not args.no_interactive,
        )
        if args.launch is None:
            ensure_process_running(device, args.attach or profile.attach_process)
        command_run = _new_device_run(
            "capture-start",
            platform=getattr(args, "platform", "ios") or "ios",
            parameters={
                "device_udid": device.udid,
                "device_name": device.name,
                "template": args.template or profile.capture_template,
                "attach": args.attach or profile.attach_process,
                "launch": args.launch,
                "output_dir": str(output_dir),
            },
        )
        session = start_capture(
            device,
            template=args.template or profile.capture_template,
            attach=args.attach or profile.attach_process,
            launch=args.launch,
            output_dir=output_dir,
            no_prompt=not args.prompt,
            no_summarize=args.no_summarize,
            quiet=args.json,
        )
        payload = {
            "active": True,
            "session": {
                **session.to_dict(),
                "alive": True,
            },
        }
        payload.update(_finish_device_run(command_run, payload))
        if args.json:
            _print_json(payload)
        else:
            print(f"manifest: {payload['manifest_path']}")
        return 0
    except (CaptureError, DeviceError, ProfileError, RunIntegrityError, OSError) as exc:
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


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


def _dispatch_plugin_backend(args: argparse.Namespace) -> int:
    """通过公开 PlatformBackend 协议驱动第三方平台。"""
    from ..platforms.registry import get_backend

    backend = get_backend(args.platform)
    command = args.command
    command_run: Optional[CommandRun] = None
    try:
        if command == "list":
            devices = backend.list_devices()
            if args.json:
                _print_backend_payload(devices)
            else:
                for index, device in enumerate(devices, 1):
                    print(device.display(index))
            return 0

        if command == "stream":
            device = _resolve_backend_device(backend, args)
            output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_LOG_OUTPUT_DIR / args.platform
            output_path = build_output_path(
                output_dir,
                device,
                bool(args.date),
                Path(args.output_file).expanduser() if args.output_file else None,
            )
            command_run = _new_device_run(
                "stream",
                platform=args.platform,
                parameters={"output_path": str(output_path)},
            )
            result = backend.stream_logs(
                device,
                package=args.process_name or "",
                output_path=output_path,
                also_stdout=not args.no_stdout,
                subsystem=args.subsystem or "all",
            )
            payload = _finish_device_run(
                command_run,
                result,
                artifacts=((output_path, "device_log"),),
            )
            _print_backend_payload(payload)
            return 0

        if command == "session":
            output_dir = Path(args.output_dir).expanduser() if getattr(args, "output_dir", None) else DEFAULT_LOG_OUTPUT_DIR / args.platform
            if args.session_command == "status":
                result = backend.get_session_status(output_dir=output_dir)
            elif args.session_command == "stop":
                command_run = _new_device_run(
                    "session-stop",
                    platform=args.platform,
                    parameters={"output_dir": str(output_dir)},
                )
                result = backend.stop_session(output_dir=output_dir)
            elif args.session_command == "start":
                if getattr(args, "all_devices", False) or getattr(args, "indices", None):
                    raise ValueError("第三方平台 session 暂不支持 --all / --indices")
                device = _resolve_backend_device(backend, args)
                command_run = _new_device_run(
                    "session-start",
                    platform=args.platform,
                    parameters={"output_dir": str(output_dir)},
                )
                result = backend.start_session(
                    device,
                    output_dir=output_dir,
                    include_date=bool(args.date),
                    output_file=(
                        Path(args.output_file).expanduser() if args.output_file else None
                    ),
                )
            else:
                raise ValueError(f"未知 session 子命令: {args.session_command}")
            if command_run is not None:
                ready = _json_ready(result)
                output_path = ready.get("output_path") if isinstance(ready, dict) else None
                result = _finish_device_run(
                    command_run,
                    ready,
                    artifacts=(
                        ((output_path if args.session_command == "stop" else None), "device_log"),
                    ),
                )
            _print_backend_payload(result)
            return 0

        if command == "capture":
            output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_CAPTURE_OUTPUT_DIR / args.platform
            if args.capture_command == "status":
                result = backend.get_capture_status(output_dir=output_dir)
            elif args.capture_command == "stop":
                command_run = _new_device_run(
                    "capture-stop",
                    platform=args.platform,
                    parameters={"output_dir": str(output_dir)},
                )
                result = backend.stop_capture(output_dir=output_dir)
            elif args.capture_command == "start":
                device = _resolve_backend_device(backend, args)
                command_run = _new_device_run(
                    "capture-start",
                    platform=args.platform,
                    parameters={"output_dir": str(output_dir)},
                )
                result = backend.start_capture(
                    device,
                    template=args.template or "default",
                    output_dir=output_dir,
                    attach=args.attach,
                    launch=args.launch,
                    prompt=args.prompt,
                    no_summarize=args.no_summarize,
                )
            else:
                raise ValueError(f"未知 capture 子命令: {args.capture_command}")
            if command_run is not None:
                ready = _json_ready(result)
                trace_path = ready.get("trace_path") if isinstance(ready, dict) else None
                metadata_path = ready.get("metadata_path") if isinstance(ready, dict) else None
                result = _finish_device_run(
                    command_run,
                    ready,
                    artifacts=(
                        ((trace_path if args.capture_command == "stop" else None), "performance_trace"),
                        ((metadata_path if args.capture_command == "stop" else None), "trace_metadata"),
                    ),
                )
            _print_backend_payload(result)
            return 0

        raise ValueError(
            f"第三方平台 {args.platform!r} 不支持命令 {command!r} 的当前子命令"
        )
    except Exception as exc:  # 插件异常统一转为 CLI 错误，不泄漏 traceback
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: 平台插件 {args.platform!r}: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def dispatch_device_command(args: argparse.Namespace) -> Optional[int]:
    """按平台和命令分派设备域处理器。"""
    platform = getattr(args, "platform", "ios")
    if platform not in {"ios", "android"} and args.command in {
        "list",
        "stream",
        "session",
        "capture",
        "archive",
    }:
        return _dispatch_plugin_backend(args)
    if platform == "android":
        if args.command in {"list", "stream", "session", "capture"}:
            from ..platforms.android.cli_handlers import android_dispatch

            return android_dispatch(args)

    handlers = {
        "list": cmd_list,
        "stream": cmd_stream,
        "capture": cmd_capture,
        "session": cmd_session,
        "archive": cmd_archive,
    }
    handler = handlers.get(args.command)
    return None if handler is None else handler(args)

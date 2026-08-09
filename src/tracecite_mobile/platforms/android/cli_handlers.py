# -*- coding: utf-8 -*-
"""Android 命令处理器：被 cli.py 在 --platform android 时分派调用。

每个处理器调用 AndroidBackend，输出与 iOS 命令同构的 JSON，便于 Agent 消费。
保持 iOS 命令路径完全不变；此处仅服务 Android 平台。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..registry import get_backend
from ...shared import config as cfg
from .logger import build_log_output_path
from ...shared.command_run import CommandRun
from ...shared.constants import (
    DEFAULT_OUTPUT_ROOT_DIR,
    DEFAULT_RUN_OUTPUT_DIR,
)


def _run_root_for_output(output_dir: Any) -> Path:
    resolved = Path(output_dir).expanduser().resolve()
    default_root = DEFAULT_OUTPUT_ROOT_DIR.expanduser().resolve()
    if resolved == default_root or default_root in resolved.parents:
        return DEFAULT_RUN_OUTPUT_DIR
    return resolved / ".runs"


def _new_run(
    operation: str,
    *,
    run_root: Any = None,
    **parameters: Any,
) -> CommandRun:
    command_run = CommandRun(
        name=operation,
        kind="device_collection",
        platform="android",
        run_root=Path(run_root) if run_root is not None else DEFAULT_RUN_OUTPUT_DIR,
        parameters={"operation": operation, **parameters},
    )
    command_run.freeze_project_context(Path.cwd(), platform="android")
    return command_run


def _finish_run(
    command_run: CommandRun,
    payload: dict[str, Any],
    *,
    artifacts: tuple[tuple[Any, str], ...] = (),
) -> dict[str, Any]:
    for path, role in artifacts:
        command_run.add_artifact(path, role=role)
    report = command_run.write_json_artifact(
        "operation_result.json", payload, role="operation_result"
    )
    payload.update(
        command_run.complete(metrics={"artifact_count": len(command_run.run.artifacts)})
    )
    payload["report_path"] = str(report)
    return payload


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _profile(platform: str = "android"):
    return cfg.load_project_profile(Path.cwd(), platform=platform)


def _resolve(backend, args):
    return backend.resolve_device(
        udid=args.udid,
        name=args.device,
        index=args.index,
        interactive=not args.no_interactive,
    )


def android_list(args) -> int:
    backend = get_backend("android")
    try:
        devices = backend.list_devices()
    except Exception as exc:  # noqa: BLE001
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(
            [
                {
                    "platform": "android",
                    "serial": d.identifier,
                    "name": d.name,
                    "model": d.model,
                    "state": d.state,
                }
                for d in devices
            ]
        )
        return 0
    if not devices:
        print("没有已连接的 Android 设备。")
        return 1
    print("已连接 Android 设备：\n")
    for i, d in enumerate(devices, start=1):
        print(d.display(i))
    return 0


def android_stream(args) -> int:
    backend = get_backend("android")
    command_run = None
    try:
        profile = _profile()
        package = args.process_name or (getattr(profile, "package_name", None) or "")
        output_dir = (
            Path(args.output_dir).expanduser()
            if args.output_dir
            else profile.log_output_dir
        )
        device = _resolve(backend, args)
        output_path = build_log_output_path(
            Path(output_dir),
            device,
            include_date=args.date,
            output_file=Path(args.output_file).expanduser() if args.output_file else None,
        )
        command_run = _new_run(
            "stream",
            serial=device.identifier,
            package=package,
            output_path=str(output_path),
        )
        backend.stream_logs(
            device,
            package=package,
            output_path=output_path,
            also_stdout=not args.no_stdout,
        )
        payload = _finish_run(
            command_run,
            {"output_path": str(output_path), "serial": device.identifier},
            artifacts=((output_path, "device_log"),),
        )
        print(f"manifest: {payload['manifest_path']}")
        return 0
    except Exception as exc:  # noqa: BLE001
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def android_session(args) -> int:
    backend = get_backend("android")
    profile = _profile()
    command_run = None
    try:
        if args.session_command == "status":
            out = args.output_dir or profile.log_output_dir
            payload = backend.get_session_status(output_dir=Path(out))
            if args.json:
                _print_json(payload)
            elif not payload["session"]:
                print("当前没有进行中的日志 session。")
            else:
                s = payload["session"]
                print("日志 Session 状态：")
                print(f"  进行中: {'是' if s['alive'] else '否'}")
                print(f"  设备:   {s['serial']} ({s.get('model','')})")
                print(f"  开始:   {s['started_at']}")
                print(f"  日志:   {s['output_path']}")
                print(f"  包名:   {s.get('package_name','')}")
            return 0
        if args.session_command == "stop":
            out = args.output_dir or profile.log_output_dir
            command_run = _new_run("session-stop", output_dir=str(out))
            sess = backend.stop_session(output_dir=Path(out))
            payload = _finish_run(
                command_run,
                {"stopped": True, "session": sess},
                artifacts=((sess.get("output_path"), "device_log"),),
            )
            if args.json:
                _print_json(payload)
            else:
                print("日志 session 已停止：")
                print(f"  设备: {sess.get('serial')}")
                print(f"  日志: {sess.get('output_path')}")
                print(f"manifest: {payload['manifest_path']}")
            return 0
        # start
        device = _resolve(backend, args)
        command_run = _new_run(
            "session-start",
            serial=device.identifier,
            output_dir=str(profile.log_output_dir),
        )
        state = backend.start_session(
            device,
            package=getattr(profile, "package_name", "") or "",
            output_dir=profile.log_output_dir,
            include_date=args.date,
            output_file=Path(args.output_file) if args.output_file else None,
        )
        payload = _finish_run(
            command_run, {"started": True, "session": state}
        )
        if args.json:
            _print_json(payload)
        else:
            print("日志 session 已启动：")
            print(f"  设备: {state['serial']} ({state.get('model','')})")
            print(f"  日志: {state['output_path']}")
            print(f"manifest: {payload['manifest_path']}")
        return 0
    except Exception as exc:  # noqa: BLE001
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def android_capture(args) -> int:
    backend = get_backend("android")
    profile = _profile()
    command_run = None
    try:
        if args.capture_command == "status":
            out = args.output_dir or profile.capture_output_dir
            payload = backend.get_capture_status(output_dir=Path(out))
            if args.json:
                _print_json(payload)
            elif not payload["session"]:
                print("当前没有进行中的 Perfetto 录制。")
            else:
                s = payload["session"]
                print("Perfetto 状态：")
                print(f"  进行中: {'是' if s['alive'] else '否'}")
                print(f"  模板:   {s['template']}")
                print(f"  开始:   {s['started_at']}")
            return 0
        if args.capture_command == "stop":
            out = args.output_dir or profile.capture_output_dir
            command_run = _new_run("capture-stop", output_dir=str(out))
            result = backend.stop_capture(output_dir=Path(out))
            payload = {
                "trace_path": str(result.trace_path),
                "metadata_path": str(result.metadata_path) if result.metadata_path else None,
                "platform": "android",
                "serial": result.device.identifier,
            }
            payload = _finish_run(
                command_run,
                payload,
                artifacts=(
                    (result.trace_path, "performance_trace"),
                    (result.metadata_path, "trace_metadata"),
                ),
            )
            if args.json:
                _print_json(payload)
            else:
                print("Perfetto 录制已停止：")
                print(f"  trace: {result.trace_path}")
                if result.metadata_path:
                    print(f"  meta:  {result.metadata_path}")
                print(f"manifest: {payload['manifest_path']}")
            return 0
        # start
        device = _resolve(backend, args)
        template = args.template or profile.capture_template
        command_run = _new_run(
            "capture-start",
            serial=device.identifier,
            template=template,
            output_dir=str(profile.capture_output_dir),
        )
        state = backend.start_capture(
            device, template=template, output_dir=profile.capture_output_dir
        )
        payload = _finish_run(
            command_run, {"active": True, "session": {**state, "alive": True}}
        )
        if args.json:
            _print_json(payload)
        else:
            print("Perfetto 录制已启动：")
            print(f"  设备: {state['serial']}")
            print(f"  模板: {state['template']}")
            print(f"  本地: {state['local_trace_path']}")
            print(f"manifest: {payload['manifest_path']}")
        return 0
    except Exception as exc:  # noqa: BLE001
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def android_profile_init(args) -> int:
    try:
        path = cfg.write_profile_template(
            Path.cwd(), overwrite=args.force, platform="android"
        )
        if args.json:
            _print_json({"created": True, "path": str(path)})
        else:
            print(f"已生成 Android 项目配置: {path}")
            print("编辑 package_name / activity / device_serial 后开始采集。")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def android_dispatch(args) -> int:
    """按命令分派到 Android 处理器。"""
    cmd = args.command
    if cmd == "list":
        return android_list(args)
    if cmd == "stream":
        return android_stream(args)
    if cmd == "session":
        return android_session(args)
    if cmd == "capture":
        return android_capture(args)
    if cmd == "profile" and getattr(args, "profile_command", None) == "init":
        return android_profile_init(args)
    print(f"错误: --platform android 暂不支持命令 {cmd}", file=sys.stderr)
    return 1

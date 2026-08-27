"""Seal live hot logs into archive segments before analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from ..device.archive import ArchiveError, request_seal_hot
from ..device.session import SessionError, load_analysis_sessions
from ..shared.command_run import CommandRun
from ..shared.config import ProfileError, load_project_profile
from ..shared.log_paths import infer_device_name_from_hot, resolve_runs_dir


def register_seal_commands(sub: argparse._SubParsersAction) -> None:
    seal_parser = sub.add_parser(
        "seal",
        help="将 live hot 日志 rename 切段进 .archive（Agent 分析前冻结证据）",
    )
    seal_parser.add_argument(
        "hot_path",
        nargs="*",
        help="hot 日志路径；与 --from-sessions 二选一或并用",
    )
    seal_parser.add_argument(
        "--from-sessions",
        action="store_true",
        help="seal 当前全部 active session 的 hot 日志",
    )
    seal_parser.add_argument(
        "--output-dir",
        help="配合 --from-sessions：日志目录，默认取 profile",
    )
    seal_parser.add_argument("--json", action="store_true", help="以 JSON 输出")


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def cmd_seal(args: argparse.Namespace) -> int:
    platform = getattr(args, "platform", "ios")
    command_run: Optional[CommandRun] = None
    try:
        profile = load_project_profile(Path.cwd(), platform=platform)
        raw_paths = getattr(args, "hot_path", None) or []
        paths = [Path(path).expanduser() for path in raw_paths]
        labels: list[str] = []
        if getattr(args, "from_sessions", False):
            log_dir = (
                Path(args.output_dir).expanduser()
                if args.output_dir
                else profile.log_output_dir
            )
            sessions = load_analysis_sessions(log_dir, platform=platform)
            if not sessions:
                raise ArchiveError("当前没有 session；无法使用 --from-sessions")
            for session in sessions.values():
                paths.append(Path(session.output_path))
                labels.append(session.device_name)
        if not paths:
            raise ArchiveError("请提供 hot_path，或使用 --from-sessions")

        seen: set[str] = set()
        unique_paths: list[Path] = []
        unique_labels: list[str] = []
        for index, path in enumerate(paths):
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            unique_paths.append(path)
            unique_labels.append(labels[index] if index < len(labels) else "")

        command_run = CommandRun(
            name="seal",
            kind="device_collection",
            platform=platform,
            run_root=resolve_runs_dir(platform, profile),
            parameters={"from_sessions": bool(getattr(args, "from_sessions", False))},
        )
        command_run.freeze_project_context(Path.cwd(), platform=platform)
        sealed_rows: list[dict[str, Any]] = []
        for index, path in enumerate(unique_paths):
            label = unique_labels[index] if index < len(unique_labels) else ""
            result = request_seal_hot(
                path,
                device_name=infer_device_name_from_hot(path, label),
            )
            row = result.to_dict()
            sealed_rows.append(row)
            command_run.add_artifact(row.get("sealed_path"), role="sealed_log")
            command_run.add_artifact(row.get("hot_path"), role="hot_log")

        payload: dict[str, Any] = {"sealed": sealed_rows}
        payload.update(
            command_run.complete(
                metrics={"sealed_count": len(sealed_rows)},
            )
        )
        if args.json:
            _print_json(payload)
        else:
            for row in sealed_rows:
                print(f"sealed: {row.get('sealed_path')}")
                print(f"  hot: {row.get('hot_path')} ({row.get('lines', 0)} lines)")
            print(f"manifest: {payload['manifest_path']}")
        return 0
    except (ArchiveError, SessionError, ProfileError, OSError) as exc:
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed is not None:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def dispatch_seal_command(args: argparse.Namespace) -> Optional[int]:
    if getattr(args, "command", None) == "seal":
        return cmd_seal(args)
    return None

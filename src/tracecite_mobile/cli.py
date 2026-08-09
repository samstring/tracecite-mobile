# -*- coding: utf-8 -*-
"""tracecite-mobile CLI 入口：只负责装载插件、组装命令域和统一分派。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .analysis.knowledge import KnowledgeError, ensure_default_project_knowledge
from .commands.analysis import dispatch_analysis_command, register_analysis_commands
from .commands.device import dispatch_device_command, register_device_commands
from .commands.knowledge import dispatch_knowledge_command, register_knowledge_commands
from .commands.maintenance import (
    dispatch_maintenance_command,
    register_maintenance_commands,
)


def build_parser() -> argparse.ArgumentParser:
    from .platforms.registry import available_platforms

    parser = argparse.ArgumentParser(
        prog="tracecite-mobile",
        description=(
            "真机 iOS/Android 调试 CLI：设备日志、性能现场、文本过滤与场景分析。"
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--platform",
        choices=available_platforms(),
        default="ios",
        help="目标平台（默认 ios）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register_device_commands(sub)
    register_analysis_commands(sub)
    register_knowledge_commands(sub)
    register_maintenance_commands(sub)
    return parser


def _should_ensure_default_knowledge(args: argparse.Namespace) -> bool:
    """依赖 preset / 知识库的命令：启动时补默认 knowledge。"""
    if args.command == "scenario":
        return getattr(args, "scenario_command", None) == "run"
    return args.command in {
        "filter",
        "behavior",
        "grow",
        "preset",
        "profile",
        "session",
        "stream",
        "capture",
    }


def _ensure_default_knowledge_on_start(args: argparse.Namespace) -> None:
    """无项目知识库时写入 starter_knowledge，避免 preset 空词。"""
    if not _should_ensure_default_knowledge(args):
        return
    if args.command == "profile" and getattr(args, "profile_command", None) == "init":
        return
    try:
        result = ensure_default_project_knowledge(
            Path.cwd(), platform=getattr(args, "platform", "ios")
        )
    except KnowledgeError as exc:
        print(f"警告: 无法初始化默认知识库: {exc}", file=sys.stderr)
        return
    if not result.get("created") or getattr(args, "json", False):
        return
    path = result.get("path", "")
    if result.get("seeded_empty"):
        print(f"已向空知识库补入默认词表: {path}", file=sys.stderr)
    else:
        print(f"已为项目初始化默认知识库: {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    from .plugin_sdk import load_analyzer_plugins

    plugin_results = load_analyzer_plugins(strict=False)

    parser = build_parser()
    args = parser.parse_args(argv)
    setattr(args, "plugin_results", plugin_results)
    failed_plugins = [item for item in plugin_results if item.get("status") == "failed"]
    if failed_plugins and args.command != "plugin":
        names = ", ".join(str(item.get("name")) for item in failed_plugins)
        print(f"错误: 插件加载失败: {names}；请执行 tracecite-mobile plugin doctor", file=sys.stderr)
        return 1
    _ensure_default_knowledge_on_start(args)

    for dispatch in (
        dispatch_device_command,
        dispatch_analysis_command,
        dispatch_knowledge_command,
        dispatch_maintenance_command,
    ):
        result = dispatch(args)
        if result is not None:
            return result

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

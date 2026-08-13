"""项目配置、更新与历史产物维护命令。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from ..shared.constants import (
    DEFAULT_ANALYSIS_OUTPUT_DIR,
    DEFAULT_CAPTURE_OUTPUT_DIR,
    DEFAULT_LOG_OUTPUT_DIR,
)

from ..device.cleanup import CleanupError, clean_analysis_artifacts
from ..shared.config import ProfileError, load_project_profile, write_profile_template
from ..shared.update_check import (
    DEFAULT_CHECK_INTERVAL_HOURS,
    UpdateError,
    apply_update,
    check_for_updates,
)


def register_maintenance_commands(sub: argparse._SubParsersAction) -> None:
    """注册 profile / update / clean 命令参数。"""
    profile_parser = sub.add_parser("profile", help="项目级配置管理")
    profile_sub = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_sub.add_parser("show", help="显示当前生效的 profile")
    profile_show.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    profile_init = profile_sub.add_parser(
        "init", help="在当前目录生成 .tracecite/（config + knowledge，并写入 .gitignore）"
    )
    profile_init.add_argument("--force", action="store_true", help="覆盖已有配置文件")
    profile_init.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    update_parser = sub.add_parser(
        "update",
        help="手动检测/应用正式版 semver 更新（仅用户主动执行；patch 忽略，minor/major 才算有更新）",
    )
    update_sub = update_parser.add_subparsers(dest="update_command", required=True)
    update_check = update_sub.add_parser(
        "check",
        help="对比本地版本与远程最新正式 tag；patch 不算更新，minor/major 才 update_available（默认 7 天节流，可用 --force）",
    )
    update_check.add_argument("--force", action="store_true", help="忽略 7 天间隔，强制打远程")
    update_check.add_argument(
        "--interval-hours",
        type=int,
        default=DEFAULT_CHECK_INTERVAL_HOURS,
        help=f"检查间隔小时数（默认 {DEFAULT_CHECK_INTERVAL_HOURS}=7 天）",
    )
    update_check.add_argument("--remote", default="origin", help="git remote 名")
    update_check.add_argument("--json", action="store_true")
    update_apply = update_sub.add_parser(
        "apply",
        help="自愿更新：fetch tags 并 checkout 最新正式版（之后可 ./install.sh --with-skills）",
    )
    update_apply.add_argument(
        "--tag",
        default="",
        help="指定要切换的 tag（默认取远程最新正式版，且须为 minor/major）",
    )
    update_apply.add_argument("--remote", default="origin", help="git remote 名")
    update_apply.add_argument("--json", action="store_true")

    clean_parser = sub.add_parser("clean", help="清理历史日志、trace 与分析归档")
    clean_sub = clean_parser.add_subparsers(dest="clean_command", required=True)
    clean_analysis = clean_sub.add_parser("analysis", help="清理指定日期以前的分析产物")
    clean_analysis.add_argument(
        "--before",
        default="today",
        help="删除该时间点以前的产物：today/今天、yesterday/昨天 或 YYYY-MM-DD（默认 today）",
    )
    clean_analysis.add_argument(
        "--log-dir",
        help=f"日志目录，默认取 profile 或 {DEFAULT_LOG_OUTPUT_DIR}",
    )
    clean_analysis.add_argument(
        "--capture-dir",
        help=f"trace 目录，默认取 profile 或 {DEFAULT_CAPTURE_OUTPUT_DIR}",
    )
    clean_analysis.add_argument(
        "--analysis-dir",
        default=None,
        help=f"分析归档目录，默认取 profile 或 {DEFAULT_ANALYSIS_OUTPUT_DIR}",
    )
    clean_analysis.add_argument(
        "--include-archive",
        action="store_true",
        help="显式纳入隐藏归档（同时读取历史 archive/）；默认不触碰归档证据",
    )
    clean_analysis.add_argument(
        "--yes",
        action="store_true",
        help="确认实际删除归档；仅与 --include-archive 且非 --dry-run 一起生效",
    )
    clean_analysis.add_argument("--dry-run", action="store_true", help="只预览，不删除")
    clean_analysis.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    plugin_parser = sub.add_parser("plugin", help="插件清单与 SDK 健康检查")
    plugin_sub = plugin_parser.add_subparsers(dest="plugin_command", required=True)
    for name, help_text in (
        ("list", "列出已发现插件与版本"),
        ("doctor", "检查插件加载和扩展注册状态"),
    ):
        command = plugin_sub.add_parser(name, help=help_text)
        command.add_argument("--json", action="store_true")


def cmd_update(args: argparse.Namespace) -> int:
    try:
        if args.update_command == "check":
            result = check_for_updates(
                force=args.force,
                remote=args.remote,
                interval_hours=args.interval_hours,
            )
            payload = result.to_dict()
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"tool_root: {result.tool_root}")
                print(f"local_version:  {result.local_version}")
                print(f"remote_version: {result.remote_version or '-'} ({result.remote_tag or '-'})")
                print(f"bump: {result.bump or '-'}")
                print(f"checked: {result.checked} (skipped_by_interval={result.skipped_by_interval})")
                print(f"update_available: {result.update_available}")
                print(f"message: {result.message}")
                if result.hint:
                    print(f"hint: {result.hint}")
                print(f"next_check_after: {result.next_check_after}")
            return 0

        if args.update_command == "apply":
            result = apply_update(remote=args.remote, tag=args.tag or None)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"tool_root: {result['tool_root']}")
                print(f"tag: {result.get('tag') or '-'}")
                print(f"updated: {result['updated']}")
                print(f"before: {result['before'][:12]}")
                print(f"after:  {result['after'][:12]}")
                print(f"hint: {result['hint']}")
            return 0

        print(f"错误: 未知 update 子命令: {args.update_command}", file=sys.stderr)
        return 1
    except UpdateError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def cmd_clean(args: argparse.Namespace) -> int:
    try:
        profile = load_project_profile(Path.cwd(), platform=args.platform)
        if args.clean_command != "analysis":
            raise CleanupError(f"未知 clean 子命令: {args.clean_command}")
        log_dir = Path(args.log_dir).expanduser() if args.log_dir else profile.log_output_dir
        capture_dir = (
            Path(args.capture_dir).expanduser()
            if args.capture_dir
            else profile.capture_output_dir
        )
        result = clean_analysis_artifacts(
            log_dir=log_dir,
            capture_dir=capture_dir,
            analysis_dir=(
                Path(args.analysis_dir).expanduser()
                if args.analysis_dir
                else (profile.analysis_output_dir or DEFAULT_ANALYSIS_OUTPUT_DIR)
            ),
            before=args.before,
            dry_run=args.dry_run,
            include_archive=bool(args.include_archive),
            confirm_archive=bool(args.yes),
            extra_analysis_dirs=(
                (Path.cwd() / ".tracecite" / "runs",)
                if not args.analysis_dir
                else ()
            ),
            extra_run_roots=(
                (log_dir / ".runs", capture_dir / ".runs")
                if not args.analysis_dir
                else ()
            ),
        )
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            action = "将清理" if args.dry_run else "已清理"
            print(
                f"{action} {len(result.items)} 个历史产物"
                f"（截止 {result.cutoff.isoformat(timespec='seconds')} 以前）。"
            )
            size_label = "预计释放" if args.dry_run else "释放空间"
            print(f"{size_label}: {result.total_size_bytes} bytes")
        return 0
    except (CleanupError, ProfileError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def cmd_profile(args: argparse.Namespace) -> int:
    platform = args.platform
    try:
        if args.profile_command == "init":
            path = write_profile_template(Path.cwd(), overwrite=args.force, platform=platform)
            payload = {"created": True, "path": str(path), "platform": platform}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                if platform == "android":
                    print(f"已生成 Android profile: {path}")
                    print("请优先核对并按需修改：")
                    print("  package_name / activity / device_serial")
                    print("  log_output_dir / capture_output_dir（必填）")
                    print("  logcat_format / capture_template / default_filter_preset")
                elif platform == "ios":
                    print(f"已生成 profile: {path}")
                    print("请优先核对并按需修改：")
                    print("  process_name / subsystem / attach_process")
                    print("  log_output_dir / capture_output_dir（必填）")
                    print("  可选: filter_presets（覆盖或追加关键词）、default_filter_preset")
                else:
                    print(f"已生成 {platform} profile: {path}")
                    print("请按该平台插件文档补充后端所需配置。")
                print("改完后执行: tracecite-mobile profile show")
            return 0

        profile = load_project_profile(Path.cwd(), platform=platform)
        if args.json:
            print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
            return 0

        source = str(profile.source_path) if profile.source_path else "内置默认值（建议先 profile init）"
        print("当前 profile：")
        print(f"  平台:   {platform}")
        print(f"  来源:   {source}")
        if profile.source_path is None:
            print(
                f"  提示:   未找到项目配置，正在用 {platform} 默认值；"
                "请先 tracecite-mobile profile init"
            )
        if platform == "android":
            print(f"  包名:   {profile.package_name}")
            print(f"  Activity: {profile.activity}")
            print(f"  设备序列: {profile.device_serial}")
            print(f"  logcat 格式: {profile.logcat_format}")
        elif platform == "ios":
            print(f"  进程:   {profile.process_name}")
            print(f"  子系统: {profile.subsystem}")
            print(f"  默认 Attach: {profile.attach_process}")
        print(f"  日志目录: {profile.log_output_dir}")
        print(f"  Trace 目录: {profile.capture_output_dir}")
        print(f"  默认模板: {profile.capture_template}")
        if profile.default_filter_preset or profile.default_filter_pattern:
            print(
                f"  filter 默认: preset={profile.default_filter_preset!r} "
                f"pattern={profile.default_filter_pattern!r}"
            )
        if profile.filter_presets:
            print("  filter presets:")
            for name, preset in sorted(profile.filter_presets.items()):
                note = f" — {preset.note}" if preset.note else ""
                print(f"    - {name}: {preset.pattern}{note}")
        return 0
    except ProfileError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def cmd_plugin(args: argparse.Namespace) -> int:
    from tracecite_core.events import available_event_transformers
    from tracecite_core.plugin_sdk import PLUGIN_API_VERSION
    from tracecite_core.preprocess import available_preprocessor_actions
    from tracecite_core.segmenter import available_segmenters
    from tracecite_core.source import available_source_providers

    from ..analysis.assertions import available_assertion_types
    from ..analysis.behavior_summary import available_behavior_parsers
    from ..analysis.reporting import available_report_outputters
    from ..platforms.registry import available_platforms
    from ..plugin_sdk import ANALYZER_PLUGIN_API_VERSION

    plugins = list(getattr(args, "plugin_results", []) or [])
    failed = [item for item in plugins if item.get("status") == "failed"]
    payload = {
        "healthy": not failed,
        "api_versions": {
            "tracecite_core": PLUGIN_API_VERSION,
            "tracecite_mobile": ANALYZER_PLUGIN_API_VERSION,
        },
        "plugins": plugins,
        "extensions": {
            "platforms": available_platforms(),
            "source_providers": available_source_providers(),
            "segmenters": available_segmenters(),
            "preprocessors": available_preprocessor_actions(),
            "event_transformers": available_event_transformers(),
            "behavior_parsers": available_behavior_parsers(),
            "assertion_types": available_assertion_types(),
            "report_outputters": available_report_outputters(),
        },
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"插件 SDK: tracecite_core={PLUGIN_API_VERSION}, analyzer={ANALYZER_PLUGIN_API_VERSION}")
        if not plugins:
            print("未发现第三方插件。")
        for item in plugins:
            version = item.get("distribution_version") or "-"
            print(f"[{item.get('status')}] {item.get('name')} {version}")
        if args.plugin_command == "doctor":
            print("健康检查: " + ("通过" if not failed else "失败"))
    return 0 if not failed else 1


def dispatch_maintenance_command(args: argparse.Namespace) -> Optional[int]:
    handlers = {
        "profile": cmd_profile,
        "update": cmd_update,
        "clean": cmd_clean,
        "plugin": cmd_plugin,
    }
    handler = handlers.get(args.command)
    return None if handler is None else handler(args)

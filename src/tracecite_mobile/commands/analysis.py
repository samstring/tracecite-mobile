"""过滤、时间范围、行为摘要和场景分析命令。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from tracecite_core import build_segmenter, detect_segmenter_kind
from tracecite_core.run import RunIntegrityError
from ..shared.constants import DEFAULT_LOG_OUTPUT_DIR
from tracecite_core.text_filter import (
    DEFAULT_TEMPLATE_THRESHOLD,
    FilterError,
    _safe_tag,
    combine_patterns,
    filter_text,
    filter_texts,
    text_time_range,
    resolve_preset,
)

from ..analysis.behavior_summary import summarize_behavior_file
from ..analysis.knowledge import KnowledgeError, resolve_scenario_pattern
from ..analysis.scenario import cmd_scenario
from ..device.archive import ArchiveError, request_seal_hot
from ..device.session import SessionError, load_analysis_sessions
from ..shared.config import ProfileError, load_project_profile
from ..shared.command_run import CommandRun
from ..shared.log_paths import resolve_runs_dir, infer_device_name_from_hot
from ..shared.output_layout import is_immutable_log_source
from tracecite.integrations.agent_projection import compact_filter_payload, encoded_json


def register_analysis_commands(sub: argparse._SubParsersAction) -> None:
    """注册分析域命令参数。"""
    filter_parser = sub.add_parser(
        "filter",
        help="按关键词过滤运行日志（snapshot 定界 + grep，输出到隐藏目录供 AI 读取）",
    )
    filter_parser.add_argument(
        "log_path",
        nargs="*",
        help="原始日志路径（可多份）；与 --from-sessions 二选一或并用",
    )
    filter_parser.add_argument(
        "--from-sessions",
        action="store_true",
        help="自动使用当前全部 active session 的 hot 日志路径",
    )
    filter_parser.add_argument(
        "--merge-timeline",
        action="store_true",
        help="多设备时额外输出按时间合并的 timeline（行前缀 [device]）",
    )
    filter_parser.add_argument(
        "--output-dir",
        help=f"配合 --from-sessions：日志目录，默认取 profile 或 {DEFAULT_LOG_OUTPUT_DIR}",
    )
    filter_parser.add_argument(
        "--grep",
        metavar="PATTERN",
        help="扩展正则（grep -E）；与 --preset 同时使用时按 OR 合并",
    )
    filter_parser.add_argument(
        "--preset",
        metavar="NAME",
        help=(
            "预设关键词组合；可与 --grep 按 OR 合并；两者均未传时使用 profile 的 "
            "default_filter_preset / default_filter_pattern"
        ),
    )
    filter_parser.add_argument("--tag", help="输出标签（默认从 pattern / preset 推导）")
    filter_parser.add_argument("--out", metavar="PATH", help="指定单文件输出路径")
    filter_parser.add_argument(
        "--snapshot",
        action="store_true",
        help="先冻结快照再过滤（live hot 推荐改用 --seal-first）；指定 --out 时快照也写入输出目录",
    )
    filter_parser.add_argument(
        "--seal-first",
        action="store_true",
        help="过滤前先 seal 当前 hot（O(1) rename 切段），替代对 live 日志的 copy2 snapshot",
    )
    filter_parser.add_argument(
        "--segmenter",
        default="auto",
        help="记录分段方式：auto / mixed / devicelog / applog / jsonline / rawtext",
    )
    filter_parser.add_argument(
        "--encoding", default="utf-8", help="输入文本编码（默认 utf-8）"
    )
    filter_parser.add_argument(
        "--format",
        metavar="JSON_OR_NAME",
        help="内联声明式格式 JSON，或 config.json formats 中的注册名",
    )
    filter_parser.add_argument("--pid", type=int, metavar="PID", help="只保留指定 PID")
    filter_parser.add_argument("--tail-lines", type=int, metavar="N", help="只取最后 N 行覆盖的完整记录")
    filter_parser.add_argument("--line-from", type=int, metavar="N", help="起始物理行（1-based）")
    filter_parser.add_argument("--line-to", type=int, metavar="N", help="结束物理行（1-based）")
    filter_parser.add_argument("--last", metavar="DURATION", help="日志末条时间前的窗口，如 5m")
    filter_parser.add_argument("--since", metavar="TIME", help="起始时间")
    filter_parser.add_argument("--until", metavar="TIME", help="结束时间")
    filter_parser.add_argument(
        "--scenario",
        metavar="ID",
        help="合并知识库业务场景词（需要 preset 上下文）",
    )
    filter_parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    filter_parser.add_argument(
        "--agent-view",
        action="store_true",
        help="JSON 输出时附带 agent_view（默认与 --json 同时启用）",
    )
    filter_parser.add_argument(
        "--no-agent-view",
        action="store_true",
        help="JSON 输出时不附带 agent_view",
    )
    filter_parser.add_argument(
        "--max-line-chars",
        type=int,
        default=1024,
        metavar="N",
        help="过滤正文单行最大字符数，完整行保留在 records_path（默认 1024）",
    )
    filter_parser.add_argument(
        "--fold",
        action="store_true",
        help="生成模板折叠视图（.templates.jsonl）",
    )

    timerange_parser = sub.add_parser(
        "time-range",
        help="统计日志覆盖的时间范围与分钟分布",
    )
    timerange_parser.add_argument("log_path", nargs="+", help="原始日志路径（可多份）")
    timerange_parser.add_argument("--segmenter", default="auto", help="记录分段方式")
    timerange_parser.add_argument(
        "--encoding", default="utf-8", help="输入文本编码（默认 utf-8）"
    )
    timerange_parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    behavior_parser = sub.add_parser("behavior", help="生成统一用户行为事件摘要")
    behavior_sub = behavior_parser.add_subparsers(dest="behavior_command", required=True)
    behavior_summarize = behavior_sub.add_parser("summarize", help="生成行为事件流")
    behavior_summarize.add_argument("log_path", help="过滤日志路径")
    behavior_summarize.add_argument("--no-dedupe", action="store_true", help="保留相邻重复事件")
    behavior_summarize.add_argument("--scenario", help="合并该业务场景的行为 marker")
    behavior_summarize.add_argument("--json", action="store_true", help="以 JSON 输出")

    scenario_parser = sub.add_parser(
        "scenario",
        help="执行来源、分段、过滤、事件化、断言和交付的一体化场景",
    )
    scenario_sub = scenario_parser.add_subparsers(dest="scenario_command", required=True)
    scenario_run = scenario_sub.add_parser("run", help="执行 JSON/YAML 场景定义")
    scenario_run.add_argument("spec", help="场景定义文件路径")
    scenario_run.add_argument(
        "--base-dir",
        help="场景 source 相对路径的解析根目录（默认使用场景文件所在目录）",
    )
    scenario_run.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    scenario_validate = scenario_sub.add_parser("validate", help="校验场景 schema 与扩展引用")
    scenario_validate.add_argument("spec", help="场景定义文件路径")
    scenario_validate.add_argument(
        "--base-dir",
        help="场景 source 相对路径的解析根目录（默认使用场景文件所在目录）",
    )
    scenario_validate.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    scenario_explain = scenario_sub.add_parser(
        "explain", help="展示解析后的来源、格式、过滤、断言与交付计划"
    )
    scenario_explain.add_argument("spec", help="场景定义文件路径")
    scenario_explain.add_argument(
        "--base-dir",
        help="场景 source 相对路径的解析根目录（默认使用场景文件所在目录）",
    )
    scenario_explain.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    scenario_verify = scenario_sub.add_parser(
        "verify", help="校验运行 manifest 中全部输入与产物的完整性"
    )
    scenario_verify.add_argument("spec", help="manifest.json 路径")
    scenario_verify.add_argument("--json", action="store_true", help="以 JSON 输出结果")


def _attach_agent_view(payload: Dict[str, Any], *, enabled: bool) -> Dict[str, Any]:
    if not enabled:
        return payload
    payload = dict(payload)
    payload["agent_view"] = compact_filter_payload(payload)
    return payload


def _resolve_agent_view(args: argparse.Namespace) -> bool:
    if getattr(args, "no_agent_view", False):
        return False
    if getattr(args, "agent_view", False) or getattr(args, "json", False):
        return True
    return False


def _print_json(payload: Any) -> None:
    print(encoded_json(payload))


def _scenario_output_subdir(scenario: str) -> Optional[Path]:
    sid = (scenario or "").strip()
    if not sid:
        return None
    head = sid.split("-", 1)[0]
    if head and len(sid) > len(head):
        return Path(head) / sid
    return Path(sid)


def _resolve_filter_output(
    *,
    scenario: Optional[str],
    tag: str,
    source: Optional[Path],
    explicit_out: Optional[str],
    default_dir: Optional[Path] = None,
) -> Optional[Path]:
    if explicit_out:
        return Path(explicit_out).expanduser()
    if default_dir is not None and source is not None:
        root = Path(default_dir)
        if scenario:
            subdir = _scenario_output_subdir(scenario)
            if subdir is not None:
                root = root / subdir
        return root / f"filtered_{_safe_tag(tag)}_{source.name}"
    if scenario and source is not None:
        subdir = _scenario_output_subdir(scenario)
        if subdir is not None:
            return source.parent / ".filtered" / subdir / f"filtered_{_safe_tag(tag)}_{source.name}"
    return None


def _parse_format_arg(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FilterError(f"--format 不是合法 JSON: {text}\n{exc}") from exc
    return text


def _resolve_filter_segmenter(
    kind: str,
    sample: Optional[Path],
    format_spec: Any = None,
    formats: Optional[Dict[str, Any]] = None,
):
    if format_spec is not None:
        if isinstance(format_spec, dict):
            return build_segmenter(format_spec)
        name = str(format_spec).strip()
        definition = (formats or {}).get(name)
        if definition is None:
            known = ", ".join(sorted(formats or {})) or "(空)"
            raise FilterError(f"未知 format 名 {name!r}（项目注册表可用: {known}）")
        if not isinstance(definition, dict):
            raise FilterError(f"format {name!r} 注册值必须是对象: {definition!r}")
        return build_segmenter(dict(definition))

    resolved = (kind or "auto").strip().lower()
    if resolved == "auto":
        if sample is None or not Path(sample).exists():
            raise FilterError("auto 分段需要可读取的样本文件")
        resolved = detect_segmenter_kind(Path(sample))
    return build_segmenter(resolved)


_FILTER_ARTIFACT_ROLES = {
    "output_path": "filtered_log",
    "snapshot_path": "snapshot",
    "records_path": "matched_records",
    "history_path": "filter_history",
    "hits_path": "hit_metadata",
    "templates_path": "templates",
    "merged_timeline_path": "merged_timeline",
}


def _register_filter_artifacts(command_run: CommandRun, payload: Dict[str, Any]) -> None:
    rows = [payload, *(payload.get("sources") or [])]
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, role in _FILTER_ARTIFACT_ROLES.items():
            command_run.add_artifact(row.get(key), role=role)


def _seal_paths_before_filter(
    paths: list[Path],
    labels: list[str],
) -> list[Path]:
    sealed: list[Path] = []
    for index, path in enumerate(paths):
        if is_immutable_log_source(path):
            sealed.append(path)
            continue
        label = labels[index] if index < len(labels) else ""
        result = request_seal_hot(
            path,
            device_name=infer_device_name_from_hot(path, label),
        )
        sealed.append(Path(result.sealed_path))
    return sealed


def cmd_behavior(args: argparse.Namespace) -> int:
    path = Path(args.log_path).expanduser()
    platform = getattr(args, "platform", "ios")
    profile = load_project_profile(Path.cwd(), platform=platform)
    command_run = CommandRun(
        name="behavior-summarize",
        kind="behavior",
        platform=platform,
        run_root=resolve_runs_dir(platform, profile),
        parameters={
            "dedupe": not args.no_dedupe,
            "scenario": getattr(args, "scenario", None),
        },
    )
    try:
        prepared = command_run.prepare_input(path)
        command_run.freeze_project_context(Path.cwd(), platform=platform)
        summary = summarize_behavior_file(
            prepared,
            dedupe=not args.no_dedupe,
            start_dir=Path.cwd(),
            scenario=getattr(args, "scenario", None),
            platform=platform,
        )
        payload = summary.to_dict()
        report_path = command_run.write_json_artifact(
            "behavior_summary.json", payload, role="behavior_summary"
        )
        scenario_results = list(getattr(summary, "scenario_results", []))
        assertions_passed = all(bool(row.get("passed")) for row in scenario_results)
        run_fields = command_run.complete(
            verdict="passed" if assertions_passed else "failed",
            metrics={
                "event_count": summary.event_count,
                "technical_event_count": len(summary.technical_events),
                "behavior_count": len(summary.behaviors),
            },
            assertions={
                "scenario_results": scenario_results,
                "required_satisfied": assertions_passed,
            },
        )
        payload.update(run_fields)
        payload["report_path"] = str(report_path)
        payload["input_lineage"] = {
            "original": str(path.resolve()),
            "work_input": str(prepared),
        }
    except (KnowledgeError, OSError, RunIntegrityError) as exc:
        failed = command_run.fail(exc)
        print(f"错误: {exc}", file=sys.stderr)
        print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(payload)
        return 0
    print(f"source: {summary.source_path}")
    print(f"events: {summary.event_count}")
    print(f"categories: {summary.categories}")
    for event in summary.events:
        print(f"{event.timestamp or '?'}  [{event.category}] {event.label or event.name}")
    for result in getattr(summary, "scenario_results", []):
        status = "PASS" if result.get("passed") else "FAIL"
        print(f"scenario: {result.get('id') or '-'} [{status}]")
    print(f"manifest: {payload['manifest_path']}")
    return 0


def cmd_filter(args: argparse.Namespace) -> int:
    command_run: Optional[CommandRun] = None
    try:
        platform = getattr(args, "platform", "ios")
        profile = load_project_profile(Path.cwd(), platform=platform)
        pattern_source = "cli"
        preset_name: Optional[str] = None
        if args.preset:
            pattern, default_tag = resolve_preset(args.preset, profile.filter_preset_table())
            tag = args.tag or default_tag
            preset_name = args.preset
            pattern_source = f"preset:{args.preset}"
            if args.grep:
                pattern = combine_patterns(pattern, args.grep)
                pattern_source += "+grep"
        elif args.grep:
            pattern, tag = args.grep, args.tag
            pattern_source = "grep"
        else:
            resolved_default = profile.resolve_default_filter()
            if resolved_default is None:
                raise FilterError(
                    "必须指定 --grep / --preset，或配置 default_filter_preset / default_filter_pattern"
                )
            pattern, default_tag, pattern_source = resolved_default
            tag = args.tag or default_tag
            if pattern_source.startswith("preset:"):
                preset_name = pattern_source.split(":", 1)[1]

        scenario = getattr(args, "scenario", None)
        if scenario:
            if preset_name is None:
                raise FilterError("--scenario 需要 preset 上下文")
            pattern = resolve_scenario_pattern(
                preset_name,
                scenario=scenario,
                start_dir=Path.cwd(),
                base_pattern=pattern,
                platform=platform,
            )
            pattern_source += f"+scenario:{scenario}"

        raw_paths = getattr(args, "log_path", None) or []
        if isinstance(raw_paths, (str, Path)):
            raw_paths = [raw_paths]
        log_paths = [Path(path).expanduser() for path in raw_paths]
        labels: list[str] = []
        if getattr(args, "from_sessions", False):
            log_dir = Path(args.output_dir).expanduser() if args.output_dir else profile.log_output_dir
            sessions = load_analysis_sessions(log_dir, platform=platform)
            if not sessions:
                raise FilterError("当前没有 session；无法使用 --from-sessions")
            for session in sessions.values():
                log_paths.append(Path(session.output_path))
                labels.append(session.device_name)
        if not log_paths:
            raise FilterError("请提供 log_path，或使用 --from-sessions")

        seen: set[str] = set()
        unique_paths: list[Path] = []
        unique_labels: list[str] = []
        for index, path in enumerate(log_paths):
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            unique_paths.append(path)
            unique_labels.append(labels[index] if index < len(labels) else "")

        if len(unique_paths) > 1 and getattr(args, "out", None):
            raise FilterError(
                "--out 只适用于单文件；多文件请分别执行或使用 scenario output.run_dir"
            )

        if getattr(args, "seal_first", False):
            unique_paths = _seal_paths_before_filter(unique_paths, unique_labels)

        use_snapshot = bool(args.snapshot) and not getattr(args, "seal_first", False)
        if use_snapshot and all(is_immutable_log_source(path) for path in unique_paths):
            use_snapshot = False

        format_spec = _parse_format_arg(args.format)
        if len(unique_paths) > 1 and args.segmenter == "auto" and format_spec is None:
            segmenter = [
                _resolve_filter_segmenter("auto", path, formats=profile.formats)
                for path in unique_paths
            ]
        else:
            segmenter = _resolve_filter_segmenter(
                args.segmenter,
                unique_paths[0],
                format_spec=format_spec,
                formats=profile.formats,
            )
        command_run = CommandRun(
            name="filter",
            kind="filter",
            platform=platform,
            run_root=resolve_runs_dir(platform, profile),
            parameters={
                "pattern": pattern,
                "pattern_source": pattern_source,
                "tag": tag,
                "scenario": scenario,
                "segmenters": [
                    item.name for item in segmenter
                ] if isinstance(segmenter, list) else [segmenter.name],
                "snapshot": use_snapshot,
                "seal_first": bool(getattr(args, "seal_first", False)),
                "scope": {
                    "pid": args.pid,
                    "tail_lines": args.tail_lines,
                    "line_from": args.line_from,
                    "line_to": args.line_to,
                    "last": args.last,
                    "since": args.since,
                    "until": args.until,
                },
            },
        )
        command_run.freeze_project_context(Path.cwd(), platform=platform)
        prepared_paths = command_run.prepare_inputs(unique_paths)
        filter_dir = command_run.workspace.evidence_dir / ".filtered"
        filter_kwargs = dict(
            pattern=pattern,
            tag=tag,
            segmenter=segmenter,
            snapshot=use_snapshot,
            pid=args.pid,
            tail_lines=args.tail_lines,
            line_from=args.line_from,
            line_to=args.line_to,
            last=args.last,
            since=args.since,
            until=args.until,
            max_line_chars=getattr(args, "max_line_chars", 1024),
            template_threshold=(
                DEFAULT_TEMPLATE_THRESHOLD
                if args.fold
                else int(profile.analysis_get("template_threshold", 0))
            ),
            encoding=getattr(args, "encoding", "utf-8"),
        )

        if len(unique_paths) == 1 and not args.merge_timeline:
            single_kwargs = dict(filter_kwargs)
            if isinstance(single_kwargs["segmenter"], list):
                single_kwargs["segmenter"] = single_kwargs["segmenter"][0]
            result = filter_text(
                prepared_paths[0],
                output_path=_resolve_filter_output(
                    scenario=scenario,
                    tag=tag or "filtered",
                    source=unique_paths[0],
                    explicit_out=args.out,
                    default_dir=filter_dir,
                ),
                **single_kwargs,
            )
            payload = {**result.to_dict(), "pattern_source": pattern_source}
            _register_filter_artifacts(command_run, payload)
            payload.update(
                command_run.complete(
                    verdict="passed" if result.match_records > 0 else "failed",
                    metrics={
                        "source_count": 1,
                        "match_records": result.match_records,
                        "match_lines": result.match_lines,
                    },
                )
            )
            payload["input_lineage"] = [
                {"original": str(unique_paths[0].resolve()), "work_input": str(prepared_paths[0])}
            ]
            agent_view = _resolve_agent_view(args)
            if args.json:
                _print_json(_attach_agent_view(payload, enabled=agent_view))
            else:
                print(f"过滤完成: {result.match_records} 条 → {result.output_path}")
                if result.match_records == 0:
                    print("提示: 无命中仅代表证据不足，请放宽条件后重试。", file=sys.stderr)
                print(f"manifest: {payload['manifest_path']}")
            return 0 if result.match_records > 0 else 2

        multi = filter_texts(
            prepared_paths,
            merge_timeline=bool(args.merge_timeline),
            source_labels=unique_labels or None,
            output_dir=filter_dir,
            **filter_kwargs,
        )
        payload = {**multi.to_dict(), "pattern_source": pattern_source}
        _register_filter_artifacts(command_run, payload)
        payload.update(
            command_run.complete(
                verdict="passed" if multi.match_records > 0 else "failed",
                metrics={
                    "source_count": len(prepared_paths),
                    "match_records": multi.match_records,
                },
            )
        )
        payload["input_lineage"] = [
            {"original": str(original.resolve()), "work_input": str(prepared)}
            for original, prepared in zip(unique_paths, prepared_paths)
        ]
        agent_view = _resolve_agent_view(args)
        if args.json:
            _print_json(_attach_agent_view(payload, enabled=agent_view))
        else:
            print(f"多文件过滤完成: {multi.match_records} 条")
            if multi.merged_timeline_path:
                print(f"timeline: {multi.merged_timeline_path}")
            print(f"manifest: {payload['manifest_path']}")
        return 0 if multi.match_records > 0 else 2
    except (FilterError, ProfileError, KnowledgeError, SessionError, RunIntegrityError, ArchiveError, OSError) as exc:
        failed = command_run.fail(exc) if command_run is not None else None
        print(f"错误: {exc}", file=sys.stderr)
        if failed is not None:
            print(f"manifest: {failed['manifest_path']}", file=sys.stderr)
        return 1


def cmd_time_range(args: argparse.Namespace) -> int:
    paths = [Path(path).expanduser() for path in args.log_path]
    rows = []
    for path in paths:
        try:
            segmenter = _resolve_filter_segmenter(args.segmenter, path)
            rows.append(
                text_time_range(
                    path,
                    segmenter=segmenter,
                    encoding=getattr(args, "encoding", "utf-8"),
                )
            )
        except FilterError as exc:
            rows.append({"path": str(path), "error": str(exc)})
    if args.json:
        _print_json(rows if len(rows) > 1 else rows[0])
        return 0
    for row in rows:
        if "error" in row:
            print(f"[失败] {row['path']}: {row['error']}")
            continue
        print(f"文件: {row['path']}")
        print(f"  记录: {row['total_records']}，时间: {row['time_from'] or '?'} ~ {row['time_to'] or '?'}")
    return 0


def dispatch_analysis_command(args: argparse.Namespace) -> Optional[int]:
    handlers = {
        "filter": cmd_filter,
        "time-range": cmd_time_range,
        "behavior": cmd_behavior,
        "scenario": cmd_scenario,
    }
    handler = handlers.get(args.command)
    return None if handler is None else handler(args)

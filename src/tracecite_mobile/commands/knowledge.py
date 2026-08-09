"""项目过滤预设和排查知识库命令。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from ..analysis.knowledge import (
    KnowledgeError,
    add_behavior_marker,
    add_learning,
    add_playbook,
    apply_grow_suggestions,
    audit_filter_terms,
    ensure_scenario,
    load_project_knowledge,
    remove_behavior_marker,
    suggest_grow_terms,
)
from ..shared.config import (
    ProfileError,
    append_filter_preset_terms,
    load_project_profile,
    remove_filter_preset_terms,
)


def register_knowledge_commands(sub: argparse._SubParsersAction) -> None:
    """注册 preset / grow 命令参数。"""
    preset_parser = sub.add_parser(
        "preset",
        help="管理项目 filter_presets 关键词（随分析增长，勿改 Python 源码）",
    )
    preset_sub = preset_parser.add_subparsers(dest="preset_command", required=True)
    preset_list = preset_sub.add_parser("list", help="列出生效 preset 与 terms")
    preset_list.add_argument("--json", action="store_true", help="以 JSON 输出")
    preset_add = preset_sub.add_parser(
        "add",
        help="向当前平台的 .tracecite/knowledge.<platform>.json 追加关键词",
    )
    preset_add.add_argument("name", help="preset 名，如 user-behavior / network-http")
    preset_add.add_argument(
        "terms",
        nargs="+",
        help="要追加的关键词（grep -E 片段），可一次多个",
    )
    preset_add.add_argument("--note", help="可选，更新该 preset 的 note")
    preset_add.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    grow_parser = sub.add_parser(
        "grow",
        help="项目排查知识库成长（过滤词/行为 marker/经验/playbook），勿改 Python 源码",
    )
    grow_sub = grow_parser.add_subparsers(dest="grow_command", required=True)
    grow_show = grow_sub.add_parser("show", help="查看当前知识库（排查前建议先看）")
    grow_show.add_argument("--scenario", help="只显示某个业务场景")
    grow_show.add_argument("--json", action="store_true", help="以 JSON 输出")
    grow_scenario = grow_sub.add_parser(
        "scenario", help="创建/更新业务场景（AI 查到新业务场景时先建壳）"
    )
    grow_scenario.add_argument(
        "id", help="场景 id，通用短横线命名，如 feature-flow / pay-checkout"
    )
    grow_scenario.add_argument("--title", default="", help="场景标题")
    grow_scenario.add_argument("--note", default="", help="备注")
    grow_scenario.add_argument("--tags", nargs="*", default=[], help="标签")
    grow_scenario.add_argument("--json", action="store_true")
    grow_term = grow_sub.add_parser("term", help="追加或删除过滤词（--remove 为裁剪）")
    grow_term.add_argument("preset", help="preset 名，如 user-behavior")
    grow_term.add_argument("terms", nargs="+", help="关键词")
    grow_term.add_argument("--scenario", help="写入/裁剪业务场景（默认全局）")
    grow_term.add_argument("--remove", action="store_true", help="删除这些词（默认是追加）")
    grow_term.add_argument("--json", action="store_true")
    grow_marker = grow_sub.add_parser(
        "marker", help="追加或删除行为摘要 marker（--remove 为裁剪）"
    )
    grow_marker.add_argument("needle", help="日志中匹配的原文片段")
    grow_marker.add_argument("--category", default="marker", help="分类名")
    grow_marker.add_argument("--label", default="", help="摘要展示文案")
    grow_marker.add_argument("--scenario", help="写入/裁剪业务场景")
    grow_marker.add_argument(
        "--remove", action="store_true", help="按 needle 删除 marker（默认是追加）"
    )
    grow_marker.add_argument("--json", action="store_true")
    grow_learning = grow_sub.add_parser("learning", help="追加一条排查经验")
    grow_learning.add_argument("summary", help="经验摘要")
    grow_learning.add_argument("--tags", nargs="*", default=[], help="标签")
    grow_learning.add_argument("--evidence", default="", help="证据路径或短引")
    grow_learning.add_argument("--scenario", help="写入业务场景")
    grow_learning.add_argument("--json", action="store_true")
    grow_playbook = grow_sub.add_parser("playbook", help="追加/更新可复用排查步骤")
    grow_playbook.add_argument("name", help="playbook 名")
    grow_playbook.add_argument("--when", default="", help="何时使用")
    grow_playbook.add_argument("--step", action="append", default=[], dest="steps")
    grow_playbook.add_argument("--tags", nargs="*", default=[])
    grow_playbook.add_argument("--presets", nargs="*", default=[], dest="related_presets")
    grow_playbook.add_argument("--scenario", help="写入业务场景")
    grow_playbook.add_argument("--json", action="store_true")
    grow_audit = grow_sub.add_parser(
        "audit",
        help="对照日志统计词表命中量（辅助裁剪噪音/一次性词，通用启发式）",
    )
    grow_audit.add_argument("log_path", help="原始或 filtered/snapshot 日志路径")
    grow_audit.add_argument("--preset", default="user-behavior", help="preset 名（默认 user-behavior）")
    grow_audit.add_argument("--scenario", help="只审计该业务场景词表")
    grow_audit.add_argument("--json", action="store_true")
    grow_suggest = grow_sub.add_parser(
        "suggest", help="从日志自动发现「该 grow 的词」候选（发现侧；audit 是裁剪侧）"
    )
    grow_suggest.add_argument("log_path", help="原始或 filtered/snapshot 日志路径")
    grow_suggest.add_argument("--preset", default="user-behavior", help="preset 名（默认 user-behavior）")
    grow_suggest.add_argument("--scenario", help="限定业务场景（排除该场景已有词）")
    grow_suggest.add_argument("--min-count", type=int, default=5, help="候选最少出现记录数（默认 5）")
    grow_suggest.add_argument("--limit", type=int, default=12, help="最多输出候选数（默认 12）")
    grow_suggest.add_argument("--json", action="store_true")
    grow_auto = grow_sub.add_parser(
        "auto", help="按阈值把高频候选自动沉淀进知识库（自成长闭环；默认只加行为 marker）"
    )
    grow_auto.add_argument("log_path", help="原始或 filtered/snapshot 日志路径")
    grow_auto.add_argument("--preset", default="user-behavior", help="preset 名（默认 user-behavior）")
    grow_auto.add_argument("--scenario", help="写入该业务场景")
    grow_auto.add_argument("--min-count", type=int, default=5, help="候选最少出现记录数（默认 5）")
    grow_auto.add_argument("--limit", type=int, default=12, help="最多沉淀候选数（默认 12）")
    grow_auto.add_argument(
        "--terms", action="store_true", help="同时把候选加进 preset 词表（改变过滤召回，谨慎）"
    )
    grow_auto.add_argument("--dry-run", action="store_true", help="只输出将沉淀的清单，不写盘")
    grow_auto.add_argument("--json", action="store_true")


def cmd_grow(args: argparse.Namespace) -> int:
    try:
        if args.grow_command == "show":
            knowledge = load_project_knowledge(Path.cwd(), platform=args.platform)
            scenario_id = getattr(args, "scenario", None)
            if scenario_id:
                if scenario_id not in knowledge.scenarios:
                    raise KnowledgeError(f"未知场景 {scenario_id!r}")
                payload = knowledge.scenarios[scenario_id].to_dict()
                payload["source_path"] = str(knowledge.source_path) if knowledge.source_path else None
            else:
                payload = knowledge.to_dict()
                payload["source_path"] = str(knowledge.source_path) if knowledge.source_path else None
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0
            src = payload.get("source_path") or "(尚未创建，grow 后会生成)"
            print(f"knowledge: {src}")
            if scenario_id:
                print(f"scenario: {payload.get('id')} — {payload.get('title')}")
                print(f"note: {payload.get('note') or '-'}")
                print(f"tags: {payload.get('tags') or []}")
                print("filter_terms:")
                for name, terms in sorted((payload.get("filter_terms") or {}).items()):
                    print(f"  - {name}: {terms}")
                print("markers (L1 raw → technical):")
                for marker in payload.get("markers") or []:
                    print(f"  - {marker.get('needle')} => [{marker.get('category')}] {marker.get('label')}")
                print("events (L2 technical):")
                for event_id, event in sorted((payload.get("events") or {}).items()):
                    print(f"  - {event_id}: [{event.get('category')}] {event.get('name')}")
                print("behaviors (L3 business):")
                for behavior in payload.get("behaviors") or []:
                    print(f"  - {behavior.get('id')}: {behavior.get('title') or behavior.get('label')}")
                print("scenario (L4):")
                print(f"  - steps: {len(payload.get('steps') or [])}; assertions: {len(payload.get('assertions') or [])}")
                print("learnings:")
                for item in payload.get("learnings") or []:
                    print(f"  - {item.get('summary')}")
                print("playbooks:")
                for item in payload.get("playbooks") or []:
                    print(f"  - {item.get('name')}: {item.get('when') or '(no when)'}")
                return 0
            print(f"updated_at: {payload.get('updated_at') or '-'}")
            print("filter_terms (global):")
            for name, terms in sorted((payload.get("filter_terms") or {}).items()):
                print(f"  - {name}: {terms}")
            print("markers (L1 raw → technical):")
            for marker in payload.get("markers") or []:
                print(f"  - {marker.get('needle')} => [{marker.get('category')}] {marker.get('label')}")
            print("events (L2 technical):")
            for event_id, event in sorted((payload.get("events") or {}).items()):
                print(f"  - {event_id}: [{event.get('category')}] {event.get('name')}")
            print("behaviors (L3 business):")
            for behavior in payload.get("behaviors") or []:
                print(f"  - {behavior.get('id')}: {behavior.get('title') or behavior.get('label')}")
            print("learnings:")
            for item in payload.get("learnings") or []:
                tags = ",".join(item.get("tags") or []) or "-"
                print(f"  - ({tags}) {item.get('summary')}")
            print("playbooks:")
            for item in payload.get("playbooks") or []:
                print(f"  - {item.get('name')}: {item.get('when') or '(no when)'}")
            print("scenarios:")
            for sid, scenario in sorted((payload.get("scenarios") or {}).items()):
                print(f"  - {sid}: {scenario.get('title') or sid}")
            return 0

        if args.grow_command == "scenario":
            result = ensure_scenario(
                args.id,
                title=args.title,
                note=args.note,
                tags=args.tags,
                start_dir=Path.cwd(),
                platform=getattr(args, "platform", "ios"),
            )
        elif args.grow_command == "term":
            mutate = remove_filter_preset_terms if args.remove else append_filter_preset_terms
            result = mutate(
                args.preset,
                args.terms,
                start_dir=Path.cwd(),
                scenario=getattr(args, "scenario", None),
                platform=args.platform,
            )
        elif args.grow_command == "marker":
            if args.remove:
                result = remove_behavior_marker(
                    args.needle,
                    start_dir=Path.cwd(),
                    scenario=getattr(args, "scenario", None),
                    platform=args.platform,
                )
            else:
                result = add_behavior_marker(
                    args.needle,
                    category=args.category,
                    label=args.label,
                    start_dir=Path.cwd(),
                    scenario=getattr(args, "scenario", None),
                    platform=args.platform,
                )
        elif args.grow_command == "learning":
            result = add_learning(
                args.summary,
                tags=args.tags,
                evidence=args.evidence,
                start_dir=Path.cwd(),
                scenario=getattr(args, "scenario", None),
                platform=args.platform,
            )
        elif args.grow_command == "playbook":
            result = add_playbook(
                args.name,
                when=args.when,
                steps=args.steps,
                tags=args.tags,
                related_presets=args.related_presets,
                start_dir=Path.cwd(),
                scenario=getattr(args, "scenario", None),
                platform=args.platform,
            )
        elif args.grow_command == "audit":
            result = audit_filter_terms(
                Path(args.log_path),
                preset=args.preset,
                scenario=getattr(args, "scenario", None),
                start_dir=Path.cwd(),
                platform=args.platform,
            )
            if not args.json:
                print(f"log: {result['log_path']}")
                print(f"scope: {result['scope']}  preset: {result['preset']}")
                print("hits (count desc):")
                for item in result.get("hits") or []:
                    print(f"  [{item.get('hint')}] {item.get('count'):>6}  {item.get('term')}")
                print("hints:")
                for key, hint in (result.get("hints") or {}).items():
                    print(f"  - {key}: {hint}")
                return 0
        elif args.grow_command == "suggest":
            result = suggest_grow_terms(
                Path(args.log_path),
                preset=args.preset,
                scenario=getattr(args, "scenario", None),
                start_dir=Path.cwd(),
                platform=args.platform,
                min_count=args.min_count,
                limit=args.limit,
            )
            if not args.json:
                print(f"log: {result['log_path']}  segmenter: {result['segmenter']}")
                print(f"scope: {result['scenario'] or 'global'}:{result['preset']}  min_count={result['min_count']}")
                print(f"（已排除词表+marker {result['existing_excluded']} 个；候选仅建议，不写盘）")
                for candidate in result.get("candidates") or []:
                    print(f"  [{candidate['count']:>5}] {candidate['kind']:<6} {candidate['token']}")
                print("用法: grow auto <log> --preset <p> [--min-count N] 一键沉淀为 marker；")
                print("      --dry-run 先看将沉淀清单；--terms 额外加进词表")
                return 0
        elif args.grow_command == "auto":
            result = apply_grow_suggestions(
                Path(args.log_path),
                preset=args.preset,
                scenario=getattr(args, "scenario", None),
                start_dir=Path.cwd(),
                platform=args.platform,
                min_count=args.min_count,
                limit=args.limit,
                add_terms=args.terms,
                dry_run=args.dry_run,
            )
        else:
            print(f"错误: 未知 grow 子命令: {args.grow_command}", file=sys.stderr)
            return 1

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (KnowledgeError, ProfileError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def cmd_preset(args: argparse.Namespace) -> int:
    try:
        if args.preset_command == "list":
            profile = load_project_profile(Path.cwd(), platform=args.platform)
            payload = {
                "source_path": str(profile.source_path) if profile.source_path else None,
                "presets": {
                    name: preset.to_dict()
                    for name, preset in sorted(profile.filter_presets.items())
                },
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0
            print(f"profile: {payload['source_path'] or '(内置种子，无项目配置)'}")
            for name, preset in sorted(profile.filter_presets.items()):
                note = f" — {preset.note}" if preset.note else ""
                print(f"- {name}{note}")
                print(f"  terms: {list(preset.effective_terms())}")
                print(f"  pattern: {preset.pattern}")
            return 0

        if args.preset_command == "add":
            result = append_filter_preset_terms(
                args.name,
                args.terms,
                start_dir=Path.cwd(),
                note=args.note,
                platform=args.platform,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"profile: {result['profile_path']}")
                print(f"preset: {result['preset']}")
                print(f"added: {result['added'] or '(无新词)'}")
                if result["skipped_seed"]:
                    print(f"skipped(seed): {result['skipped_seed']}")
                if result["skipped_dup"]:
                    print(f"skipped(dup): {result['skipped_dup']}")
                print(f"project_terms: {result['project_terms']}")
                print(f"effective_pattern: {result['effective_pattern']}")
            return 0

        print(f"错误: 未知 preset 子命令: {args.preset_command}", file=sys.stderr)
        return 1
    except ProfileError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def dispatch_knowledge_command(args: argparse.Namespace) -> Optional[int]:
    handlers = {"preset": cmd_preset, "grow": cmd_grow}
    handler = handlers.get(args.command)
    return None if handler is None else handler(args)

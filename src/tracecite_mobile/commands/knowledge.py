"""项目过滤预设和排查知识库命令。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from tracecite.knowledge import KnowledgeGovernanceError

from ..analysis.knowledge import (
    KnowledgeError,
    audit_filter_terms,
    load_project_knowledge,
    suggest_grow_terms,
)
from ..analysis.knowledge_governance import (
    MOBILE_CANDIDATE_KINDS,
    check_mobile_knowledge_integrity,
    list_mobile_candidates,
    promote_mobile_knowledge,
    propose_mobile_knowledge,
    verify_mobile_knowledge,
)
from ..shared.config import (
    ProfileError,
    load_project_profile,
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
        help="已禁用：请使用 grow propose term",
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
        "scenario", help="已禁用：请使用 grow propose scenario"
    )
    grow_scenario.add_argument(
        "id", help="场景 id，通用短横线命名，如 feature-flow / pay-checkout"
    )
    grow_scenario.add_argument("--title", default="", help="场景标题")
    grow_scenario.add_argument("--note", default="", help="备注")
    grow_scenario.add_argument("--tags", nargs="*", default=[], help="标签")
    grow_scenario.add_argument("--json", action="store_true")
    grow_term = grow_sub.add_parser("term", help="已禁用：请使用 grow propose term")
    grow_term.add_argument("preset", help="preset 名，如 user-behavior")
    grow_term.add_argument("terms", nargs="+", help="关键词")
    grow_term.add_argument("--scenario", help="写入/裁剪业务场景（默认全局）")
    grow_term.add_argument("--remove", action="store_true", help="删除这些词（默认是追加）")
    grow_term.add_argument("--json", action="store_true")
    grow_marker = grow_sub.add_parser(
        "marker", help="已禁用：请使用 grow propose marker"
    )
    grow_marker.add_argument("needle", help="日志中匹配的原文片段")
    grow_marker.add_argument("--category", default="marker", help="分类名")
    grow_marker.add_argument("--label", default="", help="摘要展示文案")
    grow_marker.add_argument("--scenario", help="写入/裁剪业务场景")
    grow_marker.add_argument(
        "--remove", action="store_true", help="按 needle 删除 marker（默认是追加）"
    )
    grow_marker.add_argument("--json", action="store_true")
    grow_learning = grow_sub.add_parser("learning", help="已禁用：请使用 grow propose learning")
    grow_learning.add_argument("summary", help="经验摘要")
    grow_learning.add_argument("--tags", nargs="*", default=[], help="标签")
    grow_learning.add_argument("--evidence", default="", help="证据路径或短引")
    grow_learning.add_argument("--scenario", help="写入业务场景")
    grow_learning.add_argument("--json", action="store_true")
    grow_playbook = grow_sub.add_parser("playbook", help="已禁用：请使用 grow propose playbook")
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
        "auto", help="已禁用：suggest 后必须 propose/verify/promote"
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

    propose = grow_sub.add_parser(
        "propose",
        help="把知识写入独立候选库；不会修改正式知识",
    )
    propose_sub = propose.add_subparsers(dest="proposal_kind", required=True)

    def add_evidence_gate(parser: argparse.ArgumentParser, *, scenario: bool = True) -> None:
        parser.add_argument("--created-by", required=True, help="候选创建者/Agent id")
        parser.add_argument("--case-id", required=True, help="独立案例 id")
        parser.add_argument(
            "--evidence",
            action="append",
            required=True,
            help="Evidence/Manifest 引用；可重复传入",
        )
        if scenario:
            parser.add_argument("--scenario", help="候选所属业务场景")
        parser.add_argument("--json", action="store_true")

    propose_term = propose_sub.add_parser("term", help="提出过滤词候选")
    propose_term.add_argument("preset")
    propose_term.add_argument("terms", nargs="+")
    add_evidence_gate(propose_term)

    propose_marker = propose_sub.add_parser("marker", help="提出行为 marker 候选")
    propose_marker.add_argument("needle")
    propose_marker.add_argument("--category", default="marker")
    propose_marker.add_argument("--label", default="")
    add_evidence_gate(propose_marker)

    propose_learning = propose_sub.add_parser("learning", help="提出排查经验候选")
    propose_learning.add_argument("summary")
    propose_learning.add_argument("--tags", nargs="*", default=[])
    add_evidence_gate(propose_learning)

    propose_playbook = propose_sub.add_parser("playbook", help="提出 playbook 候选")
    propose_playbook.add_argument("name")
    propose_playbook.add_argument("--when", default="")
    propose_playbook.add_argument("--step", action="append", default=[], dest="steps")
    propose_playbook.add_argument("--tags", nargs="*", default=[])
    propose_playbook.add_argument("--presets", nargs="*", default=[], dest="related_presets")
    add_evidence_gate(propose_playbook)

    propose_scenario = propose_sub.add_parser("scenario", help="提出业务场景候选")
    propose_scenario.add_argument("id")
    propose_scenario.add_argument("--title", default="")
    propose_scenario.add_argument("--note", default="")
    propose_scenario.add_argument("--tags", nargs="*", default=[])
    add_evidence_gate(propose_scenario, scenario=False)

    verify = grow_sub.add_parser("verify", help="用另一个独立案例验证候选")
    verify.add_argument("candidate_id")
    verify.add_argument("--case-id", required=True)
    verify.add_argument("--outcome", choices=("support", "contradict"), required=True)
    verify.add_argument("--evidence", action="append", required=True)
    verify.add_argument("--verified-by", required=True)
    verify.add_argument("--note", default="")
    verify.add_argument("--json", action="store_true")

    promote = grow_sub.add_parser(
        "promote", help="经独立人工审核后把 verified 候选写入正式知识"
    )
    promote.add_argument("candidate_id")
    promote.add_argument("--approved-by", required=True)
    promote.add_argument("--json", action="store_true")

    candidates = grow_sub.add_parser("candidates", help="查看候选知识及状态")
    candidates.add_argument(
        "--status", choices=("candidate", "verified", "contradicted", "promoted")
    )
    candidates.add_argument("--json", action="store_true")

    doctor = grow_sub.add_parser("doctor", help="检查正式知识是否被绕过 promotion 修改")
    doctor.add_argument("--json", action="store_true")


_DIRECT_WRITE_COMMANDS = {
    "scenario",
    "term",
    "marker",
    "learning",
    "playbook",
    "auto",
}


def _proposal_payload(args: argparse.Namespace) -> Dict[str, Any]:
    kind = args.proposal_kind
    if kind == "term":
        return {"preset": args.preset, "terms": list(args.terms)}
    if kind == "marker":
        return {
            "needle": args.needle,
            "category": args.category,
            "label": args.label,
        }
    if kind == "learning":
        return {"summary": args.summary, "tags": list(args.tags)}
    if kind == "playbook":
        return {
            "name": args.name,
            "when": args.when,
            "steps": list(args.steps),
            "tags": list(args.tags),
            "related_presets": list(args.related_presets),
        }
    if kind == "scenario":
        return {
            "id": args.id,
            "title": args.title,
            "note": args.note,
            "tags": list(args.tags),
        }
    raise KnowledgeGovernanceError(
        f"不支持的 Mobile 候选 kind: {kind!r}（可用: {', '.join(MOBILE_CANDIDATE_KINDS)}）"
    )


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

        if args.grow_command in _DIRECT_WRITE_COMMANDS:
            raise KnowledgeGovernanceError(
                "Agent CLI 已禁止直接修改正式知识。请使用 grow propose，"
                "再经过 grow verify 与 grow promote。"
            )
        if args.grow_command == "propose":
            result = propose_mobile_knowledge(
                kind=args.proposal_kind,
                payload=_proposal_payload(args),
                created_by=args.created_by,
                case_id=args.case_id,
                evidence_refs=args.evidence,
                start_dir=Path.cwd(),
                platform=args.platform,
                scenario=getattr(args, "scenario", None),
            )
        elif args.grow_command == "verify":
            result = verify_mobile_knowledge(
                args.candidate_id,
                case_id=args.case_id,
                outcome=args.outcome,
                evidence_refs=args.evidence,
                verified_by=args.verified_by,
                note=args.note,
                start_dir=Path.cwd(),
                platform=args.platform,
            )
        elif args.grow_command == "promote":
            result = promote_mobile_knowledge(
                args.candidate_id,
                approved_by=args.approved_by,
                start_dir=Path.cwd(),
                platform=args.platform,
            )
        elif args.grow_command == "candidates":
            result = list_mobile_candidates(
                Path.cwd(),
                platform=args.platform,
                status=args.status or "",
            )
        elif args.grow_command == "doctor":
            result = check_mobile_knowledge_integrity(
                Path.cwd(), platform=args.platform
            )
            if result["status"] != "ok":
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
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
                print("候选不会自动写入正式知识。请用 grow propose 提案并绑定 Evidence。")
                return 0
        else:
            print(f"错误: 未知 grow 子命令: {args.grow_command}", file=sys.stderr)
            return 1

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (KnowledgeError, ProfileError, KnowledgeGovernanceError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, KnowledgeGovernanceError) else 1


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
            raise KnowledgeGovernanceError(
                "Agent CLI 已禁止 preset add 直接修改正式知识。"
                "请使用 grow propose term。"
            )

        print(f"错误: 未知 preset 子命令: {args.preset_command}", file=sys.stderr)
        return 1
    except (ProfileError, KnowledgeGovernanceError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, KnowledgeGovernanceError) else 1


def dispatch_knowledge_command(args: argparse.Namespace) -> Optional[int]:
    handlers = {"preset": cmd_preset, "grow": cmd_grow}
    handler = handlers.get(args.command)
    return None if handler is None else handler(args)

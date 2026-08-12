"""Mobile adapter for TraceCite's domain-neutral knowledge governance."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from tracecite.knowledge import (
    KnowledgeCandidate,
    KnowledgeGovernanceError,
    KnowledgeGovernanceStore,
)

from .knowledge import (
    KnowledgeError,
    add_behavior_marker,
    add_filter_terms,
    add_learning,
    add_playbook,
    ensure_default_project_knowledge,
    ensure_scenario,
)
from ..shared.project_paths import (
    find_project_root_with_meta,
    knowledge_path_in,
    project_meta_dir,
)


MOBILE_CANDIDATE_KINDS = ("term", "marker", "learning", "playbook", "scenario")


def _project_root(start_dir: Optional[Path]) -> Path:
    return find_project_root_with_meta(start_dir) or (start_dir or Path.cwd()).resolve()


def governance_store_path(
    start_dir: Optional[Path] = None,
    *,
    platform: str = "ios",
) -> Path:
    root = _project_root(start_dir)
    safe_platform = "".join(
        char if char.isalnum() or char in "-_" else "-"
        for char in str(platform).strip().lower()
    ).strip("-")
    if not safe_platform:
        raise KnowledgeGovernanceError("platform 不能为空")
    return project_meta_dir(root) / f"knowledge-candidates.mobile.{safe_platform}.json"


def _target_name(platform: str) -> str:
    return f"mobile:{str(platform).strip().lower()}"


def _prepare_store(
    start_dir: Optional[Path] = None,
    *,
    platform: str = "ios",
) -> tuple[KnowledgeGovernanceStore, Path]:
    root = _project_root(start_dir)
    ensured = ensure_default_project_knowledge(root, platform=platform)
    target = Path(str(ensured["path"])).resolve()
    store = KnowledgeGovernanceStore(
        governance_store_path(root, platform=platform)
    )
    status = store.check_target(_target_name(platform), target)
    if status["status"] == "unmanaged":
        store.register_target(_target_name(platform), target)
    return store, target


def check_mobile_knowledge_integrity(
    start_dir: Optional[Path] = None,
    *,
    platform: str = "ios",
) -> Dict[str, Any]:
    store, target = _prepare_store(start_dir, platform=platform)
    result = store.check_target(_target_name(platform), target)
    result["candidate_store"] = str(store.path)
    result["platform"] = platform
    return result


def require_mobile_knowledge_integrity(
    start_dir: Optional[Path] = None,
    *,
    platform: str = "ios",
) -> Dict[str, Any]:
    result = check_mobile_knowledge_integrity(start_dir, platform=platform)
    if result["status"] != "ok":
        raise KnowledgeGovernanceError(
            "正式 Mobile 知识未通过完整性检查："
            f"{result['status']}。请恢复受管知识，禁止绕过 promotion 直接写入。"
        )
    return result


def _validated_payload(kind: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    if kind == "term":
        preset = str(data.get("preset") or "").strip()
        terms = [str(item).strip() for item in data.get("terms") or [] if str(item).strip()]
        if not preset or not terms:
            raise KnowledgeGovernanceError("term 候选必须包含 preset 和 terms")
        data.update({"preset": preset, "terms": terms})
    elif kind == "marker":
        needle = str(data.get("needle") or "").strip()
        if not needle:
            raise KnowledgeGovernanceError("marker 候选必须包含 needle")
        data["needle"] = needle
    elif kind == "learning":
        summary = str(data.get("summary") or "").strip()
        if not summary:
            raise KnowledgeGovernanceError("learning 候选必须包含 summary")
        data["summary"] = summary
    elif kind == "playbook":
        name = str(data.get("name") or "").strip()
        if not name:
            raise KnowledgeGovernanceError("playbook 候选必须包含 name")
        data["name"] = name
    elif kind == "scenario":
        scenario_id = str(data.get("id") or "").strip()
        if not scenario_id:
            raise KnowledgeGovernanceError("scenario 候选必须包含 id")
        data["id"] = scenario_id
    else:
        raise KnowledgeGovernanceError(
            f"不支持的 Mobile 候选 kind: {kind!r}"
        )
    return data


def propose_mobile_knowledge(
    *,
    kind: str,
    payload: Mapping[str, Any],
    created_by: str,
    case_id: str,
    evidence_refs: Sequence[str],
    start_dir: Optional[Path] = None,
    platform: str = "ios",
    scenario: Optional[str] = None,
) -> Dict[str, Any]:
    require_mobile_knowledge_integrity(start_dir, platform=platform)
    store, _target = _prepare_store(start_dir, platform=platform)
    candidate = store.propose(
        kind=kind,
        payload=_validated_payload(kind, payload),
        domain=f"mobile.{platform}",
        scope=f"scenario:{scenario}" if scenario else "global",
        created_by=created_by,
        case_id=case_id,
        evidence_refs=evidence_refs,
    )
    return {
        "candidate_store": str(store.path),
        "candidate": candidate.to_dict(),
    }


def verify_mobile_knowledge(
    candidate_id: str,
    *,
    case_id: str,
    outcome: str,
    evidence_refs: Sequence[str],
    verified_by: str,
    note: str = "",
    start_dir: Optional[Path] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    require_mobile_knowledge_integrity(start_dir, platform=platform)
    store, _target = _prepare_store(start_dir, platform=platform)
    candidate = store.verify(
        candidate_id,
        case_id=case_id,
        outcome=outcome,
        evidence_refs=evidence_refs,
        verified_by=verified_by,
        note=note,
    )
    return {
        "candidate_store": str(store.path),
        "candidate": candidate.to_dict(),
    }


def _promote_mobile_candidate(
    candidate: KnowledgeCandidate,
    *,
    start_dir: Path,
    platform: str,
) -> Mapping[str, Any]:
    payload = candidate.payload
    scenario = None
    if candidate.scope.startswith("scenario:"):
        scenario = candidate.scope.split(":", 1)[1]
    if candidate.kind == "term":
        return add_filter_terms(
            str(payload["preset"]),
            list(payload["terms"]),
            start_dir=start_dir,
            scenario=scenario,
            platform=platform,
        )
    if candidate.kind == "marker":
        return add_behavior_marker(
            str(payload["needle"]),
            category=str(payload.get("category") or "marker"),
            label=str(payload.get("label") or ""),
            start_dir=start_dir,
            scenario=scenario,
            platform=platform,
        )
    if candidate.kind == "learning":
        return add_learning(
            str(payload["summary"]),
            tags=list(payload.get("tags") or []),
            evidence="; ".join(candidate.evidence_refs),
            start_dir=start_dir,
            scenario=scenario,
            platform=platform,
        )
    if candidate.kind == "playbook":
        return add_playbook(
            str(payload["name"]),
            when=str(payload.get("when") or ""),
            steps=list(payload.get("steps") or []),
            tags=list(payload.get("tags") or []),
            related_presets=list(payload.get("related_presets") or []),
            start_dir=start_dir,
            scenario=scenario,
            platform=platform,
        )
    if candidate.kind == "scenario":
        return ensure_scenario(
            str(payload["id"]),
            title=str(payload.get("title") or ""),
            note=str(payload.get("note") or ""),
            tags=list(payload.get("tags") or []),
            start_dir=start_dir,
            platform=platform,
        )
    raise KnowledgeGovernanceError(
        f"不支持的 Mobile 候选 kind: {candidate.kind!r}"
    )


def promote_mobile_knowledge(
    candidate_id: str,
    *,
    approved_by: str,
    start_dir: Optional[Path] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    integrity = require_mobile_knowledge_integrity(start_dir, platform=platform)
    root = _project_root(start_dir)
    store, target = _prepare_store(root, platform=platform)
    candidate = store.promote(
        candidate_id,
        approved_by=approved_by,
        promoter=lambda item: _promote_mobile_candidate(
            item, start_dir=root, platform=platform
        ),
        target_name=_target_name(platform),
        target_path=target,
    )
    return {
        "candidate_store": str(store.path),
        "knowledge_path": integrity["path"],
        "candidate": candidate.to_dict(),
    }


def list_mobile_candidates(
    start_dir: Optional[Path] = None,
    *,
    platform: str = "ios",
    status: str = "",
) -> Dict[str, Any]:
    store, _target = _prepare_store(start_dir, platform=platform)
    return {
        "candidate_store": str(store.path),
        "platform": platform,
        "candidates": [
            candidate.to_dict()
            for candidate in store.list_candidates(status=status)
        ],
    }


__all__ = [
    "MOBILE_CANDIDATE_KINDS",
    "check_mobile_knowledge_integrity",
    "governance_store_path",
    "list_mobile_candidates",
    "promote_mobile_knowledge",
    "propose_mobile_knowledge",
    "require_mobile_knowledge_integrity",
    "verify_mobile_knowledge",
]

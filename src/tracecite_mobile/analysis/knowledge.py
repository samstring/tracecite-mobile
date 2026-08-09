# -*- coding: utf-8 -*-
"""项目级可成长知识库：随排查过程积累，禁止堆进 Python 源码。

存放：过滤词增量、行为 marker、排查经验、可复用 playbook。
文件：``.tracecite/knowledge.<platform>.json``，不同平台互不回落。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from tracecite_core.text_filter import merge_terms, top_terms_in_text
from tracecite_core.segmenter import build_segmenter, detect_segmenter_kind
from ..shared.project_paths import (
    ensure_project_meta_gitignore,
    find_knowledge_path,
    find_project_root_with_meta,
    knowledge_path_in,
    resolve_knowledge_write_path,
)
from tracecite_core.state_file import atomic_write_json


class KnowledgeError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _stable_id(prefix: str, value: str) -> str:
    """Turn legacy human-readable values into stable layer identifiers."""
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return f"{prefix}.{slug or 'unnamed'}"


@dataclass
class TechnicalEventDefinition:
    """L2 technical event definition; it has no product/business meaning."""

    id: str
    category: str = "technical"
    name: str = ""
    label: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        self.category = str(self.category or "technical").strip() or "technical"
        self.name = str(self.name or self.id).strip() or self.id
        self.label = str(self.label or "").strip()

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "category": self.category,
            "name": self.name,
        }
        if self.label:
            out["label"] = self.label
        if self.attributes:
            out["attributes"] = dict(self.attributes)
        return out

    @classmethod
    def from_dict(cls, event_id: str, raw: Dict[str, Any]) -> "TechnicalEventDefinition":
        return cls(
            id=str(raw.get("id") or event_id).strip() or event_id,
            category=str(raw.get("category") or raw.get("type") or "technical").strip()
            or "technical",
            name=str(raw.get("name") or raw.get("event") or event_id).strip()
            or event_id,
            label=str(raw.get("label") or "").strip(),
            attributes=dict(raw.get("attributes") or {})
            if isinstance(raw.get("attributes") or {}, dict)
            else {},
        )


@dataclass
class BehaviorDefinition:
    """L3 business semantic mapping, kept entirely in analyzer knowledge."""

    id: str
    title: str = ""
    event: str = ""
    events: List[str] = field(default_factory=list)
    match: Dict[str, Any] = field(default_factory=dict)
    attributes: Dict[str, Any] = field(default_factory=dict)
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "title": self.title or self.id,
        }
        if self.event:
            out["event"] = self.event
        if self.events:
            out["events"] = list(self.events)
        if self.match:
            out["match"] = dict(self.match)
        if self.attributes:
            out["attributes"] = dict(self.attributes)
        if self.label:
            out["label"] = self.label
        return out

    @classmethod
    def from_dict(cls, behavior_id: str, raw: Dict[str, Any]) -> "BehaviorDefinition":
        event = raw.get("event") or raw.get("technical_event") or ""
        events = raw.get("events") or raw.get("technical_events") or []
        if isinstance(events, str):
            events = [events]
        match = raw.get("match") or raw.get("when") or {}
        if isinstance(match, str):
            match = {"event": match}
        return cls(
            id=str(raw.get("id") or behavior_id).strip() or behavior_id,
            title=str(raw.get("title") or raw.get("name") or behavior_id).strip()
            or behavior_id,
            event=str(event).strip(),
            events=[str(x).strip() for x in events if str(x).strip()]
            if isinstance(events, list)
            else [],
            match=dict(match) if isinstance(match, dict) else {},
            attributes=dict(raw.get("attributes") or {})
            if isinstance(raw.get("attributes") or {}, dict)
            else {},
            label=str(raw.get("label") or "").strip(),
        )


@dataclass
class BehaviorMarker:
    """L1 raw marker. ``category``/``label`` remain for old callers."""

    needle: str
    category: str = "marker"
    label: str = ""
    id: str = ""
    event: str = ""
    match: str = "contains"
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.needle = str(self.needle).strip()
        self.category = str(self.category or "marker").strip() or "marker"
        self.label = str(self.label or self.needle).strip() or self.needle
        self.id = str(self.id or _stable_id("marker", self.needle)).strip()
        self.event = str(self.event or self.category).strip() or self.category
        self.match = str(self.match or "contains").strip().lower() or "contains"

    def to_dict(self) -> Dict[str, Any]:
        """Canonical L1 representation."""
        out: Dict[str, Any] = {
            "id": self.id,
            "match": self.match,
            "needle": self.needle,
            "event": self.event,
        }
        if self.category:
            out["category"] = self.category
        if self.label:
            out["label"] = self.label
        if self.attributes:
            out["attributes"] = dict(self.attributes)
        return out

    def to_legacy_dict(self) -> Dict[str, str]:
        return {"needle": self.needle, "category": self.category, "label": self.label}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BehaviorMarker":
        return cls(
            needle=str(raw.get("needle", "")).strip(),
            category=str(raw.get("category", "")).strip() or "marker",
            label=str(raw.get("label", "")).strip()
            or str(raw.get("needle", "")).strip(),
            id=str(raw.get("id", "")).strip(),
            event=str(raw.get("event") or raw.get("event_id") or "").strip(),
            match=str(raw.get("match") or "contains").strip(),
            attributes=dict(raw.get("attributes") or {})
            if isinstance(raw.get("attributes") or {}, dict)
            else {},
        )


# New name for documentation and plugin authors; old imports continue to work.
MarkerDefinition = BehaviorMarker


@dataclass
class Learning:
    summary: str
    tags: List[str] = field(default_factory=list)
    evidence: str = ""
    at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "at": self.at,
            "summary": self.summary,
            "tags": list(self.tags),
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Learning":
        tags = raw.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        return cls(
            summary=str(raw.get("summary", "")).strip(),
            tags=[str(t).strip() for t in tags if str(t).strip()],
            evidence=str(raw.get("evidence", "") or ""),
            at=str(raw.get("at", "") or ""),
        )


@dataclass
class Playbook:
    name: str
    when: str = ""
    steps: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    related_presets: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "when": self.when,
            "steps": list(self.steps),
            "tags": list(self.tags),
            "related_presets": list(self.related_presets),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Playbook":
        def _list(key: str) -> List[str]:
            val = raw.get(key) or []
            if not isinstance(val, list):
                return [str(val)]
            return [str(x).strip() for x in val if str(x).strip()]

        return cls(
            name=str(raw.get("name", "")).strip(),
            when=str(raw.get("when", "") or ""),
            steps=_list("steps"),
            tags=_list("tags"),
            related_presets=_list("related_presets"),
        )


@dataclass
class ScenarioKnowledge:
    """业务场景知识包：AI 排查新场景时挂词表/经验，与全局 starter 分离。

    platform: common（通用）/ ios / android；平台隔离用，避免互相污染。
    """

    id: str
    title: str = ""
    note: str = ""
    tags: List[str] = field(default_factory=list)
    platform: str = "common"
    filter_terms: Dict[str, List[str]] = field(default_factory=dict)
    markers: List[BehaviorMarker] = field(default_factory=list)
    events: Dict[str, TechnicalEventDefinition] = field(default_factory=dict)
    behaviors: List[BehaviorDefinition] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    assertions: List[Dict[str, Any]] = field(default_factory=list)
    learnings: List[Learning] = field(default_factory=list)
    playbooks: List[Playbook] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "note": self.note,
            "tags": list(self.tags),
            "platform": self.platform,
            "filter_terms": {
                name: list(terms)
                for name, terms in sorted(self.filter_terms.items())
            },
            "markers": [m.to_dict() for m in self.markers],
            "events": {
                event_id: event.to_dict()
                for event_id, event in sorted(self.events.items())
            },
            "behaviors": [item.to_dict() for item in self.behaviors],
            "steps": list(self.steps),
            "assertions": list(self.assertions),
            "learnings": [x.to_dict() for x in self.learnings],
            "playbooks": [p.to_dict() for p in self.playbooks],
        }

    @classmethod
    def from_dict(cls, scenario_id: str, raw: Dict[str, Any]) -> "ScenarioKnowledge":
        sid = str(raw.get("id") or scenario_id).strip() or scenario_id
        tags = raw.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        filter_terms: Dict[str, List[str]] = {}
        raw_terms = raw.get("filter_terms") or {}
        if isinstance(raw_terms, dict):
            for name, terms in raw_terms.items():
                key = str(name).strip()
                if not key or not isinstance(terms, list):
                    continue
                filter_terms[key] = merge_terms([str(t) for t in terms])
        marker_items = list(raw.get("markers") or [])
        markers = []
        seen_marker_ids = set()
        for item in marker_items:
            if not isinstance(item, dict) or not str(item.get("needle", "")).strip():
                continue
            marker = BehaviorMarker.from_dict(item)
            if marker.id in seen_marker_ids:
                continue
            seen_marker_ids.add(marker.id)
            markers.append(marker)
        events: Dict[str, TechnicalEventDefinition] = {}
        raw_events = raw.get("events") or {}
        if isinstance(raw_events, list):
            raw_events = {
                str(item.get("id")): item
                for item in raw_events
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            }
        if isinstance(raw_events, dict):
            for event_id, item in raw_events.items():
                if isinstance(item, dict):
                    event = TechnicalEventDefinition.from_dict(str(event_id), item)
                    if event.id:
                        events[event.id] = event
        for marker in markers:
            events.setdefault(
                marker.event,
                TechnicalEventDefinition(
                    id=marker.event,
                    category=marker.category,
                    name=marker.event,
                    label=marker.label,
                ),
            )
        behaviors = []
        raw_behaviors = raw.get("behaviors") or []
        if isinstance(raw_behaviors, dict):
            raw_behaviors = [dict(item, id=behavior_id) for behavior_id, item in raw_behaviors.items() if isinstance(item, dict)]
        for item in raw_behaviors:
            if isinstance(item, dict):
                behavior = BehaviorDefinition.from_dict(
                    str(item.get("id") or item.get("name") or "behavior"), item
                )
                if behavior.id:
                    behaviors.append(behavior)
        steps = raw.get("steps") or raw.get("sequence") or []
        if not isinstance(steps, list):
            steps = [steps]
        assertions = raw.get("assertions") or raw.get("assert") or []
        if isinstance(assertions, dict):
            assertions = assertions.get("rules") or []
        if not isinstance(assertions, list):
            assertions = [assertions]
        learnings = [
            Learning.from_dict(item)
            for item in (raw.get("learnings") or [])
            if isinstance(item, dict) and str(item.get("summary", "")).strip()
        ]
        playbooks = [
            Playbook.from_dict(item)
            for item in (raw.get("playbooks") or [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        return cls(
            id=sid,
            title=str(raw.get("title", "") or sid),
            note=str(raw.get("note", "") or ""),
            tags=[str(t).strip() for t in tags if str(t).strip()],
            platform=str(raw.get("platform") or "common").strip() or "common",
            filter_terms=filter_terms,
            markers=markers,
            events=events,
            behaviors=behaviors,
            steps=[item for item in steps if isinstance(item, dict)],
            assertions=[item for item in assertions if isinstance(item, dict)],
            learnings=learnings,
            playbooks=playbooks,
        )


@dataclass
class ProjectKnowledge:
    """项目排查知识（会成长）。

    knowledge_schema_version: 知识库 schema 版本（四层知识模型为 3）。
    """

    source_path: Optional[Path] = None
    version: int = 1
    knowledge_schema_version: int = 3
    updated_at: str = ""
    filter_terms: Dict[str, List[str]] = field(default_factory=dict)
    markers: List[BehaviorMarker] = field(default_factory=list)
    events: Dict[str, TechnicalEventDefinition] = field(default_factory=dict)
    behaviors: List[BehaviorDefinition] = field(default_factory=list)
    learnings: List[Learning] = field(default_factory=list)
    playbooks: List[Playbook] = field(default_factory=list)
    scenarios: Dict[str, ScenarioKnowledge] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "knowledge_schema_version": self.knowledge_schema_version,
            "updated_at": self.updated_at or _now_iso(),
            "filter_terms": {
                name: list(terms)
                for name, terms in sorted(self.filter_terms.items())
            },
            "markers": [m.to_dict() for m in self.markers],
            "events": {
                event_id: event.to_dict()
                for event_id, event in sorted(self.events.items())
            },
            "behaviors": [item.to_dict() for item in self.behaviors],
            "learnings": [x.to_dict() for x in self.learnings],
            "playbooks": [p.to_dict() for p in self.playbooks],
            "scenarios": {
                sid: sc.to_dict() for sid, sc in sorted(self.scenarios.items())
            },
        }

    def marker_tuples(
        self, *, scenario: Optional[str] = None
    ) -> List[tuple[str, str, str]]:
        markers = list(self.markers)
        if scenario and scenario in self.scenarios:
            markers.extend(self.scenarios[scenario].markers)
        return [(m.needle, m.category, m.label) for m in markers if m.needle]

    def marker_definitions(self, *, scenario: Optional[str] = None) -> List[BehaviorMarker]:
        markers = list(self.markers)
        if scenario and scenario in self.scenarios:
            markers.extend(self.scenarios[scenario].markers)
        return markers

    def technical_events(self, *, scenario: Optional[str] = None) -> Dict[str, TechnicalEventDefinition]:
        events = dict(self.events)
        if scenario and scenario in self.scenarios:
            events.update(self.scenarios[scenario].events)
        for marker in self.marker_definitions(scenario=scenario):
            events.setdefault(
                marker.event,
                TechnicalEventDefinition(
                    id=marker.event,
                    category=marker.category,
                    name=marker.event,
                    label=marker.label,
                ),
            )
        return events

    def behavior_definitions(self, *, scenario: Optional[str] = None) -> List[BehaviorDefinition]:
        behaviors = list(self.behaviors)
        if scenario and scenario in self.scenarios:
            behaviors.extend(self.scenarios[scenario].behaviors)
        return behaviors

    def effective_filter_terms(
        self,
        preset: str,
        *,
        scenario: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> List[str]:
        """全局 preset 词 + 可选场景词。

        platform 给定时，仅合并该平台或 common 的场景，实现平台隔离。
        """
        base = list(self.filter_terms.get(preset, []))
        if scenario and scenario in self.scenarios:
            sc = self.scenarios[scenario]
            if platform is None or sc.platform in ("common", platform):
                extra = sc.filter_terms.get(preset, [])
                return merge_terms(base, extra)
        return base


def empty_knowledge(*, source_path: Optional[Path] = None) -> ProjectKnowledge:
    return ProjectKnowledge(source_path=source_path, updated_at=_now_iso())


def load_project_knowledge(
    start_dir: Optional[Path] = None,
    platform: str = "ios",
) -> ProjectKnowledge:
    path = find_knowledge_path(start_dir, platform=platform)
    if path is None or not path.is_file():
        return empty_knowledge(source_path=path)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeError(f"知识库不是合法 JSON: {path}\n{exc}") from exc
    if not isinstance(raw, dict):
        raise KnowledgeError(f"知识库顶层必须是对象: {path}")
    return knowledge_from_dict(raw, source_path=path)


def save_project_knowledge(
    knowledge: ProjectKnowledge,
    *,
    path: Optional[Path] = None,
    platform: str = "ios",
) -> Path:
    dest = path or knowledge.source_path or resolve_knowledge_write_path(platform=platform)
    knowledge.updated_at = _now_iso()
    knowledge.source_path = dest
    atomic_write_json(dest, knowledge.to_dict())
    # 若写在 .tracecite/ 下，尽量把该目录加入项目 gitignore
    if dest.parent.name == ".tracecite":
        pass  # 团队选择不忽略 tracecite_core 元数据
    return dest


def _starter_knowledge_path(platform: str = "ios") -> Path:
    # analysis/ 是子包：data/ 在包根（tracecite_mobile/data/），不在 analysis/ 下
    data_dir = Path(__file__).resolve().parents[1] / "data"
    if platform == "android":
        return data_dir / "starter_knowledge.android.json"
    if platform == "ios":
        ios_path = data_dir / "starter_knowledge.ios.json"
        if ios_path.is_file():
            return ios_path
        return data_dir / "starter_knowledge.json"
    # 第三方平台默认从空知识库开始，避免静默混入 iOS 词表。
    return data_dir / f"starter_knowledge.{platform}.json"


def load_starter_knowledge_dict(platform: str = "ios") -> Dict[str, Any]:
    """默认知识来自 data/starter_knowledge*.json（非 Python 常量）。"""
    path = _starter_knowledge_path(platform)
    if not path.is_file():
        return {
            "version": 1,
            "knowledge_schema_version": 3,
            "filter_terms": {},
            "markers": [],
            "events": {},
            "behaviors": [],
            "learnings": [],
            "playbooks": [],
            "scenarios": {},
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise KnowledgeError(f"starter_knowledge 顶层必须是对象: {path}")
    return raw


def knowledge_from_dict(
    raw: Dict[str, Any],
    *,
    source_path: Optional[Path] = None,
) -> ProjectKnowledge:
    schema_version = int(raw.get("knowledge_schema_version") or 3)
    if schema_version != 3:
        raise KnowledgeError(
            f"只支持 knowledge_schema_version=3，实际为 {schema_version}"
        )
    filter_terms: Dict[str, List[str]] = {}
    raw_terms = raw.get("filter_terms") or {}
    if isinstance(raw_terms, dict):
        for name, terms in raw_terms.items():
            key = str(name).strip()
            if not key or not isinstance(terms, list):
                continue
            filter_terms[key] = merge_terms([str(t) for t in terms])

    marker_items = list(raw.get("markers") or [])
    markers: List[BehaviorMarker] = []
    seen_marker_ids = set()
    for item in marker_items:
        if not isinstance(item, dict):
            continue
        marker = BehaviorMarker.from_dict(item)
        if marker.needle and marker.id not in seen_marker_ids:
            markers.append(marker)
            seen_marker_ids.add(marker.id)

    events: Dict[str, TechnicalEventDefinition] = {}
    raw_events = raw.get("events") or {}
    if isinstance(raw_events, list):
        raw_events = {
            str(item.get("id")): item
            for item in raw_events
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
    if isinstance(raw_events, dict):
        for event_id, item in raw_events.items():
            if not isinstance(item, dict):
                continue
            event = TechnicalEventDefinition.from_dict(str(event_id), item)
            if event.id:
                events[event.id] = event
    for marker in markers:
        events.setdefault(
            marker.event,
            TechnicalEventDefinition(
                id=marker.event,
                category=marker.category,
                name=marker.event,
                label=marker.label,
            ),
        )

    behaviors: List[BehaviorDefinition] = []
    raw_behaviors = raw.get("behaviors") or []
    if isinstance(raw_behaviors, dict):
        raw_behaviors = [
            dict(item, id=behavior_id)
            for behavior_id, item in raw_behaviors.items()
            if isinstance(item, dict)
        ]
    for item in raw_behaviors:
        if not isinstance(item, dict):
            continue
        behavior = BehaviorDefinition.from_dict(
            str(item.get("id") or item.get("name") or "behavior"), item
        )
        if behavior.id:
            behaviors.append(behavior)

    learnings: List[Learning] = []
    for item in raw.get("learnings") or []:
        if not isinstance(item, dict):
            continue
        learning = Learning.from_dict(item)
        if learning.summary:
            learnings.append(learning)

    playbooks: List[Playbook] = []
    for item in raw.get("playbooks") or []:
        if not isinstance(item, dict):
            continue
        playbook = Playbook.from_dict(item)
        if playbook.name:
            playbooks.append(playbook)

    scenarios: Dict[str, ScenarioKnowledge] = {}
    raw_scenarios = raw.get("scenarios") or {}
    if isinstance(raw_scenarios, dict):
        for sid, item in raw_scenarios.items():
            key = str(sid).strip()
            if not key or not isinstance(item, dict):
                continue
            scenarios[key] = ScenarioKnowledge.from_dict(key, item)

    return ProjectKnowledge(
        source_path=source_path,
        version=int(raw.get("version") or 1),
        knowledge_schema_version=schema_version,
        updated_at=str(raw.get("updated_at") or _now_iso()),
        filter_terms=filter_terms,
        markers=markers,
        events=events,
        behaviors=behaviors,
        learnings=learnings,
        playbooks=playbooks,
        scenarios=scenarios,
    )


def write_knowledge_template(
    destination: Path, *, overwrite: bool = False, platform: str = "ios"
) -> Path:
    path = knowledge_path_in(destination, platform=platform)
    if path.exists() and not overwrite:
        raise KnowledgeError(f"知识库已存在: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    starter = load_starter_knowledge_dict(platform)
    knowledge = knowledge_from_dict(starter, source_path=path)
    save_project_knowledge(knowledge, path=path)
    return path


def _project_root_for_knowledge(start_dir: Optional[Path] = None) -> Path:
    """有 `.tracecite/config.json`（或旧 profile）则用其项目根，否则用 cwd。"""
    rooted = find_project_root_with_meta(start_dir)
    if rooted is not None:
        return rooted
    return (start_dir or Path.cwd()).resolve()


def _existing_knowledge_file(
    project_root: Path,
    platform: str = "ios",
) -> Optional[Path]:
    modern = knowledge_path_in(project_root, platform=platform)
    if modern.is_file():
        return modern
    return None


def ensure_default_project_knowledge(
    start_dir: Optional[Path] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    """启动时确保项目有默认知识库。

    - 无对应平台知识库文件 → 从 ``data/starter_knowledge*.json`` 写入
    - 已有文件且 ``filter_terms`` 非空 → 不动（保留 grow 结果）
    - 已有文件但 ``filter_terms`` 为空 → 仅补 starter 词/marker/learning/playbook，
      不覆盖已有非空字段（按平台选 starter，避免 iOS/Android 互相污染）

    返回 ``{"created": bool, "path": str, "seeded_empty": bool}``。
    """
    root = _project_root_for_knowledge(start_dir)
    existing = _existing_knowledge_file(root, platform=platform)
    if existing is None:
        found = find_knowledge_path(start_dir or root, platform=platform)
        if found is not None and found.is_file():
            existing = found

    if existing is None:
        path = write_knowledge_template(root, overwrite=False, platform=platform)
        return {
            "created": True,
            "seeded_empty": False,
            "path": str(path),
        }

    try:
        raw = json.loads(existing.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeError(f"知识库不是合法 JSON: {existing}\n{exc}") from exc
    if not isinstance(raw, dict):
        raise KnowledgeError(f"知识库顶层必须是对象: {existing}")
    knowledge = knowledge_from_dict(raw, source_path=existing)

    if knowledge.filter_terms:
        return {
            "created": False,
            "seeded_empty": False,
            "path": str(existing),
        }

    starter = knowledge_from_dict(
        load_starter_knowledge_dict(platform), source_path=existing
    )
    knowledge.filter_terms = dict(starter.filter_terms)
    if not knowledge.markers:
        knowledge.markers = list(starter.markers)
    if not knowledge.learnings:
        knowledge.learnings = list(starter.learnings)
    if not knowledge.playbooks:
        knowledge.playbooks = list(starter.playbooks)
    knowledge.knowledge_schema_version = max(
        knowledge.knowledge_schema_version, 3
    )
    path = save_project_knowledge(knowledge, path=existing)
    return {
        "created": True,
        "seeded_empty": True,
        "path": str(path),
    }


def ensure_scenario(
    scenario_id: str,
    *,
    title: str = "",
    note: str = "",
    tags: Optional[Sequence[str]] = None,
    start_dir: Optional[Path] = None,
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """创建或更新业务场景壳（AI 查到新业务场景时先 ensure）。"""
    sid = scenario_id.strip()
    if not sid:
        raise KnowledgeError("scenario id 不能为空")
    load_platform = platform or "ios"
    knowledge = load_project_knowledge(start_dir, platform=load_platform)
    write_path = resolve_knowledge_write_path(start_dir, platform=load_platform)
    created = sid not in knowledge.scenarios
    if created:
        knowledge.scenarios[sid] = ScenarioKnowledge(
            id=sid,
            title=(title or sid).strip() or sid,
            note=note or "",
            tags=[str(t).strip() for t in (tags or []) if str(t).strip()],
            platform=platform or "common",
        )
    else:
        sc = knowledge.scenarios[sid]
        if title:
            sc.title = title.strip()
        if note:
            sc.note = note
        if tags:
            sc.tags = merge_terms(sc.tags, list(tags))
        if platform:
            sc.platform = platform
    path = save_project_knowledge(knowledge, path=write_path, platform=load_platform)
    return {
        "knowledge_path": str(path),
        "created": created,
        "scenario": knowledge.scenarios[sid].to_dict(),
    }


def _require_scenario(knowledge: ProjectKnowledge, scenario: Optional[str]) -> Optional[ScenarioKnowledge]:
    if not scenario:
        return None
    sid = scenario.strip()
    if not sid:
        return None
    if sid not in knowledge.scenarios:
        raise KnowledgeError(
            f"未知场景 {sid!r}。请先: tracecite-mobile grow scenario {sid} --title '...'"
        )
    return knowledge.scenarios[sid]


def add_filter_terms(
    preset_name: str,
    terms: Sequence[str],
    *,
    start_dir: Optional[Path] = None,
    seed_terms: Optional[Sequence[str]] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    """追加过滤词到知识库（全局或某业务场景）。"""
    name = preset_name.strip()
    if not name:
        raise KnowledgeError("preset 名称不能为空")

    knowledge = load_project_knowledge(start_dir, platform=platform)
    write_path = resolve_knowledge_write_path(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    target = sc.filter_terms if sc is not None else knowledge.filter_terms

    seed = [str(t).strip() for t in (seed_terms or []) if str(t).strip()]
    global_set = set(knowledge.filter_terms.get(name, []))
    seed_set = set(seed) | (global_set if sc is not None else set())
    current = list(target.get(name, []))
    existing = set(current)
    added: List[str] = []
    skipped_seed: List[str] = []
    skipped_dup: List[str] = []
    for term in terms:
        t = str(term).strip()
        if not t:
            continue
        if t in seed_set and t not in existing:
            skipped_seed.append(t)
            continue
        if t in existing:
            skipped_dup.append(t)
            continue
        if t in seed_set:
            skipped_seed.append(t)
            continue
        current.append(t)
        existing.add(t)
        added.append(t)

    target[name] = current
    path = save_project_knowledge(knowledge, path=write_path, platform=platform)
    effective = knowledge.effective_filter_terms(name, scenario=scenario)
    if seed:
        effective = merge_terms(seed, effective)
    from tracecite_core.text_filter import pattern_from_terms

    return {
        "knowledge_path": str(path),
        "preset": name,
        "scenario": sc.id if sc else None,
        "added": added,
        "skipped_seed": skipped_seed,
        "skipped_dup": skipped_dup,
        "project_terms": current,
        "effective_pattern": pattern_from_terms(effective),
    }


def remove_filter_terms(
    preset_name: str,
    terms: Sequence[str],
    *,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    """从知识库删除过滤词（全局或某业务场景）；不存在的词记入 missing。"""
    name = preset_name.strip()
    if not name:
        raise KnowledgeError("preset 名称不能为空")

    knowledge = load_project_knowledge(start_dir, platform=platform)
    write_path = resolve_knowledge_write_path(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    target = sc.filter_terms if sc is not None else knowledge.filter_terms
    current = list(target.get(name, []))
    existing = set(current)
    removed: List[str] = []
    missing: List[str] = []
    remove_set = set()
    for term in terms:
        t = str(term).strip()
        if not t:
            continue
        if t in existing:
            remove_set.add(t)
            removed.append(t)
        else:
            missing.append(t)

    if remove_set:
        current = [t for t in current if t not in remove_set]
        if current:
            target[name] = current
        else:
            target.pop(name, None)
        path = save_project_knowledge(knowledge, path=write_path, platform=platform)
    else:
        path = knowledge.source_path or write_path

    from tracecite_core.text_filter import pattern_from_terms

    effective = knowledge.effective_filter_terms(name, scenario=scenario)
    return {
        "knowledge_path": str(path),
        "preset": name,
        "scenario": sc.id if sc else None,
        "removed": removed,
        "missing": missing,
        "project_terms": list(target.get(name, [])),
        "effective_pattern": pattern_from_terms(effective),
    }


def add_behavior_marker(
    needle: str,
    *,
    category: str = "marker",
    label: str = "",
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    needle = needle.strip()
    if not needle:
        raise KnowledgeError("marker needle 不能为空")
    knowledge = load_project_knowledge(start_dir, platform=platform)
    write_path = resolve_knowledge_write_path(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    bucket = sc.markers if sc is not None else knowledge.markers
    for existing in bucket:
        if existing.needle == needle:
            return {
                "knowledge_path": str(knowledge.source_path or write_path),
                "added": False,
                "scenario": sc.id if sc else None,
                "marker": existing.to_dict(),
            }
    marker = BehaviorMarker(
        needle=needle,
        category=(category or "marker").strip() or "marker",
        label=(label or needle).strip() or needle,
    )
    bucket.append(marker)
    event_bucket = sc.events if sc is not None else knowledge.events
    event_bucket.setdefault(
        marker.event,
        TechnicalEventDefinition(
            id=marker.event,
            category=marker.category,
            name=marker.event,
            label=marker.label,
        ),
    )
    path = save_project_knowledge(knowledge, path=write_path, platform=platform)
    return {
        "knowledge_path": str(path),
        "added": True,
        "scenario": sc.id if sc else None,
        "marker": marker.to_dict(),
    }


def remove_behavior_marker(
    needle: str,
    *,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    """按 needle 删除行为 marker（全局或某业务场景）。"""
    needle = needle.strip()
    if not needle:
        raise KnowledgeError("marker needle 不能为空")
    knowledge = load_project_knowledge(start_dir, platform=platform)
    write_path = resolve_knowledge_write_path(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    bucket = sc.markers if sc is not None else knowledge.markers
    kept: List[BehaviorMarker] = []
    removed_marker: Optional[BehaviorMarker] = None
    for existing in bucket:
        if existing.needle == needle and removed_marker is None:
            removed_marker = existing
            continue
        kept.append(existing)
    if removed_marker is None:
        return {
            "knowledge_path": str(knowledge.source_path or write_path),
            "removed": False,
            "missing": True,
            "scenario": sc.id if sc else None,
            "needle": needle,
        }
    if sc is not None:
        sc.markers = kept
    else:
        knowledge.markers = kept
    path = save_project_knowledge(knowledge, path=write_path, platform=platform)
    return {
        "knowledge_path": str(path),
        "removed": True,
        "missing": False,
        "scenario": sc.id if sc else None,
        "marker": removed_marker.to_dict(),
    }


def audit_filter_terms(
    log_path: Path,
    *,
    preset: str = "user-behavior",
    scenario: Optional[str] = None,
    start_dir: Optional[Path] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    """对照日志文件统计词表命中量，辅助裁剪噪音词 / 一次性词。

    不做业务语义判断；只返回 count，由人/Agent 按 trunk|aux|once 规则取舍。
    """
    path = Path(log_path).expanduser().resolve()
    if not path.is_file():
        raise KnowledgeError(f"日志文件不存在: {path}")

    name = (preset or "user-behavior").strip() or "user-behavior"
    knowledge = load_project_knowledge(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    if sc is not None:
        terms = list(sc.filter_terms.get(name, []))
        scope = f"scenario:{sc.id}"
    else:
        terms = list(knowledge.filter_terms.get(name, []))
        scope = "global"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise KnowledgeError(f"无法读取日志: {path}\n{exc}") from exc

    hits: List[Dict[str, Any]] = []
    for term in terms:
        count = text.count(term)
        if count == 0:
            hint = "unused"
        elif count >= 100:
            hint = "noisy"
        elif count <= 3:
            hint = "sparse"
        else:
            hint = "ok"
        hits.append({"term": term, "count": count, "hint": hint})

    hits.sort(key=lambda x: (-int(x["count"]), str(x["term"])))
    return {
        "log_path": str(path),
        "preset": name,
        "scenario": sc.id if sc else None,
        "scope": scope,
        "term_count": len(terms),
        "hits": hits,
        "hints": {
            "noisy": "高频命中：慎作主干，常为轮询/心跳/引擎噪音",
            "sparse": "稀少命中：核对是否成败关键信号；若为具体文案/名称则用完可删",
            "unused": "本文件未命中：检查窗口/拼写，或确认为过窄词",
            "ok": "中等命中：通常可保留为辅助信号",
        },
    }


# ---------------------------------------------------------------------------
# 自成长：从日志「发现」该 grow 的词（grow suggest / grow auto）。
# grow audit 是裁剪侧（看已有词命中量）；suggest 是发现侧（找没覆盖的高频 token）。
# ---------------------------------------------------------------------------

# 高频 token 中不值得 grow 的通用噪音（级别/框架/连接词，无场景区分度；业务词不在此列）
_STOP_TOKENS = frozenset(
    {
        "error", "exception", "warning", "warn", "info", "debug", "notice",
        "fail", "failed", "failure", "success", "succeeded", "timeout", "retry",
        "retries", "request", "response", "method", "url", "http", "https",
        "json", "body", "header", "headers", "params", "parameter", "parameters",
        "value", "values", "status", "code", "message", "msg", "data", "time",
        "cost", "count", "total", "number", "name", "type", "key", "begin",
        "start", "end", "finish", "done", "true", "false", "null", "none",
        "thread", "main", "process", "system", "android", "ios", "app",
        "application", "network", "server", "client", "task", "event", "action",
        "click", "load", "update", "create", "remove", "add", "get", "set",
        "send", "receive", "open", "close", "connect", "init", "config",
        "default", "current", "result", "return", "state",
    }
)


def _is_noise_token(token: str) -> bool:
    """判定 token 是否不值得 grow（通用噪音）。"""
    low = token.lower()
    if low in _STOP_TOKENS:
        return True
    if token.isdigit():
        return True
    stripped = token.replace("_", "")
    if stripped.isdigit() and len(stripped) >= 4:
        return True  # 纯数字（ID/序号/时长）
    if len(token) > 64:
        return True  # 超长串
    # base64 疑似串（大小写字母+数字随机，>=8 字符，不常见于业务词）
    if len(token) >= 12 and re.fullmatch(r"[A-Za-z0-9+\/=]{12,}", token):
        return True  # 可能 base64
    return False


def _suggest_kind(token: str) -> str:
    """按 token 形态建议沉淀类型：全大写下划线（事件名）→ marker；其余 → term。"""
    if "_" in token and token.replace("_", "").isupper():
        return "marker"
    if any(ch.isupper() for ch in token[1:]) and token[0].islower():
        return "marker"  # 驼峰类名/接口名，适合行为 marker
    return "term"


def suggest_grow_terms(
    log_path: Path,
    *,
    preset: str = "user-behavior",
    scenario: Optional[str] = None,
    start_dir: Optional[Path] = None,
    platform: str = "ios",
    min_count: int = 5,
    limit: int = 12,
) -> Dict[str, Any]:
    """从日志自动发现「该 grow 的词」候选（只输出建议，不写盘）。

    候选来源：全量高频业务 token（``top_terms_in_text``，自动嗅探分段器），剔除：
    - 已在词表（全局 + 场景 preset 词）
    - 已是行为 marker needle
    - 通用噪音（级别/框架/纯数字/超长串）

    返回 ``{candidates: [{token, count, kind}]}``，由 Agent 判断或 ``apply_grow_suggestions``
    按阈值一键沉淀。
    """
    path = Path(log_path).expanduser().resolve()
    if not path.is_file():
        raise KnowledgeError(f"日志文件不存在: {path}")
    knowledge = load_project_knowledge(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    existing_terms = set(
        knowledge.effective_filter_terms(preset, scenario=scenario, platform=platform)
    )
    markers = sc.markers if sc is not None else knowledge.markers
    existing = existing_terms | {m.needle for m in markers}

    kind = detect_segmenter_kind(path)
    segmenter = build_segmenter(kind)

    candidates: List[Dict[str, Any]] = []
    for item in top_terms_in_text(
        path,
        segmenter=segmenter,
        exclude=existing,
        min_count=min_count,
        limit=limit * 2,
    ):
        token = str(item["token"])
        count = int(item["count"])
        if _is_noise_token(token):
            continue
        candidates.append({"token": token, "count": count, "kind": _suggest_kind(token)})
        if len(candidates) >= limit:
            break

    return {
        "log_path": str(path),
        "preset": preset,
        "scenario": sc.id if sc else None,
        "platform": platform,
        "segmenter": kind,
        "min_count": min_count,
        "existing_excluded": len(existing),
        "candidates": candidates,
    }


def apply_grow_suggestions(
    log_path: Path,
    *,
    preset: str = "user-behavior",
    scenario: Optional[str] = None,
    start_dir: Optional[Path] = None,
    platform: str = "ios",
    min_count: int = 5,
    limit: int = 12,
    add_terms: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """按阈值把高频候选自动沉淀进知识库（自成长闭环，``grow auto`` 用）。

    - 默认只加 behavior marker（安全：不影响过滤召回，只增强行为识别）
    - ``add_terms=True`` 时同时把候选加进 preset 词表（改变过滤召回，谨慎）
    - ``dry_run=True`` 只输出将沉淀的清单，不写盘
    """
    path = Path(log_path).expanduser().resolve()
    suggestion = suggest_grow_terms(
        path,
        preset=preset,
        scenario=scenario,
        start_dir=start_dir,
        platform=platform,
        min_count=min_count,
        limit=limit,
    )
    knowledge = load_project_knowledge(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    write_path = resolve_knowledge_write_path(start_dir, platform=platform)
    markers = sc.markers if sc is not None else knowledge.markers
    existing_markers = {m.needle for m in markers}
    term_bucket = sc.filter_terms if sc is not None else knowledge.filter_terms

    added_markers: List[Dict[str, Any]] = []
    added_terms: List[str] = []
    for cand in suggestion["candidates"]:
        token = str(cand["token"])
        if token in existing_markers:
            continue
        added_markers.append(
            {
                "needle": token,
                "category": "auto",
                "label": token,
                "count": int(cand["count"]),
            }
        )
        existing_markers.add(token)
        if add_terms and cand["kind"] != "marker":
            bucket = term_bucket.setdefault(preset, [])
            if token not in bucket:
                bucket.append(token)
                added_terms.append(token)

    payload = {
        "log_path": str(path),
        "preset": preset,
        "scenario": sc.id if sc else None,
        "platform": platform,
        "would_add_markers": added_markers,
        "would_add_terms": added_terms,
    }
    if dry_run:
        payload["dry_run"] = True
        return payload

    if added_markers:
        for item in added_markers:
            markers.append(
                BehaviorMarker(
                    needle=item["needle"],
                    category=item["category"],
                    label=item["label"],
                )
            )
    path_out = save_project_knowledge(knowledge, path=write_path, platform=platform)
    payload.update(
        {
            "knowledge_path": str(path_out),
            "added_markers": len(added_markers),
            "added_terms": len(added_terms),
            "dry_run": False,
        }
    )
    return payload


def add_learning(
    summary: str,
    *,
    tags: Optional[Sequence[str]] = None,
    evidence: str = "",
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    summary = summary.strip()
    if not summary:
        raise KnowledgeError("learning summary 不能为空")
    knowledge = load_project_knowledge(start_dir, platform=platform)
    write_path = resolve_knowledge_write_path(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    learning = Learning(
        summary=summary,
        tags=[str(t).strip() for t in (tags or []) if str(t).strip()],
        evidence=evidence or "",
        at=_now_iso(),
    )
    bucket = sc.learnings if sc is not None else knowledge.learnings
    bucket.append(learning)
    path = save_project_knowledge(knowledge, path=write_path, platform=platform)
    return {
        "knowledge_path": str(path),
        "scenario": sc.id if sc else None,
        "learning": learning.to_dict(),
        "count": len(bucket),
    }


def add_playbook(
    name: str,
    *,
    when: str = "",
    steps: Optional[Sequence[str]] = None,
    tags: Optional[Sequence[str]] = None,
    related_presets: Optional[Sequence[str]] = None,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    name = name.strip()
    if not name:
        raise KnowledgeError("playbook name 不能为空")
    knowledge = load_project_knowledge(start_dir, platform=platform)
    write_path = resolve_knowledge_write_path(start_dir, platform=platform)
    sc = _require_scenario(knowledge, scenario)
    bucket = sc.playbooks if sc is not None else knowledge.playbooks
    for existing in bucket:
        if existing.name == name:
            existing.when = when or existing.when
            if steps:
                existing.steps = [str(s).strip() for s in steps if str(s).strip()]
            if tags:
                existing.tags = merge_terms(existing.tags, list(tags))
            if related_presets:
                existing.related_presets = merge_terms(
                    existing.related_presets, list(related_presets)
                )
            path = save_project_knowledge(knowledge, path=write_path, platform=platform)
            return {
                "knowledge_path": str(path),
                "updated": True,
                "scenario": sc.id if sc else None,
                "playbook": existing.to_dict(),
            }
    playbook = Playbook(
        name=name,
        when=when or "",
        steps=[str(s).strip() for s in (steps or []) if str(s).strip()],
        tags=[str(t).strip() for t in (tags or []) if str(t).strip()],
        related_presets=[
            str(t).strip() for t in (related_presets or []) if str(t).strip()
        ],
    )
    bucket.append(playbook)
    path = save_project_knowledge(knowledge, path=write_path, platform=platform)
    return {
        "knowledge_path": str(path),
        "updated": False,
        "scenario": sc.id if sc else None,
        "playbook": playbook.to_dict(),
    }


def resolve_scenario_pattern(
    preset: str,
    *,
    scenario: Optional[str] = None,
    start_dir: Optional[Path] = None,
    base_pattern: str = "",
    platform: Optional[str] = None,
) -> str:
    """把场景词合并进已有 pattern（filter --scenario 用）。

    platform 给定时，只合并 platform 匹配或 common 的场景，避免 iOS/Android 互相污染。
    """
    from tracecite_core.text_filter import pattern_from_terms

    load_platform = platform or "ios"
    knowledge = load_project_knowledge(start_dir, platform=load_platform)
    if scenario and scenario not in knowledge.scenarios:
        sc = knowledge.scenarios.get(scenario)
        if platform is not None and sc is not None and sc.platform not in (
            "common",
            platform,
        ):
            raise KnowledgeError(
                f"场景 {scenario!r} 属于平台 {sc.platform}，与当前平台 {platform} 不符。"
            )
        raise KnowledgeError(
            f"未知场景 {scenario!r}。请先: tracecite-mobile grow scenario {scenario}"
        )
    terms = knowledge.effective_filter_terms(preset, scenario=scenario, platform=platform)
    terms_pattern = pattern_from_terms(terms)
    if base_pattern:
        return (
            f"(?:{base_pattern})|(?:{terms_pattern})"
            if terms_pattern
            else base_pattern
        )
    return terms_pattern

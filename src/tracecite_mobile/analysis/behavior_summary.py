# -*- coding: utf-8 -*-
"""Knowledge-driven behavior summaries with optional parser providers.

The public framework only matches configured markers and aggregates technical
events, behaviors, and scenarios. Product log formats are supplied by plugins.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from tracecite_core.events import AnalysisEvent

_TS_RE = re.compile(r"^(\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2})")
_APPLOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:[.,]\d{1,6})?")


def _parse_timestamp(text: str) -> Optional[str]:
    """Read a timestamp from supported generic mobile record headers."""
    m = _TS_RE.match(text)
    if m:
        return m.group(1)
    m = _APPLOG_TS_RE.match(text)
    if m:
        return m.group(1)
    return None


@dataclass(frozen=True)
class BehaviorParserContext:
    """Project-neutral inputs exposed to behavior parser plugins."""

    markers: Sequence[Any]
    technical_events: Mapping[str, Any]
    timestamp: Optional[str]


BehaviorParser = Callable[[str, BehaviorParserContext], Optional[AnalysisEvent]]
_BEHAVIOR_PARSERS: Dict[str, BehaviorParser] = {}


def register_behavior_parser(
    name: str,
    parser: BehaviorParser,
    *,
    replace: bool = False,
) -> None:
    key = str(name).strip()
    if not key:
        raise ValueError("behavior parser name cannot be empty")
    if key in _BEHAVIOR_PARSERS and not replace:
        raise ValueError(f"behavior parser already registered: {key}")
    _BEHAVIOR_PARSERS[key] = parser


def available_behavior_parsers() -> List[str]:
    return sorted(_BEHAVIOR_PARSERS)


def _parse_with_providers(
    text: str,
    *,
    markers: Sequence[Any],
    technical_events: Mapping[str, Any],
) -> Optional[AnalysisEvent]:
    context = BehaviorParserContext(
        markers=markers,
        technical_events=technical_events,
        timestamp=_parse_timestamp(text),
    )
    for parser in list(_BEHAVIOR_PARSERS.values()):
        event = parser(text, context)
        if event is not None:
            return event
    return None

def resolve_knowledge_layers(
    *,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Tuple[List[Any], Dict[str, Any], List[Any], Optional[Any]]:
    """Load the analyzer-owned L1/L2/L3/L4 definitions.

    The tuple return keeps this module lightweight while making the layer
    boundaries explicit: markers, technical events, business behaviors, and
    the selected scenario.
    """
    from .knowledge import KnowledgeError, load_project_knowledge

    knowledge = load_project_knowledge(start_dir, platform=platform)
    selected = None
    if scenario:
        selected = knowledge.scenarios.get(scenario)
        if selected is None:
            raise KnowledgeError(
                f"未知场景 {scenario!r}。请先: tracecite-mobile grow scenario {scenario}"
            )
    return (
        knowledge.marker_definitions(scenario=scenario),
        knowledge.technical_events(scenario=scenario),
        knowledge.behavior_definitions(scenario=scenario),
        selected,
    )


@dataclass
class BehaviorSummary:
    """一份 filtered / 行为日志的结构化摘要。"""

    source_path: str
    event_count: int = 0
    categories: Dict[str, int] = field(default_factory=dict)
    events: List[AnalysisEvent] = field(default_factory=list)
    technical_events: List[AnalysisEvent] = field(default_factory=list)
    behaviors: List[AnalysisEvent] = field(default_factory=list)
    scenario_results: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": self.source_path,
            "event_count": self.event_count,
            "categories": self.categories,
            "events": [e.to_dict() for e in self.events],
            "technical_event_count": len(self.technical_events),
            "technical_events": [e.to_dict() for e in self.technical_events],
            "behavior_count": len(self.behaviors),
            "behaviors": [e.to_dict() for e in self.behaviors],
            "scenario_results": self.scenario_results,
            "notes": self.notes,
        }


def _strip_filtered_header(text: str) -> str:
    if "# ---\n" in text:
        return text.split("# ---\n", 1)[1]
    return text


def _parse_marker(
    line: str,
    markers: Sequence[Union[Tuple[str, str, str], Any]],
    technical_events: Optional[Mapping[str, Any]] = None,
) -> Optional[AnalysisEvent]:
    """Create a generic technical event from the first configured marker."""
    for raw_marker in markers:
        if isinstance(raw_marker, tuple):
            needle, category, label = raw_marker
            marker_id = ""
            event_id = category
            match_kind = "contains"
            marker_attributes: Dict[str, Any] = {}
        else:
            needle = str(getattr(raw_marker, "needle", ""))
            category = str(getattr(raw_marker, "category", "marker"))
            label = str(getattr(raw_marker, "label", "") or needle)
            marker_id = str(getattr(raw_marker, "id", ""))
            event_id = str(getattr(raw_marker, "event", "") or category)
            match_kind = str(getattr(raw_marker, "match", "contains") or "contains")
            marker_attributes = dict(getattr(raw_marker, "attributes", {}) or {})
        matched = False
        if match_kind == "regex":
            try:
                matched = re.search(needle, line) is not None
            except re.error:
                matched = needle in line
        else:
            matched = needle in line
        if matched:
            definition = (technical_events or {}).get(event_id)
            technical_category = str(getattr(definition, "category", "") or category)
            technical_name = str(getattr(definition, "name", "") or event_id)
            technical_label = str(getattr(definition, "label", "") or label)
            attributes = dict(marker_attributes)
            attributes["marker_id"] = marker_id or None
            attributes["technical_event"] = event_id
            attributes = {key: value for key, value in attributes.items() if value is not None}
            return AnalysisEvent(
                timestamp=_parse_timestamp(line),
                category=technical_category,
                name=technical_name,
                label=technical_label,
                source="marker",
                attributes=attributes,
                text=line,
            )
    return None


def iter_behavior_events(
    chunks: Iterable[str],
    *,
    markers: Optional[Sequence[Union[Tuple[str, str, str], Any]]] = None,
    technical_events: Optional[Mapping[str, Any]] = None,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> List[AnalysisEvent]:
    """Parse complete records through providers, then configured markers."""
    resolved_technical_events: Dict[str, Any] = dict(technical_events or {})
    if markers is not None:
        resolved = list(markers)
    else:
        resolved, resolved_technical_events, _, _ = resolve_knowledge_layers(
            start_dir=start_dir, scenario=scenario, platform=platform
        )
    events: List[AnalysisEvent] = []
    for chunk in chunks:
        chunk = chunk.rstrip("\n")
        if not chunk or chunk.startswith("#"):
            continue
        ev = _parse_with_providers(
            chunk,
            markers=resolved,
            technical_events=resolved_technical_events,
        )
        if ev is None:
            ev = _parse_marker(chunk, resolved, resolved_technical_events)
        if ev is not None and resolved_technical_events:
            definition = resolved_technical_events.get(ev.name) or resolved_technical_events.get(ev.category)
            if definition is not None:
                ev.category = str(getattr(definition, "category", "") or ev.category)
                ev.name = str(getattr(definition, "name", "") or ev.name)
                if not ev.label:
                    ev.label = str(getattr(definition, "label", "") or "") or ev.label
        if ev is not None:
            events.append(ev)
    return events


def _technical_event_matches(event: AnalysisEvent, spec: Any) -> bool:
    if isinstance(spec, str):
        return spec in {event.name, event.category, event.label or ""}
    if not isinstance(spec, dict):
        return False
    expected = spec.get("event") or spec.get("technical_event") or spec.get("name")
    if expected and str(expected) not in {event.name, event.category}:
        return False
    if spec.get("category") and str(spec["category"]) != event.category:
        return False
    attrs = spec.get("attributes") or {}
    return all(event.attributes.get(str(key)) == value for key, value in attrs.items())


def apply_behavior_definitions(
    technical_events: Sequence[AnalysisEvent],
    definitions: Sequence[Any],
) -> Tuple[List[AnalysisEvent], List[AnalysisEvent]]:
    """Apply L3 business mappings and return (visible events, business events)."""
    visible: List[AnalysisEvent] = []
    business: List[AnalysisEvent] = []
    for event in technical_events:
        matched = None
        for definition in definitions:
            event_ids = list(getattr(definition, "events", []) or [])
            event_id = str(getattr(definition, "event", "") or "")
            match = getattr(definition, "match", {}) or {}
            candidates = event_ids + ([event_id] if event_id else [])
            if candidates and not any(_technical_event_matches(event, item) for item in candidates):
                continue
            if match and not _technical_event_matches(event, match):
                continue
            if not candidates and not match:
                continue
            matched = definition
            break
        if matched is None:
            visible.append(event)
            continue
        behavior_id = str(getattr(matched, "id", "behavior"))
        label = str(getattr(matched, "label", "") or getattr(matched, "title", "") or behavior_id)
        behavior = AnalysisEvent(
            timestamp=event.timestamp,
            category="behavior",
            name=behavior_id,
            label=label,
            source="behavior",
            attributes={
                **event.attributes,
                "technical_category": event.category,
                "technical_event": event.name,
                "behavior": behavior_id,
            },
            raw_ref=event.raw_ref,
            text=event.text,
        )
        visible.append(behavior)
        business.append(behavior)
    return visible, business


def _split_records(body: str) -> List[str]:
    """Split generic multi-line records when a supported header is detected."""
    lines = body.splitlines()
    has_applog_ts = any(_APPLOG_TS_RE.match(ln) for ln in lines[:200])
    if not has_applog_ts:
        return lines
    from tracecite_core import build_segmenter

    segmenter = build_segmenter("applog")
    numbered = ((i, ln) for i, ln in enumerate(lines, 1))
    return [rec.text for rec in segmenter.segment_lines(numbered)]


def dedupe_consecutive(events: List[AnalysisEvent]) -> List[AnalysisEvent]:
    """Deduplicate adjacent events with the same category and label."""
    out: List[AnalysisEvent] = []
    for ev in events:
        if out and out[-1].category == ev.category and out[-1].label == ev.label:
            continue
        out.append(ev)
    return out


def summarize_behavior_text(
    text: str,
    *,
    source_path: str = "",
    dedupe: bool = True,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> BehaviorSummary:
    body = _strip_filtered_header(text)
    markers, technical_definitions, behavior_definitions, selected_scenario = resolve_knowledge_layers(
        start_dir=start_dir,
        scenario=scenario,
        platform=platform,
    )
    technical_events = iter_behavior_events(
        _split_records(body),
        markers=markers,
        technical_events=technical_definitions,
        start_dir=start_dir,
        scenario=scenario,
        platform=platform,
    )
    if dedupe:
        technical_events = dedupe_consecutive(technical_events)
    events, behaviors = apply_behavior_definitions(
        technical_events, behavior_definitions
    )
    scenario_results: List[Dict[str, Any]] = []
    if selected_scenario is not None:
        from .scenario import evaluate_behavior_scenario

        scenario_results.append(evaluate_behavior_scenario(behaviors or events, selected_scenario))
    cats = Counter(e.category for e in events)
    notes = [
        "事件来自已注册 parser provider 或项目 knowledge marker。",
        "四层行为知识随排查写入 .tracecite/knowledge*.json（grow），不堆进 Core 或源码。",
    ]
    return BehaviorSummary(
        source_path=source_path,
        event_count=len(events),
        categories=dict(cats),
        events=events,
        technical_events=technical_events,
        behaviors=behaviors,
        scenario_results=scenario_results,
        notes=notes,
    )


def summarize_behavior_file(
    path: Path,
    *,
    dedupe: bool = True,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> BehaviorSummary:
    text = path.read_text(encoding="utf-8", errors="replace")
    return summarize_behavior_text(
        text,
        source_path=str(path),
        dedupe=dedupe,
        start_dir=start_dir or path.parent,
        scenario=scenario,
        platform=platform,
    )


def summarize_behavior_file_json(
    path: Path,
    *,
    dedupe: bool = True,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> str:
    return json.dumps(
        summarize_behavior_file(
            path,
            dedupe=dedupe,
            start_dir=start_dir,
            scenario=scenario,
            platform=platform,
        ).to_dict(),
        ensure_ascii=False,
        indent=2,
    )

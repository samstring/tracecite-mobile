"""版本化场景断言 DSL 与可扩展断言类型注册表。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from tracecite_core.events import AnalysisEvent, parse_event_datetime


class AssertionSpecError(RuntimeError):
    """断言规则不合法或扩展执行失败。"""


@dataclass(frozen=True)
class AssertionContext:
    text: str
    events: Sequence[AnalysisEvent]
    ignore_case: bool = False


@dataclass(frozen=True)
class AssertionOutcome:
    satisfied: bool
    hits: int
    details: Mapping[str, Any] = field(default_factory=dict)


AssertionEvaluator = Callable[[Mapping[str, Any], AssertionContext], AssertionOutcome]
_ASSERTION_TYPES: Dict[str, AssertionEvaluator] = {}


def register_assertion_type(
    name: str, evaluator: AssertionEvaluator, *, replace: bool = False
) -> None:
    """注册 ``assert.rules[].type``；重复注册默认失败。"""
    key = str(name).strip().lower()
    if not key:
        raise ValueError("assertion type 名不能为空")
    current = _ASSERTION_TYPES.get(key)
    if current is not None and current is not evaluator and not replace:
        raise ValueError(f"assertion type {key!r} 已注册")
    _ASSERTION_TYPES[key] = evaluator


def available_assertion_types() -> List[str]:
    return sorted(_ASSERTION_TYPES)


@dataclass
class Assertion:
    name: str
    required: bool
    satisfied: bool
    hits: int
    kind: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "satisfied": self.satisfied,
            "hits": self.hits,
        }
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class AssertionPackage:
    """断言包：回答证据是否满足结论门禁。"""

    assertions: List[Assertion] = field(default_factory=list)

    @property
    def all_required_satisfied(self) -> bool:
        return all(item.satisfied for item in self.assertions if item.required)

    @property
    def missing_required(self) -> List[str]:
        return [
            item.name
            for item in self.assertions
            if item.required and not item.satisfied
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "all_required_satisfied": self.all_required_satisfied,
            "missing_required": self.missing_required,
            "assertions": [item.to_dict() for item in self.assertions],
        }


def pattern_hits(text: str, pattern: str, *, ignore_case: bool) -> int:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return len(re.findall(pattern, text, flags))
    except re.error:
        return text.lower().count(pattern.lower()) if ignore_case else text.count(pattern)


def _field_value(event: AnalysisEvent, path: str) -> Any:
    if path in {"timestamp", "category", "name", "source", "label", "event_id"}:
        return getattr(event, path)
    current: Any = event.attributes
    for part in path.removeprefix("attributes.").split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _value_matches(value: Any, expected: Any, *, ignore_case: bool) -> bool:
    if isinstance(expected, (list, tuple)):
        return any(
            _value_matches(value, item, ignore_case=ignore_case) for item in expected
        )
    if value is None:
        return False
    actual = str(value)
    wanted = str(expected)
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.search(wanted, actual, flags) is not None
    except re.error:
        return actual.lower() == wanted.lower() if ignore_case else actual == wanted


def event_matches(event: AnalysisEvent, spec: Any, *, ignore_case: bool) -> bool:
    """公开的事件 matcher，供自定义断言复用内置字段语义。"""
    if isinstance(spec, str):
        return pattern_hits(event.searchable_text(), spec, ignore_case=ignore_case) > 0
    if not isinstance(spec, dict):
        return False
    pattern = spec.get("match")
    if pattern is not None and not event_matches(
        event, str(pattern), ignore_case=ignore_case
    ):
        return False
    for field_name in ("category", "name", "source", "label"):
        if field_name in spec and not _value_matches(
            _field_value(event, field_name),
            spec[field_name],
            ignore_case=ignore_case,
        ):
            return False
    attrs = spec.get("attributes") or {}
    if not isinstance(attrs, dict):
        return False
    for key, expected in attrs.items():
        if not _value_matches(
            _field_value(event, f"attributes.{key}"),
            expected,
            ignore_case=ignore_case,
        ):
            return False
    return pattern is not None or bool(attrs) or any(
        key in spec for key in ("category", "name", "source", "label")
    )


def duration_seconds(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*", str(raw), re.I)
    if not match:
        raise AssertionSpecError(
            f"非法时间窗: {raw!r}（示例: 500ms / 5s / 2m）"
        )
    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    return value * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]


def _sequence_match(
    events: Sequence[AnalysisEvent],
    steps: Sequence[Any],
    *,
    within: Optional[float],
    ignore_case: bool,
) -> Tuple[bool, List[str], Optional[float]]:
    if not steps:
        return False, [], None
    for start, event in enumerate(events):
        if not event_matches(event, steps[0], ignore_case=ignore_case):
            continue
        matched = [event]
        cursor = start + 1
        for step in steps[1:]:
            found = None
            while cursor < len(events):
                candidate = events[cursor]
                cursor += 1
                if event_matches(candidate, step, ignore_case=ignore_case):
                    found = candidate
                    break
            if found is None:
                matched = []
                break
            matched.append(found)
        if not matched:
            continue
        elapsed: Optional[float] = None
        if within is not None:
            first_ts = parse_event_datetime(matched[0].timestamp)
            last_ts = parse_event_datetime(matched[-1].timestamp)
            if first_ts is None or last_ts is None:
                continue
            elapsed = (last_ts - first_ts).total_seconds()
            if elapsed < 0 or elapsed > within:
                continue
        return True, [item.event_id for item in matched], elapsed
    return False, [], None


def _contains(rule: Mapping[str, Any], context: AssertionContext) -> AssertionOutcome:
    pattern = str(rule.get("match") or "")
    if not pattern:
        raise AssertionSpecError("contains 需要 match")
    hits = pattern_hits(context.text, pattern, ignore_case=context.ignore_case)
    minimum = int(rule.get("min", 1))
    maximum = rule.get("max")
    satisfied = hits >= minimum and (maximum is None or hits <= int(maximum))
    return AssertionOutcome(
        satisfied,
        hits,
        {"match": pattern, "min": minimum, "max": maximum},
    )


def _count(rule: Mapping[str, Any], context: AssertionContext) -> AssertionOutcome:
    matcher = rule.get("event", rule.get("match"))
    if matcher is None:
        raise AssertionSpecError("count 需要 event 或 match")
    matched = [
        event
        for event in context.events
        if event_matches(event, matcher, ignore_case=context.ignore_case)
    ]
    exact = rule.get("exact")
    minimum = int(rule.get("min", exact if exact is not None else 1))
    maximum = rule.get("max", exact)
    hits = len(matched)
    return AssertionOutcome(
        hits >= minimum and (maximum is None or hits <= int(maximum)),
        hits,
        {
            "event": matcher,
            "min": minimum,
            "max": maximum,
            "matched_event_ids": [event.event_id for event in matched[:20]],
        },
    )


def _absent(rule: Mapping[str, Any], context: AssertionContext) -> AssertionOutcome:
    matcher = rule.get("event", rule.get("match"))
    if matcher is None:
        raise AssertionSpecError("absent 需要 event 或 match")
    matched = [
        event
        for event in context.events
        if event_matches(event, matcher, ignore_case=context.ignore_case)
    ]
    return AssertionOutcome(
        not matched,
        len(matched),
        {
            "event": matcher,
            "matched_event_ids": [event.event_id for event in matched[:20]],
        },
    )


def _sequence(rule: Mapping[str, Any], context: AssertionContext) -> AssertionOutcome:
    kind = str(rule.get("type") or "sequence").lower()
    steps = rule.get("events") or rule.get("sequence")
    if kind == "before" and not steps:
        steps = [rule.get("first"), rule.get("then")]
    if not isinstance(steps, list) or not steps or any(step is None for step in steps):
        raise AssertionSpecError(f"{kind} 需要 events 数组")
    within = duration_seconds(rule.get("within"))
    satisfied, event_ids, elapsed = _sequence_match(
        context.events,
        steps,
        within=within,
        ignore_case=context.ignore_case,
    )
    return AssertionOutcome(
        satisfied,
        1 if satisfied else 0,
        {
            "events": steps,
            "within_seconds": within,
            "elapsed_seconds": elapsed,
            "matched_event_ids": event_ids,
        },
    )


register_assertion_type("contains", _contains)
register_assertion_type("count", _count)
register_assertion_type("absent", _absent)
register_assertion_type("sequence", _sequence)
register_assertion_type("before", _sequence)


def build_assertions(
    text: str,
    *,
    rules: Sequence[Mapping[str, Any]],
    events: Sequence[AnalysisEvent] = (),
    ignore_case: bool = False,
) -> AssertionPackage:
    """通过断言类型注册表执行 DSL。"""
    package = AssertionPackage()
    context = AssertionContext(text=text, events=events, ignore_case=ignore_case)
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise AssertionSpecError(f"assert.rules[{index}] 必须是对象")
        kind = str(rule.get("type") or "").strip().lower()
        if not kind:
            raise AssertionSpecError(f"assert.rules[{index}] 缺少 type")
        evaluator = _ASSERTION_TYPES.get(kind)
        if evaluator is None:
            known = ", ".join(available_assertion_types())
            raise AssertionSpecError(f"未知断言类型 {kind!r}（可用: {known}）")
        name = str(rule.get("name") or f"{kind}-{index + 1}")
        try:
            outcome = evaluator(rule, context)
        except AssertionSpecError as exc:
            raise AssertionSpecError(f"断言 {name}: {exc}") from exc
        except Exception as exc:
            raise AssertionSpecError(f"断言 {name} 执行失败: {exc}") from exc
        if not isinstance(outcome, AssertionOutcome):
            raise AssertionSpecError(
                f"断言类型 {kind!r} 必须返回 AssertionOutcome，"
                f"实际为 {type(outcome).__name__}"
            )
        package.assertions.append(
            Assertion(
                name=name,
                required=bool(rule.get("required", True)),
                satisfied=outcome.satisfied,
                hits=int(outcome.hits),
                kind=kind,
                details=dict(outcome.details),
            )
        )
    return package

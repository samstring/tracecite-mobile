# -*- coding: utf-8 -*-
"""声明式场景编排：新场景零编码。

一个「场景」= 一个 JSON/YAML 文件，回答四个问题：

1. **文本从哪来**   ``source``  —— 本地文件 / 目录 / 压缩包 / 任意外部命令
2. **怎么切、怎么筛** ``parse`` + ``filter`` —— 分段器 + 正则/preset/时间窗
3. **怎么判断够不够** ``assert`` —— 断言包，程序化的准确性门禁
4. **结果拿去干嘛**  ``actions`` —— 任意外部命令

设计约束：

- **不重造轮子**：过滤仍然走 ``log_filter.filter_text``，
  本模块只负责「把声明翻译成调用」，不复制任何过滤逻辑。
- **agent-native**：不配置任何模型、不调用任何大模型 API。
  产出是结构化 JSON，由**正在调用它的 Agent**自己读、自己推理。
- **省 token**：正文全量落盘到 ``.filtered/``，返回值里只有指针 + 统计 + 断言结论。

示例::

    {
      "name": "online-error",
      "source": {"type": "file", "path": "examples/demoapp.log"},
      "parse":  {"segmenter": "auto"},
      "filter": {"grep": "(?i)\\\\bE\\\\b|Exception", "tag": "online-error"},
      "assert": {"rules": [
        {"name": "has-exception", "type": "contains", "match": "Exception"}
      ]}
    }
"""

from __future__ import annotations

import json
import hashlib
import importlib.metadata
import codecs
import copy
import os
import re
import subprocess
import sys
import time
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tracecite_core.run import AnalysisRun, RunFile, RunIntegrityError, RunWorkspace
from tracecite_core.events import (
    AnalysisEvent,
    EventTransformContext,
    EventTransformError,
    apply_event_transformers,
    events_from_filter_result,
    parse_event_datetime,
    write_events_jsonl,
)
from tracecite_core.segmenter import Segmenter, build_segmenter, detect_segmenter_kind
from tracecite_core.source import Source, SourceError, resolve_source_spec
from tracecite_core.preprocess import run_preprocess_pipeline
from tracecite_core.text_filter import (
    DEFAULT_TEMPLATE_THRESHOLD,
    FilterError,
    FilterResult,
    _safe_tag,
    filter_text,
    resolve_preset,
)

from .assertions import AssertionSpecError, build_assertions
from .reporting import ReportContext, ReportOutputError, render_reports

# 命中记录数超过该阈值，提示 Agent「证据可能过多，建议收窄」
_COVERAGE_THRESHOLD = 200
SCENARIO_SCHEMA_VERSION = 2
_TIMESTAMP_PREFIX_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"|\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}"
    r"|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
)


class ScenarioError(RuntimeError):
    """场景配置或执行错误。"""


def validate_scenario_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """校验 v2 场景结构，返回可直接执行的规范化副本。"""
    if not isinstance(spec, dict):
        raise ScenarioError("场景定义顶层必须是对象")
    normalized = copy.deepcopy(spec)
    if "schema_version" not in normalized:
        raise ScenarioError(
            f"场景必须显式声明 schema_version={SCENARIO_SCHEMA_VERSION}"
        )
    version = int(normalized["schema_version"])
    if version != SCENARIO_SCHEMA_VERSION:
        raise ScenarioError(
            f"只支持场景 schema_version={SCENARIO_SCHEMA_VERSION}，实际为 {version}"
        )
    normalized["schema_version"] = version
    allowed = {
        "schema_version", "name", "description", "source", "parse", "filter",
        "events", "assert", "actions", "analysis", "output",
    }
    unknown = sorted(set(normalized) - allowed)
    if unknown:
        raise ScenarioError(f"场景包含未知顶层字段: {unknown}")
    if not str(normalized.get("name") or "").strip():
        raise ScenarioError("场景需要非空 name")
    for section in ("source", "filter"):
        if not isinstance(normalized.get(section), dict):
            raise ScenarioError(f"场景需要 {section} 对象")
    for section in ("parse", "events", "assert", "analysis", "output"):
        value = normalized.get(section)
        if value is not None and not isinstance(value, dict):
            raise ScenarioError(f"{section} 段必须是对象")

    source = normalized["source"]
    source_type = str(source.get("type") or "file").strip().lower()
    if source_type in {"file", "static", "path", "dir", "archive"}:
        unknown_source = sorted(
            set(source)
            - {
                "type", "path", "glob", "recursive", "limit", "policy",
                "encoding", "preprocess",
            }
        )
        if unknown_source:
            raise ScenarioError(f"source 段包含未知字段: {unknown_source}")
        if not str(source.get("path") or "").strip():
            raise ScenarioError("文件来源需要 source.path")
    elif source_type in {"live", "command", "cmd"}:
        unknown_source = sorted(
            set(source)
            - {
                "type", "cmd", "duration", "limit",
                "policy", "encoding", "preprocess",
            }
        )
        if unknown_source:
            raise ScenarioError(f"source 段包含未知字段: {unknown_source}")
        command = source.get("cmd")
        if not isinstance(command, list) or not command or any(
            not isinstance(item, str) or not item for item in command
        ):
            raise ScenarioError("live 来源的 source.cmd 必须是非空字符串数组")
    policy = str(source.get("policy", "all")).strip().lower()
    if policy not in {"all", "best_effort"}:
        raise ScenarioError("source.policy 仅支持 all 或 best_effort")
    source["policy"] = policy
    preprocess = source.get("preprocess") or []
    if not isinstance(preprocess, list) or any(
        not isinstance(item, dict) or not str(item.get("action") or "").strip()
        for item in preprocess
    ):
        raise ScenarioError("source.preprocess 必须是含 action 的对象数组")

    flt = normalized["filter"]
    unknown_filter = sorted(
        set(flt)
        - {
            "grep", "preset", "scenario", "tag", "snapshot", "pid",
            "tail_lines", "line_from", "line_to", "last", "since", "until",
            "fold", "stages",
        }
    )
    if unknown_filter:
        raise ScenarioError(f"filter 段包含未知字段: {unknown_filter}")
    stages = flt.get("stages") or []
    if stages and (
        not isinstance(stages, list)
        or any(not isinstance(item, dict) for item in stages)
    ):
        raise ScenarioError("filter.stages 必须是对象数组")
    if not (flt.get("grep") or flt.get("preset") or stages):
        raise ScenarioError("filter 段需要 grep / preset / stages")

    assertion_scope = str((normalized.get("assert") or {}).get("scope", "aggregate"))
    if assertion_scope not in {"aggregate", "per_source", "both"}:
        raise ScenarioError("assert.scope 仅支持 aggregate / per_source / both")
    assertion_cfg = normalized.get("assert") or {}
    unknown_assert = sorted(set(assertion_cfg) - {"scope", "ignore_case", "rules"})
    if unknown_assert:
        raise ScenarioError(f"assert 段包含未知字段: {unknown_assert}")
    rules = assertion_cfg.get("rules") or []
    if not isinstance(rules, list) or any(
        not isinstance(rule, dict) or not str(rule.get("type") or "").strip()
        for rule in rules
    ):
        raise ScenarioError("assert.rules 必须是含 type 的对象数组")

    events = normalized.get("events") or {}
    if sorted(set(events) - {"transforms"}):
        raise ScenarioError("events 段仅支持 transforms")
    transforms = events.get("transforms") or []
    if not isinstance(transforms, list):
        raise ScenarioError("events.transforms 必须是数组")

    output = normalized.get("output") or {}
    if "dir" in output:
        raise ScenarioError(
            "output.dir 已移除；证据固定写入 output.run_dir/<run_id>/evidence"
        )
    unknown_output = sorted(set(output) - {"run_dir", "reports", "pinned"})
    if unknown_output:
        raise ScenarioError(f"output 段包含未知字段: {unknown_output}")
    reports = output.get("reports") or []
    if not isinstance(reports, list):
        raise ScenarioError("output.reports 必须是数组")

    actions = normalized.get("actions") or []
    if not isinstance(actions, list):
        raise ScenarioError("actions 必须是数组")
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ScenarioError(f"actions[{index}] 必须是对象")
        command = action.get("run")
        if not isinstance(command, list) or not command or any(
            not isinstance(item, str) or not item for item in command
        ):
            raise ScenarioError(f"actions[{index}].run 必须是非空字符串数组")
        outputs = action.get("outputs") or []
        if not isinstance(outputs, list) or any(not isinstance(item, str) for item in outputs):
            raise ScenarioError(f"actions[{index}].outputs 必须是字符串数组")
        unknown_action = sorted(
            set(action) - {"name", "run", "required", "timeout", "outputs"}
        )
        if unknown_action:
            raise ScenarioError(
                f"actions[{index}] 包含未知字段: {unknown_action}"
            )
        try:
            timeout = float(action.get("timeout", 300))
        except (TypeError, ValueError) as exc:
            raise ScenarioError(f"actions[{index}].timeout 必须是数字") from exc
        if timeout <= 0:
            raise ScenarioError(f"actions[{index}].timeout 必须大于 0")
    return normalized


# ---------------------------------------------------------------------------
# Knowledge scenario layer (L4)
# ---------------------------------------------------------------------------


def _knowledge_event_matches(event: AnalysisEvent, spec: Any) -> bool:
    """Match a business/technical event without importing Core business terms."""
    if isinstance(spec, str):
        return spec in {event.name, event.category, event.label or ""}
    if not isinstance(spec, dict):
        return False
    expected = spec.get("event") or spec.get("behavior") or spec.get("name")
    if expected and str(expected) not in {event.name, event.category}:
        return False
    category = spec.get("category")
    if category and str(category) != event.category:
        return False
    label = spec.get("label")
    if label and str(label) not in (event.label or ""):
        return False
    attrs = spec.get("attributes") or {}
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            if event.attributes.get(str(key)) != value:
                return False
    return bool(expected or category or label or attrs)


def _knowledge_within_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().lower()
    if raw.endswith("ms"):
        return float(raw[:-2]) / 1000.0
    if raw.endswith("s"):
        return float(raw[:-1])
    return float(raw)


def evaluate_behavior_scenario(
    events: Sequence[AnalysisEvent],
    scenario: Any,
) -> Dict[str, Any]:
    """Evaluate L4 ordered behavior steps and declarative assertions.

    This is analyzer orchestration. It deliberately returns plain data and never
    adds product concepts to ``tracecite_core.AnalysisEvent`` or the Core transformer API.
    """
    steps = list(getattr(scenario, "steps", None) or (scenario.get("steps") if isinstance(scenario, dict) else []) or [])
    assertions = list(getattr(scenario, "assertions", None) or (scenario.get("assertions") if isinstance(scenario, dict) else []) or [])
    result: Dict[str, Any] = {
        "id": getattr(scenario, "id", "") or (scenario.get("id", "") if isinstance(scenario, dict) else ""),
        "title": getattr(scenario, "title", "") or (scenario.get("title", "") if isinstance(scenario, dict) else ""),
        "steps": [],
        "assertions": [],
        "passed": True,
    }
    cursor = 0
    previous_time: Optional[Any] = None
    for index, step in enumerate(steps):
        spec = step.get("match", step) if isinstance(step, dict) else step
        found = None
        for event_index in range(cursor, len(events)):
            if _knowledge_event_matches(events[event_index], spec):
                found = event_index
                break
        passed = found is not None
        if passed:
            cursor = found + 1
            current_time = parse_event_datetime(events[found].timestamp)
            within = _knowledge_within_seconds(
                step.get("within") if isinstance(step, dict) else None
            )
            if within is not None and previous_time is not None and current_time is not None:
                passed = (current_time - previous_time).total_seconds() <= within
            if passed:
                previous_time = current_time
        result["steps"].append({
            "index": index,
            "name": step.get("name") if isinstance(step, dict) else str(step),
            "matched": passed,
            "event_index": found,
        })
        result["passed"] = result["passed"] and passed

    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        kind = str(assertion.get("type") or "contains").strip().lower()
        spec = assertion.get("event") or assertion.get("match") or assertion
        hits = [event for event in events if _knowledge_event_matches(event, spec)]
        if kind in ("absent", "not_contains"):
            satisfied = not hits
        elif kind == "count":
            exact = assertion.get("exact")
            minimum = assertion.get("min", assertion.get("at_least"))
            maximum = assertion.get("max", assertion.get("at_most"))
            satisfied = True
            if exact is not None:
                satisfied = len(hits) == int(exact)
            if minimum is not None:
                satisfied = satisfied and len(hits) >= int(minimum)
            if maximum is not None:
                satisfied = satisfied and len(hits) <= int(maximum)
        elif kind == "sequence":
            sequence = assertion.get("events") or assertion.get("sequence") or []
            within = assertion.get("within")
            if within is not None:
                sequence = [
                    dict(item, within=within) if index > 0 and isinstance(item, dict) and "within" not in item else item
                    for index, item in enumerate(sequence)
                ]
            nested = evaluate_behavior_scenario(
                events,
                {"id": "sequence", "steps": sequence},
            )
            satisfied = bool(nested["passed"])
        else:
            satisfied = bool(hits)
        result["assertions"].append({
            "name": assertion.get("name") or kind,
            "type": kind,
            "hits": len(hits),
            "satisfied": satisfied,
        })
        result["passed"] = result["passed"] and satisfied
    return result


# ---------------------------------------------------------------------------
# spec 加载
# ---------------------------------------------------------------------------


def load_spec(path: Path) -> Dict[str, Any]:
    """加载场景定义。支持 JSON；装了 PyYAML 时也支持 YAML。"""
    path = Path(path).expanduser()
    if not path.is_file():
        raise ScenarioError(f"场景文件不存在: {path}")

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ScenarioError(
                f"读取 YAML 场景需要 PyYAML（pip install pyyaml），或改用 JSON: {path}"
            ) from exc
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ScenarioError(f"场景 JSON 解析失败 {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ScenarioError(f"场景文件顶层必须是对象: {path}")
    return validate_scenario_spec(data)


# ---------------------------------------------------------------------------
# 各段解析
# ---------------------------------------------------------------------------


def resolve_source_files(
    spec: Dict[str, Any], *, base_dir: Path, extract_dir: Optional[Path] = None
) -> Tuple[List[Path], Optional[Source], str, List[Path]]:
    """通过公共 source provider 注册表解析输入。"""
    src = spec.get("source") or {}
    if not isinstance(src, dict):
        raise ScenarioError("source 段必须是对象")
    source_spec = dict(src)
    if extract_dir is not None:
        source_spec["extract_dir"] = str(extract_dir)
        source_spec.setdefault("snapshot_dir", str(extract_dir.parent / "live"))
    resolved = resolve_source_spec(source_spec, base_dir=base_dir)
    files = list(resolved.files)
    limit = src.get("limit")
    if limit:
        files = files[: int(limit)]
    return (
        files,
        resolved.source,
        str(src.get("type") or "file"),
        list(resolved.containers),
    )


def resolve_segmenter(
    spec: Dict[str, Any],
    sample: Path,
    formats: Optional[Dict[str, Any]] = None,
) -> Tuple[Segmenter, str]:
    """解析 parse 段，返回 (分段器, 实际使用的名字)。``auto`` 走内容嗅探。

    ``parse.format`` 支持**声明式格式**：
    - dict：内联定义 ``{"start": 起始行正则, "timestamp_formats": [...], "multiline": true}``
    - str：按名字引用项目注册表（config.json ``formats`` 段），新增文本格式零代码
    """
    parse = spec.get("parse") or {}
    if not isinstance(parse, dict):
        raise ScenarioError("parse 段必须是对象")

    fmt = parse.get("format")
    if isinstance(fmt, dict):
        try:
            return build_segmenter(fmt), "format"
        except (TypeError, ValueError) as exc:
            raise ScenarioError(f"format 定义不合法: {exc}") from exc
    if isinstance(fmt, str):
        name = fmt.strip()
        if not name:
            raise ScenarioError("parse.format 名字不能为空")
        registry = formats or {}
        definition = registry.get(name)
        if definition is None:
            known = ", ".join(sorted(registry)) or "(空)"
            raise ScenarioError(
                f"未知 format 名 {name!r}（项目注册表可用: {known}）。"
                f"请在 config.json 的 \"formats\" 段注册，或改用内联 dict"
            )
        if not isinstance(definition, dict):
            raise ScenarioError(f"format {name!r} 注册值必须是对象: {definition!r}")
        try:
            return build_segmenter(dict(definition)), f"format:{name}"
        except (TypeError, ValueError) as exc:
            raise ScenarioError(f"format {name!r} 定义不合法: {exc}") from exc

    kind = str(parse.get("segmenter", "auto")).strip().lower()
    options = {k: v for k, v in parse.items() if k not in ("segmenter", "format")}

    if kind in ("", "auto"):
        kind = detect_segmenter_kind(sample)
        options = {k: v for k, v in options.items() if k in ("mode", "window")}

    try:
        return build_segmenter(kind, **options), kind
    except TypeError as exc:
        raise ScenarioError(f"segmenter {kind!r} 参数不匹配: {exc}") from exc
    except ValueError as exc:
        raise ScenarioError(str(exc)) from exc


def resolve_pattern(
    spec: Dict[str, Any],
    *,
    platform: str = "ios",
    start_dir: Optional[Path] = None,
    profile: Optional[Any] = None,
) -> Tuple[str, Optional[str]]:
    """解析 filter 段的匹配规则，返回 (pattern, tag)。

    优先级：``grep`` > ``preset``（+可选 ``scenario``）。
    preset / scenario 都来自 knowledge.json —— 规则是数据，不是代码。
    """
    flt = spec.get("filter") or {}
    if not isinstance(flt, dict):
        raise ScenarioError("filter 段必须是对象")

    tag = flt.get("tag")
    grep = flt.get("grep")
    if grep:
        return str(grep), tag

    preset = flt.get("preset")
    if not preset:
        raise ScenarioError("filter 段需要 grep 或 preset")

    from ..shared.config import load_project_profile

    resolved_profile = profile or load_project_profile(
        start_dir or Path.cwd(), platform=platform
    )
    pattern, default_tag = resolve_preset(
        str(preset), resolved_profile.filter_preset_table()
    )
    tag = tag or default_tag

    sub_scenario = flt.get("scenario")
    if sub_scenario:
        from .knowledge import resolve_scenario_pattern

        pattern = resolve_scenario_pattern(
            str(preset),
            scenario=str(sub_scenario),
            start_dir=start_dir or Path.cwd(),
            base_pattern=pattern,
            platform=platform,
        )
    return pattern, tag


def _read_text_tail(path: Path, limit: int = 2000) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit * 4))
        return handle.read().decode("utf-8", errors="replace")[-limit:]


def run_actions(
    actions: Sequence[Dict[str, Any]],
    *,
    env_extra: Dict[str, str],
    output_dir: Path,
    base_dir: Path,
) -> List[Dict[str, Any]]:
    """以 argv 方式执行 action，并校验其声明产物。"""
    results: List[Dict[str, Any]] = []
    if not actions:
        return results

    env = dict(os.environ)
    env.update(env_extra)

    for index, action in enumerate(actions):
        command = list(action["run"])
        required = bool(action.get("required", True))
        timeout = float(action.get("timeout", 300))
        action_dir = output_dir / f"{index + 1:02d}_{_safe_tag(str(action.get('name') or 'action'))}"
        action_dir.mkdir(parents=True, exist_ok=True)
        action_env = dict(env)
        action_env["TRACECITE_CORE_ACTION_OUTPUT_DIR"] = str(action_dir)
        entry: Dict[str, Any] = {
            "name": str(action.get("name") or f"action-{index + 1}"),
            "run": command,
            "required": required,
            "timeout": timeout,
        }
        stdout_path = action_dir / "stdout.log"
        stderr_path = action_dir / "stderr.log"
        entry["stdout_path"] = str(stdout_path)
        entry["stderr_path"] = str(stderr_path)
        try:
            with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                proc = subprocess.run(
                    command,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    env=action_env,
                    cwd=Path(base_dir).resolve(),
                    timeout=timeout,
                    check=False,
                )
            entry["returncode"] = proc.returncode
        except subprocess.TimeoutExpired:
            entry["returncode"] = 124
            entry["error"] = f"action 超时（>{timeout:g}s）"
        except Exception as exc:  # noqa: BLE001 - 记录为交付门禁结果
            entry["returncode"] = -1
            entry["error"] = str(exc)
        entry["stdout_tail"] = _read_text_tail(stdout_path)
        entry["stderr_tail"] = _read_text_tail(stderr_path)

        declared_outputs: List[str] = []
        output_error: Optional[str] = None
        for raw in action.get("outputs") or []:
            relative = Path(raw)
            if relative.is_absolute() or ".." in relative.parts:
                output_error = f"非法 action 输出路径: {raw}"
                break
            produced = (action_dir / relative).resolve()
            declared_path = action_dir / relative
            if (
                not produced.is_relative_to(action_dir.resolve())
                or declared_path.is_symlink()
                or not produced.is_file()
            ):
                output_error = f"action 未生成声明产物: {produced}"
                break
            declared_outputs.append(str(produced))
        produced_files = sorted(
            str(path.resolve())
            for path in action_dir.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path not in {stdout_path, stderr_path}
        )
        undeclared = sorted(set(produced_files) - set(declared_outputs))
        if undeclared and output_error is None:
            output_error = f"action 生成了未声明产物: {undeclared}"
        if output_error:
            entry["error"] = output_error
        entry["outputs"] = declared_outputs
        entry["produced_files"] = produced_files
        if undeclared:
            entry["undeclared_outputs"] = undeclared
        entry["satisfied"] = entry.get("returncode") == 0 and output_error is None
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _estimate_tokens_path(path: Path) -> int:
    chars = 0
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), ""):
                chars += len(chunk)
    except OSError:
        return 0
    return chars // 3


def _rules_need_text(rules: Sequence[Dict[str, Any]]) -> bool:
    event_only = {"count", "absent", "sequence", "before"}
    return any(str(rule.get("type") or "").lower() not in event_only for rule in rules)


def _event_sort_key(event: AnalysisEvent) -> Tuple[int, float, str]:
    parsed = parse_event_datetime(event.timestamp)
    if parsed is None:
        return (1, 0.0, event.event_id)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return (0, parsed.timestamp(), event.event_id)


def _result_payload(
    result: FilterResult,
    *,
    segmenter_kind: str,
    assertion_rules: Sequence[Dict[str, Any]],
    event_transforms: Sequence[Any] = (),
    scenario_name: str = "",
    platform: str = "",
    ignore_case: bool = False,
    coverage_threshold: int = _COVERAGE_THRESHOLD,
    assertion_scope: str = "aggregate",
) -> Dict[str, Any]:
    """把一次过滤结果整理成 Agent 友好的结构化摘要（不含正文）。"""
    payload = result.to_dict()
    payload["segmenter"] = segmenter_kind

    payload["estimated_tokens"] = _estimate_tokens_path(Path(result.output_path))
    events = events_from_filter_result(result)
    if event_transforms:
        events = apply_event_transformers(
            events,
            event_transforms,
            context=EventTransformContext(
                scenario=scenario_name,
                platform=platform,
                source_path=str(result.original_source),
            ),
        )
    events_path = write_events_jsonl(
        Path(str(result.output_path) + ".events.jsonl"), events
    )
    payload["events_path"] = str(events_path)
    payload["event_count"] = len(events)
    assertion_text = ""
    if _rules_need_text(assertion_rules):
        assertion_text = "".join(event.text or "" for event in events)
    if assertion_scope in {"per_source", "both"}:
        payload["assertions"] = build_assertions(
            assertion_text,
            rules=assertion_rules,
            events=events,
            ignore_case=ignore_case,
        ).to_dict()
    # 多文件场景最终在统一事件流上断言；这两个进程内字段会在返回前移除，
    # 不进入 JSON 输出或 manifest。
    payload["_assertion_text"] = assertion_text
    payload["_events"] = events

    warnings: List[str] = []
    if result.match_records == 0:
        warnings.append(
            "零命中：请放宽 pattern、换时间窗或加宽采集范围；"
            "不要据此写「无问题」，应写「证据不足」。"
            "若使用 preset 且日志为线上事件埋点格式：词表可能按真机 syslog 构建，"
            "两套埋点体系不同（真机=行为行、线上=事件上报块），请核对词表是否匹配该日志格式"
        )
    elif result.match_records > coverage_threshold:
        warnings.append(
            f"命中 {result.match_records} 条（>{coverage_threshold}），"
            "证据偏多容易冲淡信号：建议收窄时间窗或提高 pattern 精度"
        )
    # 未命中占比极高时的语义提示：窄事件过滤（单 EVENT 词）属正常；
    # 宽行为过滤则提示词表可能不匹配该日志埋点体系
    ratio = float((result.unmatched_summary or {}).get("unmatched_ratio") or 0)
    if result.match_records > 0 and ratio > 0.9:
        warnings.append(
            f"未命中占比极高（{ratio:.1%}）：若为单事件/窄 pattern 过滤属正常，"
            "ratio 参考意义有限；若为宽行为过滤，词表可能与该日志埋点体系不匹配"
        )
    # 折叠质量告警：模板碎片化时折叠视图既不能省 token 也不能看分布
    tstats = result.template_stats or {}
    tpl_count = int(tstats.get("templates") or 0)
    fold_ratio = float(tstats.get("fold_ratio") or 0)
    if tpl_count > 0 and fold_ratio < 0.5:
        warnings.append(
            f"模板折叠碎片化：{tpl_count} 个模板只覆盖 {fold_ratio:.0%} 的命中"
            f"（{tstats.get('singleton_templates')} 个单例），折叠已失去汇总价值，"
            "建议按字段收窄后重试或直接看 .filtered 原文"
        )
    if not assertion_rules or not any(rule.get("required", True) for rule in assertion_rules):
        warnings.append(
            "本场景未定义必需断言，all_required_satisfied 恒为 true（空真），"
            "不可据此认为结论已被验证"
        )
    payload["coverage_warning"] = warnings

    return payload


def _run_one_filter(
    path: Path,
    *,
    flt: Dict[str, Any],
    pattern: str,
    tag: Optional[str],
    segmenter: Segmenter,
    output_path: Optional[Path] = None,
    encoding: str = "utf-8",
    template_threshold: int = 0,
) -> Tuple[Optional[Dict[str, Any]], Optional[FilterResult]]:
    """对单个文件跑一次过滤，返回 (payload 或 error 条目, result 或 None)。

    成功时 payload 为 None（由调用方组装 _result_payload），result 为过滤结果；
    失败时 payload 为 error 条目，result 为 None。
    """
    try:
        result = filter_text(
            path,
            pattern=pattern,
            tag=tag,
            output_path=output_path,
            snapshot=bool(flt.get("snapshot", False)),
            pid=flt.get("pid"),
            tail_lines=flt.get("tail_lines"),
            line_from=flt.get("line_from"),
            line_to=flt.get("line_to"),
            last=flt.get("last"),
            since=flt.get("since"),
            until=flt.get("until"),
            segmenter=segmenter,
            template_threshold=template_threshold,
            encoding=encoding,
        )
    except Exception as exc:  # 单个来源失败由 source.policy 决定是否阻断
        return {"input": str(path), "error": str(exc)}, None
    return None, result


def _scenario_output_path(
    output_dir: Optional[Path],
    *,
    source: Path,
    tag: Optional[str],
    index: int,
) -> Optional[Path]:
    if output_dir is None:
        return None
    return output_dir / f"{index + 1:04d}_{_safe_tag(tag or 'scenario')}_{source.name}"


def _run_scenario_impl(
    spec: Dict[str, Any],
    *,
    base_dir: Path,
    workspace: RunWorkspace,
    run: AnalysisRun,
    platform: str = "ios",
    start_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行一个场景，返回结构化结果（正文在磁盘，返回值只给指针与结论）。

    ``start_dir`` 指定项目上下文（profile/知识库查找起点）；默认 Path.cwd()，
    与 CLI「在工程目录执行」语义一致。测试或脚本可显式传入场景目录。
    """
    name = str(spec.get("name") or "unnamed")
    flt = spec.get("filter") or {}
    asserts = spec.get("assert") or {}
    if not isinstance(asserts, dict):
        raise ScenarioError("assert 段必须是对象")
    assertion_rules = list(asserts.get("rules") or [])
    assert_ignore_case = bool(asserts.get("ignore_case", False))
    assertion_scope = str(asserts.get("scope", "aggregate"))
    events_spec = spec.get("events") or {}
    if not isinstance(events_spec, dict):
        raise ScenarioError("events 段必须是对象")
    event_transforms = events_spec.get("transforms") or []
    if not isinstance(event_transforms, list):
        raise ScenarioError("events.transforms 必须是数组")

    # 分析阈值：spec.analysis > profile.analysis > 代码默认；fold:true 强制开启折叠
    from ..shared.config import load_project_profile

    profile = load_project_profile(start_dir if start_dir is not None else Path.cwd(), platform=platform)
    analysis = {**profile.analysis, **(spec.get("analysis") or {})}
    coverage_threshold = int(analysis.get("coverage_threshold", _COVERAGE_THRESHOLD))
    template_threshold = int(analysis.get("template_threshold", 0))
    if flt.get("fold"):
        template_threshold = DEFAULT_TEMPLATE_THRESHOLD

    source_files, live, source_provider, source_containers = resolve_source_files(
        spec,
        base_dir=base_dir,
        extract_dir=workspace.temp_dir / "extract",
    )
    if not source_files:
        raise ScenarioError(f"场景 {name}: 没有解析到任何待分析文件")

    src_cfg = spec.get("source") or {}
    file_encoding = str(src_cfg.get("encoding", "utf-8")) if isinstance(src_cfg, dict) else "utf-8"
    try:
        codecs.lookup(file_encoding)
    except LookupError as exc:
        raise ScenarioError(f"未知 source.encoding: {file_encoding!r}") from exc

    files: List[Path] = []
    input_lineage: List[Dict[str, Any]] = []
    container_snapshots: Dict[Path, Path] = {}
    for container_index, container_path in enumerate(source_containers):
        container = RunFile.from_path("source_container", container_path)
        container_snapshot = workspace.freeze_context(
            container_path,
            name=f"source_container_{container_index + 1:04d}",
        )
        run.add_input(
            container_snapshot,
            role="source_container",
            metadata={
                "original_path": container.path,
                "original_sha256": container.sha256,
            },
        )
        container_snapshots[Path(container_path).resolve()] = container_snapshot
    for index, original in enumerate(source_files):
        source_file = RunFile.from_path("source_original", Path(original))
        if source_file.sha256 is None:
            raise RunIntegrityError(f"无法读取输入文件: {original}")
        frozen = workspace.freeze_input(Path(original), index=index)
        metadata: Dict[str, Any] = {
            "original_size": source_file.size,
            "source_provider": source_provider,
        }
        if Path(original).resolve().is_relative_to(workspace.temp_dir):
            owning_container: Optional[Path] = None
            for container_path, snapshot_path in container_snapshots.items():
                extracted_root = workspace.temp_dir / "extract" / container_path.stem
                if Path(original).resolve().is_relative_to(extracted_root.resolve()):
                    owning_container = snapshot_path
                    break
            if owning_container is None and len(container_snapshots) == 1:
                owning_container = next(iter(container_snapshots.values()))
            metadata.update(
                {
                    "container_snapshot": (
                        str(owning_container) if owning_container else None
                    ),
                    "member_path": str(Path(original).relative_to(workspace.temp_dir)),
                }
            )
        else:
            metadata.update(
                {
                    "original_path": source_file.path,
                    "original_sha256": source_file.sha256,
                }
            )
        run.add_input(frozen, role="source_snapshot", metadata=metadata)
        files.append(frozen)
        input_lineage.append({"original": source_file.path, "snapshot": str(frozen)})

    preprocess_steps = (src_cfg or {}).get("preprocess") if isinstance(src_cfg, dict) else None
    if preprocess_steps:
        for i in range(len(files)):
            source_snapshot = files[i]
            processed = run_preprocess_pipeline(
                files[i], preprocess_steps,
                temp_dir=workspace.preprocess_dir,
            )
            run.add_input(
                processed,
                role="preprocessed_source",
                metadata={"parent_path": str(source_snapshot)},
            )
            files[i] = processed
            input_lineage[i]["consumed"] = str(processed)

    for index in range(len(files)):
        input_lineage[index].setdefault("consumed", str(files[index]))

    parsed_files: List[Tuple[Path, Segmenter, str]] = []
    for file_path in files:
        segmenter, seg_kind = resolve_segmenter(
            spec, file_path, formats=profile.formats
        )
        parsed_files.append((file_path, segmenter, seg_kind))
        pattern_attr = getattr(segmenter, "pattern", None)
        if pattern_attr is None:
            continue
        sample_lines = []
        with file_path.open("r", encoding=file_encoding, errors="replace") as _fh:
            for _i, _l in enumerate(_fh):
                if _i >= 200:
                    break
                sample_lines.append(_l)
        if sample_lines:
            matched = sum(1 for _l in sample_lines if pattern_attr.match(_l))
            timestamp_candidates = [
                line for line in sample_lines if _TIMESTAMP_PREFIX_RE.match(line)
            ]
            missed_timestamp_starts = sum(
                1 for line in timestamp_candidates if not pattern_attr.match(line)
            )
            miss_ratio = (
                missed_timestamp_starts / len(timestamp_candidates)
                if timestamp_candidates
                else 0.0
            )
            if missed_timestamp_starts and miss_ratio >= 0.2:
                import warnings as _warn
                _warn.warn(
                    f"格式自检: {missed_timestamp_starts}/{len(timestamp_candidates)} "
                    f"条时间戳候选未被 start 正则识别 ({miss_ratio:.1%})，"
                    "可能定义不匹配日志格式",
                    stacklevel=2,
                )

    segmenter_kinds = [kind for _, _, kind in parsed_files]
    seg_kind = segmenter_kinds[0] if len(set(segmenter_kinds)) == 1 else "mixed"
    output_dir = workspace.evidence_dir

    stages_spec = flt.get("stages")
    stage_summaries: List[Dict[str, Any]] = []
    if isinstance(stages_spec, list) and stages_spec:
        # 多 stage 编排：每段独立 resolve pattern 并过滤同一 source，
        # 先粗后精看各段命中数收敛；断言按最后一段（精筛结果）评估。
        outputs: List[Dict[str, Any]] = []
        pattern = ""
        tag = None
        for idx, stage in enumerate(stages_spec):
            if not isinstance(stage, dict):
                raise ScenarioError(f"场景 {name}: stages[{idx}] 必须是对象")
            stage_filter = {k: v for k, v in flt.items() if k != "stages"}
            for key in ("grep", "preset", "scenario", "tag"):
                if key in stage:
                    stage_filter[key] = stage[key]
            stage_pattern, stage_tag = resolve_pattern(
                {"filter": stage_filter},
                platform=platform,
                start_dir=start_dir,
                profile=profile,
            )
            stage_name = str(stage.get("name") or f"stage{idx}")
            safe_name = _safe_tag(stage_name)
            stage_tag = f"{stage_tag or 'scenario'}_{safe_name}"

            per_outputs: List[Dict[str, Any]] = []
            ok: List[Dict[str, Any]] = []
            for file_index, (path, file_segmenter, file_seg_kind) in enumerate(parsed_files):
                payload, result = _run_one_filter(
                    Path(path),
                    flt=flt,
                    pattern=stage_pattern,
                    tag=stage_tag,
                    segmenter=file_segmenter,
                    output_path=_scenario_output_path(
                        output_dir,
                        source=Path(path),
                        tag=stage_tag,
                        index=file_index,
                    ),
                    encoding=file_encoding,
                    template_threshold=template_threshold,
                )
                if result is not None:
                    payload = _result_payload(
                        result,
                        segmenter_kind=file_seg_kind,
                        assertion_rules=assertion_rules,
                        event_transforms=event_transforms,
                        scenario_name=name,
                        platform=platform,
                        ignore_case=assert_ignore_case,
                        coverage_threshold=coverage_threshold,
                        assertion_scope=assertion_scope,
                    )
                    ok.append(payload)
                per_outputs.append(payload)

            stage_summaries.append(
                {
                    "name": stage_name,
                    "pattern": stage_pattern,
                    "tag": stage_tag,
                    "match_records": sum(
                        int(o.get("match_records") or 0) for o in ok
                    ),
                    "estimated_tokens": sum(
                        int(o.get("estimated_tokens") or 0) for o in ok
                    ),
                    "outputs": per_outputs,
                }
            )
            outputs = per_outputs
            pattern = stage_pattern
            tag = stage_tag
    else:
        # 单段路径：结果结构不包含 stages 字段
        pattern, tag = resolve_pattern(
            spec, platform=platform, start_dir=start_dir, profile=profile
        )
        outputs = []
        for file_index, (path, file_segmenter, file_seg_kind) in enumerate(parsed_files):
            payload, result = _run_one_filter(
                Path(path),
                flt=flt,
                pattern=pattern,
                tag=tag,
                segmenter=file_segmenter,
                output_path=_scenario_output_path(
                    output_dir,
                    source=Path(path),
                    tag=tag,
                    index=file_index,
                ),
                encoding=file_encoding,
                template_threshold=template_threshold,
            )
            if result is not None:
                payload = _result_payload(
                    result,
                    segmenter_kind=file_seg_kind,
                    assertion_rules=assertion_rules,
                    event_transforms=event_transforms,
                    scenario_name=name,
                    platform=platform,
                    ignore_case=assert_ignore_case,
                    coverage_threshold=coverage_threshold,
                    assertion_scope=assertion_scope,
                )
            outputs.append(payload)

    total_records = sum(int(o.get("match_records") or 0) for o in outputs)
    ok_outputs = [o for o in outputs if "error" not in o]
    aggregate_events: List[AnalysisEvent] = []
    aggregate_text: List[str] = []
    for output in ok_outputs:
        aggregate_events.extend(output.pop("_events", []))
        aggregate_text.append(str(output.pop("_assertion_text", "")))
    for stage in stage_summaries:
        for output in stage.get("outputs") or []:
            if isinstance(output, dict):
                output.pop("_events", None)
                output.pop("_assertion_text", None)
    aggregate_events.sort(key=_event_sort_key)
    per_source_assertions = [
        output.get("assertions")
        for output in ok_outputs
        if isinstance(output.get("assertions"), dict)
    ]
    if assertion_scope in {"aggregate", "both"}:
        aggregate_assertions = build_assertions(
            "\n".join(aggregate_text),
            rules=assertion_rules,
            events=aggregate_events,
            ignore_case=assert_ignore_case,
        )
        assertions_payload = aggregate_assertions.to_dict()
        assertion_ok = aggregate_assertions.all_required_satisfied
        if assertion_scope == "both":
            assertion_ok = bool(per_source_assertions) and assertion_ok and all(
                bool(item.get("all_required_satisfied"))
                for item in per_source_assertions
            )
            assertions_payload["all_required_satisfied"] = assertion_ok
    else:
        assertion_ok = bool(per_source_assertions) and all(
            bool(item.get("all_required_satisfied")) for item in per_source_assertions
        )
        assertions_payload = {
            "all_required_satisfied": assertion_ok,
            "missing_required": sorted(
                {
                    name
                    for item in per_source_assertions
                    for name in item.get("missing_required") or []
                }
            ),
            "assertions": [],
        }
    assertions_payload["scope"] = assertion_scope
    if per_source_assertions:
        assertions_payload["per_source"] = per_source_assertions

    failed_outputs = [output for output in outputs if "error" in output]
    source_policy = str(src_cfg.get("policy", "all"))
    source_complete = not failed_outputs
    source_accepted = source_complete or (
        source_policy == "best_effort" and bool(ok_outputs)
    )
    all_required_ok = bool(ok_outputs) and source_accepted and assertion_ok

    env_extra = {
        "TRACECITE_CORE_RUN_ID": run.run_id,
        "TRACECITE_CORE_SCENARIO": name,
        "TRACECITE_CORE_MATCH_RECORDS": str(total_records),
        "TRACECITE_CORE_RUN_DIR": str(workspace.root),
    }
    if ok_outputs:
        env_extra["TRACECITE_CORE_OUTPUT"] = str(ok_outputs[0].get("output_path", ""))
        env_extra["TRACECITE_CORE_OUTPUTS"] = "\n".join(
            str(o.get("output_path", "")) for o in ok_outputs
        )

    summary: Dict[str, Any] = {
        "scenario": name,
        "description": spec.get("description", ""),
        "platform": platform,
        "segmenter": seg_kind,
        "segmenters": [
            {"input": str(path), "segmenter": kind}
            for path, _, kind in parsed_files
        ],
        "pattern": pattern,
        "input_files": [str(p) for p in files],
        "input_lineage": input_lineage,
        "source_provider": source_provider,
        "source_completeness": {
            "policy": source_policy,
            "expected": len(outputs),
            "succeeded": len(ok_outputs),
            "failed": len(failed_outputs),
            "complete": source_complete,
            "accepted": source_accepted,
        },
        "total_match_records": total_records,
        "required_satisfied": all_required_ok,
        "assertions": assertions_payload,
        "results": outputs,
        "run_dir": str(workspace.root),
    }
    if stage_summaries:
        summary["stages"] = stage_summaries
    if live is not None:
        immutable_references = [str(path) for path in container_snapshots.values()]
        summary["source_reference"] = (
            immutable_references[0] if immutable_references else str(live.original)
        )
        if len(immutable_references) > 1:
            summary["source_containers"] = immutable_references
        if source_provider == "live":
            summary["live_capture"] = summary["source_reference"]

    actions = spec.get("actions") or []
    if actions:
        action_results = run_actions(
            actions,
            env_extra=env_extra,
            output_dir=workspace.actions_dir,
            base_dir=base_dir,
        )
        summary["actions"] = action_results
        actions_ok = all(
            bool(item.get("satisfied"))
            for item in action_results
            if item.get("required", True)
        )
        summary["delivery_satisfied"] = actions_ok
        summary["required_satisfied"] = bool(summary["required_satisfied"] and actions_ok)
    else:
        summary["delivery_satisfied"] = True

    return summary


def _run_root(spec: Dict[str, Any], *, base_dir: Path, start_dir: Optional[Path]) -> Path:
    output = spec.get("output") or {}
    if isinstance(output, dict) and output.get("run_dir"):
        configured = Path(str(output["run_dir"])).expanduser()
        return configured if configured.is_absolute() else (base_dir / configured)
    return (start_dir or base_dir) / ".tracecite" / "runs"


def _report_specs(spec: Dict[str, Any]) -> Sequence[Any]:
    output = spec.get("output") or {}
    if not isinstance(output, dict):
        raise ScenarioError("output 段必须是对象")
    reports = output.get("reports") or []
    if not isinstance(reports, list):
        raise ScenarioError("output.reports 必须是数组")
    return reports


def _register_run_files(
    run: AnalysisRun, summary: Dict[str, Any], *, workspace: RunWorkspace
) -> None:
    role_by_key = {
        "output_path": "filtered_log",
        "snapshot_path": "snapshot",
        "history_path": "filter_history",
        "hits_path": "hit_metadata",
        "templates_path": "templates",
        "events_path": "events",
        "records_path": "matched_records",
    }
    rows: List[Dict[str, Any]] = list(summary.get("results") or [])
    for stage in summary.get("stages") or []:
        rows.extend(stage.get("outputs") or [])
    for row in rows:
        if not isinstance(row, dict) or "error" in row:
            continue
        for key, role in role_by_key.items():
            raw = row.get(key)
            if raw and Path(str(raw)).exists():
                path = Path(str(raw)).resolve()
                if not path.is_relative_to(workspace.root):
                    raise RunIntegrityError(f"场景产物逃逸运行目录: {path}")
                run.add_artifact(path, role=role)
    for action in summary.get("actions") or []:
        declared = set(action.get("outputs") or [])
        for raw in action.get("produced_files") or []:
            path = Path(str(raw)).resolve()
            if not path.is_relative_to(workspace.root):
                raise RunIntegrityError(f"action 产物逃逸运行目录: {path}")
            run.add_artifact(
                path,
                role="action_output",
                metadata={
                    "action": action.get("name"),
                    "declared": str(path) in declared,
                },
            )
        for key in ("stdout_path", "stderr_path"):
            raw = action.get(key)
            if raw and Path(str(raw)).is_file():
                run.add_artifact(
                    Path(str(raw)),
                    role="action_log",
                    metadata={"action": action.get("name"), "stream": key},
                )


def run_scenario(
    spec: Dict[str, Any],
    *,
    base_dir: Path,
    platform: str = "ios",
    start_dir: Optional[Path] = None,
    spec_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行 v2 场景，并写入可复现、可校验的运行 manifest。"""
    spec = validate_scenario_spec(spec)
    name = str(spec.get("name") or "unnamed")
    canonical = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    run = AnalysisRun(name=name, kind="scenario", platform=platform)
    run.retention["pinned"] = bool((spec.get("output") or {}).get("pinned", False))
    root = _run_root(spec, base_dir=Path(base_dir), start_dir=start_dir)
    workspace = run.workspace(root)
    scenario_snapshot = workspace.write_spec(canonical)
    scenario_metadata: Dict[str, Any] = {}
    if spec_path is not None and Path(spec_path).is_file():
        original_spec = RunFile.from_path("scenario_original", Path(spec_path))
        scenario_metadata = {
            "original_path": original_spec.path,
            "original_sha256": original_spec.sha256,
        }
    run.add_input(
        scenario_snapshot,
        role="scenario_spec",
        metadata=scenario_metadata,
    )

    from ..plugin_sdk import loaded_plugins
    from ..shared.project_paths import find_knowledge_path, find_profile_path

    context_root = start_dir or Path.cwd()
    context_files = [
        ("project_profile", find_profile_path(context_root)),
        ("project_knowledge", find_knowledge_path(context_root, platform=platform)),
    ]
    for role, path in context_files:
        if path is None or not path.is_file():
            continue
        original = RunFile.from_path(role, path)
        snapshot = workspace.freeze_context(path, name=role)
        run.add_input(
            snapshot,
            role=role,
            metadata={
                "original_path": original.path,
                "original_sha256": original.sha256,
            },
        )

    def package_version(name: str) -> str:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "source-tree"

    run.parameters = {
        "spec_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "scenario_schema_version": SCENARIO_SCHEMA_VERSION,
        "runtime": {
            "python": sys.version.split()[0],
            "tracecite_core": package_version("tracecite_core"),
            "tracecite_mobile": package_version("TraceCite Mobile"),
        },
        "plugins": loaded_plugins(),
    }
    run.write_manifest(root)
    started = time.monotonic()
    try:
        summary = _run_scenario_impl(
            spec,
            base_dir=base_dir,
            workspace=workspace,
            run=run,
            platform=platform,
            start_dir=start_dir,
        )
        _register_run_files(run, summary, workspace=workspace)
        assertions = {
            **(summary.get("assertions") or {}),
            "required_satisfied": bool(summary.get("required_satisfied")),
        }
        run.parameters.update(
            {
                "segmenter": summary.get("segmenter"),
                "segmenters": summary.get("segmenters"),
                "pattern": summary.get("pattern"),
                "source_provider": summary.get("source_provider"),
                "source_policy": (summary.get("source_completeness") or {}).get("policy"),
            }
        )
        completeness = summary.get("source_completeness") or {}
        if not completeness.get("accepted", False):
            verdict = "incomplete"
        elif summary.get("required_satisfied"):
            verdict = "passed"
        else:
            verdict = "failed"
        run.finish(
            status="completed",
            verdict=verdict,
            metrics={
                "match_records": int(summary.get("total_match_records") or 0),
                "event_count": sum(
                    int(row.get("event_count") or 0)
                    for row in summary.get("results") or []
                    if isinstance(row, dict)
                ),
                "input_expected": int(completeness.get("expected") or 0),
                "input_succeeded": int(completeness.get("succeeded") or 0),
                "input_failed": int(completeness.get("failed") or 0),
                "duration_seconds": round(time.monotonic() - started, 6),
            },
            assertions=assertions,
            delivery={
                "satisfied": bool(summary.get("delivery_satisfied", True)),
                "actions": list(summary.get("actions") or []),
            },
        )
        summary["run_id"] = run.run_id
        summary["status"] = run.status
        summary["verdict"] = run.verdict
        summary["manifest_path"] = str(workspace.root / "manifest.json")
        reports = render_reports(
            _report_specs(spec),
            context=ReportContext(
                summary=summary,
                run=run,
                base_dir=Path(base_dir).resolve(),
                run_dir=workspace.root,
                output_dir=workspace.reports_dir,
            ),
        )
        if reports:
            summary["reports"] = [
                {
                    "path": str(report.path),
                    "role": report.role,
                    "metadata": dict(report.metadata),
                }
                for report in reports
            ]
            for report in reports:
                if not report.path.resolve().is_relative_to(workspace.root):
                    raise RunIntegrityError(f"报告产物逃逸运行目录: {report.path}")
                run.add_artifact(
                    report.path,
                    role=report.role,
                    metadata=dict(report.metadata),
                )
        run.verify_files()
        manifest_path = run.write_manifest(root)
        summary["manifest_path"] = str(manifest_path)
        workspace.cleanup_temp()
        return summary
    except Exception as exc:
        workspace.cleanup_temp()
        run.finish(status="failed", verdict="error", error=str(exc))
        run.write_manifest(root)
        raise


def _validate_extension_references(spec: Dict[str, Any]) -> Dict[str, List[str]]:
    from tracecite_core.events import available_event_transformers
    from tracecite_core.preprocess import available_preprocessor_actions
    from tracecite_core.segmenter import available_segmenters
    from tracecite_core.source import available_source_providers

    from .assertions import available_assertion_types
    from .reporting import available_report_outputters

    available = {
        "source_providers": available_source_providers(),
        "segmenters": available_segmenters(),
        "preprocessors": available_preprocessor_actions(),
        "event_transformers": available_event_transformers(),
        "assertion_types": available_assertion_types(),
        "report_outputters": available_report_outputters(),
    }
    source_type = str((spec.get("source") or {}).get("type") or "file").lower()
    if source_type not in available["source_providers"]:
        raise ScenarioError(f"未知 source provider {source_type!r}")
    parse = spec.get("parse") or {}
    segmenter = str(parse.get("segmenter") or "auto").lower()
    if not parse.get("format") and segmenter not in {"", "auto"} and segmenter not in available["segmenters"]:
        raise ScenarioError(f"未知 segmenter {segmenter!r}")
    for index, step in enumerate((spec.get("source") or {}).get("preprocess") or []):
        action = str((step or {}).get("action") or "").lower()
        if action not in available["preprocessors"]:
            raise ScenarioError(f"source.preprocess[{index}] 未知 action {action!r}")
    for index, step in enumerate((spec.get("events") or {}).get("transforms") or []):
        name = str(step if isinstance(step, str) else (step or {}).get("type") or "").lower()
        if name not in available["event_transformers"]:
            raise ScenarioError(f"events.transforms[{index}] 未知类型 {name!r}")
    for index, rule in enumerate((spec.get("assert") or {}).get("rules") or []):
        name = str((rule or {}).get("type") or "").lower()
        if name not in available["assertion_types"]:
            raise ScenarioError(f"assert.rules[{index}] 未知类型 {name!r}")
    for index, report in enumerate(_report_specs(spec)):
        name = str(report if isinstance(report, str) else (report or {}).get("type") or "").lower()
        if name not in available["report_outputters"]:
            raise ScenarioError(f"output.reports[{index}] 未知类型 {name!r}")
    return available


def explain_scenario(
    spec: Dict[str, Any],
    *,
    base_dir: Path,
    platform: str = "ios",
    start_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """无过滤副作用地解析场景执行计划。"""
    import tempfile

    spec = validate_scenario_spec(spec)
    available = _validate_extension_references(spec)
    from ..plugin_sdk import loaded_plugins
    from ..shared.config import load_project_profile

    profile = load_project_profile(start_dir or Path.cwd(), platform=platform)
    source = spec.get("source") or {}
    source_type = str(source.get("type") or "file").lower()
    resolved_files: List[Path] = []
    source_note: Optional[str] = None
    if source_type in {"file", "static", "path", "dir", "archive"}:
        with tempfile.TemporaryDirectory(prefix="tracecite_core_explain_") as temp:
            resolved_files, _, _, _ = resolve_source_files(
                spec, base_dir=base_dir, extract_dir=Path(temp)
            )
            segmenters = [
                {
                    "path": str(path),
                    "segmenter": resolve_segmenter(
                        spec, path, formats=profile.formats
                    )[1],
                }
                for path in resolved_files
            ]
    else:
        segmenters = []
        source_note = "自定义或 live provider 在 explain 阶段不执行，运行时解析"

    filter_spec = spec.get("filter") or {}
    if isinstance(filter_spec.get("stages"), list) and filter_spec["stages"]:
        patterns = []
        for index, stage in enumerate(filter_spec["stages"]):
            merged = {k: value for k, value in filter_spec.items() if k != "stages"}
            merged.update(stage)
            pattern, tag = resolve_pattern(
                {"filter": merged},
                platform=platform,
                start_dir=start_dir,
                profile=profile,
            )
            patterns.append({"stage": stage.get("name") or index, "pattern": pattern, "tag": tag})
    else:
        pattern, tag = resolve_pattern(
            spec, platform=platform, start_dir=start_dir, profile=profile
        )
        patterns = [{"stage": None, "pattern": pattern, "tag": tag}]

    return {
        "valid": True,
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario": spec["name"],
        "platform": platform,
        "source": {
            "provider": source_type,
            "policy": source.get("policy", "all"),
            "files": [str(path) for path in resolved_files],
            "note": source_note,
        },
        "segmenters": segmenters,
        "filters": patterns,
        "assertion_scope": (spec.get("assert") or {}).get("scope", "aggregate"),
        "assertion_types": [
            str(rule.get("type")) for rule in (spec.get("assert") or {}).get("rules") or []
        ],
        "reports": list(_report_specs(spec)),
        "actions": [action.get("name") or action.get("run", [""])[0] for action in spec.get("actions") or []],
        "run_root": str(_run_root(spec, base_dir=base_dir, start_dir=start_dir).resolve()),
        "plugins": loaded_plugins(),
        "available_extensions": available,
    }


def cmd_scenario(args) -> int:
    """CLI 入口：``tracecite-mobile scenario run <spec>``。"""
    spec_path = Path(getattr(args, "spec", "")).expanduser()
    try:
        command = getattr(args, "scenario_command", "run")
        if command == "verify":
            from tracecite_core.run import verify_manifest

            result = verify_manifest(spec_path)
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"manifest 完整: {result['run_id']} "
                    f"({result['checked_files']} 个文件)"
                )
            return 0
        spec = load_spec(spec_path)
        if command in {"validate", "explain"}:
            explanation = explain_scenario(
                spec,
                base_dir=spec_path.parent,
                platform=getattr(args, "platform", "ios") or "ios",
            )
            if getattr(args, "json", False):
                print(json.dumps(explanation, ensure_ascii=False, indent=2))
            elif command == "validate":
                print(f"场景有效: {explanation['scenario']} (schema v{SCENARIO_SCHEMA_VERSION})")
            else:
                print(json.dumps(explanation, ensure_ascii=False, indent=2))
            return 0
        summary = run_scenario(
            spec,
            base_dir=spec_path.parent,
            platform=getattr(args, "platform", "ios") or "ios",
            spec_path=spec_path,
        )
    except (
        ScenarioError,
        SourceError,
        FilterError,
        AssertionSpecError,
        EventTransformError,
        ReportOutputError,
        RunIntegrityError,
    ) as exc:
        print(f"错误: {exc}", file=__import__("sys").stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary.get("verdict") == "passed" else 2

    print(f"场景: {summary['scenario']}")
    if summary.get("description"):
        print(f"说明: {summary['description']}")
    print(f"分段器: {summary['segmenter']}")
    print(f"pattern: {summary['pattern']}")
    print(f"输入文件: {len(summary['input_files'])} 个")
    print(f"命中记录: {summary['total_match_records']}")
    print(f"必需断言: {'满足' if summary['required_satisfied'] else '未满足'}")
    print(f"交付判定: {summary.get('verdict')}")
    for item in summary["results"]:
        if "error" in item:
            print(f"  [失败] {item['input']}: {item['error']}")
            continue
        print(
            f"  [{item['match_records']:>5} 条 / ~{item['estimated_tokens']} tokens] "
            f"{item['output_path']}"
        )
        missing = (item.get("assertions") or {}).get("missing_required") or []
        if missing:
            print(f"         缺失必需断言: {', '.join(missing)}")
        for warn in item.get("coverage_warning") or []:
            print(f"         提示: {warn}")
    return 0 if summary.get("verdict") == "passed" else 2

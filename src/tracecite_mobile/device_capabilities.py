"""Additional Agent-facing Mobile backend capabilities.

The base ``capabilities`` module exposes the initial device/session surface.
This module projects the remaining stable public backend protocols so TraceCite
Core can register them once and MCP can expose them automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from tracecite.extension import AgentCapability
from tracecite.runtime import CapabilitySpec

from .capabilities import (
    _DEVICE_SCHEMA,
    _PLATFORM_SCHEMA,
    _backend_and_device,
    _jsonable,
    _platform,
)
from .device_api import get_backend


_OUTPUT_DIR_SCHEMA = {
    "type": "string",
    "description": "Optional host-local output directory selected by the caller.",
}

_OUTPUT_PATH_SCHEMA = {
    "type": "string",
    "description": "Optional host-local output path selected by the caller.",
}


def _optional_path(arguments: Mapping[str, Any], key: str) -> Path | None:
    raw = arguments.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    return Path(str(raw)).expanduser()


def _required_text(arguments: Mapping[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} 不能为空")
    return value


def _resolve_process(arguments: Mapping[str, Any], backend: Any, device: Any):
    raw_pid = arguments.get("pid")
    pid: int | None = None
    if raw_pid is not None:
        if isinstance(raw_pid, bool) or not isinstance(raw_pid, int):
            raise ValueError("pid 必须是整数")
        pid = raw_pid
    package = str(arguments.get("package") or "").strip()
    name = str(arguments.get("name") or "").strip()
    if pid is None and not package and not name:
        raise ValueError("停止 App 需要 pid、package 或 name 之一")
    return backend.resolve_process(
        device,
        pid=pid,
        package=package or None,
        name=name or None,
        interactive=False,
    )


def stop_app(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Stop one explicitly resolved process on one selected device."""
    platform, backend, device = _backend_and_device(arguments)
    process = _resolve_process(arguments, backend, device)
    backend.stop_app(device, process)
    return {
        "platform": platform,
        "device": _jsonable(device),
        "process": _jsonable(process),
        "stopped": True,
    }


def list_archive_segments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List archived log segments for one explicitly selected device."""
    platform, backend, device = _backend_and_device(arguments)
    segments = backend.list_archive_segments(
        device=device,
        output_dir=_optional_path(arguments, "output_dir"),
    )
    return {
        "platform": platform,
        "device": _jsonable(device),
        "segments": [_jsonable(segment) for segment in segments],
        "count": len(segments),
    }


def fetch_log_window(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Materialize one archived log time window for one selected device."""
    platform, backend, device = _backend_and_device(arguments)
    result = backend.fetch_log_window(
        device=device,
        time_from=_required_text(arguments, "time_from"),
        time_to=_required_text(arguments, "time_to"),
        output_dir=_optional_path(arguments, "output_dir"),
        output_path=_optional_path(arguments, "output_path"),
    )
    payload = _jsonable(result)
    payload["platform"] = platform
    payload["device"] = _jsonable(device)
    return payload


def list_performance_profiles(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List performance profiles declared by the selected backend."""
    platform = _platform(arguments)
    profiles = get_backend(platform).list_performance_profiles()
    return {
        "platform": platform,
        "profiles": [_jsonable(profile) for profile in profiles],
        "count": len(profiles),
    }


def start_performance(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Start one performance collection session on an explicit device."""
    _, backend, device = _backend_and_device(arguments)
    result = backend.start_performance(
        device,
        profile=_required_text(arguments, "profile"),
        output_dir=_optional_path(arguments, "output_dir"),
    )
    return _jsonable(result)


def get_performance_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Report current performance collection state."""
    platform = _platform(arguments)
    result = get_backend(platform).get_performance_status(
        output_dir=_optional_path(arguments, "output_dir")
    )
    return _jsonable(result)


def stop_performance(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Stop the selected platform's active performance collection."""
    platform = _platform(arguments)
    result = get_backend(platform).stop_performance(
        output_dir=_optional_path(arguments, "output_dir")
    )
    return _jsonable(result)


def run_diagnostic(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run one declared diagnostic on one explicitly selected device."""
    _, backend, device = _backend_and_device(arguments)
    result = backend.diagnose(
        device,
        kind=str(arguments.get("kind") or "all"),
        output_dir=_optional_path(arguments, "output_dir"),
    )
    return _jsonable(result)


def list_crashes(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List crash-like events visible for one explicitly selected device."""
    _, backend, device = _backend_and_device(arguments)
    result = backend.list_crashes(
        device,
        since=str(arguments.get("since") or "") or None,
        until=str(arguments.get("until") or "") or None,
    )
    payload = _jsonable(result)
    payload["count"] = len(payload.get("events") or [])
    return payload


def fetch_crash(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch one explicitly selected crash event or report."""
    platform = _platform(arguments)
    result = get_backend(platform).fetch_crash(
        _required_text(arguments, "event"),
        output_path=_optional_path(arguments, "output_path"),
    )
    return _jsonable(result)


def agent_device_capabilities() -> tuple[AgentCapability, ...]:
    """Return the remaining public backend operations as Agent capabilities."""

    specs = [
        (
            CapabilitySpec(
                name="mobile.app.stop",
                kind="action",
                description=(
                    "Authorized live action: stop one explicitly resolved running process on one selected device. "
                    "The caller must identify the process by pid, package, or name; Mobile never guesses a target."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "pid": {"type": "integer", "minimum": 1},
                        "package": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_action",
                requires_authorization=True,
            ),
            stop_app,
        ),
        (
            CapabilitySpec(
                name="mobile.archive.list",
                kind="query",
                description=(
                    "List archived log segments for one explicitly selected device. "
                    "The result is a mechanical archive index and does not imply relevance or sufficiency."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "output_dir": _OUTPUT_DIR_SCHEMA,
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            list_archive_segments,
        ),
        (
            CapabilitySpec(
                name="mobile.archive.fetch",
                kind="query",
                description=(
                    "Materialize one caller-selected archived log time window for one explicit device. "
                    "The returned file is evidence input; Mobile does not interpret its causal meaning."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "time_from": {"type": "string"},
                        "time_to": {"type": "string"},
                        "output_dir": _OUTPUT_DIR_SCHEMA,
                        "output_path": _OUTPUT_PATH_SCHEMA,
                    },
                    "required": ["device", "time_from", "time_to"],
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            fetch_log_window,
        ),
        (
            CapabilitySpec(
                name="mobile.performance.profiles",
                kind="query",
                description=(
                    "List performance profiles declared by the selected backend. "
                    "Availability is a backend capability fact, not a recommendation to collect one."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"platform": _PLATFORM_SCHEMA},
                    "additionalProperties": False,
                },
                safety="read",
                requires_authorization=False,
            ),
            list_performance_profiles,
        ),
        (
            CapabilitySpec(
                name="mobile.performance.start",
                kind="action",
                description=(
                    "Authorized live action: start one caller-selected performance profile on one explicit device. "
                    "Starting collection changes live collection state and does not establish a diagnosis."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "profile": {"type": "string"},
                        "output_dir": _OUTPUT_DIR_SCHEMA,
                    },
                    "required": ["device", "profile"],
                    "additionalProperties": False,
                },
                safety="live_action",
                requires_authorization=True,
            ),
            start_performance,
        ),
        (
            CapabilitySpec(
                name="mobile.performance.status",
                kind="query",
                description=(
                    "Report current performance collection state for the selected platform/output directory. "
                    "The state is mechanical and does not imply trace quality or evidence sufficiency."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "output_dir": _OUTPUT_DIR_SCHEMA,
                    },
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            get_performance_status,
        ),
        (
            CapabilitySpec(
                name="mobile.performance.stop",
                kind="action",
                description=(
                    "Authorized live action: stop the active performance collection for the selected platform. "
                    "Returned artifact paths are evidence inputs and do not establish causal conclusions."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "output_dir": _OUTPUT_DIR_SCHEMA,
                    },
                    "additionalProperties": False,
                },
                safety="live_action",
                requires_authorization=True,
            ),
            stop_performance,
        ),
        (
            CapabilitySpec(
                name="mobile.diagnostics.run",
                kind="query",
                description=(
                    "Acquire one backend-declared diagnostic from one explicit device. "
                    "Unsupported diagnostic kinds fail closed; returned facts are evidence, not a diagnosis by themselves."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "kind": {"type": "string", "default": "all"},
                        "output_dir": _OUTPUT_DIR_SCHEMA,
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            run_diagnostic,
        ),
        (
            CapabilitySpec(
                name="mobile.crashes.list",
                kind="query",
                description=(
                    "List crash-like events currently visible for one explicitly selected device and time scope. "
                    "An empty list is scoped to that observation and is not proof that no crash exists elsewhere."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            list_crashes,
        ),
        (
            CapabilitySpec(
                name="mobile.crashes.fetch",
                kind="query",
                description=(
                    "Fetch one explicitly selected crash event/report from the selected backend. "
                    "The returned report is evidence input and does not by itself establish root cause."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "event": {"type": "string"},
                        "output_path": _OUTPUT_PATH_SCHEMA,
                    },
                    "required": ["event"],
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            fetch_crash,
        ),
    ]
    return tuple(
        AgentCapability(spec=spec, executor=executor)
        for spec, executor in specs
    )


__all__ = [
    "agent_device_capabilities",
    "fetch_crash",
    "fetch_log_window",
    "get_performance_status",
    "list_archive_segments",
    "list_crashes",
    "list_performance_profiles",
    "run_diagnostic",
    "start_performance",
    "stop_app",
    "stop_performance",
]

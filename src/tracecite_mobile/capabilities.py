"""Agent-facing Mobile capabilities projected through TraceCite Runtime.

This module stays intentionally thin: Mobile owns device/session semantics while
TraceCite owns capability registration, safety gates, and Agent adapter policy.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
import time
from typing import Any, Dict, Mapping

from tracecite.extension import AgentCapability
from tracecite.runtime import CapabilitySpec

from .device.archive import request_seal_hot
from .device_api import get_backend


_ARTIFACT_STABILITY_POLL_SEC = 0.05
_ARTIFACT_STABILITY_CHECKS = 2


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _platform(arguments: Mapping[str, Any]) -> str:
    platform = str(arguments.get("platform") or "ios").strip().lower()
    if platform not in {"ios", "android"}:
        raise ValueError("platform 必须是 ios 或 android")
    return platform


def _backend_and_device(arguments: Mapping[str, Any]):
    platform = _platform(arguments)
    backend = get_backend(platform)
    identifier = str(arguments.get("device") or "").strip()
    if not identifier:
        raise ValueError("device 不能为空")
    device = backend.resolve_device(udid=identifier, interactive=False)
    return platform, backend, device


def _path_is_stable(path: Path) -> bool:
    """Confirm a stopped artifact is a regular file with stable size/mtime."""

    previous: tuple[int, int] | None = None
    stable = 0
    for index in range(_ARTIFACT_STABILITY_CHECKS + 1):
        try:
            stat = path.stat()
            if not path.is_file():
                return False
            current = (int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            return False
        if current == previous:
            stable += 1
        else:
            previous = current
            stable = 1
        if stable >= _ARTIFACT_STABILITY_CHECKS:
            return True
        if index < _ARTIFACT_STABILITY_CHECKS:
            time.sleep(_ARTIFACT_STABILITY_POLL_SEC)
    return False


def _stable_session_artifacts(status: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Project verified stopped session outputs into an Evidence Runtime handoff.

    The platform backend must report the session stopped, and this adapter also
    performs a bounded size/mtime stability check before it marks a path stable.
    Mobile only identifies stable files; Core/MCP own retrieval, provenance,
    coverage, materialization, replay, and verification.
    """

    payload = _jsonable(status)
    if not isinstance(payload, Mapping):
        return [], []

    artifacts: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    for raw in payload.get("sessions") or []:
        if not isinstance(raw, Mapping):
            continue
        state = str(raw.get("state") or payload.get("state") or "").strip().lower()
        path = str(raw.get("output_path") or "").strip()
        if state != "stopped" or not path or not _path_is_stable(Path(path)):
            continue

        artifact: dict[str, Any] = {
            "kind": "device_log",
            "path": path,
            "stable": True,
            "platform": str(raw.get("platform") or payload.get("platform") or ""),
            "session_id": str(raw.get("identifier") or raw.get("session_id") or ""),
        }
        device = raw.get("device")
        if isinstance(device, Mapping):
            device_id = str(device.get("identifier") or "").strip()
            if device_id:
                artifact["device_id"] = device_id
        artifacts.append(artifact)
        if path not in evidence_files:
            evidence_files.append(path)

    return artifacts, evidence_files


def _project_cut_support(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Expose whether each live session can be cut without stopping collection."""

    supports_any = False
    sessions = payload.get("sessions") or []
    if isinstance(sessions, list):
        for raw in sessions:
            if not isinstance(raw, dict):
                continue
            state = str(raw.get("state") or payload.get("state") or "").strip().lower()
            supported = state in {"running", "active"} and bool(str(raw.get("output_path") or "").strip())
            raw["supports_cut"] = supported
            supports_any = supports_any or supported
    payload["supports_cut"] = supports_any
    return payload


def _running_session_for_device(status: Any, device_id: str) -> Mapping[str, Any]:
    payload = _jsonable(status)
    if not isinstance(payload, Mapping):
        raise ValueError("当前 session 状态不可用")
    for raw in payload.get("sessions") or []:
        if not isinstance(raw, Mapping):
            continue
        state = str(raw.get("state") or payload.get("state") or "").strip().lower()
        device = raw.get("device")
        seen_device = ""
        if isinstance(device, Mapping):
            seen_device = str(device.get("identifier") or "").strip()
        path = str(raw.get("output_path") or "").strip()
        if state in {"running", "active"} and seen_device == device_id and path:
            return raw
    raise ValueError(f"设备 {device_id} 当前没有可切段的运行中日志 session")


def list_devices(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List currently connected devices through the selected Mobile backend."""
    platform = _platform(arguments)
    backend = get_backend(platform)
    devices = backend.list_devices()
    return {
        "platform": platform,
        "devices": [_jsonable(device) for device in devices],
        "count": len(devices),
    }


def probe_environment(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Report whether the selected Mobile backend has the required host tools."""
    platform = _platform(arguments)
    return _jsonable(get_backend(platform).probe_environment())


def list_processes(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List app processes visible on one explicitly selected device."""
    platform, backend, device = _backend_and_device(arguments)
    processes = backend.list_processes(
        device,
        package=str(arguments.get("package") or ""),
        name=str(arguments.get("name") or ""),
    )
    return {
        "platform": platform,
        "device": _jsonable(device),
        "processes": [_jsonable(process) for process in processes],
        "count": len(processes),
    }


def list_log_sessions(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List currently known background log sessions without mutating them."""
    platform = _platform(arguments)
    output_dir_raw = arguments.get("output_dir")
    output_dir = Path(str(output_dir_raw)).expanduser() if output_dir_raw else None
    backend = get_backend(platform)
    status = backend.list_sessions(output_dir=output_dir)
    payload = _jsonable(status)
    return _project_cut_support(payload)


def start_log_session(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Start one background log collection session for an explicit device."""
    _, backend, device = _backend_and_device(arguments)
    output_dir_raw = arguments.get("output_dir")
    output_dir = Path(str(output_dir_raw)).expanduser() if output_dir_raw else None
    status = backend.start_sessions(
        [device],
        package=str(arguments.get("package") or ""),
        output_dir=output_dir,
    )
    return _jsonable(status)


def cut_log_session(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Seal the current live segment into stable evidence while collection continues."""

    platform, backend, device = _backend_and_device(arguments)
    output_dir_raw = arguments.get("output_dir")
    output_dir = Path(str(output_dir_raw)).expanduser() if output_dir_raw else None
    before = backend.list_sessions(devices=[device], output_dir=output_dir)
    session = _running_session_for_device(before, str(device.identifier))
    hot_path = Path(str(session.get("output_path"))).expanduser()
    session_id = str(session.get("identifier") or session.get("session_id") or "")

    sealed = request_seal_hot(
        hot_path,
        device_name=str(device.name or device.identifier),
    )
    sealed_payload = sealed.to_dict()
    sealed_path = str(sealed_payload.get("sealed_path") or "")

    after = backend.list_sessions(devices=[device], output_dir=output_dir)
    after_payload = _project_cut_support(_jsonable(after))
    collection_continues = False
    for raw in after_payload.get("sessions") or []:
        if not isinstance(raw, Mapping):
            continue
        raw_id = str(raw.get("identifier") or raw.get("session_id") or "")
        state = str(raw.get("state") or after_payload.get("state") or "").strip().lower()
        if raw_id == session_id and state in {"running", "active"}:
            collection_continues = True
            break

    artifact = {
        "kind": "device_log",
        "path": sealed_path,
        "stable": True,
        "sealed": True,
        "platform": platform,
        "session_id": session_id,
        "device_id": str(device.identifier),
    }
    return {
        "platform": platform,
        "device": _jsonable(device),
        "session_id": session_id,
        "state": str(after_payload.get("state") or "running"),
        "collection_continues": collection_continues,
        "sealed": sealed_payload,
        "artifacts": [artifact],
        "evidence_files": [sealed_path],
    }


def stop_log_session(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Stop one log session and expose verified stable evidence paths."""
    _, backend, device = _backend_and_device(arguments)
    output_dir_raw = arguments.get("output_dir")
    output_dir = Path(str(output_dir_raw)).expanduser() if output_dir_raw else None
    status = backend.stop_sessions(devices=[device], output_dir=output_dir)
    payload = _jsonable(status)
    artifacts, evidence_files = _stable_session_artifacts(status)
    if artifacts:
        payload["artifacts"] = artifacts
        payload["evidence_files"] = evidence_files
    return payload


def launch_app(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Launch one app on an explicitly selected device."""
    _, backend, device = _backend_and_device(arguments)
    app = str(arguments.get("app") or "").strip()
    if not app:
        raise ValueError("app 不能为空")
    return _jsonable(backend.launch_app(device, app))


_PLATFORM_SCHEMA = {
    "type": "string",
    "enum": ["ios", "android"],
    "default": "ios",
}

_DEVICE_SCHEMA = {
    "type": "string",
    "description": (
        "Stable iOS UDID or Android device identifier returned by mobile.devices.list; "
        "do not invent or infer an unresolved device identifier."
    ),
}


def agent_capabilities() -> tuple[AgentCapability, ...]:
    """Return declarative Extension Protocol v2 capability contributions."""
    specs = [
        (
            CapabilitySpec(
                name="mobile.environment.probe",
                kind="query",
                description=(
                    "Report host-local tool readiness for the selected iOS or Android backend. "
                    "This is a mechanical readiness observation, not evidence that a device or app is healthy."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"platform": _PLATFORM_SCHEMA},
                    "additionalProperties": False,
                },
                safety="read",
                requires_authorization=False,
            ),
            probe_environment,
        ),
        (
            CapabilitySpec(
                name="mobile.devices.list",
                kind="query",
                description=(
                    "List devices currently visible to TraceCite Mobile on the selected host/platform. "
                    "An empty result is scoped to this observation and is not proof that no device exists elsewhere."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"platform": _PLATFORM_SCHEMA},
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            list_devices,
        ),
        (
            CapabilitySpec(
                name="mobile.processes.list",
                kind="query",
                description=(
                    "List the process snapshot currently visible on one explicitly selected iOS or Android device. "
                    "A missing process is scoped to that snapshot and is not a root-cause conclusion."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "package": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            list_processes,
        ),
        (
            CapabilitySpec(
                name="mobile.sessions.list",
                kind="query",
                description=(
                    "List Mobile background log-session bookkeeping for the selected platform. "
                    "Running sessions with a writable output path report supports_cut=true so stable evidence can be "
                    "sealed without stopping collection. Session state is mechanical and does not imply evidence sufficiency."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "output_dir": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                safety="live_source",
                requires_authorization=False,
            ),
            list_log_sessions,
        ),
        (
            CapabilitySpec(
                name="mobile.sessions.start",
                kind="action",
                description=(
                    "Authorized live action: start background log collection for one explicitly selected device. "
                    "This mutates collection state only and does not interpret captured evidence."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "package": {"type": "string"},
                        "output_dir": {"type": "string"},
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_action",
                requires_authorization=True,
            ),
            start_log_session,
        ),
        (
            CapabilitySpec(
                name="mobile.sessions.cut",
                kind="action",
                description=(
                    "Authorized live action: seal the current log segment for one explicitly selected running device "
                    "session into stable artifacts/evidence_files while the collection session continues. "
                    "Use this when analyzable evidence is needed without stopping ongoing collection."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "output_dir": {"type": "string"},
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_action",
                requires_authorization=True,
            ),
            cut_log_session,
        ),
        (
            CapabilitySpec(
                name="mobile.sessions.stop",
                kind="action",
                description=(
                    "Authorized live action: stop background log collection for one explicitly selected device. "
                    "After the session reports stopped and the output file passes a bounded stability check, the result "
                    "exposes artifacts/evidence_files for handoff to the canonical Evidence Runtime."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "output_dir": {"type": "string"},
                    },
                    "required": ["device"],
                    "additionalProperties": False,
                },
                safety="live_action",
                requires_authorization=True,
            ),
            stop_log_session,
        ),
        (
            CapabilitySpec(
                name="mobile.app.launch",
                kind="action",
                description=(
                    "Authorized live action: launch an explicit app on one explicitly selected iOS or Android device. "
                    "Backend success reports the action result only and does not prove app health."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "platform": _PLATFORM_SCHEMA,
                        "device": _DEVICE_SCHEMA,
                        "app": {"type": "string"},
                    },
                    "required": ["device", "app"],
                    "additionalProperties": False,
                },
                safety="live_action",
                requires_authorization=True,
            ),
            launch_app,
        ),
    ]
    return tuple(AgentCapability(spec=spec, executor=executor) for spec, executor in specs)


__all__ = [
    "agent_capabilities",
    "cut_log_session",
    "launch_app",
    "list_devices",
    "list_log_sessions",
    "list_processes",
    "probe_environment",
    "start_log_session",
    "stop_log_session",
]

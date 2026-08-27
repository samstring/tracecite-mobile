"""Agent-facing Mobile capabilities projected through TraceCite Runtime.

This module stays intentionally thin: Mobile owns device/session semantics while
TraceCite owns capability registration, safety gates, and Agent adapter policy.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

from tracecite import CapabilitySpec
from tracecite.extension import ExtensionAPI

from .device_api import get_backend


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
    return _jsonable(status)


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


def stop_log_session(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Stop background log sessions for one explicitly selected device."""
    _, backend, device = _backend_and_device(arguments)
    output_dir_raw = arguments.get("output_dir")
    output_dir = Path(str(output_dir_raw)).expanduser() if output_dir_raw else None
    status = backend.stop_sessions(devices=[device], output_dir=output_dir)
    return _jsonable(status)


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
    "description": "Stable iOS UDID or Android device identifier returned by mobile.devices.list.",
}


def register_capabilities(api: ExtensionAPI) -> None:
    specs = [
        (
            CapabilitySpec(
                name="mobile.environment.probe",
                kind="query",
                description="Check whether host tooling required by the iOS or Android backend is ready.",
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
                description="List connected iOS or Android devices visible to TraceCite Mobile.",
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
                description="List processes on one selected iOS or Android device.",
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
                description="List existing Mobile background log sessions for a platform.",
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
                description="Start background log collection for one explicitly selected device.",
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
                name="mobile.sessions.stop",
                kind="action",
                description="Stop background log collection for one explicitly selected device.",
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
                description="Launch an app on one explicitly selected iOS or Android device.",
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
    for spec, executor in specs:
        api.register_capability(spec, executor)


__all__ = [
    "launch_app",
    "list_devices",
    "list_log_sessions",
    "list_processes",
    "probe_environment",
    "register_capabilities",
    "start_log_session",
    "stop_log_session",
]

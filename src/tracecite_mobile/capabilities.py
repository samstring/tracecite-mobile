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


def list_log_sessions(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List currently known background log sessions without mutating them."""
    platform = _platform(arguments)
    output_dir_raw = arguments.get("output_dir")
    output_dir = Path(str(output_dir_raw)).expanduser() if output_dir_raw else None
    backend = get_backend(platform)
    status = backend.list_sessions(output_dir=output_dir)
    return _jsonable(status)


def register_capabilities(api: ExtensionAPI) -> None:
    api.register_capability(
        CapabilitySpec(
            name="mobile.devices.list",
            kind="query",
            description="List connected iOS or Android devices visible to TraceCite Mobile.",
            input_schema={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": ["ios", "android"], "default": "ios"}
                },
                "additionalProperties": False,
            },
            safety="live_source",
            requires_authorization=False,
        ),
        list_devices,
    )
    api.register_capability(
        CapabilitySpec(
            name="mobile.sessions.list",
            kind="query",
            description="List existing Mobile background log sessions for a platform.",
            input_schema={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": ["ios", "android"], "default": "ios"},
                    "output_dir": {"type": "string"},
                },
                "additionalProperties": False,
            },
            safety="live_source",
            requires_authorization=False,
        ),
        list_log_sessions,
    )


__all__ = ["list_devices", "list_log_sessions", "register_capabilities"]

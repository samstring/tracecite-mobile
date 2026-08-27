"""Thin Mobile adapter for TraceCite Runtime SourceSession state.

Mobile owns platform identity semantics; TraceCite Runtime owns persistence,
reuse decisions, invalidation and coverage state.  This module never runs
probe/sample/survey automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .platforms.models import DeviceRef, ProcessRef, SessionRef


class MobileSourceSessionError(RuntimeError):
    """The installed TraceCite Runtime does not expose SourceSession v1."""


@dataclass(frozen=True)
class MobileSourceProfile:
    source_id: str
    identity: Mapping[str, Any]
    source_type: str
    format: str
    segmenter: str
    extension: str = "mobile"
    confidence: float = 1.0

    def register_kwargs(self, *, coverage: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "identity": dict(self.identity),
            "source_type": self.source_type,
            "format": self.format,
            "segmenter": self.segmenter,
            "extension": self.extension,
            "recognition_status": "known",
            "confidence": self.confidence,
            "coverage": dict(coverage or {}),
        }


def _platform_defaults(platform: str) -> tuple[str, str]:
    resolved = str(platform or "").strip().lower()
    if resolved == "ios":
        return "ios_console", "devicelog"
    if resolved == "android":
        return "android_threadtime", "devicelog"
    return "mobile_device_log", "devicelog"


def build_mobile_source_profile(
    device: DeviceRef,
    *,
    app: str = "",
    process: Optional[ProcessRef] = None,
    session: Optional[SessionRef] = None,
    stream_type: str = "device_log",
    format: str = "",
    segmenter: str = "",
    confidence: float = 1.0,
) -> MobileSourceProfile:
    """Describe one logical live Mobile source for TraceCite SourceSession.

    Content growth is deliberately excluded from identity.  Device/app/launch,
    collector session and stream type are stable enough for reuse checks; a new
    launch or collector session can therefore be detected without treating each
    appended log line as a new source.
    """

    platform = str(device.platform or "").strip().lower()
    stream = str(stream_type or "device_log").strip().lower() or "device_log"
    app_id = str(app or (process.package if process else "") or "").strip()
    launch_id = ""
    if process is not None:
        launch_id = str(process.identifier or process.pid or "").strip()
    collector_session_id = str(session.identifier if session else "").strip()

    identity: Dict[str, Any] = {
        "platform": platform,
        "device_id": str(device.identifier),
        "stream_type": stream,
    }
    if app_id:
        identity["app_id"] = app_id
    if launch_id:
        identity["launch_id"] = launch_id
    if collector_session_id:
        identity["collector_session_id"] = collector_session_id

    source_anchor = collector_session_id or app_id or "default"
    source_id = f"mobile:{platform}:{device.identifier}:{stream}:{source_anchor}"
    default_format, default_segmenter = _platform_defaults(platform)
    return MobileSourceProfile(
        source_id=source_id,
        identity=identity,
        source_type=f"mobile.{platform or 'unknown'}.{stream}",
        format=str(format or default_format),
        segmenter=str(segmenter or default_segmenter),
        confidence=float(confidence),
    )


def _require_method(store: Any, name: str):
    method = getattr(store, name, None)
    if not callable(method):
        raise MobileSourceSessionError(
            f"TraceCite Runtime 缺少 {name}；请使用支持 SourceSession v1 的 tracecite 版本。"
        )
    return method


def register_mobile_source_session(
    store: Any,
    device: DeviceRef,
    *,
    app: str = "",
    process: Optional[ProcessRef] = None,
    session: Optional[SessionRef] = None,
    stream_type: str = "device_log",
    format: str = "",
    segmenter: str = "",
    confidence: float = 1.0,
    coverage: Optional[Mapping[str, Any]] = None,
    source_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    profile = build_mobile_source_profile(
        device,
        app=app,
        process=process,
        session=session,
        stream_type=stream_type,
        format=format,
        segmenter=segmenter,
        confidence=confidence,
    )
    kwargs = profile.register_kwargs(coverage=coverage)
    if source_session_id:
        kwargs["session_id"] = source_session_id
    return _require_method(store, "register_source_session")(**kwargs)


def inspect_mobile_source_session(
    store: Any,
    source_session_id: str,
    device: DeviceRef,
    *,
    app: str = "",
    process: Optional[ProcessRef] = None,
    session: Optional[SessionRef] = None,
    stream_type: str = "device_log",
) -> Dict[str, Any]:
    profile = build_mobile_source_profile(
        device,
        app=app,
        process=process,
        session=session,
        stream_type=stream_type,
    )
    return _require_method(store, "inspect_source_session")(
        source_session_id,
        identity=profile.identity,
    )


def update_mobile_source_coverage(
    store: Any,
    source_session_id: str,
    coverage: Mapping[str, Any],
) -> Dict[str, Any]:
    """Advance a growing log window without invalidating source recognition."""

    return _require_method(store, "update_source_session_coverage")(
        source_session_id,
        coverage,
    )


__all__ = [
    "MobileSourceProfile",
    "MobileSourceSessionError",
    "build_mobile_source_profile",
    "register_mobile_source_session",
    "inspect_mobile_source_session",
    "update_mobile_source_coverage",
]

"""Stable device-facing facade for Mobile extensions.

Company extensions should import device, backend, session, and profile contracts
from this module instead of depending on Mobile's internal package layout.
"""

from .platforms.base import (
    AppCapability,
    ArchiveCapability,
    BackendError,
    CrashCapability,
    DeviceCapability,
    DiagnosticCapability,
    LogCapability,
    PerformanceCapability,
    PlatformBackend,
    UnsupportedCapabilityError,
)
from .platforms.models import (
    ArchiveSegment,
    Capabilities,
    CrashEvent,
    CrashResult,
    DeviceRef,
    DiagnosticResult,
    LogSessionResult,
    LogWindowResult,
    PerformanceProfile,
    PerformanceResult,
    PerformanceSession,
    PerformanceStatus,
    ProcessRef,
    SessionRef,
    SessionStatus,
)
from .platforms.registry import get_backend
from .shared.config import load_project_profile
from .ui_api import ScreenCapability

__all__ = [
    "AppCapability",
    "ArchiveCapability",
    "ArchiveSegment",
    "BackendError",
    "Capabilities",
    "CrashCapability",
    "CrashEvent",
    "CrashResult",
    "DeviceCapability",
    "DeviceRef",
    "DiagnosticCapability",
    "DiagnosticResult",
    "get_backend",
    "LogCapability",
    "LogSessionResult",
    "LogWindowResult",
    "load_project_profile",
    "PerformanceCapability",
    "PerformanceProfile",
    "PerformanceResult",
    "PerformanceSession",
    "PerformanceStatus",
    "PlatformBackend",
    "ProcessRef",
    "ScreenCapability",
    "SessionRef",
    "SessionStatus",
    "UnsupportedCapabilityError",
]

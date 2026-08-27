"""TraceCite Mobile 插件 SDK：增加平台、断言和报告扩展点。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from tracecite_core.plugin_sdk import PluginAPI, load_entrypoint_plugins, loaded_plugins

from .analysis.assertions import (
    AssertionContext,
    AssertionOutcome,
    register_assertion_type,
)
from .analysis.reporting import (
    ReportArtifact,
    ReportContext,
    register_report_outputter,
)
from .analysis.behavior_summary import register_behavior_parser
from .platforms.registry import register_backend
from .platforms.base import (
    AppCapability,
    ArchiveCapability,
    BackendError,
    BaseBackend,
    CrashCapability,
    DeviceCapability,
    DiagnosticCapability,
    LogCapability,
    PerformanceCapability,
    PlatformBackend,
    RunResult,
    UnsupportedCapabilityError,
)
from .platforms.models import (
    ArchiveSegment,
    Capabilities,
    CaptureResult,
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

ANALYZER_PLUGIN_API_VERSION = "3"


@dataclass(frozen=True)
class AnalyzerPluginAPI(PluginAPI):
    version: str = ANALYZER_PLUGIN_API_VERSION

    def register_backend(self, platform: str, factory, *, replace: bool = False) -> None:
        register_backend(platform, factory, replace=replace)

    def register_assertion_type(
        self, name: str, evaluator, *, replace: bool = False
    ) -> None:
        register_assertion_type(name, evaluator, replace=replace)

    def register_report_outputter(
        self, name: str, outputter, *, replace: bool = False
    ) -> None:
        register_report_outputter(name, outputter, replace=replace)

    def register_behavior_parser(
        self, name: str, parser, *, replace: bool = False
    ) -> None:
        register_behavior_parser(name, parser, replace=replace)


def load_analyzer_plugins(*, strict: bool = True) -> List[Dict[str, Optional[str]]]:
    return [
        *load_entrypoint_plugins(group="tracecite.core.plugins", strict=strict),
        *load_entrypoint_plugins(
            group="tracecite.mobile.plugins",
            strict=strict,
            api=AnalyzerPluginAPI(),
            version_attribute="TRACECITE_MOBILE_PLUGIN_API",
        ),
    ]


__all__ = [
    "AnalyzerPluginAPI",
    "ANALYZER_PLUGIN_API_VERSION",
    "AppCapability",
    "ArchiveCapability",
    "ArchiveSegment",
    "AssertionContext",
    "AssertionOutcome",
    "ReportArtifact",
    "ReportContext",
    "BackendError",
    "BaseBackend",
    "Capabilities",
    "CrashCapability",
    "CrashEvent",
    "CrashResult",
    "DeviceCapability",
    "DeviceRef",
    "DiagnosticCapability",
    "DiagnosticResult",
    "LogCapability",
    "LogSessionResult",
    "LogWindowResult",
    "PerformanceCapability",
    "PerformanceProfile",
    "PerformanceResult",
    "PerformanceSession",
    "PerformanceStatus",
    "PlatformBackend",
    "ProcessRef",
    "RunResult",
    "SessionRef",
    "SessionStatus",
    "UnsupportedCapabilityError",
    "CaptureResult",
    "load_analyzer_plugins",
    "loaded_plugins",
]

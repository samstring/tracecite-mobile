# -*- coding: utf-8 -*-
"""跨平台设备后端使用的稳定数据模型。

这些类型只描述跨平台语义，不暴露具体采集工具或平台私有状态文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class DeviceRef:
    """统一设备引用；``identifier`` 由具体平台解释。"""

    platform: str
    identifier: str
    name: str
    model: str = ""
    state: str = "connected"

    def display(self, index: int) -> str:
        return (
            f"  [{index}] {self.name}  ({self.model})\n"
            f"      {self.platform} {self.identifier} [{self.state}]"
        )


@dataclass(frozen=True)
class EnvironmentStatus:
    """设备后端运行环境探测结果。"""

    platform: str
    ready: bool
    checks: Mapping[str, bool] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class ProcessRef:
    """跨平台进程引用。"""

    platform: str
    identifier: str
    device: Optional[DeviceRef] = None
    name: str = ""
    package: str = ""
    pid: Optional[int] = None


@dataclass(frozen=True)
class SessionRef:
    """后台日志 session 的稳定引用。"""

    platform: str
    identifier: str
    device: Optional[DeviceRef] = None
    output_path: Optional[Path] = None
    started_at: Optional[str] = None
    collector_pid: Optional[int] = None
    process: Optional[ProcessRef] = None
    state: str = "running"
    healthy: Optional[bool] = None
    detail: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        """兼容调用方使用 ``session_id`` 的语义化别名。"""

        return self.identifier


@dataclass(frozen=True)
class SessionStatus:
    """日志 session 当前状态。"""

    platform: str
    state: str = "unknown"
    sessions: Tuple[SessionRef, ...] = ()
    active: bool = False
    session_count: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        if self.session_count == 0 and self.sessions:
            object.__setattr__(self, "session_count", len(self.sessions))
        if self.sessions and not self.active and self.state in {"running", "active"}:
            object.__setattr__(self, "active", True)

    @property
    def session(self) -> Optional[SessionRef]:
        """单设备旧调用方使用的首个 session 兼容视图。"""

        return self.sessions[0] if self.sessions else None


@dataclass(frozen=True)
class LogSessionResult:
    """日志采集（前台或后台 session）的统一结果。"""

    platform: str
    device: DeviceRef
    output_path: Path
    started_at: str
    collector_pid: Optional[int] = None


@dataclass(frozen=True)
class PerformanceProfile:
    """可选性能采集 profile。"""

    name: str
    description: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PerformanceSession:
    """进行中的性能采集 session。"""

    platform: str
    identifier: str
    profile: str
    device: Optional[DeviceRef] = None
    output_path: Optional[Path] = None
    started_at: Optional[str] = None

    @property
    def session_id(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class PerformanceStatus:
    """性能采集当前状态。"""

    platform: str
    state: str
    session: Optional[PerformanceSession] = None
    detail: str = ""


@dataclass(frozen=True)
class PerformanceResult:
    """性能采集完成后的稳定结果。"""

    platform: str
    device: DeviceRef
    trace_path: Optional[Path] = None
    metadata_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    context_path: Optional[Path] = None
    profile: Optional[str] = None

    @property
    def output_path(self) -> Optional[Path]:
        """性能产物的通用路径别名；旧调用方继续使用 ``trace_path``。"""

        return self.trace_path


# 旧 capture 命名保留为类型别名，避免破坏已有插件和 CLI。
CaptureResult = PerformanceResult


@dataclass(frozen=True)
class ArchiveSegment:
    """一段已经落盘的日志归档。"""

    start: str
    end: str
    path: str
    bytes: int = 0
    lines: int = 0
    device: Optional[DeviceRef] = None


@dataclass(frozen=True)
class LogWindowResult:
    """按时间窗拼接后的日志结果。"""

    output_path: Path
    time_from: str
    time_to: str
    segments: Tuple[str, ...] = ()
    lines: int = 0
    bytes: int = 0


@dataclass(frozen=True)
class DiagnosticResult:
    """设备或进程诊断结果。"""

    platform: str
    kind: str
    device: Optional[DeviceRef] = None
    output_path: Optional[Path] = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CrashEvent:
    """一次崩溃、卡死、ANR 或其他异常事件。"""

    platform: str
    identifier: str
    kind: str
    device: Optional[DeviceRef] = None
    occurred_at: Optional[str] = None
    summary: str = ""
    path: Optional[Path] = None


@dataclass(frozen=True)
class CrashResult:
    """异常事件列表或拉取结果。"""

    platform: str
    events: Tuple[CrashEvent, ...] = ()
    output_path: Optional[Path] = None


@dataclass(frozen=True)
class Capabilities:
    """后端能力声明。

    布尔字段表达是否支持稳定的跨平台语义；平台特有参数放入
    ``platform_options``，不把实现细节泄漏到公共协议。
    """

    platform: str = ""
    device: bool = False
    app: bool = False
    process: bool = False
    log: bool = False
    multi_device_session: bool = False
    performance: bool = False
    performance_profiles: Tuple[str, ...] = ()
    archive: bool = False
    log_window: bool = False
    diagnostics: Tuple[str, ...] = ()
    crash: Tuple[str, ...] = ()
    crash_supported: bool = False
    platform_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 具体 profile/kind 是公共可发现信息；布尔字段保留给调用方做快速判断。
        if self.performance_profiles and not self.performance:
            object.__setattr__(self, "performance", True)
        if self.crash and not self.crash_supported:
            object.__setattr__(self, "crash_supported", True)

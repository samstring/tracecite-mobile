# -*- coding: utf-8 -*-
"""平台后端的能力协议与公共基类。

公共层只表达设备、日志、性能和诊断等跨平台语义。具体平台通过能力组合
实现协议；未声明或未实现的能力必须显式失败，不能静默退化。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Protocol, Sequence, Union, runtime_checkable

from .models import (
    ArchiveSegment,
    Capabilities,
    CaptureResult,
    CrashEvent,
    CrashResult,
    DeviceRef,
    DiagnosticResult,
    EnvironmentStatus,
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


class BackendError(RuntimeError):
    """平台后端通用错误。"""


class UnsupportedCapabilityError(BackendError):
    """请求了后端没有声明或没有实现的能力。"""

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"后端不支持能力: {capability}")


@dataclass
class RunResult:
    """外部命令的统一执行结果。"""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def default_run(
    args: List[str],
    *,
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
) -> RunResult:
    """默认 runner：参数数组执行，带 timeout 与 stderr 捕获。"""

    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            returncode=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=f"命令超时（>{timeout}s）: {' '.join(list(args))}",
        )
    except FileNotFoundError as exc:
        return RunResult(returncode=127, stdout="", stderr=f"未找到命令: {exc}")
    return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")


@runtime_checkable
class DeviceCapability(Protocol):
    """设备发现、解析和环境探测能力。"""

    platform: str

    def probe_environment(self) -> EnvironmentStatus: ...

    def list_devices(self) -> List[DeviceRef]: ...

    def resolve_device(
        self,
        *,
        udid: Optional[str] = None,
        name: Optional[str] = None,
        index: Optional[int] = None,
        interactive: bool = True,
    ) -> DeviceRef: ...

    def resolve_devices(
        self,
        *,
        udids: Optional[Sequence[str]] = None,
        name: Optional[str] = None,
        names: Optional[Sequence[str]] = None,
        indices: Optional[Sequence[int]] = None,
        all_devices: bool = False,
        interactive: bool = True,
    ) -> List[DeviceRef]: ...


@runtime_checkable
class AppCapability(Protocol):
    """应用和进程查询、启动、停止能力。"""

    platform: str

    def list_processes(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        name: str = "",
    ) -> List[ProcessRef]: ...

    def resolve_process(
        self,
        device: DeviceRef,
        *,
        pid: Optional[int] = None,
        package: Optional[str] = None,
        name: Optional[str] = None,
        interactive: bool = True,
    ) -> ProcessRef: ...

    def launch_app(
        self,
        device: DeviceRef,
        app: str,
        **kwargs: Any,
    ) -> ProcessRef: ...

    def stop_app(
        self,
        device: DeviceRef,
        process: Optional[ProcessRef] = None,
        **kwargs: Any,
    ) -> None: ...


@runtime_checkable
class LogCapability(Protocol):
    """前台日志和多设备后台 session 能力。"""

    platform: str

    def stream_logs(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        output_path: Path,
        also_stdout: bool = True,
        **kwargs: Any,
    ) -> LogSessionResult: ...

    def start_sessions(
        self,
        devices: Sequence[DeviceRef],
        *,
        package: str = "",
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus: ...

    def list_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus: ...

    def stop_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        all_devices: bool = False,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus: ...


@runtime_checkable
class ArchiveCapability(Protocol):
    """hot 日志归档与时间窗读取能力。"""

    platform: str

    def list_archive_segments(
        self,
        *,
        device: Optional[DeviceRef] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> List[ArchiveSegment]: ...

    def fetch_log_window(
        self,
        *,
        device: Optional[DeviceRef] = None,
        time_from: str,
        time_to: str,
        output_dir: Optional[Path] = None,
        output_path: Optional[Path] = None,
        **kwargs: Any,
    ) -> LogWindowResult: ...


@runtime_checkable
class PerformanceCapability(Protocol):
    """统一性能 profile 的开始、状态和停止能力。"""

    platform: str

    def list_performance_profiles(self) -> List[PerformanceProfile]: ...

    def start_performance(
        self,
        device: DeviceRef,
        *,
        profile: Union[str, PerformanceProfile],
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceSession: ...

    def get_performance_status(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceStatus: ...

    def stop_performance(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceResult: ...


@runtime_checkable
class DiagnosticCapability(Protocol):
    """设备、进程、内存、图形和窗口等诊断能力。"""

    platform: str

    def diagnose(
        self,
        device: DeviceRef,
        *,
        kind: str = "all",
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> DiagnosticResult: ...


@runtime_checkable
class CrashCapability(Protocol):
    """崩溃、卡死、ANR、OOM 等异常事件能力。"""

    platform: str

    def list_crashes(
        self,
        device: Optional[DeviceRef] = None,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        **kwargs: Any,
    ) -> CrashResult: ...

    def fetch_crash(
        self,
        event: Union[CrashEvent, str],
        *,
        output_path: Optional[Path] = None,
        **kwargs: Any,
    ) -> CrashResult: ...


@runtime_checkable
class PlatformBackend(Protocol):
    """最小平台后端契约；可选能力通过上面的 Protocol 组合。"""

    platform: str

    def capabilities(self) -> Capabilities: ...


class BaseBackend:
    """公共基类：提供 runner、能力声明和 fail-closed 默认实现。"""

    platform: str = "base"

    def __init__(self, run=None) -> None:
        self._run = run or default_run

    def run(self, args: List[str], **kw: Any) -> RunResult:
        return self._run(args, **kw)

    def capabilities(self) -> Capabilities:
        """默认不声明任何可选能力，具体平台必须显式覆盖。"""

        return Capabilities(platform=self.platform)

    @staticmethod
    def which(tool: str) -> Optional[str]:
        return shutil.which(tool)

    @staticmethod
    def _unsupported(capability: str):
        raise UnsupportedCapabilityError(capability)

    # ---- DeviceCapability ----
    def probe_environment(self) -> EnvironmentStatus:
        return self._unsupported("device.probe_environment")

    def list_devices(self) -> List[DeviceRef]:
        return self._unsupported("device.list_devices")

    def resolve_device(
        self,
        *,
        udid: Optional[str] = None,
        name: Optional[str] = None,
        index: Optional[int] = None,
        interactive: bool = True,
    ) -> DeviceRef:
        return self._unsupported("device.resolve_device")

    def resolve_devices(
        self,
        *,
        udids: Optional[Sequence[str]] = None,
        name: Optional[str] = None,
        names: Optional[Sequence[str]] = None,
        indices: Optional[Sequence[int]] = None,
        all_devices: bool = False,
        interactive: bool = True,
    ) -> List[DeviceRef]:
        return self._unsupported("device.resolve_devices")

    # ---- AppCapability ----
    def list_processes(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        name: str = "",
    ) -> List[ProcessRef]:
        return self._unsupported("app.list_processes")

    def resolve_process(
        self,
        device: DeviceRef,
        *,
        pid: Optional[int] = None,
        package: Optional[str] = None,
        name: Optional[str] = None,
        interactive: bool = True,
    ) -> ProcessRef:
        return self._unsupported("app.resolve_process")

    def launch_app(
        self,
        device: DeviceRef,
        app: str,
        **kwargs: Any,
    ) -> ProcessRef:
        return self._unsupported("app.launch_app")

    def stop_app(
        self,
        device: DeviceRef,
        process: Optional[ProcessRef] = None,
        **kwargs: Any,
    ) -> None:
        return self._unsupported("app.stop_app")

    # ---- LogCapability ----
    def stream_logs(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        output_path: Path,
        also_stdout: bool = True,
        **kwargs: Any,
    ) -> LogSessionResult:
        return self._unsupported("log.stream_logs")

    def start_sessions(
        self,
        devices: Sequence[DeviceRef],
        *,
        package: str = "",
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        return self._unsupported("log.start_sessions")

    def list_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        return self._unsupported("log.list_sessions")

    def stop_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        all_devices: bool = False,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        return self._unsupported("log.stop_sessions")

    # ---- ArchiveCapability ----
    def list_archive_segments(
        self,
        *,
        device: Optional[DeviceRef] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> List[ArchiveSegment]:
        return self._unsupported("archive.list_archive_segments")

    def fetch_log_window(
        self,
        *,
        device: Optional[DeviceRef] = None,
        time_from: str,
        time_to: str,
        output_dir: Optional[Path] = None,
        output_path: Optional[Path] = None,
        **kwargs: Any,
    ) -> LogWindowResult:
        return self._unsupported("archive.fetch_log_window")

    # ---- PerformanceCapability ----
    def list_performance_profiles(self) -> List[PerformanceProfile]:
        return self._unsupported("performance.list_profiles")

    def start_performance(
        self,
        device: DeviceRef,
        *,
        profile: Union[str, PerformanceProfile],
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceSession:
        return self._unsupported("performance.start")

    def get_performance_status(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceStatus:
        return self._unsupported("performance.status")

    def stop_performance(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceResult:
        return self._unsupported("performance.stop")

    # ---- DiagnosticCapability ----
    def diagnose(
        self,
        device: DeviceRef,
        *,
        kind: str = "all",
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> DiagnosticResult:
        return self._unsupported("diagnostics.diagnose")

    # ---- CrashCapability ----
    def list_crashes(
        self,
        device: Optional[DeviceRef] = None,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        **kwargs: Any,
    ) -> CrashResult:
        return self._unsupported("crash.list")

    def fetch_crash(
        self,
        event: Union[CrashEvent, str],
        *,
        output_path: Optional[Path] = None,
        **kwargs: Any,
    ) -> CrashResult:
        return self._unsupported("crash.fetch")

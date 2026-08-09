# -*- coding: utf-8 -*-
"""平台后端抽象：可注入 command runner + PlatformBackend Protocol。

CLI 编排层只依赖 PlatformBackend，不直接调用 adb / devicectl / xctrace。
所有平台后端都通过 injectable runner 执行外部命令，便于 fake-adb / fake-devicectl 测试。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Protocol, runtime_checkable

from .models import CaptureResult, DeviceRef, LogSessionResult


class BackendError(RuntimeError):
    """平台后端通用错误。"""


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
    """默认 runner：参数数组，绝不拼接 shell 字符串；带 timeout 与 stderr 捕获。

    子进程 stdout/stderr 分别捕获，避免长日志污染父进程。
    """
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
class PlatformBackend(Protocol):
    """所有平台后端必须实现的最小接口。

    CLI 只调用这些方法；返回统一数据模型或稳定 JSON dict。
    """

    platform: str

    def list_devices(self) -> List[DeviceRef]: ...

    def resolve_device(
        self,
        *,
        udid: Optional[str] = None,
        name: Optional[str] = None,
        index: Optional[int] = None,
        interactive: bool = True,
    ) -> DeviceRef: ...

    def stream_logs(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        output_path: Path,
        also_stdout: bool = True,
        **kwargs: Any,
    ) -> LogSessionResult: ...

    def start_session(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        output_dir: Optional[Path] = None,
        include_date: bool = False,
        output_file: Optional[Path] = None,
        **kwargs: Any,
    ) -> dict: ...

    def get_session_status(self, *, output_dir: Optional[Path] = None) -> dict: ...

    def stop_session(self, *, output_dir: Optional[Path] = None) -> dict: ...

    def start_capture(
        self,
        device: DeviceRef,
        *,
        template: str,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> dict: ...

    def get_capture_status(self, *, output_dir: Optional[Path] = None) -> dict: ...

    def stop_capture(self, *, output_dir: Optional[Path] = None) -> CaptureResult: ...

class BaseBackend:
    """公共基类：持有 injectable runner 与默认 adb/devicectl 解析工具。"""

    platform: str = "base"

    def __init__(self, run=None) -> None:
        self._run = run or default_run

    def run(self, args: List[str], **kw: Any) -> RunResult:
        return self._run(args, **kw)

    @staticmethod
    def which(tool: str) -> Optional[str]:
        return shutil.which(tool)

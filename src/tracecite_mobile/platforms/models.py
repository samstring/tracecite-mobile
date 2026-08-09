# -*- coding: utf-8 -*-
"""跨平台设备后端的稳定数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DeviceRef:
    """统一设备引用；identifier 由具体平台解释。"""

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
class LogSessionResult:
    """日志采集（前台或后台 session）的统一结果。"""

    platform: str
    device: DeviceRef
    output_path: Path
    started_at: str
    collector_pid: Optional[int] = None


@dataclass(frozen=True)
class CaptureResult:
    """性能现场采集的统一结果。"""

    platform: str
    device: DeviceRef
    trace_path: Path
    metadata_path: Optional[Path] = None
    summary_path: Optional[Path] = None

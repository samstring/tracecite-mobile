# -*- coding: utf-8 -*-
"""Android backend for adb, devices, logging, and profiling."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from ..base import BaseBackend
from ..models import CaptureResult, DeviceRef, LogSessionResult
from .adb import AndroidAdbClient
from . import devices as android_devices
from . import logger as android_logger
from . import profiler as android_profiler


class AndroidBackend(BaseBackend):
    platform = "android"

    def __init__(self, run=None, adb_path: Optional[str] = None) -> None:
        super().__init__(run=run)
        self.client = AndroidAdbClient(run=self._run, adb_path=adb_path)

    # ---- 设备 ----
    def list_devices(self) -> List[DeviceRef]:
        return android_devices.list_devices(self.client)

    def resolve_device(
        self,
        *,
        udid: Optional[str] = None,
        name: Optional[str] = None,
        index: Optional[int] = None,
        interactive: bool = True,
    ) -> DeviceRef:
        # Android 用 serial 对应 udid 语义
        return android_devices.resolve_device(
            self.client,
            serial=udid,
            name=name,
            index=index,
            interactive=interactive,
        )

    # ---- 日志 ----
    def stream_logs(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        output_path: Path,
        also_stdout: bool = True,
        **kwargs: Any,
    ) -> LogSessionResult:
        from datetime import datetime

        android_logger.stream_logs(
            self.client,
            device,
            output_path=Path(output_path),
            also_stdout=also_stdout,
            package=package,
            priority=kwargs.get("priority"),
            tag=kwargs.get("tag"),
            pid=kwargs.get("pid"),
        )
        return LogSessionResult(
            platform="android",
            device=device,
            output_path=Path(output_path),
            started_at=datetime.now().isoformat(timespec="seconds"),
        )

    def start_session(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        output_dir: Optional[Path] = None,
        include_date: bool = False,
        output_file: Optional[Path] = None,
        popen=None,
        **kwargs: Any,
    ) -> dict:
        return android_logger.start_session(
            self.client,
            device,
            package=package,
            output_dir=output_dir or Path.cwd(),
            include_date=include_date,
            output_file=output_file,
            popen=popen,
        )

    def get_session_status(self, *, output_dir: Optional[Path] = None) -> dict:
        return android_logger.get_session_status(Path(output_dir or Path.cwd()))

    def stop_session(self, *, output_dir: Optional[Path] = None) -> dict:
        return android_logger.stop_session(Path(output_dir or Path.cwd()))

    # ---- 性能现场 ----
    def start_capture(
        self,
        device: DeviceRef,
        *,
        template: str,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> dict:
        return android_profiler.start_capture(
            self.client,
            device,
            template=template,
            output_dir=output_dir or Path.cwd(),
        )

    def get_capture_status(self, *, output_dir: Optional[Path] = None) -> dict:
        return android_profiler.get_capture_status(
            Path(output_dir or Path.cwd()), client=self.client
        )

    def stop_capture(self, *, output_dir: Optional[Path] = None) -> CaptureResult:
        return android_profiler.stop_capture(
            self.client, Path(output_dir or Path.cwd())
        )

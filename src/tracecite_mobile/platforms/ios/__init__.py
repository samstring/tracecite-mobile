# -*- coding: utf-8 -*-
"""iOS backend for devices, streams, sessions, and capture.

不修改现有 iOS 实现，仅做 1:1 转发；CLI 现有 iOS 命令路径保持不变，保证向后兼容。
此后端同时作为统一架构的 iOS 一侧，供 get_backend('ios') 使用与测试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from ..base import BaseBackend, BackendError
from ..models import CaptureResult, DeviceRef, LogSessionResult
from ...device import devices as ios_devices
from ...device import stream as ios_stream
from ...device import session as ios_session
from ...device import capture as ios_capture
from ...shared import config as ios_config


class IosBackend(BaseBackend):
    platform = "ios"

    # ---- 设备 ----
    def list_devices(self) -> List[DeviceRef]:
        out: List[DeviceRef] = []
        for d in ios_devices.list_connected_devices():
            out.append(
                DeviceRef(
                    platform="ios",
                    identifier=d.udid,
                    name=d.name,
                    model=d.model,
                    state="connected",
                )
            )
        return out

    def _device_from_ref(self, ref: DeviceRef):
        for d in ios_devices.list_connected_devices():
            if d.udid == ref.identifier:
                return d
        raise BackendError(f"未找到 UDID: {ref.identifier}")

    def resolve_device(
        self,
        *,
        udid: Optional[str] = None,
        name: Optional[str] = None,
        index: Optional[int] = None,
        interactive: bool = True,
    ) -> DeviceRef:
        d = ios_devices.resolve_device(
            udid=udid, name=name, index=index, interactive=interactive
        )
        return DeviceRef(
            platform="ios", identifier=d.udid, name=d.name, model=d.model
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
        dev = self._device_from_ref(device)
        ios_stream.stream_logs(
            dev,
            process_name=package or "",
            subsystem_filter=kwargs.get("subsystem") or "all",
            output_path=Path(output_path),
            also_stdout=also_stdout,
        )
        from datetime import datetime

        return LogSessionResult(
            platform="ios",
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
        **kwargs: Any,
    ) -> dict:
        dev = self._device_from_ref(device)
        profile = ios_config.load_project_profile(Path.cwd())
        sess = ios_session.start_stream_session(
            dev,
            profile,
            include_date=include_date,
            output_file=Path(output_file) if output_file else None,
        )
        return {"started": True, "session": sess.to_dict()}

    def get_session_status(self, *, output_dir: Optional[Path] = None) -> dict:
        profile = ios_config.load_project_profile(Path.cwd())
        out = output_dir or profile.log_output_dir
        return ios_session.get_stream_session_status(Path(out))

    def stop_session(self, *, output_dir: Optional[Path] = None) -> dict:
        profile = ios_config.load_project_profile(Path.cwd())
        out = output_dir or profile.log_output_dir
        sess = ios_session.stop_stream_session(Path(out))
        return {"stopped": True, "session": sess.to_dict()}

    # ---- 性能现场 ----
    def start_capture(
        self,
        device: DeviceRef,
        *,
        template: str,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> dict:
        dev = self._device_from_ref(device)
        profile = ios_config.load_project_profile(Path.cwd())
        out = output_dir or profile.capture_output_dir
        sess = ios_capture.start_capture(
            dev,
            template=template or profile.capture_template,
            attach=kwargs.get("attach") or profile.attach_process,
            launch=kwargs.get("launch"),
            output_dir=Path(out),
            no_prompt=not kwargs.get("prompt", False),
            no_summarize=kwargs.get("no_summarize", False),
            quiet=kwargs.get("quiet", False),
        )
        return {"active": True, "session": {**sess.to_dict(), "alive": True}}

    def get_capture_status(self, *, output_dir: Optional[Path] = None) -> dict:
        profile = ios_config.load_project_profile(Path.cwd())
        out = output_dir or profile.capture_output_dir
        return ios_capture.get_capture_status(Path(out))

    def stop_capture(self, *, output_dir: Optional[Path] = None) -> CaptureResult:
        profile = ios_config.load_project_profile(Path.cwd())
        out = output_dir or profile.capture_output_dir
        result = ios_capture.stop_capture(Path(out))
        device = DeviceRef(platform="ios", identifier="", name="")
        return CaptureResult(
            platform="ios",
            device=device,
            trace_path=Path(result.trace_path),
            metadata_path=None,
            summary_path=Path(result.log_path) if result.log_path else None,
        )

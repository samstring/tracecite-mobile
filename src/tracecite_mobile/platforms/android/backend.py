# -*- coding: utf-8 -*-
"""Android backend for adb, devices, logging, and profiling."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

from ..base import BaseBackend, BackendError, UnsupportedCapabilityError
from ..models import (
    ArchiveSegment,
    Capabilities,
    CaptureResult,
    DeviceRef,
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
from .adb import AndroidAdbClient, AndroidBackendError, AdbDeviceNotFoundError
from . import devices as android_devices
from . import logger as android_logger
from . import profiler as android_profiler


_SCREEN_ACTIVITY_RE = re.compile(
    r"(?:mCurrentFocus|mFocusedApp)=.*?\s(?P<package>[^/\s}]+)/(?P<activity>[^}\s]+)"
)


class AndroidBackend(BaseBackend):
    platform = "android"

    _PUBLIC_PROFILES = {
        "startup": "perfetto-startup",
        "frame": "perfetto-frame",
        "memory": "perfetto-memory",
        "network": "perfetto-network",
    }
    _TEMPLATE_PROFILES = {value: key for key, value in _PUBLIC_PROFILES.items()}

    def __init__(self, run=None, adb_path: Optional[str] = None) -> None:
        super().__init__(run=run)
        self.client = AndroidAdbClient(run=self._run, adb_path=adb_path)

    def capabilities(self) -> Capabilities:
        """Declare only stable Android semantics.

        Diagnostics/crash and automatic archive rotation are intentionally not
        advertised until their evidence and lifecycle contracts are complete.
        """

        return Capabilities(
            platform=self.platform,
            device=True,
            app=False,
            process=True,
            log=True,
            multi_device_session=True,
            performance_profiles=("startup", "frame", "memory", "network"),
            archive=True,
            log_window=True,
            diagnostics=(),
            crash=(),
            platform_options={
                "automatic_rotation": False,
                "multi_device_session": True,
                "performance_profiles": {
                    "startup": "startup",
                    "frame": "frame",
                    "memory": "memory",
                    "network": "network",
                },
            },
        )

    def probe_environment(self) -> EnvironmentStatus:
        """Probe bridge/device readiness using implementation-neutral keys."""

        try:
            self.client.require_adb()
            raw = self.client.list_devices()
            connected = any(item.state == "device" for item in raw)
            checks = {
                "device_bridge": True,
                "log_stream": connected,
                "performance": connected,
            }
            detail = "ready" if connected else "no connected device"
            return EnvironmentStatus(
                platform=self.platform,
                ready=connected,
                checks=checks,
                detail=detail,
            )
        except Exception as exc:  # noqa: BLE001
            return EnvironmentStatus(
                platform=self.platform,
                ready=False,
                checks={"device_bridge": False, "log_stream": False, "performance": False},
                detail=str(exc),
            )

    # ---- 设备 ----
    def list_devices(self) -> List[DeviceRef]:
        return android_devices.list_devices(self.client)

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
        if name and names:
            raise AdbDeviceNotFoundError("name 与 names 不能同时使用。")
        selected_names = list(names or ())
        if name:
            selected_names.append(name)
        return android_devices.resolve_devices(
            self.client,
            serials=udids,
            names=selected_names or None,
            indices=indices,
            all_devices=all_devices,
            interactive=interactive,
        )

    # ---- 应用/进程 ----
    def list_processes(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        name: str = "",
    ) -> List[ProcessRef]:
        if not package:
            raise BackendError("Android process.list 需要 package；未可靠支持全量进程枚举。")
        pid = self.client.pidof(device.identifier, package)
        if pid is None:
            return []
        return [
            ProcessRef(
                platform=self.platform,
                identifier=f"{device.identifier}:{pid}",
                device=device,
                name=name or package,
                package=package,
                pid=pid,
            )
        ]

    def resolve_process(
        self,
        device: DeviceRef,
        *,
        pid: Optional[int] = None,
        package: Optional[str] = None,
        name: Optional[str] = None,
        interactive: bool = True,
    ) -> ProcessRef:
        if pid is None and not package:
            raise BackendError("Android process.resolve 需要 pid 或 package。")
        if pid is None:
            pid = self.client.pidof(device.identifier, str(package))
            if pid is None:
                raise BackendError(f"设备 {device.identifier} 上未运行进程: {package}")
        if pid <= 0:
            raise BackendError(f"无效进程 PID: {pid}")
        return ProcessRef(
            platform=self.platform,
            identifier=f"{device.identifier}:{pid}",
            device=device,
            name=name or str(package or ""),
            package=str(package or ""),
            pid=pid,
        )

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

    # ---- 可选 screen/UI 能力 ----
    @staticmethod
    def _screen_text(result, operation: str) -> str:
        if result.ok:
            return result.stdout
        detail = (result.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        raise BackendError(
            f"Android screen operation {operation} failed ({result.returncode}){suffix}"
        )

    def dump_ui_hierarchy(self, device: DeviceRef) -> str:
        """Return the current UI hierarchy source for ``device``."""

        remote_path = "/sdcard/window.xml"
        self._screen_text(
            self.client.run_adb(
                device.identifier,
                "shell",
                "uiautomator",
                "dump",
                remote_path,
            ),
            "dump_ui_hierarchy",
        )
        return self._screen_text(
            self.client.run_adb(
                device.identifier,
                "exec-out",
                "cat",
                remote_path,
            ),
            "read_ui_hierarchy",
        )

    def capture_screen(self, device: DeviceRef) -> bytes:
        """Return a PNG screenshot without exposing the transport client."""

        try:
            return self.client.screencap(device.identifier)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"Android screen capture failed: {exc}") from exc

    def current_app(self, device: DeviceRef) -> str:
        """Return the focused ``package/activity`` semantic identifier."""

        text = self.system_diagnostic(device, kind="window")
        match = _SCREEN_ACTIVITY_RE.search(text)
        if not match:
            return ""
        return f"{match.group('package')}/{match.group('activity')}"

    def system_diagnostic(
        self,
        device: DeviceRef,
        *,
        kind: str,
        target: str = "",
    ) -> str:
        """Return a supported device diagnostic as plain evidence text."""

        supported = {"meminfo", "gfxinfo", "activity", "window"}
        if kind not in supported:
            raise UnsupportedCapabilityError(f"screen.system_diagnostic:{kind}")
        args = ["shell", "dumpsys", kind]
        if target and kind in {"meminfo", "gfxinfo"}:
            args.append(target)
        if kind == "window":
            args.append("windows")
        return self._screen_text(
            self.client.run_adb(device.identifier, *args),
            f"system_diagnostic:{kind}",
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

    @staticmethod
    def _session_ref(state: dict, *, alive: Optional[bool] = None) -> SessionRef:
        device = DeviceRef(
            platform="android",
            identifier=str(state.get("serial") or ""),
            name=str(state.get("device_name") or state.get("model") or state.get("serial") or ""),
            model=str(state.get("model") or ""),
        )
        collector = state.get("collector_pid")
        try:
            collector_pid = int(collector) if collector is not None else None
        except (TypeError, ValueError):
            collector_pid = None
        return SessionRef(
            platform="android",
            identifier=str(state.get("session_id") or state.get("serial") or state.get("output_path") or ""),
            device=device,
            output_path=Path(state["output_path"]) if state.get("output_path") else None,
            started_at=str(state.get("started_at") or "") or None,
            collector_pid=collector_pid,
            state="running" if alive is not False else "stopped",
            healthy=alive,
            detail="",
            metadata={
                "package": str(state.get("package_name") or ""),
                "serial": device.identifier,
            },
        )

    @classmethod
    def _session_status(cls, payload: dict, *, state: Optional[str] = None) -> SessionStatus:
        rows = (
            payload.get("stopped")
            if state == "stopped"
            else payload.get("sessions")
        ) or []
        refs = tuple(
            cls._session_ref(row, alive=row.get("alive"))
            for row in rows
            if isinstance(row, dict)
        )
        active = bool(payload.get("active"))
        if state is None:
            state = "running" if active else ("idle" if not refs else "stopped")
        return SessionStatus(
            platform="android",
            state=state,
            sessions=refs,
            active=active if state != "stopped" else False,
            session_count=len(refs),
        )

    def start_sessions(
        self,
        devices: Sequence[DeviceRef],
        *,
        package: str = "",
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        payload = android_logger.start_sessions(
            self.client,
            list(devices),
            package=package,
            output_dir=Path(output_dir or Path.cwd()),
            include_date=bool(kwargs.get("include_date", False)),
            output_file=Path(kwargs["output_file"]) if kwargs.get("output_file") else None,
            popen=kwargs.get("popen"),
        )
        return self._session_status(payload, state="running")

    def list_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        payload = android_logger.list_sessions(
            Path(output_dir or Path.cwd()), refs=list(devices) if devices else None
        )
        return self._session_status(payload)

    def stop_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        all_devices: bool = False,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        payload = android_logger.stop_sessions(
            Path(output_dir or Path.cwd()),
            refs=list(devices) if devices else None,
            all_devices=all_devices,
        )
        return self._session_status(payload, state="stopped")

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

    # ---- 统一性能 profile ----
    def list_performance_profiles(self) -> List[PerformanceProfile]:
        return [
            PerformanceProfile(name=name, description=f"Android {name} performance profile")
            for name in self._PUBLIC_PROFILES
        ]

    def start_performance(
        self,
        device: DeviceRef,
        *,
        profile: Union[str, PerformanceProfile],
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceSession:
        name = profile.name if isinstance(profile, PerformanceProfile) else str(profile)
        template = self._PUBLIC_PROFILES.get(name)
        if template is None:
            raise AndroidBackendError(
                f"未知 Android performance profile: {name!r}（可选: {', '.join(self._PUBLIC_PROFILES)}）"
            )
        state = android_profiler.start_capture(
            self.client,
            device,
            template=template,
            output_dir=Path(output_dir or Path.cwd()),
        )
        return PerformanceSession(
            platform="android",
            identifier=str(state.get("session_id") or state.get("serial") or ""),
            profile=name,
            device=device,
            output_path=Path(state["local_trace_path"]) if state.get("local_trace_path") else None,
            started_at=str(state.get("started_at") or "") or None,
        )

    def get_performance_status(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceStatus:
        payload = android_profiler.get_capture_status(
            Path(output_dir or Path.cwd()), client=self.client
        )
        state = payload.get("session")
        if not isinstance(state, dict):
            return PerformanceStatus(platform="android", state="idle", session=None)
        template = str(state.get("profile") or state.get("template") or "")
        profile = self._TEMPLATE_PROFILES.get(template, template)
        device = DeviceRef(
            platform="android",
            identifier=str(state.get("serial") or ""),
            name=str(state.get("model") or state.get("serial") or ""),
            model=str(state.get("model") or ""),
        )
        session = PerformanceSession(
            platform="android",
            identifier=str(state.get("session_id") or state.get("serial") or ""),
            profile=profile,
            device=device,
            output_path=Path(state["local_trace_path"]) if state.get("local_trace_path") else None,
            started_at=str(state.get("started_at") or "") or None,
        )
        return PerformanceStatus(
            platform="android",
            state="running" if payload.get("active") else "stopped",
            session=session,
        )

    def stop_performance(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceResult:
        result = android_profiler.stop_capture(
            self.client, Path(output_dir or Path.cwd())
        )
        profile = getattr(result, "profile", None)
        return PerformanceResult(
            platform="android",
            device=result.device,
            trace_path=result.trace_path,
            metadata_path=result.metadata_path,
            summary_path=result.summary_path,
            profile=profile,
        )

    # ---- 通用 archive adapter ----
    def list_archive_segments(
        self,
        *,
        device: Optional[DeviceRef] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> List[ArchiveSegment]:
        from ...device.archive import list_archive_segments as list_segments

        payload = list_segments(
            Path(output_dir or Path.cwd()),
            device_name=device.name if device else None,
        )
        out: List[ArchiveSegment] = []
        for device_name, info in (payload.get("devices") or {}).items():
            for raw in info.get("segments") or []:
                out.append(
                    ArchiveSegment(
                        start=str(raw.get("start") or ""),
                        end=str(raw.get("end") or ""),
                        path=str(raw.get("path") or ""),
                        bytes=int(raw.get("bytes", 0) or 0),
                        lines=int(raw.get("lines", 0) or 0),
                        device=device
                        or DeviceRef(
                            platform="android",
                            identifier=device_name,
                            name=device_name,
                        ),
                    )
                )
        return out

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
        if device is None:
            raise BackendError("Android log window 需要明确 device，不能在多设备日志间猜测。")
        from ...device.archive import pull_archive_window

        result = pull_archive_window(
            Path(output_dir or kwargs.get("log_output_dir") or Path.cwd()),
            device_name=device.name or device.identifier,
            since=time_from,
            until=time_to,
            hot_path=Path(kwargs["hot_path"]) if kwargs.get("hot_path") else None,
            output_path=output_path,
        )
        return LogWindowResult(
            output_path=result.output_path,
            time_from=result.time_from,
            time_to=result.time_to,
            segments=tuple(result.segments),
            lines=result.lines,
            bytes=result.bytes,
        )

    def rotate_log(
        self,
        hot_path: Path,
        *,
        device: Optional[DeviceRef] = None,
        device_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        from ...device.archive import rotate_hot_log

        hot = Path(hot_path).expanduser()
        owner = device_name or (device.name if device is not None else "") or hot.stem
        requested_window = kwargs.get("hot_window_sec")
        return rotate_hot_log(
            hot,
            device_name=owner or "device",
            hot_window_sec=int(requested_window if requested_window is not None else 1800),
        )

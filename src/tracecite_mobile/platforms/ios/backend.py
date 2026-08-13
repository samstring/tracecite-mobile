# -*- coding: utf-8 -*-
"""iOS 的跨平台 ``PlatformBackend`` 实现。

这里是一个薄适配层：设备、日志 session、性能采集和 archive 的成熟实现
仍由 ``tracecite_mobile.device`` 提供，但公共调用方只看到稳定的跨平台模型。
旧的 ``session`` / ``capture`` 方法继续保留为兼容 shim，并委托到新能力。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence

from ..base import BaseBackend, BackendError
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
from ...device import archive as ios_archive
from ...device import capture as ios_capture
from ...device import devices as ios_devices
from ...device import session as ios_session
from ...device import stream as ios_stream
from ...shared import config as ios_config


_PROFILE_ALIASES = {
    "cpu": "Time Profiler",
    "time": "Time Profiler",
    "profiler": "Time Profiler",
    "system": "System Trace",
    "network": "Network",
    "net": "Network",
    "memory": "Allocations",
    "mem": "Allocations",
    "alloc": "Allocations",
    "allocations": "Allocations",
    "launch": "App Launch",
    "leak": "Leaks",
    "leaks": "Leaks",
    "hitch": "Animation Hitches",
    "hitches": "Animation Hitches",
}

_LEGACY_TO_PUBLIC_PROFILE = {
    "time profiler": "cpu",
    "system trace": "system",
    "network": "network",
    "allocations": "memory",
    "app launch": "launch",
}

# 这些名称是跨平台语义；具体采集实现不出现在能力声明中。
_PUBLIC_PROFILES = (
    PerformanceProfile("cpu", "CPU sampling"),
    PerformanceProfile("system", "System activity"),
    PerformanceProfile("network", "Network activity"),
    PerformanceProfile("memory", "Memory allocations"),
    PerformanceProfile("launch", "Application launch"),
)


class IosBackend(BaseBackend):
    """将成熟 iOS 设备模块适配到稳定跨平台后端协议。"""

    platform = "ios"

    # ---- environment / capabilities ---------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            device=True,
            # iOS 进程查询可用，但当前没有可靠、跨版本的 app launch/stop。
            app=False,
            process=True,
            log=True,
            multi_device_session=True,
            performance_profiles=tuple(item.name for item in _PUBLIC_PROFILES),
            archive=True,
            log_window=True,
            diagnostics=(),
            crash=(),
            platform_options={"session_stop": ("device", "all")},
        )

    def probe_environment(self) -> EnvironmentStatus:
        checks = {
            "device_bridge": self.which("xcrun") is not None,
            "log_stream": self.which("idevicesyslog") is not None,
            "performance": self.which("xcrun") is not None,
        }
        missing = [name for name, ready in checks.items() if not ready]
        detail = "环境就绪" if not missing else "缺少能力: " + ", ".join(missing)
        return EnvironmentStatus(
            platform=self.platform,
            ready=not missing,
            checks=checks,
            detail=detail,
        )

    # ---- device / process --------------------------------------------
    @staticmethod
    def _to_device_ref(device: Any) -> DeviceRef:
        return DeviceRef(
            platform="ios",
            identifier=str(device.udid),
            name=str(device.name),
            model=str(getattr(device, "model", "") or ""),
            state="connected",
        )

    @staticmethod
    def _to_process_ref(device: DeviceRef, process: Any) -> ProcessRef:
        pid = int(getattr(process, "pid", 0) or 0)
        name = str(getattr(process, "name", "") or "")
        return ProcessRef(
            platform="ios",
            identifier=str(pid),
            device=device,
            name=name,
            pid=pid or None,
        )

    def list_devices(self) -> List[DeviceRef]:
        return [self._to_device_ref(item) for item in ios_devices.list_connected_devices()]

    def _device_from_ref(self, ref: DeviceRef) -> Any:
        if ref.platform and ref.platform != self.platform:
            raise BackendError(f"设备平台不匹配: {ref.platform}")
        for device in ios_devices.list_connected_devices():
            if str(device.udid) == str(ref.identifier):
                return device
        raise BackendError(f"未找到 UDID: {ref.identifier}")

    def resolve_device(
        self,
        *,
        udid: Optional[str] = None,
        name: Optional[str] = None,
        index: Optional[int] = None,
        interactive: bool = True,
    ) -> DeviceRef:
        return self._to_device_ref(
            ios_devices.resolve_device(
                udid=udid,
                name=name,
                index=index,
                interactive=interactive,
            )
        )

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
        # Legacy resolver already owns de-duplication and interactive errors.
        if names:
            resolved: List[Any] = []
            for item in names:
                resolved.extend(
                    ios_devices.resolve_devices(
                        name=str(item),
                        all_devices=False,
                        interactive=interactive,
                    )
                )
            seen: set[str] = set()
            refs: List[DeviceRef] = []
            for item in resolved:
                identifier = str(item.udid)
                if identifier in seen:
                    continue
                seen.add(identifier)
                refs.append(self._to_device_ref(item))
            return refs
        return [
            self._to_device_ref(item)
            for item in ios_devices.resolve_devices(
                udids=list(udids) if udids is not None else None,
                name=name,
                indices=list(indices) if indices is not None else None,
                all_devices=all_devices,
                interactive=interactive,
            )
        ]

    def list_processes(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        name: str = "",
    ) -> List[ProcessRef]:
        raw_device = self._device_from_ref(device)
        list_running = getattr(ios_devices, "_list_running_processes", None)
        if list_running is None:
            if package or name:
                process = ios_devices.find_running_process(raw_device, package or name)
                processes = [] if process is None else [process]
            else:
                raise BackendError("iOS 进程列表能力不可用")
        else:
            processes = list_running(raw_device)
        refs = [self._to_process_ref(device, item) for item in processes]
        needle = (package or name or "").strip().lower()
        if needle:
            refs = [item for item in refs if needle in item.name.lower()]
        return refs

    def resolve_process(
        self,
        device: DeviceRef,
        *,
        pid: Optional[int] = None,
        package: Optional[str] = None,
        name: Optional[str] = None,
        interactive: bool = True,
    ) -> ProcessRef:
        raw_device = self._device_from_ref(device)
        query = str(pid) if pid is not None else (package or name or "")
        if not query:
            raise BackendError("resolve_process 需要 pid、package 或 name")
        process = ios_devices.find_running_process(raw_device, query)
        if process is None:
            raise BackendError(f"设备上未找到运行中的进程: {query}")
        return self._to_process_ref(device, process)

    # ---- profile / config helpers ------------------------------------
    @staticmethod
    def _profile(
        *,
        output_dir: Optional[Path] = None,
        package: Optional[str] = None,
        subsystem: Optional[str] = None,
    ) -> Any:
        profile = ios_config.load_project_profile(Path.cwd())
        updates: dict[str, Any] = {}
        if output_dir is not None:
            updates["log_output_dir"] = Path(output_dir).expanduser().resolve()
        if package:
            updates["process_name"] = str(package)
        if subsystem is not None:
            updates["subsystem"] = str(subsystem)
        return replace(profile, **updates) if updates else profile

    @staticmethod
    def _output_dir(output_dir: Optional[Path], *, capture: bool = False) -> Path:
        if output_dir is not None:
            return Path(output_dir).expanduser().resolve()
        profile = ios_config.load_project_profile(Path.cwd())
        return Path(
            profile.capture_output_dir if capture else profile.log_output_dir
        ).expanduser().resolve()

    # ---- foreground and background logs -------------------------------
    def stream_logs(
        self,
        device: DeviceRef,
        *,
        package: str = "",
        output_path: Path,
        also_stdout: bool = True,
        **kwargs: Any,
    ) -> LogSessionResult:
        raw_device = self._device_from_ref(device)
        started_at = datetime.now().isoformat(timespec="seconds")
        profile = self._profile(
            output_dir=Path(output_path).parent,
            package=package or None,
            subsystem=kwargs.get("subsystem"),
        )
        ios_stream.stream_logs(
            raw_device,
            process_name=profile.process_name,
            subsystem_filter=profile.subsystem or "all",
            output_path=Path(output_path),
            also_stdout=also_stdout,
            hot_window_sec=int(kwargs.get("hot_window_sec", profile.hot_window_sec or 1800)),
            rotate_check_bytes=int(kwargs.get("rotate_check_bytes", 256 * 1024)),
        )
        return LogSessionResult(
            platform=self.platform,
            device=device,
            output_path=Path(output_path),
            started_at=started_at,
        )

    @staticmethod
    def _session_ref(raw: Mapping[str, Any], *, state: Optional[str] = None) -> SessionRef:
        udid = str(raw.get("device_udid") or raw.get("udid") or "")
        name = str(raw.get("device_name") or raw.get("name") or "")
        device = DeviceRef(
            platform="ios",
            identifier=udid,
            name=name,
            model=str(raw.get("device_model") or raw.get("model") or ""),
        ) if (udid or name) else None
        output = raw.get("output_path")
        pid_raw = raw.get("pid")
        try:
            pid = int(pid_raw) if pid_raw is not None else None
        except (TypeError, ValueError):
            pid = None
        alive = raw.get("alive")
        healthy = raw.get("healthy")
        if healthy is None and alive is not None:
            healthy = bool(alive)
        session_state = state or ("running" if alive else "stopped")
        identifier = udid or str(output or pid or "session")
        metadata = dict(raw)
        return SessionRef(
            platform="ios",
            identifier=identifier,
            device=device,
            output_path=Path(str(output)) if output else None,
            started_at=(str(raw["started_at"]) if raw.get("started_at") else None),
            collector_pid=pid,
            state=session_state,
            healthy=healthy,
            detail=("stream stalled" if raw.get("stalled") else ""),
            metadata=metadata,
        )

    @staticmethod
    def _status_from_payload(payload: Mapping[str, Any]) -> SessionStatus:
        raw_sessions = payload.get("sessions") or []
        refs = tuple(
            IosBackend._session_ref(item)
            for item in raw_sessions
            if isinstance(item, Mapping)
        )
        active = bool(payload.get("active"))
        return SessionStatus(
            platform="ios",
            state="running" if active else ("stopped" if refs else "idle"),
            sessions=refs,
            active=active,
            session_count=int(payload.get("session_count", len(refs)) or len(refs)),
        )

    def start_sessions(
        self,
        devices: Sequence[DeviceRef],
        *,
        package: str = "",
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        if not devices:
            raise BackendError("未指定任何设备")
        output_file = kwargs.get("output_file")
        if output_file is not None and len(devices) != 1:
            raise BackendError("多设备 session start 不能使用 output_file")
        profile = self._profile(
            output_dir=output_dir,
            package=package or None,
            subsystem=kwargs.get("subsystem"),
        )
        include_date = bool(kwargs.get("include_date", kwargs.get("date", False)))
        requested_window = kwargs.get("hot_window_sec")
        hot_window_sec = int(
            requested_window
            if requested_window is not None
            else (profile.hot_window_sec or 1800)
        )
        started: List[SessionRef] = []
        errors: List[str] = []
        for index, ref in enumerate(devices):
            try:
                raw = self._device_from_ref(ref)
                session = ios_session.start_stream_session(
                    raw,
                    profile,
                    include_date=include_date,
                    output_file=(Path(output_file) if output_file is not None else None),
                    hot_window_sec=hot_window_sec,
                    platform=self.platform,
                )
                started.append(
                    self._session_ref(
                        {**session.to_dict(), "alive": True, "healthy": True},
                        state="running",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - retain partial-device detail
                errors.append(f"{ref.name or ref.identifier}: {exc}")
        if errors:
            prefix = "全部设备启动失败" if not started else "部分设备启动失败"
            raise BackendError(prefix + ":\n" + "\n".join(errors))
        return SessionStatus(
            platform=self.platform,
            state="running",
            sessions=tuple(started),
            active=True,
        )

    def list_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        payload = ios_session.get_stream_session_status(
            self._output_dir(output_dir)
        )
        self._last_session_payload = dict(payload)
        status = self._status_from_payload(payload)
        if not devices:
            return status
        allowed = {item.identifier for item in devices}
        sessions = tuple(
            item for item in status.sessions
            if item.device is not None and item.device.identifier in allowed
        )
        return SessionStatus(
            platform=self.platform,
            state="running" if any(item.healthy for item in sessions) else ("stopped" if sessions else "idle"),
            sessions=sessions,
            active=any(bool(item.healthy) for item in sessions),
        )

    def stop_sessions(
        self,
        *,
        devices: Optional[Sequence[DeviceRef]] = None,
        all_devices: bool = False,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> SessionStatus:
        out = self._output_dir(output_dir)
        explicit_udid = kwargs.get("udid")
        stop_all = bool(all_devices or kwargs.get("stop_all", False))
        targets = list(devices or ())
        if not targets and explicit_udid:
            targets = [DeviceRef(self.platform, str(explicit_udid), str(explicit_udid))]
        stopped: List[SessionRef] = []
        if targets and not stop_all:
            for target in targets:
                raw_stopped = ios_session.stop_stream_sessions(
                    out, udid=target.identifier, stop_all=False
                )
                for item in raw_stopped:
                    raw = item.to_dict()
                    raw.setdefault("device_udid", target.identifier)
                    raw.setdefault("device_name", target.name)
                    raw.setdefault("device_model", target.model)
                    stopped.append(self._session_ref(raw, state="stopped"))
        else:
            raw_stopped = ios_session.stop_stream_sessions(
                out, udid=None, stop_all=stop_all
            )
            stopped.extend(
                self._session_ref(item.to_dict(), state="stopped")
                for item in raw_stopped
            )
        return SessionStatus(
            platform=self.platform,
            state="stopped",
            sessions=tuple(stopped),
            active=False,
        )

    # Legacy session shims ---------------------------------------------
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
        status = self.start_sessions(
            (device,),
            package=package,
            output_dir=output_dir,
            include_date=include_date,
            output_file=output_file,
            **kwargs,
        )
        session = status.session
        return {"started": True, "session": dict(session.metadata) if session else {}}

    def get_session_status(self, *, output_dir: Optional[Path] = None) -> dict:
        status = self.list_sessions(output_dir=output_dir)
        sessions = [dict(item.metadata) for item in status.sessions]
        for item, raw in zip(status.sessions, sessions):
            raw.setdefault("output_path", str(item.output_path) if item.output_path else None)
            raw.setdefault("device_udid", item.device.identifier if item.device else "")
            raw.setdefault("device_name", item.device.name if item.device else "")
            raw["alive"] = bool(item.healthy)
            raw["healthy"] = item.healthy
        return {
            "active": status.active,
            "sessions": sessions,
            "session_count": status.session_count,
            "capture": getattr(self, "_last_session_payload", {}).get("capture"),
        }

    def stop_session(self, *, output_dir: Optional[Path] = None) -> dict:
        status = self.stop_sessions(output_dir=output_dir, all_devices=False)
        session = status.session
        return {
            "stopped": True,
            "session": dict(session.metadata) if session else {},
            "sessions": [dict(item.metadata) for item in status.sessions],
        }

    # ---- performance --------------------------------------------------
    @staticmethod
    def _resolve_profile(profile: str | PerformanceProfile) -> tuple[str, str]:
        name = profile.name if isinstance(profile, PerformanceProfile) else str(profile)
        key = name.strip().lower()
        return name, _PROFILE_ALIASES.get(key, name.strip())

    @staticmethod
    def _performance_session(raw: Optional[Mapping[str, Any]]) -> Optional[PerformanceSession]:
        if not raw:
            return None
        device_id = str(raw.get("device_udid") or raw.get("udid") or "")
        device_name = str(raw.get("device_name") or raw.get("name") or "")
        device = DeviceRef(
            "ios", device_id, device_name, str(raw.get("device_model") or "")
        ) if (device_id or device_name) else None
        trace = raw.get("trace_path") or raw.get("output_path")
        identifier = str(raw.get("pid") or trace or "performance")
        profile = str(raw.get("profile") or raw.get("template") or "")
        profile = _LEGACY_TO_PUBLIC_PROFILE.get(profile.lower(), profile)
        return PerformanceSession(
            platform="ios",
            identifier=identifier,
            profile=profile,
            device=device,
            output_path=Path(str(trace)) if trace else None,
            started_at=(str(raw["started_at"]) if raw.get("started_at") else None),
        )

    def list_performance_profiles(self) -> List[PerformanceProfile]:
        return list(_PUBLIC_PROFILES)

    def start_performance(
        self,
        device: DeviceRef,
        *,
        profile: str | PerformanceProfile,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceSession:
        public_name, legacy_template = self._resolve_profile(profile)
        raw_device = self._device_from_ref(device)
        config = ios_config.load_project_profile(Path.cwd())
        out = self._output_dir(output_dir, capture=True)
        raw = ios_capture.start_capture(
            raw_device,
            template=legacy_template or config.capture_template,
            attach=kwargs.get("attach") or config.attach_process,
            launch=kwargs.get("launch"),
            output_dir=out,
            no_prompt=not bool(kwargs.get("prompt", False)),
            no_summarize=bool(kwargs.get("no_summarize", False)),
            quiet=bool(kwargs.get("quiet", False)),
        )
        payload = raw if isinstance(raw, Mapping) else {"session": raw.to_dict()}
        session_payload = dict(payload.get("session") or {})
        session_payload.setdefault("profile", public_name)
        session_payload.setdefault("device_udid", device.identifier)
        session_payload.setdefault("device_name", device.name)
        session_payload.setdefault("device_model", device.model)
        result = self._performance_session(session_payload)
        if result is None:
            raise BackendError("性能采集启动成功但没有返回 session 状态")
        return replace(result, profile=public_name)

    def get_performance_status(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceStatus:
        raw = ios_capture.get_capture_status(self._output_dir(output_dir, capture=True))
        session = self._performance_session(raw.get("session") if isinstance(raw, Mapping) else None)
        active = bool(raw.get("active")) if isinstance(raw, Mapping) else False
        return PerformanceStatus(
            platform=self.platform,
            state="running" if active else ("stopped" if session else "idle"),
            session=session,
        )

    def stop_performance(
        self,
        *,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> PerformanceResult:
        out = self._output_dir(output_dir, capture=True)
        # 先读取状态，停止之后状态文件会被清理；这是设备身份的唯一可靠来源。
        prior: Optional[Mapping[str, Any]] = None
        try:
            status = ios_capture.get_capture_status(out)
            if isinstance(status, Mapping):
                prior = status.get("session")
        except Exception:
            prior = None
        summarize = kwargs.get("summarize")
        if summarize is None and "no_summarize" in kwargs:
            summarize = not bool(kwargs.get("no_summarize"))
        raw_result = ios_capture.stop_capture(
            out,
            summarize=summarize,
            quiet=bool(kwargs.get("quiet", False)),
            log_path=(
                Path(kwargs.get("context_log_path") or kwargs.get("log_path"))
                if (kwargs.get("context_log_path") or kwargs.get("log_path"))
                else None
            ),
        )
        device_id = str((prior or {}).get("device_udid") or "")
        device_name = str((prior or {}).get("device_name") or "")
        device_model = str((prior or {}).get("device_model") or "")
        device = DeviceRef(self.platform, device_id, device_name, device_model)
        trace_path = getattr(raw_result, "trace_path", None)
        toc_path = getattr(raw_result, "toc_path", None)
        log_path = getattr(raw_result, "log_path", None)
        legacy_profile = str((prior or {}).get("template") or "")
        public_profile = _LEGACY_TO_PUBLIC_PROFILE.get(
            legacy_profile.lower(), legacy_profile or None
        )
        return PerformanceResult(
            platform=self.platform,
            device=device,
            trace_path=Path(trace_path) if trace_path else None,
            metadata_path=Path(toc_path) if toc_path else None,
            summary_path=None,
            context_path=Path(log_path) if log_path else None,
            profile=public_profile,
        )

    # Legacy capture shims ---------------------------------------------
    def start_capture(
        self,
        device: DeviceRef,
        *,
        template: str,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> dict:
        session = self.start_performance(
            device,
            profile=template,
            output_dir=output_dir,
            **kwargs,
        )
        return {
            "active": True,
            "session": {
                "pid": int(session.identifier) if session.identifier.isdigit() else session.identifier,
                "trace_path": str(session.output_path) if session.output_path else None,
                "device_udid": session.device.identifier if session.device else "",
                "device_name": session.device.name if session.device else "",
                "device_model": session.device.model if session.device else "",
                "template": _PROFILE_ALIASES.get(
                    session.profile.lower(), session.profile
                ),
                "profile": session.profile,
                "started_at": session.started_at,
                "alive": True,
            },
        }

    def get_capture_status(self, *, output_dir: Optional[Path] = None) -> dict:
        status = self.get_performance_status(output_dir=output_dir)
        if status.session is None:
            return {"active": False, "session": None}
        session = status.session
        return {
            "active": status.state == "running",
            "session": {
                "pid": int(session.identifier) if session.identifier.isdigit() else session.identifier,
                "trace_path": str(session.output_path) if session.output_path else None,
                "device_udid": session.device.identifier if session.device else "",
                "device_name": session.device.name if session.device else "",
                "device_model": session.device.model if session.device else "",
                "template": _PROFILE_ALIASES.get(
                    session.profile.lower(), session.profile
                ),
                "profile": session.profile,
                "started_at": session.started_at,
                "alive": status.state == "running",
            },
        }

    def stop_capture(self, *, output_dir: Optional[Path] = None, **kwargs: Any) -> CaptureResult:
        return self.stop_performance(output_dir=output_dir, **kwargs)

    # ---- archive / log window ----------------------------------------
    def _archive_device_name(
        self,
        device: Optional[DeviceRef],
        output_dir: Path,
        kwargs: Mapping[str, Any],
    ) -> str:
        if device is not None and device.name:
            return device.name
        explicit = str(kwargs.get("device_name") or kwargs.get("device") or "").strip()
        if explicit:
            return explicit
        try:
            sessions = ios_session.load_all_sessions(output_dir)
        except Exception:
            sessions = {}
        if len(sessions) == 1:
            return next(iter(sessions.values())).device_name
        raise BackendError("archive 操作需要 device 或 device_name")

    def list_archive_segments(
        self,
        *,
        device: Optional[DeviceRef] = None,
        output_dir: Optional[Path] = None,
        **kwargs: Any,
    ) -> List[ArchiveSegment]:
        out = self._output_dir(output_dir)
        device_name = device.name if device is not None else kwargs.get("device_name")
        payload = ios_archive.list_archive_segments(
            out,
            device_name=str(device_name) if device_name else None,
        )
        result: List[ArchiveSegment] = []
        for key, info in (payload.get("devices") or {}).items():
            if not isinstance(info, Mapping):
                continue
            for raw in info.get("segments") or ():
                if not isinstance(raw, Mapping):
                    continue
                result.append(
                    ArchiveSegment(
                        start=str(raw.get("start", "")),
                        end=str(raw.get("end", "")),
                        path=str(raw.get("path", "")),
                        bytes=int(raw.get("bytes", 0) or 0),
                        lines=int(raw.get("lines", 0) or 0),
                        device=device,
                    )
                )
        return result

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
        out = self._output_dir(output_dir)
        device_name = self._archive_device_name(device, out, kwargs)
        result = ios_archive.pull_archive_window(
            out,
            device_name=device_name,
            since=str(kwargs.get("since", time_from)),
            until=str(kwargs.get("until", time_to)),
            hot_path=(Path(kwargs["hot_path"]) if kwargs.get("hot_path") else None),
            output_path=Path(output_path) if output_path is not None else None,
        )
        return LogWindowResult(
            output_path=Path(result.output_path),
            time_from=str(result.time_from),
            time_to=str(result.time_to),
            segments=tuple(str(item) for item in result.segments),
            lines=int(result.lines),
            bytes=int(result.bytes),
        )

    def rotate_log(
        self,
        hot_path: Path,
        *,
        device: Optional[DeviceRef] = None,
        device_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """保留 iOS archive rotate 的平台扩展，不放入最小公共协议。"""

        hot = Path(hot_path).expanduser()
        resolved_name = device_name or (device.name if device is not None else "")
        if not resolved_name:
            try:
                sessions = ios_session.load_all_sessions(
                    self._output_dir(kwargs.get("output_dir"))
                )
            except Exception:
                sessions = {}
            resolved_hot = hot.resolve()
            for session in sessions.values():
                if Path(session.output_path).expanduser().resolve() == resolved_hot:
                    resolved_name = session.device_name
                    break
        resolved_name = resolved_name or hot.stem or "device"
        requested_window = kwargs.get("hot_window_sec")
        return ios_archive.rotate_hot_log(
            hot,
            device_name=resolved_name,
            hot_window_sec=int(requested_window if requested_window is not None else 1800),
        )

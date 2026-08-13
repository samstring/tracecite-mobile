"""跨平台能力协议的最小契约测试。"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tracecite_mobile.platforms import (
    AppCapability,
    ArchiveCapability,
    BaseBackend,
    Capabilities,
    CaptureResult,
    CrashCapability,
    CrashEvent,
    CrashResult,
    DeviceCapability,
    DeviceRef,
    EnvironmentStatus,
    DiagnosticCapability,
    DiagnosticResult,
    LogCapability,
    LogWindowResult,
    PerformanceCapability,
    PerformanceProfile,
    PerformanceResult,
    PerformanceSession,
    PerformanceStatus,
    PlatformBackend,
    ProcessRef,
    SessionRef,
    SessionStatus,
    UnsupportedCapabilityError,
)


class _IosFakeBackend(BaseBackend):
    platform = "ios"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            device=True,
            app=True,
            process=True,
            log=True,
            multi_device_session=True,
            performance_profiles=("time", "network"),
            archive=True,
            log_window=True,
            diagnostics=("device", "memory"),
            crash=("crash", "hang"),
            platform_options={"max_devices": 8},
        )


class _AndroidFakeBackend(BaseBackend):
    platform = "android"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            device=True,
            app=True,
            process=True,
            log=True,
            multi_device_session=True,
            performance_profiles=("cpu",),
            archive=True,
            log_window=True,
            diagnostics=("device",),
            crash=("anr", "oom"),
            platform_options={"max_devices": 4},
        )


def test_ios_and_android_declare_the_same_public_capability_shape() -> None:
    ios = _IosFakeBackend()
    android = _AndroidFakeBackend()

    for backend in (ios, android):
        caps = backend.capabilities()
        assert isinstance(backend, PlatformBackend)
        assert isinstance(backend, DeviceCapability)
        assert isinstance(backend, AppCapability)
        assert isinstance(backend, LogCapability)
        assert isinstance(backend, ArchiveCapability)
        assert isinstance(backend, PerformanceCapability)
        assert isinstance(backend, DiagnosticCapability)
        assert isinstance(backend, CrashCapability)
        assert caps.platform == backend.platform
        assert caps.device and caps.app and caps.process and caps.log
        assert caps.multi_device_session
        assert caps.performance
        assert caps.performance_profiles
        assert caps.archive and caps.log_window
        assert caps.diagnostics and caps.crash
        assert caps.platform_options["max_devices"] > 0

    assert ios.capabilities().performance_profiles == ("time", "network")
    assert android.capabilities().performance_profiles == ("cpu",)
    assert ios.capabilities().diagnostics == ("device", "memory")
    assert android.capabilities().crash == ("anr", "oom")


def test_public_models_are_frozen_and_capture_remains_compatible() -> None:
    device = DeviceRef("ios", "device-1", "Phone")
    process = ProcessRef("ios", "process-1", device=device, pid=42)
    session = SessionRef(
        "ios",
        "session-1",
        device=device,
        process=process,
        collector_pid=43,
    )
    status = SessionStatus("ios", "running", sessions=(session,))
    profile = PerformanceProfile("time")
    performance_session = PerformanceSession("ios", "perf-1", "time", device=device)
    performance_status = PerformanceStatus("ios", "running", performance_session)
    result = PerformanceResult("ios", device, trace_path=Path("trace"))
    archive = LogWindowResult(Path("window.log"), "a", "b")
    diagnostics = DiagnosticResult("ios", "device", device=device)
    crash = CrashResult("ios", (CrashEvent("ios", "crash-1", "crash"),))
    environment = EnvironmentStatus("ios", True, {"device_bridge": True})

    assert status.session is session
    assert status.session_count == 1
    assert performance_status.session is performance_session
    assert result.output_path == result.trace_path
    assert isinstance(profile, PerformanceProfile)
    assert isinstance(archive, LogWindowResult)
    assert isinstance(diagnostics, DiagnosticResult)
    assert isinstance(crash, CrashResult)
    assert environment.ready is True
    assert CaptureResult is PerformanceResult

    with pytest.raises(FrozenInstanceError):
        device.name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "invoke, capability",
    [
        (lambda backend: backend.resolve_devices(all_devices=True), "device.resolve_devices"),
        (lambda backend: backend.probe_environment(), "device.probe_environment"),
        (lambda backend: backend.list_processes(DeviceRef("x", "1", "x")), "app.list_processes"),
        (lambda backend: backend.start_sessions(()), "log.start_sessions"),
        (lambda backend: backend.list_sessions(), "log.list_sessions"),
        (lambda backend: backend.stop_sessions(all_devices=True), "log.stop_sessions"),
        (
            lambda backend: backend.fetch_log_window(time_from="a", time_to="b"),
            "archive.fetch_log_window",
        ),
        (
            lambda backend: backend.start_performance(
                DeviceRef("x", "1", "x"), profile="profile"
            ),
            "performance.start",
        ),
        (lambda backend: backend.get_performance_status(), "performance.status"),
        (lambda backend: backend.stop_performance(), "performance.stop"),
        (
            lambda backend: backend.diagnose(DeviceRef("x", "1", "x")),
            "diagnostics.diagnose",
        ),
        (lambda backend: backend.list_crashes(), "crash.list"),
        (lambda backend: backend.fetch_crash("crash-1"), "crash.fetch"),
    ],
)
def test_base_backend_fails_closed(invoke, capability: str) -> None:
    with pytest.raises(UnsupportedCapabilityError, match=capability.split(".")[-1]):
        invoke(BaseBackend())

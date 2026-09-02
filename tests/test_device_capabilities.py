from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tracecite_mobile.device_capabilities as caps
from tracecite.extension import AgentCapability
from tracecite_mobile.extension import EXTENSION
from tracecite_mobile.platforms.models import (
    ArchiveSegment,
    CrashEvent,
    CrashResult,
    DeviceRef,
    DiagnosticResult,
    LogWindowResult,
    PerformanceProfile,
    PerformanceResult,
    PerformanceSession,
    PerformanceStatus,
    ProcessRef,
)


@dataclass
class FakeBackend:
    platform: str = "ios"

    def __post_init__(self):
        self.device = DeviceRef("ios", "D1", "Phone")
        self.process = ProcessRef(
            "ios",
            "P1",
            device=self.device,
            name="App",
            package="com.example.app",
            pid=42,
        )
        self.calls = []

    def resolve_device(self, *, udid=None, name=None, index=None, interactive=True):
        self.calls.append(("resolve_device", udid, interactive))
        assert udid == "D1"
        assert interactive is False
        return self.device

    def resolve_process(
        self,
        device,
        *,
        pid=None,
        package=None,
        name=None,
        interactive=True,
    ):
        self.calls.append(
            ("resolve_process", device.identifier, pid, package, name, interactive)
        )
        assert interactive is False
        return self.process

    def stop_app(self, device, process=None, **kwargs):
        self.calls.append(
            ("stop_app", device.identifier, process.identifier if process else None)
        )

    def list_archive_segments(self, *, device=None, output_dir=None, **kwargs):
        self.calls.append(
            (
                "list_archive_segments",
                device.identifier if device else None,
                str(output_dir) if output_dir else None,
            )
        )
        return [
            ArchiveSegment(
                "2026-09-03T00:00:00",
                "2026-09-03T00:05:00",
                "/tmp/archive.log",
                bytes=100,
                lines=5,
                device=device,
            )
        ]

    def fetch_log_window(
        self,
        *,
        device=None,
        time_from,
        time_to,
        output_dir=None,
        output_path=None,
        **kwargs,
    ):
        self.calls.append(
            (
                "fetch_log_window",
                device.identifier if device else None,
                time_from,
                time_to,
                str(output_dir) if output_dir else None,
                str(output_path) if output_path else None,
            )
        )
        return LogWindowResult(
            output_path=output_path or Path("/tmp/window.log"),
            time_from=time_from,
            time_to=time_to,
            segments=("/tmp/archive.log",),
            lines=5,
            bytes=100,
        )

    def list_performance_profiles(self):
        self.calls.append(("list_performance_profiles",))
        return [PerformanceProfile("cpu", "CPU sampling")]

    def start_performance(self, device, *, profile, output_dir=None, **kwargs):
        self.calls.append(
            (
                "start_performance",
                device.identifier,
                profile,
                str(output_dir) if output_dir else None,
            )
        )
        return PerformanceSession(
            "ios",
            "PERF1",
            profile,
            device=device,
            output_path=Path("/tmp/perf.trace"),
        )

    def get_performance_status(self, *, output_dir=None, **kwargs):
        self.calls.append(
            ("get_performance_status", str(output_dir) if output_dir else None)
        )
        return PerformanceStatus("ios", "running")

    def stop_performance(self, *, output_dir=None, **kwargs):
        self.calls.append(("stop_performance", str(output_dir) if output_dir else None))
        return PerformanceResult(
            "ios",
            self.device,
            trace_path=Path("/tmp/perf.trace"),
            profile="cpu",
        )

    def diagnose(self, device, *, kind="all", output_dir=None, **kwargs):
        self.calls.append(
            ("diagnose", device.identifier, kind, str(output_dir) if output_dir else None)
        )
        return DiagnosticResult(
            "ios",
            kind,
            device=device,
            data={"sample": "value"},
        )

    def list_crashes(self, device=None, *, since=None, until=None, **kwargs):
        self.calls.append(
            ("list_crashes", device.identifier if device else None, since, until)
        )
        return CrashResult(
            "ios",
            events=(
                CrashEvent(
                    "ios",
                    "C1",
                    "crash",
                    device=device,
                    occurred_at="2026-09-03T00:00:00",
                    summary="synthetic crash",
                ),
            ),
        )

    def fetch_crash(self, event, *, output_path=None, **kwargs):
        self.calls.append(
            ("fetch_crash", str(event), str(output_path) if output_path else None)
        )
        return CrashResult(
            "ios",
            events=(CrashEvent("ios", str(event), "crash", device=self.device),),
            output_path=output_path or Path("/tmp/crash.txt"),
        )


def _fake(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(caps, "get_backend", lambda platform: backend)
    monkeypatch.setattr(
        caps,
        "_backend_and_device",
        lambda arguments: (
            "ios",
            backend,
            backend.resolve_device(
                udid=str(arguments.get("device") or ""),
                interactive=False,
            ),
        ),
    )
    return backend


def test_app_stop_requires_explicit_process_and_maps_to_backend(monkeypatch) -> None:
    backend = _fake(monkeypatch)

    stopped = caps.stop_app(
        {
            "platform": "ios",
            "device": "D1",
            "package": "com.example.app",
        }
    )

    assert stopped["stopped"] is True
    assert stopped["process"]["pid"] == 42
    assert (
        "resolve_process",
        "D1",
        None,
        "com.example.app",
        None,
        False,
    ) in backend.calls
    assert ("stop_app", "D1", "P1") in backend.calls

    try:
        caps.stop_app({"platform": "ios", "device": "D1"})
    except ValueError as exc:
        assert "pid、package 或 name" in str(exc)
    else:
        raise AssertionError("stop_app must reject an unresolved process target")


def test_archive_capabilities_map_explicit_scope(monkeypatch, tmp_path) -> None:
    backend = _fake(monkeypatch)

    listed = caps.list_archive_segments(
        {
            "platform": "ios",
            "device": "D1",
            "output_dir": str(tmp_path),
        }
    )
    fetched = caps.fetch_log_window(
        {
            "platform": "ios",
            "device": "D1",
            "time_from": "2026-09-03T00:00:00",
            "time_to": "2026-09-03T00:05:00",
            "output_dir": str(tmp_path),
            "output_path": str(tmp_path / "window.log"),
        }
    )

    assert listed["count"] == 1
    assert listed["segments"][0]["lines"] == 5
    assert fetched["platform"] == "ios"
    assert fetched["device"]["identifier"] == "D1"
    assert fetched["lines"] == 5
    assert (
        "list_archive_segments",
        "D1",
        str(tmp_path),
    ) in backend.calls
    assert (
        "fetch_log_window",
        "D1",
        "2026-09-03T00:00:00",
        "2026-09-03T00:05:00",
        str(tmp_path),
        str(tmp_path / "window.log"),
    ) in backend.calls


def test_performance_capabilities_map_to_backend(monkeypatch, tmp_path) -> None:
    backend = _fake(monkeypatch)

    profiles = caps.list_performance_profiles({"platform": "ios"})
    started = caps.start_performance(
        {
            "platform": "ios",
            "device": "D1",
            "profile": "cpu",
            "output_dir": str(tmp_path),
        }
    )
    status = caps.get_performance_status(
        {
            "platform": "ios",
            "output_dir": str(tmp_path),
        }
    )
    stopped = caps.stop_performance(
        {
            "platform": "ios",
            "output_dir": str(tmp_path),
        }
    )

    assert profiles["profiles"][0]["name"] == "cpu"
    assert started["profile"] == "cpu"
    assert status["state"] == "running"
    assert stopped["trace_path"] == "/tmp/perf.trace"
    assert ("list_performance_profiles",) in backend.calls
    assert ("start_performance", "D1", "cpu", str(tmp_path)) in backend.calls
    assert ("get_performance_status", str(tmp_path)) in backend.calls
    assert ("stop_performance", str(tmp_path)) in backend.calls


def test_diagnostic_and_crash_queries_map_to_backend(monkeypatch, tmp_path) -> None:
    backend = _fake(monkeypatch)

    diagnostic = caps.run_diagnostic(
        {
            "platform": "ios",
            "device": "D1",
            "kind": "memory",
            "output_dir": str(tmp_path),
        }
    )
    crashes = caps.list_crashes(
        {
            "platform": "ios",
            "device": "D1",
            "since": "2026-09-03T00:00:00",
            "until": "2026-09-03T01:00:00",
        }
    )
    fetched = caps.fetch_crash(
        {
            "platform": "ios",
            "event": "C1",
            "output_path": str(tmp_path / "crash.txt"),
        }
    )

    assert diagnostic["kind"] == "memory"
    assert crashes["count"] == 1
    assert crashes["events"][0]["identifier"] == "C1"
    assert fetched["output_path"] == str(tmp_path / "crash.txt")
    assert ("diagnose", "D1", "memory", str(tmp_path)) in backend.calls
    assert (
        "list_crashes",
        "D1",
        "2026-09-03T00:00:00",
        "2026-09-03T01:00:00",
    ) in backend.calls
    assert ("fetch_crash", "C1", str(tmp_path / "crash.txt")) in backend.calls


def test_extended_capability_contract_and_extension_projection() -> None:
    specs = {
        capability.spec.name: capability.spec
        for capability in caps.agent_device_capabilities()
    }

    assert set(specs) == {
        "mobile.app.stop",
        "mobile.archive.list",
        "mobile.archive.fetch",
        "mobile.performance.profiles",
        "mobile.performance.start",
        "mobile.performance.status",
        "mobile.performance.stop",
        "mobile.diagnostics.run",
        "mobile.crashes.list",
        "mobile.crashes.fetch",
    }

    assert specs["mobile.performance.profiles"].kind == "query"
    assert specs["mobile.performance.profiles"].safety == "read"
    assert specs["mobile.performance.profiles"].requires_authorization is False

    for name in (
        "mobile.archive.list",
        "mobile.archive.fetch",
        "mobile.performance.status",
        "mobile.diagnostics.run",
        "mobile.crashes.list",
        "mobile.crashes.fetch",
    ):
        assert specs[name].kind == "query"
        assert specs[name].safety == "live_source"
        assert specs[name].requires_authorization is False

    for name in (
        "mobile.app.stop",
        "mobile.performance.start",
        "mobile.performance.stop",
    ):
        assert specs[name].kind == "action"
        assert specs[name].safety == "live_action"
        assert specs[name].requires_authorization is True
        assert "authorized live action" in specs[name].description.lower()

    extension_agent_names = {
        capability.spec.name
        for capability in EXTENSION.capabilities
        if isinstance(capability, AgentCapability)
    }
    assert set(specs) <= extension_agent_names

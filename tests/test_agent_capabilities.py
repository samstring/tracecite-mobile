from __future__ import annotations

from dataclasses import dataclass

import tracecite_mobile.capabilities as caps
from tracecite_mobile.platforms.models import (
    DeviceRef,
    EnvironmentStatus,
    ProcessRef,
    SessionRef,
    SessionStatus,
)


@dataclass
class FakeBackend:
    platform: str = "ios"

    def __post_init__(self):
        self.device = DeviceRef("ios", "D1", "Phone")
        self.calls = []

    def probe_environment(self):
        self.calls.append(("probe_environment",))
        return EnvironmentStatus("ios", True, {"tool": True}, "ready")

    def list_devices(self):
        self.calls.append(("list_devices",))
        return [self.device]

    def resolve_device(self, *, udid=None, name=None, index=None, interactive=True):
        self.calls.append(("resolve_device", udid, interactive))
        assert udid == "D1"
        assert interactive is False
        return self.device

    def list_processes(self, device, *, package="", name=""):
        self.calls.append(("list_processes", device.identifier, package, name))
        return [ProcessRef("ios", "P1", device=device, name="App", package=package, pid=42)]

    def list_sessions(self, *, devices=None, output_dir=None, **kwargs):
        self.calls.append(("list_sessions", str(output_dir) if output_dir else None))
        return SessionStatus("ios", state="running", sessions=(SessionRef("ios", "S1", device=self.device),))

    def start_sessions(self, devices, *, package="", output_dir=None, **kwargs):
        self.calls.append(("start_sessions", [item.identifier for item in devices], package, str(output_dir) if output_dir else None))
        return SessionStatus("ios", state="running", sessions=(SessionRef("ios", "S1", device=self.device),))

    def stop_sessions(self, *, devices=None, all_devices=False, output_dir=None, **kwargs):
        self.calls.append(("stop_sessions", [item.identifier for item in devices or []], all_devices, str(output_dir) if output_dir else None))
        return SessionStatus("ios", state="stopped")

    def launch_app(self, device, app, **kwargs):
        self.calls.append(("launch_app", device.identifier, app))
        return ProcessRef("ios", "P2", device=device, name="Launched", package=app, pid=99)


def _fake(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(caps, "get_backend", lambda platform: backend)
    return backend


def test_environment_and_process_queries_map_to_backend(monkeypatch) -> None:
    backend = _fake(monkeypatch)

    env = caps.probe_environment({"platform": "ios"})
    processes = caps.list_processes({"platform": "ios", "device": "D1", "package": "com.example.app"})

    assert env["ready"] is True
    assert processes["count"] == 1
    assert processes["processes"][0]["pid"] == 42
    assert ("resolve_device", "D1", False) in backend.calls
    assert ("list_processes", "D1", "com.example.app", "") in backend.calls


def test_session_actions_require_explicit_device_in_executor(monkeypatch, tmp_path) -> None:
    backend = _fake(monkeypatch)

    started = caps.start_log_session({
        "platform": "ios",
        "device": "D1",
        "package": "com.example.app",
        "output_dir": str(tmp_path),
    })
    stopped = caps.stop_log_session({
        "platform": "ios",
        "device": "D1",
        "output_dir": str(tmp_path),
    })

    assert started["state"] == "running"
    assert stopped["state"] == "stopped"
    assert ("start_sessions", ["D1"], "com.example.app", str(tmp_path)) in backend.calls
    assert ("stop_sessions", ["D1"], False, str(tmp_path)) in backend.calls


def test_launch_app_maps_explicit_device_and_app(monkeypatch) -> None:
    backend = _fake(monkeypatch)

    launched = caps.launch_app({"platform": "ios", "device": "D1", "app": "com.example.app"})

    assert launched["pid"] == 99
    assert ("launch_app", "D1", "com.example.app") in backend.calls

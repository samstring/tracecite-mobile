from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    stable_path: Path = Path("/tmp/tracecite-mobile-stable.log")

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
        return SessionStatus(
            "ios",
            state="running",
            sessions=(SessionRef("ios", "S1", device=self.device, output_path=Path("/tmp/live.log")),),
        )

    def start_sessions(self, devices, *, package="", output_dir=None, **kwargs):
        self.calls.append(("start_sessions", [item.identifier for item in devices], package, str(output_dir) if output_dir else None))
        return SessionStatus(
            "ios",
            state="running",
            sessions=(SessionRef("ios", "S1", device=self.device, output_path=Path("/tmp/live.log")),),
        )

    def stop_sessions(self, *, devices=None, all_devices=False, output_dir=None, **kwargs):
        self.calls.append(("stop_sessions", [item.identifier for item in devices or []], all_devices, str(output_dir) if output_dir else None))
        return SessionStatus(
            "ios",
            state="stopped",
            sessions=(
                SessionRef(
                    "ios",
                    "S1",
                    device=self.device,
                    output_path=self.stable_path,
                    state="stopped",
                ),
            ),
        )

    def launch_app(self, device, app, **kwargs):
        self.calls.append(("launch_app", device.identifier, app))
        return ProcessRef("ios", "P2", device=device, name="Launched", package=app, pid=99)


def _fake(monkeypatch, *, stable_path: Path | None = None):
    backend = FakeBackend(stable_path=stable_path or Path("/tmp/tracecite-mobile-stable.log"))
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
    stable_path = tmp_path / "stable.log"
    stable_path.write_text("stable evidence\n", encoding="utf-8")
    backend = _fake(monkeypatch, stable_path=stable_path)

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
    assert "artifacts" not in started
    assert stopped["state"] == "stopped"
    assert stopped["evidence_files"] == [str(stable_path)]
    assert stopped["artifacts"] == [
        {
            "kind": "device_log",
            "path": str(stable_path),
            "stable": True,
            "platform": "ios",
            "session_id": "S1",
            "device_id": "D1",
        }
    ]
    assert ("start_sessions", ["D1"], "com.example.app", str(tmp_path)) in backend.calls
    assert ("stop_sessions", ["D1"], False, str(tmp_path)) in backend.calls


def test_stopped_missing_file_is_not_advertised_as_stable(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "missing.log"
    _fake(monkeypatch, stable_path=missing)

    stopped = caps.stop_log_session({
        "platform": "ios",
        "device": "D1",
        "output_dir": str(tmp_path),
    })

    assert stopped["state"] == "stopped"
    assert "artifacts" not in stopped
    assert "evidence_files" not in stopped


def test_live_session_views_do_not_claim_stable_artifacts(monkeypatch) -> None:
    _fake(monkeypatch)

    listed = caps.list_log_sessions({"platform": "ios"})

    assert listed["state"] == "running"
    assert "artifacts" not in listed
    assert "evidence_files" not in listed


def test_launch_app_maps_explicit_device_and_app(monkeypatch) -> None:
    backend = _fake(monkeypatch)

    launched = caps.launch_app({"platform": "ios", "device": "D1", "app": "com.example.app"})

    assert launched["pid"] == 99
    assert ("launch_app", "D1", "com.example.app") in backend.calls


def test_agent_capability_contract_exposes_scope_and_authorization() -> None:
    specs = {cap.spec.name: cap.spec for cap in caps.agent_capabilities()}

    assert set(specs) == {
        "mobile.environment.probe",
        "mobile.devices.list",
        "mobile.processes.list",
        "mobile.sessions.list",
        "mobile.sessions.start",
        "mobile.sessions.stop",
        "mobile.app.launch",
    }

    assert specs["mobile.environment.probe"].safety == "read"
    assert specs["mobile.environment.probe"].requires_authorization is False
    assert "mechanical" in specs["mobile.environment.probe"].description.lower()

    for name in ("mobile.devices.list", "mobile.processes.list", "mobile.sessions.list"):
        assert specs[name].kind == "query"
        assert specs[name].safety == "live_source"
        assert specs[name].requires_authorization is False

    assert "scoped" in specs["mobile.devices.list"].description.lower()
    assert "root-cause" in specs["mobile.processes.list"].description.lower()
    assert "sufficiency" in specs["mobile.sessions.list"].description.lower()

    for name in ("mobile.sessions.start", "mobile.sessions.stop", "mobile.app.launch"):
        assert specs[name].kind == "action"
        assert specs[name].safety == "live_action"
        assert specs[name].requires_authorization is True
        assert "authorized live action" in specs[name].description.lower()

    assert "artifacts/evidence_files" in specs["mobile.sessions.stop"].description

    device_schema = specs["mobile.processes.list"].input_schema["properties"]["device"]
    assert "do not invent" in device_schema["description"].lower()

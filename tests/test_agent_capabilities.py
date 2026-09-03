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
    live_path: Path = Path("/tmp/live.log")

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
        self.calls.append(("list_sessions", [item.identifier for item in devices or []], str(output_dir) if output_dir else None))
        return SessionStatus(
            "ios",
            state="running",
            sessions=(SessionRef("ios", "S1", device=self.device, output_path=self.live_path),),
        )

    def start_sessions(self, devices, *, package="", output_dir=None, **kwargs):
        self.calls.append(("start_sessions", [item.identifier for item in devices], package, str(output_dir) if output_dir else None))
        return SessionStatus(
            "ios",
            state="running",
            sessions=(SessionRef("ios", "S1", device=self.device, output_path=self.live_path),),
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


def _fake(monkeypatch, *, stable_path: Path | None = None, live_path: Path | None = None):
    backend = FakeBackend(
        stable_path=stable_path or Path("/tmp/tracecite-mobile-stable.log"),
        live_path=live_path or Path("/tmp/live.log"),
    )
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


def test_cut_session_returns_stable_handoff_and_collection_continues(monkeypatch, tmp_path) -> None:
    live_path = tmp_path / "live.log"
    sealed_path = tmp_path / ".archive" / "Phone" / "sealed.log"
    backend = _fake(monkeypatch, live_path=live_path)

    class FakeSeal:
        def to_dict(self):
            return {
                "sealed_path": str(sealed_path),
                "hot_path": str(live_path),
                "start": "2026-09-03T11:00:00",
                "end": "2026-09-03T11:01:00",
                "bytes": 128,
                "lines": 3,
                "segment": {"path": str(sealed_path)},
            }

    seen = {}

    def fake_seal(path, *, device_name):
        seen["path"] = path
        seen["device_name"] = device_name
        return FakeSeal()

    monkeypatch.setattr(caps, "request_seal_hot", fake_seal)

    cut = caps.cut_log_session({
        "platform": "ios",
        "device": "D1",
        "output_dir": str(tmp_path),
    })

    assert seen == {"path": live_path, "device_name": "Phone"}
    assert cut["state"] == "running"
    assert cut["collection_continues"] is True
    assert cut["evidence_files"] == [str(sealed_path)]
    assert cut["artifacts"] == [
        {
            "kind": "device_log",
            "path": str(sealed_path),
            "stable": True,
            "sealed": True,
            "platform": "ios",
            "session_id": "S1",
            "device_id": "D1",
        }
    ]
    assert backend.calls.count(("list_sessions", ["D1"], str(tmp_path))) == 2


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


def test_live_session_views_advertise_cut_without_claiming_stability(monkeypatch) -> None:
    _fake(monkeypatch)

    listed = caps.list_log_sessions({"platform": "ios"})

    assert listed["state"] == "running"
    assert listed["supports_cut"] is True
    assert listed["sessions"][0]["supports_cut"] is True
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
        "mobile.sessions.cut",
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
    assert "supports_cut=true" in specs["mobile.sessions.list"].description

    for name in ("mobile.sessions.start", "mobile.sessions.cut", "mobile.sessions.stop", "mobile.app.launch"):
        assert specs[name].kind == "action"
        assert specs[name].safety == "live_action"
        assert specs[name].requires_authorization is True
        assert "authorized live action" in specs[name].description.lower()

    assert "collection session continues" in specs["mobile.sessions.cut"].description
    assert "artifacts/evidence_files" in specs["mobile.sessions.cut"].description
    assert "artifacts/evidence_files" in specs["mobile.sessions.stop"].description

    device_schema = specs["mobile.processes.list"].input_schema["properties"]["device"]
    assert "do not invent" in device_schema["description"].lower()

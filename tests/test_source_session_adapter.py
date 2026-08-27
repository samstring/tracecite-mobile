from __future__ import annotations

from tracecite_mobile.platforms.models import DeviceRef, ProcessRef, SessionRef
from tracecite_mobile.source_session import (
    build_mobile_source_profile,
    inspect_mobile_source_session,
    register_mobile_source_session,
    update_mobile_source_coverage,
)


class FakeStore:
    def __init__(self) -> None:
        self.registered = None
        self.inspected = None
        self.coverage = None

    def register_source_session(self, **kwargs):
        self.registered = kwargs
        return {"id": kwargs.get("session_id", "S1"), **kwargs}

    def inspect_source_session(self, session_id, *, identity=None, fingerprint=None):
        self.inspected = {"session_id": session_id, "identity": identity, "fingerprint": fingerprint}
        return {"session_id": session_id, "status": "known", "reuse": True}

    def update_source_session_coverage(self, session_id, coverage):
        self.coverage = {"session_id": session_id, "coverage": dict(coverage)}
        return self.coverage


def test_ios_profile_uses_stable_live_source_identity() -> None:
    device = DeviceRef(platform="ios", identifier="UDID-1", name="iPhone")
    process = ProcessRef(
        platform="ios",
        identifier="launch-1",
        device=device,
        package="com.example.app",
        pid=123,
    )
    session = SessionRef(platform="ios", identifier="collector-1", device=device, process=process)

    profile = build_mobile_source_profile(device, process=process, session=session)

    assert profile.format == "ios_console"
    assert profile.segmenter == "devicelog"
    assert profile.source_type == "mobile.ios.device_log"
    assert profile.identity == {
        "platform": "ios",
        "device_id": "UDID-1",
        "stream_type": "device_log",
        "app_id": "com.example.app",
        "launch_id": "launch-1",
        "collector_session_id": "collector-1",
    }


def test_growing_coverage_does_not_change_identity() -> None:
    device = DeviceRef(platform="android", identifier="serial-1", name="Pixel")
    first = build_mobile_source_profile(device, app="com.example.app")
    second = build_mobile_source_profile(device, app="com.example.app")
    assert first.identity == second.identity
    assert first.source_id == second.source_id


def test_adapter_registers_inspects_and_updates_runtime_state() -> None:
    device = DeviceRef(platform="ios", identifier="UDID-1", name="iPhone")
    store = FakeStore()

    created = register_mobile_source_session(
        store,
        device,
        app="com.example.app",
        coverage={"start": "10:00", "end": "10:05"},
        source_session_id="S1",
    )
    assert created["id"] == "S1"
    assert store.registered["recognition_status"] == "known"
    assert store.registered["extension"] == "mobile"

    inspected = inspect_mobile_source_session(
        store,
        "S1",
        device,
        app="com.example.app",
    )
    assert inspected["reuse"] is True
    assert store.inspected["identity"] == store.registered["identity"]

    updated = update_mobile_source_coverage(
        store,
        "S1",
        {"start": "10:00", "end": "10:10"},
    )
    assert updated["coverage"]["end"] == "10:10"

"""iOS 统一后端的契约测试；所有外部设备能力均使用 fake。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracecite_mobile.platforms.ios import IosBackend
from tracecite_mobile.platforms.models import (
    Capabilities,
    DeviceRef,
    EnvironmentStatus,
    PerformanceResult,
    SessionStatus,
)


def _device(identifier: str = "u1", name: str = "Phone") -> DeviceRef:
    return DeviceRef("ios", identifier, name, "iPhone")


def test_capabilities_and_environment(monkeypatch):
    backend = IosBackend()
    caps = backend.capabilities()
    assert isinstance(caps, Capabilities)
    assert caps.device and caps.process and caps.log
    assert caps.multi_device_session and caps.archive and caps.log_window
    assert caps.app is False
    assert caps.diagnostics == ()
    assert caps.crash == ()
    assert {"cpu", "system"}.issubset(caps.performance_profiles)

    monkeypatch.setattr(backend, "which", staticmethod(lambda tool: "/bin/tool"))
    env = backend.probe_environment()
    assert isinstance(env, EnvironmentStatus)
    assert env.ready is True
    assert env.checks == {
        "device_bridge": True,
        "log_stream": True,
        "performance": True,
    }


def test_device_and_process_adapters(monkeypatch):
    raw = SimpleNamespace(udid="u1", name="Phone", model="iPhone")
    process = SimpleNamespace(pid=42, name="Demo")
    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_devices.list_connected_devices",
        lambda: [raw],
    )
    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_devices.resolve_device",
        lambda **kwargs: raw,
    )
    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_devices._list_running_processes",
        lambda device: [process],
    )
    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_devices.find_running_process",
        lambda device, query: process,
    )
    backend = IosBackend()
    assert backend.list_devices()[0] == _device()
    assert backend.resolve_device(udid="u1", interactive=False).identifier == "u1"
    refs = backend.list_processes(_device(), name="Demo")
    assert refs[0].pid == 42 and refs[0].device == _device()
    assert backend.resolve_process(_device(), pid=42).name == "Demo"


def test_multi_session_status_and_legacy_shims(monkeypatch, tmp_path):
    backend = IosBackend()
    raw_device = SimpleNamespace(udid="u1", name="Phone", model="iPhone")
    backend._device_from_ref = lambda ref: raw_device  # type: ignore[method-assign]

    def start(raw, profile, **kwargs):
        from tracecite_mobile.device.session import StreamSession

        return StreamSession(
            pid=100,
            device_name="Phone",
            device_udid="u1",
            device_model="iPhone",
            process_name=profile.process_name,
            subsystem=profile.subsystem,
            output_path=str(tmp_path / "phone.log"),
            log_output_dir=str(tmp_path),
            capture_output_dir=str(tmp_path),
            stream_log_path=str(tmp_path / "collector.log"),
            started_at="2026-01-01T00:00:00",
            profile_path=None,
        )

    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_session.start_stream_session",
        start,
    )
    status = backend.start_sessions([_device()], output_dir=tmp_path, package="Demo")
    assert isinstance(status, SessionStatus)
    assert status.active and status.session is not None
    assert status.session.device == _device()

    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_session.get_stream_session_status",
        lambda output: {
            "active": True,
            "session_count": 1,
            "sessions": [
                {
                    "pid": 100,
                    "device_udid": "u1",
                    "device_name": "Phone",
                    "device_model": "iPhone",
                    "output_path": str(tmp_path / "phone.log"),
                    "alive": True,
                    "healthy": True,
                }
            ],
        },
    )
    listed = backend.list_sessions(output_dir=tmp_path)
    assert listed.active and listed.session_count == 1
    legacy = backend.get_session_status(output_dir=tmp_path)
    assert legacy["active"] is True and legacy["sessions"][0]["device_udid"] == "u1"

    stopped_raw = SimpleNamespace(to_dict=lambda: {
        "pid": 100,
        "device_udid": "u1",
        "device_name": "Phone",
        "output_path": str(tmp_path / "phone.log"),
    })
    stop_args = {}
    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_session.stop_stream_sessions",
        lambda output, **kwargs: (stop_args.update(kwargs) or [stopped_raw]),
    )
    stopped = backend.stop_sessions(devices=[_device()], output_dir=tmp_path)
    assert stopped.state == "stopped" and stopped.session.device == _device()
    assert stop_args == {"udid": "u1", "stop_all": False}

    backend.stop_sessions(output_dir=tmp_path)
    assert stop_args == {"udid": None, "stop_all": False}


def test_performance_profile_mapping_and_identity(monkeypatch, tmp_path):
    backend = IosBackend()
    raw_device = SimpleNamespace(udid="u1", name="Phone", model="iPhone")
    backend._device_from_ref = lambda ref: raw_device  # type: ignore[method-assign]
    seen = {}

    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_capture.start_capture",
        lambda raw, **kwargs: (
            seen.update(kwargs)
            or {
                "active": True,
                "session": {
                    "pid": 7,
                    "trace_path": str(tmp_path / "capture.trace"),
                    "device_udid": "u1",
                    "device_name": "Phone",
                    "device_model": "iPhone",
                    "started_at": "2026-01-01T00:00:00",
                    "template": "Time Profiler",
                },
            }
        ),
    )
    session = backend.start_performance(_device(), profile="cpu", output_dir=tmp_path)
    assert session.profile == "cpu"
    assert seen["template"] == "Time Profiler"
    assert session.device == _device()

    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_capture.get_capture_status",
        lambda output: {
            "active": True,
            "session": {
                "pid": 7,
                "trace_path": str(tmp_path / "capture.trace"),
                "device_udid": "u1",
                "device_name": "Phone",
                "device_model": "iPhone",
                "template": "Time Profiler",
            },
        },
    )
    stopped = {}
    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_capture.stop_capture",
        lambda output, **kwargs: (
            stopped.update(kwargs)
            or SimpleNamespace(
                trace_path=tmp_path / "capture.trace",
                toc_path=tmp_path / "toc.xml",
                log_path=tmp_path / "summary.log",
            )
        ),
    )
    result = backend.stop_performance(
        output_dir=tmp_path, quiet=True, no_summarize=True
    )
    assert isinstance(result, PerformanceResult)
    assert result.device == _device()
    assert result.metadata_path == tmp_path / "toc.xml"
    assert stopped["summarize"] is False


def test_archive_adapters(monkeypatch, tmp_path):
    backend = IosBackend()
    dev = _device()
    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_archive.list_archive_segments",
        lambda output, **kwargs: {
            "devices": {
                "Phone": {
                    "segments": [
                        {"start": "a", "end": "b", "path": "/tmp/a.log", "bytes": 3, "lines": 1}
                    ]
                }
            }
        },
    )
    segments = backend.list_archive_segments(device=dev, output_dir=tmp_path)
    assert len(segments) == 1 and segments[0].device == dev

    monkeypatch.setattr(
        "tracecite_mobile.platforms.ios.backend.ios_archive.pull_archive_window",
        lambda output, **kwargs: SimpleNamespace(
            output_path=tmp_path / "window.log",
            time_from="a",
            time_to="b",
            segments=["/tmp/a.log"],
            lines=1,
            bytes=3,
        ),
    )
    window = backend.fetch_log_window(
        device=dev,
        time_from="a",
        time_to="b",
        output_path=tmp_path / "window.log",
    )
    assert window.output_path == tmp_path / "window.log"
    assert window.segments == ("/tmp/a.log",)

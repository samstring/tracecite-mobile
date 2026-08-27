"""统一 PlatformBackend CLI 编排契约。"""

from __future__ import annotations

import json
import inspect
from pathlib import Path

import pytest

from tracecite_core.run import verify_manifest
from tracecite_mobile.cli import build_parser
from tracecite_mobile.commands import device as device_commands
from tracecite_mobile.platforms.android import cli_handlers as android_cli_handlers
from tracecite_mobile.platforms.base import BaseBackend
from tracecite_mobile.platforms.models import (
    Capabilities,
    DeviceRef,
    LogSessionResult,
    LogWindowResult,
    PerformanceResult,
    PerformanceSession,
    PerformanceStatus,
    SessionRef,
    SessionStatus,
)


class FakeBackend(BaseBackend):
    def __init__(self, platform: str, root: Path) -> None:
        super().__init__()
        self.platform = platform
        self.root = root
        self.calls: list[tuple[str, object]] = []
        self.devices = [
            DeviceRef(platform, "one", "One", "Phone"),
            DeviceRef(platform, "two", "Two", "Tablet"),
        ]

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            device=True,
            log=True,
            multi_device_session=True,
            performance=True,
            performance_profiles=("cpu", "network"),
            archive=True,
            log_window=True,
        )

    def list_devices(self):
        self.calls.append(("list_devices", None))
        return self.devices

    def resolve_device(self, *, udid=None, name=None, index=None, interactive=True):
        refs = self.resolve_devices(
            udids=[udid] if udid else None,
            name=name,
            indices=[index] if index is not None else None,
            interactive=interactive,
        )
        return refs[0]

    def resolve_devices(
        self,
        *,
        udids=None,
        name=None,
        names=None,
        indices=None,
        all_devices=False,
        interactive=True,
    ):
        self.calls.append(
            (
                "resolve_devices",
                {
                    "udids": udids,
                    "name": name,
                    "indices": indices,
                    "all_devices": all_devices,
                },
            )
        )
        if all_devices:
            return list(self.devices)
        selected = list(self.devices)
        if udids:
            selected = [d for d in selected if d.identifier in udids]
        if name:
            selected = [d for d in selected if name.lower() in d.name.lower()]
        if indices:
            selected = [self.devices[i - 1] for i in indices]
        if not selected:
            raise RuntimeError("fake device not found")
        return selected

    def stream_logs(self, device, *, package="", output_path, also_stdout=True, **kwargs):
        self.calls.append(("stream_logs", device.identifier))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("stream\n", encoding="utf-8")
        return LogSessionResult(self.platform, device, output_path, "now")

    def start_sessions(self, devices, *, package="", output_dir=None, **kwargs):
        self.calls.append(("start_sessions", [d.identifier for d in devices]))
        sessions = []
        for device in devices:
            output = Path(output_dir) / f"{device.identifier}.log"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("session\n", encoding="utf-8")
            sessions.append(
                SessionRef(self.platform, f"session-{device.identifier}", device=device, output_path=output)
            )
        return SessionStatus(self.platform, "running", tuple(sessions))

    def list_sessions(self, *, devices=None, output_dir=None, **kwargs):
        self.calls.append(("list_sessions", Path(output_dir) if output_dir else None))
        return SessionStatus(self.platform, "idle")

    def stop_sessions(self, *, devices=None, all_devices=False, output_dir=None, **kwargs):
        self.calls.append(
            ("stop_sessions", {"devices": devices, "all_devices": all_devices})
        )
        return SessionStatus(self.platform, "stopped")

    def start_performance(self, device, *, profile, output_dir=None, **kwargs):
        self.calls.append(("start_performance", profile))
        return PerformanceSession(self.platform, "perf-1", str(profile), device=device)

    def list_performance_profiles(self):
        self.calls.append(("list_performance_profiles", None))
        from tracecite_mobile.platforms.models import PerformanceProfile

        return [PerformanceProfile("cpu", "CPU")]

    def get_performance_status(self, *, output_dir=None, **kwargs):
        self.calls.append(("get_performance_status", None))
        return PerformanceStatus(self.platform, "idle")

    def stop_performance(self, *, output_dir=None, **kwargs):
        self.calls.append(("stop_performance", kwargs))
        output = Path(output_dir) / "trace.bin"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"trace")
        return PerformanceResult(self.platform, self.devices[0], trace_path=output)

    def list_archive_segments(self, *, device=None, output_dir=None, **kwargs):
        self.calls.append(("list_archive_segments", device.identifier if device else None))
        return []

    def fetch_log_window(self, *, device=None, time_from, time_to, output_path=None, **kwargs):
        self.calls.append(("fetch_log_window", (time_from, time_to)))
        output = Path(output_path or self.root / "window.log")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("window\n", encoding="utf-8")
        return LogWindowResult(output, time_from, time_to)


@pytest.fixture
def fake_backend(monkeypatch, tmp_path):
    backends = {}

    def get_backend(platform):
        backends.setdefault(platform, FakeBackend(platform, tmp_path))
        return backends[platform]

    monkeypatch.setattr(device_commands, "get_backend", get_backend)
    monkeypatch.setattr(device_commands, "DEFAULT_RUN_OUTPUT_DIR", tmp_path / "runs")
    return backends


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_same_dispatcher_lists_both_platforms(platform, fake_backend, capsys):
    args = build_parser().parse_args(["--platform", platform, "list", "--json"])
    assert device_commands.dispatch_device_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["udid" if platform == "ios" else "serial"] == "one"
    assert fake_backend[platform].calls == [("list_devices", None)]


def test_session_all_and_indices_use_standard_backend_methods(fake_backend, tmp_path):
    parser = build_parser()
    all_args = parser.parse_args(
        [
            "--platform",
            "ios",
            "session",
            "start",
            "--all",
            "--output-dir",
            str(tmp_path / "logs"),
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(all_args) == 0
    backend = fake_backend["ios"]
    assert ("start_sessions", ["one", "two"]) in backend.calls
    assert list((tmp_path / "logs" / ".runs").rglob("manifest.json"))

    indexed = parser.parse_args(
        [
            "--platform",
            "ios",
            "session",
            "start",
            "--indices",
            "2",
            "--output-dir",
            str(tmp_path / "logs2"),
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(indexed) == 0
    assert ("start_sessions", ["two"]) in backend.calls


def test_stream_json_and_session_manifest_artifact_lifecycle(
    fake_backend, tmp_path, capsys
):
    """Live logs are omitted at start and hashable only after stop."""

    class StableSessionBackend(FakeBackend):
        def __init__(self, platform: str, root: Path) -> None:
            super().__init__(platform, root)
            self.session_refs = []

        def start_sessions(self, devices, *, package="", output_dir=None, **kwargs):
            refs = []
            for device in devices:
                output = Path(output_dir) / f"{device.identifier}.log"
                collector = Path(output_dir) / f"{device.identifier}.collector.log"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("device\n", encoding="utf-8")
                collector.write_text("collector\n", encoding="utf-8")
                refs.append(
                    SessionRef(
                        self.platform,
                        f"session-{device.identifier}",
                        device=device,
                        output_path=output,
                        metadata={"stream_log_path": str(collector)},
                    )
                )
            self.session_refs = refs
            return SessionStatus(self.platform, "running", tuple(refs))

        def stop_sessions(self, *, devices=None, all_devices=False, output_dir=None, **kwargs):
            return SessionStatus(self.platform, "stopped", tuple(self.session_refs))

    backend = StableSessionBackend("ios", tmp_path)
    fake_backend["ios"] = backend

    stream_args = build_parser().parse_args(
        [
            "--platform",
            "ios",
            "stream",
            "",
            str(tmp_path / "stream"),
            "--udid",
            "one",
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(stream_args) == 0
    stream_payload = json.loads(capsys.readouterr().out)
    assert stream_payload["status"] == "completed"

    log_dir = tmp_path / "sessions"
    start_args = build_parser().parse_args(
        [
            "--platform",
            "ios",
            "session",
            "start",
            "--udid",
            "one",
            "--output-dir",
            str(log_dir),
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(start_args) == 0
    start_payload = json.loads(capsys.readouterr().out)
    start_manifest = Path(start_payload["manifest_path"])
    start_checked = verify_manifest(start_manifest)
    assert start_checked["valid"] is True
    start_roles = {
        item["role"] for item in json.loads(start_manifest.read_text())["artifacts"]
    }
    assert start_roles == {"operation_result"}
    assert "warnings" in start_payload

    stop_args = build_parser().parse_args(
        [
            "--platform",
            "ios",
            "session",
            "stop",
            "--udid",
            "one",
            "--output-dir",
            str(log_dir),
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(stop_args) == 0
    stop_payload = json.loads(capsys.readouterr().out)
    stop_manifest = Path(stop_payload["manifest_path"])
    stop_checked = verify_manifest(stop_manifest)
    assert stop_checked["valid"] is True
    stop_roles = {
        item["role"] for item in json.loads(stop_manifest.read_text())["artifacts"]
    }
    assert stop_roles == {"device_log", "collector_log", "operation_result"}


def test_performance_command_and_capture_alias_share_backend(fake_backend, tmp_path):
    parser = build_parser()
    performance = parser.parse_args(
        [
            "--platform",
            "android",
            "performance",
            "start",
            "--profile",
            "cpu",
            "--udid",
            "one",
            "--output-dir",
            str(tmp_path / "perf"),
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(performance) == 0

    capture_alias = parser.parse_args(
        [
            "--platform",
            "android",
            "capture",
            "start",
            "--template",
            "network",
            "--udid",
            "one",
            "--output-dir",
            str(tmp_path / "capture"),
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(capture_alias) == 0
    assert [call for call in fake_backend["android"].calls if call[0] == "start_performance"] == [
        ("start_performance", "cpu"),
        ("start_performance", "network"),
    ]
    assert list((tmp_path / "capture" / ".runs").rglob("manifest.json"))


def test_performance_profiles_are_discovered_through_backend(fake_backend, capsys):
    args = build_parser().parse_args(
        ["--platform", "ios", "performance", "profiles", "--json"]
    )

    assert device_commands.dispatch_device_command(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "cpu"
    assert ("list_performance_profiles", None) in fake_backend["ios"].calls


def test_performance_stop_reads_context_from_log_output_dir(
    fake_backend, monkeypatch, tmp_path
):
    profile = type(
        "Profile",
        (),
        {
            "log_output_dir": tmp_path / "logs",
            "capture_output_dir": tmp_path / "performance",
            "capture_template": "cpu",
        },
    )()
    monkeypatch.setattr(device_commands, "_profile_for_backend", lambda *args: profile)
    args = build_parser().parse_args(
        ["--platform", "ios", "performance", "stop", "--no-summarize", "--json"]
    )

    assert device_commands.dispatch_device_command(args) == 0
    calls = fake_backend["ios"].calls
    assert ("list_sessions", tmp_path / "logs") in calls
    stop_call = next(value for name, value in calls if name == "stop_performance")
    assert stop_call["no_summarize"] is True


def test_performance_start_rejects_attach_and_launch_together(
    fake_backend, tmp_path, capsys
):
    args = build_parser().parse_args(
        [
            "--platform",
            "ios",
            "performance",
            "start",
            "--profile",
            "cpu",
            "--udid",
            "one",
            "--attach",
            "Demo",
            "--launch",
            "com.example.demo",
            "--output-dir",
            str(tmp_path / "performance"),
        ]
    )

    assert device_commands.dispatch_device_command(args) == 1
    assert "不能同时使用" in capsys.readouterr().err
    assert not [
        call for call in fake_backend["ios"].calls if call[0] == "start_performance"
    ]


def test_capability_missing_fails_closed_without_legacy_fallback(monkeypatch, tmp_path, capsys):
    class NoLogBackend(FakeBackend):
        def capabilities(self):
            return Capabilities(platform=self.platform, device=True)

    backend = NoLogBackend("ios", tmp_path)
    monkeypatch.setattr(device_commands, "get_backend", lambda platform: backend)
    args = build_parser().parse_args(
        [
            "--platform",
            "ios",
            "stream",
            "",
            str(tmp_path),
            "--udid",
            "one",
        ]
    )
    assert device_commands.dispatch_device_command(args) == 1
    assert "log" in capsys.readouterr().err
    assert not [call for call in backend.calls if call[0] == "stream_logs"]


def test_invalid_backend_contract_is_reported_without_traceback(monkeypatch, capsys):
    class LegacyBackend:
        platform = "ios"

    monkeypatch.setattr(device_commands, "get_backend", lambda platform: LegacyBackend())
    args = build_parser().parse_args(["--platform", "ios", "list", "--json"])

    assert device_commands.dispatch_device_command(args) == 1
    assert "capabilities" in capsys.readouterr().err


def test_dispatch_source_has_no_private_platform_composition() -> None:
    source = inspect.getsource(device_commands)
    assert "from ..device" not in source
    assert "def cmd_list" not in source
    assert "def cmd_stream" not in source
    assert "def cmd_session" not in source
    assert "def cmd_capture" not in source
    assert "def cmd_archive" not in source

    handler_source = inspect.getsource(android_cli_handlers)
    assert "from .adb" not in handler_source
    assert "from .logger" not in handler_source
    assert "from .profiler" not in handler_source
    assert "def android_list" not in handler_source
    assert "def android_stream" not in handler_source
    assert "def android_session" not in handler_source
    assert "def android_capture" not in handler_source


def test_android_dispatch_never_routes_through_compat_handler(
    fake_backend, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        android_cli_handlers,
        "android_dispatch",
        lambda args: (_ for _ in ()).throw(AssertionError("legacy handler called")),
        raising=False,
    )
    args = build_parser().parse_args(["--platform", "android", "list", "--json"])
    assert device_commands.dispatch_device_command(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["serial"] == "one"


def test_archive_pull_requires_log_window_capability(
    monkeypatch, tmp_path, capsys
) -> None:
    class NoWindowBackend(FakeBackend):
        def capabilities(self):
            return Capabilities(
                platform=self.platform,
                device=True,
                log=True,
                archive=True,
                log_window=False,
            )

    backend = NoWindowBackend("android", tmp_path)
    monkeypatch.setattr(device_commands, "get_backend", lambda platform: backend)
    args = build_parser().parse_args(
        [
            "--platform",
            "android",
            "archive",
            "pull",
            "--device",
            "offline-device",
            "--since",
            "10:00",
            "--until",
            "10:01",
            "--json",
        ]
    )
    assert device_commands.dispatch_device_command(args) == 1
    assert "log_window" in capsys.readouterr().err
    assert not [call for call in backend.calls if call[0] == "fetch_log_window"]

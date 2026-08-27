"""Contract tests for the Android cross-platform backend (no real device)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracecite_mobile.platforms.base import RunResult
from tracecite_mobile.platforms.models import DeviceRef
from tracecite_mobile.platforms.android.backend import AndroidBackend
from tracecite_mobile.platforms.android import logger, profiler
from tracecite_mobile.device import archive


class _Proc:
    next_pid = 7000

    def __init__(self) -> None:
        self.pid = _Proc.next_pid
        _Proc.next_pid += 1


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _StreamProc:
    def __init__(self, lines) -> None:
        self.pid = 7100
        self.stdout = iter(lines)

    def poll(self):
        return 0

    def terminate(self):
        return None

    def wait(self, timeout=None):
        return 0


class _LogClient:
    def __init__(self, proc) -> None:
        self.proc = proc

    def spawn_logcat(self, *args, **kwargs):
        return self.proc


class _Runner:
    def __init__(self) -> None:
        self.perf_pids: list[int] = []
        self.commands: list[list[str]] = []

    def __call__(self, args, **kwargs):
        args = list(args)
        self.commands.append(args)
        if "devices" in args and "shell" not in args:
            return RunResult(0, "List of devices attached\nA\tdevice\nB\tdevice\n", "")
        if "getprop" in args:
            return RunResult(0, "Pixel", "")
        if "pidof" in args:
            target = args[-1]
            if target == "perfetto":
                return RunResult(0, " ".join(map(str, self.perf_pids)), "")
            return RunResult(0, "", "")
        if "perfetto" in args and "-d" in args:
            self.perf_pids = [4101]
            return RunResult(0, "started", "")
        if "kill" in args:
            try:
                pid = int(args[-1])
            except (TypeError, ValueError):
                pid = None
            if pid is not None:
                self.perf_pids = [item for item in self.perf_pids if item != pid]
            return RunResult(0, "", "")
        if "pull" in args:
            Path(args[-1]).write_bytes(b"trace")
            return RunResult(0, "pulled", "")
        return RunResult(0, "", "")


def _backend() -> tuple[AndroidBackend, _Runner]:
    runner = _Runner()
    return AndroidBackend(run=runner, adb_path="adb"), runner


def test_capabilities_and_environment_use_public_keys() -> None:
    backend, _ = _backend()
    caps = backend.capabilities()
    assert caps.multi_device_session
    assert caps.performance_profiles == ("startup", "frame", "memory", "network")
    assert caps.platform_options["automatic_rotation"] is True
    assert set(backend.probe_environment().checks) == {
        "device_bridge",
        "log_stream",
        "performance",
    }


def test_android_foreground_stream_rotates_only_after_30_minute_check(
    tmp_path, capsys
) -> None:
    """The Android adapter uses the shared time scheduler, not byte checks."""

    clock = _Clock()
    root = tmp_path
    hot = root / "android_live_Pixel.log"
    old = "07-31 10:00:00.000  100  100 I Demo: OLD\n"
    middle = "07-31 10:20:00.000  100  100 I Demo: MIDDLE\n"
    newest = "07-31 10:40:00.000  100  100 I Demo: NEWEST\n"

    def lines():
        yield old
        clock.advance(30 * 60 - 1)
        yield middle
        # A short check interval must not be bypassed by the next write.
        assert not (root / ".archive").exists()
        clock.advance(1)
        yield newest

    proc = _StreamProc(lines())
    client = _LogClient(proc)
    ref = DeviceRef("android", "SERIAL", "Pixel", model="Pixel")
    logger.stream_logs(
        client,
        ref,
        output_path=hot,
        also_stdout=False,
        hot_window_sec=30 * 60,
        archive_interval_sec=30 * 60,
        clock=clock,
    )

    captured = capsys.readouterr()
    assert "日志已保存" in captured.out
    hot_text = hot.read_text(encoding="utf-8")
    assert "OLD" not in hot_text
    assert "MIDDLE" in hot_text and "NEWEST" in hot_text
    listed = archive.list_archive_segments(root)
    assert listed["devices"]["Pixel"]["segment_count"] == 1
    assert ".archive" in listed["devices"]["Pixel"]["archive_dir"]


def test_android_writer_lifecycle_bridge_starts_and_closes_scheduler() -> None:
    events: list[str] = []

    class Writer:
        def start_scheduler(self) -> None:
            events.append("start")

        def close(self) -> None:
            events.append("close")

    writer = Writer()
    logger._start_writer_scheduler(writer)  # type: ignore[arg-type]
    logger._close_writer_scheduler(writer)  # type: ignore[arg-type]
    assert events == ["start", "close"]


def test_multi_device_sessions_have_canonical_state_and_safe_stop(tmp_path, monkeypatch) -> None:
    backend, _ = _backend()
    refs = backend.resolve_devices(all_devices=True, interactive=False)
    monkeypatch.setattr(logger, "_pid_alive", lambda pid: True)
    status = backend.start_sessions(
        refs,
        output_dir=tmp_path,
        popen=lambda *args, **kwargs: _Proc(),
    )
    assert status.session_count == 2
    state_path = tmp_path / ".tracecite-sessions.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert {row["serial"] for row in state["sessions"]} == {"A", "B"}
    listed = backend.list_sessions(output_dir=tmp_path)
    assert listed.session_count == 2
    stopped = backend.stop_sessions(output_dir=tmp_path, all_devices=True)
    assert stopped.state == "stopped"
    assert stopped.session_count == 2


def test_production_session_persists_hidden_rotation_contract(tmp_path, monkeypatch) -> None:
    backend, _ = _backend()
    ref = DeviceRef("android", "A", "Pixel", model="Pixel")
    collector = _Proc()
    monkeypatch.setattr(logger, "_spawn_rotating_collector", lambda *a, **k: collector)
    monkeypatch.setattr(logger, "_pid_alive", lambda pid: True)
    state = backend.start_session(
        ref,
        output_dir=tmp_path,
        hot_window_sec=1200,
        archive_interval_sec=1800,
    )
    assert state["collector_mode"] == "rotating-reader"
    assert state["collector_marker"] == logger._COLLECTOR_MARKER
    assert state["hot_window_sec"] == 1200
    assert state["archive_interval_sec"] == 1800.0
    assert state["archive_dir"].endswith("/.archive/Pixel")
    saved = json.loads((tmp_path / ".tracecite-session.json").read_text(encoding="utf-8"))
    assert saved["archive_dir"] == state["archive_dir"]


def test_multi_device_output_file_is_rejected(tmp_path) -> None:
    backend, _ = _backend()
    refs = backend.resolve_devices(all_devices=True, interactive=False)
    with pytest.raises(RuntimeError, match="output_file"):
        backend.start_sessions(refs, output_dir=tmp_path, output_file=tmp_path / "one.log")


def test_public_performance_profile_records_and_stops_only_its_pids(tmp_path, monkeypatch) -> None:
    backend, runner = _backend()
    device = DeviceRef("android", "A", "Pixel", model="Pixel")
    monkeypatch.setattr(profiler, "_STOP_WAIT", 0)
    session = backend.start_performance(device, profile="frame", output_dir=tmp_path)
    assert session.profile == "frame"
    assert backend.get_performance_status(output_dir=tmp_path).state == "running"
    expected_pid = runner.perf_pids[0]
    result = backend.stop_performance(output_dir=tmp_path)
    assert result.profile == "frame"
    kill_commands = [cmd for cmd in runner.commands if "kill" in cmd]
    assert kill_commands and all(str(expected_pid) in cmd for cmd in kill_commands)


def test_performance_start_excludes_preexisting_collector(tmp_path, monkeypatch) -> None:
    class Preexisting(_Runner):
        def __init__(self) -> None:
            super().__init__()
            self.perf_pids = [111]

        def __call__(self, args, **kwargs):
            if "perfetto" in args and "-d" in args:
                self.perf_pids.append(222)
                self.commands.append(list(args))
                return RunResult(0, "started", "")
            return super().__call__(args, **kwargs)

    runner = Preexisting()
    backend = AndroidBackend(run=runner, adb_path="adb")
    monkeypatch.setattr(profiler, "_STOP_WAIT", 0)
    device = DeviceRef("android", "A", "Pixel", model="Pixel")
    backend.start_performance(device, profile="frame", output_dir=tmp_path)
    backend.stop_performance(output_dir=tmp_path)
    kill_commands = [cmd for cmd in runner.commands if "kill" in cmd]
    assert kill_commands
    assert all("222" in cmd and "111" not in cmd for cmd in kill_commands)


def test_performance_remote_artifacts_are_session_scoped(tmp_path, monkeypatch) -> None:
    backend, runner = _backend()
    device = DeviceRef("android", "A", "Pixel", model="Pixel")
    observations = iter(([], [101], [101], [101, 202]))
    monkeypatch.setattr(profiler, "_perfetto_pids", lambda *args: next(observations))

    backend.start_performance(device, profile="frame", output_dir=tmp_path / "one")
    backend.start_performance(device, profile="frame", output_dir=tmp_path / "two")

    push_commands = [cmd for cmd in runner.commands if "push" in cmd]
    start_commands = [cmd for cmd in runner.commands if "perfetto" in cmd and "-d" in cmd]
    assert len(push_commands) == len(start_commands) == 2
    assert push_commands[0][-1] != push_commands[1][-1]
    first_trace = start_commands[0][start_commands[0].index("-o") + 1]
    second_trace = start_commands[1][start_commands[1].index("-o") + 1]
    assert first_trace != second_trace


def test_performance_start_pid_identity_failure_preserves_recovery_state(
    tmp_path, monkeypatch
) -> None:
    backend, _ = _backend()
    device = DeviceRef("android", "A", "Pixel", model="Pixel")
    observations = iter(([], None, None, None))
    monkeypatch.setattr(profiler, "_PID_OBSERVE_DELAY", 0)
    monkeypatch.setattr(profiler, "_perfetto_pids", lambda *args: next(observations))

    with pytest.raises(RuntimeError, match="已保留恢复状态"):
        backend.start_performance(device, profile="frame", output_dir=tmp_path)

    state = json.loads((tmp_path / ".tracecite-capture.json").read_text(encoding="utf-8"))
    assert state["phase"] == "recovery_required"
    assert state["recovery_required"] is True
    assert state["perfetto_pids"] == []
    assert state["remote_config"] and state["remote_trace"]


def test_performance_stop_pid_observation_failure_keeps_state(
    tmp_path, monkeypatch
) -> None:
    backend, _ = _backend()
    device = DeviceRef("android", "A", "Pixel", model="Pixel")
    start_observations = iter(([], [101]))
    monkeypatch.setattr(profiler, "_perfetto_pids", lambda *args: next(start_observations))
    backend.start_performance(device, profile="frame", output_dir=tmp_path)

    stop_observations = iter(([101], None))
    monkeypatch.setattr(profiler, "_perfetto_pids", lambda *args: next(stop_observations))
    monkeypatch.setattr(profiler, "_STOP_WAIT", 1)
    with pytest.raises(RuntimeError, match="无法核验 PID"):
        backend.stop_performance(output_dir=tmp_path)

    state = json.loads((tmp_path / ".tracecite-capture.json").read_text(encoding="utf-8"))
    assert state["phase"] == "recovery_required"
    assert state["recovery_required"] is True
    assert state["perfetto_pids"] == [101]


def test_performance_stop_timeout_keeps_live_collector_state(
    tmp_path, monkeypatch
) -> None:
    backend, runner = _backend()
    device = DeviceRef("android", "A", "Pixel", model="Pixel")
    monkeypatch.setattr(profiler, "_STOP_WAIT", 1)
    backend.start_performance(device, profile="frame", output_dir=tmp_path)

    class _Clock:
        values = iter((100.0, 100.0, 102.0))

        @staticmethod
        def time() -> float:
            return next(_Clock.values)

        @staticmethod
        def sleep(_: float) -> None:
            return None

    monkeypatch.setattr(profiler, "time", _Clock)
    monkeypatch.setattr(profiler, "_perfetto_pids", lambda *args: [4101])
    with pytest.raises(RuntimeError, match="仍存活"):
        backend.stop_performance(output_dir=tmp_path)

    state = json.loads((tmp_path / ".tracecite-capture.json").read_text(encoding="utf-8"))
    assert state["phase"] == "recovery_required"
    assert state["recovery_required"] is True
    assert state["perfetto_pids"] == [4101]
    assert not any("pull" in cmd for cmd in runner.commands)


def test_missing_identity_and_legacy_performance_pid_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logger, "_pid_alive", lambda pid: True)
    (tmp_path / ".tracecite-session.json").write_text(
        json.dumps({"serial": "A", "collector_pid": 333, "output_path": str(tmp_path / "a.log")}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="身份无法核验"):
        logger.stop_session(tmp_path)

    (tmp_path / ".tracecite-capture.json").write_text(
        json.dumps({"serial": "A", "template": "perfetto-frame"}),
        encoding="utf-8",
    )
    backend, _ = _backend()
    with pytest.raises(Exception, match="缺少采集 PID"):
        backend.stop_performance(output_dir=tmp_path)

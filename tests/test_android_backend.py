# -*- coding: utf-8 -*-
"""Android 后端测试：fake-adb runner，不依赖真实设备。

覆盖：adb 解析、设备选择、logcat 解析、session 状态机、Perfetto 状态机、知识库平台隔离。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracecite_mobile.platforms.base import RunResult
from tracecite_mobile.platforms.android.adb import (
    AdbNoDeviceError,
    AdbOfflineError,
    AdbUnauthorizedError,
    AdbDeviceNotFoundError,
    AndroidAdbClient,
)
from tracecite_mobile.platforms.android.devices import list_devices, resolve_device
from tracecite_mobile.platforms.android.logger import (
    parse_threadtime_line,
    start_session,
    get_session_status,
    stop_session,
    session_state_path,
)
from tracecite_mobile.platforms.android.profiler import (
    start_capture as perf_start,
    stop_capture as perf_stop,
    get_capture_status,
    resolve_config,
)
from tracecite_mobile.platforms.android.backend import AndroidBackend
from tracecite_mobile.platforms.models import DeviceRef


SAMPLE_DEVICES = (
    "List of devices attached\n"
    "ABCDEF0123\tdevice\n"
    "SERIAL2\t  unauthorized\n"
    "SERIAL3\t  offline\n"
)


class FakeProc:
    def __init__(self, pid: int = 9999):
        self.pid = pid
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self._alive = False

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0


class FakeAdb:
    def __init__(
        self,
        devices_text: str = SAMPLE_DEVICES,
        model: str = "Pixel 6",
        perfetto_pid: int = 12345,
        app_pid: int = 0,
    ) -> None:
        self.devices_text = devices_text
        self.model = model
        self.perfetto_pid = perfetto_pid
        self.perfetto_running = False
        self.app_pid = app_pid
        self.pulls: list[str] = []
        self.last_command: Optional[list[str]] = None

    def run(self, args, **kw):
        self.last_command = list(args)
        if args[0] != "adb":
            return RunResult(127, "", "not adb")
        # adb devices -l
        if "devices" in args and "shell" not in args:
            return RunResult(0, self.devices_text, "")
        # adb [-s X] shell getprop ro.product.model
        if "getprop" in args:
            return RunResult(0, self.model, "")
        # adb [-s X] shell pidof <pkg|perfetto>
        if "pidof" in args:
            target = args[-1]
            if target == "perfetto":
                return RunResult(
                    0, str(self.perfetto_pid) if self.perfetto_running else "", ""
                )
            if self.app_pid:
                return RunResult(0, str(self.app_pid), "")
            return RunResult(0, "", "")
        # perfetto -d
        if "perfetto" in args and "-d" in args:
            self.perfetto_running = True
            return RunResult(0, "started", "")
        # push / pull / kill / rm
        if "push" in args:
            return RunResult(0, "", "")
        if "pull" in args:
            local = args[-1]
            self.pulls.append(local)
            Path(local).write_bytes(b"PERFETTO_TRACE_BYTES")
            return RunResult(0, f"{local}: 1 file pulled.", "")
        if "kill" in args or "rm" in args:
            if "kill" in args:
                self.perfetto_running = False
            return RunResult(0, "", "")
        return RunResult(0, "", "")


def _backend(devices_text=SAMPLE_DEVICES, **kw) -> AndroidBackend:
    fake = FakeAdb(devices_text=devices_text, **kw)
    return AndroidBackend(run=fake.run, adb_path="adb")


# ---------------- adb 解析 ----------------
def test_parse_devices():
    devs = AndroidAdbClient.parse_devices(SAMPLE_DEVICES)
    assert [d.serial for d in devs] == ["ABCDEF0123", "SERIAL2", "SERIAL3"]
    assert devs[0].state == "device"
    assert devs[1].state == "unauthorized"
    assert devs[2].state == "offline"


def test_list_devices_fills_model():
    b = _backend()
    refs = b.list_devices()
    assert refs[0].identifier == "ABCDEF0123"
    assert refs[0].model == "Pixel 6"
    assert refs[0].state == "device"


def test_resolve_no_device():
    b = _backend(devices_text="List of devices attached\n")
    try:
        b.resolve_device(interactive=False)
        assert False, "should raise"
    except AdbNoDeviceError:
        pass


def test_resolve_unauthorized_offline():
    b = _backend()
    try:
        b.resolve_device(udid="SERIAL2", interactive=False)
        assert False
    except AdbUnauthorizedError:
        pass
    try:
        b.resolve_device(udid="SERIAL3", interactive=False)
        assert False
    except AdbOfflineError:
        pass


def test_resolve_by_serial_and_index():
    b = _backend()
    ref = b.resolve_device(udid="ABCDEF0123", interactive=False)
    assert ref.identifier == "ABCDEF0123"
    ref2 = b.resolve_device(index=1, interactive=False)
    assert ref2.identifier == "ABCDEF0123"


def test_resolve_multi_picks_first_when_one():
    # 多台设备但仅一台可用（device），自动选它
    txt = "List of devices attached\nA1\tdevice\nA2\tunauthorized\n"
    b = _backend(devices_text=txt)
    ref = b.resolve_device(interactive=False)
    assert ref.identifier == "A1"


def test_resolve_multi_requires_choice():
    txt = "List of devices attached\nA1\tdevice\nA2\tdevice\n"
    b = _backend(devices_text=txt)
    try:
        b.resolve_device(interactive=False)
        assert False
    except AdbDeviceNotFoundError:
        pass


# ---------------- logcat 解析 ----------------
def test_parse_threadtime_valid():
    line = "07-25 18:42:00.123  1234  1234 D Tag: hello world\n"
    rec = parse_threadtime_line(line)
    assert rec["unparsed"] is False
    assert rec["pid"] == 1234
    assert rec["tid"] == 1234
    assert rec["priority"] == "D"
    assert rec["tag"] == "Tag"
    assert rec["message"] == "hello world"


def test_parse_threadtime_unparsed_kept():
    rec = parse_threadtime_line("this is not a logcat line\n")
    assert rec["unparsed"] is True
    assert rec["raw_line"] == "this is not a logcat line"


# ---------------- session 状态机 ----------------
def test_session_start_status_stop(tmp_path, monkeypatch):
    from tracecite_mobile.platforms.android import logger as android_logger

    # 测试中用 FakeProc，没有真实 OS 进程；mock 存活检查以模拟“采集进行中”
    monkeypatch.setattr(android_logger, "_pid_alive", lambda pid: True)
    b = _backend()
    ref = DeviceRef(platform="android", identifier="ABCDEF0123", name="Pixel", model="Pixel 6")
    state = b.start_session(
        ref, package="com.example.app", output_dir=tmp_path, popen=lambda *a, **k: FakeProc()
    )
    assert state["platform"] == "android"
    assert state["serial"] == "ABCDEF0123"
    assert (tmp_path / ".tracecite-session.json").is_file()
    status = b.get_session_status(output_dir=tmp_path)
    assert status["active"] is True
    stopped = b.stop_session(output_dir=tmp_path)
    assert stopped["serial"] == "ABCDEF0123"
    assert not session_state_path(tmp_path).is_file()


def test_corrupt_session_state_fails_closed(tmp_path):
    path = session_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="状态文件不可读"):
        get_session_status(tmp_path)


# ---------------- Perfetto 状态机 ----------------
def test_perfetto_start_status_stop(tmp_path):
    b = _backend()
    ref = DeviceRef(platform="android", identifier="ABCDEF0123", name="Pixel", model="Pixel 6")
    state = b.start_capture(ref, template="perfetto-frame", output_dir=tmp_path)
    assert state["template"] == "perfetto-frame"
    st = b.get_capture_status(output_dir=tmp_path)
    assert st["active"] is True
    result = b.stop_capture(output_dir=tmp_path)
    assert result.trace_path.is_file()
    assert result.trace_path.stat().st_size > 0
    assert result.metadata_path is not None and result.metadata_path.is_file()


def test_perfetto_unknown_template(tmp_path):
    b = _backend()
    ref = DeviceRef(platform="android", identifier="X", name="n", model="m")
    try:
        b.start_capture(ref, template="nope", output_dir=tmp_path)
        assert False
    except Exception:
        pass


def test_resolve_config_known():
    p = resolve_config("perfetto-memory")
    assert p.is_file()


# ---------------- 知识库平台隔离 ----------------
def test_knowledge_platform_isolation(tmp_path):
    from tracecite_mobile.analysis import knowledge as K

    # 写入 android 场景
    K.ensure_scenario(
        "android-profile-reload",
        title="t",
        start_dir=tmp_path,
        platform="android",
    )
    K.ensure_scenario(
        "ios-thing",
        title="t",
        start_dir=tmp_path,
        platform="ios",
    )
    know = K.load_project_knowledge(tmp_path, platform="android")
    # android 平台解析只合并 android 场景
    terms = know.effective_filter_terms(
        "android-anr", scenario="android-profile-reload", platform="android"
    )
    # 场景可能无词，至少不报错；ios 场景在 android 下应被排除
    ios_terms = know.effective_filter_terms(
        "android-anr", scenario="ios-thing", platform="android"
    )
    assert ios_terms == []  # ios 场景不污染 android


def test_android_starter_seeds(tmp_path):
    from tracecite_mobile.analysis import knowledge as K

    res = K.ensure_default_project_knowledge(tmp_path, platform="android")
    assert res["created"]
    know = K.load_project_knowledge(tmp_path, platform="android")
    assert know.knowledge_schema_version >= 2
    assert "android-anr" in know.filter_terms
    assert know.scenarios == {}


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

# -*- coding: utf-8 -*-
"""多设备 session + archive rewind/pull + 多文件 filter 单元测试（不依赖真机）。"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tracecite_mobile.device import archive, session
from tracecite_mobile.device.devices import Device, DeviceError, resolve_devices
from tracecite_core.text_filter import filter_text, filter_texts


def _line(ts: str, msg: str, pid: int = 100) -> str:
    return f"{ts} DemoApp(com.example.demo.logging)[{pid}] <Notice>: {msg}\n"


def _hot_log_text() -> str:
    # 跨约 40 分钟：前段应被 rewind，后 30 分钟留 hot
    lines = [
        _line("Jul 31 10:00:00", "OLD_A"),
        _line("Jul 31 10:05:00", "OLD_B"),
        _line("Jul 31 10:20:00", "MID"),
        _line("Jul 31 10:35:00", "HOT_A"),
        _line("Jul 31 10:39:00", "HOT_B"),
    ]
    return "".join(lines)


class _FakeClock:
    """Small monotonic clock substitute for interval scheduling tests."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _session(root: Path, *, udid: str, name: str, pid: int) -> session.StreamSession:
    (root / f"ios_live_{name}.log").write_text("", encoding="utf-8")
    (root / f"ios_live_{name}_session.log").write_text("collector\n", encoding="utf-8")
    return session.StreamSession(
        pid=pid,
        device_name=name,
        device_udid=udid,
        device_model="iPhone",
        process_name="DemoApp",
        subsystem="com.example.demo.logging",
        output_path=str(root / f"ios_live_{name}.log"),
        log_output_dir=str(root),
        capture_output_dir=str(root),
        stream_log_path=str(root / f"ios_live_{name}_session.log"),
        started_at="2026-07-31T10:00:00",
        profile_path=None,
        hot_window_sec=1800,
        archive_dir=str(archive.archive_device_dir(root, name)),
    )


class MultiSessionTest(unittest.TestCase):
    def test_save_load_multi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _session(root, udid="u1", name="PhoneA", pid=11)
            b = _session(root, udid="u2", name="PhoneB", pid=22)
            session.save_all_sessions(root, {"u1": a, "u2": b})
            loaded = session.load_all_sessions(root)
            self.assertEqual(set(loaded), {"u1", "u2"})
            self.assertEqual(loaded["u1"].device_name, "PhoneA")
            status = session.get_stream_session_status(root)
            self.assertEqual(status["session_count"], 2)
            self.assertEqual(len(status["sessions"]), 2)
            self.assertNotIn("session", status)

    def test_stop_requires_udid_when_multiple(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session.save_all_sessions(
                root,
                {
                    "u1": _session(root, udid="u1", name="A", pid=1),
                    "u2": _session(root, udid="u2", name="B", pid=2),
                },
            )
            with mock.patch.object(session, "_session_process_alive", return_value=False):
                with mock.patch.object(session, "_pid_alive", return_value=False):
                    with self.assertRaisesRegex(session.SessionError, "多个"):
                        session.stop_stream_sessions(root)
                    stopped = session.stop_stream_sessions(root, udid="u1")
                    self.assertEqual(len(stopped), 1)
                    self.assertEqual(stopped[0].device_udid, "u1")
                    left = session.load_all_sessions(root)
                    self.assertEqual(set(left), {"u2"})


class ResolveDevicesTest(unittest.TestCase):
    def test_all_and_indices(self) -> None:
        fake = [
            Device(name="A", udid="ua", model="m"),
            Device(name="B", udid="ub", model="m"),
        ]
        with mock.patch(
            "tracecite_mobile.device.devices.list_connected_devices", return_value=fake
        ):
            all_devs = resolve_devices(all_devices=True, interactive=False)
            self.assertEqual([d.udid for d in all_devs], ["ua", "ub"])
            picked = resolve_devices(indices=[2, 1], interactive=False)
            self.assertEqual([d.udid for d in picked], ["ub", "ua"])
            with self.assertRaises(DeviceError):
                resolve_devices(interactive=False)


class ArchiveRotatePullTest(unittest.TestCase):
    def test_rotate_keeps_last_30_min(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            hot.write_text(_hot_log_text(), encoding="utf-8")
            result = archive.rotate_hot_log(
                hot, device_name="PhoneA", hot_window_sec=30 * 60
            )
            self.assertTrue(result.rotated)
            self.assertEqual(len(result.archived), 1)
            hot_text = hot.read_text(encoding="utf-8")
            self.assertIn("HOT_A", hot_text)
            self.assertIn("HOT_B", hot_text)
            self.assertNotIn("OLD_A", hot_text)
            self.assertNotIn("OLD_B", hot_text)
            # 10:20 距 10:39 = 19min，应仍在 hot
            self.assertIn("MID", hot_text)
            archived = Path(result.archived[0].path).read_text(encoding="utf-8")
            self.assertIn("OLD_A", archived)
            self.assertIn("OLD_B", archived)
            manifest = archive.load_manifest(archive.archive_device_dir(root, "PhoneA"))
            self.assertEqual(len(manifest), 1)

    def test_rotate_noop_within_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            hot.write_text(
                _line("Jul 31 10:30:00", "A") + _line("Jul 31 10:35:00", "B"),
                encoding="utf-8",
            )
            result = archive.rotate_hot_log(
                hot, device_name="PhoneA", hot_window_sec=30 * 60
            )
            self.assertFalse(result.rotated)
            self.assertEqual(result.archived, [])

    def test_pull_joins_archive_and_hot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            hot.write_text(_hot_log_text(), encoding="utf-8")
            archive.rotate_hot_log(hot, device_name="PhoneA", hot_window_sec=30 * 60)
            pulled = archive.pull_archive_window(
                root,
                device_name="PhoneA",
                since="10:00:00",
                until="10:40:00",
                hot_path=hot,
            )
            text = pulled.output_path.read_text(encoding="utf-8")
            self.assertIn("OLD_A", text)
            self.assertIn("HOT_B", text)
            self.assertGreaterEqual(len(pulled.segments), 2)

    def test_list_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            hot.write_text(_hot_log_text(), encoding="utf-8")
            archive.rotate_hot_log(hot, device_name="PhoneA", hot_window_sec=30 * 60)
            listed = archive.list_archive_segments(root, device_name="PhoneA")
            self.assertIn("PhoneA", listed["devices"])
            self.assertEqual(listed["devices"]["PhoneA"]["segment_count"], 1)
            self.assertIn("/.archive/", listed["devices"]["PhoneA"]["archive_dir"])

    def test_legacy_visible_archive_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / "archive" / "PhoneA"
            legacy_dir.mkdir(parents=True)
            segment_path = legacy_dir / "20260731_100000-20260731_100030.log"
            segment_text = _line("Jul 31 10:00:00", "LEGACY")
            segment_path.write_text(segment_text, encoding="utf-8")
            archive.save_manifest(
                legacy_dir,
                [
                    archive.ArchiveSegment(
                        start="2026-07-31T10:00:00",
                        end="2026-07-31T10:00:30",
                        path=str(segment_path),
                        bytes=len(segment_text.encode("utf-8")),
                        lines=1,
                    )
                ],
            )

            listed = archive.list_archive_segments(root, device_name="PhoneA")
            self.assertEqual(listed["devices"]["PhoneA"]["segment_count"], 1)
            self.assertEqual(
                listed["devices"]["PhoneA"]["archive_dir"], str(legacy_dir.resolve())
            )
            pulled = archive.pull_archive_window(
                root,
                device_name="PhoneA",
                since="10:00:00",
                until="10:01:00",
            )
            self.assertIn("LEGACY", pulled.output_path.read_text(encoding="utf-8"))
            self.assertIn("/.archive/pulled/", str(pulled.output_path))

    def test_canonical_and_legacy_duplicate_segment_is_not_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / "archive" / "PhoneA"
            legacy_dir.mkdir(parents=True)
            legacy_path = legacy_dir / "segment.log"
            segment_text = _line("Jul 31 10:00:00", "DUPLICATE")
            legacy_path.write_text(segment_text, encoding="utf-8")
            segment = archive.ArchiveSegment(
                start="2026-07-31T10:00:00",
                end="2026-07-31T10:00:30",
                path=str(legacy_path),
                bytes=len(segment_text.encode("utf-8")),
                lines=1,
            )
            archive.save_manifest(legacy_dir, [segment])

            canonical_dir = archive.archive_device_dir(root, "PhoneA")
            canonical_dir.mkdir(parents=True)
            canonical_path = canonical_dir / "segment.log"
            canonical_path.write_text(segment_text, encoding="utf-8")
            archive.save_manifest(
                canonical_dir,
                [
                    archive.ArchiveSegment(
                        **{**segment.__dict__, "path": str(canonical_path)}
                    )
                ],
            )

            listed = archive.list_archive_segments(root, device_name="PhoneA")
            self.assertEqual(listed["devices"]["PhoneA"]["segment_count"], 1)
            self.assertEqual(
                listed["devices"]["PhoneA"]["segments"][0]["path"],
                str(canonical_path),
            )


class MultiFilterTest(unittest.TestCase):
    def test_filter_two_devices_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "ios_live_PhoneA.log"
            b = root / "ios_live_PhoneB.log"
            a.write_text(
                _line("Jul 31 10:01:00", "task.started A1")
                + _line("Jul 31 10:02:00", "noise"),
                encoding="utf-8",
            )
            b.write_text(
                _line("Jul 31 10:01:30", "task.started B1")
                + _line("Jul 31 10:03:00", "task.started B2"),
                encoding="utf-8",
            )
            multi = filter_texts(
                [a, b],
                pattern=r"task\.started",
                tag="ub",
                merge_timeline=True,
                source_labels=["PhoneA", "PhoneB"],
            )
            self.assertEqual(multi.match_records, 3)
            self.assertEqual(len(multi.sources), 2)
            self.assertIsNotNone(multi.merged_timeline_path)
            merged = multi.merged_timeline_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
            self.assertIn("[PhoneA]", merged)
            self.assertIn("[PhoneB]", merged)
            # A1 应在 B1 前
            self.assertLess(merged.find("A1"), merged.find("B1"))

    def test_single_filter_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "ios_live_PhoneA.log"
            src.write_text(_line("Jul 31 10:01:00", "task.started X"), encoding="utf-8")
            result = filter_text(src, pattern=r"task\.started", tag="t")
            self.assertEqual(result.match_records, 1)


class HotWriterRotateTest(unittest.TestCase):
    def test_scheduler_checks_after_interval_without_new_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            hot.write_text(_hot_log_text(), encoding="utf-8")
            clock = _FakeClock()
            rotated = threading.Event()
            waits = 0

            def fake_wait(interval: float) -> bool:
                nonlocal waits
                waits += 1
                if waits == 1:
                    clock.advance(interval)
                    return False
                return True

            with hot.open("a+", encoding="utf-8") as fp:
                writer = archive.HotRotatingWriter(
                    fp,
                    hot_path=hot,
                    device_name="PhoneA",
                    archive_interval_sec=30 * 60,
                    clock=clock,
                    scheduler_wait=fake_wait,
                )
                with mock.patch.object(
                    archive, "rotate_hot_log", side_effect=lambda *a, **k: rotated.set()
                ) as rotate:
                    writer.start_scheduler()
                    self.assertTrue(rotated.wait(timeout=1))
                    writer.close()
                    self.assertEqual(rotate.call_count, 1)

    def test_writer_triggers_rotate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            clock = _FakeClock()
            with hot.open("w", encoding="utf-8") as fp:
                writer = archive.HotRotatingWriter(
                    fp,
                    hot_path=hot,
                    device_name="PhoneA",
                    hot_window_sec=30 * 60,
                    archive_interval_sec=30 * 60,
                    check_bytes=10,  # 兼容参数不再触发短间隔检查
                    clock=clock,
                )
                writer.write(_hot_log_text())
                # The old byte threshold would rotate here. The first check
                # is now time based and must wait for the 30-minute interval.
                self.assertEqual(archive.list_archive_segments(root)["devices"], {})
                clock.advance(30 * 60)
                writer.write(_line("Jul 31 10:40:00", "NEW"))
            text = hot.read_text(encoding="utf-8")
            self.assertIn("HOT_B", text)
            self.assertNotIn("OLD_A", text)
            listed = archive.list_archive_segments(root)
            self.assertEqual(listed["devices"]["PhoneA"]["segment_count"], 1)

    def test_interval_resets_after_successful_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            clock = _FakeClock()
            with hot.open("w", encoding="utf-8") as fp:
                writer = archive.HotRotatingWriter(
                    fp,
                    hot_path=hot,
                    device_name="PhoneA",
                    archive_interval_sec=30 * 60,
                    clock=clock,
                )
                with mock.patch.object(
                    archive, "rotate_hot_log", wraps=archive.rotate_hot_log
                ) as rotate:
                    writer.write(_line("Jul 31 10:00:00", "A"))
                    clock.advance(30 * 60 - 1)
                    writer.write(_line("Jul 31 10:40:00", "B"))
                    self.assertEqual(rotate.call_count, 0)
                    clock.advance(1)
                    writer.write(_line("Jul 31 10:41:00", "C"))
                    self.assertEqual(rotate.call_count, 1)
                    # A check at the same clock value does not repeat.
                    writer.write(_line("Jul 31 10:42:00", "D"))
                    self.assertEqual(rotate.call_count, 1)
                    clock.advance(30 * 60 - 1)
                    writer.write(_line("Jul 31 11:10:00", "E"))
                    self.assertEqual(rotate.call_count, 1)
                    clock.advance(1)
                    writer.write(_line("Jul 31 11:11:00", "F"))
                    self.assertEqual(rotate.call_count, 2)

    def test_failed_check_retries_on_next_interval_not_next_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            clock = _FakeClock()
            attempts = []

            def fail_once(*args: object, **kwargs: object) -> archive.RotateResult:
                attempts.append(clock.value)
                if len(attempts) == 1:
                    raise RuntimeError("temporary archive failure")
                return archive.RotateResult(
                    rotated=False,
                    cutoff=None,
                    last_ts=None,
                    archived=[],
                    hot_path=str(hot),
                    hot_lines=1,
                    hot_bytes=1,
                )

            with hot.open("w", encoding="utf-8") as fp:
                writer = archive.HotRotatingWriter(
                    fp,
                    hot_path=hot,
                    device_name="PhoneA",
                    archive_interval_sec=30 * 60,
                    clock=clock,
                )
                with mock.patch.object(archive, "rotate_hot_log", side_effect=fail_once):
                    writer.write(_line("Jul 31 10:00:00", "A"))
                    clock.advance(30 * 60)
                    writer.write(_line("Jul 31 10:40:00", "B"))
                    writer.write(_line("Jul 31 10:40:01", "C"))
                    self.assertEqual(attempts, [30 * 60])
                    clock.advance(30 * 60 - 1)
                    writer.write(_line("Jul 31 11:10:00", "D"))
                    self.assertEqual(attempts, [30 * 60])
                    clock.advance(1)
                    writer.write(_line("Jul 31 11:10:01", "E"))
                    self.assertEqual(attempts, [30 * 60, 60 * 60])

    def test_flush_and_empty_write_do_not_force_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            clock = _FakeClock()
            with hot.open("w", encoding="utf-8") as fp:
                writer = archive.HotRotatingWriter(
                    fp,
                    hot_path=hot,
                    device_name="PhoneA",
                    archive_interval_sec=30 * 60,
                    clock=clock,
                )
                with mock.patch.object(archive, "rotate_hot_log") as rotate:
                    clock.advance(30 * 60)
                    writer.flush()
                    writer.write("")
                    self.assertEqual(rotate.call_count, 0)


if __name__ == "__main__":
    unittest.main()

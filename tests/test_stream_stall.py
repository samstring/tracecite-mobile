# -*- coding: utf-8 -*-
"""idevicesyslog stall 检测与 session 假存活判定。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tracecite_mobile.device import session
from tracecite_mobile.device.stream import (
    StallDetectingReader,
    StallError,
    stream_heartbeat_path,
)


class StallDetectingReaderTest(unittest.TestCase):
    def test_readline_returns_line(self) -> None:
        r, w = os.pipe()
        try:
            os.write(w, b"hello\n")
            with open(r, "rb", buffering=0) as pipe:
                reader = StallDetectingReader(pipe, stall_sec=2)
                self.assertEqual(reader.readline(), b"hello\n")
                r = -1
        finally:
            os.close(w)
            if r >= 0:
                try:
                    os.close(r)
                except OSError:
                    pass

    def test_stall_raises(self) -> None:
        r, w = os.pipe()
        try:
            with open(r, "rb", buffering=0) as pipe:
                reader = StallDetectingReader(pipe, stall_sec=0.2)
                with self.assertRaises(StallError):
                    reader.readline()
                r = -1
        finally:
            os.close(w)
            if r >= 0:
                try:
                    os.close(r)
                except OSError:
                    pass

    def test_activity_callback(self) -> None:
        r, w = os.pipe()
        hits: list[int] = []
        try:
            os.write(w, b"a\n")
            with open(r, "rb", buffering=0) as pipe:
                reader = StallDetectingReader(
                    pipe,
                    stall_sec=2,
                    on_activity=lambda: hits.append(1),
                )
                reader.readline()
                r = -1
            self.assertTrue(hits)
        finally:
            os.close(w)
            if r >= 0:
                try:
                    os.close(r)
                except OSError:
                    pass


class SessionStallStatusTest(unittest.TestCase):
    def _sess(self, root: Path, *, started_at: str) -> session.StreamSession:
        out = root / "ios_live_Phone.log"
        out.write_text("", encoding="utf-8")
        return session.StreamSession(
            pid=4242,
            device_name="Phone",
            device_udid="udid-1",
            device_model="iPhone",
            process_name="DemoApp",
            subsystem="com.example.demo.logging",
            output_path=str(out),
            log_output_dir=str(root),
            capture_output_dir=str(root),
            stream_log_path=str(root / "session.log"),
            started_at=started_at,
            profile_path=None,
        )

    def test_status_marks_stalled_when_heartbeat_missing_and_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess = self._sess(root, started_at="2020-01-01T00:00:00")
            session.save_stream_session(root, sess)
            with mock.patch.object(session, "_session_process_alive", return_value=True):
                status = session.get_stream_session_status(root)
            item = status["sessions"][0]
            self.assertTrue(item["process_alive"])
            self.assertTrue(item["stalled"])
            self.assertFalse(item["alive"])
            self.assertFalse(item["healthy"])

    def test_fresh_heartbeat_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess = self._sess(root, started_at="2020-01-01T00:00:00")
            hb = stream_heartbeat_path(Path(sess.output_path))
            hb.touch()
            session.save_stream_session(root, sess)
            with mock.patch.object(session, "_session_process_alive", return_value=True):
                status = session.get_stream_session_status(root)
            item = status["sessions"][0]
            self.assertFalse(item["stalled"])
            self.assertTrue(item["alive"])
            self.assertTrue(item["healthy"])

    def test_start_auto_recovers_stalled_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess = self._sess(root, started_at="2020-01-01T00:00:00")
            session.save_stream_session(root, sess)

            fake_device = mock.Mock()
            fake_device.name = "Phone"
            fake_device.udid = "udid-1"
            fake_device.model = "iPhone"

            fake_profile = mock.Mock()
            fake_profile.process_name = "DemoApp"
            fake_profile.subsystem = "com.example.demo.logging"
            fake_profile.log_output_dir = root
            fake_profile.capture_output_dir = root
            fake_profile.source_path = None

            fake_proc = mock.Mock()
            fake_proc.pid = 9999

            with mock.patch.object(session, "_session_process_alive", return_value=True):
                with mock.patch.object(session, "_session_stream_stalled", return_value=True):
                    with mock.patch.object(session, "ensure_dependencies"):
                        with mock.patch.object(session.os, "killpg"):
                            with mock.patch.object(
                                session.os, "waitpid", side_effect=ChildProcessError
                            ):
                                with mock.patch.object(
                                    session.subprocess, "Popen", return_value=fake_proc
                                ):
                                    started = session.start_stream_session(
                                        fake_device, fake_profile
                                    )
            self.assertEqual(started.pid, 9999)
            loaded = session.load_all_sessions(root)
            self.assertEqual(loaded["udid-1"].pid, 9999)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""状态文件原子写入与 PID 复用保护。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tracecite_mobile.device import session
from tracecite_core.state_file import atomic_write_json, read_json


class StateFileTest(unittest.TestCase):
    def test_atomic_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            atomic_write_json(path, {"pid": 123, "name": "设备"})
            self.assertEqual(read_json(path), {"pid": 123, "name": "设备"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_session_stop_rejects_reused_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = session.StreamSession(
                pid=123,
                device_name="iPhone",
                device_udid="udid",
                device_model="model",
                process_name="App",
                subsystem="all",
                output_path=str(root / "app.log"),
                log_output_dir=str(root),
                capture_output_dir=str(root),
                stream_log_path=str(root / "session.log"),
                started_at="2026-07-30T00:00:00",
                profile_path=None,
            )
            session.save_stream_session(root, saved)

            with mock.patch.object(session, "_pid_alive", return_value=True):
                with mock.patch.object(
                    session,
                    "_session_process_alive",
                    return_value=False,
                ):
                    with mock.patch.object(session.os, "killpg") as kill:
                        with self.assertRaisesRegex(session.SessionError, "PID 123"):
                            session.stop_stream_session(root)
            kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()

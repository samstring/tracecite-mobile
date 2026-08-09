# -*- coding: utf-8 -*-
"""capture stop 指向真实 stream 日志，session status 不误报 capture 存活。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tracecite_mobile.device import capture, session


def _capture_session(root: Path, pid: int = 4321) -> capture.CaptureSession:
    return capture.CaptureSession(
        pid=pid,
        trace_path=str(root / "trace.trace"),
        toc_path=str(root / "trace_toc.xml"),
        device_udid="udid",
        device_name="iPhone",
        output_dir=str(root),
        template="Time Profiler",
        attach="App",
        launch=None,
        started_at="2026-07-31T00:00:00",
        no_summarize=True,
        xctrace_log=str(root / "trace_xctrace.log"),
    )


def _stream_session(root: Path, output_path: Path) -> session.StreamSession:
    return session.StreamSession(
        pid=1234,
        device_name="iPhone",
        device_udid="udid",
        device_model="model",
        process_name="App",
        subsystem="all",
        output_path=str(output_path),
        log_output_dir=str(root),
        capture_output_dir=str(root),
        stream_log_path=str(root / "session.log"),
        started_at="2026-07-31T00:00:00",
        profile_path=None,
    )


class CaptureStopLogPathTest(unittest.TestCase):
    def test_stop_uses_explicit_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture.save_capture_session(root, _capture_session(root))
            custom_log = root / "custom_20260731.log"

            with mock.patch.object(capture, "ensure_xctrace"):
                with mock.patch.object(capture, "_pid_alive", return_value=False):
                    with mock.patch.object(capture, "_reap_process"):
                        with mock.patch.object(capture, "_wait_for_xctrace_save"):
                            with mock.patch.object(capture, "_ensure_trace_ready"):
                                with mock.patch.object(
                                    capture,
                                    "print_capture_summary",
                                    return_value=None,
                                ) as summary:
                                    result = capture.stop_capture(
                                        root,
                                        summarize=False,
                                        quiet=True,
                                        log_path=custom_log,
                                    )

            self.assertEqual(result.log_path, custom_log)
            self.assertEqual(summary.call_args[0][2], custom_log)

    def test_stop_without_session_reports_no_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture.save_capture_session(root, _capture_session(root))

            with mock.patch.object(capture, "ensure_xctrace"):
                with mock.patch.object(capture, "_pid_alive", return_value=False):
                    with mock.patch.object(capture, "_reap_process"):
                        with mock.patch.object(capture, "_wait_for_xctrace_save"):
                            with mock.patch.object(capture, "_ensure_trace_ready"):
                                with mock.patch.object(
                                    capture,
                                    "print_capture_summary",
                                    return_value=None,
                                ):
                                    result = capture.stop_capture(
                                        root,
                                        summarize=False,
                                        quiet=True,
                                    )

            # 猜一个默认命名比明确留空更容易误导联合分析
            self.assertIsNone(result.log_path)
            self.assertIsNone(result.to_dict()["log_path"])

    def test_status_capture_alive_checks_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_path = root / "app.log"
            session.save_stream_session(root, _stream_session(root, output_path))
            # 本用例验证 PID 命令行归属，不验证 stall；提供新鲜 heartbeat，
            # 避免健康检查因旧 started_at + 无心跳而正确判为 stalled。
            session.stream_heartbeat_path(output_path).touch()
            capture.save_capture_session(root, _capture_session(root))

            with mock.patch.object(session, "_pid_alive", return_value=True):
                with mock.patch.object(capture, "_pid_alive", return_value=True):
                    with mock.patch.object(
                        capture,
                        "process_command_contains",
                        return_value=False,
                    ):
                        with mock.patch.object(
                            session,
                            "process_command_contains",
                            return_value=True,
                        ):
                            status = session.get_stream_session_status(root)

            self.assertTrue(status["active"])
            self.assertFalse(status["capture"]["alive"])


if __name__ == "__main__":
    unittest.main()

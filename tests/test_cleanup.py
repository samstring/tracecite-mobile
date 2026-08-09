# -*- coding: utf-8 -*-
"""历史分析产物清理测试。"""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tracecite_mobile.device.cleanup import clean_analysis_artifacts, parse_before
from tracecite_mobile.device.session import StreamSession, save_stream_session


class CleanupTest(unittest.TestCase):
    def test_pinned_nested_run_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "Log"
            capture_dir = root / "Instrument"
            analysis_dir = root / "analysis"
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            project = analysis_dir / "project-a"
            run_dir = project / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"retention": {"pinned": True}}), encoding="utf-8"
            )
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            os.utime(project, (old_ts, old_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )

            self.assertNotIn(project, {item.path for item in result.items})
            self.assertTrue(run_dir.exists())

    def test_parse_before_today_uses_start_of_day(self) -> None:
        now = datetime(2026, 6, 26, 16, 30).astimezone()
        cutoff = parse_before("today", now=now)
        self.assertEqual(cutoff.hour, 0)
        self.assertEqual(cutoff.minute, 0)
        self.assertEqual(cutoff.date(), now.date())

    def test_clean_analysis_artifacts_removes_only_before_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "Log"
            capture_dir = root / "Instrument"
            analysis_dir = root / "analysis"
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()

            old_log = log_dir / "old.log"
            old_hidden_filter = log_dir / ".filtered"
            old_trace = capture_dir / "old.trace"
            new_log = log_dir / "today.log"
            old_package = analysis_dir / "export_20260625T120000.zip"

            old_log.write_text("old\n", encoding="utf-8")
            old_hidden_filter.mkdir()
            (old_hidden_filter / "filtered.log").write_text("old filtered\n", encoding="utf-8")
            old_trace.mkdir()
            (old_trace / "data.txt").write_text("trace\n", encoding="utf-8")
            old_package.write_text("zip\n", encoding="utf-8")
            new_log.write_text("new\n", encoding="utf-8")

            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            new_ts = datetime(2026, 6, 26, 9, 0).timestamp()
            for path in (old_log, old_hidden_filter, old_trace, old_package):
                os.utime(path, (old_ts, old_ts))
            os.utime(new_log, (new_ts, new_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )

            deleted = {item.path.name for item in result.items}
            self.assertEqual(deleted, {"old.log", ".filtered", "old.trace", "export_20260625T120000.zip"})
            self.assertFalse(old_log.exists())
            self.assertFalse(old_hidden_filter.exists())
            self.assertFalse(old_trace.exists())
            self.assertFalse(old_package.exists())
            self.assertTrue(new_log.exists())

    def test_active_session_log_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "Log"
            capture_dir = root / "Instrument"
            analysis_dir = root / "analysis"
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()

            active_log = log_dir / "active.log"
            active_log.write_text("streaming\n", encoding="utf-8")
            stale_log = log_dir / "stale.log"
            stale_log.write_text("old\n", encoding="utf-8")
            save_stream_session(
                log_dir,
                StreamSession(
                    pid=1234,
                    device_name="iPhone",
                    device_udid="udid",
                    device_model="model",
                    process_name="App",
                    subsystem="all",
                    output_path=str(active_log),
                    log_output_dir=str(log_dir),
                    capture_output_dir=str(capture_dir),
                    stream_log_path=str(log_dir / "active_session.log"),
                    started_at="2026-06-25T12:00:00",
                    profile_path=None,
                ),
            )

            # 长跑 session：日志 mtime 落在 cutoff 之前
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            for path in (active_log, stale_log):
                os.utime(path, (old_ts, old_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )

            deleted = {item.path.name for item in result.items}
            self.assertIn("stale.log", deleted)
            self.assertNotIn("active.log", deleted)
            self.assertTrue(active_log.exists())


if __name__ == "__main__":
    unittest.main()

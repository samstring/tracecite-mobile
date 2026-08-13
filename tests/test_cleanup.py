# -*- coding: utf-8 -*-
"""历史分析产物清理测试。"""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tracecite_mobile.device.cleanup import CleanupError, clean_analysis_artifacts, parse_before
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

    def test_canonical_state_lock_and_active_sidecars_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            active = log_dir / "active.log"
            active.write_text("streaming\n", encoding="utf-8")
            collector = log_dir / "active_session.log"
            collector.write_text("collector\n", encoding="utf-8")
            heartbeat = active.with_name(active.name + ".heartbeat")
            rotate_tmp = active.with_name(f".{active.name}.rotate.tmp")
            heartbeat.write_text("heartbeat\n", encoding="utf-8")
            rotate_tmp.write_text("partial\n", encoding="utf-8")
            state = log_dir / ".tracecite-sessions.json"
            state.write_text(
                json.dumps(
                    {
                        "platform": "ios",
                        "sessions": {
                            "udid": {
                                "pid": 1234,
                                "session_id": "session-1",
                                "device_udid": "udid",
                                "output_path": str(active),
                                "stream_log_path": str(collector),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            lock = log_dir / ".tracecite-sessions.json.lock"
            lock.write_text("", encoding="utf-8")
            atomic_tmp = log_dir / ".tracecite-sessions.json.stale-tmp"
            atomic_tmp.write_text("partial", encoding="utf-8")
            stale = log_dir / "stale.log"
            stale.write_text("stale\n", encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            for path in (active, collector, heartbeat, rotate_tmp, state, lock, atomic_tmp, stale):
                os.utime(path, (old_ts, old_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )

            deleted = {item.path.name for item in result.items}
            self.assertEqual(deleted, {"stale.log"})
            for path in (active, collector, heartbeat, rotate_tmp, state, lock, atomic_tmp):
                self.assertTrue(path.exists(), path)

    def test_stale_sidecars_without_runtime_state_are_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            heartbeat = log_dir / "orphan.log.heartbeat"
            rotate_tmp = log_dir / ".orphan.log.rotate.tmp"
            heartbeat.write_text("old\n", encoding="utf-8")
            rotate_tmp.write_text("old\n", encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            os.utime(heartbeat, (old_ts, old_ts))
            os.utime(rotate_tmp, (old_ts, old_ts))

            clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertFalse(heartbeat.exists())
            self.assertFalse(rotate_tmp.exists())

    def test_malformed_runtime_state_fails_closed_for_its_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            (log_dir / ".tracecite-sessions.json").write_text("{broken", encoding="utf-8")
            stale = log_dir / "possibly-live.log"
            stale.write_text("do not remove\n", encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            os.utime(stale, (old_ts, old_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertNotIn(stale, {item.path for item in result.items})
            self.assertTrue(stale.exists())

    def test_unknown_capture_state_fails_closed_for_capture_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            (capture_dir / ".tracecite-capture.json").write_text(
                json.dumps({"phase": "unknown"}), encoding="utf-8"
            )
            stale = capture_dir / "possibly-live.trace"
            stale.write_text("do not remove\n", encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            os.utime(stale, (old_ts, old_ts))
            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertNotIn(stale.resolve(), {item.path for item in result.items})
            self.assertTrue(stale.exists())

    def test_recovery_capture_state_keeps_trace_and_unrelated_old_capture_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            trace = capture_dir / "recovery.trace"
            trace.write_text("incomplete\n", encoding="utf-8")
            (capture_dir / ".tracecite-capture.json").write_text(
                json.dumps(
                    {
                        "phase": "recovery_required",
                        "recovery_required": True,
                        "session_id": "perf-1",
                        "local_trace_path": str(trace),
                    }
                ),
                encoding="utf-8",
            )
            unrelated = capture_dir / "old.trace"
            unrelated.write_text("old\n", encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            for path in (trace, unrelated):
                os.utime(path, (old_ts, old_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertNotIn(trace.resolve(), {item.path for item in result.items})
            self.assertIn(unrelated.resolve(), {item.path for item in result.items})
            self.assertTrue(trace.exists())
            self.assertFalse(unrelated.exists())

    def test_archive_is_excluded_by_default_and_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            canonical = log_dir / ".archive" / "PhoneA"
            legacy = log_dir / "archive" / "PhoneB"
            for directory in (canonical, legacy):
                directory.mkdir(parents=True)
                segment = directory / "old.log"
                segment.write_text("old\n", encoding="utf-8")
                (directory / "manifest.json").write_text(
                    json.dumps({"segments": []}), encoding="utf-8"
                )
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            for path in canonical.iterdir():
                os.utime(path, (old_ts, old_ts))
            for path in legacy.iterdir():
                os.utime(path, (old_ts, old_ts))
            os.utime(canonical, (old_ts, old_ts))
            os.utime(legacy, (old_ts, old_ts))

            default = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertFalse(default.include_archive)
            self.assertTrue(canonical.exists())
            self.assertTrue(legacy.exists())

            preview = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                dry_run=True,
                include_archive=True,
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertTrue(preview.include_archive)
            self.assertTrue(any(item.path == canonical.resolve() for item in preview.items))
            self.assertTrue(any(item.path == legacy.resolve() for item in preview.items))
            with self.assertRaisesRegex(CleanupError, "--include-archive --yes"):
                clean_analysis_artifacts(
                    log_dir=log_dir,
                    capture_dir=capture_dir,
                    analysis_dir=analysis_dir,
                    before="today",
                    include_archive=True,
                    now=datetime(2026, 6, 26, 16, 30).astimezone(),
                )
            clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                include_archive=True,
                confirm_archive=True,
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertFalse(canonical.exists())
            self.assertFalse(legacy.exists())

    def test_running_or_malformed_analysis_manifest_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            for name, contents in (
                ("running", {"status": "running"}),
                ("broken", "{broken"),
            ):
                run = analysis_dir / name
                run.mkdir()
                manifest = run / "manifest.json"
                manifest.write_text(
                    contents if isinstance(contents, str) else json.dumps(contents),
                    encoding="utf-8",
                )
                (run / "evidence.txt").write_text("evidence\n", encoding="utf-8")
                old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
                os.utime(run, (old_ts, old_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertFalse({item.path.name for item in result.items} & {"running", "broken"})
            self.assertTrue((analysis_dir / "running" / "evidence.txt").exists())
            self.assertTrue((analysis_dir / "broken" / "evidence.txt").exists())

    def test_extra_project_run_root_is_cleaned_with_same_manifest_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            project_runs = root / ".tracecite" / "runs"
            for directory in (log_dir, capture_dir, analysis_dir, project_runs):
                directory.mkdir(parents=True)
            completed = project_runs / "completed"
            completed.mkdir()
            (completed / "manifest.json").write_text(
                json.dumps({"status": "completed", "retention": {"pinned": False}}),
                encoding="utf-8",
            )
            (completed / "evidence.txt").write_text("old\n", encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            os.utime(completed, (old_ts, old_ts))
            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                extra_analysis_dirs=(project_runs,),
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertIn(completed.resolve(), {item.path for item in result.items})
            self.assertFalse(completed.exists())

    def test_runs_container_is_cleaned_per_run_not_as_a_whole(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            runs = log_dir / ".runs"
            completed = runs / "completed"
            running = runs / "running"
            pinned = runs / "pinned"
            malformed = runs / "malformed"
            for run in (completed, running, pinned, malformed):
                run.mkdir(parents=True)
                (run / "evidence.txt").write_text("evidence\n", encoding="utf-8")
            (completed / "manifest.json").write_text(
                json.dumps({"status": "completed", "retention": {"pinned": False}}),
                encoding="utf-8",
            )
            (running / "manifest.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
            (pinned / "manifest.json").write_text(
                json.dumps({"status": "completed", "retention": {"pinned": True}}),
                encoding="utf-8",
            )
            (malformed / "manifest.json").write_text("{broken", encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            for run in (completed, running, pinned, malformed):
                os.utime(run, (old_ts, old_ts))

            result = clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            deleted = {item.path for item in result.items}
            self.assertIn(completed.resolve(), deleted)
            self.assertNotIn(running.resolve(), deleted)
            self.assertNotIn(pinned.resolve(), deleted)
            self.assertNotIn(malformed.resolve(), deleted)
            self.assertTrue(runs.exists())
            self.assertFalse(completed.exists())
            for run in (running, pinned, malformed):
                self.assertTrue(run.exists())

    def test_analysis_runs_container_is_also_split_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, capture_dir, analysis_dir = (root / name for name in ("Log", "Instrument", "analysis"))
            for directory in (log_dir, capture_dir, analysis_dir):
                directory.mkdir()
            runs = analysis_dir / "runs"
            completed = runs / "completed"
            running = runs / "running"
            for run in (completed, running):
                run.mkdir(parents=True)
                (run / "evidence.txt").write_text("evidence\n", encoding="utf-8")
            (completed / "manifest.json").write_text(
                json.dumps({"status": "completed", "retention": {"pinned": False}}),
                encoding="utf-8",
            )
            (running / "manifest.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
            old_ts = datetime(2026, 6, 25, 12, 0).timestamp()
            for run in (completed, running):
                os.utime(run, (old_ts, old_ts))
            clean_analysis_artifacts(
                log_dir=log_dir,
                capture_dir=capture_dir,
                analysis_dir=analysis_dir,
                before="today",
                now=datetime(2026, 6, 26, 16, 30).astimezone(),
            )
            self.assertFalse(completed.exists())
            self.assertTrue(running.exists())
            self.assertTrue(runs.exists())


if __name__ == "__main__":
    unittest.main()

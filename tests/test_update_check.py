# -*- coding: utf-8 -*-
"""update check：正式版 tag + minor/major 才算有更新；patch 忽略；仅手动触发。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tracecite_mobile.shared import update_check as uc


class SemverPolicyTest(unittest.TestCase):
    def test_parse_and_bump(self) -> None:
        self.assertEqual(uc.parse_semver("1.0.0"), (1, 0, 0))
        self.assertEqual(uc.parse_semver("v1.1.0"), (1, 1, 0))
        self.assertIsNone(uc.parse_semver("1.0.0-rc1"))
        self.assertFalse(uc.is_minor_or_major_bump((1, 0, 0), (1, 0, 1)))
        self.assertTrue(uc.is_minor_or_major_bump((1, 0, 0), (1, 1, 0)))
        self.assertTrue(uc.is_minor_or_major_bump((1, 0, 0), (2, 0, 0)))
        self.assertEqual(uc.bump_kind((1, 0, 0), (1, 0, 1)), "patch")
        self.assertEqual(uc.bump_kind((1, 0, 0), (1, 1, 0)), "minor")


class SoftHintTest(unittest.TestCase):
    def test_soft_hint_line(self) -> None:
        line = uc.soft_update_hint_line("1.0.0", "1.1.0")
        self.assertIn("提示:", line)
        self.assertIn("1.1.0", line)
        self.assertIn("1.0.0", line)
        self.assertIn("tracecite-mobile update apply", line)
        self.assertNotIn("必须", line)
        self.assertNotIn("建议立即", line)

    def test_format_soft_update_hint_only_when_available(self) -> None:
        available = uc.UpdateCheckResult(
            checked=True,
            skipped_by_interval=False,
            update_available=True,
            local_version="1.0.0",
            remote_version="1.1.0",
            bump="minor",
            local_tag="",
            remote_tag="v1.1.0",
            local_commit="",
            remote_commit="",
            remote_name="origin",
            remote_url="",
            interval_hours=48,
            last_checked_at="",
            next_check_after="",
            tool_root="/tmp",
            message="",
        )
        none = uc.UpdateCheckResult(
            checked=True,
            skipped_by_interval=False,
            update_available=False,
            local_version="1.0.0",
            remote_version="1.0.1",
            bump="patch",
            local_tag="",
            remote_tag="v1.0.1",
            local_commit="",
            remote_commit="",
            remote_name="origin",
            remote_url="",
            interval_hours=48,
            last_checked_at="",
            next_check_after="",
            tool_root="/tmp",
            message="",
        )
        self.assertTrue(uc.format_soft_update_hint(available))
        self.assertEqual(uc.format_soft_update_hint(none), "")

    def test_maybe_print_update_hint_prints_stderr(self) -> None:
        result = uc.UpdateCheckResult(
            checked=False,
            skipped_by_interval=True,
            update_available=True,
            local_version="1.0.0",
            remote_version="1.1.0",
            bump="minor",
            local_tag="",
            remote_tag="v1.1.0",
            local_commit="",
            remote_commit="",
            remote_name="origin",
            remote_url="",
            interval_hours=48,
            last_checked_at="",
            next_check_after="",
            tool_root="/tmp",
            message="",
            hint=uc.soft_update_hint_line("1.0.0", "1.1.0"),
        )
        buf = io.StringIO()
        with mock.patch.object(uc, "check_for_updates", return_value=result):
            out = uc.maybe_print_update_hint(stream=buf)
        self.assertIs(out, result)
        self.assertIn("提示: 有新正式版 1.1.0 可用", buf.getvalue())

    def test_maybe_print_silent_when_unavailable(self) -> None:
        result = uc.UpdateCheckResult(
            checked=True,
            skipped_by_interval=False,
            update_available=False,
            local_version="1.0.0",
            remote_version="1.0.1",
            bump="patch",
            local_tag="",
            remote_tag="v1.0.1",
            local_commit="",
            remote_commit="",
            remote_name="origin",
            remote_url="",
            interval_hours=48,
            last_checked_at="",
            next_check_after="",
            tool_root="/tmp",
            message="",
        )
        buf = io.StringIO()
        with mock.patch.object(uc, "check_for_updates", return_value=result):
            uc.maybe_print_update_hint(stream=buf)
        self.assertEqual(buf.getvalue(), "")

    def test_maybe_print_swallows_errors(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(uc, "check_for_updates", side_effect=uc.UpdateError("boom")):
            self.assertIsNone(uc.maybe_print_update_hint(stream=buf))
        self.assertEqual(buf.getvalue(), "")


class UpdateCheckTest(unittest.TestCase):
    def test_interval_skips_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            (root / "tracecite_mobile").mkdir()
            (root / ".git").mkdir()
            state = Path(tmp) / "state.json"
            now = datetime.now(timezone.utc)
            state.write_text(
                json.dumps(
                    {
                        "last_checked_at": now.isoformat(),
                        "update_available": True,
                        "remote_version": "1.1.0",
                        "bump": "minor",
                        "remote_tag": "v1.1.0",
                        "remote_name": "origin",
                        "remote_url": "git@gitlab.example/x.git",
                        "hint": "update",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(uc, "update_state_path", return_value=state):
                with mock.patch.object(uc, "find_tool_root", return_value=root):
                    with mock.patch.object(uc, "_run_git", side_effect=["aaa"]) as git:
                        result = uc.check_for_updates(
                            force=False,
                            interval_hours=48,
                            local_version="1.0.0",
                        )
            self.assertTrue(result.skipped_by_interval)
            self.assertTrue(result.update_available)
            self.assertEqual(git.call_count, 1)

    def test_patch_not_notified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            (root / "tracecite_mobile").mkdir()
            (root / ".git").mkdir()
            state = Path(tmp) / "state.json"

            def fake_git(_root, *args, **_kwargs):
                if args[0] == "rev-parse":
                    return "abc"
                if args[0] == "remote":
                    return "git@gitlab.example/x.git"
                if args[0] == "ls-remote":
                    return (
                        "c1\trefs/tags/v1.0.0\n"
                        "c2\trefs/tags/v1.0.1\n"
                    )
                raise AssertionError(args)

            with mock.patch.object(uc, "update_state_path", return_value=state):
                with mock.patch.object(uc, "find_tool_root", return_value=root):
                    with mock.patch.object(uc, "_run_git", side_effect=fake_git):
                        result = uc.check_for_updates(
                            force=True,
                            local_version="1.0.0",
                        )
            self.assertTrue(result.checked)
            self.assertFalse(result.update_available)
            self.assertEqual(result.bump, "patch")
            self.assertEqual(result.remote_version, "1.0.1")
            self.assertEqual(result.hint, "")

    def test_minor_notified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            (root / "tracecite_mobile").mkdir()
            (root / ".git").mkdir()
            state = Path(tmp) / "state.json"

            def fake_git(_root, *args, **_kwargs):
                if args[0] == "rev-parse":
                    return "abc"
                if args[0] == "remote":
                    return "git@gitlab.example/x.git"
                if args[0] == "ls-remote":
                    return (
                        "c1\trefs/tags/v1.0.0\n"
                        "c2\trefs/tags/v1.1.0\n"
                    )
                raise AssertionError(args)

            with mock.patch.object(uc, "update_state_path", return_value=state):
                with mock.patch.object(uc, "find_tool_root", return_value=root):
                    with mock.patch.object(uc, "_run_git", side_effect=fake_git):
                        result = uc.check_for_updates(
                            force=True,
                            local_version="1.0.0",
                        )
            self.assertTrue(result.update_available)
            self.assertEqual(result.bump, "minor")
            self.assertEqual(result.remote_tag, "v1.1.0")
            self.assertIn("需要时可执行: tracecite-mobile update apply", result.hint)
            self.assertNotIn("建议更新", result.hint)

    def test_apply_rejects_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                uc,
                "_run_git",
                return_value=" M tracecite_mobile/update_check.py",
            ) as git:
                with self.assertRaisesRegex(uc.UpdateError, "工作区有未提交"):
                    uc.apply_update(tool_root=root)
            git.assert_called_once_with(
                root,
                "status",
                "--porcelain",
                "--untracked-files=all",
            )

    def test_apply_rejects_unknown_explicit_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_git(_root, *args, **_kwargs):
                if args[0] == "status":
                    return ""
                if args[0] == "rev-parse":
                    return "abc"
                if args[0] == "fetch":
                    return ""
                if args[0] == "ls-remote":
                    return "c1\trefs/tags/v1.1.0\n"
                raise AssertionError(args)

            with mock.patch.object(uc, "_run_git", side_effect=fake_git):
                with self.assertRaisesRegex(uc.UpdateError, "不是远程"):
                    uc.apply_update(tool_root=root, tag="v9.9.9")


if __name__ == "__main__":
    unittest.main()

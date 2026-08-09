# -*- coding: utf-8 -*-
"""filter 的 scenario 合并与错误处理（含默认 preset 回落路径）。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from tracecite_mobile.commands.analysis import cmd_filter
from tracecite_mobile.shared.config import write_profile_template
from tracecite_mobile.analysis.knowledge import add_filter_terms, ensure_scenario


SAMPLE_LOG = """\
Jul 25 18:42:10 DemoApp(com.example.demo.logging)[6306] <Notice>: I Action: 示例导航 tapped
Jul 25 18:42:30 DemoApp(com.example.demo.logging)[6306] <Notice>: I Action: unrelated
"""


def _args(log_path: Path, **overrides: object) -> SimpleNamespace:
    base = dict(
        log_path=str(log_path),
        preset=None,
        grep=None,
        scenario=None,
        tag=None,
        out=None,
        snapshot=False,
        pid=None,
        tail_lines=None,
        line_from=None,
        line_to=None,
        last=None,
        since=None,
        until=None,
        segmenter="auto",
        format=None,
        from_sessions=False,
        merge_timeline=False,
        output_dir=None,
        fold=False,
        platform="ios",
        json=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class CliFilterScenarioTest(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        write_profile_template(root)
        ensure_scenario("demo-navigation", title="示例导航", start_dir=root)
        add_filter_terms(
            "user-behavior",
            ["示例导航"],
            start_dir=root,
            scenario="demo-navigation",
        )
        config_path = root / ".tracecite" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["default_filter_preset"] = "user-behavior"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log_path = root / "runtime.log"
        log_path.write_text(SAMPLE_LOG, encoding="utf-8")
        return log_path

    def _run(self, root: Path, args: SimpleNamespace) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        cwd = Path.cwd()
        os.chdir(root)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = cmd_filter(args)
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def test_scenario_merges_on_default_preset_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = self._project(root)

            code, out, _ = self._run(
                root,
                _args(log_path, scenario="demo-navigation"),
            )

            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertIn("示例导航", payload["pattern"])
            self.assertEqual(
                payload["pattern_source"],
                "preset:user-behavior+scenario:demo-navigation",
            )
            manifest_path = Path(payload["manifest_path"])
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "filter")
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["verdict"], "passed")
            self.assertIn("filtered_log", {row["role"] for row in manifest["artifacts"]})
            self.assertFalse((log_path.parent / ".filtered").exists())

    def test_unknown_scenario_reports_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = self._project(root)

            code, _, err = self._run(root, _args(log_path, scenario="no-such"))

            self.assertEqual(code, 1)
            self.assertIn("未知场景", err)

    def test_scenario_without_preset_context_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = self._project(root)

            code, _, err = self._run(
                root,
                _args(log_path, grep="Action:", scenario="demo-navigation"),
            )

            self.assertEqual(code, 1)
            self.assertIn("--scenario 需要 preset 上下文", err)


if __name__ == "__main__":
    unittest.main()

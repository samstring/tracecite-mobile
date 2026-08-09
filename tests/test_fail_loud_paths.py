# -*- coding: utf-8 -*-
"""不允许静默降级：知识库损坏、未知场景、trace 数据不可解析。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tracecite_mobile.device import trace_analysis as analysis_module
from tracecite_mobile.device.trace_analysis import TraceAnalysis, analyze_trace, format_analysis_summary
from tracecite_mobile.analysis.behavior_summary import summarize_behavior_file
from tracecite_mobile.shared.config import ProfileError, load_project_profile, write_profile_template
from tracecite_mobile.analysis.knowledge import KnowledgeError, ensure_scenario


class FailLoudTest(unittest.TestCase):
    def test_corrupt_knowledge_blocks_profile_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            (root / ".tracecite" / "knowledge.ios.json").write_text(
                "{not json", encoding="utf-8"
            )

            with self.assertRaisesRegex(ProfileError, "知识库不可用"):
                load_project_profile(root)

    def test_behavior_unknown_scenario_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            ensure_scenario("known", title="已知", start_dir=root)
            log_path = root / "runtime.log"
            log_path.write_text("nothing\n", encoding="utf-8")

            with self.assertRaisesRegex(KnowledgeError, "未知场景"):
                summarize_behavior_file(log_path, start_dir=root, scenario="typo")

    def test_unparsable_hangs_xml_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "app.trace"
            trace.mkdir()
            hangs = root / "hangs.xml"
            hangs.write_text("<not-xml", encoding="utf-8")

            analysis = analyze_trace(
                trace,
                hangs_path=hangs,
                export_missing=False,
            )

            self.assertTrue(analysis.data_errors)
            summary = format_analysis_summary(analysis)
            self.assertIn("数据不可解析", summary)
            self.assertNotIn("未检测到 hang", summary)

    def test_failed_export_is_not_treated_as_no_hang(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "app.trace"
            trace.mkdir()

            with mock.patch.object(analysis_module, "export_toc", return_value=False):
                with mock.patch.object(
                    analysis_module, "export_xpath", return_value=False
                ):
                    result = analyze_trace(trace, export_missing=True)

            self.assertTrue(result.data_errors)
            summary = format_analysis_summary(result)
            self.assertIn("数据不可解析", summary)
            self.assertNotIn("未检测到 hang", summary)

    def test_clean_summary_still_reports_no_issue_when_data_ok(self) -> None:
        summary = format_analysis_summary(TraceAnalysis(trace_path=Path("a.trace")))
        self.assertIn("未检测到 hang", summary)


if __name__ == "__main__":
    unittest.main()

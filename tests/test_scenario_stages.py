# -*- coding: utf-8 -*-
"""scenario 多 stage 编排：先粗后精、每段独立计数。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.analysis.scenario import run_scenario

SAMPLE = """\
2026-08-08 01:00:01.100  I Boot : start
2026-08-08 01:00:02.200  E Net : request failed errorCode=401
2026-08-08 01:00:03.300  I Net : retry
2026-08-08 01:00:04.400  E Net : request failed errorCode=500
2026-08-08 01:00:05.500  I Boot : done
"""


class _TmpDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.log = self.tmp / "app.log"
        self.log.write_text(SAMPLE, encoding="utf-8")

    def _base_spec(self, **filter_extra) -> dict:
        spec = {
            "schema_version": 2,
            "name": "stages-test",
            "source": {"type": "file", "path": str(self.log)},
            "parse": {"segmenter": "applog"},
            "filter": {},
            "assert": {"rules": [
                {"name": "errorCode=401", "type": "contains", "match": "errorCode=401"}
            ]},
        }
        spec["filter"].update(filter_extra)
        return spec


class ScenarioStagesTest(_TmpDirTest):
    def test_two_stages_coarse_then_refine(self) -> None:
        spec = self._base_spec(
            stages=[
                {"name": "coarse", "grep": "error|timeout|retry", "tag": "coarse"},
                {"name": "refine", "grep": r"errorCode=4\d\d", "tag": "refine"},
            ]
        )
        summary = run_scenario(spec, base_dir=self.tmp)
        stages = summary["stages"]
        self.assertEqual(len(stages), 2)
        # 先粗后精：命中数收敛
        self.assertEqual(stages[0]["name"], "coarse")
        self.assertEqual(stages[0]["match_records"], 3)
        self.assertEqual(stages[1]["name"], "refine")
        self.assertEqual(stages[1]["match_records"], 1)
        # 顶层汇总 = 最后一段（精筛）
        self.assertEqual(summary["total_match_records"], 1)
        self.assertEqual(summary["pattern"], r"errorCode=4\d\d")
        self.assertTrue(summary["required_satisfied"])
        self.assertEqual(len(summary["results"]), 1)
        # 各段产物不互相覆盖
        self.assertNotEqual(
            stages[0]["outputs"][0]["output_path"],
            stages[1]["outputs"][0]["output_path"],
        )
        # 每段有自己的 tag
        self.assertIn("coarse", stages[0]["tag"])
        self.assertIn("refine", stages[1]["tag"])

    def test_stage_missing_required_reported(self) -> None:
        spec = self._base_spec(
            stages=[
                {"name": "a", "grep": "start|done"},
                {"name": "b", "grep": "errorCode=9\\d\\d"},
            ]
        )
        summary = run_scenario(spec, base_dir=self.tmp)
        self.assertEqual(summary["total_match_records"], 0)
        self.assertFalse(summary["required_satisfied"])
        self.assertEqual(summary["assertions"]["missing_required"], ["errorCode=401"])

    def test_single_filter_has_no_stages_key(self) -> None:
        spec = self._base_spec(grep="errorCode=401")
        summary = run_scenario(spec, base_dir=self.tmp)
        self.assertNotIn("stages", summary)
        self.assertEqual(summary["total_match_records"], 1)
        self.assertTrue(summary["required_satisfied"])
        self.assertEqual(summary["pattern"], "errorCode=401")

    def test_stage_grep_fallback_and_preset_error(self) -> None:
        """stage 无 grep/preset 时报错，而不是静默。"""
        spec = self._base_spec(stages=[{"name": "bad"}])
        with self.assertRaises(Exception):
            run_scenario(spec, base_dir=self.tmp)


if __name__ == "__main__":
    unittest.main()


class ScenarioAnalysisConfigTest(_TmpDirTest):
    """分析阈值配置化：spec.analysis > profile.analysis > 代码默认。"""

    def test_spec_analysis_coverage_threshold(self) -> None:
        """spec 配 coverage_threshold=2：命中 3 条就触发「证据偏多」提示。"""
        spec = {
            "schema_version": 2,
            "name": "cfg-coverage",
            "source": {"type": "file", "path": str(self.log)},
            "parse": {"segmenter": "applog"},
            "filter": {"grep": "error|retry"},
            "analysis": {"coverage_threshold": 2},
        }
        summary = run_scenario(spec, base_dir=self.tmp)
        warns = summary["results"][0].get("coverage_warning") or []
        self.assertTrue(any("证据偏多" in w for w in warns), warns)

    def test_default_coverage_threshold_no_warning(self) -> None:
        """默认 200：3 条命中不触发收窄提示。"""
        spec = {
            "schema_version": 2,
            "name": "cfg-default",
            "source": {"type": "file", "path": str(self.log)},
            "parse": {"segmenter": "applog"},
            "filter": {"grep": "error|retry"},
        }
        summary = run_scenario(spec, base_dir=self.tmp)
        warns = summary["results"][0].get("coverage_warning") or []
        self.assertFalse(any("证据偏多" in w for w in warns), warns)

    def test_spec_analysis_template_threshold_generates_fold(self) -> None:
        """spec 配 template_threshold=1：命中≥1 自动生成模板折叠。"""
        spec = {
            "schema_version": 2,
            "name": "cfg-fold",
            "source": {"type": "file", "path": str(self.log)},
            "parse": {"segmenter": "applog"},
            "filter": {"grep": "errorCode=401"},
            "analysis": {"template_threshold": 1},
        }
        summary = run_scenario(spec, base_dir=self.tmp)
        self.assertEqual(summary["results"][0]["match_records"], 1)
        self.assertIsNotNone(summary["results"][0].get("templates_path"))
        self.assertIsNotNone(summary["results"][0].get("template_stats"))

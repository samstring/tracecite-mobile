# -*- coding: utf-8 -*-
"""配置驱动覆盖与自成长：
- 命名文本格式注册（config.json formats 段 + scenario parse.format 名字引用）
- grow suggest（发现侧）与 grow auto（按阈值沉淀）闭环
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.analysis.knowledge import (
    apply_grow_suggestions,
    load_project_knowledge,
    suggest_grow_terms,
)
from tracecite_mobile.analysis.scenario import ScenarioError, run_scenario

SAMPLE = """\
2026-08-08 01:00:01.100  I Boot : start
2026-08-08 01:00:02.200  E Net : request failed errorCode=401
2026-08-08 01:00:03.300  I Net : retry
2026-08-08 01:00:04.400  E Net : request failed errorCode=500
2026-08-08 01:00:05.500  I Boot : done
"""


class _TmpTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.log = self.tmp / "app.log"
        self.log.write_text(SAMPLE, encoding="utf-8")


class NamedFormatRegistryTest(_TmpTest):
    """config.json formats 段注册命名格式，scenario parse.format 用名字引用。"""

    def _write_profile_with_formats(self) -> None:
        profile = {
            "process_name": "test",
            "log_output_dir": str(self.tmp / "log"),
            "capture_output_dir": str(self.tmp / "cap"),
            "formats": {
                "myfmt": {"start": r"^\d{4}-\d{2}-\d{2}"},
            },
        }
        (self.tmp / ".tracecite").mkdir(exist_ok=True)
        (self.tmp / ".tracecite" / "config.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_format_name_reference(self) -> None:
        self._write_profile_with_formats()
        spec = {
            "schema_version": 2,
            "name": "named-fmt",
            "source": {"type": "file", "path": str(self.log)},
            "parse": {"format": "myfmt"},
            "filter": {"grep": "errorCode"},
        }
        summary = run_scenario(spec, base_dir=self.tmp, start_dir=self.tmp)
        self.assertEqual(summary["segmenter"], "format:myfmt")
        self.assertEqual(summary["total_match_records"], 2)

    def test_unknown_format_name_raises(self) -> None:
        self._write_profile_with_formats()
        spec = {
            "schema_version": 2,
            "name": "bad-fmt",
            "source": {"type": "file", "path": str(self.log)},
            "parse": {"format": "no-such-format"},
            "filter": {"grep": "errorCode"},
        }
        with self.assertRaises(ScenarioError):
            run_scenario(spec, base_dir=self.tmp, start_dir=self.tmp)

    def test_inline_format_still_works(self) -> None:
        spec = {
            "schema_version": 2,
            "name": "inline-fmt",
            "source": {"type": "file", "path": str(self.log)},
            "parse": {"format": {"start": r"^\d{4}-\d{2}-\d{2}"}},
            "filter": {"grep": "errorCode"},
        }
        summary = run_scenario(spec, base_dir=self.tmp, start_dir=self.tmp)
        self.assertEqual(summary["total_match_records"], 2)


class GrowSuggestTest(_TmpTest):
    """自成长发现侧：高频未覆盖 token 自动成为候选。"""

    def test_suggest_finds_uncovered_token(self) -> None:
        result = suggest_grow_terms(
            self.log, preset="user-behavior", start_dir=self.tmp, min_count=1
        )
        tokens = [c["token"] for c in result["candidates"]]
        # errorCode 高频且不在词表/停用词 → 候选；retry/request/failed/start/done 是停用词
        self.assertIn("errorCode", tokens)
        self.assertNotIn("retry", tokens)
        self.assertNotIn("request", tokens)
        cand = next(c for c in result["candidates"] if c["token"] == "errorCode")
        self.assertEqual(cand["count"], 2)
        self.assertEqual(cand["kind"], "marker")  # 驼峰 → marker 建议

    def test_suggest_respects_min_count(self) -> None:
        result = suggest_grow_terms(
            self.log, preset="user-behavior", start_dir=self.tmp, min_count=5
        )
        self.assertEqual(result["candidates"], [])

    def test_suggest_excludes_existing_markers(self) -> None:
        # 先沉淀 errorCode 为 marker，再 suggest 应排除
        apply_grow_suggestions(self.log, preset="user-behavior", start_dir=self.tmp, min_count=1)
        result = suggest_grow_terms(
            self.log, preset="user-behavior", start_dir=self.tmp, min_count=1
        )
        tokens = [c["token"] for c in result["candidates"]]
        self.assertNotIn("errorCode", tokens)


class GrowAutoTest(_TmpTest):
    """自成长沉淀侧：按阈值一键写 knowledge。"""

    def test_dry_run_no_write(self) -> None:
        result = apply_grow_suggestions(
            self.log, preset="user-behavior", start_dir=self.tmp, min_count=1, dry_run=True
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["would_add_markers"]), 1)
        self.assertEqual(result["would_add_markers"][0]["needle"], "errorCode")
        # dry-run 不写盘：知识文件不存在
        self.assertFalse((self.tmp / ".tracecite" / "knowledge.json").exists())

    def test_auto_adds_marker_then_excludes(self) -> None:
        result = apply_grow_suggestions(
            self.log, preset="user-behavior", start_dir=self.tmp, min_count=1
        )
        self.assertEqual(result["added_markers"], 1)
        self.assertFalse(result["dry_run"])
        knowledge = load_project_knowledge(self.tmp, platform="ios")
        needles = [m.needle for m in knowledge.markers]
        self.assertIn("errorCode", needles)
        # 再 auto 一次：无新增（已存在）
        again = apply_grow_suggestions(
            self.log, preset="user-behavior", start_dir=self.tmp, min_count=1
        )
        self.assertEqual(again["added_markers"], 0)


if __name__ == "__main__":
    unittest.main()


class FormatViaScenarioIntegrationTest:
    """声明式 format 与 scenario 引擎的集成测试（上层，非 core）。"""
    FORMAT = {
        "start": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+",
        "timestamp_formats": ["%Y-%m-%d %H:%M:%S.%f"],
        "multiline": True,
    }
    SAMPLE = (
        "2026-08-09 10:00:00.001  I Test : normal\n"
        "2026-08-09 10:00:02.002  E Test : ERROR purchase failed\n"
        "  stack trace\n"
        "2026-08-09 10:00:03.003  I Test : heartbeat\n"
    )

    def test_scenario_parse_format_end_to_end(self, tmp_path):
        from tracecite_mobile.analysis.scenario import run_scenario
        log = tmp_path / "custom.log"
        log.write_text(self.SAMPLE, encoding="utf-8")
        spec = {
            "schema_version": 2,
            "name": "fmt-test", "source": {"type": "file", "path": str(log)},
            "parse": {"format": self.FORMAT},
            "filter": {"grep": "ERROR|purchase"},
            "assert": {"rules": [
                {"name": "purchase", "type": "contains", "match": "purchase"}
            ]},
        }
        summary = run_scenario(spec, base_dir=tmp_path)
        assert summary["segmenter"] == "format"
        r = summary["results"][0]
        assert r["match_records"] == 1


def test_android_profile_loads_android_knowledge(tmp_path):
    from tracecite_mobile.analysis.knowledge import add_filter_terms
    from tracecite_mobile.shared.config import load_project_profile

    add_filter_terms(
        "android-user-behavior",
        ["$AppClick"],
        start_dir=tmp_path,
        platform="android",
    )
    profile = load_project_profile(tmp_path, platform="android")
    assert "$AppClick" in profile.filter_presets["android-user-behavior"].pattern

# -*- coding: utf-8 -*-
"""引擎接缝（core.segmenter / core.source / scenario 断言包）的回归测试。

重点钉死一个曾经真实发生过的偏差：断言若在 filtered 文件的元信息头部上统计
命中，`# pattern: xxx` 会白送一次命中，导致「0 命中」被判成「必需断言满足」——
这正是断言包本该拦住的错误结论。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracecite_core import (
    AnalysisEvent,
    ArchiveSource,
    SourceError,
    build_segmenter,
)
from tracecite_mobile.plugins.segmenters import detect_segmenter_kind
from tracecite_core.text_filter import HEADER_TERMINATOR, strip_filter_header
from tracecite_mobile.analysis.assertions import build_assertions


APPLOG_SAMPLE = """\
2026-08-08 01:00:01.100  I Boot : start
2026-08-08 01:00:02.200  E Net : request failed errorCode=401
    at com.foo.Bar(Bar.java:12)
    at com.foo.Baz(Baz.java:34)
2026-08-08 01:00:03.300  I Net : retry
"""

ANDROID_APPLOG_SAMPLE = """\
2026-08-07 23:33:04.879 pool-9-thread-1 I TaskExecutor : initialized
2026-08-07 23:33:04.881 DefaultDispatcher-worker-20 E TaskLog : SecurityException
"""

DEVICELOG_SAMPLE = """\
Jul 25 18:42:10 DemoApp(com.example.demo.logging)[6306] <Notice>: I Action: tapped
Jul 25 18:42:11 DemoApp(com.example.demo.logging)[6306] <Error>: failed line1
continuation without header
Jul 25 18:42:12 DemoApp(com.example.demo.logging)[6306] <Notice>: done
"""


class _TmpDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name: str, content: str) -> Path:
        path = self.tmp / name
        path.write_text(content, encoding="utf-8")
        return path


class AssertionHeaderIsolationTest(_TmpDirTest):
    """断言只能统计正文，不能被 filtered 头部里的元信息污染。"""

    def _filtered(self, pattern: str, body: str) -> str:
        return (
            "# tracecite log filter\n"
            f"# tag: unit\n"
            f"# pattern: {pattern}\n"
            "# match_records: 0\n"
            f"{HEADER_TERMINATOR}\n"
        ) + body

    def test_strip_filter_header_removes_metadata(self) -> None:
        raw = self._filtered("errorCode", "real body line\n")
        self.assertEqual(strip_filter_header(raw), "real body line\n")

    def test_strip_filter_header_passthrough_when_absent(self) -> None:
        self.assertEqual(strip_filter_header("no header here\n"), "no header here\n")

    def test_zero_hit_must_not_satisfy_required_assertion(self) -> None:
        """回归：pattern 出现在头部时，0 命中不得被判成断言满足。"""
        raw = self._filtered("ThisNeverAppears", "")

        rules = [{"name": "ThisNeverAppears", "type": "contains", "match": "ThisNeverAppears"}]
        polluted = build_assertions(raw, rules=rules)
        self.assertTrue(
            polluted.assertions[0].satisfied,
            "前置条件：未剥头部时确实会被污染（否则本测试失去意义）",
        )

        clean = build_assertions(strip_filter_header(raw), rules=rules)
        self.assertFalse(clean.assertions[0].satisfied)
        self.assertEqual(clean.assertions[0].hits, 0)
        self.assertFalse(clean.to_dict()["all_required_satisfied"])
        self.assertEqual(clean.to_dict()["missing_required"], ["ThisNeverAppears"])

    def test_hit_count_excludes_header_occurrence(self) -> None:
        raw = self._filtered("errorCode", "a errorCode=1\nb errorCode=2\n")
        pkg = build_assertions(
            strip_filter_header(raw),
            rules=[{"name": "errorCode", "type": "contains", "match": "errorCode"}],
        )
        self.assertEqual(pkg.assertions[0].hits, 2)

    def test_invalid_regex_falls_back_to_literal(self) -> None:
        pkg = build_assertions(
            "cost is 100% (approx)",
            rules=[{"name": "literal", "type": "contains", "match": "100% ("}],
        )
        self.assertTrue(pkg.assertions[0].satisfied)


class AssertionCaseSensitivityTest(unittest.TestCase):
    """filter 侧常写 `(?i)`，断言若不同步会严重少算（实测差 250 倍）。"""

    BODY = "Error: boom\nFAILED to connect\nRequest Timeout\n"

    def test_default_is_case_sensitive(self) -> None:
        pkg = build_assertions(
            self.BODY,
            rules=[{"name": "error", "type": "contains", "match": "error"}],
        )
        self.assertEqual(pkg.assertions[0].hits, 0)

    def test_ignore_case_counts_all_variants(self) -> None:
        pkg = build_assertions(
            self.BODY,
            rules=[
                {"name": "error", "type": "contains", "match": "error"},
                {"name": "fail", "type": "contains", "match": "fail", "required": False},
                {"name": "timeout", "type": "contains", "match": "timeout", "required": False},
            ],
            ignore_case=True,
        )
        hits = {a.name: a.hits for a in pkg.assertions}
        self.assertEqual(hits, {"error": 1, "fail": 1, "timeout": 1})
        self.assertTrue(pkg.to_dict()["all_required_satisfied"])

    def test_ignore_case_applies_to_literal_fallback(self) -> None:
        pkg = build_assertions(
            "Cost 100% (X)",
            rules=[{"name": "literal", "type": "contains", "match": "100% (x"}],
            ignore_case=True,
        )
        self.assertTrue(pkg.assertions[0].satisfied)


class AssertionDslTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            AnalysisEvent("2026-08-08 01:00:01.000", "interaction", "tap", "behavior", label="打开示例"),
            AnalysisEvent("2026-08-08 01:00:02.000", "network", "request", "filter", attributes={"status": 200}),
            AnalysisEvent("2026-08-08 01:00:03.500", "business", "play", "filter", label="播放成功"),
        ]

    def test_count_absent_and_sequence(self) -> None:
        pkg = build_assertions(
            "",
            events=self.events,
            rules=[
                {"name": "one-request", "type": "count", "event": {"category": "network"}, "exact": 1},
                {"name": "no-crash", "type": "absent", "event": {"category": "crash"}},
                {
                    "name": "tap-to-play",
                    "type": "sequence",
                    "events": [{"name": "tap"}, {"name": "request"}, {"name": "play"}],
                    "within": "3s",
                },
            ],
        )
        self.assertTrue(pkg.all_required_satisfied)
        self.assertEqual([item.hits for item in pkg.assertions], [1, 0, 1])

    def test_sequence_respects_time_window(self) -> None:
        pkg = build_assertions(
            "",
            events=self.events,
            rules=[{
                "name": "too-fast",
                "type": "sequence",
                "events": [{"name": "tap"}, {"name": "play"}],
                "within": "2s",
            }],
        )
        self.assertFalse(pkg.all_required_satisfied)

    def test_filter_configuration_does_not_pollute_event_match(self) -> None:
        event = AnalysisEvent(
            "2026-08-08 01:00:00.000",
            "log_match",
            "SecurityException",
            "filter",
            attributes={"pattern": "SecurityException|FATAL EXCEPTION"},
            text="java.lang.SecurityException",
        )
        pkg = build_assertions(
            "",
            events=[event],
            rules=[{"name": "no-fatal", "type": "absent", "event": {"match": "FATAL EXCEPTION"}}],
        )
        self.assertTrue(pkg.all_required_satisfied)


class SegmenterTest(_TmpDirTest):
    def test_applog_keeps_multiline_block_intact(self) -> None:
        path = self._write("app.log", APPLOG_SAMPLE)
        records = list(build_segmenter("applog").segment_file(path))
        self.assertEqual(len(records), 3)
        self.assertEqual(records[1].line_count, 3, "堆栈行必须并入上一条记录")
        self.assertIn("errorCode=401", records[1].text)
        self.assertIsNotNone(records[0].timestamp)

    def test_devicelog_merges_continuation_lines(self) -> None:
        path = self._write("dev.log", DEVICELOG_SAMPLE)
        records = list(build_segmenter("devicelog").segment_file(path))
        self.assertEqual(len(records), 3)
        self.assertEqual(records[1].line_count, 2)

    def test_jsonline_parses_declared_fields(self) -> None:
        path = self._write(
            "a.jsonl",
            "\n".join(
                json.dumps({"ts": f"2026-08-08 01:00:0{i}", "level": "ERROR", "msg": f"m{i}"})
                for i in range(1, 4)
            )
            + "\n",
        )
        seg = build_segmenter(
            "jsonline", time_field="ts", level_field="level", msg_field="msg"
        )
        records = list(seg.segment_file(path))
        self.assertEqual(len(records), 3)
        self.assertIsNotNone(records[0].timestamp)

    def test_rawtext_line_mode(self) -> None:
        path = self._write("p.txt", "alpha\nbeta\ngamma\n")
        self.assertEqual(len(list(build_segmenter("rawtext", mode="line").segment_file(path))), 3)

    def test_unknown_kind_raises_with_choices(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_segmenter("definitely-not-a-segmenter")
        self.assertIn("applog", str(ctx.exception))

    def test_detect_segmenter_kind(self) -> None:
        cases = {
            "app.log": (APPLOG_SAMPLE, "applog"),
            "android-app.log": (ANDROID_APPLOG_SAMPLE, "applog"),
            "dev.log": (DEVICELOG_SAMPLE, "devicelog"),
            "plain.txt": ("alpha\nbeta\ngamma\n", "rawtext"),
        }
        for name, (content, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(detect_segmenter_kind(self._write(name, content)), expected)


class ArchiveDetectionTest(_TmpDirTest):
    def test_misleading_extension_is_not_treated_as_archive(self) -> None:
        """`.zip.txt` 是纯文本，必须按内容判定而不是按后缀。"""
        path = self._write("notreally.zip.txt", APPLOG_SAMPLE)
        self.assertFalse(ArchiveSource.is_archive(path))

    def test_real_zip_is_detected(self) -> None:
        import zipfile

        path = self.tmp / "bundle.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("inner.txt", APPLOG_SAMPLE)
        self.assertTrue(ArchiveSource.is_archive(path))

    def test_archive_rejects_path_traversal_members(self) -> None:
        import zipfile

        path = self.tmp / "unsafe.zip"
        extract = self.tmp / "extract"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("../escaped.txt", "must not escape")
            zf.writestr("safe/inside.txt", APPLOG_SAMPLE)

        with self.assertRaisesRegex(SourceError, "不安全成员"):
            ArchiveSource(path, extract_dir=extract).extract()

        self.assertFalse((self.tmp / "escaped.txt").exists())
        self.assertFalse((extract / "safe" / "inside.txt").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

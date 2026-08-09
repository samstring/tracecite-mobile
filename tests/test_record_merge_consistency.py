# -*- coding: utf-8 -*-
"""stream 写入与 filter 离线合并必须切出同样的 record 边界。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from tracecite_core import build_segmenter
from tracecite_core.text_filter import _iter_merged_records, filter_text
from tracecite_mobile.plugins.processor import process_stream


# 第二行是「带头续行」（message 以 " 开头），但上一行本身已闭合
SAMPLE = (
    'Jul 25 18:42:10 DemoApp(dylib)[6306] <Notice>: I Http: response body\n'
    'Jul 25 18:42:10 DemoApp(dylib)[6306] <Notice>: "code": 500, "msg": "boom"\n'
    'Jul 25 18:42:11 DemoApp(dylib)[6306] <Notice>: I Action: next\n'
)


class RecordMergeConsistencyTest(unittest.TestCase):
    def test_filter_merges_same_records_as_stream(self) -> None:
        out = io.StringIO()
        process_stream(io.BytesIO(SAMPLE.encode("utf-8")), out)

        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "raw.log"
            raw_path.write_text(SAMPLE, encoding="utf-8")
            written_path = Path(tmp) / "written.log"
            written_path.write_text(out.getvalue(), encoding="utf-8")

            segmenter = build_segmenter("devicelog")
            raw_records = list(_iter_merged_records(raw_path, segmenter=segmenter))
            written_records = list(
                _iter_merged_records(written_path, segmenter=segmenter)
            )

        # 带头续行必须并入上一条，且 filter 复读 stream 输出得到同样边界
        self.assertEqual(len(raw_records), 2)
        self.assertEqual(
            [r.text for r in raw_records],
            [r.text for r in written_records],
        )
        self.assertIn('"code": 500', raw_records[0].text)

    def test_continuation_payload_kept_with_header_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.log"
            path.write_text(SAMPLE, encoding="utf-8")
            result = filter_text(
                path,
                pattern="response body",
                tag="http",
                segmenter=build_segmenter("devicelog"),
            )
            body = result.output_path.read_text(encoding="utf-8").split("# ---\n", 1)[1]

        self.assertIn("response body", body)
        self.assertIn('"code": 500', body)


if __name__ == "__main__":
    unittest.main()

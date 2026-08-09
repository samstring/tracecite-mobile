from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tracecite_core import build_segmenter, detect_segmenter_kind
from tracecite_core.text_filter import filter_text, record_timestamp, text_time_range

from tracecite_mobile.plugins.segmenters import (
    AppLogSegmenter,
    DeviceLogSegmenter,
    MixedLogSegmenter,
)


MIXED_LOG = """\
2026-08-09 10:00:00.000 I App : app keep one
2026-08-09 10:00:01.000 I App : app keep two
08-09 10:00:02.000  100  101 I Device : device keep one
08-09 10:00:03.000  100  101 I Device : device keep two
{"timestamp":"2026-08-09 10:00:04","level":"INFO","message":"json keep one"}
{"timestamp":"2026-08-09 10:00:05","level":"INFO","message":"json keep two"}
"""


def _strip(segmenter, line: str) -> str:
    match = segmenter.header_strip_re.match(line)
    return line[match.end():] if match else line


def test_mixed_segmenter_detects_and_preserves_each_format(tmp_path: Path) -> None:
    source = tmp_path / "mixed.log"
    source.write_text(MIXED_LOG, encoding="utf-8")

    assert detect_segmenter_kind(source) == "mixed"
    segmenter = build_segmenter("mixed")
    assert isinstance(segmenter, MixedLogSegmenter)
    assert isinstance(build_segmenter("applog"), AppLogSegmenter)
    records = list(segmenter.segment_file(source))

    assert len(records) == 6
    assert [record.start_line for record in records] == [1, 2, 3, 4, 5, 6]
    reference = datetime(2026, 8, 9, 10, 0, 0)
    assert [
        record_timestamp(record, ref=reference, segmenter=segmenter)
        for record in records
    ] == [
        datetime(2026, 8, 9, 10, 0, second)
        for second in range(6)
    ]

    time_range = text_time_range(source, segmenter=segmenter)
    assert time_range["time_from"] == "2026-08-09T10:00:00"
    assert time_range["time_to"] == "2026-08-09T10:00:05"
    assert time_range["unparsed_records"] == 0

    result = filter_text(
        source,
        pattern="keep",
        tag="mixed",
        last="3s",
        segmenter=segmenter,
    )
    assert result.match_records == 4


def test_device_time_and_header_rules_live_in_application_segmenters() -> None:
    reference = datetime(2026, 1, 1, 0, 0, 5)
    segmenter = DeviceLogSegmenter()
    parsed = segmenter.parse_time_argument(
        "Dec 31 23:59:59", reference=reference
    )
    assert parsed == datetime(2025, 12, 31, 23, 59, 59)

    app = AppLogSegmenter()
    assert _strip(
        app,
        "2026-08-07 23:33:11.183 DefaultDispatcher-worker-20 E AppMetrics : error",
    ) == "error"
    assert _strip(
        segmenter,
        "Jul 25 18:42:10 YourApp(dylib)[6306] <Notice>: I Action: tapped",
    ) == "I Action: tapped"
    assert _strip(
        segmenter,
        "06-26 09:19:31.924  1710  1710 D Tag: msg",
    ) == "msg"

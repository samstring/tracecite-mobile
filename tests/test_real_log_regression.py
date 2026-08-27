from __future__ import annotations

import os
from pathlib import Path

import pytest

from tracecite_mobile.plugins.segmenters import DeviceLogSegmenter, detect_segmenter_kind


LOGHUB_DIR = Path(os.environ.get("LOGHUB_DIR", "")) if os.environ.get("LOGHUB_DIR") else None
pytestmark = pytest.mark.skipif(
    not os.environ.get("TRACECITE_REAL_LOGS") or LOGHUB_DIR is None,
    reason="real Loghub samples are only downloaded in the dedicated CI job",
)


def _sample(name: str) -> Path:
    assert LOGHUB_DIR is not None
    path = LOGHUB_DIR / name
    assert path.is_file(), f"missing Loghub sample: {path}"
    return path


def test_loghub_android_is_recognized_as_device_log() -> None:
    android = _sample("Android_2k.log")
    assert detect_segmenter_kind(android) == "devicelog"


def test_loghub_android_segments_into_stable_records() -> None:
    android = _sample("Android_2k.log")
    with android.open("r", encoding="utf-8", errors="replace") as handle:
        records = list(DeviceLogSegmenter().segment_lines(enumerate(handle, start=1)))

    # Loghub's sample contains 2,000 physical records.  The Mobile segmenter may
    # merge genuine continuations, so assert a strong lower bound rather than
    # coupling the regression test to every historical message body.
    assert 1500 <= len(records) <= 2000
    assert all(record.line_start >= 1 for record in records)
    assert all(record.line_end >= record.line_start for record in records)
    assert any("WindowManager" in record.text for record in records)
    assert any("ActivityManager" in record.text for record in records)


def test_loghub_unfamiliar_formats_fall_back_without_false_mobile_detection() -> None:
    # HealthApp and Apache are deliberately different from Android threadtime /
    # iOS syslog.  A Mobile extension should safely classify them as generic
    # raw text instead of pretending it understands their domain semantics.
    assert detect_segmenter_kind(_sample("HealthApp_2k.log")) == "rawtext"
    assert detect_segmenter_kind(_sample("Apache_2k.log")) == "rawtext"

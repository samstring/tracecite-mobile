"""具体文本格式分段器（真机日志、线上日志、混合格式）。"""

import json
import re
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from tracecite_core.segmenter import JsonLineSegmenter, RawTextSegmenter, Record, Segmenter
from tracecite_mobile.plugins.processor import (
    MAX_INCOMPLETE_LINES,
    continues_previous_record,
    extract_pid,
    is_syslog_header,
    looks_incomplete,
)

# ---------- 格式特定正则 ----------

SYSLOG_TS_RE = re.compile(
    r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+"
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})(?:\.(?P<frac>\d+))?"
)
THREADTIME_TS_RE = re.compile(
    r"^(?P<mon>\d{2})-(?P<day>\d{2})\s+"
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})(?:\.(?P<frac>\d+))?"
)
_THREADTIME_HEADER_RE = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+"
)
APPLOG_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
)
APPLOG_HEAD_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"\s+(?:(?P<thread>\S+)\s+)?(?P<level>[VDIWEF])\s+"
    r"(?P<tag>[^:]+?)\s*:\s*"
)
_APPLOG_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
DEVICE_HEADER_BODY_RE = re.compile(
    r"^(?:\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+\d+\s+\d+\s+"
    r"[VDIWEF]\s+\S+\s*:\s*|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?\s+\S+\s+<[^>]+>\s*:?\s*)"
)
APPLOG_HEADER_BODY_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[^:]+?:\s*"
)
MIXED_HEADER_BODY_RE = re.compile(
    f"(?:{DEVICE_HEADER_BODY_RE.pattern}|{APPLOG_HEADER_BODY_RE.pattern})"
)


def _nearest_year_datetime(match: re.Match, *, reference: datetime) -> Optional[datetime]:
    month_raw = match.group("mon")
    month = int(month_raw) if month_raw.isdigit() else _MONTHS.get(month_raw, 0)
    fraction = (match.groupdict().get("frac") or "")[:6].ljust(6, "0")
    candidates: List[datetime] = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(
                datetime(
                    year,
                    month,
                    int(match.group("day")),
                    int(match.group("h")),
                    int(match.group("m")),
                    int(match.group("s")),
                    int(fraction or "0"),
                )
            )
        except ValueError:
            continue
    return min(candidates, key=lambda value: abs(value - reference)) if candidates else None


def _device_timestamp(raw: str, *, reference: datetime) -> Optional[datetime]:
    match = SYSLOG_TS_RE.match(raw) or THREADTIME_TS_RE.match(raw)
    return _nearest_year_datetime(match, reference=reference) if match else None


class DeviceLogSegmenter(Segmenter):
    """iOS syslog / Android threadtime，多行续行按 PID 合并。"""

    name = "devicelog"

    @property
    def header_strip_re(self) -> re.Pattern:
        return DEVICE_HEADER_BODY_RE

    @staticmethod
    def is_header(line: str) -> bool:
        return is_syslog_header(line) or bool(_THREADTIME_HEADER_RE.match(line))

    def segment_lines(self, lines: Iterator[Tuple[int, str]]) -> Iterator[Record]:
        pending: List[Tuple[int, str]] = []
        for line_number, line in lines:
            if self.is_header(line):
                if pending:
                    previous_pid = extract_pid(pending[0][1])
                    current_pid = extract_pid(line)
                    merged = "".join(item for _, item in pending)
                    if (
                        previous_pid
                        and current_pid
                        and previous_pid == current_pid
                        and continues_previous_record(merged, line)
                    ):
                        pending.append((line_number, line))
                        continue
                    yield self._build(pending)
                    pending = []
                pending.append((line_number, line))
            else:
                pending.append((line_number, line))
        if pending:
            yield self._build(pending)

    def record_timestamp(
        self,
        record: Record,
        *,
        reference: datetime,
    ) -> Optional[datetime]:
        if record.timestamp is not None:
            return record.timestamp
        return _device_timestamp(record.text.split("\n", 1)[0], reference=reference)

    def parse_time_argument(
        self,
        raw: str,
        *,
        reference: datetime,
    ) -> Optional[datetime]:
        return _device_timestamp(raw.strip(), reference=reference)

    @staticmethod
    def _build(pending: List[Tuple[int, str]]) -> Record:
        fields: Dict[str, Any] = {}
        pid = extract_pid(pending[0][1])
        if pid:
            fields["pid"] = pid
        return Record(
            text="".join(line for _, line in pending),
            start_line=pending[0][0],
            end_line=pending[-1][0],
            fields=fields,
        )


class AppLogSegmenter(Segmenter):
    """线上日志分段：YYYY-MM-DD HH:MM:SS.mmm 时间戳开头 + 多行块合并。"""

    name = "applog"

    @property
    def header_strip_re(self) -> re.Pattern:
        return APPLOG_HEADER_BODY_RE

    def segment_lines(self, lines: Iterator[Tuple[int, str]]) -> Iterator[Record]:
        pending: List[Tuple[int, str]] = []
        in_block = False
        for line_number, line in lines:
            m = APPLOG_HEAD_RE.match(line)
            if m:
                if pending:
                    yield self._build(pending)
                    pending.clear()
                in_block = looks_incomplete(line)
                pending.append((line_number, line))
            elif in_block:
                pending.append((line_number, line))
                merged = "\n".join(l for _, l in pending)
                if not looks_incomplete(merged) or len(pending) >= MAX_INCOMPLETE_LINES:
                    in_block = False
            else:
                pending.append((line_number, line))
        if pending:
            yield self._build(pending)

    @staticmethod
    def _build(pending: List[Tuple[int, str]]) -> Record:
        text = "".join(line for _, line in pending)
        ts = None
        first = pending[0][1]
        m = APPLOG_TS_RE.search(first)
        if m:
            for fmt in _APPLOG_TS_FORMATS:
                try:
                    ts = datetime.strptime(m.group().strip(), fmt)
                    break
                except ValueError:
                    continue
        return Record(text=text, start_line=pending[0][0], end_line=pending[-1][0], timestamp=ts)


class MixedLogSegmenter(Segmenter):
    """Route contiguous sections of one file to their matching segmenter."""

    name = "mixed"

    def __init__(self) -> None:
        self._device = DeviceLogSegmenter()
        self._app = AppLogSegmenter()
        self._json = JsonLineSegmenter()
        self._raw = RawTextSegmenter()

    @property
    def header_strip_re(self) -> re.Pattern:
        return MIXED_HEADER_BODY_RE

    @staticmethod
    def _kind(line: str) -> Optional[str]:
        if APPLOG_HEAD_RE.match(line):
            return "applog"
        if DeviceLogSegmenter.is_header(line):
            return "devicelog"
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return "jsonline" if isinstance(json.loads(stripped), dict) else None
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    def _segmenter(self, kind: str) -> Segmenter:
        return {
            "applog": self._app,
            "devicelog": self._device,
            "jsonline": self._json,
            "rawtext": self._raw,
        }[kind]

    def segment_lines(self, lines: Iterator[Tuple[int, str]]) -> Iterator[Record]:
        pending: List[Tuple[int, str]] = []
        pending_kind: Optional[str] = None

        def flush() -> Iterator[Record]:
            nonlocal pending, pending_kind
            if not pending:
                return iter(())
            selected = self._segmenter(pending_kind or "rawtext")
            batch = pending
            pending = []
            pending_kind = None
            return selected.segment_lines(iter(batch))

        for line_number, line in lines:
            kind = self._kind(line)
            if kind == "jsonline":
                yield from flush()
                yield from self._json.segment_lines(iter(((line_number, line),)))
                continue
            if kind is not None and pending and kind != pending_kind:
                yield from flush()
            if not pending:
                pending_kind = kind or "rawtext"
            pending.append((line_number, line))
        yield from flush()

    def record_timestamp(
        self,
        record: Record,
        *,
        reference: datetime,
    ) -> Optional[datetime]:
        if record.timestamp is not None:
            return record.timestamp
        first = record.text.split("\n", 1)[0]
        if DeviceLogSegmenter.is_header(first):
            return self._device.record_timestamp(record, reference=reference)
        return None

    def parse_time_argument(
        self,
        raw: str,
        *,
        reference: datetime,
    ) -> Optional[datetime]:
        return self._device.parse_time_argument(raw, reference=reference)


# Public registration is performed by tracecite_mobile.extension.register().


def detect_segmenter_kind(path, *, sample_lines=200):
    """嗅探文件格式；多种记录头显著共存时返回 mixed。"""
    dh = ah = jh = s = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for l in f:
            s += 1
            if s > sample_lines:
                break
            if DeviceLogSegmenter.is_header(l):
                dh += 1
            if APPLOG_HEAD_RE.match(l):
                ah += 1
            stripped = l.strip()
            if stripped.startswith("{"):
                try:
                    if isinstance(json.loads(stripped), dict):
                        jh += 1
                except (json.JSONDecodeError, TypeError):
                    pass
    counts = sorted((dh, ah, jh), reverse=True)
    recognised = sum(counts)
    if counts[1] >= 2 and recognised and counts[1] / recognised >= 0.10:
        return "mixed"
    if dh >= ah and dh > 0:
        return "devicelog"
    if ah > dh and ah > 0:
        return "applog"
    if jh >= max(1, s // 2):
        return "jsonline"
    return "rawtext"

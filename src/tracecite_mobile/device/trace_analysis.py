# -*- coding: utf-8 -*-
"""Instruments trace 导出与 hang 分析。"""

from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class TraceDataError(Exception):
    """trace 导出数据存在但不可解析。"""


@dataclass
class HangEvent:
    """potential-hangs 表中的一条记录。"""

    start: str = ""
    duration: str = ""
    hang_type: str = ""
    thread: str = ""
    process: str = ""

    def summary(self) -> str:
        parts = [p for p in (self.duration, self.hang_type, self.thread, self.process) if p]
        return " · ".join(parts) if parts else "hang detected"

    def to_dict(self) -> Dict[str, str]:
        return {
            "start": self.start,
            "duration": self.duration,
            "hang_type": self.hang_type,
            "thread": self.thread,
            "process": self.process,
        }


@dataclass
class HangRiskEvent:
    """hang-risks 表中的一条记录。"""

    time: str = ""
    process: str = ""
    message: str = ""
    severity: str = ""
    event_type: str = ""
    thread: str = ""

    def summary(self) -> str:
        parts = [p for p in (self.severity, self.message, self.thread) if p]
        return " · ".join(parts) if parts else "hang risk detected"

    def to_dict(self) -> Dict[str, str]:
        return {
            "time": self.time,
            "process": self.process,
            "message": self.message,
            "severity": self.severity,
            "event_type": self.event_type,
            "thread": self.thread,
        }


@dataclass
class TraceAnalysis:
    """单次 trace 分析结果。"""

    trace_path: Path
    toc_path: Optional[Path] = None
    hangs_path: Optional[Path] = None
    hang_risks_path: Optional[Path] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    duration_seconds: Optional[float] = None
    hangs: List[HangEvent] = field(default_factory=list)
    hang_risks: List[HangRiskEvent] = field(default_factory=list)
    data_errors: List[str] = field(default_factory=list)

    @property
    def has_issue(self) -> bool:
        return bool(self.hangs or self.hang_risks)

    def issue_lines(self) -> List[str]:
        lines: List[str] = []
        for hang in self.hangs:
            lines.append(f"hang: {hang.summary()}")
        for risk in self.hang_risks:
            lines.append(f"hang-risk: {risk.summary()}")
        return lines

    def to_dict(self) -> Dict[str, object]:
        return {
            "trace_path": str(self.trace_path),
            "toc_path": str(self.toc_path) if self.toc_path else None,
            "hangs_path": str(self.hangs_path) if self.hangs_path else None,
            "hang_risks_path": str(self.hang_risks_path) if self.hang_risks_path else None,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "duration_seconds": self.duration_seconds,
            "hangs": [hang.to_dict() for hang in self.hangs],
            "hang_risks": [risk.to_dict() for risk in self.hang_risks],
            "has_issue": self.has_issue,
            "data_errors": list(self.data_errors),
        }


def export_toc(trace_path: Path, toc_path: Path) -> bool:
    result = subprocess.run(
        [
            "xcrun", "xctrace", "export",
            "--input", str(trace_path),
            "--toc",
            "--output", str(toc_path),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and toc_path.is_file()


def export_xpath(trace_path: Path, schema: str, output_path: Path) -> bool:
    xpath = f'/trace-toc/run[@number="1"]/data/table[@schema="{schema}"]'
    for attempt in range(3):
        result = subprocess.run(
            [
                "xcrun", "xctrace", "export",
                "--input", str(trace_path),
                "--xpath", xpath,
                "--output", str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if (
            result.returncode == 0
            and output_path.is_file()
            and output_path.stat().st_size > 0
        ):
            return True
        if attempt < 2:
            time.sleep(0.5)
    if output_path.is_file() and output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
    return False


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _row_to_dict(row: ET.Element) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for child in row:
        key = _local_tag(child.tag)
        text = (child.text or "").strip()
        if text:
            data[key] = text
        for sub in child:
            sub_key = _local_tag(sub.tag)
            sub_text = (sub.text or "").strip()
            if sub_text:
                data[sub_key] = sub_text
    return data


def _parse_root(path: Path) -> Optional[ET.Element]:
    """空/缺失文件视为无数据；损坏文件必须报错，否则会得出错误阴性结论。"""
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise TraceDataError(f"{path.name} 不是可解析的 XML: {exc}") from exc


def _parse_hangs_xml(path: Path) -> List[HangEvent]:
    root = _parse_root(path)
    if root is None:
        return []
    events: List[HangEvent] = []
    for row in root.iter():
        if _local_tag(row.tag) != "row":
            continue
        data = _row_to_dict(row)
        events.append(
            HangEvent(
                start=data.get("start", data.get("start-time", "")),
                duration=data.get("duration", ""),
                hang_type=data.get("hang-type", ""),
                thread=data.get("thread", ""),
                process=data.get("process", ""),
            )
        )
    return events


def _parse_hang_risks_xml(path: Path) -> List[HangRiskEvent]:
    root = _parse_root(path)
    if root is None:
        return []
    events: List[HangRiskEvent] = []
    for row in root.iter():
        if _local_tag(row.tag) != "row":
            continue
        data = _row_to_dict(row)
        events.append(
            HangRiskEvent(
                time=data.get("time", data.get("timestamp", "")),
                process=data.get("process", ""),
                message=data.get("message", ""),
                severity=data.get("severity", ""),
                event_type=data.get("event-type", ""),
                thread=data.get("thread", ""),
            )
        )
    return events


def _parse_toc_timing(toc_path: Optional[Path]) -> tuple[Optional[str], Optional[str], Optional[float]]:
    if toc_path is None:
        return None, None, None
    root = _parse_root(toc_path)
    if root is None:
        return None, None, None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    duration_seconds: Optional[float] = None
    for elem in root.iter():
        tag = _local_tag(elem.tag)
        text = (elem.text or "").strip()
        if not text:
            continue
        if tag == "start-date":
            start_date = text
        elif tag == "end-date":
            end_date = text
        elif tag == "duration":
            try:
                duration_seconds = float(text)
            except ValueError:
                pass
    return start_date, end_date, duration_seconds


def analyze_trace(
    trace_path: Path,
    *,
    toc_path: Optional[Path] = None,
    hangs_path: Optional[Path] = None,
    hang_risks_path: Optional[Path] = None,
    export_missing: bool = True,
) -> TraceAnalysis:
    """导出并解析 hang / hang-risks，返回分析结果。"""
    resolved_toc = toc_path
    resolved_hangs = hangs_path
    resolved_risks = hang_risks_path

    data_errors: List[str] = []

    if export_missing:
        if resolved_toc is None:
            resolved_toc = trace_path.with_name(f"{trace_path.stem}_toc.xml")
        if not resolved_toc.is_file() and not export_toc(trace_path, resolved_toc):
            data_errors.append(f"toc 导出失败: {resolved_toc.name}")

        if resolved_hangs is None:
            resolved_hangs = trace_path.with_name(f"{trace_path.stem}_hangs.xml")
        if not resolved_hangs.is_file() and not export_xpath(
            trace_path, "potential-hangs", resolved_hangs
        ):
            # 导出失败与「确实没有 hang」不可区分，必须标记而不是当成阴性
            data_errors.append("potential-hangs 数据未导出成功")

        if resolved_risks is None:
            resolved_risks = trace_path.with_name(f"{trace_path.stem}_hang_risks.xml")
        if not resolved_risks.is_file() and not export_xpath(
            trace_path, "hang-risks", resolved_risks
        ):
            data_errors.append("hang-risks 数据未导出成功")

    try:
        start_date, end_date, duration_seconds = _parse_toc_timing(resolved_toc)
    except TraceDataError as exc:
        start_date = end_date = None
        duration_seconds = None
        data_errors.append(str(exc))
    try:
        hangs = _parse_hangs_xml(resolved_hangs) if resolved_hangs else []
    except TraceDataError as exc:
        hangs = []
        data_errors.append(str(exc))
    try:
        hang_risks = _parse_hang_risks_xml(resolved_risks) if resolved_risks else []
    except TraceDataError as exc:
        hang_risks = []
        data_errors.append(str(exc))

    return TraceAnalysis(
        trace_path=trace_path,
        toc_path=resolved_toc if resolved_toc and resolved_toc.is_file() else None,
        hangs_path=resolved_hangs if resolved_hangs and resolved_hangs.is_file() else None,
        hang_risks_path=resolved_risks if resolved_risks and resolved_risks.is_file() else None,
        start_date=start_date,
        end_date=end_date,
        duration_seconds=duration_seconds,
        hangs=hangs,
        hang_risks=hang_risks,
        data_errors=data_errors,
    )


def format_analysis_summary(analysis: TraceAnalysis, log_path: Optional[Path] = None) -> str:
    """生成人类可读的分析摘要。"""
    lines: List[str] = []
    if analysis.start_date and analysis.end_date:
        lines.append(f"时段: {analysis.start_date} ~ {analysis.end_date}")
    elif analysis.duration_seconds is not None:
        lines.append(f"采样时长: {analysis.duration_seconds:.1f}s")

    if analysis.data_errors:
        lines.append("结论: 数据不可解析，无法判断 hang / hang-risk")
        lines.extend(f"  - {item}" for item in analysis.data_errors)
        lines.append("请重试 capture stop 或手动 xctrace export 后再判断。")
        if not analysis.has_issue:
            return "\n".join(lines)

    if not analysis.has_issue:
        lines.append("结论: 未检测到 hang / hang-risk")
        return "\n".join(lines)

    lines.append(f"结论: 检测到 {len(analysis.hangs)} 次 hang，{len(analysis.hang_risks)} 条 hang-risk")
    lines.extend(f"  - {item}" for item in analysis.issue_lines())
    if log_path is not None:
        lines.append(f"建议结合日志: {log_path.resolve()}")
        if analysis.start_date:
            lines.append(f"  日志窗口: {analysis.start_date} ~ {analysis.end_date or '（见 toc）'}")
    return "\n".join(lines)

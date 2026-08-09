# -*- coding: utf-8 -*-
"""
处理 idevicesyslog 输出：
1. 合并无 syslog 头的续行
2. 合并同 PID 且上一条未闭合的多行日志（含带头的续行）
3. 按进程名 / subsystem 过滤
4. UTF-8 输出
"""

import re
import sys
from typing import BinaryIO, Optional, TextIO

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
# 单条结构化记录最大物理行数，防止括号计数误判后无限吞并。
MAX_INCOMPLETE_LINES = 100
SYSLOG_HEADER_RE = re.compile(
    r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+"
)
PID_RE = re.compile(r"\[(\d+)\]")
LEVEL_PREFIX_RE = re.compile(r"<[^>]+>:\s")
CATV_MARK_RE = re.compile(r"\\M")


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def decode_catv(text: str) -> str:
    """将 BSD cat -v / vis 转义还原为 UTF-8 文本。"""
    if not CATV_MARK_RE.search(text):
        return text

    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] == "M":
            i += 2
            if i < n and text[i] == "-":
                i += 1
            if i < n and text[i] == "^":
                i += 1
                if i < n:
                    out.append((ord(text[i]) ^ 0x40) | 0x80)
                    i += 1
            elif i < n:
                out.append(ord(text[i]) | 0x80)
                i += 1
        elif ch == "\\" and i + 1 < n and text[i + 1] == "^" and i + 2 < n:
            i += 2
            out.append(ord(text[i]) ^ 0x40)
            i += 1
        elif ch == "\\" and i + 1 < n and text[i + 1] == "\\":
            out.append(ord("\\"))
            i += 2
        elif ch == "\\" and i + 1 < n and text[i + 1] == "n":
            out.append(ord("\n"))
            i += 2
        elif ch == "\\" and i + 1 < n and text[i + 1] == "t":
            out.append(ord("\t"))
            i += 2
        elif ch == "\\" and i + 1 < n and text[i + 1] == "r":
            out.append(ord("\r"))
            i += 2
        else:
            out.append(ord(ch))
            i += 1

    return out.decode("utf-8", errors="replace")


def normalize_line(raw) -> str:
    if isinstance(raw, bytes):
        line = raw.decode("utf-8", errors="surrogateescape")
    else:
        line = raw
    if CATV_MARK_RE.search(line):
        line = decode_catv(line)
    return line


def subsystem_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"\(" + re.escape(name) + r"\)")


def process_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"\s" + re.escape(name) + r"(?:[\[(<]|$)")


def is_syslog_header(line: str) -> bool:
    return bool(SYSLOG_HEADER_RE.match(strip_ansi(line)))


def extract_pid(line: str) -> Optional[str]:
    match = PID_RE.search(line)
    return match.group(1) if match else None


def message_body(line: str) -> str:
    plain = strip_ansi(line)
    match = LEVEL_PREFIX_RE.search(plain)
    if match:
        return plain[match.end():]
    return plain


def looks_incomplete(record: str) -> bool:
    """Return whether a generic bracket-delimited record is incomplete."""
    text = record.rstrip("\n")
    if not text:
        return False

    if text.count("\n") >= MAX_INCOMPLETE_LINES:
        return False

    first = text.split("\n", 1)[0].strip()
    structured_start = first.rstrip().endswith("{") or first.rstrip().endswith("[")
    if not structured_start:
        return False

    if text.count("{") > text.count("}"):
        return True
    if text.count("[") > text.count("]"):
        return True
    if text.count("(") > text.count(")"):
        return True

    tail = text.split("\n")[-1].rstrip()
    if not tail:
        return False

    body = message_body(tail) if is_syslog_header(tail) else tail
    body = body.rstrip()
    if not body:
        return False

    return body[-1] in "{[(,\\:"


def is_continuation_header(line: str) -> bool:
    body = message_body(line).lstrip()
    if not body:
        return True
    first = body[0]
    if first in ('"', "}", "]", ")", "+", "-", ".", "\\"):
        return True
    if first.isdigit():
        return True
    return first.isspace()


def continues_previous_record(previous_record: str, header_line: str) -> bool:
    """同 PID 下是否应把新头行并入上一条：上一条未闭合，或新头行本身像续行。

    stream 写入与 filter 离线二次合并必须用同一判定，否则 record 边界会不一致。
    """
    return looks_incomplete(previous_record) or is_continuation_header(header_line)


def should_keep(record: str, process_pat, subsystem_pat) -> bool:
    if process_pat and not process_pat.search(record):
        return False
    if subsystem_pat and not subsystem_pat.search(record):
        return False
    return True


def process_stream(
    in_stream: BinaryIO,
    out_stream: TextIO,
    process_name: Optional[str] = None,
    subsystem_filter: Optional[str] = None,
) -> None:
    process_pat = None
    if process_name and process_name not in ("all", "-"):
        process_pat = process_pattern(process_name)

    subsystem_pat = None
    if subsystem_filter and subsystem_filter not in ("all", "-"):
        subsystem_pat = subsystem_pattern(subsystem_filter)

    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        record = "".join(pending)
        if should_keep(record, process_pat, subsystem_pat):
            out_stream.write(record)
            if not record.endswith("\n"):
                out_stream.write("\n")
            out_stream.flush()
        pending.clear()

    while True:
        raw = in_stream.readline()
        if not raw:
            flush()
            break

        line = normalize_line(raw)

        if is_syslog_header(line):
            if pending:
                prev_pid = extract_pid(pending[0])
                cur_pid = extract_pid(line)
                merged = "".join(pending)
                same_pid = prev_pid and cur_pid and prev_pid == cur_pid
                if same_pid and continues_previous_record(merged, line):
                    pending.append(line)
                    continue
            flush()
            pending.append(line)
        elif pending:
            pending.append(line)
        else:
            pending.append(line)

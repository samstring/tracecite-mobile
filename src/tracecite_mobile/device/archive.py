# -*- coding: utf-8 -*-
"""长监听 hot 窗口：>N 分钟的日志 rewind 到 .archive，需要时再 pull 拼窗。"""

from __future__ import annotations

import re
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

from ..shared.constants import (
    ARCHIVE_DIRNAME,
    ARCHIVE_MANIFEST_FILENAME,
    ARCHIVE_PULLED_DIRNAME,
    DEFAULT_ARCHIVE_INTERVAL_SEC,
    DEFAULT_HOT_WINDOW_SEC,
    LEGACY_ARCHIVE_DIRNAME,
)
from tracecite_core.text_filter import (
    FilterError,
    parse_time_arg,
    record_timestamp,
    reference_datetime,
)
from tracecite_core.state_file import atomic_write_json, read_json
from ..plugins.segmenters import DeviceLogSegmenter


_DEVICE_SEGMENTER = DeviceLogSegmenter()

# New writes live below a hidden directory so archive implementation details
# stay out of the user's normal log listing.  Keep reading the historical
# visible ``archive/`` tree; callers may have long-lived manifests there.
_CANONICAL_ARCHIVE_DIRNAME = ARCHIVE_DIRNAME
_LEGACY_ARCHIVE_DIRNAME = LEGACY_ARCHIVE_DIRNAME


class ArchiveError(RuntimeError):
    pass


def _log_rotate_failure(hot_path: Path, exc: Exception) -> None:
    """记录一次 rotate 失败，不抛出（不打断采集）。"""
    import sys

    try:
        print(
            f"[rotate] 归档失败（已跳过，采集继续）: {hot_path.name}: {exc!r}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass


def cleanup_rotate_tmp(hot_path: Path) -> None:
    """清理 hot 日志的同名 .rotate.tmp 残留（上次异常退出留下的中间文件）。"""
    try:
        tmp = hot_path.with_name(f".{hot_path.name}.rotate.tmp")
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_")


@dataclass
class ArchiveSegment:
    start: str
    end: str
    path: str
    bytes: int
    lines: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "path": self.path,
            "bytes": self.bytes,
            "lines": self.lines,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArchiveSegment":
        return cls(
            start=str(data["start"]),
            end=str(data["end"]),
            path=str(data["path"]),
            bytes=int(data.get("bytes", 0)),
            lines=int(data.get("lines", 0)),
        )


@dataclass
class RotateResult:
    rotated: bool
    cutoff: Optional[str]
    last_ts: Optional[str]
    archived: List[ArchiveSegment]
    hot_path: str
    hot_lines: int
    hot_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rotated": self.rotated,
            "cutoff": self.cutoff,
            "last_ts": self.last_ts,
            "archived": [s.to_dict() for s in self.archived],
            "hot_path": self.hot_path,
            "hot_lines": self.hot_lines,
            "hot_bytes": self.hot_bytes,
        }


@dataclass
class PullResult:
    output_path: Path
    segments: List[str]
    time_from: str
    time_to: str
    lines: int
    bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "segments": self.segments,
            "time_from": self.time_from,
            "time_to": self.time_to,
            "lines": self.lines,
            "bytes": self.bytes,
        }


def archive_device_dir(log_output_dir: Path, device_name: str) -> Path:
    safe = _safe_name(device_name) or "device"
    return _canonical_archive_root(log_output_dir) / safe


def _canonical_archive_root(log_output_dir: Path) -> Path:
    return log_output_dir.expanduser().resolve() / _CANONICAL_ARCHIVE_DIRNAME


def _legacy_archive_root(log_output_dir: Path) -> Path:
    return log_output_dir.expanduser().resolve() / _LEGACY_ARCHIVE_DIRNAME


def _archive_roots(log_output_dir: Path) -> List[Path]:
    """Return canonical-first archive roots for read operations."""
    canonical = _canonical_archive_root(log_output_dir)
    legacy = _legacy_archive_root(log_output_dir)
    return [canonical] if canonical == legacy else [canonical, legacy]


def _archive_segments(log_output_dir: Path, device_name: str) -> List[ArchiveSegment]:
    """Load canonical and legacy manifests, preferring canonical duplicates."""
    safe = _safe_name(device_name) or "device"
    merged: List[ArchiveSegment] = []
    seen: set[Tuple[str, str, int, int]] = set()
    for root in _archive_roots(log_output_dir):
        device_dir = root / safe
        if not device_dir.is_dir():
            continue
        for segment in load_manifest(device_dir):
            # A segment copied from legacy into the canonical tree should not
            # appear twice. Include size/line count so distinct same-time
            # segments (possible after a filename collision) remain visible.
            key = (segment.start, segment.end, segment.bytes, segment.lines)
            if key in seen:
                continue
            seen.add(key)
            merged.append(segment)
    merged.sort(key=lambda segment: (segment.start, segment.end, segment.path))
    return merged


def _archive_source_dir(log_output_dir: Path, device_name: str) -> Path:
    """Return the manifest-bearing directory, preferring canonical storage."""
    safe = _safe_name(device_name) or "device"
    canonical = _canonical_archive_root(log_output_dir) / safe
    legacy = _legacy_archive_root(log_output_dir) / safe
    if manifest_path(canonical).is_file() or not manifest_path(legacy).is_file():
        return canonical if canonical.is_dir() else legacy
    return legacy


def manifest_path(device_archive_dir: Path) -> Path:
    return device_archive_dir / ARCHIVE_MANIFEST_FILENAME


def load_manifest(device_archive_dir: Path) -> List[ArchiveSegment]:
    path = manifest_path(device_archive_dir)
    if not path.is_file():
        return []
    try:
        data = read_json(path)
    except ValueError as exc:
        raise ArchiveError(str(exc)) from exc
    segments = data.get("segments") or []
    if not isinstance(segments, list):
        raise ArchiveError(f"manifest segments 必须是数组: {path}")
    return [ArchiveSegment.from_dict(item) for item in segments]


def save_manifest(device_archive_dir: Path, segments: List[ArchiveSegment]) -> None:
    device_archive_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        manifest_path(device_archive_dir),
        {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "segments": [s.to_dict() for s in segments],
        },
    )


def _stamp(ts: datetime) -> str:
    return ts.strftime("%Y%m%d_%H%M%S")


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _segment_overlaps(
    seg: ArchiveSegment,
    *,
    time_from: datetime,
    time_to: datetime,
) -> bool:
    start = _parse_iso(seg.start)
    end = _parse_iso(seg.end)
    return start < time_to and end > time_from


def list_archive_segments(
    log_output_dir: Path,
    *,
    device_name: Optional[str] = None,
) -> Dict[str, Any]:
    devices: Dict[str, Any] = {}
    names: set[str] = set()
    for root in _archive_roots(log_output_dir):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if (
                child.is_dir()
                and not child.name.startswith("_")
                and child.name != ARCHIVE_PULLED_DIRNAME
            ):
                names.add(child.name)

    wanted = (_safe_name(device_name) or device_name) if device_name else None
    for name in sorted(names):
        if wanted and name != wanted:
            continue
        segments = _archive_segments(log_output_dir, name)
        devices[name] = {
            "archive_dir": str(_archive_source_dir(log_output_dir, name)),
            "segments": [s.to_dict() for s in segments],
            "segment_count": len(segments),
        }
    return {"devices": devices}


def rotate_hot_log(
    hot_path: Path,
    *,
    device_name: str,
    hot_window_sec: int = DEFAULT_HOT_WINDOW_SEC,
    open_fp: Optional[TextIO] = None,
) -> RotateResult:
    """
    将 hot 日志中早于 (末条时间 - hot_window) 的记录 rewind 到 .archive。
    若传入 open_fp（stream 正在写），原地 truncate 重写后保持句柄可用。
    """
    path = hot_path.expanduser().resolve()
    if open_fp is not None:
        open_fp.flush()
    # 先清理上次异常退出可能残留的同名 rotate tmp，避免中间文件堆积
    cleanup_rotate_tmp(path)
    # 文件可能以 "w"/"a" 打开不可读：统一从磁盘读当前内容
    if not path.is_file():
        raise ArchiveError(f"hot 日志不存在: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")

    if not text.strip():
        return RotateResult(
            rotated=False,
            cutoff=None,
            last_ts=None,
            archived=[],
            hot_path=str(path),
            hot_lines=0,
            hot_bytes=0,
        )

    # 写临时文件供 record 迭代（与 filter 同一合并逻辑）
    tmp = path.with_name(f".{path.name}.rotate.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        ref = reference_datetime(tmp, segmenter=_DEVICE_SEGMENTER)
        records = list(_DEVICE_SEGMENTER.segment_file(tmp))
        last_ts: Optional[datetime] = None
        stamped: List[Tuple[Optional[datetime], str]] = []
        for record in records:
            ts = record_timestamp(record, ref=ref, segmenter=_DEVICE_SEGMENTER)
            if ts is not None:
                last_ts = ts
            stamped.append((ts, record.text if record.text.endswith("\n") else record.text + "\n"))
    finally:
        tmp.unlink(missing_ok=True)

    if last_ts is None:
        hot_lines = text.count("\n")
        return RotateResult(
            rotated=False,
            cutoff=None,
            last_ts=None,
            archived=[],
            hot_path=str(path),
            hot_lines=hot_lines,
            hot_bytes=len(text.encode("utf-8")),
        )

    cutoff = last_ts - timedelta(seconds=max(1, int(hot_window_sec)))
    archive_parts: List[str] = []
    hot_parts: List[str] = []
    archive_start: Optional[datetime] = None
    archive_end: Optional[datetime] = None

    for ts, chunk in stamped:
        # 无时间戳：跟当前桶（还在 archive 阶段则进 archive，否则进 hot）
        if ts is None:
            if archive_parts and not hot_parts:
                archive_parts.append(chunk)
            else:
                hot_parts.append(chunk)
            continue
        if ts < cutoff:
            archive_parts.append(chunk)
            archive_start = ts if archive_start is None else min(archive_start, ts)
            archive_end = ts if archive_end is None else max(archive_end, ts)
        else:
            hot_parts.append(chunk)

    if not archive_parts:
        hot_text = "".join(hot_parts) if hot_parts else text
        _rewrite_hot(path, hot_text, open_fp=open_fp)
        return RotateResult(
            rotated=False,
            cutoff=_iso(cutoff),
            last_ts=_iso(last_ts),
            archived=[],
            hot_path=str(path),
            hot_lines=hot_text.count("\n"),
            hot_bytes=len(hot_text.encode("utf-8")),
        )

    # archive 段右端用 cutoff（半开语义说明写在 manifest end=最后归档记录时间）
    assert archive_start is not None and archive_end is not None
    device_dir = archive_device_dir(path.parent, device_name)
    device_dir.mkdir(parents=True, exist_ok=True)
    seg_name = f"{_stamp(archive_start)}-{_stamp(archive_end)}.log"
    seg_path = device_dir / seg_name
    # 撞名则追加序号
    if seg_path.exists():
        n = 1
        while True:
            candidate = device_dir / f"{_stamp(archive_start)}-{_stamp(archive_end)}_{n}.log"
            if not candidate.exists():
                seg_path = candidate
                break
            n += 1

    archive_text = "".join(archive_parts)
    seg_path.write_text(archive_text, encoding="utf-8")
    segment = ArchiveSegment(
        start=_iso(archive_start),
        end=_iso(archive_end),
        path=str(seg_path),
        bytes=len(archive_text.encode("utf-8")),
        lines=archive_text.count("\n"),
    )
    segments = load_manifest(device_dir)
    segments.append(segment)
    segments.sort(key=lambda s: s.start)
    save_manifest(device_dir, segments)

    hot_text = "".join(hot_parts)
    _rewrite_hot(path, hot_text, open_fp=open_fp)

    return RotateResult(
        rotated=True,
        cutoff=_iso(cutoff),
        last_ts=_iso(last_ts),
        archived=[segment],
        hot_path=str(path),
        hot_lines=hot_text.count("\n"),
        hot_bytes=len(hot_text.encode("utf-8")),
    )


def _rewrite_hot(path: Path, hot_text: str, *, open_fp: Optional[TextIO]) -> None:
    if open_fp is not None:
        open_fp.seek(0)
        open_fp.truncate()
        open_fp.write(hot_text)
        open_fp.flush()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(hot_text)
            handle.flush()
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def pull_archive_window(
    log_output_dir: Path,
    *,
    device_name: str,
    since: str,
    until: str,
    hot_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> PullResult:
    """
    按时间窗从 archive（+ 可选当前 hot）拼出只读临时文件，供 filter 使用。
    """
    root = log_output_dir.expanduser().resolve()
    if not since or not until:
        raise ArchiveError("archive pull 需要 --since 与 --until")

    ref = datetime.now()
    if hot_path and hot_path.expanduser().is_file():
        try:
            ref = reference_datetime(
                hot_path.expanduser().resolve(), segmenter=_DEVICE_SEGMENTER
            )
        except OSError:
            pass
    else:
        # 用 canonical 或 legacy 中最新 archive 段作 ref。
        segs = _archive_segments(root, device_name)
        if segs:
            ref = _parse_iso(segs[-1].end)

    try:
        time_from = parse_time_arg(since, ref=ref, segmenter=_DEVICE_SEGMENTER)
        time_to = parse_time_arg(until, ref=ref, segmenter=_DEVICE_SEGMENTER)
    except FilterError as exc:
        raise ArchiveError(str(exc)) from exc
    if time_from > time_to:
        raise ArchiveError(f"时间窗口无效: {since} > {until}")

    chunks: List[str] = []
    used: List[str] = []
    segments = _archive_segments(root, device_name)
    for seg in segments:
        if not _segment_overlaps(seg, time_from=time_from, time_to=time_to):
            continue
        seg_file = Path(seg.path)
        if not seg_file.is_file():
            continue
        chunk = _extract_time_window(seg_file, time_from=time_from, time_to=time_to)
        if chunk:
            chunks.append(chunk)
            used.append(str(seg_file))

    if hot_path is not None:
        hot = hot_path.expanduser().resolve()
        if hot.is_file():
            chunk = _extract_time_window(hot, time_from=time_from, time_to=time_to)
            if chunk:
                chunks.append(chunk)
                used.append(str(hot))

    if not chunks:
        raise ArchiveError(
            f"时间窗 { _iso(time_from) } → { _iso(time_to) } 无可用 archive/hot 片段"
            f"（设备 {device_name}）"
        )

    # Pulled windows are derived artifacts too; keep new ones under the hidden
    # canonical root while legacy archive segments remain readable above.
    pulled_dir = _canonical_archive_root(root) / ARCHIVE_PULLED_DIRNAME
    pulled_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(device_name) or "device"
    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        output_path = pulled_dir / f"pulled_{safe}_{_stamp(time_from)}-{_stamp(time_to)}_{stamp}.log"
    else:
        output_path = output_path.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

    body = "".join(chunks)
    header = (
        f"# tracecite-mobile archive pull\n"
        f"# device: {device_name}\n"
        f"# time_from: {_iso(time_from)}\n"
        f"# time_to: {_iso(time_to)}\n"
        f"# segments: {len(used)}\n"
        f"# ---\n"
    )
    output_path.write_text(header + body, encoding="utf-8")
    return PullResult(
        output_path=output_path,
        segments=used,
        time_from=_iso(time_from),
        time_to=_iso(time_to),
        lines=body.count("\n"),
        bytes=len(body.encode("utf-8")),
    )


def _extract_time_window(
    path: Path,
    *,
    time_from: datetime,
    time_to: datetime,
) -> str:
    ref = reference_datetime(path, segmenter=_DEVICE_SEGMENTER)
    parts: List[str] = []
    for record in _DEVICE_SEGMENTER.segment_file(path):
        ts = record_timestamp(record, ref=ref, segmenter=_DEVICE_SEGMENTER)
        if ts is None:
            # 无头续行：若已开始收集则保留
            if parts:
                parts.append(record.text if record.text.endswith("\n") else record.text + "\n")
            continue
        if time_from <= ts <= time_to:
            parts.append(record.text if record.text.endswith("\n") else record.text + "\n")
    return "".join(parts)


class HotRotatingWriter:
    """包装写盘，并用独立周期调度器 rewind hot。

    ``hot_window_sec`` 是归档保留窗口，``archive_interval_sec`` 是归档检查
    周期，两者是独立的概念。历史版本的 ``check_bytes`` 参数保留作兼容，
    但不再触发轮换；这样低流量采集不会因为短时间内写入少量字节而频繁
    检查归档。

    ``start_scheduler`` 后，即使没有新日志写入也会按周期检查。写入路径
    仍保留到期兜底，兼容未启动调度器的旧调用方。写入与轮换共用一把锁，
    避免后台轮换重写 hot 文件时采集器同时写入。一次检查无论成功还是
    抛错都会重置周期，失败会在下一个周期再次尝试。
    """

    def __init__(
        self,
        file_obj: TextIO,
        *,
        hot_path: Path,
        device_name: str,
        hot_window_sec: int = DEFAULT_HOT_WINDOW_SEC,
        archive_interval_sec: float = DEFAULT_ARCHIVE_INTERVAL_SEC,
        check_bytes: int = 256 * 1024,
        mirror: bool = False,
        mirror_stream: Optional[TextIO] = None,
        clock: Callable[[], float] = time.monotonic,
        scheduler_wait: Optional[Callable[[float], bool]] = None,
    ):
        self._file = file_obj
        self._hot_path = hot_path
        self._device_name = device_name
        self._hot_window_sec = hot_window_sec
        self._archive_interval_sec = max(0.0, float(archive_interval_sec))
        # ``check_bytes`` was the pre-interval trigger. Keep accepting it so
        # existing callers do not break, but deliberately do not use it for
        # scheduling anymore.
        self._legacy_check_bytes = max(1, int(check_bytes))
        self._clock = clock
        self._last_check_at = self._clock()
        self._mirror = mirror
        self._mirror_stream = mirror_stream
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._scheduler_wait = scheduler_wait or self._stop_event.wait
        self._scheduler_thread: Optional[threading.Thread] = None
        self._closed = False

    def write(self, data: str) -> None:
        with self._lock:
            if self._closed:
                raise ValueError("I/O operation on closed HotRotatingWriter")
            self._file.write(data)
            self._file.flush()
            if self._mirror and self._mirror_stream is not None:
                self._mirror_stream.write(data)
                self._mirror_stream.flush()
            if data:
                self._check_due_locked()

    def _check_due_locked(self, *, force: bool = False) -> bool:
        now = self._clock()
        if not force and now - self._last_check_at < self._archive_interval_sec:
            return False
        # Mark before rotate so a failure retries next period, never every line.
        self._last_check_at = now
        try:
            rotate_hot_log(
                self._hot_path,
                device_name=self._device_name,
                hot_window_sec=self._hot_window_sec,
                open_fp=self._file,
            )
        except Exception as exc:  # noqa: BLE001 - 任何归档异常都不能打断采集
            # 捕获所有 Exception（而非仅 ArchiveError/OSError），避免时间戳
            # 解析 ValueError / manifest KeyError 等冒泡导致 stream 进程崩溃。
            _log_rotate_failure(self._hot_path, exc)
        return True

    def check_due(self) -> bool:
        """Run one due check; useful to deterministic schedulers and tests."""
        with self._lock:
            if self._closed:
                return False
            return self._check_due_locked()

    def _scheduler_loop(self) -> None:
        while not self._scheduler_wait(self._archive_interval_sec):
            self.check_due()

    def start_scheduler(self) -> None:
        """Start the one-per-writer daemon scheduler idempotently."""
        with self._lock:
            if self._closed or self._scheduler_thread is not None:
                return
            thread = threading.Thread(
                target=self._scheduler_loop,
                name=f"tracecite-archive-{_safe_name(self._device_name) or 'device'}",
                daemon=True,
            )
            self._scheduler_thread = thread
            thread.start()

    # Compatibility with callers that use a generic lifecycle hook.
    start = start_scheduler

    def flush(self) -> None:
        """Flush bytes only; it never forces an archive check."""
        with self._lock:
            self._file.flush()
            if self._mirror and self._mirror_stream is not None:
                self._mirror_stream.flush()

    def close(self) -> None:
        """Stop scheduling without closing the caller-owned file handle."""
        self._stop_event.set()
        thread = self._scheduler_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, min(self._archive_interval_sec, 5.0)))
        with self._lock:
            if self._closed:
                return
            self.flush()
            self._closed = True

    def __enter__(self) -> "HotRotatingWriter":
        self.start_scheduler()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

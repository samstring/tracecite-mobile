# -*- coding: utf-8 -*-
"""清理历史日志、Instruments 与分析归档产物。"""

from __future__ import annotations

import shutil
import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Iterable, List, Optional


class CleanupError(Exception):
    """清理失败。"""


@dataclass
class CleanupItem:
    path: Path
    kind: str
    size_bytes: int
    modified_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
        }


@dataclass
class CleanupResult:
    cutoff: datetime
    dry_run: bool
    roots: List[Path]
    items: List[CleanupItem] = field(default_factory=list)

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.items)

    def to_dict(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff.isoformat(timespec="seconds"),
            "dry_run": self.dry_run,
            "roots": [str(root) for root in self.roots],
            "deleted_count": 0 if self.dry_run else len(self.items),
            "matched_count": len(self.items),
            "total_size_bytes": self.total_size_bytes,
            "items": [item.to_dict() for item in self.items],
        }


_PRESERVE_NAMES = {
    ".tracecite-session.json",
    ".tracecite-capture.json",
}


def parse_before(value: str, *, now: Optional[datetime] = None) -> datetime:
    """解析清理截止时间；today 表示今天 00:00 以前。"""
    now = now or datetime.now().astimezone()
    raw = value.strip().lower()
    if raw in {"today", "今天"}:
        return datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    if raw in {"yesterday", "昨天"}:
        return datetime.combine(now.date() - timedelta(days=1), time.min, tzinfo=now.tzinfo)
    try:
        parsed_date = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CleanupError("--before 仅支持 today/今天、yesterday/昨天 或 YYYY-MM-DD") from exc
    return datetime.combine(parsed_date, time.min, tzinfo=now.tzinfo)


def _path_size(path: Path) -> int:
    if path.is_dir():
        return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
    return path.stat().st_size


def _item_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def _contains_pinned_run(path: Path) -> bool:
    if not path.is_dir():
        return False
    manifests = [path / "manifest.json"]
    manifests.extend(path.rglob("manifest.json"))
    for manifest in manifests:
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if bool((payload.get("retention") or {}).get("pinned")):
            return True
    return False


def _active_artifact_paths(log_dir: Path, capture_dir: Path) -> set[Path]:
    """进行中 session / capture 正在写的文件，长跑时 mtime 会显得很旧。"""
    from .capture import load_capture_session
    from .session import load_all_sessions

    paths: set[Path] = set()
    try:
        streams = load_all_sessions(log_dir)
    except Exception:
        streams = {}
    for stream in streams.values():
        paths.update({Path(stream.output_path), Path(stream.stream_log_path)})
    try:
        capture = load_capture_session(capture_dir)
    except Exception:
        capture = None
    if capture is not None:
        paths.update(
            {
                Path(capture.trace_path),
                Path(capture.toc_path),
                Path(capture.xctrace_log),
            }
        )
    return {p.expanduser().resolve() for p in paths}


def _iter_cleanup_candidates(
    roots: Iterable[Path],
    cutoff: datetime,
    *,
    keep_paths: Optional[set[Path]] = None,
) -> Iterable[CleanupItem]:
    keep = keep_paths or set()
    for root in roots:
        if not root.exists():
            continue
        if not root.is_dir():
            raise CleanupError(f"清理根路径不是目录: {root}")
        for child in sorted(root.iterdir()):
            if child.name in _PRESERVE_NAMES:
                continue
            if child.expanduser().resolve() in keep:
                continue
            if _contains_pinned_run(child):
                continue
            try:
                modified = _item_mtime(child)
            except FileNotFoundError:
                continue
            if modified >= cutoff:
                continue
            kind = "dir" if child.is_dir() else "file"
            yield CleanupItem(
                path=child,
                kind=kind,
                size_bytes=_path_size(child),
                modified_at=modified.isoformat(timespec="seconds"),
            )


def clean_analysis_artifacts(
    *,
    log_dir: Path,
    capture_dir: Path,
    analysis_dir: Path,
    before: str = "today",
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> CleanupResult:
    """删除指定日期以前的日志、trace 与分析归档。"""
    cutoff = parse_before(before, now=now)
    roots = [
        log_dir.expanduser().resolve(),
        capture_dir.expanduser().resolve(),
        analysis_dir.expanduser().resolve(),
    ]
    result = CleanupResult(cutoff=cutoff, dry_run=dry_run, roots=roots)
    result.items.extend(
        _iter_cleanup_candidates(
            roots,
            cutoff,
            keep_paths=_active_artifact_paths(roots[0], roots[1]),
        )
    )

    if not dry_run:
        for item in result.items:
            if item.path.is_dir():
                shutil.rmtree(item.path)
            else:
                item.path.unlink()
    return result

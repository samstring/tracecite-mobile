# -*- coding: utf-8 -*-
"""清理历史日志、Instruments 与分析归档产物。"""

from __future__ import annotations

import shutil
import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, List, Optional

from ..shared.constants import (
    ARCHIVE_DIRNAME,
    LEGACY_ARCHIVE_DIRNAME,
    CAPTURE_STATE_FILENAME,
    SESSIONS_STATE_FILENAME,
)


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
    include_archive: bool = False

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.items)

    def to_dict(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff.isoformat(timespec="seconds"),
            "dry_run": self.dry_run,
            "include_archive": self.include_archive,
            "roots": [str(root) for root in self.roots],
            "deleted_count": 0 if self.dry_run else len(self.items),
            "matched_count": len(self.items),
            "total_size_bytes": self.total_size_bytes,
            "items": [item.to_dict() for item in self.items],
        }


_PRESERVE_NAMES = {
    ".tracecite-session.json",
    ".tracecite-sessions.json",
    ".tracecite-capture.json",
}

_RUNTIME_LOCK_SUFFIX = ".lock"
_ARCHIVE_LEGACY_NAME = "archive"
_RUN_CONTAINER_NAMES = (".runs", "runs")


@dataclass
class _RuntimeProtection:
    """Paths which a maintenance pass must not remove.

    A malformed runtime state makes the containing root unsafe to mutate.  We
    intentionally fail closed here: a stale-looking log next to a corrupt
    state file may still be owned by a collector whose identity cannot be
    established.
    """

    keep_paths: set[Path] = field(default_factory=set)
    unsafe_roots: set[Path] = field(default_factory=set)


def _archive_dir_names() -> set[str]:
    """Return canonical hidden and historical visible archive names."""
    raw = str(ARCHIVE_DIRNAME or "archive").strip().strip("/") or "archive"
    canonical = raw if raw.startswith(".") else f".{raw}"
    legacy = str(LEGACY_ARCHIVE_DIRNAME or raw.lstrip(".") or _ARCHIVE_LEGACY_NAME).strip().strip("/")
    legacy = legacy.lstrip(".") or _ARCHIVE_LEGACY_NAME
    return {canonical, legacy}


def _is_runtime_lock(path: Path) -> bool:
    # A lock may be held while its mtime is old.  Never unlink it from a
    # maintenance process; the next operation can safely reuse it.
    return path.name.endswith(_RUNTIME_LOCK_SUFFIX)


def _is_state_atomic_temp(path: Path) -> bool:
    """Keep an atomic-write staging file while its state/lock exists."""
    for name in _PRESERVE_NAMES:
        if path.name.startswith(f"{name}."):
            return (path.parent / name).exists() or (path.parent / f"{name}.lock").exists()
    return False


def _safe_resolve(raw: Any) -> Optional[Path]:
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        return None
    try:
        return Path(str(raw)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _state_rows(payload: dict[str, Any], name: str) -> Optional[list[dict[str, Any]]]:
    """Normalize iOS/Android single and aggregate session state rows."""
    if name == SESSIONS_STATE_FILENAME:
        rows = payload.get("sessions")
        if isinstance(rows, dict):
            rows = list(rows.values())
        if rows is None:
            # Canonical iOS/Android aggregate state always carries a sessions
            # collection.  Missing it is corruption, so do not guess that the
            # directory is idle.
            return None
        if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
            return None
        return [item for item in rows if isinstance(item, dict)]
    return [payload]


def _paths_from_runtime_row(row: dict[str, Any]) -> set[Path]:
    paths: set[Path] = set()
    # Both platform implementations use these stable names; unknown extra
    # paths are ignored rather than guessed.
    for key in (
        "output_path",
        "stream_log_path",
        "trace_path",
        "toc_path",
        "xctrace_log",
        "local_trace_path",
        "metadata_path",
        "summary_path",
        "archive_dir",
    ):
        path = _safe_resolve(row.get(key))
        if path is None:
            continue
        paths.add(path)
        if key != "archive_dir" and path.name:
            paths.add(path.with_name(path.name + ".heartbeat"))
            paths.add(path.with_name(f".{path.name}.rotate.tmp"))
    return paths


def _runtime_protection(log_dir: Path, capture_dir: Path) -> _RuntimeProtection:
    protection = _RuntimeProtection()
    state_roots = (
        (log_dir.expanduser().resolve(), {".tracecite-session.json", SESSIONS_STATE_FILENAME}),
        (capture_dir.expanduser().resolve(), {CAPTURE_STATE_FILENAME}),
    )
    for root, names in state_roots:
        for name in names:
            path = root / name
            if not path.exists():
                continue
            resolved = path.resolve()
            protection.keep_paths.add(resolved)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                protection.unsafe_roots.add(root)
                continue
            if not isinstance(payload, dict):
                protection.unsafe_roots.add(root)
                continue
            if name == CAPTURE_STATE_FILENAME:
                # Any path-bearing capture state is protected, including
                # stopping/recovery_required states.  An idle envelope is safe.
                row_paths = _paths_from_runtime_row(payload)
                if row_paths:
                    protection.keep_paths.update(row_paths)
                else:
                    # A persisted capture state is only written for a
                    # path-bearing running/stopping/recovery session.  An
                    # unknown shape cannot be safely treated as idle.
                    protection.unsafe_roots.add(root)
                continue
            rows = _state_rows(payload, name)
            if rows is None:
                protection.unsafe_roots.add(root)
                continue
            for row in rows:
                row_paths = _paths_from_runtime_row(row)
                # A row with an identity but no output path is not safe to
                # interpret as idle: preserve its entire root instead.
                if not row_paths and any(
                    row.get(key) not in (None, "", 0, False)
                    for key in ("pid", "collector_pid", "session_id", "serial", "device_udid")
                ):
                    protection.unsafe_roots.add(root)
                protection.keep_paths.update(row_paths)
    return protection


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


def _contains_protected_run(path: Path) -> bool:
    """Return whether an analysis run must be preserved.

    Running, pinned, unknown, and malformed manifests are all protected.  A
    cleanup command must never turn an interrupted run into an apparently
    complete run by deleting its evidence or temporary workspace.
    """
    if not path.is_dir():
        return False
    manifests = [path / "manifest.json"]
    manifests.extend(path.rglob("manifest.json"))
    found_manifest = False
    for manifest in manifests:
        if not manifest.is_file():
            continue
        found_manifest = True
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        if not isinstance(payload, dict):
            return True
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            return True
        if bool((payload.get("retention") or {}).get("pinned")):
            return True
    return False if found_manifest else False


def _contains_pinned_run(path: Path) -> bool:
    """Backward-compatible private helper retained for older callers."""
    return _contains_protected_run(path)


def _active_artifact_paths(log_dir: Path, capture_dir: Path) -> set[Path]:
    """进行中 session / capture 正在写的文件，长跑时 mtime 会显得很旧。"""
    return _runtime_protection(log_dir, capture_dir).keep_paths


def _iter_cleanup_candidates(
    roots: Iterable[Path],
    cutoff: datetime,
    *,
    keep_paths: Optional[set[Path]] = None,
    unsafe_roots: Optional[set[Path]] = None,
    skip_names: Optional[set[str]] = None,
) -> Iterable[CleanupItem]:
    keep = keep_paths or set()
    unsafe = {path.expanduser().resolve() for path in (unsafe_roots or set())}
    skipped = skip_names or set()
    for root in roots:
        root = root.expanduser().resolve()
        if any(root == unsafe_root or unsafe_root in root.parents for unsafe_root in unsafe):
            continue
        if not root.exists():
            continue
        if not root.is_dir():
            raise CleanupError(f"清理根路径不是目录: {root}")
        for child in sorted(root.iterdir()):
            if child.name in _PRESERVE_NAMES or child.name in skipped:
                continue
            if _is_runtime_lock(child):
                continue
            if _is_state_atomic_temp(child):
                continue
            if child.expanduser().resolve() in keep:
                continue
            if child.name == "manifest.json" and child.is_file():
                # A top-level malformed/active manifest is evidence, not a
                # disposable log.  Keep it conservatively.
                try:
                    payload = json.loads(child.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict) or str(payload.get("status") or "").lower() not in {
                        "completed",
                        "failed",
                    }:
                        continue
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
            if _contains_protected_run(child):
                continue
            try:
                modified = _item_mtime(child)
            except FileNotFoundError:
                continue
            if modified >= cutoff:
                continue
            kind = "dir" if child.is_dir() else "file"
            try:
                size_bytes = _path_size(child)
            except OSError:
                # A concurrent writer/remover changed the candidate while it
                # was being inspected; skip rather than guessing its size.
                continue
            yield CleanupItem(
                path=child,
                kind=kind,
                size_bytes=size_bytes,
                modified_at=modified.isoformat(timespec="seconds"),
            )


def _iter_archive_candidates(
    roots: Iterable[Path],
    cutoff: datetime,
    *,
    keep_paths: Optional[set[Path]] = None,
    unsafe_roots: Optional[set[Path]] = None,
) -> Iterable[CleanupItem]:
    """Enumerate archive trees only when explicitly requested.

    Archive manifests refer to segment files, so remove a device directory as
    one unit only when every descendant is older than the cutoff.  This avoids
    deleting a fresh segment merely because the directory mtime is stale.
    """
    keep = keep_paths or set()
    unsafe = {path.expanduser().resolve() for path in (unsafe_roots or set())}
    for root in roots:
        root = root.expanduser().resolve()
        if any(root == unsafe_root or unsafe_root in root.parents for unsafe_root in unsafe):
            continue
        if not root.exists():
            continue
        if not root.is_dir():
            raise CleanupError(f"归档根路径不是目录: {root}")
        for child in sorted(root.iterdir()):
            if child.expanduser().resolve() in keep or _is_runtime_lock(child):
                continue
            manifest = child / "manifest.json" if child.is_dir() else None
            if manifest is not None and manifest.exists():
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict) or not isinstance(payload.get("segments", []), list):
                        continue
                except (OSError, UnicodeError, json.JSONDecodeError):
                    # A damaged archive manifest is evidence whose references
                    # cannot be reconciled safely; fail closed.
                    continue
            try:
                descendants = [child, *child.rglob("*")] if child.is_dir() else [child]
                mtimes = [_item_mtime(item) for item in descendants if item.exists()]
            except (FileNotFoundError, OSError):
                continue
            if not mtimes or any(item.expanduser().resolve() in keep for item in descendants):
                continue
            if any(modified >= cutoff for modified in mtimes):
                continue
            modified = max(mtimes)
            try:
                size_bytes = _path_size(child)
            except OSError:
                continue
            yield CleanupItem(
                path=child,
                kind="dir" if child.is_dir() else "file",
                size_bytes=size_bytes,
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
    include_archive: bool = False,
    confirm_archive: bool = False,
    extra_analysis_dirs: Optional[Iterable[Path]] = None,
    extra_run_roots: Optional[Iterable[Path]] = None,
) -> CleanupResult:
    """删除指定日期以前的日志、trace 与分析产物。

    Archive segments are valuable evidence and are excluded by default.  A
    caller must opt in explicitly and provide a second confirmation token for
    a real deletion; dry-run may inspect them without confirmation.
    """
    if include_archive and not dry_run and not confirm_archive:
        raise CleanupError(
            "归档包含可复核证据；实际删除需要同时指定 --include-archive --yes，"
            "请先使用 --dry-run 预览。"
        )
    cutoff = parse_before(before, now=now)
    resolved_log_dir = log_dir.expanduser().resolve()
    resolved_capture_dir = capture_dir.expanduser().resolve()
    resolved_analysis_dir = analysis_dir.expanduser().resolve()
    runtime = _runtime_protection(resolved_log_dir, resolved_capture_dir)
    archive_names = _archive_dir_names()
    base_roots = [
        resolved_log_dir,
        resolved_capture_dir,
        resolved_analysis_dir,
    ]
    for extra in extra_analysis_dirs or ():
        resolved_extra = Path(extra).expanduser().resolve()
        if resolved_extra not in base_roots:
            base_roots.append(resolved_extra)
    run_roots: list[Path] = []
    candidate_roots = [
        *base_roots,
        *(Path(item).expanduser().resolve() for item in (extra_run_roots or ())),
    ]
    for root in candidate_roots:
        if root.name in _RUN_CONTAINER_NAMES and root in base_roots:
            continue
        for container_name in _RUN_CONTAINER_NAMES:
            candidate = root if root.name == container_name else root / container_name
            if candidate not in run_roots:
                run_roots.append(candidate)
    archive_roots: list[Path] = []
    if include_archive:
        archive_roots = [resolved_log_dir / name for name in sorted(archive_names)]
        # Keep the canonical hidden root first in user-visible output when
        # both names are present, while still reading legacy ``archive/``.
        archive_roots = [path for path in archive_roots if path.exists()]
    roots = [*base_roots, *archive_roots]
    result = CleanupResult(
        cutoff=cutoff,
        dry_run=dry_run,
        roots=roots,
        include_archive=include_archive,
    )
    result.items.extend(
        _iter_cleanup_candidates(
            base_roots,
            cutoff,
            keep_paths=runtime.keep_paths,
            unsafe_roots=runtime.unsafe_roots,
            skip_names={*archive_names, *_RUN_CONTAINER_NAMES},
        )
    )
    # A .runs container can contain a mix of running, pinned, malformed, and
    # completed run directories.  Never delete the container as one item;
    # inspect each run independently with the manifest guards above.
    result.items.extend(
        _iter_cleanup_candidates(
            run_roots,
            cutoff,
            keep_paths=runtime.keep_paths,
            unsafe_roots=runtime.unsafe_roots,
        )
    )
    if include_archive:
        result.items.extend(
            _iter_archive_candidates(
                archive_roots,
                cutoff,
                keep_paths=runtime.keep_paths,
                unsafe_roots=runtime.unsafe_roots,
            )
        )

    if not dry_run:
        for item in result.items:
            try:
                if item.path.is_dir():
                    shutil.rmtree(item.path)
                else:
                    item.path.unlink()
            except FileNotFoundError:
                # Another bounded cleanup/collector exit won the race; the
                # requested end state is already satisfied.
                continue
    return result

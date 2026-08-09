# -*- coding: utf-8 -*-
"""Manually check release tags for an explicit minor/major update.

默认路径（filter / behavior / grow / capture 等）**不会**自动检查。
仅 ``tracecite-mobile update check`` / ``update apply`` 由用户主动触发。

规则（semver）：
- ``1.0.0 → 1.0.1``（仅 patch）：不算有更新
- ``1.0.0 → 1.1.0`` / ``1.0.0 → 2.0.0``：``update_available``

``check`` 默认约每 7 天节流（未到间隔返回缓存；``--force`` 可强制打远程）。
``update apply`` 为自愿操作。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

from .. import __version__

# 默认检查间隔：1 周
DEFAULT_CHECK_INTERVAL_HOURS = 168
UPDATE_STATE_DIR = Path.home() / ".cache" / "tracecite"
UPDATE_STATE_FILENAME = "update-check.json"

_SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)


class UpdateError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def _parse_iso(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_semver(raw: str) -> Optional[Tuple[int, int, int]]:
    """解析正式版 ``1.2.3`` / ``v1.2.3``；带预发布后缀的返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    m = _SEMVER_RE.match(text)
    if not m or m.group("pre"):
        return None
    return int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


def format_semver(ver: Tuple[int, int, int]) -> str:
    return f"{ver[0]}.{ver[1]}.{ver[2]}"


def is_minor_or_major_bump(
    local: Tuple[int, int, int], remote: Tuple[int, int, int]
) -> bool:
    """仅当 remote 的 major/minor 大于 local 时为 True（忽略纯 patch）。"""
    return (remote[0], remote[1]) > (local[0], local[1])


def bump_kind(
    local: Tuple[int, int, int], remote: Tuple[int, int, int]
) -> str:
    if remote == local:
        return "same"
    if remote < local:
        return "local_ahead_or_different"
    if remote[0] > local[0]:
        return "major"
    if remote[1] > local[1]:
        return "minor"
    if remote[2] > local[2]:
        return "patch"
    return "other"


def find_tool_root(start: Optional[Path] = None) -> Path:
    """定位工具仓库根（含 .git 或 pyproject.toml）。"""
    here = (start or Path(__file__)).resolve()
    candidates = [here.parent if here.is_file() else here, *here.parents]
    for candidate in candidates:
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").is_file():
            return candidate
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "tracecite_mobile"
        ).is_dir():
            return candidate
    raise UpdateError("无法定位 TraceCite Mobile 仓库根（需要 pyproject.toml）")


def update_state_path() -> Path:
    override = os.environ.get("TRACECITE_UPDATE_STATE")
    if override:
        return Path(override).expanduser()
    return UPDATE_STATE_DIR / UPDATE_STATE_FILENAME


def _run_git(root: Path, *args: str, timeout: int = 30) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UpdateError("未找到 git 命令") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"git 超时: {' '.join(args)}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise UpdateError(f"git {' '.join(args)} 失败: {err or proc.returncode}")
    return (proc.stdout or "").strip()


@dataclass
class UpdateCheckResult:
    checked: bool
    skipped_by_interval: bool
    update_available: bool
    local_version: str
    remote_version: str
    bump: str
    local_tag: str
    remote_tag: str
    local_commit: str
    remote_commit: str
    remote_name: str
    remote_url: str
    interval_hours: int
    last_checked_at: str
    next_check_after: str
    tool_root: str
    message: str
    hint: str = ""
    policy: str = "minor_or_major_tag_only"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_remote_release_tags(root: Path, remote: str = "origin") -> List[Tuple[str, str, Tuple[int, int, int]]]:
    """返回 [(tag_name, commit, semver), ...]，仅正式版，按版本升序。"""
    out = _run_git(root, "ls-remote", "--tags", remote, timeout=45)
    found: Dict[Tuple[int, int, int], Tuple[str, str]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        commit, ref = parts[0], parts[1]
        # 忽略剥皮 tag 的 ^{}
        if ref.endswith("^{}"):
            ref = ref[:-3]
        if not ref.startswith("refs/tags/"):
            continue
        tag = ref[len("refs/tags/") :]
        ver = parse_semver(tag)
        if ver is None:
            continue
        # 同版本保留后出现的（通常 annotated 剥皮后是真实 commit）
        found[ver] = (tag, commit)
    items = [(tag, commit, ver) for ver, (tag, commit) in found.items()]
    items.sort(key=lambda x: x[2])
    return items


def latest_release_tag(
    tags: List[Tuple[str, str, Tuple[int, int, int]]],
) -> Optional[Tuple[str, str, Tuple[int, int, int]]]:
    return tags[-1] if tags else None


def check_for_updates(
    *,
    force: bool = False,
    remote: str = "origin",
    interval_hours: int = DEFAULT_CHECK_INTERVAL_HOURS,
    tool_root: Optional[Path] = None,
    local_version: Optional[str] = None,
) -> UpdateCheckResult:
    """对比本地版本与远程最新正式 tag；仅 minor/major 升级才 update_available。"""
    root = tool_root or find_tool_root()
    state_path = update_state_path()
    state = _load_state(state_path)
    now = _now()
    last = _parse_iso(str(state.get("last_checked_at") or ""))
    interval = max(1, int(interval_hours))
    next_after = (last + timedelta(hours=interval)) if last else now

    local_ver_str = (local_version or __version__).strip()
    local_parsed = parse_semver(local_ver_str)
    if local_parsed is None:
        raise UpdateError(f"本地版本无法解析为正式 semver: {local_ver_str!r}")

    local_commit = ""
    try:
        local_commit = _run_git(root, "rev-parse", "HEAD")
    except UpdateError:
        local_commit = str(state.get("local_commit") or "")

    if (
        not force
        and last is not None
        and now < last + timedelta(hours=interval)
    ):
        cached_available = bool(state.get("update_available"))
        cached_remote = str(state.get("remote_version") or "")
        cached_hint = (
            soft_update_hint_line(local_ver_str, cached_remote)
            if cached_available and cached_remote
            else ""
        )
        return UpdateCheckResult(
            checked=False,
            skipped_by_interval=True,
            update_available=cached_available,
            local_version=local_ver_str,
            remote_version=cached_remote,
            bump=str(state.get("bump") or ""),
            local_tag=str(state.get("local_tag") or ""),
            remote_tag=str(state.get("remote_tag") or ""),
            local_commit=local_commit or str(state.get("local_commit") or ""),
            remote_commit=str(state.get("remote_commit") or ""),
            remote_name=str(state.get("remote_name") or remote),
            remote_url=str(state.get("remote_url") or ""),
            interval_hours=interval,
            last_checked_at=_iso(last),
            next_check_after=_iso(next_after),
            tool_root=str(root),
            message="未到检查间隔，使用缓存结果",
            hint=cached_hint,
        )

    remote_url = ""
    try:
        remote_url = _run_git(root, "remote", "get-url", remote)
    except UpdateError as exc:
        raise UpdateError(f"无法读取 remote {remote!r}: {exc}") from exc

    tags = list_remote_release_tags(root, remote)
    latest = latest_release_tag(tags)
    if latest is None:
        raise UpdateError(
            f"远程 {remote} 没有正式版 semver tag（如 v1.1.0）。"
            "请在 GitLab 打正式版本 tag 后再检查。"
        )
    remote_tag, remote_commit, remote_parsed = latest
    remote_ver_str = format_semver(remote_parsed)
    kind = bump_kind(local_parsed, remote_parsed)
    notable = is_minor_or_major_bump(local_parsed, remote_parsed)

    if notable:
        message = (
            f"有新正式版可用：{local_ver_str} → {remote_ver_str}（{kind}）"
        )
        hint = soft_update_hint_line(local_ver_str, remote_ver_str)
    elif kind == "patch":
        message = (
            f"远程最新正式版 {remote_ver_str} 仅为 patch（相对本地 {local_ver_str}），"
            "按策略不提示更新"
        )
        hint = ""
    elif kind == "same":
        message = f"已是最新正式版 {local_ver_str}"
        hint = ""
    else:
        message = (
            f"本地 {local_ver_str} 与远程最新正式版 {remote_ver_str} 无 minor/major 升级关系"
        )
        hint = ""

    result = UpdateCheckResult(
        checked=True,
        skipped_by_interval=False,
        update_available=notable,
        local_version=local_ver_str,
        remote_version=remote_ver_str,
        bump=kind,
        local_tag="",
        remote_tag=remote_tag,
        local_commit=local_commit,
        remote_commit=remote_commit,
        remote_name=remote,
        remote_url=remote_url,
        interval_hours=interval,
        last_checked_at=_iso(now),
        next_check_after=_iso(now + timedelta(hours=interval)),
        tool_root=str(root),
        message=message,
        hint=hint,
    )
    _save_state(
        state_path,
        {
            **result.to_dict(),
            "saved_at": _iso(now),
        },
    )
    return result


def soft_update_hint_line(local_version: str, remote_version: str) -> str:
    """一行软提示文案（不强制更新）。"""
    return (
        f"提示: 有新正式版 {remote_version} 可用（当前 {local_version}），"
        f"需要时可执行: tracecite-mobile update apply"
    )


def format_soft_update_hint(result: UpdateCheckResult) -> str:
    """仅当 update_available 时返回软提示行，否则空串。"""
    if not result.update_available:
        return ""
    remote = (result.remote_version or "").strip() or (result.remote_tag or "").strip()
    local = (result.local_version or "").strip() or "?"
    if not remote:
        return ""
    return soft_update_hint_line(local, remote)


def maybe_print_update_hint(
    *,
    stream: Optional[TextIO] = None,
    force: bool = False,
) -> Optional[UpdateCheckResult]:
    """可选 helper：有 minor/major 时向 stderr 打一行提示；失败静默。

    CLI 默认路径不调用。走 ``check_for_updates``（含 7 天节流）。
    """
    try:
        result = check_for_updates(force=force)
    except Exception:
        return None
    line = format_soft_update_hint(result)
    if line:
        print(line, file=stream or sys.stderr)
    return result


def apply_update(
    *,
    tool_root: Optional[Path] = None,
    remote: str = "origin",
    tag: Optional[str] = None,
) -> Dict[str, Any]:
    """安全地切换到远程正式版 tag；脏工作区直接拒绝。"""
    root = tool_root or find_tool_root()
    dirty = _run_git(root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise UpdateError(
            "工作区有未提交或未跟踪文件，已拒绝更新，避免覆盖本地修改。"
            "请先提交、暂存或清理后重试。"
        )
    before = _run_git(root, "rev-parse", "HEAD")
    _run_git(root, "fetch", "--tags", remote, timeout=120)
    tags = list_remote_release_tags(root, remote)
    target = (tag or "").strip()
    if not target:
        latest = latest_release_tag(tags)
        if latest is None:
            raise UpdateError("远程没有可用的正式版 tag")
        # 再次确认相对当前包版本是 minor/major
        local_parsed = parse_semver(__version__)
        if local_parsed is None or not is_minor_or_major_bump(local_parsed, latest[2]):
            raise UpdateError(
                f"最新正式版 {format_semver(latest[2])} 相对本地 {__version__} "
                "不是 minor/major 升级，已拒绝 apply"
            )
        target = latest[0]
    else:
        remote_tags = {name for name, _commit, _version in tags}
        if parse_semver(target) is None or target not in remote_tags:
            raise UpdateError(
                f"目标 {target!r} 不是远程 {remote} 上的正式 semver tag"
            )
    _run_git(root, "checkout", target, timeout=60)
    after = _run_git(root, "rev-parse", "HEAD")
    install_sh = root / "install.sh"
    return {
        "tool_root": str(root),
        "tag": target,
        "before": before,
        "after": after,
        "updated": before != after,
        "hint": (
            f"已切换到 {target}。请执行: {install_sh} --with-skills"
            if install_sh.is_file()
            else f"已切换到 {target}。请重新 pip install -e . 并同步 skills"
        ),
    }

# -*- coding: utf-8 -*-
"""Android 日志采集：threadtime 解析、前台 logcat、后台 session 状态机。

约定（见 ANDROID_SUPPORT_PLAN.md §7）：
- 默认 threadtime，提取 timestamp/pid/tid/priority/tag/message。
- 解析失败的行保留 raw_line，并统计 unparsed_records，不静默丢弃。
- 默认不清空日志；package 过滤优先走统一 filter，采集不硬删跨进程证据。
- session 状态文件含 platform/serial/package_name/pid/collector_pid/output_path/started_at。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, TextIO

from tracecite_core.state_file import (
    atomic_write_json,
    process_command_contains,
    read_json,
    state_lock,
)

from ..models import DeviceRef
from .adb import AndroidAdbClient

# 跟踪后台 session 打开的文件句柄，stop 时关闭
_session_log_fps: Dict[str, TextIO] = {}

_THREADTIME_RE = re.compile(
    r"^(?P<mon>\d{2})-(?P<day>\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<prio>[VDIWEF])\s+(?P<tag>\S+?)\s*:\s?(?P<msg>.*)$"
)
_SESSION_FILENAME = ".tracecite-session.json"
_SESSIONS_FILENAME = ".tracecite-sessions.json"


def sanitize_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_")


def parse_threadtime_line(line: str) -> Dict[str, Any]:
    """解析一条 threadtime 行；失败返回含 raw_line 的未解析记录。"""
    text = line.rstrip("\n")
    m = _THREADTIME_RE.match(text)
    if not m:
        return {"raw_line": text, "unparsed": True}
    return {
        "mon": m.group("mon"),
        "day": m.group("day"),
        "time": m.group("time"),
        "timestamp": f"{m.group('mon')}-{m.group('day')} {m.group('time')}",
        "pid": int(m.group("pid")),
        "tid": int(m.group("tid")),
        "priority": m.group("prio"),
        "tag": m.group("tag"),
        "message": m.group("msg"),
        "raw_line": text,
        "unparsed": False,
    }


def iter_parsed_logcat(path: Path) -> Iterator[Dict[str, Any]]:
    """逐行解析 logcat，未解析行也保留 raw_line。"""
    with path.open(encoding="utf-8", errors="replace") as fp:
        for line in fp:
            yield parse_threadtime_line(line)


def build_log_output_path(
    output_dir: Path,
    ref: DeviceRef,
    *,
    include_date: bool,
    output_file: Optional[Path] = None,
) -> Path:
    if output_file is not None:
        output_file = Path(output_file).expanduser()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        return output_file
    safe = sanitize_filename(ref.identifier or ref.name)
    if include_date:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"android_live_{safe}_{stamp}.log"
    else:
        filename = f"android_live_{safe}.log"
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / filename


def write_log_metadata(
    path: Path,
    *,
    platform: str,
    serial: str,
    model: str,
    package: str,
    pid: Optional[int],
    command: str,
    started_at: str,
    scope: str = "logcat",
) -> None:
    header = (
        f"# tracecite android logcat\n"
        f"# platform: {platform}\n"
        f"# serial: {serial}\n"
        f"# model: {model}\n"
        f"# package: {package}\n"
        f"# pid: {pid if pid is not None else ''}\n"
        f"# command: {command}\n"
        f"# started_at: {started_at}\n"
        f"# scope: {scope}\n"
        f"# format: threadtime\n"
        f"# ---\n"
    )
    with path.open("a", encoding="utf-8") as fp:
        fp.write(header)


# ---------------- 前台采集 ----------------
class _TeeWriter:
    def __init__(self, file_obj, mirror: bool) -> None:
        self._file = file_obj
        self._mirror = mirror

    def write(self, data: str) -> None:
        self._file.write(data)
        self._file.flush()
        if self._mirror:
            sys.stdout.write(data)
            sys.stdout.flush()

    def flush(self) -> None:
        self._file.flush()
        if self._mirror:
            sys.stdout.flush()


def stream_logs(
    client: AndroidAdbClient,
    ref: DeviceRef,
    *,
    output_path: Path,
    also_stdout: bool = True,
    package: str = "",
    priority: Optional[str] = None,
    tag: Optional[str] = None,
    pid: Optional[int] = None,
) -> int:
    """前台采集 logcat 直到 Ctrl+C；写出文件并可选镜像终端。"""
    proc = client.spawn_logcat(
        ref.identifier, output_path, priority=priority, tag=tag, pid=pid
    )
    if proc.stdout is None:
        raise RuntimeError("无法启动 adb logcat")
    started_at = datetime.now().isoformat(timespec="seconds")
    write_log_metadata(
        output_path,
        platform="android",
        serial=ref.identifier,
        model=ref.model,
        package=package,
        pid=pid,
        command="adb logcat -v threadtime",
        started_at=started_at,
    )
    print()
    print(f"设备: {ref.name} ({ref.model})  serial: {ref.identifier}")
    print(f"包名: {package or '(全部)'}")
    print(f"输出: {output_path}")
    print()
    print("开始采集… 按 Ctrl+C 结束")
    print("-" * 40)
    try:
        with output_path.open("a", encoding="utf-8") as fp:
            writer = _TeeWriter(fp, also_stdout)
            for line in proc.stdout:
                writer.write(line)
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        print()
        print("-" * 40)
        print(f"日志已保存: {output_path}")
        if output_path.is_file():
            print(f"大小: {output_path.stat().st_size} bytes")
    return 0


# ---------------- 后台 session ----------------
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def session_state_path(output_dir: Path) -> Path:
    return Path(output_dir).expanduser().resolve() / _SESSION_FILENAME


def sessions_state_path(output_dir: Path) -> Path:
    """Canonical aggregate state path for Android multi-device sessions."""

    return Path(output_dir).expanduser().resolve() / _SESSIONS_FILENAME


def load_session(output_dir: Path) -> Optional[Dict[str, Any]]:
    path = session_state_path(output_dir)
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except ValueError as exc:
        raise RuntimeError(f"Android session 状态文件不可读: {path}: {exc}") from exc


def _read_aggregate(output_dir: Path) -> Optional[Dict[str, Any]]:
    """Read the canonical aggregate state, failing closed on corruption.

    A legacy single-session file is intentionally only *read* as a migration
    view.  New multi-device operations always write the aggregate file.
    """

    path = sessions_state_path(output_dir)
    if path.is_file():
        try:
            data = read_json(path)
        except ValueError as exc:
            raise RuntimeError(f"Android sessions 状态文件不可读: {path}: {exc}") from exc
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            raise RuntimeError(f"Android sessions 状态文件格式无效（sessions 必须是数组）: {path}")
        return data

    legacy = load_session(output_dir)
    if legacy is None:
        return None
    return {
        "platform": "android",
        "schema_version": 1,
        "sessions": [legacy],
    }


def load_sessions(output_dir: Path) -> Optional[Dict[str, Any]]:
    """Return canonical aggregate state, migrating legacy state in memory."""

    return _read_aggregate(output_dir)


def _session_id(state: Dict[str, Any]) -> str:
    return str(
        state.get("session_id")
        or state.get("serial")
        or state.get("output_path")
        or uuid.uuid4().hex
    )


def _state_alive(state: Dict[str, Any]) -> bool:
    """Best-effort liveness used for status and duplicate-start protection."""

    try:
        pid = int(state.get("collector_pid", 0) or 0)
    except (TypeError, ValueError):
        return False
    return bool(pid and _pid_alive(pid))


def _identity_matches(state: Dict[str, Any]) -> bool:
    """Verify a collector PID before sending a signal.

    Legacy state without an explicit identity marker is never signalled.  Test
    doubles may opt into the dedicated marker because they do not have a real
    host process command line to inspect.
    """

    if not state.get("identity_required") or not state.get("collector_marker"):
        return False
    try:
        pid = int(state.get("collector_pid", 0) or 0)
    except (TypeError, ValueError):
        return False
    if not pid or not _pid_alive(pid):
        return False
    if state.get("collector_test_double"):
        return True
    marker = str(state.get("collector_marker") or "logcat")
    return process_command_contains(pid, marker)


def _close_session_fp(state: Dict[str, Any], output_dir: Optional[Path] = None) -> None:
    output_path = str(state.get("output_path") or "")
    keys = [output_path]
    if output_dir is not None:
        keys.append(str(Path(output_dir).expanduser().resolve()))
    for key in keys:
        fp = _session_log_fps.pop(key, None)
        if fp is None:
            continue
        try:
            fp.close()
        except OSError:
            pass
        break


def _start_session_unlocked(
    client: AndroidAdbClient,
    ref: DeviceRef,
    *,
    package: str = "",
    output_dir: Path,
    include_date: bool = False,
    output_file: Optional[Path] = None,
    popen=None,
) -> Dict[str, Any]:
    """后台启动 logcat 采集，写入状态文件。popen 可注入用于测试。"""
    output_dir = Path(output_dir).expanduser().resolve()
    existing = load_session(output_dir)
    if existing is not None and _pid_alive(int(existing.get("collector_pid", 0) or 0)):
        raise RuntimeError(
            f"已有进行中的日志 session（PID {existing.get('collector_pid')}）。\n"
            f"输出: {existing.get('output_path')}\n请先执行 session stop。"
        )
    if existing is not None:
        session_state_path(output_dir).unlink(missing_ok=True)

    output_path = build_log_output_path(
        output_dir, ref, include_date=include_date, output_file=output_file
    ).expanduser().resolve()
    app_pid = client.pidof(ref.identifier, package) if package else None
    started_at = datetime.now().isoformat(timespec="seconds")
    write_log_metadata(
        output_path,
        platform="android",
        serial=ref.identifier,
        model=ref.model,
        package=package,
        pid=app_pid,
        command="adb logcat -v threadtime",
        started_at=started_at,
    )

    spawn = popen or client.spawn_logcat
    # 以文件句柄传入，子进程直接写入 output_path
    # 注意：log_fp 不在此关闭——子进程正在写入。stop_session 时 kill 进程后关闭。
    log_fp = open(output_path, "a", encoding="utf-8")
    try:
        proc = spawn(
            ref.identifier,
            output_path,
            priority=None,
            tag=None,
            pid=app_pid,
            log_fp=log_fp,
        )
    except OSError as exc:
        log_fp.close()
        raise RuntimeError(f"无法启动后台日志 session: {exc}") from exc

    # 保存文件句柄以便 stop 时关闭
    _session_log_fps[str(output_path)] = log_fp

    state = {
        "platform": "android",
        "session_id": f"android-{sanitize_filename(ref.identifier)}-{uuid.uuid4().hex[:12]}",
        "serial": ref.identifier,
        "device_name": ref.name,
        "model": ref.model,
        "package_name": package,
        "pid": app_pid,
        "collector_pid": proc.pid,
        "output_path": str(output_path),
        "started_at": started_at,
        "collector_marker": "logcat",
        "identity_required": True,
        "collector_test_double": popen is not None,
    }
    atomic_write_json(session_state_path(output_dir), state)
    return state


def start_session(
    client: AndroidAdbClient,
    ref: DeviceRef,
    *,
    package: str = "",
    output_dir: Path,
    include_date: bool = False,
    output_file: Optional[Path] = None,
    popen=None,
) -> Dict[str, Any]:
    """后台启动 logcat；状态检查和写入在同一把跨进程锁内完成。"""
    output_dir = Path(output_dir).expanduser().resolve()
    with state_lock(session_state_path(output_dir)):
        with state_lock(sessions_state_path(output_dir)):
            aggregate = _read_aggregate(output_dir)
            existing_rows = (aggregate or {}).get("sessions") or []
            if any(
                str(row.get("serial")) == ref.identifier and _state_alive(row)
                for row in existing_rows
                if isinstance(row, dict)
            ):
                raise RuntimeError(f"设备 {ref.identifier} 已有进行中的日志 session。")
            state = _start_session_unlocked(
                client,
                ref,
                package=package,
                output_dir=output_dir,
                include_date=include_date,
                output_file=output_file,
                popen=popen,
            )
            current = [
                dict(row)
                for row in existing_rows
                if str(row.get("serial")) != ref.identifier
            ]
            current.append(state)
            _write_aggregate(output_dir, current)
            return state


def get_session_status(output_dir: Path) -> Dict[str, Any]:
    aggregate = _read_aggregate(output_dir)
    if not aggregate or not aggregate.get("sessions"):
        return {"active": False, "session": None}
    rows = [{**state, "alive": _state_alive(state)} for state in aggregate["sessions"]]
    active = any(row["alive"] for row in rows)
    # Keep the legacy JSON shape while exposing the aggregate view when there
    # are multiple devices.
    return {
        "active": active,
        "session": rows[0],
        "sessions": rows,
        "session_count": len(rows),
    }


def _stop_session_unlocked(output_dir: Path) -> Dict[str, Any]:
    output_dir = Path(output_dir).expanduser().resolve()
    state = load_session(output_dir)
    if state is None:
        raise RuntimeError("当前没有进行中的日志 session。")
    pid = int(state.get("collector_pid", 0) or 0)
    if pid and _pid_alive(pid):
        if not _identity_matches(state):
            raise RuntimeError(
                f"拒绝停止 Android session {state.get('session_id') or state.get('serial')}："
                "collector PID 身份无法核验。"
            )
        try:
            os.killpg(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    # 关闭文件句柄
    _close_session_fp(state, output_dir)
    session_state_path(output_dir).unlink(missing_ok=True)
    return state


def stop_session(output_dir: Path) -> Dict[str, Any]:
    """停止后台 logcat；与并发 start/stop 串行化状态转换。"""
    output_dir = Path(output_dir).expanduser().resolve()
    with state_lock(session_state_path(output_dir)):
        with state_lock(sessions_state_path(output_dir)):
            if session_state_path(output_dir).is_file():
                state = _stop_session_unlocked(output_dir)
            else:
                aggregate = _read_aggregate(output_dir)
                rows = list((aggregate or {}).get("sessions") or [])
                if len(rows) != 1:
                    raise RuntimeError(
                        "旧 session stop 只能停止单台设备；多设备请使用 stop_sessions。"
                    )
                state = _stop_state_unlocked(rows[0], output_dir)
            aggregate = _read_aggregate(output_dir)
            if aggregate is not None:
                current = [
                    dict(row)
                    for row in aggregate.get("sessions", [])
                    if not (
                        str(row.get("session_id")) == str(state.get("session_id"))
                        or (
                            str(row.get("serial")) == str(state.get("serial"))
                            and str(row.get("output_path")) == str(state.get("output_path"))
                        )
                    )
                ]
                _write_aggregate(output_dir, current)
            return state


# ---------------- canonical multi-device sessions ----------------
def _write_aggregate(output_dir: Path, sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {
        "platform": "android",
        "schema_version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "sessions": sessions,
    }
    atomic_write_json(sessions_state_path(output_dir), payload)
    return payload


def _new_session_unlocked(
    client: AndroidAdbClient,
    ref: DeviceRef,
    *,
    package: str,
    output_dir: Path,
    include_date: bool,
    output_file: Optional[Path],
    popen=None,
) -> Dict[str, Any]:
    """Start one collector without changing aggregate state."""

    output_path = build_log_output_path(
        output_dir, ref, include_date=include_date, output_file=output_file
    ).expanduser().resolve()
    app_pid = client.pidof(ref.identifier, package) if package else None
    started_at = datetime.now().isoformat(timespec="seconds")
    write_log_metadata(
        output_path,
        platform="android",
        serial=ref.identifier,
        model=ref.model,
        package=package,
        pid=app_pid,
        command="adb logcat -v threadtime",
        started_at=started_at,
    )
    spawn = popen or client.spawn_logcat
    log_fp = open(output_path, "a", encoding="utf-8")
    try:
        proc = spawn(
            ref.identifier,
            output_path,
            priority=None,
            tag=None,
            pid=app_pid,
            log_fp=log_fp,
        )
    except OSError as exc:
        log_fp.close()
        raise RuntimeError(f"无法启动后台日志 session: {exc}") from exc
    state = {
        "platform": "android",
        "schema_version": 1,
        "session_id": f"android-{sanitize_filename(ref.identifier)}-{uuid.uuid4().hex[:12]}",
        "serial": ref.identifier,
        "device_name": ref.name,
        "model": ref.model,
        "package_name": package,
        "pid": app_pid,
        "collector_pid": getattr(proc, "pid", None),
        "collector_marker": "logcat",
        "identity_required": True,
        "collector_test_double": popen is not None,
        "output_path": str(output_path),
        "started_at": started_at,
    }
    _session_log_fps[str(output_path)] = log_fp
    return state


def start_sessions(
    client: AndroidAdbClient,
    refs: List[DeviceRef],
    *,
    package: str = "",
    output_dir: Path,
    include_date: bool = False,
    output_file: Optional[Path] = None,
    popen=None,
) -> Dict[str, Any]:
    """Start one or more Android collectors into canonical aggregate state.

    ``output_file`` is deliberately single-device only; multi-device sessions
    always receive serial-derived output names so paths cannot collide.
    """

    output_dir = Path(output_dir).expanduser().resolve()
    refs = list(refs)
    if not refs:
        raise RuntimeError("至少需要选择一台 Android 设备。")
    if output_file is not None and len(refs) != 1:
        raise RuntimeError("多设备 session start 不能使用 output_file。")
    serials = [ref.identifier for ref in refs]
    if len(set(serials)) != len(serials):
        raise RuntimeError("多设备 session start 包含重复 serial。")

    with state_lock(sessions_state_path(output_dir)):
        aggregate = _read_aggregate(output_dir) or {
            "platform": "android",
            "schema_version": 1,
            "sessions": [],
        }
        current = [dict(row) for row in aggregate.get("sessions", [])]
        current_by_serial = {str(row.get("serial")): row for row in current}
        for serial in serials:
            existing = current_by_serial.get(serial)
            if existing is not None and _state_alive(existing):
                raise RuntimeError(
                    f"设备 {serial} 已有进行中的日志 session（PID {existing.get('collector_pid')}）。"
                )
            if existing is not None:
                current.remove(existing)

        started: List[Dict[str, Any]] = []
        try:
            for ref in refs:
                state = _new_session_unlocked(
                    client,
                    ref,
                    package=package,
                    output_dir=output_dir,
                    include_date=include_date,
                    output_file=output_file,
                    popen=popen,
                )
                started.append(state)
                current.append(state)
                _write_aggregate(output_dir, current)
        except Exception:
            # Do not leave orphan collectors when a later device fails.
            for state in started:
                pid = int(state.get("collector_pid", 0) or 0)
                if pid and _pid_alive(pid) and _identity_matches(state):
                    try:
                        os.killpg(pid, signal.SIGINT)
                    except (ProcessLookupError, PermissionError):
                        pass
                _close_session_fp(state, output_dir)
            current = [row for row in current if row not in started]
            _write_aggregate(output_dir, current)
            raise
        return _write_aggregate(output_dir, current)


def list_sessions(
    output_dir: Path,
    *,
    refs: Optional[List[DeviceRef]] = None,
) -> Dict[str, Any]:
    """List canonical sessions (or an in-memory legacy migration view)."""

    aggregate = _read_aggregate(output_dir)
    if aggregate is None:
        return {
            "platform": "android",
            "schema_version": 1,
            "sessions": [],
            "active": False,
            "session_count": 0,
        }
    selected = {ref.identifier for ref in refs} if refs else None
    rows = []
    for row in aggregate.get("sessions", []):
        if selected is not None and str(row.get("serial")) not in selected:
            continue
        rows.append({**row, "alive": _state_alive(row)})
    return {
        "platform": "android",
        "schema_version": int(aggregate.get("schema_version", 1)),
        "sessions": rows,
        "active": any(row["alive"] for row in rows),
        "session_count": len(rows),
    }


def _stop_state_unlocked(state: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    pid = int(state.get("collector_pid", 0) or 0)
    if pid and _pid_alive(pid):
        if not _identity_matches(state):
            raise RuntimeError(
                f"拒绝停止 Android session {state.get('session_id') or state.get('serial')}："
                "collector PID 身份无法核验。"
            )
        try:
            os.killpg(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    _close_session_fp(state, output_dir)
    return {**state, "alive": False, "state": "stopped"}


def stop_sessions(
    output_dir: Path,
    *,
    refs: Optional[List[DeviceRef]] = None,
    all_devices: bool = False,
) -> Dict[str, Any]:
    """Stop selected canonical sessions, never an unrelated collector."""

    output_dir = Path(output_dir).expanduser().resolve()
    with state_lock(sessions_state_path(output_dir)):
        aggregate = _read_aggregate(output_dir)
        if aggregate is None:
            raise RuntimeError("当前没有进行中的日志 session。")
        current = [dict(row) for row in aggregate.get("sessions", [])]
        if refs:
            wanted = {ref.identifier for ref in refs}
        elif all_devices:
            wanted = {str(row.get("serial")) for row in current}
        elif len(current) == 1:
            wanted = {str(current[0].get("serial"))}
        else:
            raise RuntimeError("多设备 session stop 请指定设备或 all_devices=True。")
        selected = [row for row in current if str(row.get("serial")) in wanted]
        if not selected:
            raise RuntimeError("未找到要停止的 Android session。")
        stopped: List[Dict[str, Any]] = []
        remaining: List[Dict[str, Any]] = []
        for row in current:
            if row in selected:
                stopped_state = _stop_state_unlocked(row, output_dir)
                # Old single-session callers may have a legacy mirror.  Only
                # remove it when it is the exact session being stopped.
                legacy = load_session(output_dir)
                if legacy is not None and (
                    str(legacy.get("session_id")) == str(row.get("session_id"))
                    or (
                        str(legacy.get("serial")) == str(row.get("serial"))
                        and str(legacy.get("output_path")) == str(row.get("output_path"))
                    )
                ):
                    session_state_path(output_dir).unlink(missing_ok=True)
                stopped.append(stopped_state)
            else:
                remaining.append(row)
        _write_aggregate(output_dir, remaining)
        # Keep the stopped rows in the return value so callers can produce a
        # stable result even though canonical state now only contains active rows.
        return {
            "platform": "android",
            "schema_version": 1,
            "stopped": stopped,
            "sessions": remaining,
            "active": any(_state_alive(row) for row in remaining),
            "session_count": len(remaining),
        }

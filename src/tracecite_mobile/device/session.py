# -*- coding: utf-8 -*-
"""AI 友好的后台日志 session 管理（支持多设备并行）。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .archive import archive_device_dir
from .capture import capture_process_alive, load_capture_session
from ..shared.config import ProjectProfile
from ..shared.constants import (
    DEFAULT_HOT_WINDOW_SEC,
    SESSIONS_STATE_FILENAME,
    STREAM_HEARTBEAT_STALE_SEC,
)
from .devices import Device
from .stream import build_output_path, ensure_dependencies, stream_heartbeat_path
from tracecite_core.state_file import (
    atomic_write_json,
    process_command_contains,
    read_json,
    state_lock,
)


class SessionError(RuntimeError):
    pass


@dataclass
class StreamSession:
    pid: int
    device_name: str
    device_udid: str
    device_model: str
    process_name: str
    subsystem: str
    output_path: str
    log_output_dir: str
    capture_output_dir: str
    stream_log_path: str
    started_at: str
    profile_path: Optional[str]
    hot_window_sec: int = DEFAULT_HOT_WINDOW_SEC
    archive_dir: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamSession":
        missing = [
            key
            for key in (
                "output_path",
                "log_output_dir",
                "capture_output_dir",
                "stream_log_path",
                "pid",
                "device_name",
                "device_udid",
                "started_at",
            )
            if key not in data
        ]
        if missing:
            raise SessionError(
                "session 状态文件格式过旧或缺少字段: "
                + ", ".join(missing)
                + "。请 session stop 或删除 .tracecite-sessions.json 后重新 session start。"
            )
        archive = data.get("archive_dir")
        if not archive:
            archive = str(
                archive_device_dir(Path(str(data["log_output_dir"])), str(data["device_name"]))
            )
        return cls(
            pid=int(data["pid"]),
            device_name=str(data["device_name"]),
            device_udid=str(data["device_udid"]),
            device_model=str(data.get("device_model", "")),
            process_name=str(data.get("process_name", "")),
            subsystem=str(data.get("subsystem", "all")),
            output_path=str(data["output_path"]),
            log_output_dir=str(data["log_output_dir"]),
            capture_output_dir=str(data["capture_output_dir"]),
            stream_log_path=str(data["stream_log_path"]),
            started_at=str(data["started_at"]),
            profile_path=(
                str(data["profile_path"])
                if data.get("profile_path") is not None
                else None
            ),
            hot_window_sec=int(data.get("hot_window_sec", DEFAULT_HOT_WINDOW_SEC)),
            archive_dir=str(archive) if archive else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def sessions_state_path(log_output_dir: Path) -> Path:
    return log_output_dir.expanduser().resolve() / SESSIONS_STATE_FILENAME


def load_all_sessions(log_output_dir: Path) -> Dict[str, StreamSession]:
    """按 UDID 返回全部 session。"""
    resolved = log_output_dir.expanduser().resolve()
    multi = sessions_state_path(resolved)
    sessions: Dict[str, StreamSession] = {}

    if multi.is_file():
        try:
            data = read_json(multi)
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        raw = data.get("sessions") or {}
        if not isinstance(raw, dict):
            raise SessionError(f"sessions 状态格式错误: {multi}")
        for udid, item in raw.items():
            if isinstance(item, dict):
                sessions[str(udid)] = StreamSession.from_dict(item)
        return sessions

    return sessions


def save_all_sessions(log_output_dir: Path, sessions: Dict[str, StreamSession]) -> None:
    path = sessions_state_path(log_output_dir)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "sessions": {udid: s.to_dict() for udid, s in sessions.items()},
    }
    atomic_write_json(path, payload)


def load_stream_session(
    log_output_dir: Path,
    *,
    udid: Optional[str] = None,
) -> Optional[StreamSession]:
    """读取一个 session；多条时优先返回仍存活的第一条。"""
    sessions = load_all_sessions(log_output_dir)
    if not sessions:
        return None
    if udid:
        return sessions.get(udid)
    if len(sessions) == 1:
        return next(iter(sessions.values()))
    for session in sessions.values():
        if _session_process_alive(session):
            return session
    return next(iter(sessions.values()))


def save_stream_session(log_output_dir: Path, session: StreamSession) -> None:
    """写入多 session 索引中的一条。"""
    sessions = load_all_sessions(log_output_dir)
    sessions[session.device_udid] = session
    save_all_sessions(log_output_dir, sessions)


def clear_stream_session(
    log_output_dir: Path,
    *,
    udid: Optional[str] = None,
) -> None:
    sessions = load_all_sessions(log_output_dir)
    if udid:
        sessions.pop(udid, None)
    else:
        sessions.clear()
    if sessions:
        save_all_sessions(log_output_dir, sessions)
    else:
        sessions_state_path(log_output_dir).unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGINT)
        os.waitpid(pid, 0)
    except (OSError, ChildProcessError):
        pass


def _session_process_alive(session: StreamSession) -> bool:
    return _pid_alive(session.pid) and process_command_contains(
        session.pid, "tracecite_mobile"
    )


def _parse_started_at(started_at: str) -> Optional[datetime]:
    text = (started_at or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _session_heartbeat_age_sec(session: StreamSession) -> Optional[float]:
    """心跳文件距今秒数；无文件返回 None。"""
    hb = stream_heartbeat_path(Path(session.output_path))
    if not hb.is_file():
        return None
    try:
        return max(0.0, time.time() - hb.stat().st_mtime)
    except OSError:
        return None


def _session_stream_stalled(
    session: StreamSession,
    *,
    stale_sec: float = STREAM_HEARTBEAT_STALE_SEC,
) -> bool:
    """
    进程仍在但采集链路假存活：
    - 已启动超过 stale_sec 仍无 heartbeat，或
    - heartbeat mtime 超过 stale_sec 未更新
    刚启动的宽限期内不判 stalled。
    """
    if not _session_process_alive(session):
        return False
    started = _parse_started_at(session.started_at)
    if started is not None:
        age = (datetime.now() - started).total_seconds()
        if age < stale_sec:
            return False
    hb_age = _session_heartbeat_age_sec(session)
    if hb_age is None:
        return True
    return hb_age > stale_sec


def _session_stream_healthy(session: StreamSession) -> bool:
    return _session_process_alive(session) and not _session_stream_stalled(session)


def _stream_entry_command(
    device: Device,
    output_path: Path,
    profile: ProjectProfile,
    *,
    hot_window_sec: int,
    platform: str = "ios",
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tracecite_mobile",
        "--platform",
        platform,
        "stream",
        profile.process_name,
        str(profile.log_output_dir),
        profile.subsystem,
        "--no-interactive",
        "--udid",
        device.udid,
        "--output-file",
        str(output_path),
        "--no-stdout",
        "--hot-window-sec",
        str(hot_window_sec),
    ]


def _start_stream_session_unlocked(
    device: Device,
    profile: ProjectProfile,
    *,
    include_date: bool = False,
    output_file: Optional[Path] = None,
    hot_window_sec: int = DEFAULT_HOT_WINDOW_SEC,
    platform: str = "ios",
) -> StreamSession:
    ensure_dependencies()
    log_output_dir = profile.log_output_dir.expanduser().resolve()
    sessions = load_all_sessions(log_output_dir)
    existing = sessions.get(device.udid)
    if existing is not None and _session_process_alive(existing):
        if _session_stream_stalled(existing):
            # 假存活：自动收尸后继续 start，避免 Agent/用户卡在「已有 session」
            _stop_one_unlocked(sessions, existing)
            save_all_sessions(log_output_dir, sessions)
        else:
            raise SessionError(
                f"设备 {device.name} ({device.udid}) 已有进行中的日志 session（PID {existing.pid}）。\n"
                f"输出: {existing.output_path}\n"
                "请先执行: tracecite-mobile session stop --udid "
                f"{device.udid}"
            )
    elif existing is not None:
        # 状态里有旧 session 但进程被判定已死/无法确认存活：
        # 覆盖前仍先清理旧进程组，防止残留进程继续写同一日志文件（多写者叠加）。
        # 注意：ps 受限环境下 _session_process_alive 可能误判，这里用 os.kill 探测兜底。
        if _pid_alive(existing.pid):
            try:
                _kill_process_group(existing.pid)
            except (OSError, ChildProcessError):
                pass
        sessions.pop(device.udid, None)

    output_path = build_output_path(
        log_output_dir,
        device,
        include_date,
        output_file.expanduser() if output_file else None,
    ).expanduser().resolve()
    stream_log_path = output_path.with_name(f"{output_path.stem}_session.log")
    window = max(60, int(hot_window_sec))
    command = _stream_entry_command(
        device,
        output_path,
        profile,
        hot_window_sec=window,
        platform=platform,
    )
    archive_dir = archive_device_dir(log_output_dir, device.name)

    log_fp = open(stream_log_path, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            command,
            # 必须留在调用方项目目录：换成工具仓库根会让子进程发现别的项目 .tracecite/config.json
            cwd=os.getcwd(),
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise SessionError(f"无法启动后台日志 session: {exc}") from exc
    finally:
        log_fp.close()

    session = StreamSession(
        pid=proc.pid,
        device_name=device.name,
        device_udid=device.udid,
        device_model=device.model,
        process_name=profile.process_name,
        subsystem=profile.subsystem,
        output_path=str(output_path),
        log_output_dir=str(log_output_dir),
        capture_output_dir=str(profile.capture_output_dir.expanduser().resolve()),
        stream_log_path=str(stream_log_path),
        started_at=datetime.now().isoformat(timespec="seconds"),
        profile_path=str(profile.source_path) if profile.source_path else None,
        hot_window_sec=window,
        archive_dir=str(archive_dir),
    )
    try:
        sessions[device.udid] = session
        save_all_sessions(log_output_dir, sessions)
    except Exception as exc:
        _kill_process_group(proc.pid)
        raise SessionError(f"无法保存 session 状态，已停止采集进程: {exc}") from exc
    return session


def start_stream_session(
    device: Device,
    profile: ProjectProfile,
    *,
    include_date: bool = False,
    output_file: Optional[Path] = None,
    hot_window_sec: int = DEFAULT_HOT_WINDOW_SEC,
    platform: str = "ios",
) -> StreamSession:
    state_path = sessions_state_path(profile.log_output_dir)
    with state_lock(state_path):
        return _start_stream_session_unlocked(
            device,
            profile,
            include_date=include_date,
            output_file=output_file,
            hot_window_sec=hot_window_sec,
            platform=platform,
        )


def start_stream_sessions(
    devices: List[Device],
    profile: ProjectProfile,
    *,
    include_date: bool = False,
    hot_window_sec: int = DEFAULT_HOT_WINDOW_SEC,
    platform: str = "ios",
) -> List[StreamSession]:
    if not devices:
        raise SessionError("未指定任何设备")
    started: List[StreamSession] = []
    errors: List[str] = []
    for device in devices:
        try:
            started.append(
                start_stream_session(
                    device,
                    profile,
                    include_date=include_date,
                    hot_window_sec=hot_window_sec,
                    platform=platform,
                )
            )
        except SessionError as exc:
            errors.append(f"{device.name}: {exc}")
    if errors and not started:
        raise SessionError("全部设备启动失败:\n" + "\n".join(errors))
    if errors:
        raise SessionError(
            "部分设备启动失败（已启动 "
            + str(len(started))
            + " 台）:\n"
            + "\n".join(errors)
        )
    return started


def _stop_one_unlocked(
    sessions: Dict[str, StreamSession],
    session: StreamSession,
) -> StreamSession:
    if _pid_alive(session.pid) and not _session_process_alive(session):
        raise SessionError(
            f"状态中的 PID {session.pid} 已被其他进程复用，已拒绝发送停止信号。"
        )
    if _session_process_alive(session):
        try:
            os.killpg(session.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(session.pid, 0)
        except (ChildProcessError, ProcessLookupError):
            pass
    _cleanup_session_leftovers(session)
    sessions.pop(session.device_udid, None)
    return session


def _cleanup_session_leftovers(session: StreamSession) -> None:
    """停止/失败后兜底清理心跳与 rotate 临时文件，避免残留堆积。

    子进程正常退出时会自己清理；若子进程已崩溃（SIGKILL/异常退出），
    这里补一次清理，保证 .heartbeat 与 .rotate.tmp 不残留。
    """
    from .archive import cleanup_rotate_tmp
    from .stream import stream_heartbeat_path

    try:
        out = Path(session.output_path)
    except Exception:
        return
    for leftover in (
        stream_heartbeat_path(out),
        out.with_name(f".{out.name}.rotate.tmp"),
    ):
        try:
            if leftover.is_file():
                leftover.unlink()
        except OSError:
            pass
    try:
        cleanup_rotate_tmp(out)
    except Exception:
        pass


def _stop_stream_sessions_unlocked(
    log_output_dir: Path,
    *,
    udid: Optional[str] = None,
    stop_all: bool = False,
) -> List[StreamSession]:
    resolved_dir = log_output_dir.expanduser().resolve()
    sessions = load_all_sessions(resolved_dir)
    if not sessions:
        raise SessionError("当前没有进行中的日志 session。")

    stopped: List[StreamSession] = []
    if stop_all or (udid is None and len(sessions) == 1):
        targets = list(sessions.values())
    elif udid:
        target = sessions.get(udid)
        if target is None:
            raise SessionError(f"未找到 UDID 对应的 session: {udid}")
        targets = [target]
    else:
        lines = "\n".join(
            f"  - {s.device_name} ({s.device_udid}) → {s.output_path}"
            for s in sessions.values()
        )
        raise SessionError(
            "当前有多个日志 session，请指定 --udid 或使用 --all：\n" + lines
        )

    for session in targets:
        stopped.append(_stop_one_unlocked(sessions, session))

    if sessions:
        save_all_sessions(resolved_dir, sessions)
    else:
        sessions_state_path(resolved_dir).unlink(missing_ok=True)
    return stopped


def stop_stream_session(
    log_output_dir: Path,
    *,
    udid: Optional[str] = None,
    stop_all: bool = False,
) -> StreamSession:
    """停止一台（或唯一一台）；多台且未指定时抛错。返回最后停止的那台。"""
    stopped = stop_stream_sessions(
        log_output_dir, udid=udid, stop_all=stop_all
    )
    return stopped[-1]


def stop_stream_sessions(
    log_output_dir: Path,
    *,
    udid: Optional[str] = None,
    stop_all: bool = False,
) -> List[StreamSession]:
    resolved_dir = log_output_dir.expanduser().resolve()
    with state_lock(sessions_state_path(resolved_dir)):
        return _stop_stream_sessions_unlocked(
            resolved_dir, udid=udid, stop_all=stop_all
        )


def get_stream_session_status(log_output_dir: Path) -> Dict[str, Any]:
    resolved_dir = log_output_dir.expanduser().resolve()
    sessions = load_all_sessions(resolved_dir)
    session_payloads = []
    any_alive = False
    for session in sessions.values():
        process_alive = _session_process_alive(session)
        stalled = _session_stream_stalled(session) if process_alive else False
        healthy = process_alive and not stalled
        # alive：进程在且采集健康；假存活对外视为非 alive，便于 Agent 发现并重启
        alive = healthy
        any_alive = any_alive or alive
        hb_age = _session_heartbeat_age_sec(session)
        session_payloads.append(
            {
                **session.to_dict(),
                "alive": alive,
                "process_alive": process_alive,
                "healthy": healthy,
                "stalled": stalled,
                "heartbeat_age_sec": (
                    None if hb_age is None else round(hb_age, 1)
                ),
            }
        )

    # capture 仍挂在「任一 session 的 capture_output_dir」上（通常同 profile）
    capture_payload = None
    capture_dir: Optional[Path] = None
    if session_payloads:
        capture_dir = Path(session_payloads[0]["capture_output_dir"])
    else:
        # 无 session 时不猜 capture 目录
        capture_dir = None
    if capture_dir is not None:
        capture_session = load_capture_session(capture_dir)
        if capture_session is not None:
            capture_payload = {
                "pid": capture_session.pid,
                "trace_path": capture_session.trace_path,
                "toc_path": capture_session.toc_path,
                "template": capture_session.template,
                "attach": capture_session.attach,
                "launch": capture_session.launch,
                "started_at": capture_session.started_at,
                "alive": capture_process_alive(capture_session),
            }

    return {
        "active": any_alive,
        "sessions": session_payloads,
        "session_count": len(session_payloads),
        "capture": capture_payload,
    }

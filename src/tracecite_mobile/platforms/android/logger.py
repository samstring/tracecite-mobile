# -*- coding: utf-8 -*-
"""Android 日志采集：threadtime 解析、前台 logcat、后台 session 状态机。

约定（见 ANDROID_SUPPORT_PLAN.md §7）：
- 默认 threadtime，提取 timestamp/pid/tid/priority/tag/message。
- 解析失败的行保留 raw_line，并统计 unparsed_records，不静默丢弃。
- 默认不清空日志；package 过滤优先走统一 filter，采集不硬删跨进程证据。
- session 状态文件含 platform/serial/package_name/pid/collector_pid/output_path/started_at。
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, TextIO

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


def load_session(output_dir: Path) -> Optional[Dict[str, Any]]:
    path = session_state_path(output_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


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
    _session_log_fps[str(output_dir)] = log_fp

    state = {
        "platform": "android",
        "serial": ref.identifier,
        "package_name": package,
        "pid": app_pid,
        "collector_pid": proc.pid,
        "output_path": str(output_path),
        "started_at": started_at,
    }
    session_state_path(output_dir).parent.mkdir(parents=True, exist_ok=True)
    session_state_path(output_dir).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


def get_session_status(output_dir: Path) -> Dict[str, Any]:
    state = load_session(output_dir)
    if state is None:
        return {"active": False, "session": None}
    alive = _pid_alive(int(state.get("collector_pid", 0) or 0))
    return {"active": alive, "session": {**state, "alive": alive}}


def stop_session(output_dir: Path) -> Dict[str, Any]:
    state = load_session(output_dir)
    if state is None:
        raise RuntimeError("当前没有进行中的日志 session。")
    pid = int(state.get("collector_pid", 0) or 0)
    if pid and _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    # 关闭文件句柄
    fp = _session_log_fps.pop(str(output_dir), None)
    if fp is not None:
        try:
            fp.close()
        except OSError:
            pass
    session_state_path(output_dir).unlink(missing_ok=True)
    return state

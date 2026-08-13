# -*- coding: utf-8 -*-
"""idevicesyslog 流式采集与写文件。"""

from __future__ import annotations

import os
import re
import select
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable, Optional, TextIO

from ..shared.constants import (
    DEFAULT_ARCHIVE_INTERVAL_SEC,
    DEFAULT_HOT_WINDOW_SEC,
    DEFAULT_PROCESS_NAME,
    DEFAULT_SUBSYSTEM,
    HOT_ROTATE_CHECK_BYTES,
    LOG_FILENAME_PREFIX,
    STREAM_HEARTBEAT_TOUCH_INTERVAL_SEC,
    STREAM_RAW_STALL_SEC,
    STREAM_RECONNECT_SLEEP_SEC,
)
from .devices import Device
from ..plugins.processor import configure_stdio, process_stream


class StreamError(RuntimeError):
    pass


class StallError(StreamError):
    """idevicesyslog 原始输出超过阈值无字节（假存活）。"""


def sanitize_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_")


def build_output_path(
    output_dir: Path,
    device: Device,
    include_date: bool,
    output_file: Optional[Path] = None,
) -> Path:
    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        return output_file

    safe_name = sanitize_filename(device.name)

    if include_date:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{LOG_FILENAME_PREFIX}_{safe_name}_{timestamp}.log"
    else:
        filename = f"{LOG_FILENAME_PREFIX}_{safe_name}.log"

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / filename


def stream_heartbeat_path(output_path: Path) -> Path:
    """与业务 .log 并列的心跳文件：有原始 syslog 活动时更新 mtime。"""
    return output_path.with_name(output_path.name + ".heartbeat")


def ensure_dependencies() -> None:
    if shutil.which("idevicesyslog") is None:
        raise StreamError(
            "未找到 idevicesyslog，请先安装 libimobiledevice:\n"
            "  brew install libimobiledevice"
        )
    if shutil.which("xcrun") is None:
        raise StreamError("未找到 xcrun，请安装 Xcode Command Line Tools。")


class TeeWriter:
    def __init__(self, file_obj: TextIO, mirror: bool):
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


class StallDetectingReader:
    """对 pipe 做 select 超时读；超时抛 StallError，读到数据回调 on_activity。"""

    def __init__(
        self,
        pipe: BinaryIO,
        *,
        stall_sec: float,
        on_activity: Optional[Callable[[], None]] = None,
    ) -> None:
        self._pipe = pipe
        self._stall_sec = max(1.0, float(stall_sec))
        self._on_activity = on_activity
        self._buf = b""
        self._fd = pipe.fileno()

    def readline(self) -> bytes:
        while b"\n" not in self._buf:
            ready, _, _ = select.select([self._fd], [], [], self._stall_sec)
            if not ready:
                raise StallError(
                    f"idevicesyslog 超过 {int(self._stall_sec)}s 无原始输出（疑似假存活）"
                )
            try:
                chunk = os.read(self._fd, 65536)
            except OSError as exc:
                raise StallError(f"读取 idevicesyslog 失败: {exc}") from exc
            if not chunk:
                # EOF：返回缓冲中残留（可能无换行）
                leftover = self._buf
                self._buf = b""
                return leftover
            self._buf += chunk
            if self._on_activity is not None:
                self._on_activity()

        line, self._buf = self._buf.split(b"\n", 1)
        return line + b"\n"


def _touch_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _make_activity_toucher(heartbeat: Path) -> Callable[[], None]:
    last = [0.0]
    interval = float(STREAM_HEARTBEAT_TOUCH_INTERVAL_SEC)

    def _touch() -> None:
        now = time.monotonic()
        if now - last[0] < interval:
            return
        last[0] = now
        try:
            _touch_heartbeat(heartbeat)
        except OSError:
            pass

    return _touch


def _terminate_syslog(syslog_proc: subprocess.Popen) -> None:
    if syslog_proc.poll() is not None:
        return
    syslog_proc.terminate()
    try:
        syslog_proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        syslog_proc.kill()
        try:
            syslog_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _run_syslog_once(
    device: Device,
    *,
    process_name: str,
    subsystem_filter: str,
    output_path: Path,
    writer: TextIO,
    stall_sec: float,
) -> str:
    """
    跑一轮 idevicesyslog。
    返回: "eof" | "stall"（stall 时已清理子进程，由上层决定是否重试）
    KeyboardInterrupt 向上抛。
    """
    heartbeat = stream_heartbeat_path(output_path)
    on_activity = _make_activity_toucher(heartbeat)
    # 启动即 touch，避免 status 在首包到达前误判 stalled
    try:
        _touch_heartbeat(heartbeat)
    except OSError:
        pass

    syslog_proc = subprocess.Popen(
        ["idevicesyslog", "--no-colors", "-u", device.udid],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if syslog_proc.stdout is None:
        raise StreamError("无法启动 idevicesyslog")

    reader = StallDetectingReader(
        syslog_proc.stdout,
        stall_sec=stall_sec,
        on_activity=on_activity,
    )
    outcome = "eof"
    try:
        process_stream(
            reader,  # type: ignore[arg-type]
            writer,
            process_name=process_name,
            subsystem_filter=subsystem_filter,
        )
    except StallError:
        outcome = "stall"
        raise
    except KeyboardInterrupt:
        raise
    finally:
        _terminate_syslog(syslog_proc)
    return outcome


def stream_logs(
    device: Device,
    *,
    process_name: str = DEFAULT_PROCESS_NAME,
    subsystem_filter: str = DEFAULT_SUBSYSTEM,
    output_path: Path,
    also_stdout: bool = True,
    hot_window_sec: int = DEFAULT_HOT_WINDOW_SEC,
    rotate_check_bytes: int = HOT_ROTATE_CHECK_BYTES,
    archive_interval_sec: float = DEFAULT_ARCHIVE_INTERVAL_SEC,
    stall_sec: float = STREAM_RAW_STALL_SEC,
    reconnect_sleep_sec: float = STREAM_RECONNECT_SLEEP_SEC,
) -> None:
    ensure_dependencies()
    configure_stdio()

    print()
    print(f"设备: {device.name} ({device.model})")
    print(f"进程: {process_name}")
    if subsystem_filter in ("all", "-"):
        print("过滤: 无（包含 Network 等全部 subsystem）")
    else:
        print(f"过滤: 仅保留 {process_name}({subsystem_filter})")
    print(f"输出: {output_path}")
    print(f"hot 窗口: {hot_window_sec}s（更早日志 rewind 到 .archive）")
    print(f"归档调度: 每 {int(archive_interval_sec)}s 检查一次")
    print(f"stall 重启: 原始 syslog {int(stall_sec)}s 无输出则自动重启 idevicesyslog")
    print()
    print("开始采集… 按 Ctrl+C 结束")
    print("-" * 40)

    restart_count = 0
    from .archive import HotRotatingWriter

    try:
        # Keep one hot-file owner and one scheduler for the whole session.
        # Recreating them on every stall reconnect would reset the 30-minute
        # archive clock indefinitely on a quiet device.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fp:
            with HotRotatingWriter(
                fp,
                hot_path=output_path,
                device_name=device.name,
                hot_window_sec=hot_window_sec,
                check_bytes=rotate_check_bytes,
                archive_interval_sec=archive_interval_sec,
                mirror=also_stdout,
                mirror_stream=sys.stdout if also_stdout else None,
            ) as writer:
                while True:
                    try:
                        _run_syslog_once(
                            device,
                            process_name=process_name,
                            subsystem_filter=subsystem_filter,
                            output_path=output_path,
                            writer=writer,
                            stall_sec=stall_sec,
                        )
                        # EOF：设备断开或 idevicesyslog 退出 → 等待后重连
                        restart_count += 1
                        print(
                            f"idevicesyslog 已退出，{reconnect_sleep_sec:.0f}s 后重连 "
                            f"(#{restart_count})",
                            flush=True,
                        )
                        time.sleep(reconnect_sleep_sec)
                    except StallError as exc:
                        restart_count += 1
                        print(
                            f"{exc}；自动重启 idevicesyslog (#{restart_count})",
                            flush=True,
                        )
                        continue
                    except Exception as exc:  # noqa: BLE001 - 单轮异常不崩进程
                        restart_count += 1
                        print(
                            f"采集异常，{reconnect_sleep_sec:.0f}s 后重连 "
                            f"(#{restart_count}): {exc!r}",
                            flush=True,
                        )
                        time.sleep(reconnect_sleep_sec)
                        continue
    except KeyboardInterrupt:
        pass
    finally:
        print()
        print("-" * 40)
        print(f"日志已保存: {output_path}")
        if output_path.is_file():
            print(f"大小: {output_path.stat().st_size} bytes")
        hb = stream_heartbeat_path(output_path)
        for leftover in (hb, output_path.with_name(f".{output_path.name}.rotate.tmp")):
            try:
                if leftover.is_file():
                    leftover.unlink()
            except OSError:
                pass

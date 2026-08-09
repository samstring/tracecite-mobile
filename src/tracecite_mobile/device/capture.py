# -*- coding: utf-8 -*-
"""xctrace 录制 Instruments：手动开始 / 结束，最长 2 小时自动停止。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .trace_analysis import analyze_trace, export_toc, format_analysis_summary
from ..shared.constants import (
    CAPTURE_FILENAME_PREFIX,
    CAPTURE_STATE_FILENAME,
    DEFAULT_ATTACH_PROCESS,
    DEFAULT_CAPTURE_OUTPUT_DIR,
)
from .devices import Device
from tracecite_core.state_file import (
    atomic_write_json,
    process_command_contains,
    read_json,
    state_lock,
)
from .stream import sanitize_filename


class CaptureError(RuntimeError):
    pass


MAX_RECORD_DURATION = "2h"
STARTUP_READY_MAX_WAIT_SECONDS = 20
STARTUP_READY_POLL_INTERVAL_SECONDS = 0.5
# 停止后等待 xctrace 写盘；大模板（Animation Hitches 等）可能需数分钟，绝不 SIGKILL
STOP_SAVE_MAX_WAIT_SECONDS = 3600
STOP_SAVE_PROGRESS_INTERVAL_SECONDS = 10
TRACE_EXPORT_MAX_ATTEMPTS = 30
TRACE_EXPORT_RETRY_INTERVAL_SECONDS = 2
TRACE_MIN_FILE_COUNT = 5

TEMPLATE_ALIASES = {
    "cpu": "Time Profiler",
    "time": "Time Profiler",
    "profiler": "Time Profiler",
    "leak": "Leaks",
    "leaks": "Leaks",
    "alloc": "Allocations",
    "allocations": "Allocations",
    "network": "Network",
    "net": "Network",
    "launch": "App Launch",
    "system": "System Trace",
    "hitch": "Animation Hitches",
    "hitches": "Animation Hitches",
}


@dataclass
class CaptureSession:
    pid: int
    trace_path: str
    toc_path: str
    device_udid: str
    device_name: str
    output_dir: str
    template: str
    attach: str
    launch: Optional[str]
    started_at: str
    no_summarize: bool
    xctrace_log: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaptureSession":
        return cls(
            pid=int(data["pid"]),
            trace_path=str(data["trace_path"]),
            toc_path=str(data["toc_path"]),
            device_udid=str(data["device_udid"]),
            device_name=str(data["device_name"]),
            output_dir=str(data["output_dir"]),
            template=str(data["template"]),
            attach=str(data.get("attach", DEFAULT_ATTACH_PROCESS)),
            launch=data.get("launch"),
            started_at=str(data["started_at"]),
            no_summarize=bool(data.get("no_summarize", False)),
            xctrace_log=str(data["xctrace_log"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CaptureStopResult:
    trace_path: Path
    toc_path: Optional[Path]
    log_path: Optional[Path]
    analysis: Optional[TraceAnalysis]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_path": str(self.trace_path),
            "toc_path": str(self.toc_path) if self.toc_path else None,
            "log_path": str(self.log_path) if self.log_path else None,
            "analysis": self.analysis.to_dict() if self.analysis else None,
        }


def resolve_template(name: str) -> str:
    key = name.strip().lower()
    if key in TEMPLATE_ALIASES:
        return TEMPLATE_ALIASES[key]
    return name.strip()


def ensure_xctrace() -> None:
    if shutil.which("xcrun") is None:
        raise CaptureError("未找到 xcrun，请安装 Xcode Command Line Tools。")
    result = subprocess.run(
        ["xcrun", "xctrace", "help"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CaptureError(
            "未找到 xctrace，请确认 Xcode 已安装。\n"
            "可用 `xcrun xctrace list templates` 查看模板列表。"
        )


def build_capture_paths(output_dir: Path, device: Device) -> Tuple[Path, Path]:
    safe_name = sanitize_filename(device.name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{CAPTURE_FILENAME_PREFIX}_{safe_name}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"{stem}.trace"
    toc_path = output_dir / f"{stem}_toc.xml"
    return trace_path, toc_path


def capture_state_path(output_dir: Path) -> Path:
    return output_dir.expanduser().resolve() / CAPTURE_STATE_FILENAME


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def capture_process_alive(session: CaptureSession) -> bool:
    return _pid_alive(session.pid) and process_command_contains(session.pid, "xctrace")


def load_capture_session(output_dir: Path) -> Optional[CaptureSession]:
    path = capture_state_path(output_dir)
    if not path.is_file():
        return None
    try:
        data = read_json(path)
    except ValueError as exc:
        raise CaptureError(str(exc)) from exc
    return CaptureSession.from_dict(data)


def save_capture_session(output_dir: Path, session: CaptureSession) -> None:
    path = capture_state_path(output_dir)
    atomic_write_json(path, asdict(session))


def clear_capture_session(output_dir: Path) -> None:
    path = capture_state_path(output_dir)
    path.unlink(missing_ok=True)


def capture_session_payload(session: Optional[CaptureSession], *, alive: Optional[bool] = None) -> Dict[str, Any]:
    if session is None:
        return {
            "active": False,
            "session": None,
        }

    resolved_alive = capture_process_alive(session) if alive is None else alive
    return {
        "active": resolved_alive,
        "session": {
            **session.to_dict(),
            "alive": resolved_alive,
        },
    }


def _build_record_cmd(
    device: Device,
    *,
    template: str,
    attach: str,
    launch: Optional[str],
    trace_path: Path,
    no_prompt: bool,
) -> list[str]:
    cmd = [
        "xcrun", "xctrace", "record",
        "--template", template,
        "--device", device.udid,
        "--time-limit", MAX_RECORD_DURATION,
        "--output", str(trace_path),
    ]
    if no_prompt:
        cmd.append("--no-prompt")
    if launch:
        cmd.extend(["--launch", launch])
    else:
        if not attach:
            raise CaptureError(
                "capture 需要 attach 进程：请在项目 profile（.tracecite/config.json）"
                "配置 attach_process，或显式传 --attach <进程名>"
            )
        cmd.extend(["--attach", attach])
    return cmd


def _wait_for_capture_start(
    proc: subprocess.Popen[str],
    trace_path: Path,
    xctrace_log: Path,
    *,
    max_wait_seconds: float = STARTUP_READY_MAX_WAIT_SECONDS,
) -> None:
    """确认 xctrace 已真正开始写 trace，避免留下“假开始”的状态。"""
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        log_text = _read_text_tail(xctrace_log)
        file_count = _trace_file_count(trace_path)
        ready_log = "Ctrl-C to stop the recording" in log_text
        ready_files = file_count >= 1
        if trace_path.exists() and ready_log and ready_files:
            return

        exit_code = proc.poll()
        if exit_code is not None:
            log_text = log_text.strip()
            details = f"\n日志: {xctrace_log}"
            if log_text:
                details += f"\n最近输出:\n{log_text}"
            raise CaptureError(
                "xctrace 未成功开始录制，未生成 trace 文件。"
                + details
            )
        time.sleep(STARTUP_READY_POLL_INTERVAL_SECONDS)

    log_text = _read_text_tail(xctrace_log).strip()
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

    details = f"\n日志: {xctrace_log}"
    if log_text:
        details += f"\n最近输出:\n{log_text}"
    raise CaptureError(
        (
            f"xctrace 在 {int(max_wait_seconds)}s 内未完成启动确认。"
            "需要同时满足：trace 已至少写入一个文件，且日志出现录制开始提示。"
        )
        + details
    )


def _start_capture_unlocked(
    device: Device,
    *,
    template: str = "Time Profiler",
    attach: str = DEFAULT_ATTACH_PROCESS,
    launch: Optional[str] = None,
    output_dir: Path,
    no_prompt: bool = True,
    no_summarize: bool = False,
    quiet: bool = False,
) -> CaptureSession:
    """后台启动 xctrace 录制，直到 stop 或达到 2 小时上限。"""
    ensure_xctrace()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = load_capture_session(output_dir)
    if existing is not None and capture_process_alive(existing):
        raise CaptureError(
            f"已有进行中的录制（PID {existing.pid}）。\n"
            f"trace: {existing.trace_path}\n"
            "请先执行: tracecite-mobile capture stop"
        )

    if existing is not None:
        clear_capture_session(output_dir)

    resolved_template = resolve_template(template)
    trace_path, toc_path = build_capture_paths(output_dir, device)
    xctrace_log = trace_path.with_name(f"{trace_path.stem}_xctrace.log")

    record_cmd = _build_record_cmd(
        device,
        template=resolved_template,
        attach=attach,
        launch=launch,
        trace_path=trace_path,
        no_prompt=no_prompt,
    )

    log_fp = open(xctrace_log, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            record_cmd,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        log_fp.close()
        raise CaptureError(f"无法启动 xctrace: {exc}") from exc
    log_fp.close()

    try:
        _wait_for_capture_start(proc, trace_path, xctrace_log)
    except CaptureError:
        # 启动确认失败也要确保 xctrace 不残留，否则会占着设备继续录
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        _reap_process(proc.pid)
        clear_capture_session(output_dir)
        raise

    session = CaptureSession(
        pid=proc.pid,
        trace_path=str(trace_path.resolve()),
        toc_path=str(toc_path.resolve()),
        device_udid=device.udid,
        device_name=device.name,
        output_dir=str(output_dir),
        template=resolved_template,
        attach=attach,
        launch=launch,
        started_at=datetime.now().isoformat(timespec="seconds"),
        no_summarize=no_summarize,
        xctrace_log=str(xctrace_log.resolve()),
    )
    try:
        save_capture_session(output_dir, session)
    except Exception as exc:
        # 状态写不下去就必须收掉 xctrace，否则它会一直录到 2 小时上限
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except OSError:
            pass
        _reap_process(proc.pid)
        raise CaptureError(f"无法保存录制状态，已停止 xctrace: {exc}") from exc

    if not quiet:
        print()
        print(f"设备: {device.name} ({device.model})")
        print(f"模板: {resolved_template}")
        if launch:
            print(f"启动: {launch}")
        else:
            print(f"Attach: {attach}")
        print(f"输出: {trace_path.resolve()}")
        print(f"PID:  {proc.pid}")
        print(f"上限: {MAX_RECORD_DURATION}（超时自动停止）")
        print()
        print("录制已开始。请在真机上操作；结束后执行:")
        print("  tracecite-mobile capture stop")
        if output_dir.resolve() != DEFAULT_CAPTURE_OUTPUT_DIR.resolve():
            print(f"  tracecite-mobile capture stop --output-dir {output_dir}")
        print("-" * 40)
    return session


def start_capture(
    device: Device,
    *,
    template: str = "Time Profiler",
    attach: str = DEFAULT_ATTACH_PROCESS,
    launch: Optional[str] = None,
    output_dir: Path,
    no_prompt: bool = True,
    no_summarize: bool = False,
    quiet: bool = False,
) -> CaptureSession:
    with state_lock(capture_state_path(output_dir)):
        return _start_capture_unlocked(
            device,
            template=template,
            attach=attach,
            launch=launch,
            output_dir=output_dir,
            no_prompt=no_prompt,
            no_summarize=no_summarize,
            quiet=quiet,
        )


def print_capture_status(output_dir: Path) -> bool:
    """打印当前录制状态，返回是否正在录制。"""
    output_dir = output_dir.expanduser().resolve()
    session = load_capture_session(output_dir)
    if session is None:
        print("当前没有进行中的 capture 录制。")
        return False

    alive = capture_process_alive(session)
    print("Capture 状态：")
    print(f"  进行中: {'是' if alive else '否（进程已结束，请 capture stop 收尾）'}")
    print(f"  PID:    {session.pid}")
    print(f"  开始:   {session.started_at}")
    print(f"  trace:  {session.trace_path}")
    print(f"  模板:   {session.template}")
    print(f"  Attach: {session.attach}")
    return alive


def get_capture_status(output_dir: Path) -> Dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    session = load_capture_session(output_dir)
    if session is None:
        return capture_session_payload(None)
    alive = capture_process_alive(session)
    return capture_session_payload(session, alive=alive)


def _trace_file_count(trace_path: Path) -> int:
    if not trace_path.exists():
        return 0
    return sum(1 for _ in trace_path.rglob("*") if _.is_file())


def _read_text_tail(path: Path, max_bytes: int = 65536) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with open(path, "rb") as fp:
        if size > max_bytes:
            fp.seek(size - max_bytes)
        return fp.read().decode("utf-8", errors="replace")


def _log_indicates_save_complete(log_text: str) -> bool:
    return "Output file saved as:" in log_text


def _log_indicates_save_failed(log_text: str) -> bool:
    markers = (
        "Recording failed with errors",
        "Failed to start the recording",
        "Cannot find process matching name:",
    )
    return any(marker in log_text for marker in markers)


def _reap_process(pid: int) -> None:
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _wait_for_xctrace_save(
    pid: int,
    xctrace_log: Path,
    trace_path: Path,
    *,
    max_wait_seconds: float = STOP_SAVE_MAX_WAIT_SECONDS,
    quiet: bool = False,
) -> None:
    """
    发送 SIGINT 后等待 xctrace 完成写盘。

    绝不 SIGKILL；以日志「Output file saved」+ 进程退出 + trace 可 export 为准。
    """
    deadline = time.monotonic() + max_wait_seconds
    last_progress = 0.0
    started = time.monotonic()

    while time.monotonic() < deadline:
        log_text = _read_text_tail(xctrace_log)
        saved = _log_indicates_save_complete(log_text)
        alive = _pid_alive(pid)

        if saved and not alive:
            return

        if not alive:
            # 进程已退出；若日志未标记 saved，继续等 export 可成功或文件数稳定
            if saved:
                return
            if _log_indicates_save_failed(log_text):
                break
            # 给写盘/flush 一点时间，下面用 export 重试验证
            if time.monotonic() - started > 30:
                break

        now = time.monotonic()
        if not quiet and now - last_progress >= STOP_SAVE_PROGRESS_INTERVAL_SECONDS:
            elapsed = int(now - started)
            print(
                f"仍在保存 trace… 已等待 {elapsed}s"
                + ("（请勿关闭终端）" if alive else "（进程已退出，等待文件就绪）"),
                flush=True,
            )
            last_progress = now

        time.sleep(0.5)

    if _pid_alive(pid):
        raise CaptureError(
            f"xctrace 保存超过 {int(max_wait_seconds)}s 仍未完成。\n"
            f"请保持终端打开并稍后重试: tracecite-mobile capture stop\n"
            f"trace: {trace_path}\n"
            f"日志: {xctrace_log}"
        )

    log_text = _read_text_tail(xctrace_log)
    if _log_indicates_save_failed(log_text):
        raise CaptureError(
            "xctrace 录制失败，trace 可能不可用。\n"
            f"详见: {xctrace_log}"
        )


def _ensure_trace_ready(trace_path: Path, toc_path: Path) -> None:
    """确认 trace 已写完且可 export，否则抛出 CaptureError。"""
    if not trace_path.is_dir() and not trace_path.exists():
        raise CaptureError(f"未找到 trace 文件: {trace_path}")

    file_count = _trace_file_count(trace_path)
    if file_count < TRACE_MIN_FILE_COUNT:
        raise CaptureError(
            f"trace 文件不完整（仅 {file_count} 个文件，预期至少 {TRACE_MIN_FILE_COUNT}）。\n"
            f"可能仍在保存，请稍后重试: tracecite-mobile capture stop\n"
            f"trace: {trace_path}"
        )

    last_error = ""
    for attempt in range(1, TRACE_EXPORT_MAX_ATTEMPTS + 1):
        if export_toc(trace_path, toc_path):
            if toc_path.stat().st_size > 0:
                return
            last_error = "toc.xml 为空"
        else:
            last_error = "xctrace export --toc 失败"

        if attempt < TRACE_EXPORT_MAX_ATTEMPTS:
            time.sleep(TRACE_EXPORT_RETRY_INTERVAL_SECONDS)

    raise CaptureError(
        f"trace 已存在但无法导出 toc（{last_error}）。\n"
        f"trace: {trace_path}\n"
        "请稍后重试 tracecite-mobile capture stop，或手动:\n"
        f"  xcrun xctrace export --input {trace_path} --toc --output {toc_path}"
    )


def _stop_capture_unlocked(
    output_dir: Path,
    *,
    summarize: Optional[bool] = None,
    quiet: bool = False,
    log_path: Optional[Path] = None,
) -> CaptureStopResult:
    """停止录制、等待 trace 保存完成、导出 toc，并可选输出总结。"""
    ensure_xctrace()
    output_dir = output_dir.expanduser().resolve()
    session = load_capture_session(output_dir)
    if session is None:
        raise CaptureError(
            "没有进行中的 capture 录制。\n"
            "请先执行: tracecite-mobile capture start"
        )

    trace_path = Path(session.trace_path)
    toc_path = Path(session.toc_path)
    xctrace_log = Path(session.xctrace_log)
    do_summarize = not session.no_summarize if summarize is None else summarize

    if _pid_alive(session.pid) and not capture_process_alive(session):
        raise CaptureError(
            f"状态中的 PID {session.pid} 已被其他进程复用，已拒绝发送停止信号。"
        )
    if capture_process_alive(session):
        if not quiet:
            print(f"正在停止录制（PID {session.pid}）…")
        os.killpg(session.pid, signal.SIGINT)
        if not quiet:
            print("已发送停止信号，等待 xctrace 保存 trace（大文件可能需数分钟）…")
        _wait_for_xctrace_save(session.pid, xctrace_log, trace_path, quiet=quiet)
        _reap_process(session.pid)
    else:
        _reap_process(session.pid)
        if not quiet:
            print("录制进程已结束，等待 trace 就绪…")
        _wait_for_xctrace_save(session.pid, xctrace_log, trace_path, quiet=quiet)

    if not quiet:
        print("正在验证 trace 并导出 toc.xml…")
    try:
        _ensure_trace_ready(trace_path, toc_path)
    except CaptureError:
        # 保留状态文件，便于用户稍后重试 stop
        raise

    clear_capture_session(output_dir)
    if not quiet:
        print("trace 保存成功。")

    # 没有 session 状态时宁可不给路径，也不猜一个可能不存在的默认命名
    resolved_log_path = log_path
    analysis = print_capture_summary(
        trace_path,
        toc_path,
        resolved_log_path,
        summarize=do_summarize,
        quiet=quiet,
    )
    return CaptureStopResult(
        trace_path=trace_path,
        toc_path=toc_path if toc_path and toc_path.is_file() else None,
        log_path=resolved_log_path,
        analysis=analysis,
    )


def stop_capture(
    output_dir: Path,
    *,
    summarize: Optional[bool] = None,
    quiet: bool = False,
    log_path: Optional[Path] = None,
) -> CaptureStopResult:
    resolved_dir = output_dir.expanduser().resolve()
    with state_lock(capture_state_path(resolved_dir)):
        return _stop_capture_unlocked(
            resolved_dir,
            summarize=summarize,
            quiet=quiet,
            log_path=log_path,
        )


def print_capture_summary(
    trace_path: Path,
    toc_path: Optional[Path],
    log_path: Optional[Path],
    *,
    summarize: bool = True,
    quiet: bool = False,
) -> Optional[TraceAnalysis]:
    return _print_capture_summary(trace_path, toc_path, log_path, summarize=summarize, quiet=quiet)


def _print_capture_summary(
    trace_path: Path,
    toc_path: Optional[Path],
    log_path: Optional[Path],
    *,
    summarize: bool,
    quiet: bool,
) -> Optional[TraceAnalysis]:
    if not quiet:
        print()
        print("Capture 完成：")
        print(f"  trace: {trace_path.resolve()}")
        if toc_path and toc_path.is_file():
            print(f"  toc:   {toc_path.resolve()}")
        else:
            print("  toc:   （导出失败，见上方警告）")
        if log_path is not None:
            print(f"  建议 Grep 日志: {log_path.resolve()}")
        else:
            print("  建议 Grep 日志: （无进行中 session，请自行指定日志文件）")

    if not summarize:
        return None

    if not quiet:
        print()
        print("录制总结：")
    analysis = analyze_trace(trace_path, toc_path=toc_path, export_missing=True)
    if not quiet:
        if analysis.hangs_path and analysis.hangs_path.is_file():
            print(f"  hangs: {analysis.hangs_path.resolve()}")
        if analysis.hang_risks_path and analysis.hang_risks_path.is_file():
            print(f"  hang-risks: {analysis.hang_risks_path.resolve()}")
        print(format_analysis_summary(analysis, log_path))
    return analysis

# -*- coding: utf-8 -*-
"""Android 性能现场采集：Perfetto start/status/stop 状态机。

第一版只做可靠采集 + 元数据，不解析 protobuf trace 内容（见计划 §9.1）。
配置模板仓库版本化（perfetto/*.textproto），Agent 不自由生成。
错误处理覆盖：重复 start、未录制 stop、设备断开、超时、空间不足、拉取失败。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from tracecite_core.state_file import atomic_write_json, read_json, state_lock

from ..models import CaptureResult, DeviceRef
from .adb import (
    AndroidBackendError,
    AndroidAdbClient,
    AdbNoDeviceError,
    AdbOfflineError,
    AdbUnauthorizedError,
)

_PERF_CONFIG_DIR = Path(__file__).resolve().parent / "perfetto"
_TEMPLATE_NAMES: List[str] = [
    "perfetto-startup",
    "perfetto-frame",
    "perfetto-memory",
    "perfetto-network",
]
_STATE_FILENAME = ".tracecite-capture.json"

# 拉取/停止超时（秒）
_PULL_TIMEOUT = 120
_STOP_WAIT = 20
_PID_OBSERVE_RETRIES = 3
_PID_OBSERVE_DELAY = 0.1


def template_names() -> List[str]:
    return list(_TEMPLATE_NAMES)


def resolve_config(template: str) -> Path:
    if template not in _TEMPLATE_NAMES:
        raise AndroidBackendError(
            f"未知 Perfetto 模板: {template!r}（可选: {', '.join(_TEMPLATE_NAMES)}）"
        )
    path = _PERF_CONFIG_DIR / f"{template}.textproto"
    if not path.is_file():
        raise AndroidBackendError(f"模板配置文件缺失: {path}")
    return path


def _raise_for_adb_failure(res, serial: str) -> None:
    if res.ok:
        return
    stderr = (res.stderr or "").lower()
    if "device not found" in stderr or "no devices" in stderr or res.returncode == 127:
        raise AdbNoDeviceError(f"设备 {serial} 未连接或已断开。")
    if "unauthorized" in stderr:
        raise AdbUnauthorizedError(f"设备 {serial} 未授权调试。")
    if "offline" in stderr:
        raise AdbOfflineError(f"设备 {serial} 处于 offline。")
    raise AndroidBackendError(
        f"adb 命令失败（{res.returncode}）: {res.stderr.strip()}"
    )


def _state_path(output_dir: Path) -> Path:
    return Path(output_dir).expanduser().resolve() / _STATE_FILENAME


def load_state(output_dir: Path) -> Optional[Dict[str, Any]]:
    path = _state_path(output_dir)
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except ValueError as exc:
        raise AndroidBackendError(f"Android performance 状态文件不可读: {path}: {exc}") from exc


def _perfetto_pids(client: AndroidAdbClient, serial: str) -> Optional[List[int]]:
    """Return device-side collector PIDs; ``None`` means identity unknown."""

    result = client.run_adb(serial, "shell", "pidof", "perfetto")
    if not result.ok:
        return None
    pids = [int(item) for item in result.stdout.split() if item.strip().isdigit()]
    return pids


def _observe_new_pids(
    client: AndroidAdbClient,
    serial: str,
    before: List[int],
) -> Optional[List[int]]:
    """Observe collector identity with a bounded retry window.

    ``perfetto -d`` returns before ``pidof`` is guaranteed to see the new
    process.  Retry only a few times; returning ``None`` keeps the caller
    fail-closed when the device cannot provide a trustworthy identity.
    """

    for attempt in range(_PID_OBSERVE_RETRIES):
        observed = _perfetto_pids(client, serial)
        if observed is not None:
            new = sorted(set(observed).difference(before))
            if new:
                return new
        if attempt + 1 < _PID_OBSERVE_RETRIES:
            time.sleep(_PID_OBSERVE_DELAY)
    return None


def _mark_recovery_required(
    output_dir: Path,
    state: Dict[str, Any],
    message: str,
) -> None:
    """Persist enough launch context for a later, manual recovery attempt."""

    state = {
        **state,
        "phase": "recovery_required",
        "error": message,
        "recovery_required": True,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    atomic_write_json(_state_path(output_dir), state)


def start_capture(
    client: AndroidAdbClient,
    ref: DeviceRef,
    *,
    template: str,
    output_dir: Path,
    timeout: int = _PULL_TIMEOUT,
) -> Dict[str, Any]:
    output_dir = Path(output_dir).expanduser().resolve()
    with state_lock(_state_path(output_dir)):
        config = resolve_config(template)
        existing = load_state(output_dir)
        if existing is not None:
            if existing.get("phase") == "recovery_required" or existing.get(
                "recovery_required"
            ):
                raise AndroidBackendError(
                    "上一次性能采集启动未能核验设备端 PID，已保留恢复状态；"
                    "请先在设备上确认/结束采集并清理该状态后重试。"
                )
            raise AndroidBackendError(
                f"已有进行中的性能采集（profile {existing.get('profile') or existing.get('template')}）。\n"
                "请先 capture stop。"
            )
        output_dir.mkdir(parents=True, exist_ok=True)

        before_pids = _perfetto_pids(client, ref.identifier)
        if before_pids is None:
            raise AndroidBackendError(
                f"无法核验设备 {ref.identifier} 已有的性能采集 PID，已拒绝开始。"
            )

        token = uuid4().hex[:16]
        session_id = f"android-performance-{ref.identifier}-{token}"
        # Do not share remote names across output directories/sessions: a
        # second start must never truncate the first session's config/trace.
        remote_cfg = f"/data/local/tmp/tracecite_perf_{token}.textproto"
        remote_trace = f"/data/misc/perfetto-traces/tracecite_perf_{token}.pb"
        started_at = datetime.now().isoformat(timespec="seconds")
        state: Dict[str, Any] = {
            "platform": "android",
            "session_id": session_id,
            "serial": ref.identifier,
            "model": ref.model,
            "profile": template,
            # Legacy capture consumers continue to read template/config_name.
            "template": template,
            "config_name": config.name,
            "remote_config": remote_cfg,
            "remote_trace": remote_trace,
            "perfetto_pids": [],
            "local_trace_path": str(output_dir / f"perfetto_{template}.pb"),
            "started_at": started_at,
            "output_dir": str(output_dir),
            "state_schema_version": 3,
            "phase": "starting",
            "recovery_required": False,
        }
        # Persist the launch context before invoking the detached collector so
        # every post-start error leaves recoverable state rather than an
        # untracked device-side process.
        atomic_write_json(_state_path(output_dir), state)
        config_pushed = False
        start_invoked = False
        try:
            # 推送配置到设备
            push = client.run_adb(ref.identifier, "push", str(config), remote_cfg)
            _raise_for_adb_failure(push, ref.identifier)
            config_pushed = True

            # 先清理当前 session 唯一的旧 trace（通常不存在）
            client.run_adb(ref.identifier, "shell", "rm", "-f", remote_trace)
            # -d 后台分离录制；--txt 因为 config 是 textproto 格式
            # Mark before invoking the runner: a transport exception may still
            # mean the detached process was created on the device.
            start_invoked = True
            start = client.run_adb(
                ref.identifier,
                "shell",
                "perfetto",
                "-c",
                remote_cfg,
                "-o",
                remote_trace,
                "-d",
                "--txt",
            )
            # A command returning an error can still have spawned a detached
            # process; from this point on retain recovery state on all errors.
            _raise_for_adb_failure(start, ref.identifier)

            # Detached collectors must be tied to the PIDs observed for this
            # start.  A bounded retry handles the normal pidof startup race;
            # failure remains fail-closed and keeps the launch context.
            pids = _observe_new_pids(client, ref.identifier, before_pids)
            if pids is None:
                raise AndroidBackendError(
                    f"无法确认设备 {ref.identifier} 的性能采集 PID；"
                    "已保留恢复状态，拒绝写入可停止状态。"
                )

            state.update(
                {
                    "perfetto_pids": pids,
                    "phase": "running",
                    "recovery_required": False,
                }
            )
            atomic_write_json(_state_path(output_dir), state)
            return state
        except Exception as exc:
            if start_invoked:
                _mark_recovery_required(output_dir, state, str(exc))
            else:
                # No detached collector was invoked; remove provisional state
                # and best-effort clean the pushed config.
                _state_path(output_dir).unlink(missing_ok=True)
                if config_pushed:
                    client.run_adb(ref.identifier, "shell", "rm", "-f", remote_cfg)
            raise


def _device_perfetto_alive(client: AndroidAdbClient, serial: str) -> bool:
    pids = _perfetto_pids(client, serial)
    return bool(pids)


def get_capture_status(
    output_dir: Path, client: Optional[AndroidAdbClient] = None
) -> Dict[str, Any]:
    with state_lock(_state_path(output_dir)):
        state = load_state(output_dir)
        if state is None:
            return {"active": False, "session": None}
        serial = state.get("serial", "")
        # A caller without a client can only inspect state, never claim a
        # device-side collector is healthy.
        pids = _perfetto_pids(client, serial) if client else None
        recorded = state.get("perfetto_pids")
        known = isinstance(recorded, list) and all(
            isinstance(pid, int) and pid > 0 for pid in recorded
        )
        alive = bool(known and pids and set(recorded).intersection(pids))
        return {"active": alive, "session": {**state, "alive": alive}}


def stop_capture(
    client: AndroidAdbClient, output_dir: Path
) -> CaptureResult:
    output_dir = Path(output_dir).expanduser().resolve()
    with state_lock(_state_path(output_dir)):
        state = load_state(output_dir)
        if state is None:
            raise AndroidBackendError("当前没有进行中的性能采集。")
        serial = state.get("serial", "")
        template = state.get("profile") or state.get("template", "")
        started_at = state.get("started_at", "")
        recorded = state.get("perfetto_pids")
        if not isinstance(recorded, list) or not recorded or not all(
            isinstance(pid, int) and pid > 0 for pid in recorded
        ):
            raise AndroidBackendError(
                "旧性能状态缺少采集 PID，已拒绝停止以避免误杀设备上的其他采集进程；"
                "请在原设备上结束采集或清理状态后重新开始。"
            )

        remote_trace = str(state.get("remote_trace") or "").strip()
        local_trace_raw = str(state.get("local_trace_path") or "").strip()
        if not remote_trace or not local_trace_raw:
            raise AndroidBackendError(
                "性能状态缺少 trace 路径，已拒绝停止以避免拉取未知文件；"
                "请保留状态并在原设备上手动恢复。"
            )
        local_trace = Path(local_trace_raw).expanduser().resolve()

        # Stop only the exact PIDs recorded for this session.  If the device
        # cannot report its current collector set, fail closed.
        current = _perfetto_pids(client, serial)
        if current is None:
            raise AndroidBackendError(
                f"无法核验设备 {serial} 的性能采集 PID，已拒绝停止。"
            )
        targeted = sorted(set(recorded).intersection(current))
        if current and not targeted:
            raise AndroidBackendError(
                f"设备 {serial} 当前的性能采集 PID 与本 session 不匹配，已拒绝停止。"
            )

        state.update(
            {
                "phase": "stopping",
                "stop_requested_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        atomic_write_json(_state_path(output_dir), state)
        for pid in targeted:
            client.run_adb(serial, "shell", "kill", "-INT", str(pid))

        # 等待 trace 落盘
        deadline = time.time() + _STOP_WAIT
        collector_stopped = not targeted
        while targeted:
            after = _perfetto_pids(client, serial)
            if after is None:
                message = (
                    f"停止设备 {serial} 的性能采集后无法核验 PID；"
                    "已保留 stopping 状态，请稍后重试。"
                )
                _mark_recovery_required(output_dir, state, message)
                raise AndroidBackendError(message)
            if not set(recorded).intersection(after):
                collector_stopped = True
                break
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        if not collector_stopped:
            message = (
                f"设备 {serial} 的性能采集 PID 在停止超时后仍存活；"
                "已保留 stopping 状态，请稍后重试，拒绝拉取并清理状态。"
            )
            _mark_recovery_required(output_dir, state, message)
            raise AndroidBackendError(message)

        # 拉取 trace
        pull = client.run_adb(
            serial, "pull", remote_trace, str(local_trace), timeout=_PULL_TIMEOUT
        )
        _raise_for_adb_failure(pull, serial)

        if not local_trace.is_file() or local_trace.stat().st_size == 0:
            raise AndroidBackendError(
                f"性能 trace 拉取失败或为空: {local_trace}\n"
                "可能原因：设备断开、空间不足、或录制未产出数据。"
            )

        # 元数据
        meta_path = local_trace.with_suffix(".meta.json")
        meta = {
            "platform": "android",
            "serial": serial,
            "profile": template,
            "template": template,
            "remote_trace": remote_trace,
            "local_trace": str(local_trace),
            "started_at": started_at,
            "stopped_at": datetime.now().isoformat(timespec="seconds"),
            "size_bytes": local_trace.stat().st_size,
        }
        atomic_write_json(meta_path, meta)

        _state_path(output_dir).unlink(missing_ok=True)

        device = DeviceRef(
            platform="android",
            identifier=serial,
            name=state.get("model", "") or serial,
            model=state.get("model", "") or "",
        )
        return CaptureResult(
            platform="android",
            device=device,
            trace_path=local_trace,
            metadata_path=meta_path,
            summary_path=None,
            profile=(
                {"perfetto-startup": "startup", "perfetto-frame": "frame", "perfetto-memory": "memory", "perfetto-network": "network"}
                .get(template, template)
            ),
        )

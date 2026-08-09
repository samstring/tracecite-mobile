# -*- coding: utf-8 -*-
"""Android 性能现场采集：Perfetto start/status/stop 状态机。

第一版只做可靠采集 + 元数据，不解析 protobuf trace 内容（见计划 §9.1）。
配置模板仓库版本化（perfetto/*.textproto），Agent 不自由生成。
错误处理覆盖：重复 start、未录制 stop、设备断开、超时、空间不足、拉取失败。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def start_capture(
    client: AndroidAdbClient,
    ref: DeviceRef,
    *,
    template: str,
    output_dir: Path,
    timeout: int = _PULL_TIMEOUT,
) -> Dict[str, Any]:
    config = resolve_config(template)
    existing = load_state(output_dir)
    if existing is not None:
        raise AndroidBackendError(
            f"已有进行中的 Perfetto 录制（模板 {existing.get('template')}）。\n"
            "请先 capture stop。"
        )
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # 推送配置到设备
    remote_cfg = f"/data/local/tmp/xcode_debug_{template}.textproto"
    push = client.run_adb(ref.identifier, "push", str(config), remote_cfg)
    _raise_for_adb_failure(push, ref.identifier)

    remote_trace = f"/data/misc/perfetto-traces/xcode_debug_{template}.pb"
    # 先清理可能存在的旧 trace
    client.run_adb(ref.identifier, "shell", "rm", "-f", remote_trace)
    # -d 后台分离录制；--txt 因为 config 是 textproto 格式
    start = client.run_adb(
        ref.identifier, "shell", "perfetto", "-c", remote_cfg, "-o", remote_trace, "-d", "--txt"
    )
    _raise_for_adb_failure(start, ref.identifier)

    started_at = datetime.now().isoformat(timespec="seconds")
    state = {
        "platform": "android",
        "serial": ref.identifier,
        "template": template,
        "config_name": config.name,
        "remote_config": remote_cfg,
        "remote_trace": remote_trace,
        "local_trace_path": str(output_dir / f"perfetto_{template}.pb"),
        "started_at": started_at,
        "output_dir": str(output_dir),
    }
    _state_path(output_dir).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


def _device_perfetto_alive(client: AndroidAdbClient, serial: str) -> bool:
    res = client.run_adb(serial, "shell", "pidof", "perfetto")
    if not res.ok:
        return False
    return bool(res.stdout.strip())


def get_capture_status(
    output_dir: Path, client: Optional[AndroidAdbClient] = None
) -> Dict[str, Any]:
    state = load_state(output_dir)
    if state is None:
        return {"active": False, "session": None}
    serial = state.get("serial", "")
    # 有 client 时真实检测设备端 perfetto 存活；否则信任状态文件
    alive = _device_perfetto_alive(client, serial) if client else True
    return {"active": alive, "session": {**state, "alive": alive}}


def stop_capture(
    client: AndroidAdbClient, output_dir: Path
) -> CaptureResult:
    state = load_state(output_dir)
    if state is None:
        raise AndroidBackendError("当前没有进行中的 Perfetto 录制。")
    serial = state.get("serial", "")
    remote_trace = state.get("remote_trace", "")
    local_trace = Path(state.get("local_trace_path", "")).expanduser().resolve()
    template = state.get("template", "")
    started_at = state.get("started_at", "")

    # 停止设备端 perfetto
    pid_res = client.run_adb(serial, "shell", "pidof", "perfetto")
    if pid_res.ok and pid_res.stdout.strip():
        for pid in pid_res.stdout.split():
            pid = pid.strip()
            if pid.isdigit():
                client.run_adb(serial, "shell", "kill", "-INT", pid)
    else:
        # 没有 perfetto 进程也可能已经结束；继续尝试 pull
        pass

    # 等待 trace 落盘
    deadline = time.time() + _STOP_WAIT
    while time.time() < deadline:
        if not _device_perfetto_alive(client, serial):
            break
        time.sleep(0.5)

    # 拉取 trace
    pull = client.run_adb(
        serial, "pull", remote_trace, str(local_trace), timeout=_PULL_TIMEOUT
    )
    _raise_for_adb_failure(pull, serial)

    if not local_trace.is_file() or local_trace.stat().st_size == 0:
        raise AndroidBackendError(
            f"Perfetto trace 拉取失败或为空: {local_trace}\n"
            "可能原因：设备断开、空间不足、或录制未产出数据。"
        )

    # 元数据
    meta_path = local_trace.with_suffix(".meta.json")
    meta = {
        "platform": "android",
        "serial": serial,
        "template": template,
        "remote_trace": remote_trace,
        "local_trace": str(local_trace),
        "started_at": started_at,
        "stopped_at": datetime.now().isoformat(timespec="seconds"),
        "size_bytes": local_trace.stat().st_size,
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _state_path(output_dir).unlink(missing_ok=True)

    device = DeviceRef(
        platform="android",
        identifier=serial,
        name=state.get("model", "") or serial,
    )
    return CaptureResult(
        platform="android",
        device=device,
        trace_path=local_trace,
        metadata_path=meta_path,
        summary_path=None,
    )

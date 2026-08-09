# -*- coding: utf-8 -*-
"""通过 devicectl 列出已连接 iOS 真机。"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class Device:
    name: str
    udid: str
    model: str

    def display(self, index: int) -> str:
        return f"  [{index}] {self.name}  ({self.model})\n      UDID: {self.udid}"


@dataclass(frozen=True)
class RunningProcess:
    pid: int
    name: str


class DeviceError(RuntimeError):
    pass


def list_connected_devices() -> List[Device]:
    """返回 tunnelState=connected 的真机列表。"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        json_path = Path(tmp.name)

    try:
        result = subprocess.run(
            ["xcrun", "devicectl", "list", "devices", "--json-output", str(json_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise DeviceError(
                "无法列出设备，请确认 Xcode 已安装且设备已信任。"
                + (f"\n{stderr}" if stderr else "")
            )

        with json_path.open(encoding="utf-8") as f:
            payload = json.load(f)

        devices: List[Device] = []
        for dev in payload.get("result", {}).get("devices", []):
            conn = dev.get("connectionProperties", {})
            # tunnelState：connected=隧道连接；disconnected=USB 直连（无隧道，同样可用）；
            # unavailable=不可用（未配对/未连接）。只排除 unavailable，否则 USB 直连
            # 的可用设备会被误过滤成「没有已连接的真机」。
            if conn.get("tunnelState") == "unavailable":
                continue
            props = dev.get("deviceProperties", {})
            hw = dev.get("hardwareProperties", {})
            name = props.get("name") or "Unknown"
            udid = hw.get("udid") or ""
            model = hw.get("marketingName") or hw.get("deviceType") or ""
            if udid:
                devices.append(Device(name=name, udid=udid, model=model))
        return devices
    finally:
        json_path.unlink(missing_ok=True)


def _executable_name(raw_executable: str) -> str:
    if not raw_executable:
        return ""
    if raw_executable.startswith("file://"):
        path = unquote(urlparse(raw_executable).path)
        return Path(path).name
    return Path(raw_executable).name


def _list_running_processes(device: Device) -> List[RunningProcess]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        json_path = Path(tmp.name)
    try:
        result = subprocess.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "info",
                "processes",
                "--device",
                device.udid,
                "--json-output",
                str(json_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            raise DeviceError(
                f"无法列出设备进程: {device.name} ({device.udid})"
                + (f"\n{stderr}" if stderr else "")
            )
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        processes: List[RunningProcess] = []
        for item in payload.get("result", {}).get("runningProcesses", []):
            name = _executable_name(str(item.get("executable", "")))
            pid = int(item.get("processIdentifier", 0))
            if pid > 0 and name:
                processes.append(RunningProcess(pid=pid, name=name))
        return processes
    finally:
        json_path.unlink(missing_ok=True)


def find_running_process(
    device: Device,
    process_name: str,
) -> Optional[RunningProcess]:
    if process_name.isdigit():
        pid = int(process_name)
        for proc in _list_running_processes(device):
            if proc.pid == pid:
                return proc
        return None

    needle = process_name.lower()
    matched = [
        proc
        for proc in _list_running_processes(device)
        if proc.name.lower() == needle or needle in proc.name.lower()
    ]
    if not matched:
        return None
    exact = [proc for proc in matched if proc.name.lower() == needle]
    return exact[0] if exact else matched[0]


def ensure_process_running(device: Device, process_name: str) -> RunningProcess:
    process = find_running_process(device, process_name)
    if process is None:
        raise DeviceError(
            f"设备上未找到运行中的进程: {process_name}\n"
            "请先启动目标 App 后重试。"
        )
    return process


def resolve_device(
    *,
    udid: Optional[str] = None,
    name: Optional[str] = None,
    index: Optional[int] = None,
    interactive: bool = True,
) -> Device:
    devices = resolve_devices(
        udids=[udid] if udid else None,
        name=name,
        indices=[index] if index is not None else None,
        all_devices=False,
        interactive=interactive,
    )
    return devices[0]


def resolve_devices(
    *,
    udids: Optional[List[str]] = None,
    name: Optional[str] = None,
    indices: Optional[List[int]] = None,
    all_devices: bool = False,
    interactive: bool = True,
) -> List[Device]:
    """解析一台或多台设备；all_devices 时返回全部已连接真机。"""
    devices = list_connected_devices()
    if not devices:
        raise DeviceError(
            "没有已连接的真机（tunnelState=connected）。\n"
            "请用 USB 连接设备并信任此电脑，或确认已在 Xcode 中完成配对。"
        )

    if all_devices:
        return list(devices)

    selected: List[Device] = []
    if udids:
        by_udid = {d.udid: d for d in devices}
        for raw in udids:
            udid = (raw or "").strip()
            if not udid:
                continue
            if udid not in by_udid:
                raise DeviceError(f"未找到 UDID: {udid}")
            selected.append(by_udid[udid])

    if indices:
        for index in indices:
            if not (1 <= index <= len(devices)):
                raise DeviceError(f"无效序号 {index}，可选范围 1-{len(devices)}")
            selected.append(devices[index - 1])

    if name and not selected:
        matched = [d for d in devices if name.lower() in d.name.lower()]
        if len(matched) == 1:
            selected.append(matched[0])
        elif len(matched) > 1:
            if not interactive:
                lines = "\n".join(d.display(i + 1) for i, d in enumerate(matched))
                raise DeviceError(
                    f"设备名 '{name}' 匹配多台，请用 --udid / --index 指定，或去掉 --no-interactive：\n"
                    + lines
                )
            selected.append(_prompt_device_choice(matched))
        else:
            raise DeviceError(f"未找到名称包含 '{name}' 的设备")

    # 去重保序
    if selected:
        seen = set()
        unique: List[Device] = []
        for dev in selected:
            if dev.udid in seen:
                continue
            seen.add(dev.udid)
            unique.append(dev)
        return unique

    if len(devices) == 1:
        return [devices[0]]

    if not interactive:
        lines = "\n".join(d.display(i + 1) for i, d in enumerate(devices))
        raise DeviceError(
            "已连接多台设备，请指定 --device / --udid / --index / --all，"
            "或去掉 --no-interactive 以交互选择：\n" + lines
        )

    return [_prompt_device_choice(devices)]


def _prompt_device_choice(devices: List[Device]) -> Device:
    print("请选择要采集日志的设备：\n")
    for i, dev in enumerate(devices, start=1):
        print(dev.display(i))
    print()

    while True:
        try:
            choice = input(f"输入序号 [1-{len(devices)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise DeviceError("已取消设备选择") from None
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(devices):
                return devices[idx - 1]
        print("无效序号，请重试。")

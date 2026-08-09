# -*- coding: utf-8 -*-
"""Android 设备枚举与选择：serial / name / index，错误可操作。"""

from __future__ import annotations

import sys
from typing import List, Optional

from ..models import DeviceRef
from .adb import (
    AdbDeviceNotFoundError,
    AdbOfflineError,
    AdbUnauthorizedError,
    AndroidAdbClient,
    AdbNoDeviceError,
)


def _with_models(client: AndroidAdbClient, devices) -> List[DeviceRef]:
    refs: List[DeviceRef] = []
    for d in devices:
        if not d.model:
            d.model = client.model_of(d.serial)
        refs.append(d.to_ref())
    return refs


def list_devices(client: AndroidAdbClient) -> List[DeviceRef]:
    """解析 adb devices -l，填充 model，返回统一 DeviceRef 列表。"""
    raw = client.list_devices()
    return _with_models(client, raw)


def _authorize_state(ref: DeviceRef) -> None:
    if ref.state == "unauthorized":
        raise AdbUnauthorizedError(
            f"设备 {ref.name} ({ref.identifier}) 处于 unauthorized。\n"
            "请在设备上点击「允许 USB 调试」并重新插拔。"
        )
    if ref.state == "offline":
        raise AdbOfflineError(
            f"设备 {ref.name} ({ref.identifier}) 处于 offline。\n"
            "请重连 USB 或执行 adb kill-server && adb start-server。"
        )


def resolve_device(
    client: AndroidAdbClient,
    *,
    serial: Optional[str] = None,
    name: Optional[str] = None,
    index: Optional[int] = None,
    interactive: bool = True,
) -> DeviceRef:
    """设备选择：0 台停；1 台自动选；多台必须让用户选；unauthorized/offline 只提示。"""
    refs = list_devices(client)
    if not refs:
        raise AdbNoDeviceError(
            "没有已连接的 Android 设备。\n"
            "请确认：USB 调试已开启；设备已授权；adb devices -l 可见。"
        )

    candidates = refs
    if serial:
        matched = [r for r in refs if r.identifier == serial]
        if not matched:
            raise AdbDeviceNotFoundError(f"未找到 serial: {serial}")
        candidates = matched
    elif name:
        matched = [
            r
            for r in refs
            if name.lower() in (r.name or "").lower()
            or name.lower() in (r.identifier or "").lower()
        ]
        if not matched:
            raise AdbDeviceNotFoundError(f"未找到名称包含 '{name}' 的设备")
        candidates = matched

    # 未指定 serial/name 时：若多台里只有一台处于可用状态（device），直接选它，
    # 避免对唯一可用设备还要手动选择；多台可用仍要求用户指定/选择。
    if serial is None and name is None and len(candidates) > 1:
        usable = [r for r in candidates if r.state == "device"]
        if len(usable) == 1:
            ref = usable[0]
            _authorize_state(ref)
            return ref

    if len(candidates) == 1:
        ref = candidates[0]
        _authorize_state(ref)
        return ref

    # 多台设备
    if index is not None:
        if 1 <= index <= len(candidates):
            ref = candidates[index - 1]
            _authorize_state(ref)
            return ref
        raise AdbDeviceNotFoundError(f"无效序号 {index}，可选范围 1-{len(candidates)}")

    if not interactive:
        lines = "\n".join(r.display(i + 1) for i, r in enumerate(candidates))
        raise AdbDeviceNotFoundError(
            "已连接多台设备，请指定 --serial / --index，或去掉 --no-interactive：\n"
            + lines
        )

    return _prompt_choice(candidates)


def _prompt_choice(candidates: List[DeviceRef]) -> DeviceRef:
    print("请选择要采集日志的设备：\n")
    for i, ref in enumerate(candidates, start=1):
        print(ref.display(i))
    print()
    while True:
        try:
            choice = input(f"输入序号 [1-{len(candidates)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise AdbDeviceNotFoundError("已取消设备选择") from None
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                ref = candidates[idx - 1]
                _authorize_state(ref)
                return ref
        print("无效序号，请重试。")

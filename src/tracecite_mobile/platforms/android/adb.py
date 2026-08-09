# -*- coding: utf-8 -*-
"""Android ADB 客户端：参数数组、可注入 runner、状态解析与错误分类。

安全问题：
- 仅用 shutil.which 检测 adb 是否存在，绝不修改 PATH 或自动安装 SDK。
- 所有命令走参数数组，不拼接 shell 字符串；均带 timeout 与 stderr 捕获。
- 不执行隐含清日志（adb logcat -c）、不删除设备文件、不重启 App。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import List, Optional

from ..base import BackendError, RunResult, default_run

_ADB_DEVICES_RE = re.compile(
    r"^(?P<serial>\S+)\s+(?P<state>device|unauthorized|offline|"
    r"no permissions|recovery|sideload|bootloader|disconnected)\b"
)
_ADB_PROP_RE = re.compile(r"\[(?P<key>[^\]]+)\]:\s*\[(?P<value>[^\]]*)\]")


class AndroidBackendError(BackendError):
    """Android 后端错误基类。"""


class AdbNotInstalledError(AndroidBackendError):
    """未安装 adb / android-platform-tools。"""


class AdbNoDeviceError(AndroidBackendError):
    """没有任何已连接设备。"""


class AdbUnauthorizedError(AndroidBackendError):
    """设备 unauthorized（未在该电脑授权调试）。"""


class AdbOfflineError(AndroidBackendError):
    """设备 offline。"""


class AdbDeviceNotFoundError(AndroidBackendError):
    """按 serial / name / index 未匹配到唯一设备。"""


class AppNotRunningError(AndroidBackendError):
    """目标 App 未在设备上运行。"""


@dataclass
class AdbDevice:
    serial: str
    state: str  # device / unauthorized / offline / ...
    model: str = ""
    product: str = ""

    def to_ref(self) -> "DeviceRef":  # noqa: F821
        from ..models import DeviceRef

        return DeviceRef(
            platform="android",
            identifier=self.serial,
            name=self.model or self.serial,
            model=self.model,
            state=self.state,
        )


class AndroidAdbClient:
    """对 adb 的薄封装；runner 可注入用于 fake-adb 测试。"""

    def __init__(self, run=None, adb_path: Optional[str] = None) -> None:
        self._run = run or default_run
        # adb_path=None 表示允许跳过 which 检查（测试注入 runner 时）
        self.adb_path = adb_path if adb_path is not None else shutil.which("adb")

    # ---- 基础 ----
    def require_adb(self) -> str:
        if not self.adb_path:
            raise AdbNotInstalledError(
                "未找到 adb，请先安装 Android platform-tools：\n"
                "  brew install android-platform-tools\n"
                "  adb version\n  adb devices -l"
            )
        return self.adb_path

    def _cmd(self, serial: Optional[str], *args: str) -> List[str]:
        self.require_adb()
        base = [self.adb_path]
        if serial:
            base += ["-s", serial]
        return base + list(args)

    def run_adb(
        self, serial: Optional[str], *args: str, timeout: Optional[float] = None
    ) -> RunResult:
        return self._run(self._cmd(serial, *args), timeout=timeout)

    # ---- 设备枚举 ----
    @staticmethod
    def parse_devices(text: str) -> List[AdbDevice]:
        devices: List[AdbDevice] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("List of devices"):
                continue
            m = _ADB_DEVICES_RE.match(line)
            if not m:
                continue
            devices.append(
                AdbDevice(serial=m.group("serial"), state=m.group("state"))
            )
        return devices

    def list_devices(self) -> List[AdbDevice]:
        res = self.run_adb(None, "devices", "-l")
        if not res.ok:
            raise AndroidBackendError(
                f"adb devices -l 失败（{res.returncode}）: {res.stderr.strip()}"
            )
        return self.parse_devices(res.stdout)

    def getprop(self, serial: str, prop: str) -> str:
        res = self.run_adb(serial, "shell", "getprop", prop)
        if not res.ok:
            return ""
        return res.stdout.strip()

    def model_of(self, serial: str) -> str:
        model = self.getprop(serial, "ro.product.model")
        if not model:
            product = self.getprop(serial, "ro.product.name")
            model = product or serial
        return model

    def pidof(self, serial: str, package: str) -> Optional[int]:
        """返回 App 主进程 pid；未运行返回 None。"""
        res = self.run_adb(serial, "shell", "pidof", package)
        if not res.ok:
            return None
        text = res.stdout.strip()
        if not text:
            return None
        # pidof 可能返回多个 pid，取第一个
        first = text.split()[0]
        if first.isdigit():
            return int(first)
        return None

    # ---- 前台/后台 logcat 进程 ----
    def spawn_logcat(
        self,
        serial: str,
        output_path,
        *,
        priority: Optional[str] = None,
        tag: Optional[str] = None,
        pid: Optional[int] = None,
        log_fp=None,
    ):
        """启动 logcat 子进程（Popen）。

        前台（stream_logs）用 PIPE 逐行读取；后台（session start）传 log_fp
        让子进程直接写入文件。
        """
        import subprocess

        cmd = self._cmd(serial, "logcat", "-v", "threadtime")
        if pid is not None:
            cmd += ["--pid", str(pid)]
        if tag:
            cmd += ["-s", f"{tag}:*"]
        if priority:
            cmd += [f"*:{priority}"]
        # 后台 session 须脱离父进程组，否则父进程退出时子进程被连带杀掉
        return subprocess.Popen(
            cmd,
            stdout=log_fp or subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    # ---- 截图 ----
    def screencap(self, serial: str) -> bytes:
        """执行 adb exec-out screencap -p，返回 PNG 原始字节。"""
        import subprocess

        cmd = self._cmd(serial, "exec-out", "screencap", "-p")
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise AndroidBackendError("screencap 超时（>15s）")
        if proc.returncode != 0:
            raise AndroidBackendError(
                f"screencap 失败（{proc.returncode}）: {proc.stderr.strip()}"
            )
        return proc.stdout

    # ---- 错误分类辅助 ----
    @staticmethod
    def classify_no_device(devices: List[AdbDevice]) -> None:
        if not devices:
            raise AdbNoDeviceError(
                "没有已连接的 Android 设备。\n"
                "请确认：USB 调试已开启；设备已授权；adb devices -l 可见。"
            )

"""TraceCite Mobile 设备/平台特定常量（不在 core tracecite_core 中）。"""

from pathlib import Path

DEFAULT_CAPTURE_OUTPUT_DIR = Path.home() / "Desktop" / "TraceCite" / "Instrument"
DEFAULT_CAPTURE_TEMPLATE = "Time Profiler"
LOG_FILENAME_PREFIX = "ios_live"
CAPTURE_FILENAME_PREFIX = "capture"
CAPTURE_STATE_FILENAME = ".tracecite-capture.json"
STREAM_RAW_STALL_SEC = 90
STREAM_HEARTBEAT_STALE_SEC = 120
STREAM_HEARTBEAT_TOUCH_INTERVAL_SEC = 5
STREAM_RECONNECT_SLEEP_SEC = 5
ANDROID_LOGCAT_FORMAT = "threadtime"
DEFAULT_ATTACH_PROCESS = ""

ANDROID_FILTER_PRESET_NAMES = (
    "android-anr", "android-crash", "android-user-behavior",
    "android-network", "android-memory", "android-startup", "android-custom",
)
ANDROID_DEFAULT_CAPTURE_TEMPLATE = "perfetto-frame"

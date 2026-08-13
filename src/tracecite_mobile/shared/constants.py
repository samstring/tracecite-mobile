"""TraceCite Mobile defaults; Core owns no device or project conventions."""

from pathlib import Path

DEFAULT_OUTPUT_ROOT_DIR = Path.home() / "Desktop" / "TraceCite"
DEFAULT_LOG_OUTPUT_DIR = DEFAULT_OUTPUT_ROOT_DIR / "Log"
DEFAULT_CAPTURE_OUTPUT_DIR = DEFAULT_OUTPUT_ROOT_DIR / "Instrument"
DEFAULT_CAPTURE_TEMPLATE = "Time Profiler"
DEFAULT_PROCESS_NAME = ""
DEFAULT_SUBSYSTEM = ""
DEFAULT_ATTACH_PROCESS = ""
LOG_FILENAME_PREFIX = "ios_live"
CAPTURE_FILENAME_PREFIX = "capture"
KNOWLEDGE_BASENAME_IOS = "knowledge.ios.json"
KNOWLEDGE_BASENAME_ANDROID = "knowledge.android.json"
CAPTURE_STATE_FILENAME = ".tracecite-capture.json"
SESSIONS_STATE_FILENAME = ".tracecite-sessions.json"
# Internal hot-log archive storage.  Users access it through archive list/pull;
# the leading dot keeps implementation artifacts out of normal directory views.
ARCHIVE_DIRNAME = ".archive"
LEGACY_ARCHIVE_DIRNAME = "archive"
ARCHIVE_MANIFEST_FILENAME = "manifest.json"
ARCHIVE_PULLED_DIRNAME = "pulled"
PROJECT_META_DIRNAME = ".tracecite"
PROFILE_BASENAME = "config.json"
GITIGNORE_PROJECT_META_ENTRY = "/.tracecite/"
DEFAULT_HOT_WINDOW_SEC = 30 * 60
DEFAULT_ARCHIVE_INTERVAL_SEC = 30 * 60
STREAM_RAW_STALL_SEC = 90
STREAM_HEARTBEAT_STALE_SEC = 120
STREAM_HEARTBEAT_TOUCH_INTERVAL_SEC = 5
STREAM_RECONNECT_SLEEP_SEC = 5
# Deprecated compatibility only.  Archive scheduling is time-based through
# DEFAULT_ARCHIVE_INTERVAL_SEC; byte volume must not cause frequent rotation.
HOT_ROTATE_CHECK_BYTES = 256 * 1024
ANDROID_LOGCAT_FORMAT = "threadtime"
ANDROID_FILTER_PRESET_NAMES = (
    "android-anr",
    "android-crash",
    "android-system",
    "android-network",
    "android-memory",
    "android-startup",
    "android-custom",
)
ANDROID_DEFAULT_CAPTURE_TEMPLATE = "perfetto-frame"
ANDROID_LOG_OUTPUT_DIR = DEFAULT_OUTPUT_ROOT_DIR / "Log" / "Android"
ANDROID_CAPTURE_OUTPUT_DIR = DEFAULT_OUTPUT_ROOT_DIR / "Instrument" / "Android"
ANDROID_ANALYSIS_OUTPUT_DIR = DEFAULT_OUTPUT_ROOT_DIR / "AndroidAnalysis"
DEFAULT_ANALYSIS_OUTPUT_DIR = DEFAULT_OUTPUT_ROOT_DIR / "analysis"
DEFAULT_RUN_OUTPUT_DIR = DEFAULT_ANALYSIS_OUTPUT_DIR / "runs"
DEFAULT_OUTPUT_DIR = DEFAULT_LOG_OUTPUT_DIR

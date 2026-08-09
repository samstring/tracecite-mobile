"""声明式格式预设（可显式选择的 applog-format）。

这些从前在 core 的 _PRESET_FORMATS 中，现在作为上层插件注入。
"""

from typing import Any, Dict

PRESET_FORMATS: Dict[str, Dict[str, Any]] = {
    "applog-format": {
        "start": (
            r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)"
            r"\s+[^:]+:\s"
        ),
        "timestamp_formats": [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
        ],
        "multiline": True,
        "header_strip": (
            r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[^:]+?:\s*"
        ),
    },
}

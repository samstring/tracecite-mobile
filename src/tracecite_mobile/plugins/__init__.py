"""Mobile formats registered explicitly through the public Core Plugin API."""

from __future__ import annotations


from tracecite_core.plugin_sdk import PluginAPI


def register_all(api: PluginAPI) -> None:
    """Register Mobile formats; conflicts fail unless a caller opts to replace."""

    from .segmenters import (
        APPLOG_HEAD_RE,
        APPLOG_TS_RE,
        SYSLOG_TS_RE,
        THREADTIME_TS_RE,
        AppLogSegmenter,
        DeviceLogSegmenter,
        MixedLogSegmenter,
        detect_segmenter_kind,
    )
    from .formats import PRESET_FORMATS

    api.register_segmenter(
        "devicelog",
        DeviceLogSegmenter,
        aliases=("syslog", "ios", "android", "threadtime"),
    )
    api.register_segmenter("applog", AppLogSegmenter, aliases=("online",))
    api.register_segmenter("mixed", MixedLogSegmenter)

    for name, fmt in PRESET_FORMATS.items():
        api.register_format(name, fmt)

    api.register_detector("TraceCite Mobile", detect_segmenter_kind, priority=100)


__all__ = ["register_all"]

"""TraceCite Mobile 插件层：真机/线上日志格式的具体实现。

core (tracecite_core) 只提供通用框架（FormatSegmenter/RawTextSegmenter/JsonLineSegmenter）。
本包负责注册具体格式（devicelog、applog 等）和 syslog 处理函数。
首次 import TraceCite Mobile 时自动将格式注册到 core 的 _BUILDERS / _PRESET_FORMATS。
"""


def register_all():
    """通过 tracecite_core 公共 SDK 注册上层格式。"""
    from tracecite_core import register_format, register_segmenter, register_segmenter_detector

    from .segmenters import (
        APPLOG_HEAD_RE, APPLOG_TS_RE, AppLogSegmenter,
        DeviceLogSegmenter, MixedLogSegmenter, SYSLOG_TS_RE, THREADTIME_TS_RE,
        detect_segmenter_kind,
    )
    from .formats import PRESET_FORMATS

    # 设备/业务格式
    register_segmenter(
        "devicelog",
        DeviceLogSegmenter,
        aliases=("syslog", "ios", "android", "threadtime"),
        replace=True,
    )
    register_segmenter(
        "applog", AppLogSegmenter, aliases=("online",), replace=True
    )
    register_segmenter("mixed", MixedLogSegmenter, replace=True)

    # 声明式预设
    for name, fmt in PRESET_FORMATS.items():
        register_format(name, fmt, replace=True)

    register_segmenter_detector(
        "TraceCite Mobile", detect_segmenter_kind, priority=100, replace=True
    )


# 自动注册
register_all()

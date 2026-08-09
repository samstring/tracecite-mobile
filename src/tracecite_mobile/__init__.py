"""TraceCite Mobile: mobile evidence collection and agent analysis workflows."""

__version__ = "0.1.0"

# 首次 import 时自动注册插件到 tracecite_core 内核
from . import plugins  # noqa: E402,F401

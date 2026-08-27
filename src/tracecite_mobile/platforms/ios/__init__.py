# -*- coding: utf-8 -*-
"""iOS 平台后端。

``IosBackend`` 的实现放在 :mod:`.backend`，本模块只保留稳定的导出路径；
已有 ``from tracecite_mobile.platforms.ios import IosBackend`` 调用无需迁移。
"""

from .backend import IosBackend

__all__ = ["IosBackend"]

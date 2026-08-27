# -*- coding: utf-8 -*-
"""平台后端注册与获取。

CLI 通过 ``get_backend(platform)`` 取得对应后端；内置与第三方平台使用同一注册契约。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .base import BaseBackend

_BACKENDS: Dict[str, Callable[..., BaseBackend]] = {}


def register_backend(
    platform: str,
    factory: Callable[..., BaseBackend],
    *,
    replace: bool = False,
) -> None:
    """注册平台后端；第三方插件不需要修改 CLI 或本模块源码。"""
    key = str(platform).strip().lower()
    if not key:
        raise ValueError("platform 名不能为空")
    current = _BACKENDS.get(key)
    if current is not None and current is not factory and not replace:
        raise ValueError(f"platform backend {key!r} 已注册")
    _BACKENDS[key] = factory


def _ensure_builtin_backends() -> None:
    if "ios" not in _BACKENDS:
        from .ios import IosBackend

        register_backend("ios", IosBackend)
    if "android" not in _BACKENDS:
        from .android.backend import AndroidBackend

        register_backend("android", AndroidBackend)


def available_platforms() -> List[str]:
    _ensure_builtin_backends()
    return sorted(_BACKENDS)


def is_supported_platform(platform: str) -> bool:
    return str(platform).strip().lower() in available_platforms()


def get_backend(platform: str = "ios", run=None) -> BaseBackend:
    """返回平台后端实例；具体采集实现对公共调用方不可见。"""
    _ensure_builtin_backends()
    key = str(platform or "ios").strip().lower()
    factory = _BACKENDS.get(key)
    if factory is None:
        raise ValueError(
            f"不支持的平台: {platform!r}（可选: {', '.join(available_platforms())}）"
        )
    return factory(run=run)

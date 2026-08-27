"""Official Mobile domain extension for the public TraceCite Runtime."""

from __future__ import annotations

from tracecite.extension import ExtensionAPI

from .analysis.scenario_runtime import MOBILE_RUNTIME
from .plugins import register_all


TRACECITE_EXTENSION_API = "1"


def register(api: ExtensionAPI) -> None:
    """Register Mobile capabilities without modifying the TraceCite package."""
    register_all(api)
    api.register_runtime("mobile", MOBILE_RUNTIME)


__all__ = ["TRACECITE_EXTENSION_API", "register"]

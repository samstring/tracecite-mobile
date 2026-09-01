"""Official declarative Mobile extension for TraceCite Runtime."""

from __future__ import annotations

from tracecite.extension import (
    CorePluginCapability,
    ExtensionManifest,
    TraceCiteExtension,
)

from . import __version__
from .analysis.scenario_runtime import MOBILE_SCENARIO
from .capabilities import agent_capabilities
from .plugins import register_all


EXTENSION = TraceCiteExtension(
    manifest=ExtensionManifest(
        id="mobile",
        version=__version__,
        domain="mobile",
        description="iOS and Android evidence collection and analysis workflows.",
    ),
    capabilities=(
        CorePluginCapability(name="mobile.core", register=register_all),
        *agent_capabilities(),
        MOBILE_SCENARIO,
    ),
)


def extension() -> TraceCiteExtension:
    """Return the stable TraceCite Extension declaration."""
    return EXTENSION


__all__ = ["EXTENSION", "extension"]

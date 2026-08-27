"""Official Mobile domain extension for TraceCite Extension Protocol v2."""

from __future__ import annotations

from tracecite.extension import (
    CorePluginCapability,
    ExtensionManifest,
    TraceCiteExtension,
    register_extension,
)

from .analysis.scenario_runtime import MOBILE_SCENARIO_CAPABILITY
from .capabilities import agent_capabilities
from .plugins import register_all


EXTENSION = TraceCiteExtension(
    manifest=ExtensionManifest(
        id="mobile",
        version="0.1.0",
        domain="mobile",
        description="iOS and Android evidence collection and investigation capabilities.",
    ),
    capabilities=(
        CorePluginCapability(name="mobile.formats", register=register_all),
        *agent_capabilities(),
        MOBILE_SCENARIO_CAPABILITY,
    ),
)

# Alias supported by the Core v2 loader when entry points return this module.
extension = EXTENSION


def register() -> TraceCiteExtension:
    """Explicitly install the declarative Mobile extension in this process."""

    return register_extension(EXTENSION)


__all__ = ["EXTENSION", "extension", "register"]

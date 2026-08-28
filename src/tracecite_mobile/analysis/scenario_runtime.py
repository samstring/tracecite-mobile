"""Mobile scenario contribution for TraceCite Extension Protocol v2."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tracecite.extension import ScenarioCapability


def _load_profile(start_dir: Path, platform: str):
    from ..shared.config import load_project_profile

    return load_project_profile(start_dir, platform=platform or "ios")


def _resolve_scenario_pattern(
    preset: str,
    scenario: str,
    start_dir: Path,
    base_pattern: str,
    platform: str,
) -> str:
    from .knowledge import resolve_scenario_pattern

    return resolve_scenario_pattern(
        preset,
        scenario=scenario,
        start_dir=start_dir,
        base_pattern=base_pattern,
        platform=platform or "ios",
    )


def _context_files(
    start_dir: Path, platform: str
) -> Sequence[Tuple[str, Optional[Path]]]:
    from ..shared.project_paths import find_knowledge_path, find_profile_path

    selected = platform or "ios"
    return (
        ("project_profile", find_profile_path(start_dir)),
        ("project_knowledge", find_knowledge_path(start_dir, platform=selected)),
    )


def _loaded_plugins() -> List[Dict[str, Any]]:
    from ..plugin_sdk import loaded_plugins

    return [dict(item) for item in loaded_plugins()]


def _runtime_versions() -> Mapping[str, str]:
    try:
        version = importlib.metadata.version("tracecite-mobile")
    except importlib.metadata.PackageNotFoundError:
        version = "source-tree"
    return {"tracecite_mobile": version}


MOBILE_SCENARIO = ScenarioCapability(
    name="mobile",
    load_profile=_load_profile,
    resolve_scenario_pattern=_resolve_scenario_pattern,
    context_files=_context_files,
    loaded_plugins=_loaded_plugins,
    runtime_versions=_runtime_versions,
    allow_live_source=True,
    allow_actions=True,
)


__all__ = ["MOBILE_SCENARIO"]

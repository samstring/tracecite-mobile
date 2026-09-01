"""Mobile facade for :mod:`tracecite.runtime.scenario`.

Existing ``tracecite_mobile.analysis.scenario`` imports remain valid while Core
owns the actual scenario runtime created from ``MOBILE_SCENARIO``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from tracecite.runtime.scenario import *  # noqa: F401,F403
from tracecite.runtime import scenario as _runtime_scenario


def _mobile_runtime():
    from tracecite.extension import get_runtime, register_extension
    from ..extension import EXTENSION

    register_extension(EXTENSION)
    return get_runtime("mobile")


def resolve_pattern(
    spec: Dict[str, Any],
    *,
    platform: str = "ios",
    start_dir: Optional[Path] = None,
    profile: Optional[Any] = None,
) -> Tuple[str, Optional[str]]:
    return _runtime_scenario.resolve_pattern(
        spec,
        platform=platform,
        start_dir=start_dir,
        profile=profile,
        runtime=_mobile_runtime(),
    )


def run_scenario(
    spec: Dict[str, Any],
    *,
    base_dir: Path,
    platform: str = "ios",
    start_dir: Optional[Path] = None,
    spec_path: Optional[Path] = None,
) -> Dict[str, Any]:
    return _runtime_scenario.run_scenario(
        spec,
        base_dir=base_dir,
        platform=platform,
        start_dir=start_dir,
        spec_path=spec_path,
        runtime=_mobile_runtime(),
    )


def explain_scenario(
    spec: Dict[str, Any],
    *,
    base_dir: Path,
    platform: str = "ios",
    start_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    return _runtime_scenario.explain_scenario(
        spec,
        base_dir=base_dir,
        platform=platform,
        start_dir=start_dir,
        runtime=_mobile_runtime(),
    )


def cmd_scenario(args) -> int:
    return _runtime_scenario.cmd_scenario(args, runtime=_mobile_runtime())

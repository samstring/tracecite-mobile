# -*- coding: utf-8 -*-
"""Mobile 默认 output 树；机制在 tracecite 公开 output_layout。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from tracecite import (
    deep_merge,
    load_output_config as public_load_output_config,
    write_output_config,
)
import tracecite.output_layout as public_output_layout
from tracecite_core.immutable import is_immutable_log_source, is_stable_source

DEFAULT_OUTPUT_CONFIG: Dict[str, Any] = {
    "output_root": "~/Documents/TraceCite",
    "plugins": {
        "bugly": {"dir": "bugly"},
        "mobile": {
            "dir": "mobile",
            "platforms": {
                "android": {"dir": "Android"},
                "ios": {"dir": "iOS"},
            },
        },
    },
}


def load_output_config() -> Dict[str, Any]:
    return public_load_output_config(defaults=DEFAULT_OUTPUT_CONFIG)


def write_default_output_config(*, overwrite: bool = False) -> Path:
    return write_output_config(
        DEFAULT_OUTPUT_CONFIG,
        config_path=public_output_layout.USER_OUTPUT_CONFIG_PATH,
        overwrite=overwrite,
    )


def load_output_layout() -> "OutputLayout":
    return OutputLayout.load()


@dataclass(frozen=True)
class BuglyLayout:
    root: Path
    exports_dir: Path
    runs_dir: Path
    cache_dir: Path


@dataclass(frozen=True)
class MobilePlatformLayout:
    root: Path
    log_dir: Path
    instrument_dir: Path
    runs_dir: Path


@dataclass(frozen=True)
class OutputLayout:
    output_root: Path
    plugins: Dict[str, Any]

    @classmethod
    def load(cls) -> "OutputLayout":
        config = load_output_config()
        root = Path(str(config.get("output_root", DEFAULT_OUTPUT_CONFIG["output_root"]))).expanduser().resolve()
        plugins = dict(config.get("plugins") or {})
        return cls(output_root=root, plugins=plugins)

    def _plugin_dir(self, plugin: str) -> Path:
        entry = self.plugins.get(plugin) or {}
        rel = str(entry.get("dir") or plugin)
        return self.output_root / rel

    def bugly(self) -> BuglyLayout:
        root = self._plugin_dir("bugly")
        return BuglyLayout(
            root=root,
            exports_dir=root / "exports",
            runs_dir=root / "runs",
            cache_dir=root / "cache",
        )

    def mobile(self, platform: str) -> MobilePlatformLayout:
        selected = str(platform or "ios").strip().lower() or "ios"
        if selected not in {"ios", "android"}:
            selected = "ios"
        mobile_entry = self.plugins.get("mobile") or {}
        mobile_root = self.output_root / str(mobile_entry.get("dir") or "mobile")
        platforms = mobile_entry.get("platforms") or {}
        platform_entry = platforms.get(selected) or {}
        platform_dir = mobile_root / str(
            platform_entry.get("dir") or ("Android" if selected == "android" else "iOS")
        )
        return MobilePlatformLayout(
            root=platform_dir,
            log_dir=platform_dir / "log",
            instrument_dir=platform_dir / "instrument",
            runs_dir=platform_dir / "runs",
        )

    def ensure_mobile(self, platform: str) -> MobilePlatformLayout:
        layout = self.mobile(platform)
        for path in (
            layout.log_dir,
            layout.instrument_dir,
            layout.runs_dir,
            layout.log_dir / ".archive",
        ):
            path.mkdir(parents=True, exist_ok=True)
        return layout

    def ensure_bugly(self) -> BuglyLayout:
        layout = self.bugly()
        for path in (layout.exports_dir, layout.runs_dir, layout.cache_dir):
            path.mkdir(parents=True, exist_ok=True)
        return layout


__all__ = [
    "DEFAULT_OUTPUT_CONFIG",
    "deep_merge",
    "load_output_config",
    "write_default_output_config",
    "load_output_layout",
    "OutputLayout",
    "BuglyLayout",
    "MobilePlatformLayout",
    "is_immutable_log_source",
    "is_stable_source",
]

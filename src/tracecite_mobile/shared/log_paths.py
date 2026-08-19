# -*- coding: utf-8 -*-
"""Mobile 输出路径解析。"""

from __future__ import annotations

from pathlib import Path

from .config import ProjectProfile
from .output_layout import load_output_layout


def resolve_runs_dir(platform: str, profile: ProjectProfile | None = None) -> Path:
    if profile is not None and profile.analysis_output_dir is not None:
        return profile.analysis_output_dir.expanduser().resolve()
    return load_output_layout().mobile(platform).runs_dir


def infer_device_name_from_hot(path: Path, fallback: str = "") -> str:
    name = (fallback or "").strip()
    if name:
        return name
    stem = Path(path).stem
    if stem.startswith("ios_live_"):
        return stem[len("ios_live_") :]
    if stem.startswith("android_live_"):
        return stem[len("android_live_") :]
    return stem or "device"

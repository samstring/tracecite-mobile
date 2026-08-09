# -*- coding: utf-8 -*-
"""项目本地隐藏目录 `.tracecite/` 路径解析。"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Optional

from .constants import (
    GITIGNORE_PROJECT_META_ENTRY,
    KNOWLEDGE_BASENAME_ANDROID,
    KNOWLEDGE_BASENAME_IOS,
    PROFILE_BASENAME,
    PROJECT_META_DIRNAME,
)


def project_meta_dir(project_root: Path) -> Path:
    return project_root / PROJECT_META_DIRNAME


def profile_path_in(project_root: Path) -> Path:
    return project_meta_dir(project_root) / PROFILE_BASENAME


def _knowledge_basename(platform: str = "ios") -> str:
    if platform == "android":
        return KNOWLEDGE_BASENAME_ANDROID
    if platform == "ios":
        return KNOWLEDGE_BASENAME_IOS
    safe = re.sub(r"[^a-z0-9_-]+", "-", str(platform).strip().lower()).strip("-")
    if not safe:
        raise ValueError("platform 名不能为空")
    return f"knowledge.{safe}.json"


def knowledge_path_in(project_root: Path, platform: str = "ios") -> Path:
    return project_meta_dir(project_root) / _knowledge_basename(platform)


def find_project_root_with_meta(start_dir: Optional[Path] = None) -> Optional[Path]:
    """向上查找含 `.tracecite/config.json` 的项目根。"""
    current = (start_dir or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if profile_path_in(candidate).is_file():
            return candidate
    return None


def find_profile_path(start_dir: Optional[Path] = None) -> Optional[Path]:
    current = (start_dir or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        modern = profile_path_in(candidate)
        if modern.is_file():
            return modern
    return None


def find_knowledge_path(start_dir: Optional[Path] = None, platform: str = "ios") -> Optional[Path]:
    """查找与 profile 同级的平台 knowledge。

    iOS → knowledge.ios.json，Android → knowledge.android.json，无交叉回落。
    """
    current = (start_dir or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        modern_k = knowledge_path_in(candidate, platform=platform)
        modern_p = profile_path_in(candidate)
        if modern_k.is_file():
            return modern_k
        if modern_p.is_file():
            return modern_k  # 锚定写入位置（可能尚未创建）
    return None


def resolve_knowledge_write_path(start_dir: Optional[Path] = None, platform: str = "ios") -> Path:
    found = find_knowledge_path(start_dir, platform=platform)
    if found is not None:
        basename = _knowledge_basename(platform)
        if found.name == basename:
            return found
        return found
    root = (start_dir or Path.cwd()).resolve()
    return knowledge_path_in(root, platform=platform)


def resolve_profile_write_path(destination: Path) -> Path:
    return profile_path_in(destination)


def ensure_project_meta_gitignore(project_root: Path) -> Path:
    """确保项目根 `.gitignore` 忽略 `.tracecite/`，避免提交本地排查配置。"""
    gitignore = project_root / ".gitignore"
    entry = GITIGNORE_PROJECT_META_ENTRY
    block = (
        "\n# tracecite local project meta (config + knowledge; do not commit)\n"
        f"{entry}\n"
    )
    if not gitignore.exists():
        gitignore.write_text(block.lstrip("\n"), encoding="utf-8")
        return gitignore

    text = gitignore.read_text(encoding="utf-8")
    # 已有 .tracecite 相关忽略则跳过
    if ".tracecite/" in text or ".tracecite\n" in text or text.rstrip().endswith(".tracecite"):
        return gitignore
    if not text.endswith("\n"):
        text += "\n"
    gitignore.write_text(text + block, encoding="utf-8")
    return gitignore

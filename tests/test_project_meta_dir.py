# -*- coding: utf-8 -*-
"""项目隐藏目录 .tracecite/ 与 gitignore。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.shared.config import load_project_profile, write_profile_template
from tracecite_mobile.shared.project_paths import (
    ensure_project_meta_gitignore,
    knowledge_path_in,
    profile_path_in,
)


class ProjectMetaDirTest(unittest.TestCase):
    def test_init_writes_hidden_dir_and_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_profile_template(root)
            self.assertEqual(path, profile_path_in(root))
            self.assertTrue(path.is_file())
            self.assertTrue(knowledge_path_in(root).is_file())
            self.assertIn(".tracecite/", (root / ".gitignore").read_text(encoding="utf-8"))
            profile = load_project_profile(root)
            self.assertEqual(profile.source_path.resolve(), path.resolve())
            self.assertIn(
                "UIApplicationDidBecomeActiveNotification",
                profile.filter_presets["system-lifecycle"].pattern,
            )

    def test_gitignore_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_project_meta_gitignore(root)
            ensure_project_meta_gitignore(root)
            text = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertEqual(text.count(".tracecite/"), 1)

if __name__ == "__main__":
    unittest.main()

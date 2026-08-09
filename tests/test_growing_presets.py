# -*- coding: utf-8 -*-
"""preset terms 随分析增长：代码无关键词，只注册名字。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.shared.config import (
    append_filter_preset_terms,
    load_project_profile,
    write_profile_template,
)
from tracecite_core.text_filter import (
    DEFAULT_FILTER_PRESET_SEEDS,
    DEFAULT_FILTER_PRESETS,
    FilterError,
    resolve_preset,
)
from tracecite_mobile.analysis.knowledge import load_project_knowledge


class GrowingPresetTest(unittest.TestCase):
    def test_code_has_no_keyword_seeds(self) -> None:
        for name, seeds in DEFAULT_FILTER_PRESET_SEEDS.items():
            self.assertEqual(seeds, [], msg=f"{name} 不应在代码里有关键词")
            self.assertEqual(DEFAULT_FILTER_PRESETS[name][0], "")

    def test_empty_preset_resolve_asks_grow_without_project(self) -> None:
        with self.assertRaises(FilterError) as ctx:
            resolve_preset("user-behavior")
        self.assertIn("提供 preset 词表", str(ctx.exception))

    def test_template_terms_start_empty_in_profile_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_profile_template(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            # Project protocol slots stay empty; system terms live in starter knowledge.
            self.assertEqual(data["filter_presets"]["user-behavior"]["terms"], [])
            knowledge = load_project_knowledge(root)
            self.assertIn(
                "UIApplicationDidBecomeActiveNotification",
                knowledge.filter_terms["system-lifecycle"],
            )

    def test_preset_add_grows_project_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            result = append_filter_preset_terms(
                "system-lifecycle",
                ["task.started", "UIApplicationDidBecomeActiveNotification"],
                start_dir=root,
            )
            self.assertEqual(result["added"], ["task.started"])
            self.assertIn("UIApplicationDidBecomeActiveNotification", result["skipped_dup"])

            knowledge = load_project_knowledge(root)
            self.assertIn("task.started", knowledge.filter_terms["system-lifecycle"])

            profile = load_project_profile(root)
            pattern = profile.filter_presets["system-lifecycle"].pattern
            self.assertIn(r"task\.started", pattern)
            resolved = resolve_preset(
                "system-lifecycle", profile.filter_preset_table()
            )
            self.assertIn("UIApplicationDidBecomeActiveNotification", resolved[0])

            result2 = append_filter_preset_terms(
                "system-lifecycle",
                ["task.started", "task.completed"],
                start_dir=root,
            )
            self.assertEqual(result2["added"], ["task.completed"])
            self.assertEqual(result2["skipped_dup"], ["task.started"])


if __name__ == "__main__":
    unittest.main()

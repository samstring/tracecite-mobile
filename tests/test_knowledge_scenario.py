# -*- coding: utf-8 -*-
"""知识库业务场景：AI 查到新场景时可挂词表/经验。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.shared.config import write_profile_template
from tracecite_mobile.analysis.knowledge import (
    KnowledgeError,
    add_filter_terms,
    add_learning,
    ensure_scenario,
    load_project_knowledge,
    resolve_scenario_pattern,
)


class KnowledgeScenarioTest(unittest.TestCase):
    def test_scenario_terms_merge_for_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            ensure_scenario(
                "demo-navigation",
                title="示例导航",
                tags=["navigation"],
                start_dir=root,
            )
            add_filter_terms(
                "user-behavior",
                ["示例导航", "DemoNavigation"],
                start_dir=root,
                scenario="demo-navigation",
            )
            add_learning(
                "打开示例导航并进入结果页",
                tags=["nav"],
                start_dir=root,
                scenario="demo-navigation",
            )
            knowledge = load_project_knowledge(root)
            self.assertIn("demo-navigation", knowledge.scenarios)
            self.assertNotIn(
                "示例导航", knowledge.filter_terms.get("user-behavior", [])
            )
            self.assertIn(
                "示例导航",
                knowledge.scenarios["demo-navigation"].filter_terms["user-behavior"],
            )
            pattern = resolve_scenario_pattern(
                "user-behavior",
                scenario="demo-navigation",
                start_dir=root,
                base_pattern="task.started|task.completed",
            )
            self.assertIn("task.started", pattern)
            self.assertIn("示例导航", pattern)
            self.assertIn("DemoNavigation", pattern)

    def test_unknown_scenario_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            with self.assertRaises(KnowledgeError):
                add_filter_terms(
                    "user-behavior",
                    ["x"],
                    start_dir=root,
                    scenario="no-such",
                )


if __name__ == "__main__":
    unittest.main()

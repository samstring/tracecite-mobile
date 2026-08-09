# -*- coding: utf-8 -*-
"""项目知识库成长：term / marker / learning / playbook。"""

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
from tracecite_mobile.shared.constants import KNOWLEDGE_BASENAME_IOS, PROJECT_META_DIRNAME
from tracecite_mobile.analysis.knowledge import (
    add_behavior_marker,
    add_learning,
    add_playbook,
    ensure_default_project_knowledge,
    load_project_knowledge,
)
from tracecite_mobile.shared.project_paths import knowledge_path_in
from tracecite_mobile.analysis.behavior_summary import summarize_behavior_text


class ProjectKnowledgeGrowTest(unittest.TestCase):
    def test_profile_init_creates_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            knowledge_file = knowledge_path_in(root)
            self.assertTrue(knowledge_file.is_file())
            self.assertEqual(knowledge_file.name, KNOWLEDGE_BASENAME_IOS)
            self.assertEqual(knowledge_file.parent.name, PROJECT_META_DIRNAME)
            gitignore = root / ".gitignore"
            self.assertTrue(gitignore.is_file())
            self.assertIn(".tracecite/", gitignore.read_text(encoding="utf-8"))
            knowledge = load_project_knowledge(root)
            self.assertIn(
                "UIApplicationDidBecomeActiveNotification",
                knowledge.filter_terms.get("system-lifecycle", []),
            )
            blob = json.dumps(
                {
                    "filter_terms": knowledge.filter_terms,
                    "learnings": [x.to_dict() for x in knowledge.learnings],
                    "playbooks": [x.to_dict() for x in knowledge.playbooks],
                },
                ensure_ascii=False,
            ).lower()
            self.assertNotIn("project-secret", blob)
            self.assertTrue(knowledge.markers)
            self.assertTrue(knowledge.learnings)
            self.assertTrue(knowledge.playbooks)

            profile = load_project_profile(root)
            pattern = profile.filter_presets["system-lifecycle"].pattern
            self.assertIn("UIApplicationDidBecomeActiveNotification", pattern)

    def test_grow_term_and_merge_into_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            result = append_filter_preset_terms(
                "system-lifecycle",
                ["UIApplicationDidBecomeActiveNotification", "task.started"],
                start_dir=root,
            )
            self.assertEqual(result["added"], ["task.started"])
            self.assertIn("UIApplicationDidBecomeActiveNotification", result["skipped_dup"])
            knowledge = load_project_knowledge(root)
            self.assertIn("task.started", knowledge.filter_terms["system-lifecycle"])
            profile = load_project_profile(root)
            pattern = profile.filter_presets["system-lifecycle"].pattern
            self.assertIn(r"task\.started", pattern)

    def test_marker_learning_playbook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            add_behavior_marker(
                "task.started",
                category="task",
                label="Task started",
                start_dir=root,
            )
            add_learning(
                "示例经验",
                tags=["demo"],
                evidence="filtered.log",
                start_dir=root,
            )
            add_playbook(
                "demo-playbook",
                when="测试",
                steps=["a", "b"],
                tags=["demo"],
                related_presets=["user-behavior"],
                start_dir=root,
            )
            knowledge = load_project_knowledge(root)
            self.assertGreaterEqual(len(knowledge.markers), 2)
            self.assertTrue(any(m.needle == "task.started" for m in knowledge.markers))
            self.assertIn("JetsamEvent", knowledge.filter_terms.get("system-memory", []))
            self.assertGreaterEqual(len(knowledge.learnings), 2)  # starter + 示例
            self.assertGreaterEqual(len(knowledge.playbooks), 1)

            sample = (
                "Aug  9 10:00:00 x: task.started\n"
                "Aug  9 10:00:01 x: UIApplicationDidEnterBackgroundNotification\n"
            )
            summary = summarize_behavior_text(sample, start_dir=root)
            labels = [e.label for e in summary.events]
            self.assertIn("Task started", labels)
            self.assertIn("Application lifecycle", labels)

    def test_ensure_default_creates_starter_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ensure_default_project_knowledge(root)
            self.assertTrue(first["created"])
            self.assertFalse(first["seeded_empty"])
            knowledge_file = knowledge_path_in(root)
            self.assertTrue(knowledge_file.is_file())
            self.assertEqual(Path(first["path"]).resolve(), knowledge_file.resolve())
            knowledge = load_project_knowledge(root)
            self.assertIn(
                "UIApplicationDidBecomeActiveNotification",
                knowledge.filter_terms.get("system-lifecycle", []),
            )
            # 幂等：已有非空词表不再改写
            second = ensure_default_project_knowledge(root)
            self.assertFalse(second["created"])
            self.assertEqual(
                Path(second["path"]).resolve(), knowledge_file.resolve()
            )

    def test_ensure_default_seeds_empty_filter_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_file = knowledge_path_in(root)
            knowledge_file.parent.mkdir(parents=True, exist_ok=True)
            knowledge_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "filter_terms": {},
                        "markers": [],
                        "learnings": [{"summary": "keep-me", "tags": []}],
                        "playbooks": [],
                        "scenarios": {},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            result = ensure_default_project_knowledge(root)
            self.assertTrue(result["created"])
            self.assertTrue(result["seeded_empty"])
            knowledge = load_project_knowledge(root)
            self.assertIn("JetsamEvent", knowledge.filter_terms["system-memory"])
            self.assertTrue(
                any(x.summary == "keep-me" for x in knowledge.learnings)
            )


if __name__ == "__main__":
    unittest.main()

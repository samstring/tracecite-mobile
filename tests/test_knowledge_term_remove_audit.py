# -*- coding: utf-8 -*-
"""grow term/marker --remove 与 grow audit（通用词表裁剪）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.shared.config import (
    append_filter_preset_terms,
    remove_filter_preset_terms,
    write_profile_template,
)
from tracecite_mobile.analysis.knowledge import (
    add_behavior_marker,
    audit_filter_terms,
    ensure_scenario,
    load_project_knowledge,
    load_starter_knowledge_dict,
    remove_behavior_marker,
)


class KnowledgeTermRemoveAuditTest(unittest.TestCase):
    def test_starter_has_system_evidence_playbook(self) -> None:
        starter = load_starter_knowledge_dict()
        names = [p.get("name") for p in starter.get("playbooks") or []]
        self.assertIn("ios-system-evidence", names)
        tags = []
        for item in starter.get("learnings") or []:
            tags.extend(item.get("tags") or [])
        self.assertIn("system", tags)
        # starter 不承载具体业务场景壳
        self.assertEqual(starter.get("scenarios") or {}, {})

    def test_remove_scenario_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            ensure_scenario("feature-flow", title="业务流", start_dir=root)
            append_filter_preset_terms(
                "user-behavior",
                ["StableSignal", "OneOffCopy"],
                start_dir=root,
                scenario="feature-flow",
            )
            result = remove_filter_preset_terms(
                "user-behavior",
                ["OneOffCopy", "MissingTerm"],
                start_dir=root,
                scenario="feature-flow",
            )
            self.assertEqual(result["removed"], ["OneOffCopy"])
            self.assertEqual(result["missing"], ["MissingTerm"])
            knowledge = load_project_knowledge(root)
            terms = knowledge.scenarios["feature-flow"].filter_terms["user-behavior"]
            self.assertIn("StableSignal", terms)
            self.assertNotIn("OneOffCopy", terms)
            self.assertNotIn(
                "OneOffCopy", knowledge.filter_terms.get("user-behavior", [])
            )

    def test_remove_behavior_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            ensure_scenario("feature-flow", title="业务流", start_dir=root)
            add_behavior_marker(
                "StableSignal",
                category="flow",
                label="关键信号",
                start_dir=root,
                scenario="feature-flow",
            )
            removed = remove_behavior_marker(
                "StableSignal",
                start_dir=root,
                scenario="feature-flow",
            )
            self.assertTrue(removed["removed"])
            missing = remove_behavior_marker(
                "StableSignal",
                start_dir=root,
                scenario="feature-flow",
            )
            self.assertTrue(missing["missing"])
            knowledge = load_project_knowledge(root)
            needles = [
                m.needle for m in knowledge.scenarios["feature-flow"].markers
            ]
            self.assertNotIn("StableSignal", needles)

    def test_audit_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_profile_template(root)
            ensure_scenario("feature-flow", title="业务流", start_dir=root)
            append_filter_preset_terms(
                "user-behavior",
                ["RareSignal", "NoisyLoop", "UnusedSignal"],
                start_dir=root,
                scenario="feature-flow",
            )
            log = root / "sample.log"
            lines = ["RareSignal once\n"] + ["NoisyLoop\n"] * 120
            log.write_text("".join(lines), encoding="utf-8")
            result = audit_filter_terms(
                log,
                preset="user-behavior",
                scenario="feature-flow",
                start_dir=root,
            )
            by_term = {h["term"]: h for h in result["hits"]}
            self.assertEqual(by_term["RareSignal"]["hint"], "sparse")
            self.assertEqual(by_term["NoisyLoop"]["hint"], "noisy")
            self.assertEqual(by_term["UnusedSignal"]["hint"], "unused")


if __name__ == "__main__":
    unittest.main()

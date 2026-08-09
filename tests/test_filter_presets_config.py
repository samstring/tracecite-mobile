# -*- coding: utf-8 -*-
"""filter_presets：代码无关键词，项目知识库增长。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.shared.config import load_project_profile, write_profile_template
from tracecite_core.text_filter import (
    DEFAULT_FILTER_PRESETS,
    merge_filter_presets,
    resolve_preset,
)


class FilterPresetConfigTest(unittest.TestCase):
    def test_builtin_names_have_empty_patterns(self) -> None:
        self.assertIn("profile-leak", DEFAULT_FILTER_PRESETS)
        self.assertEqual(DEFAULT_FILTER_PRESETS["profile-leak"][0], "")
        self.assertEqual(DEFAULT_FILTER_PRESETS["memory-leak"][0], "")
        self.assertEqual(DEFAULT_FILTER_PRESETS["user-action"][0], "")
        self.assertEqual(DEFAULT_FILTER_PRESETS["user-nav"][0], "")
        self.assertNotIn("leak-trace", DEFAULT_FILTER_PRESETS)

    def test_merge_override_and_append(self) -> None:
        merged = merge_filter_presets(
            {
                "profile-leak": (r"CustomMarker", "custom-profile"),
                "crash": (r"uncaught exception", "crash"),
            }
        )
        self.assertEqual(merged["profile-leak"], (r"CustomMarker", "custom-profile"))
        self.assertEqual(merged["crash"], (r"uncaught exception", "crash"))
        self.assertIn("apm-frame", merged)

    def test_profile_without_filter_presets_keeps_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_profile_template(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("filter_presets", data)
            self.assertIn("profile-leak", data["filter_presets"])
            del data["filter_presets"]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            profile = load_project_profile(root)
            self.assertIn("profile-leak", profile.filter_presets)
            self.assertEqual(profile.filter_presets["profile-leak"].pattern, "")

    def test_profile_can_override_and_append_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_profile_template(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["filter_presets"]["profile-leak"] = {
                "pattern": r"MyAppMarker",
                "tag": "my-profile",
                "note": "自定义",
            }
            data["filter_presets"]["crash"] = {
                "pattern": r"NSInternalInconsistencyException",
                "tag": "crash",
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            profile = load_project_profile(root)
            self.assertEqual(profile.filter_presets["profile-leak"].pattern, r"MyAppMarker")
            self.assertEqual(profile.filter_presets["profile-leak"].tag, "my-profile")
            self.assertEqual(
                profile.filter_presets["crash"].pattern,
                r"NSInternalInconsistencyException",
            )
            self.assertIn("apm-frame", profile.filter_presets)

    def test_profile_default_filter_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_profile_template(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(data.get("default_filter_preset"))
            self.assertIn("user-behavior", data["filter_presets"])

            data["default_filter_preset"] = "user-behavior"
            # 先写入词，否则 resolve 会因空 pattern 失败（在 filter 时）
            data["filter_presets"]["user-behavior"] = {
                "tag": "user-behavior",
                "terms": ["task.started"],
                "pattern": "task.started",
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            profile = load_project_profile(root)
            resolved = profile.resolve_default_filter()
            assert resolved is not None
            pattern, tag, source = resolved
            self.assertEqual(source, "preset:user-behavior")
            self.assertEqual(tag, "user-behavior")
            self.assertIn("task.started", pattern)

            data["default_filter_preset"] = None
            data["default_filter_pattern"] = r"MyCustom|Marker"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            profile = load_project_profile(root)
            resolved = profile.resolve_default_filter()
            assert resolved is not None
            pattern, tag, source = resolved
            self.assertEqual(source, "pattern")
            self.assertEqual(tag, "default")
            self.assertEqual(pattern, r"MyCustom|Marker")

    def test_intent_presets_builtin_names_only(self) -> None:
        self.assertIn("user-behavior", DEFAULT_FILTER_PRESETS)
        self.assertIn("network-http", DEFAULT_FILTER_PRESETS)
        self.assertEqual(DEFAULT_FILTER_PRESETS["user-behavior"][0], "")
        self.assertEqual(DEFAULT_FILTER_PRESETS["network-http"][0], "")

    def test_default_filter_preset_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_profile_template(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["default_filter_preset"] = "not-exist"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaises(Exception) as ctx:
                load_project_profile(root)
            self.assertIn("default_filter_preset", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""配置里的原始正则不能被字面量转义破坏。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from tracecite_mobile.shared.config import (
    append_filter_preset_terms,
    load_project_profile,
    write_profile_template,
)


CUSTOM_REGEX = r"Error\((\d+)\)"


class RegexPresetTest(unittest.TestCase):
    def _write_regex_preset(self, root: Path) -> None:
        path = write_profile_template(root)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["filter_presets"]["custom-regex"] = {
            "pattern": CUSTOM_REGEX,
            "tag": "custom-regex",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_pattern_stays_regex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_regex_preset(root)

            preset = load_project_profile(root).filter_presets["custom-regex"]

            self.assertEqual(preset.pattern, CUSTOM_REGEX)
            self.assertEqual(preset.terms, ())
            self.assertRegex("Error(500)", preset.pattern)

    def test_growing_terms_keeps_regex_effective(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_regex_preset(root)

            append_filter_preset_terms("custom-regex", ["支付失败"], start_dir=root)
            preset = load_project_profile(root).filter_presets["custom-regex"]

            compiled = re.compile(preset.pattern)
            self.assertTrue(compiled.search("Error(500)"))
            self.assertTrue(compiled.search("这里 支付失败 了"))


if __name__ == "__main__":
    unittest.main()

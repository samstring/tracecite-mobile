from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from tracecite_mobile.cli import build_parser


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "skills"
EXPECTED_SKILLS = {
    "android-device-log",
    "android-device-profile",
    "ios-analysis-package",
    "ios-device-log",
    "ios-device-profile",
    "tracecite-mobile",
}
LEGACY_MARKERS = (
    "已废弃",
    "deprecated",
    "textkit",
    "text_analyzer",
    "device-debug",
    "xcode-debug",
    "pp" + "live",
    "tracecite_core",
    "./bin/",
    "live" + "-" + "gi" + "ft",
    "live" + "_" + "gi" + "ft",
)


def _skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def _frontmatter(text: str) -> str:
    assert text.startswith("---\n")
    _, frontmatter, _ = text.split("---", 2)
    return frontmatter


def test_skill_frontmatter_names_match_directories() -> None:
    files = _skill_files()
    assert {path.parent.name for path in files} == EXPECTED_SKILLS
    for path in files:
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))
        match = re.search(r"(?m)^name:\s*([^\s]+)\s*$", frontmatter)
        assert match is not None, path
        assert match.group(1) == path.parent.name
        assert re.search(r"(?m)^description:\s*>-\s*$", frontmatter), path


def test_skills_have_no_legacy_or_private_markers() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _skill_files()).lower()
    for marker in LEGACY_MARKERS:
        assert marker.lower() not in combined


def test_device_skill_commands_use_mobile_console_script() -> None:
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^(tracecite[-_]\S*|python\d*\s+-m\s+tracecite\S*)", stripped):
                assert stripped.startswith("tracecite-mobile"), (path, stripped)


def test_documented_mobile_commands_parse_without_device_access() -> None:
    replacements = {
        "<UDID>": "DEMO-UDID",
        "<SERIAL>": "emulator-5554",
        "$LOG": "demo.log",
        "$FILTERED_LOG": "filtered.log",
    }
    parser = build_parser()
    parsed = 0
    for path in _skill_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            command = line.strip()
            if not command.startswith("tracecite-mobile "):
                continue
            for old, new in replacements.items():
                command = command.replace(old, new)
            argv = shlex.split(command)[1:]
            if argv == ["--version"]:
                with pytest.raises(SystemExit) as exc:
                    parser.parse_args(argv)
                assert exc.value.code == 0
            else:
                parser.parse_args(argv)
            parsed += 1
    assert parsed >= 30


def test_agent_host_skill_mirrors_match_canonical() -> None:
    canonical = (SKILLS / "tracecite-mobile" / "SKILL.md").read_text(encoding="utf-8")
    for path in (
        ROOT / ".agents" / "skills" / "tracecite-mobile" / "SKILL.md",
        ROOT / ".pi" / "skills" / "tracecite-mobile" / "SKILL.md",
    ):
        assert path.read_text(encoding="utf-8") == canonical


@pytest.mark.parametrize(
    "argv",
    [
        ["list", "--help"],
        ["profile", "init", "--help"],
        ["session", "start", "--help"],
        ["session", "status", "--help"],
        ["session", "stop", "--help"],
        ["stream", "--help"],
        ["filter", "--help"],
        ["behavior", "summarize", "--help"],
        ["capture", "start", "--help"],
        ["capture", "status", "--help"],
        ["capture", "stop", "--help"],
        ["--platform", "android", "grow", "scenario", "--help"],
        ["--platform", "android", "grow", "term", "--help"],
    ],
)
def test_skill_command_help_routes_exist(argv: list[str], capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 0
    assert "usage: tracecite-mobile" in capsys.readouterr().out

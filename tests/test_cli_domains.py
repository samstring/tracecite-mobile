"""CLI 命令域组装与跨平台分派回归测试。"""

from __future__ import annotations

import json

from tracecite_mobile.cli import build_parser, main
from tracecite_mobile.commands import analysis as analysis_commands


def test_parser_registers_all_command_domains() -> None:
    parser = build_parser()
    assert parser.prog == "tracecite-mobile"
    cases = {
        "list": ["list"],
        "filter": ["filter", "app.log", "--grep", "error"],
        "grow": ["grow", "show"],
        "profile": ["profile", "show"],
    }
    for expected, argv in cases.items():
        assert parser.parse_args(argv).command == expected


def test_filter_parser_accepts_preset_and_grep_together() -> None:
    args = build_parser().parse_args(
        [
            "filter",
            "app.log",
            "--preset",
            "system-fault",
            "--grep",
            "checkout|payment",
        ]
    )

    assert args.preset == "system-fault"
    assert args.grep == "checkout|payment"


def test_clean_archive_requires_explicit_flags() -> None:
    args = build_parser().parse_args(
        [
            "clean",
            "analysis",
            "--include-archive",
            "--dry-run",
            "--before",
            "yesterday",
        ]
    )
    assert args.command == "clean"
    assert args.clean_command == "analysis"
    assert args.include_archive is True
    assert args.dry_run is True
    assert args.yes is False


def test_scenario_commands_accept_and_forward_base_dir(monkeypatch) -> None:
    for command in ("run", "validate", "explain"):
        args = build_parser().parse_args(
            [
                "scenario",
                command,
                "scenario.json",
                "--base-dir",
                "/tmp/project",
            ]
        )
        assert args.base_dir == "/tmp/project"

        captured = {}

        def fake_cmd_scenario(parsed):
            captured["args"] = parsed
            return 37

        monkeypatch.setattr(analysis_commands, "cmd_scenario", fake_cmd_scenario)
        assert analysis_commands.dispatch_analysis_command(args) == 37
        assert captured["args"].base_dir == "/tmp/project"


def test_android_profile_show_uses_common_maintenance_handler(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--platform", "android", "profile", "show", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["package_name"] == ""
    assert payload["logcat_format"] == "threadtime"


def test_plugin_list_and_doctor_are_machine_readable(capsys) -> None:
    assert main(["plugin", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["healthy"] is True
    assert listed["api_versions"] == {"tracecite_core": "2", "tracecite_mobile": "3"}
    assert "source_providers" in listed["extensions"]
    assert "event_transformers" in listed["extensions"]
    assert "behavior_parsers" in listed["extensions"]
    assert "assertion_types" in listed["extensions"]
    assert "report_outputters" in listed["extensions"]

    assert main(["plugin", "doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True

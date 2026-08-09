"""CLI 命令域组装与跨平台分派回归测试。"""

from __future__ import annotations

import json

from tracecite_mobile.cli import build_parser, main


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
    assert listed["api_versions"] == {"tracecite_core": "2", "tracecite_mobile": "2"}
    assert "source_providers" in listed["extensions"]
    assert "event_transformers" in listed["extensions"]
    assert "behavior_parsers" in listed["extensions"]
    assert "assertion_types" in listed["extensions"]
    assert "report_outputters" in listed["extensions"]

    assert main(["plugin", "doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tracecite_mobile.commands.analysis import cmd_behavior
from tracecite_mobile.commands import device as device_commands
from tracecite_mobile.analysis.knowledge import ensure_default_project_knowledge


def test_behavior_command_emits_analysis_run(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    ensure_default_project_knowledge(tmp_path, platform="ios")
    source = tmp_path / "behavior.log"
    source.write_text(
        "Aug  9 10:00:00 App[1] <Notice>: UIApplicationDidBecomeActiveNotification\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        log_path=str(source),
        no_dedupe=False,
        scenario=None,
        platform="ios",
        json=True,
    )

    assert cmd_behavior(args) == 0
    payload = json.loads(capsys.readouterr().out)
    manifest = json.loads(
        Path(payload["manifest_path"]).read_text(encoding="utf-8")
    )

    assert manifest["kind"] == "behavior"
    assert manifest["status"] == "completed"
    assert manifest["metrics"]["event_count"] == 1
    assert {row["role"] for row in manifest["inputs"]} >= {"source_snapshot"}
    assert {row["role"] for row in manifest["artifacts"]} == {"behavior_summary"}


def test_device_operation_uses_same_manifest_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        device_commands, "DEFAULT_RUN_OUTPUT_DIR", tmp_path / "device-runs"
    )
    log = tmp_path / "device.log"
    log.write_text("captured\n", encoding="utf-8")

    command_run = device_commands._new_device_run(
        "session-stop", platform="ios", parameters={"device": "test"}
    )
    payload = device_commands._finish_device_run(
        command_run,
        {"stopped": True, "output_path": str(log)},
        artifacts=((log, "device_log"),),
    )
    manifest = json.loads(
        Path(payload["manifest_path"]).read_text(encoding="utf-8")
    )

    assert manifest["kind"] == "device_collection"
    assert manifest["status"] == "completed"
    assert manifest["verdict"] == "passed"
    assert {row["role"] for row in manifest["artifacts"]} == {
        "device_log",
        "operation_result",
    }

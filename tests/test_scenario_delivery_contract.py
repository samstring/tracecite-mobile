from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
import warnings
from pathlib import Path

import pytest

import tracecite.runtime.scenario as scenario_module
from tracecite_mobile.analysis.scenario import ScenarioError, run_scenario, validate_scenario_spec
from tracecite_mobile.cli import main
from tracecite_core.run import RunIntegrityError, verify_manifest


def _base_spec(source: Path, run_root: Path) -> dict:
    return {
        "schema_version": 2,
        "name": "delivery-contract",
        "source": {"type": "file", "path": str(source)},
        "parse": {"segmenter": "rawtext"},
        "filter": {"grep": "target"},
        "assert": {
            "rules": [
                {
                    "name": "has-target",
                    "type": "count",
                    "event": {"match": "target"},
                    "min": 1,
                }
            ]
        },
        "output": {"run_dir": str(run_root)},
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_repeated_runs_never_overwrite_historical_run(tmp_path: Path) -> None:
    source = tmp_path / "source.log"
    source.write_text("target\n", encoding="utf-8")
    spec = _base_spec(source, tmp_path / "runs")

    first = run_scenario(spec, base_dir=tmp_path)
    first_manifest = Path(first["manifest_path"])
    first_bytes = first_manifest.read_bytes()
    first_artifacts = {
        Path(row["path"]): row["sha256"]
        for row in json.loads(first_bytes)["artifacts"]
    }

    second = run_scenario(spec, base_dir=tmp_path)

    assert first["run_id"] != second["run_id"]
    assert Path(first["run_dir"]) != Path(second["run_dir"])
    assert first_manifest.read_bytes() == first_bytes
    assert all(_sha256(path) == digest for path, digest in first_artifacts.items())
    assert verify_manifest(first_manifest)["valid"] is True


def test_manifest_verification_detects_tampered_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.log"
    source.write_text("target\n", encoding="utf-8")
    summary = run_scenario(_base_spec(source, tmp_path / "runs"), base_dir=tmp_path)
    manifest = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    artifact = Path(manifest["artifacts"][0]["path"])
    artifact.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="发生变化"):
        verify_manifest(Path(summary["manifest_path"]))


def test_assertion_failure_is_completed_but_cli_exits_two(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source.log"
    source.write_text("target\n", encoding="utf-8")
    spec = _base_spec(source, tmp_path / "runs")
    spec["assert"]["rules"][0]["event"] = {"match": "missing"}
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    assert main(["scenario", "run", str(path), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    assert payload["verdict"] == "failed"
    assert manifest["status"] == "completed"
    assert manifest["verdict"] == "failed"


def test_validate_explain_and_manifest_verify_cli(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source.log"
    source.write_text("target\n", encoding="utf-8")
    spec = _base_spec(source, tmp_path / "runs")
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    assert main(["scenario", "validate", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(["scenario", "explain", str(path), "--json"]) == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained["source"]["files"] == [str(source)]
    assert explained["filters"][0]["pattern"] == "target"

    summary = run_scenario(spec, base_dir=tmp_path)
    assert main(
        ["scenario", "verify", summary["manifest_path"], "--json"]
    ) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["valid"] is True
    assert verified["run_id"] == summary["run_id"]


def test_partial_input_failure_respects_all_and_best_effort(
    tmp_path: Path, monkeypatch
) -> None:
    source_dir = tmp_path / "logs"
    source_dir.mkdir()
    (source_dir / "a.log").write_text("target\n", encoding="utf-8")
    (source_dir / "b.log").write_text("target\n", encoding="utf-8")
    spec = _base_spec(source_dir, tmp_path / "runs")
    spec["source"].update({"glob": "*.log", "policy": "all"})
    spec["assert"]["rules"][0]["exact"] = 1
    spec["assert"]["rules"][0].pop("min")

    real_filter = scenario_module._run_one_filter

    def fail_second(path, **kwargs):
        if Path(path).name.startswith("0002_"):
            return {"input": str(path), "error": "synthetic source failure"}, None
        return real_filter(path, **kwargs)

    monkeypatch.setattr(scenario_module, "_run_one_filter", fail_second)

    strict = run_scenario(spec, base_dir=tmp_path)
    assert strict["verdict"] == "incomplete"
    assert strict["source_completeness"] == {
        "policy": "all",
        "expected": 2,
        "succeeded": 1,
        "failed": 1,
        "complete": False,
        "accepted": False,
    }

    spec["source"]["policy"] = "best_effort"
    tolerant = run_scenario(spec, base_dir=tmp_path)
    assert tolerant["verdict"] == "passed"
    assert tolerant["source_completeness"]["accepted"] is True
    assert tolerant["source_completeness"]["complete"] is False


def test_per_file_auto_detection_and_non_utf8_input(tmp_path: Path) -> None:
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "app.log").write_text(
        "2026-08-09 10:00:00.001 I Unit : target applog\n",
        encoding="utf-8",
    )
    (mixed / "events.jsonl").write_text(
        json.dumps({"ts": "2026-08-09 10:00:01.001", "msg": "target json"}) + "\n",
        encoding="utf-8",
    )
    spec = _base_spec(mixed, tmp_path / "mixed-runs")
    spec["source"]["glob"] = "*"
    spec["parse"]["segmenter"] = "auto"
    spec["assert"]["rules"][0]["min"] = 2

    mixed_summary = run_scenario(spec, base_dir=tmp_path)
    assert mixed_summary["verdict"] == "passed"
    assert mixed_summary["segmenter"] == "mixed"
    assert {row["segmenter"] for row in mixed_summary["segmenters"]} == {
        "applog",
        "jsonline",
    }

    encoded = tmp_path / "latin1.log"
    encoded.write_bytes("café target\n".encode("cp1252"))
    encoded_spec = _base_spec(encoded, tmp_path / "encoded-runs")
    encoded_spec["source"]["encoding"] = "cp1252"
    encoded_spec["filter"]["grep"] = "café"
    encoded_spec["assert"]["rules"][0]["event"] = {"match": "café"}
    encoded_summary = run_scenario(encoded_spec, base_dir=tmp_path)
    assert encoded_summary["verdict"] == "passed"
    assert Path(encoded_summary["results"][0]["output_path"]).read_text(
        encoding="utf-8"
    ).endswith("café target\n")


def test_action_contract_is_argv_only_and_persisted_in_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.log"
    source.write_text("target\n", encoding="utf-8")
    spec = _base_spec(source, tmp_path / "runs")
    script = (
        "from pathlib import Path; import os; "
        "Path(os.environ['TRACECITE_CORE_ACTION_OUTPUT_DIR'], 'receipt.txt').write_text("
        "os.environ['TRACECITE_CORE_RUN_ID'], encoding='utf-8')"
    )
    spec["actions"] = [
        {
            "name": "receipt",
            "run": [sys.executable, "-c", script],
            "outputs": ["receipt.txt"],
        }
    ]

    summary = run_scenario(spec, base_dir=tmp_path)
    manifest = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    assert summary["verdict"] == "passed"
    assert manifest["delivery"]["satisfied"] is True
    assert manifest["delivery"]["actions"][0]["run"] == [sys.executable, "-c", script]
    assert any(row["role"] == "action_output" for row in manifest["artifacts"])

    spec["actions"][0]["outputs"] = ["missing.txt"]
    failed = run_scenario(spec, base_dir=tmp_path)
    assert failed["verdict"] == "failed"
    assert failed["delivery_satisfied"] is False


def test_v2_schema_rejects_shell_string_and_removed_output_dir(tmp_path: Path) -> None:
    source = tmp_path / "source.log"
    source.write_text("target\n", encoding="utf-8")
    spec = _base_spec(source, tmp_path / "runs")
    spec.pop("schema_version")
    with pytest.raises(ScenarioError, match="显式声明"):
        validate_scenario_spec(spec)

    spec = _base_spec(source, tmp_path / "runs")
    spec["actions"] = [{"run": "echo unsafe"}]
    with pytest.raises(ScenarioError, match="字符串数组"):
        validate_scenario_spec(spec)

    spec.pop("actions")
    spec["output"]["dir"] = str(tmp_path / "old")
    with pytest.raises(ScenarioError, match="已移除"):
        validate_scenario_spec(spec)

    spec = _base_spec(source, tmp_path / "runs")
    spec["source"] = {"type": "live", "cmd": "printf target"}
    with pytest.raises(ScenarioError, match="字符串数组"):
        validate_scenario_spec(spec)

    spec["source"] = {
        "type": "live",
        "cmd": ["printf", "target"],
        "output": str(tmp_path / "outside.log"),
    }
    with pytest.raises(ScenarioError, match="未知字段"):
        validate_scenario_spec(spec)


def test_live_capture_and_archive_containers_are_frozen_into_run(
    tmp_path: Path,
) -> None:
    live_spec = _base_spec(tmp_path / "unused.log", tmp_path / "live-runs")
    live_spec["source"] = {
        "type": "live",
        "cmd": [sys.executable, "-c", "print('target', flush=True)"],
        "duration": 0.1,
    }
    live = run_scenario(live_spec, base_dir=tmp_path)
    live_reference = Path(live["live_capture"])
    live_manifest = json.loads(
        Path(live["manifest_path"]).read_text(encoding="utf-8")
    )
    assert live_reference.is_file()
    assert live_reference.is_relative_to(Path(live["run_dir"]))
    assert any(row["role"] == "source_container" for row in live_manifest["inputs"])

    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    for index in range(2):
        with zipfile.ZipFile(archive_dir / f"{index}.zip", "w") as archive:
            archive.writestr(f"{index}.log", "target\n")
    archive_spec = _base_spec(archive_dir, tmp_path / "archive-runs")
    archive_spec["source"]["glob"] = "*.zip"
    archive_spec["assert"]["rules"][0]["min"] = 2
    archived = run_scenario(archive_spec, base_dir=tmp_path)
    archive_manifest = json.loads(
        Path(archived["manifest_path"]).read_text(encoding="utf-8")
    )
    containers = [
        row for row in archive_manifest["inputs"] if row["role"] == "source_container"
    ]
    snapshots = [
        row for row in archive_manifest["inputs"] if row["role"] == "source_snapshot"
    ]
    assert len(containers) == 2
    assert all(row["metadata"]["container_snapshot"] for row in snapshots)


def test_format_self_check_distinguishes_continuations_from_missed_starts(
    tmp_path: Path,
) -> None:
    multiline = tmp_path / "multiline.log"
    multiline.write_text(
        "2026-08-09 10:00:00.001 I Unit : target\n"
        + "  continuation\n" * 10,
        encoding="utf-8",
    )
    valid_spec = _base_spec(multiline, tmp_path / "valid-runs")
    valid_spec["parse"]["segmenter"] = "applog"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_scenario(valid_spec, base_dir=tmp_path)
    assert not [item for item in caught if "格式自检" in str(item.message)]

    mismatched_spec = _base_spec(multiline, tmp_path / "mismatch-runs")
    mismatched_spec["parse"] = {"format": {"start": r"^Jul\s+\d+"}}
    with pytest.warns(UserWarning, match="时间戳候选未被"):
        run_scenario(mismatched_spec, base_dir=tmp_path)

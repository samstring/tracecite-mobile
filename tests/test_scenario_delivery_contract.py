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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_scenario_manifest_verifies_and_detects_tampering(tmp_path: Path) -> None:
    source = tmp_path / "app.log"
    source.write_text("target\nnoise\n", encoding="utf-8")
    result = run_scenario(_base_spec(source, tmp_path / "runs"), base_dir=tmp_path)
    manifest_path = Path(result["manifest_path"])
    checked = verify_manifest(manifest_path)
    assert checked["integrity_checked"] is True

    filtered = next(
        Path(row["path"])
        for row in json.loads(manifest_path.read_text(encoding="utf-8"))["artifacts"]
        if row["role"] == "filtered_log"
    )
    filtered.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RunIntegrityError):
        verify_manifest(manifest_path)


def test_scenario_manifest_freezes_original_source(tmp_path: Path) -> None:
    source = tmp_path / "app.log"
    source.write_text("target\n", encoding="utf-8")
    result = run_scenario(_base_spec(source, tmp_path / "runs"), base_dir=tmp_path)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    snapshots = [row for row in manifest["inputs"] if row["role"] == "source_snapshot"]
    assert len(snapshots) == 1
    assert snapshots[0]["sha256"] == _sha256(source)
    assert Path(snapshots[0]["path"]).is_relative_to(Path(result["run_dir"]))


def test_scenario_rejects_removed_and_unsafe_schema_shapes(tmp_path: Path) -> None:
    source = tmp_path / "app.log"
    source.write_text("target\n", encoding="utf-8")
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
    assert len(snapshots) == 2
    # Mobile owns the domain adapter, while container-to-member lineage is a
    # Core manifest concern.  The Mobile contract requires immutable snapshots
    # with explicit member paths and separately frozen source containers; it
    # does not duplicate Core's internal extraction-directory convention.
    assert all(row["metadata"].get("member_path") for row in snapshots)
    assert all(Path(row["path"]).is_relative_to(Path(archived["run_dir"])) for row in snapshots)


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
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_scenario(mismatched_spec, base_dir=tmp_path)
    assert [item for item in caught if "格式自检" in str(item.message)]


def test_cli_verify_scenario_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "app.log"
    source.write_text("target\n", encoding="utf-8")
    result = run_scenario(_base_spec(source, tmp_path / "runs"), base_dir=tmp_path)
    code = main(["scenario", "verify", result["manifest_path"], "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["integrity_checked"] is True


def test_scenario_runtime_remains_mobile_authorized() -> None:
    assert scenario_module.DEFAULT_RUNTIME.allow_live_source is False

from __future__ import annotations

import json
from pathlib import Path

from tracecite_mobile.analysis.scenario import run_scenario


def test_scenario_delivers_events_and_manifest_outside_source(tmp_path: Path) -> None:
    source_dir = tmp_path / "readonly-source"
    source_dir.mkdir()
    source = source_dir / "app.log"
    source.write_text(
        "2026-08-09 10:00:00.001 I Action : tap example\n"
        "2026-08-09 10:00:01.001 I Net : request success\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "runs"
    spec = {
        "schema_version": 2,
        "name": "delivery-test",
        "source": {"type": "file", "path": str(source)},
        "parse": {"segmenter": "applog"},
        "filter": {"grep": "tap|success", "snapshot": True},
        "assert": {
            "rules": [
                {
                    "name": "tap-before-success",
                    "type": "sequence",
                    "events": [{"match": "tap"}, {"match": "success"}],
                    "within": "2s",
                }
            ]
        },
        "output": {
            "run_dir": str(run_dir),
            "reports": ["markdown"],
        },
    }

    summary = run_scenario(spec, base_dir=tmp_path)
    result = summary["results"][0]
    assert summary["required_satisfied"] is True
    evidence_dir = Path(summary["run_dir"]) / "evidence"
    assert Path(result["output_path"]).parent == evidence_dir
    assert Path(result["events_path"]).is_file()
    assert Path(result["snapshot_path"]).parent == evidence_dir / ".snapshots"
    assert not (source_dir / ".filtered").exists()
    assert not (source_dir / ".snapshots").exists()

    manifest = Path(summary["manifest_path"])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["run_id"] == summary["run_id"]
    assert payload["status"] == "completed"
    assert payload["verdict"] == "passed"
    assert payload["metrics"]["match_records"] == 2
    assert payload["metrics"]["event_count"] == 2
    roles = {item["role"] for item in payload["artifacts"]}
    assert {"filtered_log", "events", "snapshot", "filter_history", "matched_records"} <= roles
    assert "report" in roles
    report_path = Path(summary["reports"][0]["path"])
    assert report_path.parent == manifest.parent / "reports"
    assert "tap-before-success" in report_path.read_text(encoding="utf-8")


def test_multi_file_assertions_use_one_ordered_event_stream(tmp_path: Path) -> None:
    source_dir = tmp_path / "logs"
    source_dir.mkdir()
    (source_dir / "01.log").write_text(
        "2026-08-09 10:00:00.001 I Flow : start checkout\n",
        encoding="utf-8",
    )
    (source_dir / "02.log").write_text(
        "2026-08-09 10:00:01.001 I Flow : finish checkout\n",
        encoding="utf-8",
    )
    spec = {
        "schema_version": 2,
        "name": "cross-file-sequence",
        "source": {"type": "file", "path": str(source_dir), "glob": "*.log"},
        "parse": {"segmenter": "applog"},
        "filter": {"grep": "checkout"},
        "assert": {
            "rules": [
                {
                    "name": "checkout-completes",
                    "type": "sequence",
                    "events": [{"match": "start"}, {"match": "finish"}],
                    "within": "2s",
                }
            ]
        },
        "output": {"run_dir": str(tmp_path / "runs")},
    }

    summary = run_scenario(spec, base_dir=tmp_path)

    assert all("assertions" not in row for row in summary["results"])
    assert summary["required_satisfied"] is True
    assert summary["assertions"]["assertions"][0]["details"]["elapsed_seconds"] == 1.0
    json.dumps(summary)

    manifest = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["assertions"]["required_satisfied"] is True
    assert manifest["assertions"]["all_required_satisfied"] is True
    assert "per_source" not in manifest["assertions"]

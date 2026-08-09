from __future__ import annotations

import json

from tracecite_mobile.analysis.behavior_summary import summarize_behavior_text
from tracecite_mobile.analysis.knowledge import knowledge_from_dict


def test_canonical_marker_builds_technical_event() -> None:
    knowledge = knowledge_from_dict(
        {
            "knowledge_schema_version": 3,
            "markers": [
                {"needle": "task.started", "category": "task", "label": "Task started"}
            ],
        }
    )

    assert len(knowledge.markers) == 1
    assert knowledge.markers[0].event == "task"
    assert "task" in knowledge.events
    payload = knowledge.to_dict()
    assert {"markers", "events", "behaviors", "scenarios"} <= payload.keys()
    assert len(payload["markers"]) == 1


def test_four_layers_produce_business_event_and_scenario_result(tmp_path) -> None:
    meta = tmp_path / ".tracecite"
    meta.mkdir()
    (meta / "knowledge.ios.json").write_text(
        json.dumps(
            {
                "knowledge_schema_version": 3,
                "markers": [
                    {"id": "marker.task", "needle": "task.started", "event": "task.started"}
                ],
                "events": {
                    "task.started": {
                        "category": "task",
                        "name": "task.started",
                        "label": "Task started",
                    }
                },
                "behaviors": [
                    {"id": "task.flow.started", "title": "Task flow started", "event": "task.started"}
                ],
                "scenarios": {
                    "demo-flow": {
                        "title": "Task flow",
                        "steps": [{"event": "task.flow.started"}],
                        "assertions": [
                            {"name": "started", "type": "contains", "event": "task.flow.started"},
                            {"name": "no-crash", "type": "absent", "event": "crash"},
                        ],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_behavior_text(
        "task.started\n",
        start_dir=tmp_path,
        scenario="demo-flow",
    )

    assert [event.name for event in summary.technical_events] == ["task.started"]
    assert [event.name for event in summary.behaviors] == ["task.flow.started"]
    assert summary.scenario_results[0]["passed"] is True
    assert summary.scenario_results[0]["assertions"][1]["satisfied"] is True


def test_regex_marker_is_analyzer_knowledge(tmp_path) -> None:
    meta = tmp_path / ".tracecite"
    meta.mkdir()
    (meta / "knowledge.android.json").write_text(
        json.dumps(
            {
                "markers": [
                    {
                        "needle": r"SecurityException\b",
                        "match": "regex",
                        "event": "android.security.exception",
                    }
                ],
                "events": {
                    "android.security.exception": {
                        "category": "android-security",
                        "name": "android.security.exception",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    summary = summarize_behavior_text(
        "2026-08-07 23:33:04 E TaskLog: java.lang.SecurityException\n",
        start_dir=tmp_path,
        platform="android",
    )

    assert summary.event_count == 1
    assert summary.events[0].name == "android.security.exception"

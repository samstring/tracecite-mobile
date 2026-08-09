from __future__ import annotations

import json
from pathlib import Path

from tracecite_core.text_filter import DEFAULT_FILTER_PRESETS
from tracecite_mobile.analysis.behavior_summary import (
    summarize_behavior_file,
    summarize_behavior_text,
)
from tracecite_mobile.analysis.knowledge import ensure_default_project_knowledge


def _write_knowledge(root: Path, payload: dict, *, platform: str = "ios") -> None:
    meta = root / ".tracecite"
    meta.mkdir(exist_ok=True)
    (meta / f"knowledge.{platform}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_default_ios_summary_uses_only_system_marker(tmp_path) -> None:
    ensure_default_project_knowledge(tmp_path, platform="ios")
    summary = summarize_behavior_text(
        "Aug  9 10:00:00 Demo[1] <Notice>: UIApplicationDidBecomeActiveNotification\n",
        start_dir=tmp_path,
    )
    assert summary.event_count == 1
    assert summary.events[0].name == "ios.lifecycle"


def test_project_and_scenario_markers_are_knowledge_driven(tmp_path) -> None:
    _write_knowledge(
        tmp_path,
        {
            "version": 1,
            "markers": [
                {"needle": "task.started", "event": "task.started", "label": "Task started"}
            ],
            "events": {
                "task.started": {"category": "task", "name": "task.started"},
                "task.completed": {"category": "task", "name": "task.completed"},
            },
            "scenarios": {
                "task-flow": {
                    "markers": [
                        {"needle": "task.completed", "event": "task.completed", "label": "Task completed"}
                    ]
                }
            },
        },
    )
    path = tmp_path / "runtime.log"
    path.write_text("task.started\ntask.completed\n", encoding="utf-8")
    summary = summarize_behavior_file(path, start_dir=tmp_path, scenario="task-flow")
    assert [event.name for event in summary.events] == ["task.started", "task.completed"]


def test_generic_marker_keeps_only_configured_attributes(tmp_path) -> None:
    _write_knowledge(
        tmp_path,
        {
            "markers": [
                {
                    "needle": "request.failed",
                    "event": "request.failed",
                    "attributes": {"severity": "error"},
                }
            ],
            "events": {
                "request.failed": {"category": "request", "name": "request.failed"}
            },
        },
    )
    summary = summarize_behavior_text(
        "request.failed opaque=value\n", start_dir=tmp_path
    )
    assert summary.events[0].attributes["severity"] == "error"
    assert "opaque" not in summary.events[0].attributes


def test_android_summary_uses_android_project_knowledge(tmp_path) -> None:
    _write_knowledge(
        tmp_path,
        {
            "markers": [
                {"needle": "request.failed", "event": "request.failed", "label": "Request failed"}
            ]
        },
        platform="android",
    )
    path = tmp_path / "android.log"
    path.write_text("08-09 10:00:00.000 100 101 E App: request.failed\n", encoding="utf-8")
    summary = summarize_behavior_file(path, start_dir=tmp_path, platform="android")
    assert summary.event_count == 1
    assert summary.events[0].label == "Request failed"


def test_user_behavior_core_preset_has_no_code_keywords() -> None:
    assert DEFAULT_FILTER_PRESETS["user-behavior"][0] == ""

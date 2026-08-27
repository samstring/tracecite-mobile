"""TraceCite Mobile 平台扩展 SDK 回归测试。"""

import json
from pathlib import Path

from tracecite_mobile.analysis.scenario import run_scenario
from tracecite_mobile.analysis.behavior_summary import summarize_behavior_text
from tracecite_mobile.cli import main
from tracecite_mobile.platforms.registry import available_platforms, get_backend
from tracecite_mobile.platforms import Capabilities
from tracecite_mobile.plugin_sdk import (
    AnalyzerPluginAPI,
    AssertionOutcome,
    BaseBackend,
    DeviceRef,
    ReportArtifact,
    load_analyzer_plugins,
)
from tracecite_core.events import AnalysisEvent
from tracecite_core.source import SourceResolution


class _UnitBackend(BaseBackend):
    platform = "unit-platform"

    def capabilities(self):
        return Capabilities(platform=self.platform, device=True)

    def list_devices(self):
        return [
            DeviceRef(
                platform=self.platform,
                identifier="unit-1",
                name="Unit Device",
                model="virtual",
            )
        ]


def test_analyzer_plugin_api_registers_platform_backend(capsys) -> None:
    api = AnalyzerPluginAPI()
    api.register_backend("unit-platform", _UnitBackend)

    assert "unit-platform" in available_platforms()
    assert isinstance(get_backend("unit-platform"), _UnitBackend)

    assert main(["--platform", "unit-platform", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["identifier"] == "unit-1"


def test_custom_platform_has_generic_profile(tmp_path, monkeypatch, capsys) -> None:
    api = AnalyzerPluginAPI()
    api.register_backend("profile-platform", _UnitBackend)
    monkeypatch.chdir(tmp_path)

    assert main(["--platform", "profile-platform", "profile", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["platform"] == "profile-platform"
    assert payload["scenarios"] == {}
    assert payload["filter_presets"] == {}
    assert payload["log_output_dir"].endswith("/profile-platform/log")


def test_behavior_parser_provider_is_project_neutral(tmp_path) -> None:
    api = AnalyzerPluginAPI()

    def parser(text, context):
        if "provider.signal" not in text:
            return None
        return AnalysisEvent(
            timestamp=context.timestamp,
            category="provider",
            name="provider.signal",
            label="Provider signal",
            source="unit-provider",
            text=text,
        )

    api.register_behavior_parser("unit-provider", parser, replace=True)
    api.register_behavior_parser("unit-provider", parser)
    meta = tmp_path / ".tracecite"
    meta.mkdir()
    (meta / "knowledge.ios.json").write_text('{"markers": []}\n', encoding="utf-8")

    summary = summarize_behavior_text(
        "2026-08-09 10:00:00 provider.signal\n", start_dir=tmp_path
    )

    assert summary.events[0].source == "unit-provider"


def test_all_scenario_extension_points_execute_end_to_end(tmp_path: Path) -> None:
    api = AnalyzerPluginAPI()
    calls = {"source": 0, "events": 0, "assertion": 0, "report": 0}

    def source_provider(spec, base_dir):
        calls["source"] += 1
        path = Path(base_dir) / str(spec["fixture"])
        return SourceResolution((path,))

    def event_transformer(event, params, context):
        calls["events"] += 1
        assert context.scenario == "plugin-pipeline"
        return AnalysisEvent(
            timestamp=event.timestamp,
            category=str(params["category"]),
            name=event.name,
            source="unit-transformer",
            label=event.label,
            attributes={**event.attributes, "transformed": True},
            raw_ref=event.raw_ref,
            text=event.text,
        )

    def assertion_type(rule, context):
        calls["assertion"] += 1
        hits = sum(event.category == rule["category"] for event in context.events)
        return AssertionOutcome(hits >= int(rule["min"]), hits, {"category": rule["category"]})

    def report_outputter(context, options):
        calls["report"] += 1
        path = context.output_dir / str(options["path"])
        path.write_text(f"run={context.run.run_id}\n", encoding="utf-8")
        return ReportArtifact(path, metadata={"format": "unit"})

    api.register_source_provider("unit-source", source_provider)
    api.register_event_transformer("unit-events", event_transformer)
    api.register_assertion_type("unit-count", assertion_type)
    api.register_report_outputter("unit-report", report_outputter)

    (tmp_path / "fixture.log").write_text(
        "2026-08-09 10:00:00.001 I Unit : alpha\n"
        "2026-08-09 10:00:01.001 I Unit : beta\n",
        encoding="utf-8",
    )
    spec = {
        "schema_version": 2,
        "name": "plugin-pipeline",
        "source": {"type": "unit-source", "fixture": "fixture.log"},
        "parse": {"segmenter": "applog"},
        "filter": {"grep": "alpha|beta"},
        "events": {"transforms": [{"type": "unit-events", "category": "unit"}]},
        "assert": {
            "rules": [
                {"name": "two-unit-events", "type": "unit-count", "category": "unit", "min": 2}
            ]
        },
        "output": {
            "run_dir": str(tmp_path / "runs"),
            "reports": [{"type": "unit-report", "path": "unit-report.txt"}],
        },
    }

    summary = run_scenario(spec, base_dir=tmp_path)

    assert summary["required_satisfied"] is True
    assert summary["assertions"]["assertions"][0]["hits"] == 2
    assert calls == {"source": 1, "events": 2, "assertion": 1, "report": 1}
    assert Path(summary["reports"][0]["path"]).read_text(encoding="utf-8").startswith("run=")
    manifest = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    report = next(item for item in manifest["artifacts"] if item["role"] == "report")
    assert report["metadata"] == {"format": "unit"}


def test_analyzer_entrypoint_uses_its_own_version_declaration(monkeypatch) -> None:
    calls = []

    def fake_loader(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("tracecite_mobile.plugin_sdk.load_entrypoint_plugins", fake_loader)

    assert load_analyzer_plugins() == []
    assert calls[0]["group"] == "tracecite.core.plugins"
    assert calls[1]["group"] == "tracecite.mobile.plugins"
    assert calls[1]["version_attribute"] == "TRACECITE_MOBILE_PLUGIN_API"

from __future__ import annotations

import json
from pathlib import Path

from tracecite_core.immutable import is_stable_source as is_immutable_log_source
from tracecite_mobile.shared.output_layout import (
    DEFAULT_OUTPUT_CONFIG,
    OutputLayout,
    load_output_config,
)


def test_default_output_root_is_documents_tracecite(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "output.json"
    monkeypatch.setattr(
        "tracecite.output_layout.USER_OUTPUT_CONFIG_PATH",
        config_path,
    )
    layout = OutputLayout.load()
    assert layout.output_root == (Path.home() / "Documents" / "TraceCite").resolve()
    ios = layout.mobile("ios")
    assert ios.log_dir.name == "log"
    assert ios.runs_dir.name == "runs"
    assert ios.instrument_dir.name == "instrument"


def test_user_output_config_overrides_root(monkeypatch, tmp_path) -> None:
    custom_root = tmp_path / "MyTrace"
    config_path = tmp_path / "output.json"
    config_path.write_text(
        json.dumps({"output_root": str(custom_root)}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tracecite.output_layout.USER_OUTPUT_CONFIG_PATH",
        config_path,
    )
    layout = OutputLayout.load()
    assert layout.output_root == custom_root.resolve()


def test_is_immutable_log_source() -> None:
    archive = Path("/tmp/log/.archive/device/sealed_20260101-20260102.log")
    pulled = Path("/tmp/log/.archive/pulled/window.log")
    hot = Path("/tmp/log/ios_live_phone.log")
    assert is_immutable_log_source(archive) is True
    assert is_immutable_log_source(pulled) is True
    assert is_immutable_log_source(hot) is False


def test_load_output_config_deep_merges_plugins(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "output.json"
    config_path.write_text(
        json.dumps({"plugins": {"mobile": {"dir": "mobile-dev"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tracecite.output_layout.USER_OUTPUT_CONFIG_PATH",
        config_path,
    )
    config = load_output_config()
    assert config["output_root"] == DEFAULT_OUTPUT_CONFIG["output_root"]
    assert config["plugins"]["mobile"]["dir"] == "mobile-dev"
    assert config["plugins"]["bugly"]["dir"] == "bugly"

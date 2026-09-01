from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CURRENT_EXTENSION_SURFACES = (
    "src/tracecite_mobile/extension.py",
    "src/tracecite_mobile/analysis/scenario_runtime.py",
    "docs/agent-integration.md",
    "docs/agent-integration.zh-CN.md",
    "README.md",
    "README.zh-CN.md",
)


@pytest.mark.parametrize("relative_path", CURRENT_EXTENSION_SURFACES)
def test_current_extension_surfaces_do_not_present_v2_as_an_integration_mode(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "Extension Protocol v2" not in text
    assert "Extension Contract v2" not in text


def test_mobile_still_uses_core_machine_protocol_version() -> None:
    from tracecite.extension import EXTENSION_PROTOCOL_VERSION
    from tracecite_mobile.extension import EXTENSION

    assert EXTENSION.manifest.protocol_version == EXTENSION_PROTOCOL_VERSION == "2"

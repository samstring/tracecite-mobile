from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from tracecite_mobile.cli import build_parser


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "tracecite_mobile"
PUBLIC_SURFACE = (
    PACKAGE,
    ROOT / "docs",
    ROOT / "skills",
    ROOT / "examples",
    ROOT / "tests",
)
PUBLIC_FILES = (ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "pyproject.toml")


def test_mobile_depends_only_on_public_tracecite_layers_at_runtime() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"tracecite>=0.1.0,<0.2.0"' in pyproject
    assert "tracecite-core" not in pyproject
    assert "tracecite-agent" not in pyproject
    private_distribution = "tracecite-" + "liz" + "hi"
    private_import = "tracecite_" + "liz" + "hi"
    assert private_distribution not in pyproject
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not item.name.startswith(private_import) for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(private_import)


def test_mobile_scenario_compatibility_imports_use_runtime_implementation() -> None:
    from tracecite.runtime.assertions import build_assertions as runtime_build_assertions
    from tracecite.runtime.scenario import validate_scenario_spec as runtime_validate
    from tracecite_mobile.analysis.assertions import build_assertions as mobile_build_assertions
    from tracecite_mobile.analysis.scenario import validate_scenario_spec as mobile_validate

    assert mobile_build_assertions is runtime_build_assertions
    assert mobile_validate is runtime_validate


def test_mobile_registers_through_public_extension_v2_contract() -> None:
    from tracecite.extension import EXTENSION_PROTOCOL_VERSION, get_runtime, register_extension
    from tracecite.runtime import DEFAULT_RUNTIME
    from tracecite_mobile.extension import EXTENSION

    assert EXTENSION_PROTOCOL_VERSION == "2"
    assert EXTENSION.manifest.protocol_version == "2"
    assert EXTENSION.manifest.id == "mobile"
    assert DEFAULT_RUNTIME.allow_live_source is False
    assert DEFAULT_RUNTIME.allow_actions is False

    register_extension(EXTENSION)
    runtime = get_runtime("mobile")

    assert runtime.allow_live_source is True
    assert runtime.allow_actions is True


def test_builtin_formats_do_not_force_replace_core_registrations() -> None:
    source = (PACKAGE / "plugins" / "__init__.py").read_text(encoding="utf-8")
    assert "replace=True" not in source
    package_init = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    assert "from . import plugins" not in package_init


def test_mobile_has_no_upper_layer_inspection_surface() -> None:
    short = "u" + "i"
    alternate = "look" + "in"
    inspection_suffix = "walk" + "through"
    forbidden_files = {f"{short}.py", f"{alternate}.py", f"{alternate}_{inspection_suffix}.py"}
    assert not any(path.name in forbidden_files for path in PACKAGE.rglob("*.py"))
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([short, "dump"])
    with pytest.raises(SystemExit):
        parser.parse_args([alternate, "hier" + "archy"])


def test_mobile_public_surface_has_no_project_log_protocols() -> None:
    forbidden = (
        "u" + "i_click",
        "page_" + "jump",
        "foreground_" + "change",
        "network_state_" + "change",
        "User " + "clicked",
        "ViewController " + "jumped",
        "append " + "breadcrumb",
        "Example" + "APM",
        "Trace" + "Monitor",
        "EVENT_NET_" + "HTTP",
        "HTTP_" + "BEGIN",
        "HTTP_" + "END",
        "Demo" + "Net" + ".reporter",
        "call" + "Status",
        "call" + "Cost",
        "Frame" + "Monitor",
        "startTrace" + "Page",
        "stopTrace" + "Page",
        "DEMO_" + "APM",
        "R" + "DS" + "EVENT",
        "NS" + "Dictionary",
        "Sensors" + "Data",
        "current_page_" + "name",
        "transaction" + "Id",
        "send" + "Cost",
        "$element_" + "name",
        "神" + "策",
        "Demo" + "APM",
        "accom" + "pany" + "-list",
        "~/Down" + "loads/logs",
        "Down" + "loads/",
        "/Users" + "/",
        "PP" + "Live",
        "Liz" + "hi",
        "Te" + "ki",
        "IT" + "Net",
        "live" + "-" + "gi" + "ft",
        "live" + "_" + "gi" + "ft",
    )
    paths = [
        path
        for root in PUBLIC_SURFACE
        for path in root.rglob("*")
        if path.is_file() and path.suffix not in {".pyc", ".whl"}
    ]
    paths.extend(path for path in PUBLIC_FILES if path.is_file())
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        for token in forbidden:
            assert token.casefold() not in text, (path, token)
        acronym = "r" + "ds"
        assert re.search(rf"\b{acronym}\b", text) is None, path


def test_generic_behavior_module_has_no_project_field_parser() -> None:
    text = (PACKAGE / "analysis" / "behavior_summary.py").read_text(encoding="utf-8")
    forbidden_symbols = (
        "_parse_" + "breadcrumb",
        "click_" + "label",
        "decode_oc_" + "unicode",
        "_DATA_" + "BUTTON_RE",
        "_PLIST_" + "FORM_RE",
    )
    for symbol in forbidden_symbols:
        assert symbol not in text


def test_mobile_gitignore_protects_runtime_evidence_and_keeps_demo(
    tmp_path: Path,
) -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required_rules = {
        ".code-review-graph/",
        ".device-debug/",
        ".workbuddy/",
        ".env",
        ".env.*",
        "!.env.example",
        "*.pem",
        "*.key",
        "*.p12",
        "*.mobileprovision",
        ".pytest_cache/",
        ".coverage",
        "htmlcov/",
        "/.tracecite/",
        ".filtered/",
        ".tracecite-session.json",
        ".tracecite-sessions.json",
        ".tracecite-capture.json",
        "*.records.jsonl",
        "*.hits.jsonl",
        "filter_history.jsonl.lock",
        "*.log",
        "!examples/demoapp.log",
        ".DS_Store",
        "*.ips",
        "*.crash",
        "*.xcresult",
        "*.xcarchive",
        "*.dSYM/",
        "*.sqlite",
        "*.db",
    }
    rules = {
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert required_rules <= rules
    assert gitignore.index("*.log") < gitignore.index("!examples/demoapp.log")

    (tmp_path / ".gitignore").write_text(gitignore, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    generated = (
        ".code-review-graph/graph.db",
        ".tracecite/runs/demo/manifest.json",
        ".filtered/evidence.txt",
        ".tracecite-session.json",
        ".tracecite-sessions.json.lock",
        ".tracecite-capture.json",
        "runtime.log",
        "result.txt.records.jsonl",
        ".coverage",
        ".DS_Store",
        ".env",
        ".env.local",
        "signing.pem",
        "signing.key",
        "profile.p12",
        "Demo.mobileprovision",
        "incident.ips",
        "incident.crash",
        "Demo.xcresult/Info.plist",
        "Demo.xcarchive/Info.plist",
        "Demo.dSYM/Contents/Info.plist",
        "cache.sqlite",
        "state.db",
        ".device-debug/state.json",
        ".workbuddy/session.json",
    )
    for relative in generated:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative],
            cwd=tmp_path,
            check=False,
        )
        assert ignored.returncode == 0, relative

    demo = tmp_path / "examples" / "demoapp.log"
    demo.parent.mkdir(parents=True, exist_ok=True)
    demo.write_text("task.started\n", encoding="utf-8")
    visible = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", "examples/demoapp.log"],
        cwd=tmp_path,
        check=False,
    )
    assert visible.returncode == 1

    environment_template = tmp_path / ".env.example"
    environment_template.write_text("TRACECITE_SETTING=example\n", encoding="utf-8")
    visible_template = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", ".env.example"],
        cwd=tmp_path,
        check=False,
    )
    assert visible_template.returncode == 1

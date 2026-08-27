from __future__ import annotations

import subprocess
import sys
import textwrap


def _run_isolated(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_importing_mobile_has_no_core_registration_side_effect() -> None:
    result = _run_isolated(
        """
        from tracecite_core.segmenter import available_segmenters

        before = available_segmenters()
        import tracecite_mobile
        after = available_segmenters()

        assert after == before, (before, after)
        """
    )

    assert result.returncode == 0, result.stderr


def test_declarative_extension_is_complete_and_idempotent() -> None:
    result = _run_isolated(
        """
        from tracecite import list_capabilities
        from tracecite.extension import available_runtimes, get_runtime, list_extensions
        from tracecite_core.segmenter import available_segmenters
        from tracecite_mobile.extension import EXTENSION, register

        assert EXTENSION.manifest.protocol_version == "2"
        assert EXTENSION.manifest.id == "mobile"
        assert "devicelog" not in available_segmenters()
        assert available_runtimes() == ["default"]
        assert not [item for item in list_capabilities() if item.name.startswith("mobile.")]

        first = register()
        second = register()
        assert first is EXTENSION
        assert second is EXTENSION

        expected = {
            "android", "applog", "devicelog", "ios", "mixed", "online",
            "syslog", "threadtime",
        }
        assert expected <= set(available_segmenters())
        assert "mobile" in available_runtimes()
        runtime = get_runtime("mobile")
        assert runtime.allow_live_source is True
        assert runtime.allow_actions is True

        installed = {item["id"]: item for item in list_extensions()}
        assert installed["mobile"]["protocol_version"] == "2"
        kinds = {item["kind"] for item in installed["mobile"]["capabilities"]}
        assert {"core.plugins", "agent.capability", "runtime.scenario"} <= kinds

        capabilities = {item.name: item for item in list_capabilities()}
        assert capabilities["mobile.environment.probe"].safety == "read"
        assert capabilities["mobile.devices.list"].safety == "live_source"
        assert capabilities["mobile.processes.list"].safety == "live_source"
        assert capabilities["mobile.sessions.list"].safety == "live_source"

        for name in (
            "mobile.sessions.start",
            "mobile.sessions.stop",
            "mobile.app.launch",
        ):
            assert capabilities[name].safety == "live_action"
            assert capabilities[name].requires_authorization is True
        """
    )

    assert result.returncode == 0, result.stderr


def test_entrypoint_module_loads_as_protocol_v2_extension() -> None:
    result = _run_isolated(
        """
        from types import SimpleNamespace

        import tracecite.extension as extension_api
        import tracecite_core.plugin_sdk as plugin_sdk
        from tracecite.extension import available_runtimes, load_extensions
        from tracecite_core.segmenter import available_segmenters

        class EntryPoint:
            name = "mobile"
            value = "tracecite_mobile.extension"
            dist = SimpleNamespace(name="tracecite-mobile", version="0.1.0")

            def load(self):
                import tracecite_mobile.extension as extension
                return extension

        def entry_points(**kwargs):
            if kwargs.get("group") == "tracecite.extensions":
                return [EntryPoint()]
            return []

        extension_api.metadata.entry_points = entry_points
        plugin_sdk.metadata.entry_points = entry_points

        loaded = load_extensions(strict=True)
        domain = next(item for item in loaded if item["group"] == "tracecite.extensions")
        assert domain["status"] == "loaded"
        assert domain["protocol_version"] == "2"
        assert domain["extension_id"] == "mobile"
        assert "devicelog" in available_segmenters()
        assert "mobile" in available_runtimes()
        """
    )

    assert result.returncode == 0, result.stderr


def test_registration_conflict_fails_without_replacing_core_capability() -> None:
    result = _run_isolated(
        """
        from tracecite.extension import available_runtimes
        from tracecite_core import build_segmenter, register_segmenter
        from tracecite_mobile.extension import register

        class ExistingSegmenter:
            pass

        register_segmenter("devicelog", ExistingSegmenter)
        try:
            register()
        except Exception as exc:
            assert "devicelog" in str(exc)
        else:
            raise AssertionError("registration conflict must fail")

        assert isinstance(build_segmenter("devicelog"), ExistingSegmenter)
        assert available_runtimes() == ["default"]
        """
    )

    assert result.returncode == 0, result.stderr

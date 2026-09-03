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
        from tracecite.extension import (
            EXTENSION_PROTOCOL_VERSION,
            available_runtimes,
            get_runtime,
            register_extension,
        )
        from tracecite_core.segmenter import available_segmenters
        from tracecite_mobile.extension import EXTENSION, extension

        assert "devicelog" not in available_segmenters()
        assert available_runtimes() == ["default"]
        assert not [item for item in list_capabilities() if item.name.startswith("mobile.")]

        assert extension() is EXTENSION
        assert EXTENSION.manifest.protocol_version == EXTENSION_PROTOCOL_VERSION == "2"
        assert EXTENSION.manifest.domain == "mobile"

        register_extension(EXTENSION)
        register_extension(EXTENSION)

        expected_segmenters = {
            "android", "applog", "devicelog", "ios", "mixed", "online",
            "syslog", "threadtime",
        }
        assert expected_segmenters <= set(available_segmenters())
        assert get_runtime("mobile") is not None
        capabilities = {item.name: item for item in list_capabilities()}

        expected_agent_capabilities = {
            "mobile.environment.probe",
            "mobile.devices.list",
            "mobile.processes.list",
            "mobile.sessions.list",
            "mobile.sessions.start",
            "mobile.sessions.cut",
            "mobile.sessions.stop",
            "mobile.app.launch",
            "mobile.app.stop",
            "mobile.archive.list",
            "mobile.archive.fetch",
            "mobile.performance.profiles",
            "mobile.performance.start",
            "mobile.performance.status",
            "mobile.performance.stop",
            "mobile.diagnostics.run",
            "mobile.crashes.list",
            "mobile.crashes.fetch",
        }
        assert expected_agent_capabilities <= set(capabilities)

        assert capabilities["mobile.environment.probe"].safety == "read"
        assert capabilities["mobile.devices.list"].safety == "live_source"
        assert capabilities["mobile.processes.list"].safety == "live_source"
        assert capabilities["mobile.sessions.list"].safety == "live_source"

        for name in (
            "mobile.sessions.start",
            "mobile.sessions.cut",
            "mobile.sessions.stop",
            "mobile.app.launch",
            "mobile.app.stop",
            "mobile.performance.start",
            "mobile.performance.stop",
        ):
            assert capabilities[name].safety == "live_action"
            assert capabilities[name].requires_authorization is True
        """
    )

    assert result.returncode == 0, result.stderr


def test_entrypoint_loader_accepts_current_mobile_protocol() -> None:
    result = _run_isolated(
        """
        import tracecite.extension as extension_api
        from tracecite.extension import available_runtimes, list_extensions, load_extensions
        from tracecite_core.segmenter import available_segmenters

        class EntryPoint:
            name = "mobile"
            value = "tracecite_mobile.extension:extension"
            dist = None

            def load(self):
                from tracecite_mobile.extension import extension
                return extension

        def entry_points(**kwargs):
            return [EntryPoint()] if kwargs.get("group") == "tracecite.extensions" else []

        extension_api.metadata.entry_points = entry_points
        loaded = load_extensions(strict=True)

        assert loaded[0]["status"] == "loaded"
        assert loaded[0]["protocol_version"] == "2"
        assert loaded[0]["extension_id"] == "mobile"
        assert list_extensions()[0]["protocol_version"] == "2"
        assert "devicelog" in available_segmenters()
        assert "mobile" in available_runtimes()
        """
    )

    assert result.returncode == 0, result.stderr


def test_registration_conflict_fails_without_replacing_core_capability() -> None:
    result = _run_isolated(
        """
        from tracecite.extension import available_runtimes, register_extension
        from tracecite_core import build_segmenter, register_segmenter
        from tracecite_mobile.extension import EXTENSION

        class ExistingSegmenter:
            pass

        register_segmenter("devicelog", ExistingSegmenter)
        try:
            register_extension(EXTENSION)
        except Exception as exc:
            assert "devicelog" in str(exc)
        else:
            raise AssertionError("registration conflict must fail")

        assert build_segmenter("devicelog") is not None
        assert available_runtimes() == ["default"]
        """
    )

    assert result.returncode == 0, result.stderr

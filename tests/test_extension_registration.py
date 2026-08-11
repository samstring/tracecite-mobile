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


def test_explicit_registration_is_complete_and_idempotent() -> None:
    result = _run_isolated(
        """
        from tracecite.extension import ExtensionAPI, available_runtimes, get_runtime
        from tracecite_core.segmenter import available_segmenters
        from tracecite_mobile.analysis.scenario_runtime import MOBILE_RUNTIME
        from tracecite_mobile.extension import register

        assert "devicelog" not in available_segmenters()
        assert available_runtimes() == ["default"]

        register(ExtensionAPI())
        register(ExtensionAPI())

        expected = {
            "android", "applog", "devicelog", "ios", "mixed", "online",
            "syslog", "threadtime",
        }
        assert expected <= set(available_segmenters())
        assert get_runtime("mobile") is MOBILE_RUNTIME
        """
    )

    assert result.returncode == 0, result.stderr


def test_api_version_mismatch_cannot_mutate_core_registry() -> None:
    result = _run_isolated(
        """
        import tracecite_core.plugin_sdk as plugin_sdk
        from tracecite.extension import available_runtimes, load_extensions
        from tracecite_core.segmenter import available_segmenters

        class EntryPoint:
            name = "mobile-version-mismatch"
            value = "tracecite_mobile.extension"
            dist = None

            def load(self):
                import tracecite_mobile.extension as extension
                extension.TRACECITE_EXTENSION_API = "99"
                return extension

        def entry_points(**kwargs):
            return [EntryPoint()] if kwargs.get("group") == "tracecite.extensions" else []

        plugin_sdk.metadata.entry_points = entry_points
        before = available_segmenters()
        loaded = load_extensions(strict=False)

        assert loaded[0]["status"] == "failed"
        assert "需要插件 API 99" in loaded[0]["error"]
        assert available_segmenters() == before
        assert available_runtimes() == ["default"]
        """
    )

    assert result.returncode == 0, result.stderr


def test_registration_conflict_fails_without_replacing_core_capability() -> None:
    result = _run_isolated(
        """
        from tracecite.extension import ExtensionAPI, available_runtimes
        from tracecite_core import build_segmenter, register_segmenter
        from tracecite_mobile.extension import register

        class ExistingSegmenter:
            pass

        register_segmenter("devicelog", ExistingSegmenter)
        try:
            register(ExtensionAPI())
        except ValueError as exc:
            assert "devicelog" in str(exc)
        else:
            raise AssertionError("registration conflict must fail")

        assert build_segmenter("devicelog") is not None
        assert available_runtimes() == ["default"]
        """
    )

    assert result.returncode == 0, result.stderr

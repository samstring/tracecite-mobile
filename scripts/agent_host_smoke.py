#!/usr/bin/env python3
"""Device-free smoke for the TraceCite Mobile Agent-facing surface."""

from __future__ import annotations

import json

from tracecite import list_capabilities
from tracecite.extension import EXTENSION_PROTOCOL_VERSION, list_extensions, register_extension

from tracecite_mobile.extension import EXTENSION


def run_smoke() -> dict[str, object]:
    assert EXTENSION.manifest.protocol_version == EXTENSION_PROTOCOL_VERSION == "2"
    assert EXTENSION.manifest.id == "mobile"
    assert EXTENSION.manifest.domain == "mobile"

    register_extension(EXTENSION)
    register_extension(EXTENSION)

    extensions = {item["extension_id"]: item for item in list_extensions()}
    assert extensions["mobile"]["protocol_version"] == "2"

    capabilities = {item.name: item for item in list_capabilities()}
    expected = {
        "mobile.environment.probe",
        "mobile.devices.list",
        "mobile.processes.list",
        "mobile.sessions.list",
        "mobile.sessions.start",
        "mobile.sessions.stop",
        "mobile.app.launch",
    }
    assert expected <= set(capabilities)

    for name in (
        "mobile.environment.probe",
        "mobile.devices.list",
        "mobile.processes.list",
        "mobile.sessions.list",
    ):
        assert capabilities[name].requires_authorization is False

    for name in (
        "mobile.sessions.start",
        "mobile.sessions.stop",
        "mobile.app.launch",
    ):
        assert capabilities[name].safety == "live_action"
        assert capabilities[name].requires_authorization is True

    return {
        "extension": {
            "id": EXTENSION.manifest.id,
            "domain": EXTENSION.manifest.domain,
            "protocol_version": EXTENSION.manifest.protocol_version,
        },
        "capabilities": sorted(expected),
        "status": "ok",
    }


def main() -> int:
    print(json.dumps(run_smoke(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

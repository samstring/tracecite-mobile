#!/usr/bin/env python3
"""Device-free smoke for the real TraceCite MCP + Mobile Agent surface."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from tracecite.extension import EXTENSION_PROTOCOL_VERSION

from tracecite_mobile.extension import EXTENSION


CORE_TOOLS = {
    "tracecite_retrieve",
    "tracecite_materialize",
    "tracecite_replay",
    "tracecite_aggregate",
    "tracecite_traverse",
    "tracecite_verify",
}

MOBILE_TOOLS = {
    "tracecite_mobile_environment_probe",
    "tracecite_mobile_devices_list",
    "tracecite_mobile_processes_list",
    "tracecite_mobile_sessions_list",
    "tracecite_mobile_sessions_start",
    "tracecite_mobile_sessions_stop",
    "tracecite_mobile_app_launch",
}


async def _run_mcp_smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="tracecite-mobile-mcp-") as tmp:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tracecite_mcp.server"],
            cwd=str(Path(tmp)),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert CORE_TOOLS <= names
                assert MOBILE_TOOLS <= names

                # This capability is read-only and device-free.  It proves the
                # dynamically discovered Mobile tool is executable through the
                # same MCP process an Agent Host will use.  MCP may serialize a
                # dynamically projected capability as text content rather than
                # structured_content, so the smoke intentionally checks the
                # transport success instead of depending on one SDK projection.
                probe = await session.call_tool(
                    "tracecite_mobile_environment_probe",
                    arguments={"arguments": {"platform": "ios"}},
                )
                assert probe.is_error is False
                assert probe.content

                return {
                    "tools": sorted(CORE_TOOLS | MOBILE_TOOLS),
                    "probe_executed": True,
                }


def run_smoke() -> dict[str, object]:
    assert EXTENSION.manifest.protocol_version == EXTENSION_PROTOCOL_VERSION == "2"
    assert EXTENSION.manifest.id == "mobile"
    assert EXTENSION.manifest.domain == "mobile"

    mcp_result = asyncio.run(_run_mcp_smoke())
    return {
        "extension": {
            "id": EXTENSION.manifest.id,
            "domain": EXTENSION.manifest.domain,
            "protocol_version": EXTENSION.manifest.protocol_version,
        },
        "mcp": mcp_result,
        "status": "ok",
    }


def main() -> int:
    print(json.dumps(run_smoke(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Agent integration

TraceCite Mobile is the iOS/Android domain extension for the TraceCite Evidence Runtime.
For the complete Agent Host contract, capability safety model, SourceSession identity,
and Evidence Runtime / MCP handoff semantics, read:

- [Agent integration (English)](agent-integration.md)
- [Agent 接入（简体中文）](agent-integration.zh-CN.md)

When `tracecite-mcp` and `tracecite-mobile` are installed in the same Python environment,
the MCP server discovers Mobile through Core's `tracecite.extensions` entry points and
projects the registered Mobile AgentCapabilities automatically. Agent Hosts do not need
to call `register_extension()` for the MCP path.

A typical mobile evidence pipeline is:

1. initialize `.tracecite/config.json` with `tracecite-mobile profile init`;
2. resolve the platform and an explicit device target;
3. collect bounded device logs with `session` or `stream`;
4. collect performance evidence with `capture` when needed;
5. stop the selected background session or seal another live/hot source before analysis;
6. prefer `mobile.sessions.stop` returned `artifacts` / `evidence_files` when handing a stopped session to Core/MCP;
7. run `filter --json` and `behavior summarize --json` for the selected scope when Mobile-local mechanics are useful;
8. retain manifests, hashes, and evidence paths for review;
9. hand stable artifacts to the canonical TraceCite Evidence API (`retrieve`, `materialize`, `replay`, `aggregate`, `traverse`, `verify`) when generic evidence work is needed.

A live `output_path` from `session start/list` is not automatically an immutable evidence
boundary. The Agent-facing stop adapter only advertises a path in `artifacts` /
`evidence_files` after the session reports stopped and the file passes a bounded size/mtime
stability check.

If a returned Mobile artifact lies outside the MCP process's current path boundary, the
Host must add the appropriate root to `TRACECITE_MCP_ALLOWED_ROOTS`. That setting is an
authorization boundary, not a source inventory.

The Agent owns hypotheses, investigation order, causal interpretation, evidence
sufficiency, the final conclusion, and when to stop. Mobile query/status output is
mechanical scope-bound evidence only; Mobile live actions require explicit
authorization and do not establish app health or root cause.

The public package contains no product defaults. Add project terms through
`.tracecite/knowledge.<platform>.json` or a private upper-layer distribution.

Default outputs live under `~/Documents/TraceCite/`:

```
~/Documents/TraceCite/
├── bugly/{exports,runs,cache}/
└── mobile/
    ├── Android/{log/.archive/, instrument/, runs/}
    └── iOS/{log/.archive/, instrument/, runs/}
```

Override layout in `~/.tracecite/output.json`. Run manifests preserve source
hashes, parameters, assertions, and artifact paths for Agent verification.

For live hot logs, prefer `seal --from-sessions` before `filter` instead of
copying a growing source. Archive / sealed segments are already stable evidence
boundaries.

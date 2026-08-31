<p align="center">
  <strong>TraceCite Mobile</strong>
</p>

<p align="center">
  <strong>The official iOS / Android domain extension for the TraceCite Evidence Runtime.</strong>
</p>

<p align="center">
  <a href="#"><img alt="version" src="https://img.shields.io/badge/version-0.1.0-blue"></a>
  <a href="#"><img alt="license" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="#"><img alt="python" src="https://img.shields.io/badge/python-3.10%2B-blue"></a>
  <a href="#"><img alt="platforms" src="https://img.shields.io/badge/platforms-iOS%20%7C%20Android-lightgrey"></a>
</p>

---

## The Problem

Four things that make mobile debugging harder than it should be:

**Logs are after the fact.** The issue happens on a device, but you can only export logs afterward from an IDE. The context around the failure moment is already gone.

**Logs and user actions speak different languages.** The log says `"[1234]: [UI] button tapped: checkout"`, but you need to answer "what was the user doing before the crash?"

**Lag needs more than logs.** The app froze — logs alone don't show whether it's a main thread block or memory pressure. You need a performance trace, but setting up xctrace or Perfetto by hand is tedious.

**Every investigation starts from scratch.** Same app, similar issue — you're re-inventing what keywords to search, what metrics to check. Nothing carries over.

## Install

```bash
# Install the main TraceCite distribution, then the Mobile extension
pip install tracecite
pip install tracecite-mobile
tracecite-mobile profile init
```

iOS needs `idevicesyslog` (libimobiledevice) + Xcode CLT. Android needs `adb` (SDK Platform Tools).

## Usage

```bash
# 1. Start background capture
tracecite-mobile session start --date

# ... reproduce the issue on device ...

# 2. Analyze the 2-minute window around the failure
tracecite-mobile seal --from-sessions --json
tracecite-mobile filter --from-sessions --last 2m --preset system-fault --json

# 3. Lift raw log lines into user behavior events
tracecite-mobile behavior summarize --from-sessions --json
```

Live logs keep a 30-minute hot window. Active collectors perform one archive
check every 30 minutes and move expired records into the hidden internal
`.archive/` store. Use `archive list` and `archive pull`; direct folder
management is neither required nor recommended.

Maintenance is explicit: `clean analysis --before today` removes only old
logs, performance outputs, and unpinned completed analysis runs. Runtime state,
locks, active/recovery outputs, and malformed manifests are kept fail-closed.
The default pass inspects `~/Documents/TraceCite/mobile/*/runs/` and redirected
`.runs` containers one run directory at a time. Archive evidence is excluded by
default. Preview it with `clean analysis --include-archive --dry-run`; an actual
archive deletion requires both `--include-archive --yes`.

After one full investigation, the Agent has:

- **Structured hits within a time window** — which line, what time, what keyword matched
- **User behavior event stream** — "user opened settings → tapped save → app froze"
- **A complete run record** — input frozen, parameters recorded, reproducible any time

```bash
# Lag issue? Record a performance trace alongside
tracecite-mobile capture start --template "Time Profiler"
# ... reproduce the lag ...
tracecite-mobile capture stop    # → auto trace file + hang summary

# Forget what to search for? Use presets
tracecite-mobile filter app.log --preset system-lifecycle --json
tracecite-mobile filter app.log --preset network-http --json
tracecite-mobile filter app.log --preset memory-leak --json

# Add incident-specific terms without discarding the reusable preset (OR semantics)
tracecite-mobile filter app.log --preset network-http --grep 'checkout|payment' --json
```

## Agent-native contract

TraceCite Mobile is an evidence-acquisition extension, not a planner or root-cause oracle.
The Agent owns hypotheses, investigation order, causal interpretation, evidence sufficiency,
the final conclusion, and when to stop.

Mobile exposes scoped mechanical facts and explicit live actions. Device/process/session
queries do not become global absence claims, and successful live actions do not establish
app health or root cause. Generic evidence retrieval, exact materialization/replay,
aggregation, traversal, verification, provenance, and RetrievalSession novelty belong to
the main TraceCite Evidence Runtime.

See [Agent integration](docs/agent-integration.md) and the repository skill at
[`skills/tracecite-mobile/SKILL.md`](skills/tracecite-mobile/SKILL.md).

## vs. Dumping Raw Logs to AI

| | Raw logs to AI | TraceCite Mobile |
|---|---|---|
| Collection | Manually export from IDE after the fact | Real-time background capture; failure window not lost |
| Data types | Text logs only | Logs + performance traces; one command records both |
| Output format | Raw text; AI parses on its own | Structured JSON + user behavior events |
| User actions | AI infers from log strings | Auto-lifted into structured behavior events |
| Knowledge reuse | None | Auditable project-local presets and candidate terms |
| Reproducibility | Different steps across runs | Full run manifest records all parameters, input frozen |

## Architecture

The main TraceCite distribution provides the canonical Evidence Runtime and the
versioned Extension API. TraceCite MCP can project those same evidence semantics
to MCP Hosts. Mobile stays independent and contributes iOS / Android domain
capabilities through `tracecite.extensions`:

```text
External Agent / Host
        |
TraceCite Evidence Runtime ---- TraceCite MCP (optional transport)
        |
tracecite-mobile
        |
iOS / Android devices
```

Importing either `tracecite` or `tracecite_mobile` does not register Mobile
formats or mutate the Core registry. Use `tracecite extension load` or
`tracecite run ... --load-extensions --runtime mobile` when the main Runtime
should discover the installed Mobile extension. The standalone `tracecite-mobile`
CLI explicitly hosts the same declarative Extension Protocol v2 contribution
before dispatching a command.

<img src="architecture.svg" alt="Mobile architecture: Device, Analysis, Knowledge, Plugin layers on Core" width="100%"/>

`--platform ios|android` switches backends through the same capability contract.
Use `performance profiles` to discover supported profiles; optional operations
fail explicitly when a backend does not declare them.

```bash
tracecite-mobile --platform ios performance profiles --json
tracecite-mobile --platform android performance start --profile frame --json
```

## Customization

**Presets.** Stop writing regex every time. Pre-built keyword sets cover common scenarios,
and project-specific candidates remain governed and auditable:

```bash
tracecite-mobile filter app.log --preset system-lifecycle --json
tracecite-mobile filter app.log --preset network-http --grep 'checkout|payment' --json
tracecite-mobile grow propose scenario task-flow --title "Task flow" \
  --created-by agent-a --case-id run-001 --evidence evidence://run/001#manifest
```

**Scenario files.** Save deterministic analysis mechanics as JSON — team-sharable and version-controllable. The Agent still owns the investigation strategy and causal conclusions:

```json
{
  "schema_version": 2,
  "name": "crash-investigation",
  "source": { "type": "file", "path": "sealed.log" },
  "parse": { "segmenter": "auto" },
  "filter": { "grep": "SIGABRT|SIGSEGV" },
  "assert": {
    "rules": [
      {
        "name": "has-sigabrt",
        "type": "count",
        "event": { "match": "SIGABRT" },
        "min": 1
      }
    ]
  }
}
```

```bash
tracecite-mobile scenario run crash-investigation.json
```

**Knowledge governance.** Agent findings first enter a physically separate
candidate store. A second independent case verifies them; a different reviewer
then authorizes promotion:

```bash
tracecite-mobile grow suggest app.log --preset my-app
tracecite-mobile grow propose learning "Bounded evidence is required" \
  --created-by agent-a --case-id run-001 --evidence evidence://run/001#manifest
tracecite-mobile grow verify kc-ID --case-id run-002 --outcome support \
  --verified-by agent-b --evidence evidence://run/002#manifest
tracecite-mobile grow promote kc-ID --approved-by human-reviewer
tracecite-mobile grow doctor
```

Legacy direct writes (`preset add`, `grow term/marker/learning/playbook/auto`)
are rejected by the Agent CLI. Candidate and trusted knowledge stay in separate
files under `.tracecite/`; nothing is uploaded. A changed trusted file fails its
integrity gate until restored through the governed workflow.

## See Also

- [**TraceCite**](../tracecite-core/) — canonical Evidence Runtime and Extension API.
- [Agent integration](docs/agent-integration.md) — Mobile Host/Agent contract.
- [Agent 接入（简体中文）](docs/agent-integration.zh-CN.md) — Chinese integration contract.

## License

MIT

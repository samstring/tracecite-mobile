<p align="center">
  <strong>TraceCite Mobile</strong>
</p>

<p align="center">
  <strong>The official Mobile domain extension for the TraceCite agent context gateway.</strong>
</p>

<p align="center">
  <a href="#"><img alt="version" src="https://img.shields.io/badge/version-0.1.0-blue"></a>
  <a href="#"><img alt="license" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="#"><img alt="python" src="https://img.shields.io/badge/python-3.10%2B-blue"></a>
  <a href="#"><img alt="platforms" src="https://img.shields.io/badge/platforms-iOS%20%7C%20Android-lightgrey"></a>
</p>

---

## The Problem

Four things make mobile debugging harder than it should be:

**Logs are after the fact.** The issue happens on a device, but you often export logs only afterward. The context around the failure moment may already be gone.

**Logs and user actions speak different languages.** A raw line can say that a button was tapped, while the investigation needs a structured answer about what the user was doing before a crash or hang.

**Lag needs more than logs.** A freeze may require performance traces, process state, and logs together rather than a single text stream.

**Every investigation starts from scratch.** Similar incidents repeatedly require the same presets, evidence collection, and validation steps unless those domain semantics are reusable.

## Install

```bash
pip install tracecite
pip install tracecite-mobile
tracecite-mobile profile init
```

iOS needs `idevicesyslog` (libimobiledevice) plus Xcode CLT. Android needs `adb` from the SDK Platform Tools.

## Usage

```bash
# 1. Start background capture
tracecite-mobile session start --date

# ... reproduce the issue on device ...

# 2. Analyze the failure window
tracecite-mobile seal --from-sessions --json
tracecite-mobile filter --from-sessions --last 2m --preset system-fault --json

# 3. Lift raw lines into structured behavior events
tracecite-mobile behavior summarize --from-sessions --json
```

Live logs keep a 30-minute hot window. Active collectors periodically archive expired records into the hidden internal `.archive/` store. Use `archive list` and `archive pull`; direct folder management is not required.

Maintenance is explicit. `clean analysis --before today` removes only old logs, performance outputs, and unpinned completed analysis runs. Runtime state, locks, active/recovery outputs, and malformed manifests remain fail-closed. Archive evidence is excluded by default; preview it with `--include-archive --dry-run`, and actual archive deletion additionally requires `--yes`.

After one full investigation, the Agent can have:

- structured hits within a bounded time window;
- a user-behavior event stream;
- immutable Evidence references and Coverage;
- a complete run record with frozen input and parameters.

For performance investigations:

```bash
tracecite-mobile capture start --template "Time Profiler"
# ... reproduce the lag ...
tracecite-mobile capture stop
```

Reusable presets remain domain-owned:

```bash
tracecite-mobile filter app.log --preset system-lifecycle --json
tracecite-mobile filter app.log --preset network-http --json
tracecite-mobile filter app.log --preset memory-leak --json
tracecite-mobile filter app.log --preset network-http --grep 'checkout|payment' --json
```

## vs. Dumping Raw Logs to AI

| | Raw logs to AI | TraceCite Mobile |
|---|---|---|
| Collection | Manual export after the fact | Background capture and bounded incident windows |
| Data types | Usually text only | Logs + performance/device capabilities |
| Output | Raw text | Structured results + Evidence/Coverage |
| User actions | Model infers from strings | Domain events can be lifted deterministically |
| Knowledge reuse | Ad hoc | Governed presets and knowledge candidates |
| Reproducibility | Steps vary | Frozen evidence and run manifests |

## Architecture

TraceCite Mobile is a **declarative Extension Protocol v2 package**. It does not define a second general-purpose Runtime and does not require TraceCite Core to import Mobile.

```text
External Agent / CLI / MCP
          |
   TraceCite Runtime
          |
Extension Protocol v2
          |
   tracecite-mobile
   /       |        \
Core     Agent     Scenario
plugins  capabilities capability
```

The package publishes one `TraceCiteExtension` with an `ExtensionManifest` and independently versionable capabilities:

- `core.plugins` registers Mobile formats/segmenters;
- `agent.capability` exposes device, process, session, and app operations through the generic TraceCite Capability Registry;
- `runtime.scenario` supplies Mobile profile/preset/scenario hooks to the generic Runtime.

`ScenarioRuntime` is retained only as a compatibility/internal adapter for callers that still construct it directly. It is not the Extension Protocol v2 boundary.

Importing `tracecite` or `tracecite_mobile` does not mutate Core registries. Explicit hosts install the extension by loading the `tracecite.extensions` entry point or by calling `tracecite_mobile.extension.register()`.

```bash
tracecite extension load
tracecite run scenario.json --load-extensions --runtime mobile
```

The `--runtime mobile` command remains an operational compatibility route: Core adapts the published `ScenarioCapability` to its current scenario executor internally. Domain packages should depend on the declarative capability contract rather than `register_runtime` or `ExtensionAPI`.

The standalone `tracecite-mobile` CLI explicitly installs the same declarative extension before dispatching a command.

<img src="architecture.svg" alt="Mobile architecture: Device, Analysis, Knowledge, Plugin layers on Core" width="100%"/>

`--platform ios|android` switches backends through the same Mobile capability contract. Optional operations fail explicitly when a backend does not declare them.

```bash
tracecite-mobile --platform ios performance profiles --json
tracecite-mobile --platform android performance start --profile frame --json
```

## Agent capabilities and safety

Mobile publishes read, live-source, and live-action capabilities. TraceCite Runtime owns the safety gate; installing Mobile does not itself authorize device reads or mutations.

Examples include:

- `mobile.environment.probe` — read;
- `mobile.devices.list` / `mobile.processes.list` / `mobile.sessions.list` — live source;
- `mobile.sessions.start` / `mobile.sessions.stop` / `mobile.app.launch` — live action and explicit authorization required.

MCP or another Agent host may expose these through the generic Capability Registry without importing Mobile internals.

## Customization

**Presets.** Reuse project/domain filters instead of rewriting regex each time:

```bash
tracecite-mobile grow propose term my-preset "payment failed" "network timeout" \
  --created-by agent-a --case-id run-001 --evidence evidence://run/001#manifest
```

**Scenario files.** Save repeatable investigation recipes as JSON:

```json
{
  "name": "crash-investigation",
  "source": { "type": "session", "device": "ios" },
  "filter": {
    "stages": [
      { "grep": "SIGABRT|SIGSEGV", "scope": { "last": "2m" } },
      { "grep": "backtrace|callstack" }
    ]
  },
  "assert": {
    "rules": [
      { "type": "contains", "match": "SIGABRT", "min": 1 }
    ]
  }
}
```

```bash
tracecite-mobile scenario run crash-investigation.json
```

**Knowledge governance.** Agent findings first enter a physically separate candidate store. An independent case verifies them and a different reviewer authorizes promotion:

```bash
tracecite-mobile grow suggest app.log --preset my-app
tracecite-mobile grow propose learning "Bounded evidence is required" \
  --created-by agent-a --case-id run-001 --evidence evidence://run/001#manifest
tracecite-mobile grow verify kc-ID --case-id run-002 --outcome support \
  --verified-by agent-b --evidence evidence://run/002#manifest
tracecite-mobile grow promote kc-ID --approved-by human-reviewer
tracecite-mobile grow doctor
```

Legacy direct writes are rejected by the Agent CLI. Candidate and trusted knowledge stay separate; a modified trusted file fails its integrity gate until restored through the governed workflow.

## Development status

The `refactor/agent-v2` branch is validated against the matching TraceCite Core branch. The automated matrix covers Python 3.10–3.14 and macOS, builds the distribution, and includes pinned real-log regression using Loghub samples.

## See Also

- **TraceCite Core** — generic Evidence/Runtime, Extension Protocol v2, Context Engine, and Agent integration.
- **TraceCite MCP** — generic MCP projection of TraceCite tools and Extension v2 capabilities.

## License

MIT

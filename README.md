<p align="center">
  <strong>TraceCite Mobile</strong>
</p>

<p align="center">
  <strong>The official Mobile domain extension for TraceCite.</strong>
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
tracecite-mobile filter --from-sessions --snapshot --last 2m --preset system-fault --json

# 3. Lift raw log lines into user behavior events
tracecite-mobile behavior summarize --from-sessions --json
```

After one full investigation, the agent has:

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
```

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

The main TraceCite distribution provides Core evidence primitives, the generic
Runtime, and the versioned Extension API. Mobile stays independent and
registers its device adapters and domain semantics through
`tracecite.extensions`:

```text
External Agent
      |
TraceCite Runtime ---- tracecite-mobile
      |
TraceCite Core
```

Importing either `tracecite` or `tracecite_mobile` does not register Mobile
formats or mutate the Core registry. Use
`tracecite extension load` or `tracecite run ... --load-extensions --runtime mobile`
when the main Runtime should discover the installed Mobile extension.
The standalone `tracecite-mobile` CLI explicitly hosts the same extension
before dispatching a command.

<img src="architecture.svg" alt="Mobile architecture: Device, Analysis, Knowledge, Plugin layers on Core" width="100%"/>

`--platform ios|android` switches backends. All commands work across platforms.

## Customization

**Presets.** Stop writing regex every time. Pre-built keyword sets for common scenarios:

```bash
tracecite-mobile filter app.log --preset system-lifecycle --json
tracecite-mobile preset add --name my-preset --terms "payment failed, network timeout, order error"
```

**Scenario files.** Save investigation steps as JSON — team-sharable, version-controllable:

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

**Knowledge candidates.** Candidate terms discovered during investigations can
be saved and audited locally:

```bash
tracecite-mobile grow auto app.log --preset my-app   # auto-discover from logs
tracecite-mobile grow audit --preset my-app           # prune unused terms
```

All knowledge stays in the `.tracecite/` local directory. Nothing is uploaded.
Candidates are not proven diagnoses: an Agent conclusion is never sufficient to
promote itself into trusted knowledge, and zero matches do not prove absence.

## See Also

- [**TraceCite**](../tracecite-core/) — main distribution: Core, Runtime, and Extension API.

## License

MIT

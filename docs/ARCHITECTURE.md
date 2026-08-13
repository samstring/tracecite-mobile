# TraceCite Mobile architecture

Dependency direction is one-way:

```text
cli -> commands -> device / analysis / shared / platforms -> tracecite-core
```

- `device`: device enumeration, streams, sessions, archives, and performance capture.
- `platforms`: generic iOS and Android backends.
- `analysis`: knowledge, behavior summaries, assertions, reports, and scenarios.
- `shared`: project paths, configuration, command runs, and explicit update checks.
- `plugins`: generic mobile log formats and segmenter registration.
- `analysis.behavior_summary`: generic marker/event aggregation plus a parser-provider
  registry; concrete application protocols are registered by upper layers.

TraceCite Mobile has no company or product layer and contains no interface-inspection
implementation. Upper-layer packages can call its device and analysis APIs without
creating a reverse dependency.

## Platform boundary

The device command domain has one composition root: `commands.device.dispatch_device_command`.
It resolves `get_backend(platform)`, reads `capabilities()` before dispatch, and fails
closed when a capability or method is not declared. iOS, Android, and third-party
backends therefore share the same CLI semantics; the legacy platform handlers remain
compatibility modules only and are not reachable from the public dispatcher.

The public backend contract is expressed as capability protocols and stable models:

- `DeviceCapability`: device discovery and single/multi-device resolution.
- `LogCapability`: foreground stream plus `start_sessions`, `list_sessions`, and
  `stop_sessions`.
- `PerformanceCapability`: profile-based `start_performance`, status, and stop. The
  historical `capture` command and `--template` option are compatibility aliases.
- `ArchiveCapability`: archive listing and time-window fetch. Rotation is optional and
  must be explicitly declared by a backend.
- Optional app/process, diagnostics, crash, and UI capabilities may be composed without
  expanding the minimum backend protocol.

CLI and Agent callers consume `DeviceRef`, `SessionRef`/`SessionStatus`, performance,
archive, and capability models through `tracecite_mobile.device_api` or
`tracecite_mobile.plugin_sdk`; they do not read platform state files or invoke a
platform-specific collector directly. A backend that only implements the pre-capability
legacy methods receives an actionable migration error instead of silent fallback.
This capability-first backend contract is Mobile Plugin API v3; plugins must
explicitly declare `capabilities()` and migrate device operations to the stable
models before updating their version declaration.

## Session manifest lifecycle

Background log sessions treat collector and device logs as mutable live outputs:

- `session start` writes a passed manifest for the session operation and its
  immutable context, but deliberately does not register the still-writing device
  or collector log as an artifact. The result carries a warning that those logs
  are deferred until stop.
- `session stop` sends the bounded stop request, confirms the exact collector
  process has exited without assuming it is a child of the CLI, then requires
  bounded size/mtime stability for both log files before registering their
  hashes in the passed manifest.
- If process identity, exit, file existence, or file stability cannot be
  confirmed within the bounded limits, the command fails closed and must not
  write a passed manifest containing mutable log artifacts.

Project metadata lives in `.tracecite/`; default run artifacts live under
`~/Desktop/TraceCite/analysis/runs/`.

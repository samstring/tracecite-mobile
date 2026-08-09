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

Project metadata lives in `.tracecite/`; default run artifacts live under
`~/Desktop/TraceCite/analysis/runs/`.

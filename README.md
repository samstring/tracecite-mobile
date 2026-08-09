# TraceCite Mobile

TraceCite Mobile collects and analyzes iOS and Android diagnostic evidence for
coding agents. It provides the independent `tracecite-mobile` CLI, device sessions, iOS xctrace,
Android Perfetto, filtering, behavior summaries, knowledge growth,
scenario assertions, and reproducible run manifests.

Behavior summaries are knowledge-driven. The public package matches configured
markers and aggregates events; concrete application log fields and category
formats must be supplied by an upper-layer behavior parser provider.

It intentionally contains only collection and text-analysis capabilities.
Additional product integrations belong to private upper-layer packages. It depends only on
`tracecite-core`. Product and company knowledge belongs in a
separate private upper-layer plugin.

```bash
python -m pip install -e ../tracecite-core
python -m pip install -e .
tracecite-mobile --help
tracecite-mobile profile init
```

Project-local configuration and growing knowledge live under `.tracecite/`.
Default output is written under `~/Desktop/TraceCite/`.

The runnable synthetic example in `examples/` uses `DemoApp` and contains no
production logs or company data.

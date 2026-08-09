# Agent integration

TraceCite Mobile gives coding agents a stable evidence pipeline for iOS and Android:

1. initialize `.tracecite/config.json` with `tracecite-mobile profile init`;
2. collect bounded device logs with `session` or `stream`;
3. collect performance evidence with `capture`;
4. freeze a snapshot and run `filter --json`;
5. summarize the same time window with `behavior summarize --json`;
6. cite only the decisive lines and retain full evidence on disk.

The public package contains no product defaults. Add project terms through
`.tracecite/knowledge.<platform>.json` or a private upper-layer distribution.

Default outputs live under `~/Desktop/TraceCite/`; run manifests preserve source
hashes, parameters, assertions, and artifact paths for agent verification.


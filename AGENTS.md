# TraceCite Mobile boundaries

- This project depends on `tracecite-core`; it must not depend on company plugins.
- Keep device integrations and generic agent workflows here.
- Put all product names, internal URLs, company APM markers, and business scenarios
  in a private extension project.
- Use `.tracecite/` for project-local state and `~/Desktop/TraceCite/` for defaults.
- Tests, starter knowledge, docs, and examples must use synthetic data.


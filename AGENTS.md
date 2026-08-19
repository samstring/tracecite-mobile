# TraceCite Mobile boundaries

- This project depends on the public `tracecite` runtime; it must not depend on company plugins.
- Treat Mobile as an official domain extension and example implementation.
- Keep device integrations and mobile adapters here. Generic runtime workflows live in `tracecite`.
- Put all product names, internal URLs, company APM markers, and business scenarios
  in a private extension project.
- Use `.tracecite/` for project-local state and `~/Documents/TraceCite/` for defaults.
- Tests, starter knowledge, docs, and examples must use synthetic data.

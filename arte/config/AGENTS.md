# Effective configuration and device profiles

Read `../doc/02-architecture.md`, `../doc/06-trading-broker.md`, and
`../doc/08-performance-operations.md` before adding a schema or profile.

- Treat configuration as validated engine input, never UI-owned authority.
  Every run pins an exact effective configuration, code version, execution
  interval, source requirements, and content hash. A strategy number or a
  mutable latest pointer is not enough.
- Strategy requirements explicitly declare instruments or causal admission,
  channels, bars, indicators, Signal Streams, Watchlists, V7, lookbacks,
  reference capabilities, and each computation's own execution interval.
  Missing dependencies block readiness; do not infer defaults from data.
- Keep account cash, allocation, risk, margin, currency, instrument, and mode
  permissions separate per account. No UI selection may grant broker access.
- Laptop and workstation profiles carry explicit approved resource, retention,
  latency, queue, ClickHouse, and runtime-root budgets. Validate the selected
  profile against the host; never silently resize it or fall back to another
  path. Do not invent production numeric settings before measurement.
- Secrets are external references only. Do not store credentials, login
  artifacts, or runtime-generated state in this directory.

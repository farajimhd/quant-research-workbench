# ClickHouse schemas and migrations

Read `../doc/03-events.md`, `../doc/04-data-lifecycle.md`, and
`../doc/05-v7-state.md` before changing DDL or migration definitions.

- ARTE writers target its configured database. Certified yearly compact
  events are permitted read-only historical input. The Rust/ClickHouse
  flatfile importer's write target remains undecided; do not add a yearly
  table writer without explicit approval.
- Use explicit `live_market_ssd` policy for operational tables and verify
  actual part placement. `default` and policies routing to it are forbidden.
- Validate source identity and revision semantics before finalizing event
  primary/sort keys. Do not add a dense ordinal prerequisite. Preserve live
  receipt metadata and historical knowledge separately without redundant
  payload copies.
- Version compact bars, indicators, Boolean products, V7 levels, seeds,
  decisions, commands, fills, logs, and coverage by their actual causal and
  publication contracts. Store only required products with pinned retention.
- Maintain compatible existing `arte.structural_levels_v7`, V7 coverage and
  builder checkpoints, plus completed `arte.market_day_bars_v1` and
  `arte.market_day_technical_v1`; verify source and calculation identities
  before consumer cutover.
- Make migrations restart-safe and independently verifiable. Insert immutable
  objects first, read them back, and publish roots/coverage last. Never treat
  `IF NOT EXISTS` or a setting change as proof of schema or part migration.
- Do not run DDL, migrations, or connected probes before the user extracts
  ARTE into its new repository. Schema source and offline validation are the
  only authorized work in this temporary location.

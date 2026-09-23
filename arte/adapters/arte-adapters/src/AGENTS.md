# External adapters, live intake, and maintenance

Applies to flat adapter modules and child directories. Read
`../../../doc/03-events.md`, `../../../doc/04-data-lifecycle.md`, and
`../../../doc/08-performance-operations.md` for the affected path.
The flat `playback_runtime.rs`, `clickhouse.rs`, `ibkr.rs`,
`rest_acquisition.rs`, and `live_market.rs` modules must
also follow the rules in their like-named child directory's `AGENTS.md`.
Those child files do not apply to the flat parent modules by path alone.

- Live market intake uses WebSocket and stamps receive time before parsing or
  batching. Historical preparation may read certified yearly compact events;
  ARTE-owned Rust/ClickHouse flatfile digestion and REST repair provide other
  source generations. Only the ingestion authority opens raw flatfiles. No
  adapter calls parent code or services at runtime.
- Preserve source identity, exact price/size, SIP and participant precision,
  optional receive time, corrections, and provenance. REST acquisition time
  is not live receive time. Do not invent a dense persisted ordinal.
- Resolve REST/WS overlap by verified source identity; do not duplicate a
  payload or erase what was known at a past live decision. Source sequence
  jumps alone do not certify gaps.
- Maintenance owns compact-source selection, flatfile acquisition, REST gap
  repair, coverage certification, historical
  V7, and retention. Live may report suspected gaps and buffer arrivals, but
  cannot trade through missing required source or derived dependencies.
- The live receiver, market state, calculations, and strategy share an owned
  in-process path. Persistence and observer publication use bounded nonblocking
  queues. Never fetch a feature from ClickHouse or HTTP per live decision.
- Keep required intraday events, bars, indicators, levels, and signals resident
  within an approved budget. Apply per-instrument events in order; parallelize
  independent instruments and bounded computations without starving intake or
  broker/protection work.
- Gate new exposure on measured source age, clock uncertainty, local backlog,
  missing operands, coverage, and broker readiness. Preserve protective and
  reduce-only actions during an incident; report unresolved issues repeatedly
  at a bounded configured cadence.

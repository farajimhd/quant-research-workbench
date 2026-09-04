# Structural Checkpoint Campaign v7

## Purpose

Campaign v7 runs Generic Structure algorithm v16 over the immutable compact
event archive without requiring a reconstructed participant/execution clock.
It preserves the v6 process-sharded, ordinal-chunked, certified, resumable
campaign and the current absolute and ticker-relative score projections.

## Historical structural input authority

Completed historical sessions use the explicit
`historical-sip-condition-v1` approximation:

- `market_sip_compact.events_YYYY` is the only event authority;
- events are consumed in canonical `(ticker, ordinal)` order, which preserves
  SIP availability order;
- trade price and size eligibility are resolved from the canonical condition
  reference (`update_last`, `update_high_low`, and `update_volume`);
- corporate actions are applied from `q_live.market_stock_split_v1` at the
  04:00 America/New_York session boundary;
- no historical execution-clock sidecar, retained flatfile, or ClickHouse
  `file()` fallback is read.

This is intentionally an approximate historical level reference. Conditions
express aggregation eligibility, not a complete delayed-report classifier.
Legacy archive rows retain at most five condition tokens. Unknown decoded
conditions fail eligibility, and this limitation is part of the versioned
`historical-sip-condition-v1` contract rather than being presented as exact
condition completeness.

The condition mapping and its three aggregation decisions are deterministically
hashed. The hash, structural input policy, archive continuity, source plan,
split revision, algorithm version, exact event evidence, checkpoint hash, and
predecessor chain are all part of checkpoint compatibility.

## Live continuation

QMD Live continues a compatible historical seed in SIP availability order.
Native q_live/WebSocket trades retain participant/execution time. A report for
a previously completed one-second bucket remains auditable but cannot revise
current bars, indicators, structure, session extrema, or strategy gates.
Same-second reports remain eligible and condition rules still apply.

The resulting lineage is composite: historical SIP-plus-condition seed followed
by participant-aware live continuation. Historical archive tables are never
altered to imitate the live schema.

## Recovery and resume

The recovery source checkpoint set is immutable. Campaign v7 validates its
checkpoint payload hashes, predecessor chain, algorithm version, authority
start, and exact event evidence before copying a compatible prefix into a new
set. Derived score fields are rebuilt from raw counts.

Older certified SIP-plus-condition campaigns may be explicitly recertified into
the v7 source policy. They are not relabeled in place. Execution-clock campaign
rows remain a different lineage and are not silently mixed into the new set.

Workers then resume from the latest certified target checkpoint and process
only later ordinal ranges. Empty sessions persist the carried state so a later
resume never restarts from the campaign start.

## Operational behavior

- One OS process is assigned per worker shard, up to 80 workers.
- Each worker completes one ticker chronologically before moving to the next.
- SUGP and JUNS are placed first during recovery.
- Splits for the ticker are loaded once in the campaign manifest.
- Event fetches use bounded physical ordinal chunks and bounded response
  batches.
- The terminal reports active, queued, completed, certified, skipped, retried,
  failed, event rate, aggregate worker progress, and event-weighted ETA.
- Ctrl+C writes resumable state and requests graceful worker termination.

Campaign output belongs under `D:\TradingML\runtimes` and must not be committed.

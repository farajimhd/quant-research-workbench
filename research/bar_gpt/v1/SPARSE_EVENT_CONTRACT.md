# BarGPT sparse event-bar contract

## Defect that invalidated the dense-clock shards

The ClickHouse one-second authority stores only active SIP seconds: every row is
created by grouping source events and has `source_event_count > 0`.  The v3
loader nevertheless expanded every 04:00-20:00 session to 57,600 clock rows,
filled absent seconds with zeros, and treated every row as a valid origin and
context token.  Representative 2020 shards contained 30-54% zero-event
origins; an older GOOGL month contained 64%.  Those rows were not batch
padding: `origin_mask` was true and physical-horizon heads and losses consumed
them.  Dense zero rows also occupied attention context and changed positional
geometry.

The v3 catalog is immutable and must never be repaired in place.  Sparse-event
shards use a new root and compatibility contract.

## Bar and context authority

* A model bar exists only when its interval contains at least one market-source
  event.  One-second authority rows must satisfy `source_event_count > 0`.
* A coarser fixed bucket is emitted only when the sum of source events in the
  bucket is positive.  Empty buckets are absent, not zero-valued model tokens.
* Every bar persists `bar_start_us`, `bar_end_us`, and `available_at_us`, with
  `bar_start_us < bar_end_us <= available_at_us`.
* Context is the last configured number of nonempty, completed bars.  The
  counts are 720/360/360/240/240/96/16/8 for 1s through 1h and 90/52/24 for
  1D/1W/1MO.  Actual timestamp gaps remain explicit, so a fixed token count may
  span more wall time for an illiquid instrument.
* The initial intraday fetch range is derived from the configured physical
  spans (currently at most eight trading hours).  The builder traverses prior
  sessions until every fixed nonempty context count is available; the derived
  lookback is not a hardcoded row count.
* Origins are active one-second bars only.  Blocks pack active origins, while
  each origin retains exact causal context and physical timestamps.
* Condition-only sidecar rows do not manufacture market bars.  They remain a
  separate timestamped target authority.

## Physical-horizon targets

For active origin availability time `t0` and horizon `H`, the target window is
`(t0, t0 + H]`.  Horizons remain 5s, 30s, 1m, 5m, 15m, and 1h; they are never
defined as the next N events.

Endpoint price targets are independent bid, ask, and trade log returns.  For
each family, the baseline is the last valid value at or before `t0`, and the
endpoint is the last valid update at or before `t0 + H`.  The endpoint must be
a new family update inside the target window.  A family with no new update is
masked rather than labeled with a zero return.  Midpoint is not a target.

Trade upper/lower excursion uses actual future trade highs/lows.  Realized
volatility uses timestamp-ordered valid price updates.  Trade volume and count
are valid zero values when no trades occur.  Trade/bid/ask/paired-quote
availability and certified condition channels are valid zero values when no
corresponding event occurs.  Horizons extending beyond authoritative session
coverage are masked independently.

Direction supervision is derived independently from valid bid, ask, and trade
returns.  Neutral returns keep the existing neutral-band exclusion.  Direction
loss is backpropagated with the existing unbalanced loss formulation; no class
balancing or balanced loss is introduced.

The intraday autoregressive auxiliary contract is deliberately not redefined in
this migration. Its existing fixed-step continuity mask remains authoritative:
after sparse conversion, an AR transition is supervised only when consecutive
stored bars are exactly one configured timeframe apart. Whether AR should later
predict the next event or the next clock bucket is a separate versioned design
decision.

## Model and shard contract

The model consumes fixed-count nonempty histories with explicit start, end,
availability, elapsed-gap, session-position, timeframe, and pathway
information.  Right-padding is structural batch padding only and must remain
masked.  Causal as-of fusion selects only completed bars.

Sparse shards persist view interval metadata, active origin indices/timestamps,
per-origin as-of indices, timestamp-derived physical targets and masks,
condition provenance counts, and SHA-256-certified sidecars.  Shard discovery
fails closed on contract/hash mismatch.  Existing v3 checkpoints, cursors,
cached block indices, discovery panels, and validation manifests are not
resume-compatible.

## Required certification

Pilot and full builders share one implementation.  The pilot only restricts
ticker/date scope.  Audits must verify at least:

* no origin or context token has zero source events;
* interval ordering, duration, availability, identity, split basis, and causal
  as-of relationships;
* exact configured context counts at every origin;
* no future leakage and timestamp-derived horizon boundaries;
* independent bid/ask/trade return values and masks;
* valid-zero versus unavailable-mask semantics for all targets;
* condition coverage and positives, finite tensors, feature/target ranges,
  duplicate keys, monotonicity, padding masks, block/session continuity, shard
  counts, sidecar hashes, and deterministic ClickHouse reconstruction.

Before a full cohort build, a bounded overfit run must demonstrate that the
model can drive training loss down on the certified pilot shards and that all
three direction heads receive gradients.

# QMD Market-Signal Contracts

QMD owns reusable, causal market signals derived exclusively from canonical
market data. Strategies may consume these events alongside portfolio,
reference, risk, news, or model inputs, but only a strategy owns the final
entry, exit, hold, wait, and order intent.

This separates three contracts:

- **Indicator**: continuously computed descriptive state.
- **Market signal**: a versioned QMD lifecycle event that calls attention to a
  reproducible market condition.
- **Strategy signal**: a strategy-owned decision that may cite one or more QMD
  market-signal ids as evidence.

## Endpoints

```text
GET /snapshot/signals?limit=250
GET /snapshot/signal-events?limit=1000
WS  /stream/signals
GET /signal-catalog
```

`/snapshot/signals` contains only active lifecycles. The event snapshot and
stream contain `triggered`, `updated`, and `resolved` events. The legacy
`/snapshot/scanner-primitives` and `/stream/scanner-primitives` paths are
compatibility aliases over this same payload and are not a separate authority.

## Lifecycle Contract

| Field | Meaning |
|---|---|
| `schema_version`, `signal_version`, `engine_version` | Version the event schema, observation method, and deterministic engine separately. |
| `event_id` | Unique id for one lifecycle event. |
| `signal_id` | Stable id shared by all events in one lifecycle. |
| `signal_key`, `producer` | Reusable detector identity and its authority. |
| `ticker` | Uppercase market symbol. |
| `working_timeframe`, `confirmation_timeframe` | The calculation clock and optional context clock. |
| `observed_at`, `effective_at` | When QMD observed and made the event usable. Both remain causal. |
| `state` | `triggered`, `updated`, or `resolved`. |
| `direction` | `bullish`, `bearish`, or `neutral`. It is not an order side. |
| `score`, `rank_score`, `confidence` | Signed directional strength, comparable causal-surprise rank, and evidence completeness on normalized scales. |
| `trigger_reason`, `resolution_reason` | Human-readable lifecycle evidence. |
| `reference_price`, `invalidation_price`, `expires_at` | Price/time context, never an implicit order instruction. |
| `evidence` | Exact bar and microstructure measurements used at that event. |

The chart must plot `effective_at`, not the opening timestamp of the containing
display candle. A larger chart timeframe changes presentation only; it cannot
delay or backdate the underlying signal.

## Implemented QMD Detectors

| Signal | Working timeframes | Purpose |
|---|---|---|
| `directional_flow_acceleration` | `100ms` event-native | Abrupt buyer- or seller-initiated flow acceleration. |
| `price_volume_expansion` | `1s`, `10s`, `30s`, `1m` closed bars | Concurrent exceptional price and activity expansion. |
| `vwap_transition` | `1s`, `10s`, `30s`, `1m` closed bars | Causal price transition across session VWAP. |
| `liquidity_dislocation` | `100ms` event-native | Exceptional spread widening with displayed-liquidity deterioration. |
| `liquidity_recovery` | `100ms` event-native | Measurable restoration after a liquidity dislocation. |
| `flow_price_divergence` | `100ms` event-native | Exceptional aggressive flow without confirming price acceptance. |

These are observations, not setups. Session-level breaks, structure breaks,
level rejection, opening-range breakout, gap-and-go, and similar strategy
concepts are deliberately absent. Strategies compose the six observations with
QMD structure/level indicators, news, SEC, model, portfolio, and risk inputs.

All six methods are implemented and emitted. The Canvas Scanner joins the
strongest active QMD lifecycle to each security and sorts its Signals preset by
`rank_score`; Signal Stream shows lifecycle events and the live method catalog.
The rank score is produced by QMD from causal, per-symbol/timeframe normalized
surprises rather than recomputed in the UI.

## Persistence And Replay

Raw quotes, trades, and bars remain the reproducible source. Persist lifecycle
events only when downstream audit/replay requires durable signal decisions.
Historical QMD computes the same Rust engine and publishes the same event shape
under `market_signal_events`; it never substitutes frontend heuristics.

Strategies that act on a market signal persist a separate strategy-owned row
with action, direction, score, confidence, strategy revision, source signal
ids, invalidation, reason, and effective time. An active QMD signal alone can
never place an order.

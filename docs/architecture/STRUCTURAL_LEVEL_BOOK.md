# Structural Level Book

## Authority

Generic Structure algorithm v16 is the single deterministic authority for a ticker's structural level book. It consumes canonical, event-time-ordered eligible trades and quotes. Live, Replay, Backtest, Debug, QMD History, and chart presentation must restore or advance the same checkpoint contract; none may recalculate a competing chart-timeframe book.

The current canonical market-event contract identifies a listing by normalized SIP ticker. A future security-master listing identifier may be added only as a versioned identity migration. Symbol changes or reuse must not be silently joined across that boundary.

## Causal construction

1. Eligible trades update a streaming geometry state: tick size, quoted spread, exponentially weighted absolute trade movement, executed volume, and aggressor-classified buy/sell footprint.
2. A directional leg confirms an event-native high or low only after price reverses by an adaptive distance. The distance is bounded by tick, spread, and streaming price movement; it does not depend on the selected chart interval.
3. A confirmed pivot creates or reinforces a nearby active price range. Range geometry is fixed for that role episode. Retests strengthen the same identity; they do not split the range.
4. A tentative penetration keeps the range active. Acceptance requires adaptive distance beyond the far boundary plus trade-count and time-or-volume persistence derived from tick size, range width, streaming absolute movement, spread, and typical trade size. Two small prints or a 100 ms wick cannot accept a break by themselves.
5. An accepted break remains published as `awaiting_retest`, then `retest_contact`, until the retest either confirms a role flip or fails. Pending retests are protected from capacity eviction.
6. Completed 100 ms through 1 week swing calculations are corroborating evidence only. Daily and weekly buckets use the 04:00 New York session boundary. They may strengthen an existing event-native range but cannot originate, delete, or move one.
7. Overlapping same-role episodes consolidate into the oldest causal identity before the bounded 256-track capacity is ranked. Duplicate geometry strengthens one episode instead of occupying independent slots.

## Evidence fields

- `hold_count` and `break_count`: raw causal lifecycle outcomes and the durable scoring authority.
- `hold_rate`: unsmoothed observed hold frequency; zero before the first outcome.
- `hold_probability` and `break_probability`: Beta(2, 2)-smoothed lifecycle frequencies retained for compatibility.
- `hold_quality_score`: one-sided 90% Wilson lower bound over the Beta pseudo-observations, used for comparable conservative ranking rather than as a calibrated forecast.
- `hold_observation_count`, `hold_evidence_reliability`, and `hold_score_revision`: evidence depth and exact derived-score contract needed for audit and presentation.
- `pressure_bias`: signed executed-volume imbalance around the range in `[-1, 1]`; positive is buyer-initiated pressure and negative is seller-initiated pressure.
- `touch_count`, `hold_count`, `break_count`, `role_flip_count`: causal lifecycle counts.
- `buy_volume`, `sell_volume`, `neutral_volume`, `trade_count`: merged executed activity associated with event-native sources. Timeframe corroboration does not duplicate volume.
- `confirmed_at_ms`: first instant at which the episode was tradable without lookahead.

Unified levels expose recorded evidence rather than synthetic importance, confidence, reaction, or reversal scores. Candidate selection and capacity ranking use explicit lifecycle state, bounded hold/touch/flip counts, independent-pivot breadth, causal recency, and distance from the current reference. These ranking facts never rewrite the earlier range geometry or confirmation time.

Every checkpoint load recomputes the derived hold fields from raw counts before
advancement. This repairs older v16 rows non-destructively; no valid checkpoint
needs event replay solely because a derived-score field was missing or stale.

Chart bars use the same retrospective split basis as the checkpoint: every bar before a split boundary is multiplied by the cumulative `split_from / split_to` price factor and its share volume by the inverse factor. Daily and macro responses expose `coverage_status`, `latest_session_date`, and `split_adjusted`; stale daily authority is shown to the operator rather than silently presented as current context.

Persisted checkpoints and cold historical reconstruction share the same configured ticker-book horizon (`QMD_HISTORY_STRUCTURE_BOOK_LOOKBACK_DAYS`, 180 days by default). An algorithm revision or missing checkpoint must not fall back to a shorter seed because that would silently remove older structural levels.

A complete cold reconstruction is stored atomically as a runtime prepared seed keyed by the ticker, full source revision, causal boundary, and calculation revision. Historical service restarts reuse that exact identity; partial, corrupt, or stale prepared seeds are never accepted.

## Daily checkpoints

End-of-day checkpoints are immutable, versioned seeds, not mutable forecasts. Their identity includes ticker, session date, algorithm version, source-plan hash, source-revision token, exact event cursor, and checkpoint payload. A checkpoint is publishable only after the canonical source window is complete and its revision is unchanged across construction.

- Live restores the latest compatible checkpoint and advances from its exact cursor.
- Replay, Backtest, Debug, and charts select the latest compatible checkpoint strictly before their requested `as_of`, then causally warm from that cursor.
- If no compatible checkpoint exists, QMD History rebuilds from canonical history and may persist the completed day as a restart-safe seed.
- A checkpoint from a later session, different algorithm version, incomplete source window, gap-containing plan, or mismatched revision must fail closed.

Full-universe or multi-session population follows the restart and concurrency
rules in `docs/data_contracts/structural_checkpoint_campaign_v6.md`. Campaign
planning reads the lightweight continuity index; it never performs a second
raw-event scan merely to estimate work. Resume validates the prior checkpoint's
complete event and split authority before advancing it.

## Split boundaries

`q_live.market_stock_split_v1` is the corporate-action authority. At 04:00 New York on the split execution date, before the first eligible session event is applied, the checkpoint is transformed exactly once using the split identity recorded in its payload.

- Every price coordinate and price-distance state is multiplied by `split_from / split_to`.
- Executed share quantities, footprints, and rolling trade size are multiplied by `split_to / split_from`; trade and lifecycle counts are unchanged.
- Price-keyed volume bins and footprint offsets are rebuilt in the post-split tick regime rather than merely relabeled.
- Level IDs, confirmation times, evidence scores, probabilities, roles, and lineage remain unchanged.
- The pre-split checkpoint remains immutable. Live immediately persists a split-adjusted successor state; the split-day end-of-day checkpoint and all later checkpoints carry the applied-action lineage.
- Historical reconstruction applies the same transformation at the split-day 04:00 boundary while streaming. Loading a prior checkpoint for Replay, Backtest, Debug, or charts applies every unapplied split through the requested boundary before session events are evaluated.

This is a reference-data normalization, not a price signal. A missing, invalid, or contradictory split term fails the checkpoint transition instead of mixing pre- and post-split price regimes.

## Strategy use

After Early Squeeze Move and liquidity admission, the long-momentum strategy may use the book as follows:

- Qualify entry, target, and protective-support levels with `hold_quality_score >= 0.70`. Missing or malformed conservative quality fails qualification; `hold_probability` remains compatibility and audit evidence rather than a substitute.
- Build the three-level entry frontier from the prior completed 1-second book. When a qualified range covers the session high, include that range and the next two qualified ranges below it. When the high is an uncovered long wick, begin with the highest qualified range below it and take the next two. The raw session-high price is never synthesized into a level.
- Enter only after VWAP and 1-second MACD confirmation plus causal acceptance above one of those selected resistance ranges.
- Place protection at the strategy-selected qualified support or flipped resistance, subject to the strategy risk cap. OMS may supply missing protection defaults but cannot replace protection explicitly defined by the strategy.
- Allocate integer-share profit tranches across reachable resistance ranges. Orders are OCA siblings within the position lifecycle; fills resize remaining protection rather than creating independent positions.
- Continue or reenter only after acceptance above a broken range and a successful support retest.
- Exit remaining quantity on rejected breakout, accepted loss of flipped support, strong reversal evidence, inability to progress to the next structural range, or the late MACD fallback.

Every action, including `WAIT`, must state the unmet or triggering facts and their observed values.

# BarGPT sparse event and OHLC target contract (v5)

## Defects this contract replaces

The ClickHouse one-second authority stores active SIP seconds only. Every row
is created from at least one source event and has `source_event_count > 0`.
The retired v3 loader densified each 04:00-20:00 session, then used fabricated
zero-event seconds as context and valid origins. This wasted training compute,
distorted attention geometry, and trained physical heads at times when there
was no prediction event.

The first sparse-event contract removed those clock-dense rows, but its target
contract still lost material price geometry:

* physical targets exposed one close return per trade/bid/ask family plus
  clamped trade excursion magnitudes;
* autoregressive targets blended midpoint and trade into one endpoint and
  clamped high/low excursions;
* direction supervision covered only three physical close returns and one
  blended autoregressive return;
* the model-ready low-from-open input was stored as a positive magnitude.

The old shard roots are immutable. Contract-v5 shards are rebuilt under
`offline_shards_v5`; old shards, checkpoints, cursors, discovery panels, and
validation manifests are not compatible or repairable in place.

## Bar, context, and condition authority

* A one-second model bar exists only when `source_event_count > 0`.
* A coarser fixed bucket exists only when its aggregated source-event count is
  positive. Empty buckets are absent rather than zero-valued tokens.
* Every stored bar has explicit `bar_start_us`, `bar_end_us`, and
  `available_at_us`, satisfying
  `bar_start_us < bar_end_us <= available_at_us`.
* Each origin is an active one-second bar. Context is the latest configured
  fixed number of nonempty, completed bars: 720/360/360/240/240/96/16/8 for
  1s through 1h and 90/52/24 for 1D/1W/1MO.
* Timestamp gaps remain explicit. Fixed token counts therefore span variable
  physical time for less-active instruments.
* The builder derives its warm-up requirement from the configured timeframe
  durations and context counts, traverses prior sessions, performs vectorized
  fixed-bucket aggregation, and slices the latest configured count for each
  origin block. It does not advance through the month one second at a time.
* Condition rows are a separate timestamped target authority. They never
  manufacture origins or context bars. Zero condition values are valid
  negative labels when the condition authority is certified; a condition head
  with no certified positive evidence remains inactive under training preflight.

## Signed model-ready price inputs

Each trade, bid, and ask input family preserves the following stationary price
geometry:

* close return from the prior valid family close;
* open gap from the prior valid family close;
* signed high return from the current bar open;
* signed low return from the current bar open.

High and low channels are not clamped magnitudes. A normal bar therefore has a
nonnegative high-from-open return and a nonpositive low-from-open return.

## Physical-horizon targets

For origin availability time `t0` and configured horizon `H`, the authoritative
window is `(t0, t0 + H]`. Horizons remain physical time, not event counts.
For each of trade, bid, and ask:

* baseline: the last valid family close at or before `t0`;
* open: the first direct family update in the window;
* high: the maximum direct family price in the window;
* low: the minimum direct family price in the window;
* close: the last direct family update at or before `t0 + H`.

The 12 signed price targets are ordered as:

```text
trade_open_return, trade_high_return, trade_low_return, trade_close_return,
bid_open_return,   bid_high_return,   bid_low_return,   bid_close_return,
ask_open_return,   ask_high_return,   ask_low_return,   ask_close_return
```

Every return is `log(target_price / baseline_close)` and is transformed for the
model as `asinh(log_return * 100)`. No midpoint target, sign clamp, or source
family substitution is permitted. If a family has no direct update, has no
valid baseline, or has invalid OHLC geometry, all four family targets are
masked. A horizon beyond authoritative coverage is masked independently.

The remaining physical targets are trade realized volatility, log trade
volume, log trade count, trade/bid/ask/paired-quote availability, and the four
certified condition channels. This yields 15 continuous and 8 binary targets,
23 total.

## Autoregressive targets

Each intraday view predicts the next stored nonempty completed bar, including
across a wall-clock gap. It does not require the next active bar to be exactly
one timeframe step later. The same 12 family OHLC returns are based on the
current last-valid family close and the next bar's direct family OHLC values.
A missing next-bar family masks all four targets for that family.

Autoregressive auxiliary targets additionally contain next-bar log trade
volume, log trade count, and four availability flags. There are 14 continuous
and 4 binary autoregressive targets, 18 total. Physical condition windows and
realized volatility are not fabricated as autoregressive channels.

## Direction learning and metrics

Every one of the 12 OHLC returns has its own physical direction logit and its
own autoregressive direction logit per view. Direction labels use the sign of
the corresponding transformed return outside the existing +/-1 bp neutral
band. Neutral examples are excluded from direction loss.

The existing unbalanced binary cross-entropy, direction loss weight, and
overall loss composition remain unchanged. No class weighting or balanced loss
is added. Direction loss is averaged over all valid direction elements, so
expanding from the old heads does not multiply its scale by 12.

Validation reports MAE, zero-baseline skill, quantile coverage/calibration,
direction accuracy, balanced accuracy, MCC, neutral fraction, ranking, and
confidence separately for every family/field target. Aggregate family and AR
summaries remain available for dashboard and model-discovery ranking. W&B
first-level groups remain bounded to at most 16 metrics.

## Builder, shard, and certification contract

Contract-v5 uses shard contract 5 and loader stream contract 7. Shards persist
view interval metadata, nonempty context, active origins, per-origin causal
as-of indices, 18-channel autoregressive tensors, 23-channel physical tensors,
condition provenance, and SHA-256-certified sidecars. Discovery fails closed
on contract or compatibility-hash mismatch.

The pilot and full builders invoke the same builder implementation; the pilot
only bounds ticker/month scope. Automated audit must verify:

* no zero-event origin or intraday context token;
* interval ordering, availability, exact configured context, block continuity,
  split basis, and causal as-of relationships;
* signed input high/low geometry and physical/AR OHLC ordering;
* same-family future updates, valid-zero versus masked semantics, finite values,
  target ranges, condition coverage, sidecar hashes, and deterministic
  ClickHouse reconstruction;
* target and direction support by family, OHLC field, horizon, and AR view.

Before the full cohort build, the v5 overfit runner must pass independent
return-skill and direction gates for all 12 physical tasks and demonstrate each
of the 12 autoregressive direction tasks on the configured minimum number of
eligible views.

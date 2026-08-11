# BarGPT embedded-condition sparse event and OHLC target contract (v7)

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

The v5 pilot exposed a further semantic defect: every structurally positive
trade and paired quote was accepted without applying the database condition
category's field-update permissions. AAPL's 2019-01-03 average-price/Form-T
trade (177.89 versus a market near 142.60) contaminated context and targets,
and an implausibly wide quote contaminated quote extrema. Historical trade
correction code 12 (the original incorrect data) was also retained upstream.

The old shard roots are immutable. Contract-v7 shards are rebuilt under
`offline_shards_v7`; old shards, checkpoints, cursors, discovery panels, and
validation manifests are not compatible or repairable in place.

## Bar, context, and condition authority

* A one-second model bar exists only when `origin_event_count > 0`.
  `origin_eligible=1` and `context_eligible=1` are stored explicitly and are
  required by the loader. `source_event_count` counts retained eligible events,
  including volume-only contributions in a second that already has an origin.
* Trade conditions are combined fail-closed. An event may update each stored
  sufficient statistic only when every condition token authorizes that field.
  `update_high_low`, `update_last`, and `update_volume` therefore produce
  independent high/low, close, and volume/count masks. A trade can create an
  origin only when it may update high/low or last; a volume-only trade can add
  volume to an already-active second but cannot create a bar or origin.
* Correction 00 and corrected-data 01 remain eligible upstream. Correction
  codes 07, 08, 10, 11, and 12 are excluded before compact event encoding;
  unknown condition/correction metadata fails closed. Because the compact
  event schema does not retain the original correction field, the 1s preflight
  also requires database source-day provenance containing the complete
  `07,08,10,11,12` exclusion policy and refuses an older event authority.
* A quote is eligible only as a complete positive bid/ask pair with bid <= ask,
  no Invalid/Closed/MarketMakerQuotesClosed/NonFirm/Cancel/Unknown/Crossed
  modifier, and midpoint-relative spread no wider than 1,000 bps. The bound is
  configurable and applied once in vectorized ClickHouse SQL.
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
* Halt/pause, resume, news-risk, and LULD state are classified from the retained
  events in the same vectorized SQL scan and stored as per-second counts. They
  never manufacture a row or an origin. Coarser rollups sum these counts, and
  training reads both inputs and condition targets from the certified 1s
  authority; neither the 1s build nor shard build reads a second condition-bar
  table.

The authoritative trade-condition categories observed in
`event_condition_token_reference` are:

| Field permission | SIP condition codes |
|---|---|
| high/low + last + volume (origin eligible) | 00, 01, 03, 04, 06, 08, 09, 11, 14, 17, 18, 19, 23, 24, 25, 27, 28, 30, 34, 35, 36, 41, 55 |
| high/low + volume, no last (origin eligible) | 05, 10, 22, 32, 33 |
| high/low + last, no volume (origin eligible) | 38 |
| volume only (never creates an origin) | 02, 07, 12, 13, 20, 21, 26, 29, 37, 39-54 |
| no price/volume contribution | 15, 16, 56, 59 |

These lists are not duplicated as a Python hot-path lookup. The builders load
the database flags into constant ClickHouse arrays once per query and use
`arrayAll`, preserving fail-closed multi-condition semantics without row loops.
Trade condition 12 in this table is a different source field and namespace
from historical trade correction 12; the former is volume-only, while the
latter is excluded upstream as original incorrect data.

## Model input tensor contract

Every view is a `float32` tensor with shape `[batch, tokens, 54]`. Intraday
views are `1s`, `5s`, `10s`, `30s`, `1m`, `5m`, `30m`, and `1h`; calendar
views are `1D`, `1W`, and `1MO`. The same feature order and preprocessing are
used in every view. Raw price and size aggregates are first expressed in the
split-adjusted share basis known at the example anchor. Causal projection then
uses only the current and earlier completed bars in that view.

`asinh` is used instead of clipping: it is approximately linear near zero and
logarithmic in the tails. A stored return value `z = asinh(log_return * 100)`
can be converted to log-return basis points as `sinh(z) * 100`.

| Ordered input | Model type | Source and preprocessing |
|---|---|---|
| `trade_present` | `float32`, 0/1 | One when the bar contains an eligible trade high/low or last update; volume-only events do not set it. |
| `trade_close_return` | `float32` | `asinh(log(trade_close / prior_valid_trade_close) * 100)`; zero when no causal prior trade close is available. |
| `trade_open_gap` | `float32` | `asinh(log(trade_open / prior_valid_trade_close) * 100)`. |
| `trade_high_from_open_return` | `float32` | Signed `asinh(log(trade_high / trade_open) * 100)`; not converted to an absolute magnitude. |
| `trade_low_from_open_return` | `float32` | Signed `asinh(log(trade_low / trade_open) * 100)`; normally nonpositive and not clamped. |
| `trade_log_size` | `float32` | `log1p(trade_size_sum)` in the anchor-adjusted share basis. |
| `trade_log_count` | `float32` | `log1p(trade_event_count)`. |
| `trade_vwap_deviation_bps` | `float32` | Trade VWAP is `eligible_price_size_sum / eligible_price_size`; volume-only conditions are excluded from both terms. Feature is `asinh(((VWAP / close) - 1) * 1000)`, equivalently `asinh(VWAP_deviation_bps / 10)`. |
| `trade_size_cv` | `float32` | `asinh(std(event_size) / mean(event_size))`; zero when fewer than two updates or size support is unavailable. |
| `bid_present` | `float32`, 0/1 | One when the bar contains a valid bid-family update; otherwise zero. |
| `bid_close_return` | `float32` | `asinh(log(bid_close / prior_valid_bid_close) * 100)`. |
| `bid_open_gap` | `float32` | `asinh(log(bid_open / prior_valid_bid_close) * 100)`. |
| `bid_high_from_open_return` | `float32` | Signed `asinh(log(bid_high / bid_open) * 100)`. |
| `bid_low_from_open_return` | `float32` | Signed `asinh(log(bid_low / bid_open) * 100)`. |
| `bid_log_size` | `float32` | `log1p(bid_size_sum)` in the anchor-adjusted share basis. |
| `bid_vwap_deviation_bps` | `float32` | `asinh((((bid_price_size_sum / bid_size_sum) / bid_close) - 1) * 1000)`. |
| `bid_size_cv` | `float32` | `asinh(std(bid_size) / mean(bid_size))`; zero without sufficient support. |
| `ask_present` | `float32`, 0/1 | One when the bar contains a valid ask-family update; otherwise zero. |
| `ask_close_return` | `float32` | `asinh(log(ask_close / prior_valid_ask_close) * 100)`. |
| `ask_open_gap` | `float32` | `asinh(log(ask_open / prior_valid_ask_close) * 100)`. |
| `ask_high_from_open_return` | `float32` | Signed `asinh(log(ask_high / ask_open) * 100)`. |
| `ask_low_from_open_return` | `float32` | Signed `asinh(log(ask_low / ask_open) * 100)`. |
| `ask_log_size` | `float32` | `log1p(ask_size_sum)` in the anchor-adjusted share basis. |
| `ask_vwap_deviation_bps` | `float32` | `asinh((((ask_price_size_sum / ask_size_sum) / ask_close) - 1) * 1000)`. |
| `ask_size_cv` | `float32` | `asinh(std(ask_size) / mean(ask_size))`; zero without sufficient support. |
| `quote_pair_present` | `float32`, 0/1 | One when a valid paired bid/ask observation exists in the bar. |
| `log_quote_pair_count` | `float32` | `log1p(quote_pair_count)`. |
| `spread_close_bps` | `float32` | `asinh((spread_close / midpoint_close) * 1000)`, equivalently `asinh(spread_bps / 10)`. |
| `spread_mean_bps` | `float32` | Mean spread from additive sums, then `asinh((mean_spread / midpoint_close) * 1000)`. |
| `spread_std_bps` | `float32` | Spread standard deviation from first and second moments, divided by midpoint and transformed with `asinh(x * 1000)`. |
| `spread_range_bps` | `float32` | `asinh(((spread_high - spread_low) / midpoint_close) * 1000)`. |
| `midpoint_return` | `float32` | `asinh(log(midpoint_close / prior_valid_midpoint_close) * 100)`. Midpoint is an input only, never a price target. |
| `microprice_lean_close_bps` | `float32` | `asinh(((microprice_close - midpoint_close) / midpoint_close) * 1000)`. |
| `microprice_lean_mean_bps` | `float32` | Mean microprice minus closing midpoint, normalized by closing midpoint and transformed with `asinh(x * 1000)`. |
| `microprice_lean_std_bps` | `float32` | Microprice standard deviation divided by closing midpoint and transformed with `asinh(x * 1000)`. |
| `queue_imbalance_close` | `float32`, `[-1,1]` | Closing queue imbalance, clipped only to its mathematical range. |
| `queue_imbalance_mean` | `float32`, `[-1,1]` | Mean queue imbalance from additive sums, clipped to `[-1,1]`. |
| `queue_imbalance_std` | `float32`, `[0,1]` | Standard deviation from first and second moments, clipped to `[0,1]`. |
| `locked_quote_fraction` | `float32`, `[0,1]` | `locked_quote_count / max(quote_pair_count, 1)`. |
| `crossed_quote_fraction` | `float32`, `[0,1]` | `crossed_quote_count / max(quote_pair_count, 1)`. |
| `log_condition_count` | `float32` | `log1p(condition_nonzero_count)` from source-event condition codes represented in the bar. This is distinct from the four future condition targets. |
| `halt_pause_present` | `float32`, 0/1 | One when `condition_halt_pause_count > 0` in the completed bar. |
| `log_halt_pause_count` | `float32` | `log1p(condition_halt_pause_count)`; coarser bars sum active one-second counts. |
| `resume_present` | `float32`, 0/1 | One when `condition_resume_count > 0`. |
| `log_resume_count` | `float32` | `log1p(condition_resume_count)`. |
| `news_risk_present` | `float32`, 0/1 | One when `condition_news_risk_count > 0`. |
| `log_news_risk_count` | `float32` | `log1p(condition_news_risk_count)`. |
| `luld_limit_state_present` | `float32`, 0/1 | One when `condition_luld_limit_state_count > 0`. |
| `log_luld_limit_state_count` | `float32` | `log1p(condition_luld_limit_state_count)`. |
| `log_source_event_count` | `float32` | `log1p(source_event_count)`; every stored intraday token has a strictly positive raw count. |
| `log_elapsed_wall_ratio` | `float32` | `log1p((bar_start - prior_bar_start) / configured_timeframe_duration)`; preserves physical gaps in sparse token sequences. |
| `sequence_boundary` | `float32`, 0/1 | One when elapsed wall time exceeds `1.5` configured timeframe durations. |
| `session_progress_sin` | `float32`, `[-1,1]` | Sine of New York session progress over the clamped 04:00-20:00 exchange clock. |
| `session_progress_cos` | `float32`, `[-1,1]` | Cosine of the same New York session-progress phase. |

The model additionally receives the following structural inputs. They are not
feature channels and are never inferred from padded values.

| Structural input | Type | Meaning |
|---|---|---|
| `origin_indices` | `int64 [B,N]` | Position of every active 1s origin inside its input sequence. |
| `origin_mask` | `bool [B,N]` | Distinguishes real origins from batch padding. |
| `asof_indices[view]` | `int64 [B,N]` | Last token in each coarser/calendar view whose `available_at_us <= origin_available_at_us`; `-1` means unavailable calendar history. |
| `timeframe_us` / calendar pathway identity | integer IDs embedded as `float32` model vectors | Identifies duration and intraday versus calendar pathway. |
| sequence position | RoPE position, not a stored scalar channel | Supplies ordered causal token position independently within each view. |
| `horizon_ids` | `int64 [H]` | Selects learned physical-horizon queries for 5s, 30s, 60s, 300s, 900s, and 3600s. |

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
family substitution is permitted. Each field has an independent mask. A trade
open/high/low/close target is valid only when that field has eligible evidence
in the window and a causal eligible-close baseline exists. Bid/ask fields share
the paired-quote mask. A horizon beyond authoritative coverage is masked
independently.

The remaining physical targets are trade realized volatility, log trade
volume, log trade count, trade/bid/ask/paired-quote availability, and the four
certified condition channels. This yields 15 continuous and 8 binary targets,
23 total.

### Physical target table

Physical values have shape `[B, N_origins, 6_horizons, 23]` and dtype
`float32`; their authoritative validity tensor has the same shape and dtype
`bool`. Masked values are stored as zero but never contribute to loss or
metrics.

| Ordered target | Type | Definition and preprocessing |
|---|---|---|
| `trade_open_return` | continuous `float32` | `asinh(log(first_future_trade_open / origin_trade_close) * 100)`. |
| `trade_high_return` | continuous `float32` | `asinh(log(max_future_trade_high / origin_trade_close) * 100)`. |
| `trade_low_return` | continuous `float32` | `asinh(log(min_future_trade_low / origin_trade_close) * 100)`. |
| `trade_close_return` | continuous `float32` | `asinh(log(last_future_trade_close / origin_trade_close) * 100)`. |
| `bid_open_return` | continuous `float32` | `asinh(log(first_future_bid_open / origin_bid_close) * 100)`. |
| `bid_high_return` | continuous `float32` | `asinh(log(max_future_bid_high / origin_bid_close) * 100)`. |
| `bid_low_return` | continuous `float32` | `asinh(log(min_future_bid_low / origin_bid_close) * 100)`. |
| `bid_close_return` | continuous `float32` | `asinh(log(last_future_bid_close / origin_bid_close) * 100)`. |
| `ask_open_return` | continuous `float32` | `asinh(log(first_future_ask_open / origin_ask_close) * 100)`. |
| `ask_high_return` | continuous `float32` | `asinh(log(max_future_ask_high / origin_ask_close) * 100)`. |
| `ask_low_return` | continuous `float32` | `asinh(log(min_future_ask_low / origin_ask_close) * 100)`. |
| `ask_close_return` | continuous `float32` | `asinh(log(last_future_ask_close / origin_ask_close) * 100)`. |
| `trade_realized_volatility` | continuous `float32` | `asinh(sqrt(sum(squared consecutive trade log returns in window)) * 100)`; valid only with trade-family window support. |
| `log_trade_volume` | continuous `float32` | `log1p(sum future trade size)` expressed in the origin's split-adjusted share basis. |
| `log_trade_count` | continuous `float32` | `log1p(sum future trade_event_count)`. |
| `trade_available` | binary value in `float32` | One when the physical window contains at least one direct trade update. |
| `bid_available` | binary value in `float32` | One when the physical window contains at least one direct bid update. |
| `ask_available` | binary value in `float32` | One when the physical window contains at least one direct ask update. |
| `quote_pair_available` | binary value in `float32` | One when the physical window contains at least one paired-quote observation. |
| `halt_pause_within_horizon` | binary value in `float32` | Maximum certified halt/pause condition flag in `(t0, t0+H]`; masked without condition authority. |
| `resume_within_horizon` | binary value in `float32` | Maximum certified resume flag in `(t0, t0+H]`; masked without condition authority. |
| `news_risk_within_horizon` | binary value in `float32` | Maximum certified news-risk condition flag in `(t0, t0+H]`; masked without condition authority. |
| `luld_limit_state_within_horizon` | binary value in `float32` | Maximum certified LULD-limit-state flag in `(t0, t0+H]`; masked without condition authority. |

## Autoregressive targets

Each intraday view predicts the next stored nonempty completed bar, including
across a wall-clock gap. It does not require the next active bar to be exactly
one timeframe step later. The same 12 family OHLC returns are based on the
current last-valid family close and the next bar's direct family OHLC values.
A missing or condition-ineligible next-bar field masks that field independently.

Autoregressive auxiliary targets additionally contain next-bar log trade
volume, log trade count, and four availability flags. There are 14 continuous
and 4 binary autoregressive targets, 18 total. Physical condition windows and
realized volatility are not fabricated as autoregressive channels.

### Autoregressive target table

Each intraday view stores `float32 [B, T-1, 18]` values and a parallel `bool`
mask. In the table, `family_base` is the last valid family close at the current
token and `next_*` is the next stored nonempty completed bar in that view.

| Ordered target | Type | Definition and preprocessing |
|---|---|---|
| `trade_open_return` | continuous `float32` | `asinh(log(next_trade_open / trade_base) * 100)`. |
| `trade_high_return` | continuous `float32` | `asinh(log(next_trade_high / trade_base) * 100)`. |
| `trade_low_return` | continuous `float32` | `asinh(log(next_trade_low / trade_base) * 100)`. |
| `trade_close_return` | continuous `float32` | `asinh(log(next_trade_close / trade_base) * 100)`. |
| `bid_open_return` | continuous `float32` | `asinh(log(next_bid_open / bid_base) * 100)`. |
| `bid_high_return` | continuous `float32` | `asinh(log(next_bid_high / bid_base) * 100)`. |
| `bid_low_return` | continuous `float32` | `asinh(log(next_bid_low / bid_base) * 100)`. |
| `bid_close_return` | continuous `float32` | `asinh(log(next_bid_close / bid_base) * 100)`. |
| `ask_open_return` | continuous `float32` | `asinh(log(next_ask_open / ask_base) * 100)`. |
| `ask_high_return` | continuous `float32` | `asinh(log(next_ask_high / ask_base) * 100)`. |
| `ask_low_return` | continuous `float32` | `asinh(log(next_ask_low / ask_base) * 100)`. |
| `ask_close_return` | continuous `float32` | `asinh(log(next_ask_close / ask_base) * 100)`. |
| `log_trade_volume` | continuous `float32` | `log1p(next_bar_trade_size_sum)`. |
| `log_trade_count` | continuous `float32` | `log1p(next_bar_trade_event_count)`. |
| `trade_available` | binary value in `float32` | One when the next sparse bar contains a direct trade update. |
| `bid_available` | binary value in `float32` | One when the next sparse bar contains a direct bid update. |
| `ask_available` | binary value in `float32` | One when the next sparse bar contains a direct ask update. |
| `quote_pair_available` | binary value in `float32` | One when the next sparse bar contains a paired quote. |

## Direction learning and metrics

Every one of the 12 OHLC returns has its own physical direction logit and its
own autoregressive direction logit per view. Direction labels use the sign of
the corresponding transformed return outside the existing +/-1 bp neutral
band. Neutral examples are excluded from direction loss.

The existing unbalanced binary cross-entropy, direction loss weight, and
overall loss composition remain unchanged. No class weighting or balanced loss
is added. Direction loss is averaged over all valid direction elements, so
expanding from the old heads does not multiply its scale by 12.

Validation reports direct MAE in basis points, persistence-baseline MAE,
quantile coverage/calibration, direction accuracy, balanced accuracy, MCC,
neutral fraction, ranking, and confidence separately for every family/field
target. The zero-baseline `skill_vs_zero` ratio is removed because it becomes
unstable and operationally misleading when the target's absolute return is
close to zero. It was never part of the model, loss, backpropagation, or
checkpoint contract. Aggregate family and AR summaries remain available for
dashboard and model-discovery ranking. W&B first-level groups remain bounded
to at most 16 metrics.

## Builder, shard, and certification contract

Contract-v7 uses shard contract 7 and loader stream contract 9. Shards persist
view interval metadata, nonempty context, active origins, per-origin causal
as-of indices, 18-channel autoregressive tensors, 23-channel physical tensors,
condition provenance, and SHA-256-certified sidecars. Discovery fails closed
on contract or compatibility-hash mismatch.

The pilot and full builders invoke the same builder implementation; the pilot
only bounds ticker/month scope. Automated audit must verify:

* no zero-event origin or intraday context token;
* no correction-12 or condition-ineligible field contribution in reconstructed
  ClickHouse samples;
* no implausible quote/condition/split return outlier above the configured
  fail-closed audit threshold;
* a dedicated AAPL 2020-08-31 split-boundary pilot proving causal adjustment;
* interval ordering, availability, exact configured context, block continuity,
  split basis, and causal as-of relationships;
* signed input high/low geometry and physical/AR OHLC ordering;
* same-family future updates, valid-zero versus masked semantics, finite values,
  target ranges, condition coverage, sidecar hashes, and deterministic
  ClickHouse reconstruction;
* target and direction support by family, OHLC field, horizon, and AR view.

Before the full cohort build, the v7 overfit runner must pass the total-loss
improvement gate and direction gates for every physical task with sufficient
two-class support, and demonstrate each of the 12 autoregressive direction
tasks on the configured minimum number of eligible views. It reports direct
per-head physical MAE in basis points without imposing an unapproved absolute
MAE threshold. Insufficiently supported physical direction tasks are reported
as ineligible rather than failed.

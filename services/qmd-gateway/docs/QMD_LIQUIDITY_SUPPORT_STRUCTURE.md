# QMD Liquidity, Support, and Structure Guide

This is the calculation and service-contract authority for QMD liquidity,
support/resistance, market structure, and structural-pressure indicators. It
documents indicator schema version 15 and generic-structure algorithm version
3. The application rendering contract is documented in
[`frontend/src/app/QMD_LIQUIDITY_SUPPORT_STRUCTURE.md`](../../../frontend/src/app/QMD_LIQUIDITY_SUPPORT_STRUCTURE.md).

## Scope and Interpretation Boundary

QMD consumes consolidated Level-1 NBBO quotes and eligible trade prints. It
does **not** observe venue depth beyond the best quote, hidden orders, queue
priority, or an official exchange order book. Therefore:

- displayed-liquidity indicators describe the best bid and offer only;
- support and resistance are evidence-backed zones, not guaranteed price
  floors or ceilings;
- `up_probability` is a deterministic likelihood-shaped score, not a
  statistically calibrated probability; and
- `buy`, `sell`, bullish, and bearish values are signals, never order commands.

All event-native structure is causal. A pivot has an origin time and a later
confirmation time. Strategies must use the confirmation time.

## Architecture at a Glance

QMD exposes four related layers:

1. **Interval microstructure** measures quote and trade flow inside one closed
   bar.
2. **Session-anchored flow** accumulates raw OFI and signed trade volume from
   04:00 New York time.
3. **Generic structure** derives timeframe-independent pivots, breaks, and
   support/resistance zones from the ordered event stream.
4. **Structural pressure** compresses all currently valid zones into support,
   resistance, directional bias, and confidence fields.

The same ordered event authority feeds live and historical calculation. A
100 ms interval is the base streaming unit. Higher timeframes merge raw counts,
volumes, transition numerators, exposures, and returns, then calculate one
result for the completed bar. Ratios are not averaged across child bars.

## 1. Interval Microstructure and Liquidity

### Trade classification

An eligible print at the current ask is classified as buyer initiated; a print
at the current bid is seller initiated. Prints that cannot be classified are
retained in eligible totals but excluded from directional count and volume
ratios.

| Field | Range | Calculation | Reading |
|---|---:|---|---|
| `microstructure_transaction_imbalance` | -1 to +1 | `(buy trade count - sell trade count) / classified trade count` | Positive means more buyer-initiated prints; negative means more seller-initiated prints. It ignores size. |
| `microstructure_signed_volume_delta` | shares | `buy volume - sell volume` | Raw aggressive-volume difference for the bar. |
| `microstructure_signed_volume_imbalance` | -1 to +1 | `(buy volume - sell volume) / (buy volume + sell volume)` | Positive means aggressive buy volume dominates. A few large prints may dominate. |
| `microstructure_aggressor_persistence` | -1 to +1 | Average aggressor sign, with at-ask `+1` and at-bid `-1` | Values near either extreme show one-sided execution. One-sided flow without price response may be absorption. |
| `microstructure_midpoint_return_bps` | basis points | First-to-last NBBO midpoint log return in the bar | Realized quote response, not a forecast. |
| `microstructure_trade_return_bps` | basis points | First-to-last eligible-trade log return in the bar | Realized execution-price response. |

The supporting count and volume fields are
`microstructure_buy_trade_count`, `microstructure_sell_trade_count`,
`microstructure_classified_trade_count`,
`microstructure_eligible_trade_count`, `microstructure_buy_volume`, and
`microstructure_sell_volume`.

### Level-1 order-flow imbalance (OFI)

For each ordered quote transition, raw OFI is the sum of these terms:

```text
new bid >= old bid  -> +new bid size
new bid <= old bid  -> -old bid size
new ask <= old ask  -> -new ask size
new ask >= old ask  -> +old ask size
```

At an unchanged price, the add and remove terms reduce to the size change.
Depth exposure is half the sum of old and new bid and ask sizes. QMD exposes:

- `microstructure_level1_ofi_delta`: the raw OFI numerator for the bar;
- `microstructure_level1_ofi`: raw OFI divided by accumulated depth exposure;
  and
- `microstructure_cumulative_level1_ofi`: the session-anchored raw total.

Positive OFI means bid improvement/replenishment or ask withdrawal dominates.
Negative OFI means bid withdrawal or ask improvement/replenishment dominates.
It remains displayed intent and can be cancelled.

### Queue imbalance and microprice

`microstructure_queue_imbalance` averages
`(bid size - ask size) / (bid size + ask size)` across valid quote samples.
Positive values mean more displayed bid size.

For each quote, microprice is:

```text
(ask * bid size + bid * ask size) / (bid size + ask size)
```

`microstructure_microprice_lean` averages
`(microprice - midpoint) / (spread / 2)`, clamped to `[-1, +1]`. Positive lean
means the ask queue is relatively thinner and upward repricing may be easier.

### Arrival intensity and resiliency

`microstructure_arrival_intensity_imbalance` is the average sign of directional
quote and classified-trade arrivals. Bid replenishment and ask depletion vote
positive; bid depletion and ask replenishment vote negative. At-ask trades vote
positive and at-bid trades vote negative. `microstructure_arrival_rate_per_second`
is the total qualifying arrival count divided by interval duration.

For each side, recovery is `replenishment / (replenishment + depletion)`.
`microstructure_resiliency` is bid recovery minus ask recovery, clamped to
`[-1, +1]`. Positive values favor bid recovery; negative values favor ask
recovery. This is NBBO resiliency, not full-book resiliency.

### Canonical QMD signal architecture

Midpoint and trade returns are normalized by the average spread in basis
points and clamped to `[-1, +1]`. When absolute signed-volume imbalance is at
least 0.35, the absorption response is:

```text
-signed volume imbalance * (1 - abs(normalized midpoint return))
```

The three directional blocks are:

```text
Aggressive Flow =
    0.30 * transaction imbalance
  + 0.25 * signed-volume imbalance
  + 0.20 * aggressor persistence
  + 0.15 * normalized trade return
  + 0.10 * arrival-intensity imbalance

Displayed Liquidity =
    0.35 * normalized Level-1 OFI
  + 0.25 * queue imbalance
  + 0.20 * microprice lean
  + 0.20 * arrival-intensity imbalance

Response & Resiliency =
    0.45 * normalized midpoint return
  + 0.30 * resiliency
  + 0.25 * absorption response

Combined Signal =
    0.45 * Aggressive Flow
  + 0.35 * Displayed Liquidity
  + 0.20 * Response & Resiliency
```

Every directional result is clamped to `[-1, +1]`.

Reliability is the product of interval coverage, quote quality, trade
classification quality, evidence density, and directional-block agreement.
Confidence is `100 * reliability * (0.55 + 0.45 * abs(combined signal))`,
clamped to 0-100. The deterministic action is `wait` when confidence is below
35 or absolute signal is below 0.15; otherwise it is `buy` or `sell` by sign.

Fields are `microstructure_aggressive_flow_score`,
`microstructure_displayed_liquidity_score`,
`microstructure_response_resiliency_score`,
`microstructure_regime_reliability`, `microstructure_unified_signal`,
`microstructure_unified_confidence`, and `microstructure_unified_action`.
Strategies should use this combined signal rather than recombining the blocks.

The 25/100/500-event `microstructure_{fast,confirm,context}_*` fields remain
available as event-horizon context. They are not the definition of the
timeframe-consistent bar-level combined signal.

## 2. Session-Anchored OFI and Trade Delta

The anchor is one zero baseline at 04:00 New York time. It does not reset at
09:30. QMD cumulatively adds:

- `microstructure_level1_ofi_delta` into
  `microstructure_cumulative_level1_ofi`; and
- `microstructure_signed_volume_delta` into
  `microstructure_cumulative_signed_volume_delta`.

Aligned endpoints have the same economic cumulative totals across chart
timeframes. The relationship fields classify the two signs:

| Cumulative OFI | Cumulative trade delta | Relationship | Score |
|---:|---:|---|---:|
| positive | positive | bullish confirmation | +1.00 |
| negative | negative | bearish confirmation | -1.00 |
| positive | negative | bullish absorption | +0.55 |
| negative | positive | bearish absorption | -0.55 |
| otherwise | otherwise | neutral | 0.00 |

These are exposed as `microstructure_anchored_flow_relationship` and
`microstructure_anchored_flow_relationship_score`. The cumulative lines answer
what has accumulated since 04:00, so their absolute level depends on session
history; slope and divergence are usually more useful than comparing magnitudes
between symbols.

## 3. Generic Structure

Generic structure is independent of chart candle timeframe. One per-symbol
engine consumes ordered valid NBBO midpoints, while eligible trades confirm
accepted breaks and build volume-at-price references. Every chart timeframe
samples the same causal state at its bar end.

### Adaptive scales

The base movement threshold is the maximum of:

```text
2 * price tick
1.25 * spread EWMA
1.50 * midpoint-move EWMA
reference price * 0.00005
```

| Scale | Threshold | Break acceptance | Evidence half-life | Unified weight |
|---|---:|---:|---:|---:|
| micro | 1x base | 2 events or 100 ms | 30 minutes | 0.20 |
| tactical | 3x base | 3 events or 300 ms | 5 days | 0.35 |
| context | 8x base | 5 events or 1,000 ms | 45 days | 0.45 |

A high pivot is confirmed only after midpoint falls by the scale threshold from
the candidate high; a low pivot is confirmed only after midpoint rises by the
threshold. The pivot's `pivot_at` time is descriptive. The later `confirmed_at`
time is the causal availability boundary.

Higher highs with higher lows set positive scale direction; lower highs with
lower lows set negative direction. A break requires midpoint persistence beyond
the far zone boundary plus an eligible trade beyond it. A break in the current
trend direction is BoS; a break against an established trend is CHoCH.

### Zone lifecycle and role correction

Zones progress through active, boundary-breach candidate, awaiting retest,
retest contact, rejection candidate, and retired states. Crossing a support
does not immediately relabel it resistance. The original zone retires after a
confirmed break; the opposite role is created only after a later retest from
the broken side and a separately confirmed rejection. A zone containing the
current reference price is in play and is not exposed as either side. Exposed
support must be fully below reference; exposed resistance must be fully above.

### Strength, confidence, and selection

For a zone, pre-decay evidence is:

```text
0.18
+ min(touches, 6) * 0.10
+ min(holds, 4) * 0.13
+ min(trade confirmations, 3) * 0.08
- min(breaks, 3) * 0.20
```

Strength is evidence times scale-specific freshness decay, bounded by any
seeded strength and clamped to `[0, 1]`. Confidence is based on
`sqrt((touches + 1.5 * holds) / 7)`, bounded by seeded confidence, discounted
by freshness, and clamped to `[0, 1]`. Strength describes accumulated quality;
confidence describes how well-tested and fresh that evidence is.

Within a scale, the selected level maximizes
`strength * confidence / (1 + 0.12 * normalized distance)`. The unified selected
level additionally accounts for scale weight. `qmd_structure_active_levels`
exposes up to eight nearest valid zones per side and also includes the strongest
distant zone if it was not already selected. Each candidate supplies scale,
side, price, lower/upper bounds, strength, confidence, evidence score, distance,
touch/hold counts, and creation/test timestamps.

### Unified structure score

Scale directions are weighted 0.20/0.35/0.45. Weighted direction above +0.15
is bullish, below -0.15 bearish, otherwise neutral. Agreement is absolute
weighted direction divided by active directional weight. Unified strength and
confidence are weighted across scales; confidence is further discounted when
scales disagree. The final score is:

```text
direction * strength * confidence * (0.5 + 0.5 * agreement)
```

Core fields are `qmd_structure_direction`, `qmd_structure_score`,
`qmd_structure_agreement`, `qmd_structure_strength`, and
`qmd_structure_confidence`. Selected unified zones use
`qmd_structure_{support,resistance}_{price,lower,upper,strength,confidence}`.
The same suffixes exist for `micro`, `tactical`, and `context`, together with
each scale's direction, threshold, swing high, and swing low.

### Causal event fields

`qmd_structure_event_*` exposes the latest confirmed event: deterministic id,
pivot origin time, confirmation time, kind, scale, direction, and price.
Available event kinds include `pivot_high`, `pivot_low`, `touch`, `hold`,
`bos`, `choch`, `level_break`, and `role_reversal`.

### Important reference levels

| Fields | Source and timing |
|---|---|
| `qmd_structure_session_high/low` | NBBO midpoint extrema from the 04:00 New York session anchor. |
| `qmd_structure_premarket_high/low` | NBBO midpoint extrema from 04:00 through 09:30 New York. |
| `qmd_structure_opening_range_high/low` | NBBO midpoint extrema from 09:30 through 09:35 New York. |
| `qmd_structure_trade_volume_poc` | Price with the greatest eligible session trade volume. |
| `qmd_structure_nearest_round` | Adaptive nearest round-number reference. |
| `qmd_structure_luld_upper/lower` | Locally estimated LULD bands; not an official exchange feed. |
| `qmd_structure_52_week_high/low` | Completed daily-bar references. |
| `qmd_structure_prior_month_high/low/close` | Completed prior-calendar-month daily-bar references. |

## 4. Structural Pressure

Structural pressure summarizes **all** active, correctly sided zones, not only
the selected zones rendered on a chart. For each zone:

```text
normalized distance = boundary distance / (base threshold * scale multiplier)
proximity            = exp(-normalized distance / 6)
scale reliability    = 0.75 + 0.25 * (scale weight / 0.45)
zone evidence        = strength * confidence * scale reliability * proximity
```

Overlapping zones, or zones separated by at most `0.75 * base threshold`, are
clustered to prevent double counting. Additional evidence from the same scale
receives 0.35 independence; evidence from another scale receives 0.65. The
nearest twelve clusters on each side combine as `1 - product(1 - evidence)`.

This produces:

```text
support field     S = combined support evidence, [0, 1]
resistance field  R = combined resistance evidence, [0, 1]
pressure bias       = (S - R) / (S + R + 0.20), [-1, 1]
coverage            = 1 - (1 - S) * (1 - R)
separation          = abs(S - R) / (S + R), or 0 with no evidence
confidence          = coverage * separation, [0, 1]
up likelihood       = 0.5 + 0.5 * bias * confidence, [0, 1]
```

Fields are `qmd_structure_support_field`,
`qmd_structure_resistance_field`, `qmd_structure_pressure_bias`,
`qmd_structure_pressure_confidence`, and `qmd_structure_up_probability`.
High support and high resistance with low separation describes compression,
not high directional certainty. Low values on both sides describe an open or
poorly evidenced area. One dominant side produces the strongest directional
reading.

## Causality, History, and Repainting

- Rows are sampled using only events available at each `bar_end`.
- Historical zones are segmented when causal confidence or strength changes;
  later evidence does not repaint an earlier segment.
- The latest active-level set intentionally shows the current best evidence,
  including the nearest configurable zones and strongest distant zones.
- A strategy must use the historical row at its decision time, not apply the
  newest active-level array backward.
- Ordered timestamps are a cache invariant. Duplicate or descending derived
  bars are rejected before caching or streaming.

## Service Availability

Live QMD provides:

```text
GET /indicator-catalog
GET /snapshot/indicators/{ticker}?timeframe={timeframe}&limit={rows}
GET /snapshot/microstructure-forecast/{ticker}
GET /stream/indicators/{ticker}
```

QMD History provides bars and derived indicator history through its snapshot
and `/stream/indicators/{ticker}` contracts. The application backend proxies
the bounded historical chart contract and merges historical batches with live
updates.

Closed bar indicators are memory-first. They are written to
`live_market_indicators` only when `QMD_PERSIST_INDICATORS=true`.
`qmd_structure_events_v1` and `qmd_structure_state_v1` persist confirmed events
and versioned engine checkpoints when `QMD_PERSIST_STRUCTURE_EVENTS=true`
(default). The structure state restores adaptive thresholds, pending state,
zones, references, and event lineage without replaying the full session.

## Active, Compatibility, and Retired Fields

Use `qmd_structure_*` for structure and structural pressure. The older
`liquidity_support_*`, `liquidity_resistance_*`, and `structure_*` families are
compatibility placeholders and are not active evidence authorities.

`market_level_support_score` is the selected canonical support strength times
confidence; `market_level_resistance_score` is the corresponding resistance
value; `market_level_bias` is their clamped difference; and
`liquidity_level_pressure` aliases that bias. These fields are retained for
legacy strategy screens but discard the multi-zone information captured by
structural pressure. New code should prefer `qmd_structure_*`.

## Strategy Use Checklist

1. Confirm `schema_version >= 15` and a current `bar_end`.
2. Use the row for the intended timeframe; interval microstructure changes with
   aggregation, while generic structure itself is timeframe-independent.
3. Gate structure events on `qmd_structure_event_at_ms`, never pivot time.
4. Use zone bounds, not only center price, and require the zone to remain on the
   correct side of the current reference.
5. Combine direction with confidence/reliability. A nonzero score with weak
   evidence is not a strong signal.
6. Confirm displayed liquidity with trades and subsequent price response.
7. Treat estimated LULD and implied up likelihood as deterministic diagnostics,
   not official regulatory values or calibrated probabilities.
8. Backtest using the historical row state available at each decision time.

## Implementation Authorities

- `src/microstructure_forecast.rs`: interval sufficient statistics, component
  formulas, reliability, confidence, and action.
- `src/generic_structure.rs`: adaptive pivots, zones, lifecycle, events,
  structural pressure, and persistence state.
- `src/indicators.rs`: schema, cumulative flow, reference attachment,
  compatibility fields, and ClickHouse persistence.
- `src/indicator_catalog.rs`: discoverable field catalog.
- `services/qmd_history_gateway`: bounded historical replay and streaming.

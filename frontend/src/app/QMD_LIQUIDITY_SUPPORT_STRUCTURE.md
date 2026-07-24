# QMD Liquidity, Support, and Structure in the App

This is the application integration and chart-reading guide for QMD liquidity,
support/resistance, generic structure, and structural pressure. The exact
formulas and service lifecycle are authoritative in
[`services/qmd-gateway/docs/QMD_LIQUIDITY_SUPPORT_STRUCTURE.md`](../../../services/qmd-gateway/docs/QMD_LIQUIDITY_SUPPORT_STRUCTURE.md).

## What the App Receives

The chart consumes closed-bar `IndicatorRow` values from QMD History and then
appends live indicator rows. Historical batches render together; live rows
update asynchronously. All panes share the chart's one native time axis.

QMD is consolidated Level-1 NBBO plus eligible trades. The app must never label
these indicators as Level 2, full-depth liquidity, official LULD, or guaranteed
support/resistance.

## Available Indicator Packages

These package ids are available in the Indicators menu and chart legend:

| Package id | Display name | Primary fields | Presentation |
|---|---|---|---|
| `indicator.qmd_transaction_imbalance` | QMD Transaction Imbalance | trade counts and `microstructure_transaction_imbalance` | Signed oscillator histogram. |
| `indicator.qmd_signed_volume` | QMD Signed-volume Imbalance | buy/sell volume and `microstructure_signed_volume_imbalance` | Signed oscillator histogram. |
| `indicator.qmd_level1_ofi` | QMD Level-1 OFI | `microstructure_level1_ofi` | Signed oscillator histogram. |
| `indicator.qmd_anchored_flow` | QMD Anchored OFI + Trade Delta | cumulative OFI, cumulative trade delta, relationship | Dual cumulative lines plus relationship ribbon. |
| `indicator.qmd_queue_imbalance` | QMD Queue Imbalance | `microstructure_queue_imbalance` | Signed oscillator histogram. |
| `indicator.qmd_microprice_lean` | QMD Microprice Lean | `microstructure_microprice_lean` | Signed oscillator histogram. |
| `indicator.qmd_recent_returns` | QMD Recent Midpoint & Trade Return | midpoint and trade return bps | Two-line oscillator. |
| `indicator.qmd_aggressor_persistence` | QMD Aggressor Persistence | `microstructure_aggressor_persistence` | Signed oscillator histogram. |
| `indicator.qmd_arrival_intensity` | QMD Arrival-intensity Imbalance | imbalance and arrival rate | Signed oscillator; rate is supporting evidence. |
| `indicator.qmd_resiliency` | QMD Liquidity Resiliency | `microstructure_resiliency` | Signed oscillator histogram. |
| `indicator.qmd_decision` | QMD Decision · Oscillator | canonical decision, confidence, action, reason | One signed Buy/Sell/Wait oscillator with confidence. |
| `indicator.qmd_decision_chart` | QMD Decision · Chart signals | the same canonical decision | Green Buy or red Sell marker on the next actionable candle; Wait is blank. |
| `indicator.qmd_generic_structure` | QMD Generic Structure | active zones, complete causal event stream, three-scale swings | Price overlay with independently configurable micro, tactical, and context layers. |
| `indicator.qmd_reference_levels` | QMD Reference Levels | session, premarket, opening range, POC, LULD, and completed higher-timeframe references | Independent price lines, not structural evidence. |

Every package exposes its guide from both the indicator picker and configured
chart legend. QMD Decision is the action surface. Generic Structure remains an
audit and location surface: micro, tactical, and context swings, zones, and
breaks can be turned on independently to verify the causal engine.

## How to Read the Liquidity Oscillators

All signed oscillators use the same semantic baseline:

- above zero: bullish or bid-favoring evidence;
- below zero: bearish or ask-favoring evidence; and
- near zero: balanced, mixed, or insufficient directional evidence.

Magnitude is not confidence by itself. Use the indicator-specific reliability,
activity, or price-response information when available.

### Transaction and signed-volume imbalance

Transaction imbalance compares the **number** of at-ask and at-bid prints.
Signed-volume imbalance compares their **shares**. Agreement is stronger than
either alone. Count-positive but volume-negative means more buyer-initiated
prints occurred, but seller-initiated prints carried more size.

### Level-1 OFI, queue imbalance, and microprice lean

OFI measures best-quote changes, queue imbalance measures displayed size, and
microprice lean translates relative queue size into a location inside the
spread. Agreement among them supports a displayed-liquidity interpretation.
Divergence warns that price, size, and quote-change evidence disagree. All can
be cancelled because they observe displayed NBBO, not executions.

### Arrival intensity and resiliency

Arrival imbalance gives direction; arrival rate tells whether the evidence is
arriving urgently. Resiliency asks which side replenishes after depletion.
Directional arrivals with matching resiliency are stronger than a burst that
immediately disappears.

### Returns and absorption

Midpoint and trade returns are realized response within the selected bar. Flow
and return in the same direction suggest continuation. Strong aggressive flow
with little or opposite midpoint response may indicate passive absorption.

## QMD Decision

The gateway first calculates a timeframe-native microstructure trigger:

- **Combined signal**: 45% aggressive flow, 35% displayed liquidity, and 20%
  response/resiliency;
- **Aggressive flow**: trade count, size, persistence, trade return, and arrival
  direction;
- **Displayed liquidity**: OFI, queue imbalance, microprice lean, and arrival
  direction;
- **Response & resiliency**: midpoint response, replenishment, and absorption;
  and
- **Reliability**: data quality, evidence density, coverage, and block
  agreement. It is non-directional.

It then combines that trigger with Generic Structure and structural pressure.
The trigger contributes 78% and structure contributes 22%, but material
opposition is a veto: the result becomes Wait instead of averaging contradictory
evidence into a weak direction. A directional action requires at least 35%
microstructure confidence and an absolute trigger of 0.15.

The oscillator shows signed decision and 0-1 confidence. The selected Micro,
Tactical, or Context preset converts the same canonical 100 ms stream into a
causal directional regime:

1. a qualifying QMD decision arms the setup;
2. the engine freezes the last confirmed traded-price swing in that direction;
3. entry begins only when a later 100 ms close crosses that frozen level;
4. new higher highs and higher lows extend a Long, while lower lows and lower
   highs extend a Short;
5. a confirmed lower high during a Long, or higher low during a Short, becomes
   an exhaustion candidate rather than an immediate hindsight exit; and
6. the regime closes only on a persistent opposite QMD decision, structural
   invalidation, a protected-swing break confirmed by opposing MACD, a newly
   confirmed opposing BoS or CHoCH event,
   or a failed swing accompanied by opposing preset-native MACD confirmation.

Neutral QMD, confidence decay, elapsed time, or one opposing candle does not end
the regime. Micro samples its MACD helper from completed 1-second closes,
Tactical from 5-second closes, and Context from 15-second closes. MACD
convergence is a warning; MACD on the opposing side of its signal confirms a
failed-swing exit.

The breakout level is the fixed regime rail. Green means Long and red means
Short. The entry marker states confidence and the swing-break reason; the close
marker states the exact causal exit reason. Confidence text is evidence
confidence, not a return target or calibrated win probability. Start and end
timestamps are independent of the displayed candle interval; larger candles
only consolidate the same regime geometry. Historical shading is continuous
between adjacent candle slots and never uses future confidence to restyle an
earlier segment.

## Anchored OFI + Trade Delta Pane

The pane begins from one zero anchor at 04:00 New York time:

- solid **Cumulative OFI**: displayed best-quote flow;
- dashed **Cumulative Trade Delta**: executed aggressive buy minus sell volume;
- relationship ribbon: confirmation, absorption, or neutral; and
- zero baseline: separates positive and negative session accumulation.

Interpretation:

| OFI | Trade delta | Meaning |
|---:|---:|---|
| positive | positive | Bullish confirmation: quotes and executions agree. |
| negative | negative | Bearish confirmation: quotes and executions agree. |
| positive | negative | Possible bullish absorption: displayed bids strengthen while sellers cross. |
| negative | positive | Possible bearish absorption: displayed offers strengthen while buyers cross. |

Read slope and divergence as well as sign. The two lines have different units
and should not be compared by raw height. They share a session anchor but are
not normalized against each other.

## Generic Structure Price Overlay

Generic Structure has two causal layers. The immediate level book updates from
every ordered eligible trade and owns support/resistance and executed-volume
evidence. The local swing hierarchy independently aggregates the exact highest
and lowest eligible trades into 100 ms, 1 s, 5 s, 10 s, 30 s, 1 m, 5 m, and
1 h event-time buckets. NBBO updates maintain displayed-liquidity context, but
an unexecuted quote move cannot create price structure.

Each timeframe confirms its own local high or low from three completed buckets.
The middle bucket is the pivot and the following bucket supplies causal
confirmation. A gap longer than three timeframe buckets resets the neighborhood
so an old sparse print cannot become a local swing minutes later. The chart
interval selects its matching local hierarchy by default; no candle OHLC is
used.

### Current support and resistance zones

At the right edge, the app shows a configurable one-to-six nearest supports and
resistances per side (default three). It also includes the strongest support or
resistance when that zone is not among the nearest set. A starred label marks
that distant strongest addition.

Zones are borderless shaded regions behind candles. Confidence controls fill
intensity; labels include side/rank and confidence. Support is only shown when
the full zone is below current reference price. Resistance is only shown when
the full zone is above. A crossed or in-play zone is omitted until its causal
lifecycle resolves.

### Timeframe audit layers

Each timeframe has two independently configurable audit layers:

- **Swing levels** contains SH / SL lines from the exact traded-price pivot
  until it is crossed or a newer same-side local swing supersedes it.
- **Structure breaks** contains BoS / CHoCH connectors from the pivot to the
  accepted break.

Each layer has its own bullish and bearish colors, line shape, width, opacity,
history window, and historical-label settings. Only both layers matching the
selected chart interval are visible by default. Open the indicator legend to
enable other timeframe layers for comparison. Breaks additionally expose the
swing-to-break connector toggle. Each break is rendered as one clean semantic
color from the originating pivot time to the accepted break time, with the
plain `BoS` or `CHoCH` label inset directly into the connector at its fixed
time midpoint. When the connector is too short or its centered label would
collide with another structure label, the label keeps that same horizontal
midpoint but moves above a bullish connector or below a bearish connector.
Swing lines and break connectors both start and end at the horizontal center
of the candles containing their causal event timestamps; they never expand to
the left or right candle edge. The break connector masks any overlapping
swing-reference segment so dashed styles do not mix bullish and bearish
colors. Panning or scaling can hide an off-screen label but never relocate it
to a different part of the connector. This keeps a 1 s chart readable while
still allowing the independent 100 ms through 1 h hierarchies to be audited. A
first crossing is immediate but does not become BoS or CHoCH until the
deterministic acceptance rule confirms price remained beyond the level.

### Historical structure

Historical segments use the strength and confidence known at that time. Later
touches do not repaint earlier segments. Current-edge zones intentionally show
the latest evidence; historical strategy evaluation must use each historical
row rather than project the latest zone backward.

### Events and references

BoS is continuation through a confirmed swing in the current trend direction.
CHoCH is an accepted break against an established trend. Their connectors run
from the confirmed swing origin to the later break confirmation. Pivot, BoS,
CHoCH, break, and role-reversal availability begins at confirmation time.

Configurable QMD Reference Levels include session high/low, premarket high/low,
opening range, trade-volume POC, estimated LULD, completed
52-week high/low, and prior-month high/low/close. They are context references,
not all support/resistance evidence of equal quality.

The timeframe labels are separate event-native local swing and break states.
A 1 s BoS can only break the active 1 s swing; it cannot inherit a 100 ms or
1 h break. They are still not candle-derived swing engines.

## Structural context inside QMD Decision

The standalone Structure and Structural Pressure oscillators are retired. Their
canonical fields remain available to strategies and feed QMD Decision:

- **Support field**: proximity-weighted support evidence, 0-1;
- **Resistance field**: proximity-weighted resistance evidence, 0-1;
- **Pressure bias**: signed balance of support versus resistance, -1 to +1;
- **Directional confidence**: coverage times separation, 0-1; and
- **Implied up likelihood**: `0.5 + 0.5 * bias * confidence`, 0-1.

The exact service formula discounts distance and correlated overlapping zones.
Read common states as follows:

| Support | Resistance | Bias/confidence | Interpretation |
|---|---|---|---|
| high | low | positive, higher confidence | Support evidence dominates. |
| low | high | negative, higher confidence | Resistance evidence dominates. |
| high | high | near zero, low confidence | Compression between strong opposing fields. |
| low | low | near zero, low confidence | Open area or insufficient structural evidence. |

Implied up likelihood remains deterministic and uncalibrated. `0.70` does not mean
that 70% of future bars will rise unless a separate validation study establishes
that calibration for the symbol, horizon, and market regime.

## Configuration That Matters

Generic Structure exposes useful controls for:

- current nearest-zone count and strongest-zone inclusion;
- zone/reference/event group visibility;
- causal history length and historical tag density;
- fill intensity and line style where a line is actually drawn;
- independent micro, tactical, and context zone/swing/break visibility; and
- QMD Decision oscillator thresholds and native pane height.

Swing references, session/premarket levels, opening range, POC, estimated LULD,
completed higher-timeframe references, and structure-break
connectors render as true lines rather than translucent fixed-height bands.
Their opacity control is the final line opacity from 0-100%; shape, width,
history window, historical labels, label size/limit, and axis tags are exposed
only where the corresponding visual supports them.

Current support and resistance axis tags use the same confidence-adjusted
semantic color and configured opacity as their chart regions. Setting opacity
to zero removes both the region and its axis tag; the axis-tag toggle remains
an independent visibility control. Because the chart library renders price-axis
tags as opaque swatches, the app precomposes the requested opacity against the
active chart background. This preserves the intended visible tint at settings
such as 30% in both light and dark themes while retaining readable tag text.

Structural Pressure exposes series visibility/style, a configurable horizontal
threshold, and pane height. Price-axis labels use the app's standard axis label
size; controls that cannot affect their rendering should not be added.

All price overlays must remain behind candles. Text is collision-managed and
kept off candle bodies where possible. Pane height is owned by Lightweight
Charts native pane stretch factors; the app must not introduce a competing
resize authority.

## Data and Failure States

- `schema_version` below 15 may not contain the current structure/pressure
  contract.
- Zero can mean neutral, but it can also mean missing evidence. Check counts,
  coverage, reliability, and service freshness before interpreting it.
- A disconnected QMD History service prevents historical indicator loading;
  live transport cannot reconstruct the missing past by itself.
- Historical responses are bounded and strictly chronological. The frontend
  must preserve timestamp order and merge by parsed instant, not timestamp text.
- The latest trustworthy snapshot should remain visible with its timestamp when
  a source becomes temporarily unavailable.

## Strategy-Safe Field Selection

For new strategy and app work, use:

- `microstructure_unified_signal`, confidence, reliability, and its component
  fields for interval flow;
- cumulative OFI/trade delta and relationship for session-anchored divergence;
- `qmd_structure_active_levels`, selected zone fields, event fields, and
  per-scale fields for exact structure;
- structural-pressure fields for a compact directional summary; and
- event `confirmed_at`/`qmd_structure_event_at_ms` for causal gating.

Do not build new features on `liquidity_support_*`,
`liquidity_resistance_*`, or the old `structure_*` families. They are legacy
compatibility fields. `market_level_*` and `liquidity_level_pressure` are also
lossy compatibility aliases of selected canonical zones; they do not represent
the full structural field.

## Source Locations

- Indicator registration and inline guides:
  `frontend/src/pages/CanvasConfigurationPage.tsx`
- Native panes, legends, and series rendering:
  `frontend/src/app/components/ChartPanel.tsx`
- Service formulas and lifecycle:
  `services/qmd-gateway/src/microstructure_interval.rs` and
  `services/qmd-gateway/src/generic_structure.rs`
- Schema and persistence:
  `services/qmd-gateway/src/indicators.rs`

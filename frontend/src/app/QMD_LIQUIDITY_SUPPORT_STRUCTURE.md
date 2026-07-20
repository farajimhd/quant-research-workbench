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
| `indicator.qmd_architecture` | QMD Signal Architecture | combined signal, three blocks, reliability | One explanatory oscillator package. |
| `indicator.qmd_generic_structure` | QMD Generic Structure | active zones, references, events, score/confidence/agreement | Price overlay plus structure oscillator. |
| `indicator.qmd_structural_pressure` | QMD Structural Pressure | support/resistance fields, bias, confidence, up likelihood | Directional pressure oscillator. |

Every package exposes its guide from both the indicator picker and configured
chart legend. Generic Structure and Structural Pressure are separate packages:
the first shows exact locations and causal events; the second summarizes all
active zones into one directional view.

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

## QMD Signal Architecture Pane

The pane contains:

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

Read Combined signal for direction, the three blocks for attribution, and
Reliability for trustworthiness. Do not sum the displayed lines again. The
gateway already owns the canonical combination.

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

Generic Structure comes from ordered NBBO midpoint and eligible trades, not
from the selected candle OHLC. Changing timeframe changes sampling density and
chart history, but it does not redefine the underlying pivots or zones.

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

### Selected and scale-specific zones

**Selected structure zones** are the optional single support and resistance
winners chosen across micro, tactical, and context using strength, confidence,
scale weight, and distance. They were previously called Decision zones. They
are not a separate decision signal and are disabled by default because Current
support & resistance preserves more of the active candidate map.

Micro, Tactical, and Context zones expose the winning zones within an individual
event-response scale. They are also disabled by default and are intended for
diagnosis. Tactical zones use three times the adaptive base threshold, require
three events or 300 ms of break acceptance, carry a five-day evidence half-life,
and are useful for intraday retests, invalidation, and breakout context. The
half-life is evidence retention, not a five-day forecast horizon.

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

Configurable reference groups include session high/low, premarket high/low,
opening range, trade-volume POC, nearest round price, estimated LULD, completed
52-week high/low, and prior-month high/low/close. They are context references,
not all support/resistance evidence of equal quality.

### Structure oscillator

- **Structure score**: direction times strength, confidence, and scale
  agreement, in `[-1, +1]`;
- **Confidence**: tested and fresh evidence in `[0, 1]`; and
- **Scale agreement**: agreement among micro, tactical, and context direction,
  optional by default.

Micro, tactical, and context are event-response scales, not chart timeframes.
They use increasingly large adaptive price thresholds and longer evidence
half-lives.

## Structural Pressure Pane

This pane compresses all active, correctly sided structure zones:

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

Implied up likelihood is deterministic and uncalibrated. `0.70` does not mean
that 70% of future bars will rise unless a separate validation study establishes
that calibration for the symbol, horizon, and market regime.

## Configuration That Matters

Generic Structure exposes useful controls for:

- current nearest-zone count and strongest-zone inclusion;
- zone/reference/event group visibility;
- causal history length and historical tag density;
- fill intensity and line style where a line is actually drawn;
- structure-score, confidence, and agreement series visibility; and
- oscillator thresholds and native pane height.

Swing references, session/premarket levels, opening range, POC, estimated LULD,
completed higher-timeframe references, round price, and structure-break
connectors render as true lines rather than translucent fixed-height bands.
Their opacity control is the final line opacity from 0-100%; shape, width,
history window, historical labels, label size/limit, and axis tags are exposed
only where the corresponding visual supports them.

Current support and resistance axis tags use the same confidence-adjusted
semantic color and configured opacity as their chart regions. Setting opacity
to zero removes both the region and its axis tag; the axis-tag toggle remains
an independent visibility control.

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
  `services/qmd-gateway/src/microstructure_forecast.rs` and
  `services/qmd-gateway/src/generic_structure.rs`
- Schema and persistence:
  `services/qmd-gateway/src/indicators.rs`

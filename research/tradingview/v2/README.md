# Structural reaction levels v2

Replace the old structural indicator on TradingView with the complete contents of `structural_zones.pine`. Keep using the independent v1 MACD and jump-alert scripts; those are unchanged. This remains a price-bar approximation of v7, not the repo's statistical builder or a calibrated prediction model.

## Low-price / seconds-chart display (v2.5)

At $1.23 the old 0.1% log bucket was only about $0.0012 wide. Separate DAY/HIST books could create coincident levels, and the 3% proximity filter hid major session swings after a large move.

The bucket transform is now `floor(log(1 + price / scale) / log(1 + percent_width))`, with `scale = max(minimum_bucket_price, 2 * symbol_tick) / percent_width`. Its price-space bucket width is approximately the fixed floor plus the percentage component. This is a tick/dollar-aware grouping rule, **not a minimum rendered band width**. Bounds still come solely from eligible reaction prices. The explicit default dollar floor is $0.01; adjust for non-equity instruments or sub-cent analysis.

`Include current-day structure across visible price range` is enabled by default. It admits DAY candidates inside the visible chart's high/low range even when farther than the 3% proximity filter; historical candidates still use proximity. Panning/zooming changes this display filter, not reaction extraction or qualification. Per-side limits and source reservations still apply, so not every visible swing will be drawn.

Duplicate/nearby drawings across source books are suppressed after selection priority. Minimum center separation is `max(minimum_bucket_price, symbol_tick * minimum_display_ticks, visible_price_span / 80)`. Suppression changes display only; it does not merge samples or fabricate shared geometry. The higher-priority selected source survives. Captions are independently suppressed within `max(drawing_gap, visible_price_span / 35)` to reduce overlap; lines without captions remain visible. Captions retain source, provisional flag, main price, and available bounds. Pine lacks pixel collision detection, so extreme zoom/font combinations still need visual checking.

## Current-day structure (v2.4)

The former combined book required every qualified bucket to span at least two days. Today's newly discovered highs/lows therefore remained gray provisional candidates even when they had several reactions. Historical levels could also consume every display slot.

The update separates current-day and prior-day observations into distinct books. Current-day qualification requires the same minimum spaced-reaction count (three by default), but has no multi-day requirement. Historical qualification retains its count and distinct-day filters. Three slots per side are reserved for current-day candidates by default; a second pass fills remaining space from either source. A source cannot consume more than the overall per-side limit, and unfilled reservations do not suppress historical levels. Disable reservation by setting it to zero.

Captions identify `DAY` or `HIST`, `P` when provisional, and `R` above completed price / `S` below it. Qualified historical main lines are solid; current-day main lines are dashed. Provisional lines use the same price-position colors at reduced opacity, rather than gray: their `P` label still explicitly marks insufficient evidence. Provisional levels have no fitted band. Bands still require a qualified candidate and at least three distinct reaction prices; no boundaries are invented.

This is still chart-timeframe detection: the default ten-bar pivot confirmation takes ten minutes on a 1m chart. It does not detect every turning point or imply an immediate confirmed level at the day's high/low. Current-day identity follows the exchange daily-bar boundary, including loaded extended-hours bars. On each new exchange day, previous-day reactions join the historical book and are evaluated against historical qualification. Current-day candidates remain separate even if a historical bucket occupies the same price.

## Presentation

The v2.1 update keeps the qualification thresholds unchanged and enables provisional candidates by default. Within each selection pass, qualified levels take display priority. Nearby candidates that fail the reaction-count or distinct-day requirements fill remaining per-side slots as **faint dashed lines labeled P**. Disable `Show provisional candidates` for strict qualified-only display. This is an explicit exploratory display mode, not a relaxation of level qualification. No pivots or no nearby candidates can still mean no lines.

The v2.2 presentation removes the status table. Defaults show up to eight zones above and eight below price, within 3%, prioritizing qualified zones and then nearby provisional candidates. `Zones above / below price` accepts up to 30 per side. The count is a maximum, not a promise that enough candidates exist.

The v2.3 update follows the app renderer's hierarchy (`ReactionBook.tsx`): one prominent main price line and, when geometry exists, a faint fill between lower and upper bounds. It does not draw three equally prominent levels. Qualified historical main lines are solid; current-day and provisional lines are dashed, with provisional opacity reduced. Colors still use the Pine approximation's price-position rule; they do not reproduce the app's evidence-based role state.

Bounds are available only for qualified levels with at least three spaced observations and at least three distinct reaction prices (distinction uses half a symbol tick). They use the 10th/90th reaction-price percentiles without minimum-width padding or substituted bucket edges. A lower bound must be below the main price; an upper bound must be above it. Missing bounds stay missing. Provisional, singleton, repeated-identical-price, or degenerate candidates have only their main line. A filled band requires both bounds. Optional `Outline available lower / upper boundaries` is off by default; enabling it draws thin outlines only for available sides. The app uses a Student-t fit; Pine's percentile estimate remains an explicit approximation.

Text sits below the main level near the price axis. `333.20 L:333.10 U:333.30 S8/R3` reports the main price, available lower/upper bounds, and low/high pivot counts. Missing L/U values are omitted. `P` marks provisional. Counts are not hold probabilities. Pine cannot anchor labels to an axis in pixels; adjust `Text position` to match the chart's right margin.

Current levels overlaid on earlier candles were not necessarily available then: this is an as-of-now snapshot, not a historical backtest overlay. Geometry refreshes on bar close; panning/zooming triggers Pine recalculation.

## Estimator changes

- Every retained reaction has a price, pivot timestamp, confirmation availability time, side, exchange day, and confirmed move-away strength measured in pivot-bar ATR units.
- Individual pivots expire at the rolling calendar-day cutoff; refreshed zones cannot retain expired geometry or counts. With identical loaded bars, streaming and reload use the same window and deterministic grouping.
- Fixed tick/dollar-aware logarithmic price buckets replace moving-center clustering. Default percentage component is 0.1%; bucket centers cannot chain-drift across many price ranges. A bucket boundary can split nearby reactions; this is a deliberate bounded approximation rather than a statistical fit.
- Median centers and 10th/90th percentiles replace the expanding full min/max envelope. There is no artificial minimum band width.
- A pivot is accepted only if the close at confirmation has moved away by the configured ATR multiple. Counts further require same-side reactions in a bucket to be separated by 300 seconds by default. This is a spacing proxy for independence, not proof of independent encounters. Two distinct reaction days and three reactions qualify a zone by default.
- Evidence sums `min(move_away_ATR, 5) * 0.5^(age_days / half_life_days)`. It has no probabilistic interpretation. There is no out-of-sample evidence yet that these changes improve trading outcomes.

## Inputs and coverage

Start with defaults on your 1m AAPL chart. On 1s charts, raise pivot lengths if microstructure noise produces too many pivots. Left/confirmation lengths are chart bars; default confirmation takes ten minutes on 1m, ten seconds on 1s. Enable the chart's extended-hours data to include those observations. Requested history defaults to 30 calendar days; the script cannot fetch unavailable history. The removed status table no longer reports coverage; the requested history still does not certify loaded coverage or continuity. ATR warmup and pivot confirmation require additional preceding bars.

All valid reactions are stored up to an explicit default limit of 10,000 (maximum 20,000). Exhaustion raises an error rather than silently dropping data. Increase pivot lengths/reaction threshold, shorten the window, or increase the explicit limit. Historical ingestion is incremental; zone grouping/sorting is done at the historical/live boundary and completed live bars, rather than on every historical bar or realtime tick. Expiration uses array shifts only when old observations leave. TradingView execution limits still apply; 30 days of 1s history has not been benchmarked.

## Validation status and manual checks

Source review and local invariant checks are distinct from Pine compilation. TradingView compilation, live runtime, and visual validation remain pending.

1. Compile and replace the old structural indicator; ensure it is not still drawing underneath. Check a low-price 5s equity chart and 1m QQQ/AAPL. Verify distant DAY swings inside the visible price range can display, coincident DAY/HIST levels do not duplicate, and nearby captions are suppressed.
2. Defaults show at most 16 main lines, with faint bands only where bounds exist. Provisional and identical-price candidates must have no bands. Toggle optional outlines/text, adjust horizontal text offset, and confirm text sits below the main line. Increase the per-side zone count to show more candidates.
3. Test a one-day window with minimum distinct days set to one, then return to 30 days/two days. Check provisional captions and an intentionally small retained-pivot limit. There is no status table or insufficient-history warning.
4. Compare reload versus streaming on the same loaded dataset at the same completed bar. A new reaction must wait for confirmation and pass its move-away threshold.
5. Compare measured future holds/breaks against v1 over untouched sessions before making predictive claims. Do not select settings from the evaluation sessions.

Pine references: [arrays](https://www.tradingview.com/pine-script-docs/language/arrays/), [maps](https://www.tradingview.com/pine-script-docs/language/maps/), [visible chart timestamps](https://www.tradingview.com/pine-script-docs/concepts/time/).

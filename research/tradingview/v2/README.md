# Structural reaction levels v2

Replace the old structural indicator on TradingView with the complete contents of `structural_zones.pine`. Keep using the independent v1 MACD and jump-alert scripts; those are unchanged. This remains a price-bar approximation of v7, not the repo's statistical builder or a calibrated prediction model.

## Presentation

The v2.1 update keeps the qualification thresholds unchanged and enables provisional candidates by default. Qualified levels take display priority. Nearby candidates that fail the reaction-count or distinct-day requirements fill remaining per-side slots as **dashed gray lines labeled PROVISIONAL**. Disable `Show provisional candidates` for strict qualified-only display. This is an explicit exploratory display mode, not a relaxation of level qualification. No pivots or no nearby candidates can still mean no lines.

The v2.2 presentation removes the status table. Defaults show up to eight zones above and eight below price, within 3%, prioritizing qualified zones and then nearby provisional candidates. `Zones above / below price` accepts up to 30 per side. The count is a maximum, not a promise that enough candidates exist.

Each zone has explicit lower and upper boundary lines, a dotted center line, and a faint central 80% reaction band by default. Provisional geometry is dashed gray; `P` in its caption marks it provisional. Green/red/orange qualified colors retain their price-position meaning. Disable `Show lower / upper boundaries and faint bands` for center lines only.

Optional text is placed beneath the lower boundary and right-aligned near the price axis. Pine cannot anchor labels to an axis in pixel coordinates. `Text position: bars to right of latest visible candle` controls the horizontal placement (default 15); adjust it to match your chart's right margin or disable text. If the caption is offscreen, reduce that offset or increase the chart's right margin. Text follows the latest candle while it forms.

A caption such as `333.10 / 333.20 / 333.30 S8/R3` reports lower / center / upper prices, eight independently spaced low-pivot reactions, and three high-pivot reactions. Counts are not hold probabilities. Current levels overlaid on earlier candles were not necessarily available then: this is an as-of-now snapshot, not a historical backtest overlay. Geometry refreshes on bar close; panning/zooming triggers Pine recalculation.

## Estimator changes

- Every retained reaction has a price, pivot timestamp, confirmation availability time, side, exchange day, and confirmed move-away strength measured in pivot-bar ATR units.
- Individual pivots expire at the rolling calendar-day cutoff; refreshed zones cannot retain expired geometry or counts. With identical loaded bars, streaming and reload use the same window and deterministic grouping.
- Fixed logarithmic price buckets replace moving-center clustering. Default width is 0.1%; bucket centers cannot chain-drift across many price ranges. A bucket boundary can split nearby reactions; this is a deliberate bounded approximation rather than a statistical fit.
- Median centers and 10th/90th percentiles replace the expanding full min/max envelope. The optional minimum band width can extend outside the bucket.
- A pivot is accepted only if the close at confirmation has moved away by the configured ATR multiple. Counts further require same-side reactions in a bucket to be separated by 300 seconds by default. This is a spacing proxy for independence, not proof of independent encounters. Two distinct reaction days and three reactions qualify a zone by default.
- Evidence sums `min(move_away_ATR, 5) * 0.5^(age_days / half_life_days)`. It has no probabilistic interpretation. There is no out-of-sample evidence yet that these changes improve trading outcomes.

## Inputs and coverage

Start with defaults on your 1m AAPL chart. On 1s charts, raise pivot lengths if microstructure noise produces too many pivots. Left/confirmation lengths are chart bars; default confirmation takes ten minutes on 1m, ten seconds on 1s. Enable the chart's extended-hours data to include those observations. Requested history defaults to 30 calendar days; the script cannot fetch unavailable history. The removed status table no longer reports coverage; the requested history still does not certify loaded coverage or continuity. ATR warmup and pivot confirmation require additional preceding bars.

All valid reactions are stored up to an explicit default limit of 10,000 (maximum 20,000). Exhaustion raises an error rather than silently dropping data. Increase pivot lengths/reaction threshold, shorten the window, or increase the explicit limit. Historical ingestion is incremental; zone grouping/sorting is done at the historical/live boundary and completed live bars, rather than on every historical bar or realtime tick. Expiration uses array shifts only when old observations leave. TradingView execution limits still apply; 30 days of 1s history has not been benchmarked.

## Validation status and manual checks

Source review and local invariant checks are distinct from Pine compilation. TradingView compilation, live runtime, and visual validation remain pending.

1. Compile and replace the old structural indicator; ensure it is not still drawing underneath.
2. Defaults show at most 16 zones, with three lines per zone and faint bands. Toggle bands/text, adjust horizontal text offset, and confirm text sits below the lower boundary. Increase the per-side zone count to show more candidates.
3. Test a one-day window with minimum distinct days set to one, then return to 30 days/two days. Check provisional captions and an intentionally small retained-pivot limit. There is no status table or insufficient-history warning.
4. Compare reload versus streaming on the same loaded dataset at the same completed bar. A new reaction must wait for confirmation and pass its move-away threshold.
5. Compare measured future holds/breaks against v1 over untouched sessions before making predictive claims. Do not select settings from the evaluation sessions.

Pine references: [arrays](https://www.tradingview.com/pine-script-docs/language/arrays/), [maps](https://www.tradingview.com/pine-script-docs/language/maps/), [visible chart timestamps](https://www.tradingview.com/pine-script-docs/concepts/time/).

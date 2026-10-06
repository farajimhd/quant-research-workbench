# Structural reaction levels v2

Replace the old structural indicator on TradingView with the complete contents of `structural_zones.pine`. Keep using the independent v1 MACD and jump-alert scripts; those are unchanged. This remains a price-bar approximation of v7, not the repo's statistical builder or a calibrated prediction model.

## Presentation

Defaults show three nearest levels above price and three below, within 3%. Thin center lines span the visible chart and extend right. Labels sit at the latest completed candle. Bands are off by default; enable them for faint central 80% reaction-price intervals. Green means completed price above the band, red below, orange inside. These colors describe position, not certified support/resistance role transitions.

A label such as `333.20 | S8/R3 | 4d | evidence 6.2` means eight independently spaced confirmed low-pivot reactions, three high-pivot reactions, four distinct exchange days, and a recency-weighted reaction-strength sum. It is not a hold probability or a forecast. Display selection uses proximity, not the evidence score. Current levels overlaid on earlier candles were not necessarily available then: this is expressly an as-of-now snapshot, not a historical backtest overlay. The snapshot refreshes on bar close; panning/zooming triggers Pine recalculation.

## Estimator changes

- Every retained reaction has a price, pivot timestamp, confirmation availability time, side, exchange day, and confirmed move-away strength measured in pivot-bar ATR units.
- Individual pivots expire at the rolling calendar-day cutoff; refreshed zones cannot retain expired geometry or counts. With identical loaded bars, streaming and reload use the same window and deterministic grouping.
- Fixed logarithmic price buckets replace moving-center clustering. Default width is 0.1%; bucket centers cannot chain-drift across many price ranges. A bucket boundary can split nearby reactions; this is a deliberate bounded approximation rather than a statistical fit.
- Median centers and 10th/90th percentiles replace the expanding full min/max envelope. The optional minimum band width can extend outside the bucket.
- A pivot is accepted only if the close at confirmation has moved away by the configured ATR multiple. Counts further require same-side reactions in a bucket to be separated by 300 seconds by default. This is a spacing proxy for independence, not proof of independent encounters. Two distinct reaction days and three reactions qualify a zone by default.
- Evidence sums `min(move_away_ATR, 5) * 0.5^(age_days / half_life_days)`. It has no probabilistic interpretation. There is no out-of-sample evidence yet that these changes improve trading outcomes.

## Inputs and coverage

Start with defaults on your 1m AAPL chart. On 1s charts, raise pivot lengths if microstructure noise produces too many pivots. Left/confirmation lengths are chart bars; default confirmation takes ten minutes on 1m, ten seconds on 1s. Enable the chart's extended-hours data to include those observations. Requested history defaults to 30 calendar days; the script cannot fetch unavailable history. Loaded span/bar counts do not certify continuity. ATR warmup and pivot confirmation require additional preceding bars.

All valid reactions are stored up to an explicit default limit of 10,000 (maximum 20,000). Exhaustion raises an error rather than silently dropping data. Increase pivot lengths/reaction threshold, shorten the window, or increase the explicit limit. Historical ingestion is incremental; zone grouping/sorting is done at the historical/live boundary and completed live bars, rather than on every historical bar or realtime tick. Expiration uses array shifts only when old observations leave. TradingView execution limits still apply; 30 days of 1s history has not been benchmarked.

## Validation status and manual checks

Source review and local invariant checks are distinct from Pine compilation. TradingView compilation, live runtime, and visual validation remain pending.

1. Compile and replace the old structural indicator; ensure it is not still drawing underneath.
2. Default display should show at most six lines and no shaded rectangles. Toggle bands; bounds should stay close to each center.
3. Test a one-day window with minimum distinct days set to one, then return to 30 days/two days. Check counts, insufficient-history warning, and an intentionally small retained-pivot limit.
4. Compare reload versus streaming on the same loaded dataset at the same completed bar. A new reaction must wait for confirmation and pass its move-away threshold.
5. Compare measured future holds/breaks against v1 over untouched sessions before making predictive claims. Do not select settings from the evaluation sessions.

Pine references: [arrays](https://www.tradingview.com/pine-script-docs/language/arrays/), [maps](https://www.tradingview.com/pine-script-docs/language/maps/), [visible chart timestamps](https://www.tradingview.com/pine-script-docs/concepts/time/).

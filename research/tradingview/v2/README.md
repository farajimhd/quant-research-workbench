# Structural reaction levels v2 (v2.8 display)

Copy the complete `structural_zones.pine` into TradingView's Pine Editor, replacing the old structural indicator. The independent v1 MACD/jump-alert scripts are unchanged. This is a chart-bar approximation, not the repo's causal v7 statistical builder.

## Margin text and explicit transparency (v2.10)

Remove the previous structural indicator and add the updated script, identified as **Research: structural reaction levels v2.10**, to avoid retained old input settings or duplicate drawings.

The prior zero-offset right-aligned label ended at the latest visible candle and extended its text left across candles. Captions now **start** ten bar positions beyond the rightmost visible candle and extend right, below their main line. They use bar-index positioning, so overnight/session gaps do not distort the offset. `Text gap beyond rightmost visible candle (bars)` controls that gap and cannot be zero or negative. Pine cannot anchor custom captions to the axis in pixels; provide enough chart right margin to display the text. If clipped, increase the chart's right margin or reduce the positive gap. There is no table.

`Line transparency (%): 0 solid, 100 invisible` defaults to **75% transparent** and is applied directly to every main/boundary line's color. Provisional lines add ten percentage points of transparency. `Text opacity (%)` remains independent. All lines remain solid, width 1; band transparency remains separate. Set line transparency to 100 as a diagnostic: structural main/boundary lines must disappear, while text, bands, and other indicators can remain.

## Stable level selection

Panning/zooming no longer changes eligibility, price-region selection, or level/caption spacing. The earlier visible-price-range filters and viewport-derived spacing were the cause of disappearing levels. The viewport is now used only for drawing endpoints and horizontal caption placement.

The selected book is based on the latest completed bar, accepted reactions in the requested rolling calendar-day window, and explicit inputs. Distance restriction is **off by default**, so distant historical levels can carry into current sessions. Enable `Restrict levels to maximum distance from latest price` to use the percentage-distance input.

Historical price-region coverage uses the retained reaction book's high/low, not the visible candles. Display separation uses `max(minimum_bucket_price, symbol_tick * minimum_display_ticks)`. Caption separation is twice that gap. Level suppression affects drawings only, never the retained observations. Panning over the same loaded source data at the same completed bar leaves the selected prices, geometry, status, and caption inclusion unchanged.

New completed candles, rolling expiry, input changes, or newly loaded history can still change the book. Panning may cause TradingView to load previously unavailable bars; that is a data change, not a viewport-only change. The script cannot manufacture missing history or certify uninterrupted coverage. This is an as-of-now overlay, not a reconstruction of levels known at each historical candle.

## Reactions and geometry

Detection uses the chart timeframe: ten confirmation bars means 50 seconds on 5s, ten minutes on 1m. Completed high/low pivots must pass the configured ATR move-away gate. Same-side reactions within a price bucket must be spaced by the configured seconds.

Fixed buckets use `floor(log(1 + price / scale) / log(1 + percentage))`, where `scale = max(minimum_bucket_price, 2 * symbol_tick) / percentage`. This provides a tick/dollar floor plus percentage spacing. The default dollar floor is $0.01; adjust it for other instruments. It is a grouping scale, not a fabricated band width.

DAY and HIST observations are separate. Both require three spaced reactions by default for qualification; HIST additionally requires two distinct exchange days. The exchange daily boundary defines DAY, including loaded extended-hours bars. A new day moves previous-day reactions into HIST qualification.

Each level has one main median-price line. Available bounds use the 10th/90th reaction-price percentiles only when the candidate qualifies and has at least three distinct prices. Lower must be below the main price; upper must be above. Missing sides stay missing, and a fill requires both sides. No padding or bucket edges substitute for missing bounds. Provisional candidates never receive bands. This is a percentile approximation; the app uses a Student-t fit.

## Display

All main and optional boundary lines are **solid, width 1**. Faint bands are enabled, optional boundary outlines disabled. Captions identify DAY/HIST, P for provisional, and price-relative S/R, then main price and available L/U bounds. Qualified colors are green above the level/band, red below, orange inside; provisional colors use reduced opacity. These colors are not the app's evidence-based role transitions.

Selection reserves current-day slots (three per side by default), first prioritizing eligible current-day extremes, then reaction strength. Historical selection takes one level per book-price region before filling remaining slots. Historical qualification precedes recency-weighted reaction strength. The overall limit defaults to eight per side and accepts up to 30. Extrema are accepted reaction candidates, not raw session high/low. No particular price is guaranteed to qualify or win bounded display selection.

Evidence strength is `sum(min(move_away_ATR, 5) * 0.5^(age_days / half_life))`; it is not a hold probability. Captions sit below their main line beyond the rightmost visible candle; Pine has no pixel anchor to the price axis. Adjust the bars offset to match your chart margin. Nearby captions may be suppressed to avoid overlap, but their lines remain. There is no table.

## Validation

Source review and local design/static checks do not prove Pine compilation or rendered behavior. TradingView compilation, real-feed performance, and visual verification remain pending. No measured predictive improvement is claimed.

1. Replace the old indicator and reset inputs. Check low-price 5s and QQQ/AAPL 1m charts.
2. At the same completed bar and loaded history, pan/zoom repeatedly: line prices, bounds, status, and caption inclusion must remain stable; endpoints/text positions may move.
3. Enable the distance restriction and verify the explicit filtering. Return it to off for full-book carry.
4. Single/identical-price/provisional candidates must have no fabricated bounds. Toggle fills/outlines/text independently.
5. Check new-day reclassification, rolling expiry, short history, and the explicit retained-pivot capacity error. The default limit is 10,000 pivots (maximum 20,000); shorten history or adjust detection inputs if exhausted.
6. Compare future hold/break behavior on untouched sessions before making predictive claims. Do not tune on evaluation sessions.

References: [Pine arrays](https://www.tradingview.com/pine-script-docs/language/arrays/), [bar states](https://www.tradingview.com/pine-script-docs/concepts/bar-states/), [limits](https://www.tradingview.com/pine-script-docs/writing/limitations/).

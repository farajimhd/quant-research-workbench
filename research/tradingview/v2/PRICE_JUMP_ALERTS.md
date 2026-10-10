# Price jump screener and alerts v2.1

Copy `price_jump_alerts.pine` into TradingView's Pine Editor, save, and add it to a standard time-based intraday chart. It is standalone and independent of the structural indicator and the preserved v1 jump/volume proxy. This detects percentage moves in chart-bar closes, not a statistical jump-process model or a certified workbench signal.

Default: an upward return of at least 2% from the close five chart bars ago to the latest completed close. On 1m this means five minutes; on 5m it means 25 minutes. Change **Detect** to Downward or Both to include falls. A threshold equal to the measured move qualifies. Intrabar excursions that reverse before close do not trigger.

Missing-bar/session-gap rejection is enabled: the elapsed time between the anchor and current bar must not exceed the configured bar count times the timeframe duration. Overnight gaps and sparse windows are rejected. Disable it explicitly to include moves across missing bars/session gaps; then the lookback is a count of available bars, not a continuous elapsed-time window. All inputs work without external requests or data sources.

## Pine Screener

1. Save the script and add it to your indicator favorites.
2. Open Pine Screener, choose your watchlist/source and **Research: price jump screener / alerts v2.1**.
3. Choose **1 minute** to start, and configure the indicator inputs to match your chart.
4. Filter **Recent up signal = 1** for an upward alert within the last five completed bars by default. Use **Up qualified = 1** for a move that still meets the threshold and gates. Downward equivalents require Detect = Downward or Both. To see either direction, use the screener's filter combination controls as available, or scan each direction separately.
5. Sort by **Jump %** or **Relative volume**, then scan/rescan. **New up/down signal** is only the latest completed bar's alert event; prefer Recent for scans that can miss that bar.

The first ten numeric plots are the screening columns. Flags are 1/0. **Data ready** is 0 when the price window is unavailable/gapped or required volume data is missing; it does not certify feed completeness, session eligibility, or passage of the numeric gates. Unavailable Jump %, relative volume, and dollar volume remain `na`. Forming candles retain the last completed candle's values.

TradingView currently supports screener timeframes 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1D, 1W, and 1M, and evaluates only the last 500 bars. This intraday detector supports the intraday subset. Lookbacks are bounded to 200 bars and recent retention to 100, leaving room for warmup. Long-running chart episode/cooldown state can differ from a screener freshly initialized on 500 bars if an episode predates that window; current qualification has no such persistent state. Screening is separate from creating a running alert. See [Pine Screener requirements](https://www.tradingview.com/support/solutions/43000742436-tradingview-pine-screener-key-features-and-requirements/).

## Alerts

1. Add the indicator to a chart and configure inputs, symbol, timeframe, and loaded sessions.
2. Create an alert, select this indicator, and choose **Upward price jump**, **Downward price jump**, or **Any price jump**. Choose **Once Per Bar Close**. The first two conditions also serve as screener filters for the trigger bar.
3. Alternatively, choose **Any alert() function call** for a dynamic message containing direction, symbol, timeframe, price, and measured percentage. Choose one alert mechanism to avoid duplicate notifications.
4. Recreate running alerts after changing script code, inputs, symbol, timeframe, or session settings: TradingView keeps an alert configuration snapshot. See [Pine alerts](https://www.tradingview.com/pine-script-docs/concepts/alerts/).

Each direction fires once per qualifying episode and rearms after a completed nonqualifying bar. Optional cooldown is independent per direction and measured in bar-index distance; zero disables it. If a new episode starts during cooldown, it can fire on a later qualifying close after cooldown expires. If it ends first, it emits nothing. Recent signal flags retain emitted events, even if current qualification later ends; their retention includes the trigger bar.

Volume/session gates are off by default. Relative volume compares current-bar volume with the average of prior bars only; missing/zero baseline fails a required volume gate. Minimum dollar volume uses `volume * close`, a proxy assuming equity share volume, not executed notional. Missing volume does not block price-only signals. A restricted session uses New York time and only gates signal eligibility; the return/volume windows can include bars outside it. Enable extended-hours data in the chart/feed if you want premarket/after-hours observations; the script cannot manufacture absent bars. Use equivalent sessions/feed/as-of time when comparing screener and chart results.

## Validation

Source/static checks and local deterministic scenario checks are not Pine compilation. TradingView compiler, chart rendering, screener results, and live notification delivery must be checked there.

- On a 1m chart, compare Jump % against `(completed_close / close_five_bars_ago - 1) * 100`. Verify threshold equality and both signs.
- Lower the threshold temporarily and verify one marker per continuous qualifying episode, rearming, cooldown-delayed firing, and Recent retention.
- Check that a forming candle cannot alter screener numeric values or emit an alert.
- Check overnight/missing-bar rejection, short history, optional volume warmup, unavailable volume, and session boundaries.
- Compare chart Data Window columns and screener results using matching inputs/timeframe/feed/sessions. Test alerts on realtime bars; historical markers do not deliver historical notifications.

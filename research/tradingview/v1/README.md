# TradingView research indicators v1

Copy one complete `.pine` file into TradingView's Pine Editor, save it, and add it to a standard candle chart. Each file is standalone Pine Script v6. Start on a 1-second chart if your subscription/feed provides it; 5-second or 1-minute charts also work, with different results. Enable extended-hours data if you want premarket/after-hours observations. These are exploratory approximations, not certified repo signals or execution strategies.

## Structural zones

`structural_zones.pine` processes loaded chart bars once in chronological order, then updates on completed realtime bars. Default history is 30 calendar days measured from the latest chart bar. It cannot request or manufacture unavailable history. The table reports the used calendar span and processed bars; a span is not proof of uninterrupted data coverage. History loading and runtime depend on TradingView's plan, symbol, timeframe, and script limits; fast execution is not yet benchmarked.

Confirmed high/low pivots are clustered by percentage distance. A zone center is the average reaction price; its bounds cover the reaction-price range plus a minimum percentage width. Qualification requires a configurable reaction count. Pivot lengths are in chart bars: 10 confirmation bars means 10 seconds on 1s, 10 minutes on 1m. New reactions become available only after confirmation. This deliberately replaces v7's reversal extraction, Student-t MLE fitting, and evidence-based role transitions. Green means price above the zone, red below, orange inside.

The initial seed uses reactions within the requested historical window. During streaming, a zone expires after that many days without a reaction; a repeatedly refreshed zone retains its earlier accumulated geometry/count. This is an inactivity-retention window, **not an exact rolling removal of individual old samples**. Candidate capacity exhaustion raises a visible error rather than silently discarding candidates. Increase capacity/merge distance or shorten history if needed. Only the nearest qualified zones within the display distance are drawn, up to the explicit display count. All candidates still participate in clustering.

Zones are drawn from the current bar to the right: this is an as-of-now view, not historical zone timelines or a replay certification. Reloading recalculates the seed from the newly available window and may differ from a long-running instance. No quote data or external repo data is fetched.

## Jump / volume alerts

`jump_volume_alerts.pine` is a new configurable price/volume proxy, not a port of any immutable early-squeeze release. Choose percentage jump over a seconds window, close above the prior window's high, volume expansion, or jump OR breakout. Seconds are rounded up to whole chart bars. The gap check rejects windows spanning more elapsed time than requested plus one chart bar.

Optional gates require price above daily session VWAP and volume expansion against a prior-bars-only baseline. Minimum shares and approximate dollar volume (`volume * close`) apply to the current bar, so tune them for the chart timeframe. These proxies do not measure spread, quoted size, quote freshness, executable liquidity, or universe rank. Volume is assumed to be shares for equities; other instruments may report different units. Baseline can include prior sessions. VWAP resets at the exchange daily boundary and includes all loaded chart sessions; the alert session only controls eligibility. Defaults permit New York premarket and after-hours, weekdays as supplied by the chart.

Signals fire on the first confirmed bar of a newly qualifying episode, subject to cooldown. Persistent qualification does not repeatedly fire; an episode suppressed by cooldown needs a later false-to-true transition. Intrabar touches that fail by close produce no signal. Create an alert using **Qualified upward signal** or **Any alert() function call**, not both unless duplicate notifications are intended. Alerts created before changing inputs retain their saved configuration; recreate them after input/code changes.

## MACD shading

`macd_episodes.pine` uses chart-bar closes, custom EMA12/26/9 initialized from the first available close, and no synthetic updates for missing bars. Select MACD versus signal, MACD versus zero, or symmetric normalized gap thresholds. Threshold mode leaves a neutral unshaded region. EMA state depends on loaded history; feed, bar construction, and sessions can differ from canonical repo data.

Default shading holds the previous confirmed episode while the current bar forms. Disable that option for a live preview that can change before close. Optional daily reset follows the exchange daily boundary. Session restriction hides shading outside the selected New York session but still updates EMAs on every loaded bar. Episode alerts require confirmed bars.

## TradingView validation checklist

These files have been reviewed locally; compilation/runtime checks require TradingView's Pine compiler and are not claimed until performed there.

1. Compile each file separately; check 1s and 1m charts with extended hours enabled.
2. Structural: request 30 days and inspect actual coverage; test short history, no qualified zones, and a small capacity. A new pivot must wait its confirmation bars. Verify stable reload behavior over the same loaded dataset.
3. Signals: lower thresholds temporarily on a liquid equity, inspect markers, then create one alert. Verify PM/AH eligibility, VWAP/volume gates, overnight-gap rejection, cooldown, and one signal per qualifying episode.
4. MACD: compare the signal-line mode with a 12/26/9 MACD after warmup; check threshold neutrality, daily resets, and forming-bar behavior.
5. Treat changed timeframe/feed/settings as a different approximation; record them when comparing against repo sessions.

References: [Pine limits](https://www.tradingview.com/pine-script-docs/writing/limitations/), [bar states](https://www.tradingview.com/pine-script-docs/concepts/bar-states/), [alerts](https://www.tradingview.com/pine-script-docs/concepts/alerts/), [backgrounds](https://www.tradingview.com/pine-script-docs/visuals/backgrounds/).

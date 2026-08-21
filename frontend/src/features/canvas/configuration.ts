import type { CanvasChartTimeframe } from "../../app/canvasWorkspace";
import type { ChartCatalogKnowledge, ChartDisplayItem } from "../../app/components/ChartPanel";
import type { ChartsQuotesLayoutSettings } from "../../app/components/MarketMicrostructureContainers";
import type { MarketScannerSettings, SignalStreamSettings, StrategyActivitySettings, WatchUniverseSettings } from "../../app/components/MarketScreenerContainers";
import { TRADING_WORKSPACE_CONTAINERS, type WorkspaceContainerId } from "../../app/tradingWorkspace";
import type { XbrlAnalysisSettings } from "../../app/components/XbrlAnalysisContainer";
import type { ContainerSettings } from "./contracts";
export const ALL_CONTAINER_IDS = TRADING_WORKSPACE_CONTAINERS.map((definition) => definition.id);
export const MANAGER_DEFAULT_CONTAINER_IDS: WorkspaceContainerId[] = ["scanner", "chart", "portfolio", "positions", "orders"];
export const READ_ONLY_BLOCKED_CONTAINERS = new Set<WorkspaceContainerId>([
  "strategy",
  "portfolio",
  "positions",
  "orders",
  "fills",
  "closed_trades",
  "activity",
  "performance_journal",
]);
export const DEFAULT_WATCHLIST_TAB_IDS = ["top-large-cap-gainers", "top-mid-cap-gainers", "top-small-cap-gainers", "top-penny-gainers"];
export const DEFAULT_SETTINGS: ContainerSettings = {
  version: 28,
  chart: { showVolume: true, symbol: "AAPL", timeframe: "1m", visibleIndicators: ["indicator.vwap", "indicator.macd", "indicator.flow_structure_composite", "strategy.presentation"], barGptVersion: "v2", barGptQuantile: "q50", barGptHorizon: "all", barGptTriggerMode: "auto" },
  charts_quotes: {
    main: { showVolume: true, symbol: "AAPL", timeframe: "10s", visibleIndicators: ["indicator.macd", "strategy.presentation"], barGptVersion: "v2", barGptQuantile: "q50", barGptHorizon: "all", barGptTriggerMode: "auto" },
    month: { showVolume: true, symbol: "AAPL", timeframe: "1mo", visibleIndicators: [], barGptVersion: "v2", barGptQuantile: "q50", barGptHorizon: "all", barGptTriggerMode: "auto" },
    daily: { showVolume: true, symbol: "AAPL", timeframe: "1d", visibleIndicators: [], barGptVersion: "v2", barGptQuantile: "q50", barGptHorizon: "all", barGptTriggerMode: "auto" },
    layout: { lowerRowPercent: 33, monthColumnPercent: 40, reservedColumnPercent: 20, tapeColumnPercent: 20 },
  },
  microstructure: { limit: 1024 },
  fills: { limit: 5, showCommission: true },
  positions: { limit: 20, showPnl: true },
  closed_trades: { limit: 20, showFees: true },
  activity: { limit: 30 },
  performance_journal: { limit: 100, showRiskMultiple: true },
  news: { content: "all", endDate: "", kind: "all", limit: 100, lookbackHours: 6, rangeMode: "preset", startDate: "", ticker: "" },
  ticker_news: { lookbackHours: 72, showTeaser: true },
  news_detail: {},
  orders: { limit: 6, showOrderIds: true },
  portfolio: { showExposure: true, showPnl: true },
  scanner: { columns: [], customColumns: [], limit: 250, preset: "Core Scan" },
  signal_stream: { columns: [], customColumns: [], limit: 250, signalStreamHiddenIds: [], signalStreamId: "", signalStreamIds: [] },
  watchlist: { columns: [], customColumns: [], limit: 50, watchlistId: DEFAULT_WATCHLIST_TAB_IDS[0], watchlistIds: DEFAULT_WATCHLIST_TAB_IDS },
  strategy_activity: { eventType: "", limit: 250, runId: "", strategyId: "", ticker: "" },
  sec: { content: "all", endDate: "", label: "", limit: 100, lookbackHours: 168, rangeMode: "preset", startDate: "", ticker: "" },
  ticker_sec: { lookbackHours: 720 },
  sec_detail: {},
  strategy: { showSignals: true },
  xbrl: { metricLimit: 8, showRawTags: true },
};

export const HISTORICAL_TIMEFRAMES: CanvasChartTimeframe[] = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h", "1d", "1w", "1mo", "1y"];
export const ENRICHED_QMD_TIMEFRAMES = new Set<CanvasChartTimeframe>(["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"]);
export const MACRO_TIMEFRAMES = new Set<CanvasChartTimeframe>(["1d", "1w", "1mo", "1y"]);
export const INDICATOR_GUIDES: Record<string, ChartCatalogKnowledge> = {
  "strategy.presentation": indicatorGuide("Read saved strategy decisions and their active invalidation levels on the price chart.", "The strategy runtime persists each causal evaluation with its exact effective time, action, score, confidence, reference price, and invalidation. Canvas renders only records at or before the shared clock.", "Enter and add markers show confirmed long exposure decisions.", "Reduce, take-profit, and exit markers show exposure leaving the strategy campaign.", "The presentation follows the strategy event timestamp and is independent of the chart timeframe.", ["No historical marker is reconstructed from price alone.", "An armed strategy displays future decisions only after the runtime saves them."]),
  "indicator.vwap": indicatorGuide("Compare price with the extended session's volume-weighted typical price. VWAP is the purple price overlay, starts at 04:00 ET, and continues through 09:30 without resetting.", "From the 04:00 ET anchor, cumulatively divide Σ(HLC3 × eligible volume) by Σ(eligible volume), where HLC3 = (high + low + close) / 3 for each chart bar. This matches TradingView's default HLC3 source with a Session anchor when extended hours are shown.", "Price holding above a rising VWAP suggests buyers are accepting progressively higher prices; a reclaim that persists is stronger than a brief cross.", "Price holding below a falling VWAP suggests sellers control the session auction; repeated rejection at VWAP reinforces that evidence.", "VWAP is recomputed from the selected timeframe's HLC3 bars. Its anchor remains 04:00 ET on every intraday timeframe, but values can differ slightly between timeframes because each bar has different high, low, and close inputs.", ["VWAP is a benchmark, not automatic support or resistance.", "Opening and closing auctions or a few very large prints can materially shift it.", "A TradingView comparison must use the same extended-hours visibility, Session anchor, HLC3 source, and eligible market-data feed."]),
  "indicator.ema_9": movingAverageGuide("EMA 9", 9, "fast"),
  "indicator.ema_20": movingAverageGuide("EMA 20", 20, "short-term"),
  "indicator.ema_50": movingAverageGuide("EMA 50", 50, "intermediate"),
  "indicator.sma_20": indicatorGuide("Read the equally weighted mean of the latest 20 closes against current price and its own slope.", "Arithmetic mean of the latest 20 closed-bar prices; every observation has equal weight.", "Price above a rising SMA, especially after a successful retest, supports an advancing trend.", "Price below a falling SMA, especially after rejection from underneath, supports a declining trend.", "Twenty bars means 20 minutes on a 1-minute chart and 100 minutes on a 5-minute chart, so changing timeframe changes the economic horizon.", ["A moving average lags turning points.", "Repeated crosses in a flat market are noise, not repeated independent signals."]),
  "indicator.bollinger": indicatorGuide("Read price relative to the 20-bar mean and its volatility envelope. Band slope, width, and whether price accepts outside a band matter more than a single touch.", "Middle band is the 20-bar average; upper and lower bands are two rolling standard deviations above and below it.", "Rising bands with price walking the upper band indicate persistent upside expansion; a lower-band rejection followed by a middle-band reclaim can show recovery.", "Falling bands with price walking the lower band indicate downside expansion; an upper-band rejection followed by loss of the middle band can show renewed selling.", "The lookback always spans 20 selected-timeframe bars, so band width and reaction speed expand materially on higher timeframes.", ["Touching an outer band does not by itself mean overbought, oversold, or reversal.", "Volatility expansion can keep price outside a band longer than expected."]),
  "indicator.rsi": indicatorGuide("Read the balance of recent up and down closes on a 0–100 scale. Direction, regime, and divergences are more useful than fixed thresholds alone.", "Wilder-smoothed ratio of average gains to average losses over 14 closed bars, transformed to RSI = 100 − 100/(1 + RS).", "RSI holding above 50 and making higher lows supports positive momentum; recovery from below 30 matters most when price also stabilizes.", "RSI holding below 50 and making lower highs supports negative momentum; rejection after an overbought reading matters most when price also weakens.", "Fourteen bars means 14 minutes on 1-minute data and 70 minutes on 5-minute data; readings are not directly interchangeable across timeframes.", ["Overbought can describe strong trend continuation rather than an immediate short.", "Divergence can persist and requires price confirmation."]),
  "indicator.macd": indicatorGuide("Compare the fast and slow exponential trends, then compare their difference with its signal line. The histogram shows whether momentum is accelerating or decelerating.", "MACD line = EMA(12) − EMA(26); signal = EMA(9) of MACD; histogram = MACD − signal.", "MACD above signal and rising, especially above zero with an expanding positive histogram, supports strengthening upside momentum.", "MACD below signal and falling, especially below zero with an expanding negative histogram, supports strengthening downside momentum.", "All periods are bar counts. On a 1-minute chart the slow leg spans 26 minutes; on a 5-minute chart it spans 130 minutes.", ["Crossovers in a sideways market whipsaw frequently.", "A shrinking histogram signals deceleration, not necessarily reversal."]),
  "indicator.atr": indicatorGuide("Read recent trading range in price units. ATR describes movement capacity and risk, not direction.", "Wilder-smoothed 14-bar true range, where true range includes the current high-low and gaps from the previous close.", "Rising ATR accompanying an upside breakout supports expansion and helps size realistic stops or targets; ATR itself is not bullish.", "Rising ATR accompanying a downside break supports bearish expansion; falling ATR can precede compression but has no directional sign.", "ATR covers 14 selected-timeframe bars and is stated in dollars, so both horizon and magnitude change with timeframe and price level.", ["High ATR is not a buy or sell signal.", "Comparing raw ATR across differently priced securities is misleading without normalization."]),
  "indicator.bollinger_std": indicatorGuide("Read the dispersion of closes around their 20-bar mean. Rising values mean volatility expansion; falling values mean compression.", "Population-style rolling standard deviation used by the 20-bar Bollinger envelope, expressed in price units.", "Expansion during rising price confirms active upside movement, while very low compression can precede a breakout whose direction is still unknown.", "Expansion during falling price confirms active downside movement; the indicator alone cannot assign direction.", "The measure spans 20 selected-timeframe bars and naturally grows on higher timeframes or higher-priced securities.", ["Low volatility does not predict breakout direction.", "A one-bar shock can inflate the value after the move is already underway."]),
  "indicator.volume_sma": indicatorGuide("Compare current bar volume with the average volume of the previous 20 bars to judge participation.", "Arithmetic mean of eligible volume across the latest 20 closed bars.", "Upside price movement on volume above a rising average has stronger participation than the same move on thin volume.", "Downside price movement on volume above average shows stronger selling participation; low volume weakens either directional claim.", "The average covers 20 selected-timeframe bars. Intraday seasonality means opening volume should be compared carefully with midday volume.", ["Volume confirms participation, not direction by itself.", "Auctions, news, and condition eligibility can create exceptional bars."]),
  "indicator.return": indicatorGuide("Read the signed close-to-close change for one completed chart bar. It is the most local realized price response.", "Current close divided by previous close minus one, shown as a signed return.", "Positive returns that persist and agree with volume or microstructure pressure support short-term continuation.", "Negative returns that persist and agree with selling pressure support short-term continuation lower.", "One bar means the selected timeframe exactly; a 100 ms return and a 5-minute return answer very different questions.", ["This is realized movement, not a forward forecast.", "One isolated return can be a gap, bad print, or temporary liquidity event."]),
  "indicator.price_ema": indicatorGuide("Read the percentage distance between price and EMA 20 to see extension relative to the short-term trend.", "100 × (close − EMA20) / EMA20.", "A positive distance that grows with a rising EMA supports upside momentum; a controlled pullback toward zero can be a trend retest.", "A negative distance that grows with a falling EMA supports downside momentum; rejection near zero can reinforce resistance.", "EMA 20 spans 20 selected-timeframe bars, so the same percentage has different persistence across timeframes.", ["Large distance can mean trend strength or late-stage overextension.", "Use slope and volatility before treating zero as support or resistance."]),
  "indicator.price_vwap": indicatorGuide("Read the percentage distance between price and the 04:00 ET anchored session VWAP to measure where the current auction sits versus extended-session volume-weighted consensus.", "100 × (close − session VWAP) / session VWAP.", "A sustained positive distance with rising VWAP indicates acceptance above session value.", "A sustained negative distance with falling VWAP indicates acceptance below session value.", "Both close and the HLC3 inputs follow the selected chart timeframe. The VWAP anchor remains 04:00 ET and does not reset at 09:30.", ["A large distance can be momentum or temporary extension.", "Premarket prints can retain substantial influence after the regular open, especially in actively traded movers."]),
  "indicator.trend_score": indicatorGuide("Read the combined direction and agreement of the configured trend inputs on a normalized negative-to-positive scale.", "Composite normalization of price location and moving-trend evidence; positive components add bullish weight and negative components add bearish weight.", "A positive score that strengthens and remains supported by price above its trend references indicates aligned upside structure.", "A negative score that weakens further and remains supported by price below trend references indicates aligned downside structure.", "Every component is calculated from the selected timeframe, so higher timeframes produce slower and usually more persistent scores.", ["A composite can hide disagreement between its inputs.", "Inspect the underlying averages and price response before acting on the score alone."]),
};
export const CHART_INDICATORS: ChartDisplayItem[] = [
  displayIndicator("model.bargpt.forecast.candles", "BarGPT · Forecast Candles", "model_forecast", [], "price"),
  displayIndicator("model.bargpt.forecast.open", "BarGPT · Forecast Open", "model_forecast", [], "price"),
  displayIndicator("model.bargpt.forecast.high", "BarGPT · Forecast High", "model_forecast", [], "price"),
  displayIndicator("model.bargpt.forecast.low", "BarGPT · Forecast Low", "model_forecast", [], "price"),
  displayIndicator("model.bargpt.forecast.close", "BarGPT · Forecast Close", "model_forecast", [], "price"),
  displayIndicator("strategy.presentation", "Long Campaign · Strategy Decisions", "price_action", [], "price", INDICATOR_GUIDES["strategy.presentation"]),
  displayIndicator("indicator.vwap", "VWAP", "volume_liquidity", ["vwap"]),
  displayIndicator("indicator.ema_9", "EMA 9", "momentum", ["ema_9"]),
  displayIndicator("indicator.ema_20", "EMA 20", "momentum", ["ema_20"]),
  displayIndicator("indicator.ema_50", "EMA 50", "momentum", ["ema_50"]),
  displayIndicator("indicator.sma_20", "SMA 20", "momentum", ["close_sma_20"]),
  displayIndicator("indicator.bollinger", "Bollinger Bands (20, 2)", "volatility", ["bollinger_mid_20", "bollinger_upper_20", "bollinger_lower_20"]),
  displayIndicator("indicator.rsi", "RSI 14", "momentum", ["rsi_14"], "rsi"),
  displayIndicator("indicator.macd", "MACD (12, 26, 9)", "momentum", ["macd_line", "macd_signal", "macd_histogram"], "macd"),
  displayIndicator("indicator.atr", "ATR 14", "volatility", ["atr_14"], "atr"),
  displayIndicator("indicator.bollinger_std", "Bollinger Std Dev", "volatility", ["bollinger_std_20"], "bollinger_std"),
  displayIndicator("indicator.volume_sma", "Volume SMA 20", "volume_liquidity", ["volume_sma_20"], "volume"),
  displayIndicator("indicator.return", "1-bar Return", "price_action", ["return_1_bar"], "return"),
  displayIndicator("indicator.price_ema", "Price vs EMA 20", "momentum", ["price_vs_ema20_pct"], "distance"),
  displayIndicator("indicator.price_vwap", "Price vs VWAP", "volume_liquidity", ["price_vs_vwap_pct"], "distance"),
  displayIndicator("indicator.trend_score", "Trend Score", "momentum", ["trend_score"], "trend"),
  displayIndicator(
    "indicator.flow_structure_composite",
    "Flow-Structure Composite · Oscillator",
    "microstructure",
    [
      "flow_structure_composite_score",
      "flow_structure_composite_confidence",
      "flow_structure_composite_bias",
      "flow_structure_composite_reason",
    ],
    "microstructure",
    {
      bearishEvidence: "A negative composite with rising confidence means event-native selling pressure and causal structural context lean lower.",
      bullishEvidence: "A positive composite with rising confidence means event-native buying pressure and causal structural context lean higher.",
      calculation: "Each closed 100 ms observation confidence-weights the unified microstructure score against Generic Structure and structural pressure. Agreement preserves confidence, conflict discounts it, and weak evidence remains visible instead of being vetoed to zero. Larger display bars summarize the canonical 100 ms values by confidence-weighted consensus.",
      shortDescription: "A continuous, causal flow-versus-structure bias from -1 to +1.",
      detailedDescription: "The composite is an indicator, not an entry instruction. It reports bullish, bearish, or neutral bias and exposes confidence plus the reason for the current relationship.",
      interpretation: "Read the signed score against zero, then confidence. Stronger color means the directional evidence is both larger and more reliable.",
      readingGuide: "Use the composite to inspect alignment. The separately scored Flow-Structure Alignment market signal adds the 3-of-5 persistence rule used for scanner ranking and chart markers.",
      timeframeBehavior: "The canonical calculation closes every 100 ms. Higher chart timeframes display a confidence-weighted consensus of those non-overlapping observations.",
      caveats: [
        "This is a deterministic microstructure estimate, not a guaranteed price forecast.",
        "Each 100 ms value is final after its originating bucket closes; a larger display bar can still accumulate a different consensus until it closes.",
        "Sparse bars receive lower confidence because classification and quote coverage are weaker.",
        "Displayed NBBO and eligible trades do not reveal all hidden liquidity or execution intent.",
      ],
    },
  ),
  displayIndicator(
    "indicator.qmd_market_signals",
    "QMD Market Signals",
    "microstructure",
    [],
    "price",
    {
      shortDescription: "Reusable causal market observations emitted by QMD before any strategy decides whether to trade.",
      detailedDescription: "Each marker is a versioned QMD signal event with a stable type, lifecycle state, direction, working timeframe, score, confidence, evidence, and exact effective timestamp.",
      calculation: "The same QMD state machine evaluates closed causal market-data buckets in live and historical paths. It emits Triggered when a rule first holds, Updated when material evidence changes, and Resolved when the setup no longer holds. The chart draws Triggered events only to avoid update noise.",
      readingGuide: "Green arrows are bullish observations and red arrows are bearish observations. The percentage is evidence confidence; the suffix is the originating working timeframe. A marker is not an order or a promised return.",
      bullishEvidence: "A green marker means the named QMD rule observed aligned bullish evidence at that exact market timestamp.",
      bearishEvidence: "A red marker means the named QMD rule observed aligned bearish evidence at that exact market timestamp.",
      interpretation: "Treat the event as reusable evidence. A strategy may combine it with structure, risk, portfolio state, and its own timing rules to emit Enter, Exit, Hold, or Wait.",
      timeframeBehavior: "The event keeps its originating working timeframe regardless of chart timeframe. On a larger chart it is placed on the candle containing its effective timestamp; the chart never waits for that larger candle to close.",
      caveats: [
        "Confidence measures evidence completeness and agreement, not win probability.",
        "QMD owns reusable market observations; strategies remain the authority for entries, exits, sizing, and orders.",
        "Resolved and Updated events remain available in the API and Signal Stream but are not drawn as repeated chart arrows.",
      ],
    },
  ),
  displayIndicator("indicator.qmd_transaction_imbalance", "QMD Transaction Imbalance", "microstructure", ["microstructure_transaction_imbalance", "microstructure_buy_trade_count", "microstructure_sell_trade_count"], "qmd_transaction", qmdIndicatorKnowledge("Buy-versus-sell trade-count imbalance", "Counts eligible prints classified at the ask as buys and at the bid as sells, then computes (buys - sells) / classified trades.", "Persistent positive readings mean buyer-initiated prints are arriving more often; negative readings mean seller-initiated prints dominate.", "It ignores trade size, so compare it with Signed-volume Imbalance.")),
  displayIndicator("indicator.qmd_signed_volume", "QMD Signed-volume Imbalance", "microstructure", ["microstructure_signed_volume_imbalance", "microstructure_buy_volume", "microstructure_sell_volume"], "qmd_signed_volume", qmdIndicatorKnowledge("Buy-versus-sell executed-volume imbalance", "Sums eligible volume at the ask and bid inside the selected bar, then computes (buy volume - sell volume) / classified volume.", "Positive values show aggressive buy volume; negative values show aggressive sell volume. Agreement with transaction imbalance is stronger evidence than either alone.", "A few large prints can dominate the value; inspect trade conditions and resiliency.")),
  displayIndicator("indicator.qmd_level1_ofi", "QMD Level-1 OFI", "microstructure", ["microstructure_level1_ofi"], "qmd_level1_ofi", qmdIndicatorKnowledge("Best-quote order-flow imbalance", "Measures price-improving and size-changing flow at the NBBO, normalized by exposed best-level depth and aggregated from raw quote transitions.", "Positive OFI indicates bid support or ask withdrawal; negative OFI indicates bid withdrawal or ask supply.", "Displayed orders can be cancelled and do not reveal deeper or hidden liquidity.")),
  displayIndicator("indicator.qmd_anchored_flow", "QMD Anchored OFI + Trade Delta", "microstructure", ["microstructure_cumulative_level1_ofi", "microstructure_cumulative_signed_volume_delta", "microstructure_anchored_flow_relationship", "microstructure_anchored_flow_relationship_score", "microstructure_level1_ofi_delta", "microstructure_signed_volume_delta"], "qmd_anchored_flow", {
    bearishEvidence: "Bearish confirmation: solid OFI and dashed Trade Delta are both below zero and falling. Bearish absorption: OFI is negative while Trade Delta is positive, meaning aggressive buyers are being met by strengthening offers or retreating bids; this can precede failure if price also stops advancing.",
    bullishEvidence: "Bullish confirmation: solid OFI and dashed Trade Delta are both above zero and rising. Bullish absorption: OFI is positive while Trade Delta is negative, meaning aggressive sellers are being met by strengthening bids or retreating offers; this can precede recovery if price also stops falling.",
    calculation: "The gateway starts both accumulators from zero once at 04:00 ET, then sums raw Level-1 OFI increments and raw classified buy-minus-sell volume through the New York market session. There is no 09:30 reset. Higher timeframes add the same underlying 100 ms sufficient statistics without averaging ratios.",
    caveats: ["The single anchor is 04:00 ET, so absolute magnitude grows with elapsed session activity; compare slope and regime changes, not only the final number.", "The cumulative right axis includes zero and the extrema of all currently loaded points, so panning does not rescale the lines; newly streamed or newly loaded extrema can expand it.", "The first plotted closed bar already includes that bar's flow; zero is the baseline immediately before the first 04:00 interval.", "OFI observes consolidated best quotes, not deeper or hidden liquidity, and quote cancellation can create pressure without execution.", "Trade Delta excludes unclassified or ineligible prints, so it is not total market volume.", "Relationship bars encode states, not probabilities or forecast confidence."],
    components: [
      { description: "Share-equivalent net pressure from changes at the consolidated best bid and ask since 04:00 ET. Above zero favors bid reinforcement or offer removal; below zero favors ask reinforcement or bid removal.", label: "Solid line · Cumulative OFI", tone: "info" },
      { description: "Eligible at-ask volume minus at-bid volume since 04:00 ET. Above zero means net aggressive buying; below zero means net aggressive selling.", label: "Dashed line · Cumulative Trade Delta", tone: "warning" },
      { description: "+1 green = bullish confirmation; −1 red = bearish confirmation; +0.55 cyan = bullish absorption; −0.55 amber = bearish absorption; 0 gray = neutral. These bars use the left Relationship scale.", label: "Background bars · Relationship state", tone: "neutral" },
      { description: "The reference point for both cumulative lines. A crossing shows that net pressure since 04:00 changed sign; it is not by itself a trade entry.", label: "Zero baseline", tone: "neutral" },
    ],
    detailedDescription: "The solid OFI line measures cumulative displayed NBBO pressure in share-equivalent units. The dashed Trade Delta line measures cumulative buyer-initiated minus seller-initiated eligible volume in shares. Both use one zero-inclusive right scale locked to the loaded series, while the background bars use a separate −1 to +1 left scale.",
    interpretation: "Green means bullish confirmation; red means bearish confirmation; cyan means bullish absorption (positive OFI, negative Trade Delta); amber means bearish absorption (negative OFI, positive Trade Delta); gray means one side is neutral.",
    readingGuide: "First read the relationship bars for confirmation versus absorption. Then inspect each line's sign and slope: rising is becoming more positive, falling is becoming more negative. Finally compare price response. Agreement plus matching price movement confirms pressure; disagreement or pressure without price response suggests absorption.",
    shortDescription: "Session-anchored displayed-liquidity pressure and executed aggressive-flow delta in one relationship oscillator.",
    timeframeBehavior: "Each chart bar contributes its raw OFI and signed-volume deltas to the one 04:00 ET session anchor. The gateway maintains the cumulative values once, so changing chart timeframe preserves the same economic total at aligned endpoints.",
  }),
  displayIndicator("indicator.qmd_queue_imbalance", "QMD Queue Imbalance", "microstructure", ["microstructure_queue_imbalance"], "qmd_queue", qmdIndicatorKnowledge("Displayed bid-versus-ask queue balance", "Averages (bid size - ask size) / (bid size + ask size) across quote observations in the selected bar.", "Positive readings mean more displayed size at the bid; negative readings mean more at the ask.", "Queue size is intention, not execution, and is vulnerable to cancellation.")),
  displayIndicator("indicator.qmd_microprice_lean", "QMD Microprice Lean", "microstructure", ["microstructure_microprice_lean"], "qmd_microprice", qmdIndicatorKnowledge("Size-weighted price location inside the spread", "Compares microprice with midpoint and normalizes the difference by half the spread.", "Positive lean means the ask queue is thinner and an upward move may be easier; negative lean means the bid is thinner.", "It is most useful when the spread is valid and the displayed queues persist.")),
  displayIndicator("indicator.qmd_recent_returns", "QMD Recent Midpoint & Trade Return", "microstructure", ["microstructure_midpoint_return_bps", "microstructure_trade_return_bps"], "qmd_returns", qmdIndicatorKnowledge("Realized price response within each chart bar", "Shows first-to-last midpoint and eligible-trade returns in basis points for exactly the selected timeframe.", "Agreement between flow and return suggests continuation; strong flow with little return can indicate absorption.", "This is realized response, not a future-return target.")),
  displayIndicator("indicator.qmd_aggressor_persistence", "QMD Aggressor Persistence", "microstructure", ["microstructure_aggressor_persistence"], "qmd_persistence", qmdIndicatorKnowledge("Directional consistency of classified trades", "Averages the signed aggressor sequence: at-ask trades are +1 and at-bid trades are -1.", "Values near +1 or -1 indicate highly one-sided execution; values near zero indicate mixed flow.", "Persistence without price response may be absorption rather than continuation.")),
  displayIndicator("indicator.qmd_arrival_intensity", "QMD Arrival-intensity Imbalance", "microstructure", ["microstructure_arrival_intensity_imbalance", "microstructure_arrival_rate_per_second"], "qmd_arrival", qmdIndicatorKnowledge("Direction of information arrival", "Combines directional quote transitions and classified trade arrivals, while retaining total arrivals per second as an activity diagnostic.", "A directional imbalance with a rising arrival rate signals urgent pressure; low-rate readings deserve less weight.", "Bursts can be fleeting and should be confirmed by price response or OFI.")),
  displayIndicator("indicator.qmd_resiliency", "QMD Liquidity Resiliency", "microstructure", ["microstructure_resiliency"], "qmd_resiliency", qmdIndicatorKnowledge("How displayed liquidity replenishes after depletion", "Compares same-side best-level replenishment with depletion across raw quote transitions and signs the result by the side recovering more effectively.", "Positive values favor bid recovery; negative values favor ask recovery. Near zero means balanced or insufficient recovery evidence.", "NBBO-only resiliency cannot observe deeper-book replenishment.")),
  displayIndicator("indicator.qmd_reference_levels", "QMD Reference Levels", "price_action", [
    "qmd_structure_session_high", "qmd_structure_session_low",
    "qmd_structure_opening_range_high", "qmd_structure_opening_range_low", "qmd_structure_trade_volume_poc",
    "qmd_structure_luld_upper", "qmd_structure_luld_lower", "qmd_structure_52_week_high", "qmd_structure_52_week_low",
    "qmd_structure_prior_month_high", "qmd_structure_prior_month_low", "qmd_structure_prior_month_close",
  ], "price", indicatorGuide(
    "Independent auction and regulatory reference levels; they are context, not Generic Structure evidence.",
    "Samples the eligible-trade high and low across the complete 04:00-20:00 New York extended session, plus opening range, eligible-trade volume POC, estimated LULD, and completed higher-timeframe levels from QMD's causal state. Estimated LULD upper and lower values render as continuous stepped lines because QMD updates the rolling five-minute estimate discretely; missing or inactive observations remain true gaps.",
    "Holding above an important accepted reference can support bullish context when flow confirms.",
    "Rejecting below an important reference can support bearish context when flow confirms.",
    "The underlying references are timestamp-driven; the selected chart timeframe changes only sampling density.",
    ["Estimated LULD is a rule-based estimate, not an exchange status message.", "A reference level is not automatically support or resistance."],
  )),
  displayIndicator("indicator.qmd_generic_structure", "QMD Generic Structure", "price_action", [
    "qmd_structure_score", "qmd_structure_direction", "qmd_structure_agreement", "qmd_structure_strength", "qmd_structure_confidence",
    "qmd_structure_support_price", "qmd_structure_support_lower", "qmd_structure_support_upper", "qmd_structure_support_strength", "qmd_structure_support_confidence",
    "qmd_structure_resistance_price", "qmd_structure_resistance_lower", "qmd_structure_resistance_upper", "qmd_structure_resistance_strength", "qmd_structure_resistance_confidence",
    "qmd_structure_active_levels", "qmd_structure_timeframe_states",
    "qmd_structure_developing_high", "qmd_structure_developing_low", "qmd_structure_developing_direction",
    "qmd_structure_event_id", "qmd_structure_event_pivot_at_ms", "qmd_structure_event_at_ms", "qmd_structure_event_kind", "qmd_structure_event_timeframe", "qmd_structure_event_direction", "qmd_structure_event_price",
  ], "price", {
    shortDescription: "Exact eligible-trade price levels plus a separate causal local swing and break hierarchy for every supported timeframe.",
    detailedDescription: "QMD has two related authorities. The immediate level book updates from every ordered eligible trade and retains price/volume evidence without waiting for a candle. Separately, each timeframe groups those same trades into fixed event-time buckets using the exact highest and lowest executed prices. A completed three-bucket neighborhood confirms the middle bucket only when it is a local high or low. Quotes may add liquidity context, but an unexecuted quote cannot create a swing, BoS, or CHoCH.",
    calculation: "For a selected timeframe, the middle completed trade bucket is a swing high when its exact high is at least the prior bucket high and strictly above the following bucket high; swing lows use the inverse rule. The last bucket in a same-price plateau owns the pivot, preventing duplicates. Confirmation occurs only after the following bucket is complete, so history never repaints. Only the latest confirmed local high and low can generate that timeframe's break. The first eligible trade through it emits Crossing; a second confirming trade or 100 ms of persistence emits the accepted Break, BoS, or CHoCH.",
    readingGuide: "The chart enables the swing and break layers matching its current timeframe by default. Other timeframe layers start hidden to keep the chart readable, but their eye controls remain available so you can overlay and compare them; each manual visibility choice is persisted independently. History is counted in the enabled layer's own bars, so 20 bars of 5-minute structure means 100 minutes even on a 1-second chart. The latest still-active high and low for each timeframe are carried into a shorter visible chart page. SH and SL lines are bounded: they start at the exact pivot trade and end when crossed or when a newer same-side local swing supersedes them. BoS continues the last accepted break direction; CHoCH is the first accepted break in the opposite direction. The pivot time shows where the extreme occurred, while the tooltip's later confirmation time is the earliest moment a strategy could have known it. Current support/resistance and its volume footprint remain a separate immediate level-book view.",
    bullishEvidence: "Bullish evidence increases when resistance is crossed and accepted, an upward BoS or CHoCH is confirmed for the selected timeframe, support survives retests, and buyer-initiated footprint volume concentrates at or above the level.",
    bearishEvidence: "Bearish evidence increases when support is crossed and accepted, a downward BoS or CHoCH is confirmed for the selected timeframe, resistance survives retests, and seller-initiated footprint volume concentrates at or below the level.",
    timeframeBehavior: "All intervals consume the same ordered eligible trades, but each interval owns its local extrema and break state. The timeframe controls the event-time neighborhood used to confirm a swing; it does not resample chart candle closes or inherit another timeframe's breaks. A 1-second BoS therefore breaks the latest confirmed 1-second swing, while 5-second and 1-minute structure remain independent.",
    components: [
      { label: "SH / SL · Local swings", description: "The exact highest or lowest eligible trade in a confirmed three-bucket local neighborhood for the selected timeframe. Lines stop at a break or at the next same-side swing.", tone: "neutral" },
      { label: "Developing high / low", description: "The exact highest or lowest eligible trade in the currently developing move. It has zero extraction delay but remains provisional until an opposing trade freezes it.", tone: "info" },
      { label: "Crossing", description: "The first eligible trade through a level. It is immediate and causal, but not yet evidence that price accepted the break.", tone: "warning" },
      { label: "Accepted break", description: "A later eligible trade confirms the crossed side, or the cross persists for 100 ms. A return first cancels the pending crossing without changing structure.", tone: "neutral" },
      { label: "BoS", description: "Break of Structure: an accepted break in the selected timeframe's established direction.", tone: "buy" },
      { label: "CHoCH", description: "Change of Character: the first accepted break against the selected timeframe's established direction. It is reversal evidence, not a guaranteed reversal.", tone: "warning" },
      { label: "Level footprint", description: "Executed volume within four ticks of the level, split into buyer-, seller-, and neutral-initiated volume. The nine bins show where trading actually concentrated around the reference.", tone: "info" },
      { label: "Retest / role reversal", description: "A broken level stays historical. It changes from support to resistance, or the reverse, only after a later retest from the opposite side is rejected.", tone: "warning" },
      { label: "Strength", description: "Accumulated causal evidence from survival, touches, holds, accepted breaks, retests, and traded volume. It contributes to strongest-level selection.", tone: "info" },
      { label: "Confidence", description: "Evidence repeatability and freshness for the level at that event time. It controls borderless region density and is not a forecast probability.", tone: "warning" },
      { label: "Auction references", description: "Unified 04:00-20:00 New York eligible-trade extrema, opening range, eligible-trade volume POC, estimated LULD, completed 52-week/prior-month levels, and round prices remain a separate reference-level package.", tone: "neutral" },
    ],
    caveats: ["QMD observes consolidated Level-1 NBBO and eligible prints, not full venue depth or hidden liquidity.", "A local swing is unknowable at its pivot instant; it becomes causal only after the following timeframe bucket completes. Strategies must use confirmed_at, never pivot_at.", "Nearest means absolute distance from current price. Strongest combines causal strength and confidence; it does not necessarily mean closest or most likely to hold.", "The footprint classifies aggressor side from available trade and NBBO evidence and therefore cannot reveal hidden orders.", "BoS, CHoCH, support, and resistance are deterministic evidence states—not trade instructions or win probabilities."],
  }),
  {
    ...displayIndicator("indicator.qmd_level_footprint", "QMD Level Volume Footprint", "price_action", [
      "qmd_structure_active_levels",
    ], "price", {
      shortDescription: "Executed-volume evidence shown either as a right-axis price profile or as two scale-aware buyer/seller rails at each causal swing.",
      detailedDescription: "All encountered levels keeps the latest causal session-volume snapshot for each exact price exposed around every level in loaded history. Identical prices are unioned without double counting and nearby prices are combined only when the current zoom would draw them in the same screen row. Swing rails use the volume stored on each selected-timeframe swing event. They occupy a reserved lane beyond the SH or SL label so the structure label and footprint remain independently readable.",
      calculation: "For every eligible trade, QMD classifies aggressor side from trade and NBBO evidence and accumulates total, buyer-initiated, seller-initiated, and neutral volume by exact tick price. The profile draws those absolute volumes by price; its visible-length reference is the 95th-percentile screen bin so one exceptional print cannot flatten the rest of the distribution. Values beyond that reference are capped visually, not numerically. At a swing: buy share = buy volume / total volume and sell share = sell volume / total volume. Unfilled track is neutral or unclassified volume.",
      readingGuide: "In All encountered levels mode, read the profile inward from the right price axis: longer rows identify prices where more eligible volume accumulated. Green, gray, and red divide buyer-, neutral-, and seller-initiated volume. Blank prices have no exposed eligible volume and are never filled artificially. In Swing buy/sell rails mode, both tracks begin at the center of the swing candle and extend rightward; the upper track is buyer share and the lower track is seller share. They move with the swing price and resize with chart spacing. A long green rail and short red rail means aggressive buyers dominated that swing bucket; the reverse means aggressive sellers dominated. Always confirm the subsequent price response.",
      bullishEvidence: "Buyer-dominant volume at a swing low or support, followed by price holding or advancing, supports acceptance or bullish absorption evidence.",
      bearishEvidence: "Seller-dominant volume at a swing high or resistance, followed by rejection or decline, supports supply or bearish absorption evidence.",
      timeframeBehavior: "The all-level profile is event-native and independent of chart candles. Swing rails show only causal swing occurrences for the selected chart timeframe, but their volume comes from the underlying eligible trades inside that timeframe bucket rather than candle direction.",
      components: [
        { label: "Right-axis price profile", description: "Absolute eligible volume for every exposed price in loaded causal history, aligned immediately inside the right price axis. Screen rows combine only when the current y-scale cannot display their prices separately.", tone: "info" },
        { label: "Upper buyer rail", description: "Buyer-initiated eligible volume divided by total eligible volume in the exact bucket that formed the swing.", tone: "buy" },
        { label: "Lower seller rail", description: "Seller-initiated eligible volume divided by total eligible volume in the exact bucket that formed the swing.", tone: "sell" },
        { label: "Unfilled rail track", description: "Neutral or unclassified eligible volume. It remains unfilled rather than being assigned to buy or sell.", tone: "neutral" },
      ],
      caveats: ["The profile contains only eligible consolidated trades, not hidden liquidity or full depth.", "High volume can mean acceptance or a contested level; price response determines which.", "Aggressor classification can be neutral when trade and NBBO evidence is insufficient.", "Rail percentages describe the swing bucket's executed-flow composition, not the probability that the level will hold."],
    }),
    presetOptions: [
      { value: "axis-history", label: "All encountered levels", description: "Right-axis absolute buyer, neutral, and seller volume profile for prices exposed in loaded causal history." },
      { value: "swing-rails", label: "Swing buy/sell rails", description: "Scale-aware buyer and seller shares that begin at the swing-candle center and extend rightward in a reserved label-safe lane." },
    ],
  },
];

export const INDICATOR_SERIES = [
  { column: "vwap", color: "var(--warning)", displayItemId: "indicator.vwap", label: "VWAP", pane: "price" },
  { column: "ema_9", color: "var(--info)", displayItemId: "indicator.ema_9", label: "EMA 9", pane: "price" },
  { column: "ema_20", color: "var(--primary)", displayItemId: "indicator.ema_20", label: "EMA 20", pane: "price" },
  { column: "ema_50", color: "var(--danger)", displayItemId: "indicator.ema_50", label: "EMA 50", pane: "price" },
  { column: "close_sma_20", color: "var(--success)", displayItemId: "indicator.sma_20", label: "SMA 20", pane: "price" },
  { column: "bollinger_mid_20", color: "var(--primary)", displayItemId: "indicator.bollinger", label: "Bollinger Mid", pane: "price" },
  { column: "bollinger_upper_20", color: "var(--info)", displayItemId: "indicator.bollinger", label: "Bollinger Upper", pane: "price" },
  { column: "bollinger_lower_20", color: "var(--info)", displayItemId: "indicator.bollinger", label: "Bollinger Lower", pane: "price" },
  { column: "rsi_14", color: "var(--primary)", displayItemId: "indicator.rsi", label: "RSI 14", pane: "rsi" },
  { column: "macd_line", color: "var(--info)", displayItemId: "indicator.macd", label: "MACD", pane: "macd" },
  { column: "macd_signal", color: "var(--warning)", displayItemId: "indicator.macd", label: "Signal", pane: "macd" },
  { column: "macd_histogram", color: "var(--success)", displayItemId: "indicator.macd", label: "Histogram", pane: "macd", style: "histogram" },
  { column: "atr_14", color: "var(--warning)", displayItemId: "indicator.atr", label: "ATR 14", pane: "atr" },
  { column: "bollinger_std_20", color: "var(--info)", displayItemId: "indicator.bollinger_std", label: "Bollinger Std Dev", pane: "bollinger_std" },
  { column: "volume_sma_20", color: "var(--primary)", displayItemId: "indicator.volume_sma", label: "Volume SMA 20", pane: "volume" },
  { column: "return_1_bar", color: "var(--success)", displayItemId: "indicator.return", label: "1-bar Return", pane: "return", style: "histogram" },
  { column: "price_vs_ema20_pct", color: "var(--info)", displayItemId: "indicator.price_ema", label: "Price vs EMA 20", pane: "distance" },
  { column: "price_vs_vwap_pct", color: "var(--warning)", displayItemId: "indicator.price_vwap", label: "Price vs VWAP", pane: "distance" },
  { column: "trend_score", color: "var(--primary)", displayItemId: "indicator.trend_score", label: "Trend Score", pane: "trend" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Composite", colorMode: "confidence-sign", column: "flow_structure_composite_score", color: "var(--foreground)", displayItemId: "indicator.flow_structure_composite", label: "Composite", lineWidth: 3, pane: "flow_structure_composite", priceScaleId: "right", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Confidence", column: "flow_structure_composite_confidence", color: "var(--primary)", displayItemId: "indicator.flow_structure_composite", label: "Confidence", lineStyle: "dashed", lineWidth: 2, opacity: 0.82, pane: "flow_structure_composite", priceScaleId: "left" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Imbalance", column: "microstructure_transaction_imbalance", color: "var(--foreground)", displayItemId: "indicator.qmd_transaction_imbalance", label: "Transaction imbalance", pane: "qmd_transaction", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Imbalance", column: "microstructure_signed_volume_imbalance", color: "var(--foreground)", displayItemId: "indicator.qmd_signed_volume", label: "Signed volume", pane: "qmd_signed_volume", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "OFI", column: "microstructure_level1_ofi", color: "var(--foreground)", displayItemId: "indicator.qmd_level1_ofi", label: "Level-1 OFI", pane: "qmd_level1_ofi", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Relationship", column: "microstructure_anchored_flow_relationship", color: "var(--muted-foreground)", displayItemId: "indicator.qmd_anchored_flow", label: "Relationship", opacity: 0.24, pane: "qmd_anchored_flow", priceScaleId: "left", style: "histogram" },
  { autoscaleScope: "loaded-series", axisTitle: "Cum. OFI", column: "microstructure_cumulative_level1_ofi", color: "var(--info)", displayItemId: "indicator.qmd_anchored_flow", label: "Cumulative OFI", lineWidth: 3, pane: "qmd_anchored_flow", priceScaleId: "right" },
  { autoscaleScope: "loaded-series", axisTitle: "Trade Δ", column: "microstructure_cumulative_signed_volume_delta", color: "var(--warning)", displayItemId: "indicator.qmd_anchored_flow", label: "Cumulative Trade Delta", lineStyle: "dashed", lineWidth: 3, pane: "qmd_anchored_flow", priceScaleId: "right" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Queue", column: "microstructure_queue_imbalance", color: "var(--foreground)", displayItemId: "indicator.qmd_queue_imbalance", label: "Queue imbalance", pane: "qmd_queue", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Lean", column: "microstructure_microprice_lean", color: "var(--foreground)", displayItemId: "indicator.qmd_microprice_lean", label: "Microprice lean", pane: "qmd_microprice", style: "histogram" },
  { axisTitle: "bps", column: "microstructure_midpoint_return_bps", color: "var(--info)", displayItemId: "indicator.qmd_recent_returns", label: "Midpoint return", pane: "qmd_returns" },
  { axisTitle: "bps", column: "microstructure_trade_return_bps", color: "var(--warning)", displayItemId: "indicator.qmd_recent_returns", label: "Trade return", pane: "qmd_returns" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Persistence", column: "microstructure_aggressor_persistence", color: "var(--foreground)", displayItemId: "indicator.qmd_aggressor_persistence", label: "Aggressor persistence", pane: "qmd_persistence", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Imbalance", column: "microstructure_arrival_intensity_imbalance", color: "var(--foreground)", displayItemId: "indicator.qmd_arrival_intensity", label: "Arrival imbalance", pane: "qmd_arrival", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Resiliency", column: "microstructure_resiliency", color: "var(--foreground)", displayItemId: "indicator.qmd_resiliency", label: "Liquidity resiliency", pane: "qmd_resiliency", style: "histogram" },
] as const;

export function displayIndicator(id: string, title: string, group: string, sourceColumns: string[], pane = "price", knowledge?: ChartDisplayItem["knowledge"]): ChartDisplayItem {
  return { category: pane === "price" ? "Price overlay" : "Oscillator pane", group, id, knowledge: knowledge ?? INDICATOR_GUIDES[id], presentation: { chartRole: pane === "price" ? "overlay" : "oscillator", pane, selectable: true }, sourceColumns, title };
}
export function indicatorGuide(readingGuide: string, calculation: string, bullishEvidence: string, bearishEvidence: string, timeframeBehavior: string, caveats: string[]): ChartCatalogKnowledge {
  return {
    bearishEvidence,
    bullishEvidence,
    calculation,
    caveats,
    detailedDescription: calculation,
    readingGuide,
    shortDescription: readingGuide,
    timeframeBehavior,
  };
}

export function movingAverageGuide(title: string, period: number, horizon: string): ChartCatalogKnowledge {
  return indicatorGuide(
    `Compare price with the ${title} and read the average's slope. This is a ${horizon} trend reference that weights recent closes more heavily.`,
    `Exponential moving average of ${period} closed bars using smoothing factor 2 / (${period} + 1).`,
    `Price holding above a rising ${title}, with pullbacks finding acceptance near it, supports bullish trend continuation.`,
    `Price holding below a falling ${title}, with rebounds rejected near it, supports bearish trend continuation.`,
    `${period} bars means ${period} minutes on a 1-minute chart and ${period * 5} minutes on a 5-minute chart; changing timeframe changes the signal horizon.`,
    ["Moving averages lag price and turn only after the underlying closes change.", "Repeated crosses around a flat average indicate chop rather than a strong trend."],
  );
}

export function qmdIndicatorKnowledge(shortDescription: string, detailedDescription: string, interpretation: string, caveat: string): ChartDisplayItem["knowledge"] {
  return {
    bearishEvidence: "Sustained negative readings, especially when price response and other QMD blocks agree, indicate seller or ask-side pressure.",
    bullishEvidence: "Sustained positive readings, especially when price response and other QMD blocks agree, indicate buyer or bid-side pressure.",
    calculation: detailedDescription,
    caveats: [caveat, "Positive and negative readings are evidence, not guaranteed forecasts."],
    detailedDescription,
    interpretation,
    readingGuide: `${shortDescription}. ${interpretation}`,
    shortDescription,
    timeframeBehavior: "QMD first forms causal 100 ms sufficient statistics, then merges those raw counts, volume, quote transitions, and returns once for the selected chart bar. Higher timeframes therefore describe their own interval rather than averaging overlapping forecasts.",
  };
}

import { Check, Clock3, ExternalLink, Link2, MapPin, PanelRightOpen, Plus, Save, Settings2, Trash2, Unlink } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MutableRefObject } from "react";

import { api, query } from "../api/client";
import {
  CANVAS_PREVIEW_CONTEXT_STORAGE_KEY,
  CANVAS_REGISTRY_STORAGE_KEY,
  CANVAS_REGISTRY_UPDATED_EVENT,
  CANVAS_SETTINGS_STORAGE_KEY,
  CANVAS_LINK_GROUPS,
  MAIN_CANVAS_ID,
  NEWS_READER_CANVAS_ID,
  SEC_READER_CANVAS_ID,
  canvasLinkGroupDefinition,
  canvasWorkspaceStorageKey,
  createCanvasRecord,
  focusCanvasUrl,
  ensureNewsReaderCanvas,
  readCanvasRegistry,
  readCanvasWorkspaceState,
  removeCanvasRecord,
  writeCanvasRegistry,
  writeCanvasWorkspaceState,
  type CanvasAssignedLinkGroupId,
  type CanvasChartTimeframe,
  type CanvasLinkContext,
  type CanvasLinkGroupId,
  type CanvasRegistry,
  type CanvasWorkspaceState,
} from "../app/canvasWorkspace";
import { ChartPanel, type ChartCatalogKnowledge, type ChartDisplayItem, type ChartPayload } from "../app/components/ChartPanel";
import { AllNewsContainer, NewsDetailContainer, TickerNewsContainer } from "../app/components/NewsContainers";
import { AllSecContainer, SecDetailContainer, TickerSecContainer } from "../app/components/SecContainers";
import { MarketTime } from "../app/components/MarketTime";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";
import { QuotesTapeContainer } from "../app/components/MarketMicrostructureContainers";
import { StockFactsContainer } from "../app/components/StockFactsContainer";
import { TickerIdentity, useTickerPresentations } from "../app/components/TickerIdentity";
import { TRADING_WORKSPACE_LAYOUT_VERSION, TradingWorkspace, createFocusLayouts } from "../app/components/TradingWorkspace";
import type { WorkspaceWindowLayout, WorkspaceWindowMeta, WorkspaceWindowStatus } from "../app/components/WorkspaceCanvas";
import { TRADING_WORKSPACE_CONTAINERS, containerSupportsSymbolLink, type WorkspaceContainerDefinition, type WorkspaceContainerId } from "../app/tradingWorkspace";

type HistoricalBar = { bar_end?: string; bar_start: string; close: number; high: number; is_closed?: boolean; low: number; open: number; volume: number };
type QmdStructureLevelCandidate = {
  confidence: number;
  created_at_ms: number;
  distance: number;
  evidence_score: number;
  hold_count: number;
  last_test_at_ms: number;
  lower: number;
  price: number;
  scale: "micro" | "tactical" | "context" | string;
  side: number;
  strength: number;
  touch_count: number;
  upper: number;
};
type HistoricalIndicator = {
  bar_start: string;
  qmd_structure_active_levels?: QmdStructureLevelCandidate[];
} & Record<string, number | string | QmdStructureLevelCandidate[] | undefined>;
type PreviewRow = Record<string, unknown>;
type CanvasPreview = {
  as_of: string;
  chart: { bars: HistoricalBar[]; indicators: HistoricalIndicator[]; symbol: string; timeframe: string };
  coverage: { event_count?: number; ticker_count?: number };
  errors: Record<string, string>;
  fills: PreviewRow[];
  journal: PreviewRow[];
  news: PreviewRow[];
  orders: PreviewRow[];
  portfolio: { account: PreviewRow; positions: PreviewRow[]; summary: PreviewRow };
  scanner: PreviewRow[];
  sec: PreviewRow[];
  strategy: { automatic: boolean; revision: number; signals: PreviewRow[]; state: string; strategy_id: string };
  xbrl: PreviewRow[];
};
type CanvasContext = { coverage: { event_count: number; session_date: string | null; ticker_count: number }; preview_time: string; session_date: string | null };
type QmdLiveBar = HistoricalBar & { session_date?: string };
type QmdSnapshot<T> = { current?: T | null; history?: T[]; error?: string };
type QmdBarHistory = {
  as_of: string;
  earliest_session_date: string;
  has_more: boolean;
  has_more_in_session: boolean;
  history: QmdLiveBar[];
  indicators: HistoricalIndicator[];
  indicators_available: boolean;
  next_before: string;
  previous_session_before: string;
  ticker: string;
  timeframe: string;
};
type ChartHistoryCursor = {
  asOf: string;
  nextBefore: string;
  previousSessionBefore: string;
  sessionDate: string;
};
type CanvasLiveChartResponse = {
  bars: QmdSnapshot<QmdLiveBar>;
  errors: Record<string, string>;
  historical_bars?: QmdBarHistory;
  indicators: QmdSnapshot<HistoricalIndicator>;
  source: string;
  stream_interval_ms: number;
};
type CanvasLiveChartState = {
  bars: QmdLiveBar[];
  canLoadEarlier: boolean;
  connected: boolean;
  error: string;
  historyError: string;
  historyNotice: string;
  indicators: HistoricalIndicator[];
  indicatorsAvailable: boolean;
  lastUpdateAt: string;
  loadEarlier: () => void;
  loading: boolean;
  loadingEarlier: boolean;
  pointInTime: boolean;
};

type ContainerSettings = {
  version: 9;
  chart: { showVolume: boolean; symbol: string; timeframe: CanvasChartTimeframe; visibleIndicators: string[] };
  microstructure: { limit: number };
  fills: { limit: number; showCommission: boolean };
  journal: { limit: number };
  news: { content: string; kind: string; lookbackHours: number; ticker: string };
  ticker_news: { lookbackHours: number; showTeaser: boolean };
  news_detail: Record<string, never>;
  orders: { limit: number; showOrderIds: boolean };
  portfolio: { showPositions: boolean; showPnl: boolean };
  scanner: { limit: number; showActivity: boolean };
  sec: { content: string; label: string; lookbackHours: number; ticker: string };
  ticker_sec: { lookbackHours: number };
  sec_detail: Record<string, never>;
  strategy: { showSignals: boolean };
  xbrl: { limit: number; showPeriod: boolean };
};

type CanvasPreviewContext = { previewTime: string; sessionDate: string };
type LinkedContainerState = { status: WorkspaceWindowStatus; symbol: string; title: string };

const ALL_CONTAINER_IDS = TRADING_WORKSPACE_CONTAINERS.map((definition) => definition.id);
const DEFAULT_SETTINGS: ContainerSettings = {
  version: 9,
  chart: { showVolume: true, symbol: "AAPL", timeframe: "1m", visibleIndicators: ["indicator.vwap", "indicator.macd", "indicator.microstructure_outlook"] },
  microstructure: { limit: 1024 },
  fills: { limit: 5, showCommission: true },
  journal: { limit: 6 },
  news: { content: "all", kind: "all", lookbackHours: 6, ticker: "" },
  ticker_news: { lookbackHours: 72, showTeaser: true },
  news_detail: {},
  orders: { limit: 6, showOrderIds: true },
  portfolio: { showPositions: true, showPnl: true },
  scanner: { limit: 6, showActivity: true },
  sec: { content: "all", label: "", lookbackHours: 168, ticker: "" },
  ticker_sec: { lookbackHours: 720 },
  sec_detail: {},
  strategy: { showSignals: true },
  xbrl: { limit: 6, showPeriod: true },
};

const HISTORICAL_TIMEFRAMES: CanvasChartTimeframe[] = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h", "1d", "1mo"];
const ENRICHED_QMD_TIMEFRAMES = new Set<CanvasChartTimeframe>(["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"]);
const MACRO_TIMEFRAMES = new Set<CanvasChartTimeframe>(["1d", "1mo"]);
const INDICATOR_GUIDES: Record<string, ChartCatalogKnowledge> = {
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
const CHART_INDICATORS: ChartDisplayItem[] = [
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
    "indicator.microstructure_outlook",
    "QMD Microstructure Outlook",
    "microstructure",
    [
      "microstructure_unified_signal",
      "microstructure_unified_confidence",
      "microstructure_unified_action",
    ],
    "microstructure",
    {
      bearishEvidence: "A negative signal with rising confidence, negative aggressive flow, ask-side displayed pressure, and negative price response indicates aligned short-horizon sell pressure.",
      bullishEvidence: "A positive signal with rising confidence, positive aggressive flow, bid-side displayed pressure, and positive price response indicates aligned short-horizon buy pressure.",
      calculation: "The directional blocks are weighted 45% aggressive flow, 35% displayed liquidity, and 20% response and resiliency, then attenuated by evidence reliability. Action thresholds require both sufficient score magnitude and at least 35% confidence.",
      shortDescription: "One timeframe-consistent direction signal and its confidence, derived from QMD quotes and trades.",
      detailedDescription: "QMD first records additive quote-and-trade statistics in closed 100 ms buckets. For every larger chart bar it merges counts, volume, Level-1 order flow, queue samples, returns, arrival signs, and liquidity recovery, then calculates the signal once. This avoids averaging overlapping forecasts. Signal runs from -1 (sell pressure) to +1 (buy pressure); confidence runs from 0% to 100% on the left scale.",
      interpretation: "Read the signal line against zero: neon green favors buying, neon red favors selling, and neutral gray means WAIT. Then check the confidence line on the left axis. WAIT means the score is weak, confidence is below 35%, or the flow, liquidity, and response blocks conflict. The three architecture blocks and all nine inputs are available as optional diagnostic panes.",
      readingGuide: "Read the signed signal on the right axis first, then confidence on the 0-100% left axis. Direction without confidence is weak evidence; confidence without a meaningful signed score remains WAIT.",
      timeframeBehavior: "QMD closes causal 100 ms sufficient-statistic buckets and merges their raw counts, volume, quote transitions, and returns into exactly one calculation per selected chart bar. A 1-minute result therefore summarizes that minute rather than averaging sixty 1-second forecasts.",
      caveats: [
        "This is a deterministic microstructure estimate, not a guaranteed price forecast.",
        "A bar is causal and final only after its timeframe closes; live partial bars can still change.",
        "Sparse bars receive lower confidence because classification and quote coverage are weaker.",
        "Displayed NBBO and eligible trades do not reveal all hidden liquidity or execution intent.",
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
  displayIndicator("indicator.qmd_architecture", "QMD Signal Architecture", "microstructure", ["microstructure_unified_signal", "microstructure_aggressive_flow_score", "microstructure_displayed_liquidity_score", "microstructure_response_resiliency_score", "microstructure_regime_reliability"], "qmd_architecture", qmdIndicatorKnowledge("The canonical combined signal and the four properties that explain it", "Combined Signal is 45% Aggressive Flow, 35% Displayed Liquidity, and 20% Response & Resiliency, clamped to -1 through +1. Aggressive Flow combines trade counts, volume, persistence, trade return, and arrivals. Displayed Liquidity combines OFI, queue, microprice, and arrivals. Response & Resiliency combines midpoint response, replenishment, and absorption. Reliability measures evidence quality and block agreement and controls confidence rather than adding another directional vote.", "Read Combined Signal for direction, the three directional blocks for attribution, and Reliability for whether the evidence is trustworthy enough to act. Agreement among the blocks strengthens the result; low reliability warns that the direction is poorly supported.", "The combined line is the same canonical gateway signal used by strategies and the QMD Outlook pane; do not combine the four diagnostics again in the frontend.")),
  displayIndicator("indicator.qmd_structural_pressure", "QMD Structural Pressure", "price_action", [
    "qmd_structure_support_field", "qmd_structure_resistance_field", "qmd_structure_pressure_bias", "qmd_structure_pressure_confidence", "qmd_structure_up_probability",
  ], "qmd_structural_pressure", {
    shortDescription: "One causal support field, resistance field, directional bias, and implied up likelihood from all active QMD structure zones.",
    detailedDescription: "This oscillator summarizes the complete active QMD support/resistance map without replacing the existing Generic Structure overlay. It clusters overlapping zones, discounts repeated evidence from the same scale, and weights each cluster by current strength, confidence, freshness, scale reliability, and normalized distance from the NBBO midpoint.",
    calculation: "Each active zone contributes strength x confidence x scale reliability x exponential proximity. Overlapping zones are clustered so repeated pivots do not count as independent votes. Support and resistance fields combine their remaining independent clusters onto 0-1 scales. Bias is (support - resistance) / (support + resistance + 0.20). Directional confidence combines total structural coverage with separation between the two sides. Implied up likelihood is 50% + 50% x bias x confidence, so weak or conflicting evidence shrinks toward 50%.",
    readingGuide: "Start with the green Support and red Resistance lines. The larger field is the stronger nearby structural side. Then read the signed Bias histogram: above zero favors upward pressure and below zero favors downward pressure. Finally read Up likelihood and Confidence together. A likelihood away from 50% is useful only when confidence is also elevated. Strong fields on both sides indicate compression or a range, not two simultaneous directional signals.",
    bullishEvidence: "Support exceeds resistance, the bias histogram is green above zero, implied up likelihood rises above 50%, and confidence increases. Price holding above the contributing zones provides confirmation.",
    bearishEvidence: "Resistance exceeds support, the bias histogram is red below zero, implied up likelihood falls below 50%, and confidence increases. Price rejecting below the contributing zones provides confirmation.",
    timeframeBehavior: "The gateway calculates this from ordered quote/trade structure state and samples it at each chart-bar close. Changing candle timeframe changes sampling frequency, not the underlying zones or their causal history. Live partial bars may update until close.",
    components: [
      { label: "Support field", description: "Combined confidence-adjusted evidence from active zones strictly below the current NBBO midpoint. Green, 0 to 1.", tone: "buy" },
      { label: "Resistance field", description: "Combined confidence-adjusted evidence from active zones strictly above the current NBBO midpoint. Red, 0 to 1.", tone: "sell" },
      { label: "Pressure bias", description: "Signed balance between the two fields. Positive favors support, negative favors resistance, and zero means balanced or absent evidence.", tone: "neutral" },
      { label: "Implied up likelihood", description: "A deterministic directional translation of bias and confidence. It is centered at 50% and deliberately shrinks toward 50% when evidence is weak or conflicted.", tone: "info" },
      { label: "Directional confidence", description: "How much active evidence exists and how clearly one side dominates. High support and high resistance together reduce confidence because they describe compression.", tone: "warning" },
    ],
    caveats: ["Implied up likelihood is a deterministic, uncalibrated score; it is not an empirical win probability.", "QMD observes consolidated Level-1 NBBO and eligible prints, not hidden liquidity or full venue depth.", "Displayed liquidity can be cancelled, so use subsequent price response and execution flow as confirmation.", "The oscillator summarizes active zones; use QMD Generic Structure to inspect their exact prices, regions, events, and scale."],
  }),
  displayIndicator("indicator.qmd_generic_structure", "QMD Generic Structure", "price_action", [
    "qmd_structure_score", "qmd_structure_direction", "qmd_structure_agreement", "qmd_structure_strength", "qmd_structure_confidence",
    "qmd_structure_support_price", "qmd_structure_support_lower", "qmd_structure_support_upper", "qmd_structure_support_strength", "qmd_structure_support_confidence",
    "qmd_structure_resistance_price", "qmd_structure_resistance_lower", "qmd_structure_resistance_upper", "qmd_structure_resistance_strength", "qmd_structure_resistance_confidence",
    "qmd_structure_active_levels",
    "qmd_structure_micro_direction", "qmd_structure_micro_threshold", "qmd_structure_micro_swing_high", "qmd_structure_micro_swing_low",
    "qmd_structure_micro_support_price", "qmd_structure_micro_support_lower", "qmd_structure_micro_support_upper", "qmd_structure_micro_support_strength", "qmd_structure_micro_support_confidence",
    "qmd_structure_micro_resistance_price", "qmd_structure_micro_resistance_lower", "qmd_structure_micro_resistance_upper", "qmd_structure_micro_resistance_strength", "qmd_structure_micro_resistance_confidence",
    "qmd_structure_tactical_direction", "qmd_structure_tactical_threshold", "qmd_structure_tactical_swing_high", "qmd_structure_tactical_swing_low",
    "qmd_structure_tactical_support_price", "qmd_structure_tactical_support_lower", "qmd_structure_tactical_support_upper", "qmd_structure_tactical_support_strength", "qmd_structure_tactical_support_confidence",
    "qmd_structure_tactical_resistance_price", "qmd_structure_tactical_resistance_lower", "qmd_structure_tactical_resistance_upper", "qmd_structure_tactical_resistance_strength", "qmd_structure_tactical_resistance_confidence",
    "qmd_structure_context_direction", "qmd_structure_context_threshold", "qmd_structure_context_swing_high", "qmd_structure_context_swing_low",
    "qmd_structure_context_support_price", "qmd_structure_context_support_lower", "qmd_structure_context_support_upper", "qmd_structure_context_support_strength", "qmd_structure_context_support_confidence",
    "qmd_structure_context_resistance_price", "qmd_structure_context_resistance_lower", "qmd_structure_context_resistance_upper", "qmd_structure_context_resistance_strength", "qmd_structure_context_resistance_confidence",
    "qmd_structure_event_id", "qmd_structure_event_pivot_at_ms", "qmd_structure_event_at_ms", "qmd_structure_event_kind", "qmd_structure_event_scale", "qmd_structure_event_direction", "qmd_structure_event_price",
    "qmd_structure_session_high", "qmd_structure_session_low", "qmd_structure_premarket_high", "qmd_structure_premarket_low",
    "qmd_structure_opening_range_high", "qmd_structure_opening_range_low", "qmd_structure_trade_volume_poc", "qmd_structure_nearest_round",
    "qmd_structure_luld_upper", "qmd_structure_luld_lower", "qmd_structure_52_week_high", "qmd_structure_52_week_low",
    "qmd_structure_prior_month_high", "qmd_structure_prior_month_low", "qmd_structure_prior_month_close",
  ], "price", {
    shortDescription: "One causal, event-native map of market structure and support/resistance that remains the same across chart timeframes.",
    detailedDescription: "QMD follows consolidated NBBO midpoint changes directly and uses eligible trades to confirm accepted breaks. It extracts three simultaneous price-response scales—micro, tactical, and context—then clusters confirmed pivots into persistent support and resistance zones. The chart samples this same event state at each bar end; candles do not define or recalculate the structure. At the live edge, the chart selects active candidates from the gateway state instead of projecting one winning level backward.",
    calculation: "The adaptive base reversal threshold is the maximum of two price ticks, 1.25 times the recent spread, 1.5 times the recent midpoint move, and 0.5 basis point of price. Micro, tactical, and context use 1×, 3×, and 8× that threshold. A pivot is published only after price reverses by the scale threshold. A break requires the midpoint beyond the pivot plus an eligible trade beyond it and scale-specific persistence. A break with the prior structure is BoS; the first accepted break against it is CHoCH.",
    readingGuide: "Begin with Current support & resistance. By default it shows the three nearest active zones below and above price; configure the count from 1 to 6. The strongest support and resistance by confidence-adjusted evidence are always retained when they fall outside that nearest set and carry an asterisk. S1/R1 are closest to price. Each region is borderless: darker shading and the printed percentage indicate current confidence, not a win probability. Selected structure zones are an optional single-winner view across micro, tactical, and context scales; tactical and the other scale-specific zones are optional diagnostics and are off by default to avoid duplicate shading. Swing and auction references are true configurable lines: opacity is the final rendered opacity, and shape, width, history, labels, and axis tags are available where applicable. A zone containing current price is temporarily in play and is omitted from both sides. A confirmed break retires the original role; the opposite role appears only after price retests the zone from the other side and confirms rejection with persistence plus an eligible trade. Historical regions preserve the evidence bucket known at that time and are never restyled with later confidence. BoS and CHoCH connectors begin at the causally known pivot and end at confirmation. If both endpoints land inside one selected chart candle, the connector keeps those true timestamps while its compact tag moves to the nearest collision-free lane beside that candle so the latest event remains readable.",
    bullishEvidence: "Support below price gains weight when it is retested and holds, tactical/context direction is positive, accepted bullish BoS events persist, and eligible buying moves the midpoint beyond resistance.",
    bearishEvidence: "Resistance above price gains weight when it is retested and holds, tactical/context direction is negative, accepted bearish BoS events persist, and eligible selling moves the midpoint below support.",
    timeframeBehavior: "Structure is independent of the selected candle timeframe. Every chart interval samples the same ordered quote/trade engine at that bar's end, so aligned timestamps carry the same state. Confirmed events are stored durably; live restarts restore one compact state per symbol, and historical requests warm from that ticker's earlier persisted events without using future data.",
    components: [
      { label: "S1–S6 / R1–R6 · Current zones", description: "Nearest active support and resistance candidates ranked by distance from current NBBO midpoint. The configured nearest count is shown on each side, plus the strongest omitted candidate when necessary. An asterisk marks that strongest candidate.", tone: "neutral" },
      { label: "Selected S / R", description: "Optional single support and resistance winners selected across micro, tactical, and context by strength, confidence, scale weight, and distance. This is not another signal and is off by default because Current zones retain more information.", tone: "neutral" },
      { label: "μ-S / μ-R · Micro", description: "Fast reactions using the base adaptive threshold. Useful for execution and immediate liquidity response; decays with a 30-minute half-life.", tone: "info" },
      { label: "T-S / T-R · Tactical", description: "Intermediate zones using three times the base threshold. Useful for intraday trade structure; decays with a five-day half-life.", tone: "warning" },
      { label: "C-S / C-R · Context", description: "Broad zones using eight times the base threshold. Useful for multi-session structure; decays with a 45-day half-life.", tone: "neutral" },
      { label: "BoS", description: "Break of Structure: an accepted pivot break in the established direction. Acceptance requires both midpoint persistence and an eligible trade beyond the level.", tone: "buy" },
      { label: "CHoCH", description: "Change of Character: an accepted pivot break against the established direction. It warns of a possible regime change; it does not guarantee reversal.", tone: "warning" },
      { label: "Strength", description: "Accumulated and time-decayed pivot, touch, hold, and trade-confirmation evidence. It contributes to strongest-level selection; it is not displayed as a second competing visual encoding.", tone: "info" },
      { label: "Confidence", description: "Evidence repeatability and freshness. It controls borderless region density and appears as a percentage inside each current zone. It is not a forecast probability.", tone: "warning" },
      { label: "Broken / role-reversed zone", description: "A decisive boundary break deactivates the original support or resistance. It changes sides only after a later retest from the opposite side is rejected with the same persistence and eligible-trade confirmation; a simple cross never flips the label.", tone: "warning" },
      { label: "Structure score", description: "Direction × strength × confidence × an agreement adjustment. Positive is bullish, negative bearish, and near zero means weak or conflicted structure.", tone: "neutral" },
      { label: "Auction references", description: "04:00 ET session and premarket extremes, 09:30–09:35 opening range, exact eligible-trade volume POC, estimated LULD, completed 52-week/prior-month levels, and nearest round price.", tone: "neutral" },
    ],
    caveats: ["QMD observes consolidated Level-1 NBBO and eligible prints, not full venue depth or hidden liquidity.", "Displayed liquidity can be cancelled; trade confirmation and subsequent price response still matter.", "Nearest means absolute distance from the current NBBO midpoint. Strongest means strength × confidence × scale weight; it does not necessarily mean closest or most likely to hold.", "BoS, CHoCH, support, and resistance are deterministic evidence states—not trade instructions or win probabilities.", "Only confirmed events are durable. An unconfirmed reversal candidate is intentionally rebuilt after a restart rather than persisted as structure."],
  }),
];

const INDICATOR_SERIES = [
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
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Signal", column: "microstructure_unified_signal", color: "var(--foreground)", displayItemId: "indicator.microstructure_outlook", label: "Signal WAIT", lineWidth: 3, pane: "microstructure", priceScaleId: "right" },
  { autoscaleMax: 100, autoscaleMin: 0, axisTitle: "Confidence", column: "microstructure_unified_confidence", color: "var(--primary)", displayItemId: "indicator.microstructure_outlook", label: "Confidence", lineStyle: "dashed", lineWidth: 2, opacity: 0.78, pane: "microstructure", priceScaleId: "left" },
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
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Combined", colorMode: "sign", column: "microstructure_unified_signal", color: "var(--foreground)", displayItemId: "indicator.qmd_architecture", label: "Combined signal", pane: "qmd_architecture", lineWidth: 3 },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Score", colorMode: "sign", column: "microstructure_aggressive_flow_score", color: "var(--success)", displayItemId: "indicator.qmd_architecture", label: "Aggressive flow", pane: "qmd_architecture", lineWidth: 2 },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Score", colorMode: "sign", column: "microstructure_displayed_liquidity_score", color: "var(--info)", displayItemId: "indicator.qmd_architecture", label: "Displayed liquidity", pane: "qmd_architecture", lineWidth: 2 },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Score", colorMode: "sign", column: "microstructure_response_resiliency_score", color: "var(--warning)", displayItemId: "indicator.qmd_architecture", label: "Response & resiliency", pane: "qmd_architecture", lineWidth: 2 },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Reliability", colorMode: "sign", column: "microstructure_regime_reliability", color: "var(--muted-foreground)", displayItemId: "indicator.qmd_architecture", label: "Reliability", lineStyle: "dashed", pane: "qmd_architecture", priceScaleId: "left" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Structure", colorMode: "confidence-sign", column: "qmd_structure_score", color: "var(--foreground)", displayItemId: "indicator.qmd_generic_structure", label: "Structure score", pane: "qmd_generic_structure", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Confidence", column: "qmd_structure_confidence", color: "var(--info)", displayItemId: "indicator.qmd_generic_structure", label: "Confidence", lineStyle: "solid", lineWidth: 2, pane: "qmd_generic_structure", priceScaleId: "left" },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Agreement", column: "qmd_structure_agreement", color: "var(--muted-foreground)", defaultVisible: false, displayItemId: "indicator.qmd_generic_structure", label: "Scale agreement", lastValueVisible: false, lineStyle: "dashed", lineWidth: 1, opacity: 0.65, pane: "qmd_generic_structure", priceScaleId: "left" },
  { autoscaleMax: 1, autoscaleMin: -1, axisTitle: "Bias", colorMode: "sign", column: "qmd_structure_pressure_bias", color: "var(--foreground)", displayItemId: "indicator.qmd_structural_pressure", label: "Pressure bias", pane: "qmd_structural_pressure", style: "histogram" },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Support", column: "qmd_structure_support_field", color: "var(--success)", displayItemId: "indicator.qmd_structural_pressure", label: "Support field", lineWidth: 2, pane: "qmd_structural_pressure", priceScaleId: "left" },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Resistance", column: "qmd_structure_resistance_field", color: "var(--danger)", displayItemId: "indicator.qmd_structural_pressure", label: "Resistance field", lineWidth: 2, pane: "qmd_structural_pressure", priceScaleId: "left" },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Likelihood", column: "qmd_structure_up_probability", color: "var(--info)", displayItemId: "indicator.qmd_structural_pressure", label: "Implied up likelihood", lineWidth: 3, pane: "qmd_structural_pressure", priceScaleId: "left" },
  { autoscaleMax: 1, autoscaleMin: 0, axisTitle: "Confidence", column: "qmd_structure_pressure_confidence", color: "var(--muted-foreground)", defaultVisible: false, displayItemId: "indicator.qmd_structural_pressure", label: "Directional confidence", lineStyle: "dashed", lineWidth: 2, opacity: 0.78, pane: "qmd_structural_pressure", priceScaleId: "left" },
] as const;

function displayIndicator(id: string, title: string, group: string, sourceColumns: string[], pane = "price", knowledge?: ChartDisplayItem["knowledge"]): ChartDisplayItem {
  return { category: pane === "price" ? "Price overlay" : "Oscillator pane", group, id, knowledge: knowledge ?? INDICATOR_GUIDES[id], presentation: { chartRole: pane === "price" ? "overlay" : "oscillator", pane, selectable: true }, sourceColumns, title };
}

function indicatorGuide(readingGuide: string, calculation: string, bullishEvidence: string, bearishEvidence: string, timeframeBehavior: string, caveats: string[]): ChartCatalogKnowledge {
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

function movingAverageGuide(title: string, period: number, horizon: string): ChartCatalogKnowledge {
  return indicatorGuide(
    `Compare price with the ${title} and read the average's slope. This is a ${horizon} trend reference that weights recent closes more heavily.`,
    `Exponential moving average of ${period} closed bars using smoothing factor 2 / (${period} + 1).`,
    `Price holding above a rising ${title}, with pullbacks finding acceptance near it, supports bullish trend continuation.`,
    `Price holding below a falling ${title}, with rebounds rejected near it, supports bearish trend continuation.`,
    `${period} bars means ${period} minutes on a 1-minute chart and ${period * 5} minutes on a 5-minute chart; changing timeframe changes the signal horizon.`,
    ["Moving averages lag price and turn only after the underlying closes change.", "Repeated crosses around a flat average indicate chop rather than a strong trend."],
  );
}

function qmdIndicatorKnowledge(shortDescription: string, detailedDescription: string, interpretation: string, caveat: string): ChartDisplayItem["knowledge"] {
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

function useCanvasLiveChart(symbol: string, timeframe: CanvasChartTimeframe, cutoffMs: number, sessionDate: string, visibleIndicatorIds: string[]): CanvasLiveChartState {
  const pointInTime = cutoffMs < Date.now() - 5_000;
  const indicatorColumns = useMemo(() => requestedIndicatorColumns(visibleIndicatorIds), [visibleIndicatorIds]);
  const rowBudget = useMemo(() => chartRowBudget(indicatorColumns), [indicatorColumns]);
  const [state, setState] = useState<Omit<CanvasLiveChartState, "loadEarlier">>({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", historyNotice: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, pointInTime });
  const historyCursorRef = useRef<ChartHistoryCursor | null>(null);
  const historyRequestRef = useRef(false);
  const historyAbortRef = useRef<AbortController | null>(null);
  const requestKeyRef = useRef("");

  const loadEarlier = useCallback(() => {
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    const cursor = historyCursorRef.current;
    if (!cursor || historyRequestRef.current || requestKeyRef.current !== requestKey) return;
    if (!cursor.nextBefore && !cursor.previousSessionBefore) return;
    const controller = new AbortController();
    historyAbortRef.current = controller;
    historyRequestRef.current = true;
    setState((current) => ({ ...current, historyError: "", loadingEarlier: true }));
    const params = cursor.nextBefore
      ? { as_of: cursor.asOf, before_bar: cursor.nextBefore, indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), session_date: cursor.sessionDate, symbol: ticker, timeframe }
      : { before: cursor.previousSessionBefore, indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), symbol: ticker, timeframe };
    api<QmdBarHistory>(`/api/trading/canvas-live-chart/history${query(params)}`, { signal: controller.signal, timeoutMs: 120000 })
      .then((payload) => {
        if (requestKeyRef.current !== requestKey) return;
        updateHistoryCursor(historyCursorRef, payload);
        const aligned = alignHistoricalChartRows(
          closedRowsAtCutoff(payload.history, timeframe, cutoffMs),
          closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs),
          payload.indicators_available,
        );
        setState((current) => {
          const merged = mergeHistoricalChartPage(current.bars, current.indicators, aligned.bars, aligned.indicators, rowBudget);
          return {
            ...current,
            bars: merged.bars,
            canLoadEarlier: payload.has_more && !merged.atCapacity,
            historyError: "",
            historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : "",
            indicators: merged.indicators,
            indicatorsAvailable: payload.indicators_available,
          };
        });
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        if (requestKeyRef.current !== requestKey) return;
        setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
      })
      .finally(() => {
        historyRequestRef.current = false;
        if (historyAbortRef.current === controller) historyAbortRef.current = null;
        if (requestKeyRef.current === requestKey) setState((current) => ({ ...current, loadingEarlier: false }));
      });
  }, [cutoffMs, indicatorColumns, rowBudget, symbol, timeframe]);

  useEffect(() => {
    let active = true;
    const sockets: Partial<Record<"bars" | "indicators", WebSocket>> = {};
    const reconnectTimers: number[] = [];
    const attempts = { bars: 0, indicators: 0 };
    const requestController = new AbortController();
    const historyController = new AbortController();
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    const pendingLiveSnapshots: Partial<Record<"bars" | "indicators", QmdSnapshot<QmdLiveBar> | QmdSnapshot<HistoricalIndicator>>> = {};
    let liveSnapshotFrame: number | null = null;
    historyAbortRef.current?.abort();
    historyAbortRef.current = historyController;
    requestKeyRef.current = requestKey;
    historyCursorRef.current = null;
    historyRequestRef.current = false;
    setState({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", historyNotice: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, pointInTime });

    const applySnapshot = (kind: "bars" | "indicators", payload: QmdSnapshot<QmdLiveBar> | QmdSnapshot<HistoricalIndicator>, live: boolean) => {
      if (!active) return;
      if (payload.error) {
        if (kind === "bars") setState((current) => ({ ...current, connected: false, error: payload.error || "QMD live bars are unavailable.", loading: false }));
        return;
      }
      const rows = kind === "bars"
        ? closedQmdSnapshotRows(payload as QmdSnapshot<QmdLiveBar>, timeframe, cutoffMs)
        : closedQmdSnapshotRows(payload as QmdSnapshot<HistoricalIndicator>, timeframe, cutoffMs);
      setState((current) => {
        const bars = kind === "bars" ? limitLiveRowsWithHysteresis(mergeRowsByTime(current.bars, rows as QmdLiveBar[]), rowBudget) : current.bars;
        const earliestBarTime = bars.length ? barStartTime(bars[0]) : Number.NEGATIVE_INFINITY;
        const indicators = (kind === "indicators"
          ? limitLiveRowsWithHysteresis(mergeRowsByTime(current.indicators, rows as HistoricalIndicator[]), rowBudget)
          : current.indicators
        ).filter((row) => barStartTime(row) >= earliestBarTime);
        return {
          ...current,
          bars,
          canLoadEarlier: kind === "bars" && bars.length >= rowBudget ? false : current.canLoadEarlier,
          connected: kind === "bars" && live ? true : current.connected,
          error: kind === "bars" ? "" : current.error,
          indicators,
          historyNotice: bars.length >= rowBudget ? chartHistoryLimitNotice(rowBudget) : current.historyNotice,
          lastUpdateAt: kind === "bars" && live ? new Date().toISOString() : current.lastUpdateAt,
          loading: kind === "bars" ? false : current.loading,
        };
      });
    };

    const enqueueLiveSnapshot = (kind: "bars" | "indicators", payload: QmdSnapshot<QmdLiveBar> | QmdSnapshot<HistoricalIndicator>) => {
      pendingLiveSnapshots[kind] = payload;
      if (liveSnapshotFrame !== null) return;
      liveSnapshotFrame = window.requestAnimationFrame(() => {
        liveSnapshotFrame = null;
        const bars = pendingLiveSnapshots.bars;
        const indicators = pendingLiveSnapshots.indicators;
        delete pendingLiveSnapshots.bars;
        delete pendingLiveSnapshots.indicators;
        if (bars) applySnapshot("bars", bars, true);
        if (indicators) applySnapshot("indicators", indicators, true);
      });
    };

    if (!pointInTime) {
      api<CanvasLiveChartResponse>(`/api/trading/canvas-live-chart${query({ row_limit: 500, symbol: ticker, timeframe })}`, { signal: requestController.signal, timeoutMs: 5000 })
        .then((payload) => {
          if (!active) return;
          const historicalRows = payload.historical_bars?.history ?? [];
          if (payload.historical_bars) updateHistoryCursor(historyCursorRef, payload.historical_bars);
          setState((current) => {
            const bars = limitRowsToLatest(mergeRowsByTime(closedRowsAtCutoff(historicalRows, timeframe, cutoffMs), [...closedQmdSnapshotRows(payload.bars, timeframe, cutoffMs), ...current.bars]), rowBudget);
            const atCapacity = bars.length >= rowBudget;
            return {
              ...current,
              bars,
              canLoadEarlier: historicalRows.length > 0 && Boolean(payload.historical_bars?.has_more) && !atCapacity,
              error: payload.bars.error ?? "",
              historyError: payload.errors.history ?? "",
              historyNotice: atCapacity ? chartHistoryLimitNotice(rowBudget) : "",
              loading: false,
            };
          });
          applySnapshot("indicators", payload.indicators, false);
        })
        .catch((reason) => {
          if (!active || requestController.signal.aborted) return;
          setState((current) => ({ ...current, error: `QMD live chart unavailable: ${reason instanceof Error ? reason.message : String(reason)}`, loading: false }));
        });
    }

    const fetchHistoricalPage = () => {
      historyRequestRef.current = true;
      api<QmdBarHistory>(`/api/trading/canvas-live-chart/history${query({ as_of: new Date(cutoffMs).toISOString(), indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe })}`, { signal: historyController.signal, timeoutMs: 120000 })
        .then((payload) => {
          if (!active || requestKeyRef.current !== requestKey) return;
          updateHistoryCursor(historyCursorRef, payload);
          const aligned = alignHistoricalChartRows(
            closedRowsAtCutoff(payload.history, timeframe, cutoffMs),
            closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs),
            payload.indicators_available,
          );
          setState((current) => {
            const merged = mergeHistoricalChartPage(current.bars, current.indicators, aligned.bars, aligned.indicators, rowBudget);
            return {
              ...current,
              bars: merged.bars,
              canLoadEarlier: payload.has_more && !merged.atCapacity,
              historyError: "",
              historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : "",
              indicators: merged.indicators,
              indicatorsAvailable: payload.indicators_available,
              loading: false,
            };
          });
        })
        .catch((reason) => {
          if (historyController.signal.aborted) return;
          if (!active || requestKeyRef.current !== requestKey) return;
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason), loading: false }));
        })
        .finally(() => {
          historyRequestRef.current = false;
          if (historyAbortRef.current === historyController) historyAbortRef.current = null;
        });
    };

    fetchHistoricalPage();

    const connect = (kind: "bars" | "indicators") => {
      if (!active) return;
      const socket = new WebSocket(canvasLiveStreamUrl(kind, ticker, timeframe));
      sockets[kind] = socket;
      socket.onopen = () => {
        attempts[kind] = 0;
      };
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as QmdSnapshot<QmdLiveBar> | QmdSnapshot<HistoricalIndicator>;
          enqueueLiveSnapshot(kind, payload);
        } catch {
          if (kind === "bars") setState((current) => ({ ...current, connected: false, error: "QMD live bars returned invalid data.", loading: false }));
        }
      };
      socket.onclose = () => {
        if (!active) return;
        if (kind === "bars") setState((current) => ({ ...current, connected: false, error: current.error || "QMD live bar stream disconnected; reconnecting.", loading: false }));
        const delay = Math.min(5000, 500 * (2 ** attempts[kind]));
        attempts[kind] += 1;
        reconnectTimers.push(window.setTimeout(() => connect(kind), delay));
      };
    };

    if (!pointInTime) {
      connect("bars");
      if (ENRICHED_QMD_TIMEFRAMES.has(timeframe)) connect("indicators");
    }
    return () => {
      active = false;
      if (requestKeyRef.current === requestKey) requestKeyRef.current = "";
      requestController.abort();
      historyController.abort();
      if (liveSnapshotFrame !== null) window.cancelAnimationFrame(liveSnapshotFrame);
      reconnectTimers.forEach((timer) => window.clearTimeout(timer));
      Object.values(sockets).forEach((socket) => socket?.close());
    };
  }, [cutoffMs, indicatorColumns, pointInTime, rowBudget, sessionDate, symbol, timeframe]);

  return { ...state, loadEarlier };
}

function chartPageSize(timeframe: string) {
  return 5_000;
}

function chartRowBudget(indicatorColumns: string): number {
  const projectedColumnCount = indicatorColumns ? indicatorColumns.split(",").length : 1;
  return Math.max(5_000, Math.min(25_000, Math.floor(500_000 / (projectedColumnCount + 20))));
}

function chartHistoryLimitNotice(rowBudget: number): string {
  return `${rowBudget.toLocaleString()} chart points loaded. Choose a higher timeframe to inspect earlier history.`;
}

function requestedIndicatorColumns(visibleIndicatorIds: string[]): string {
  const selected = new Set(visibleIndicatorIds.map((value) => value.toLowerCase()));
  const columns = new Set<string>(["bar_start"]);
  CHART_INDICATORS.forEach((indicator) => {
    if (!selected.has(indicator.id.toLowerCase())) return;
    indicator.sourceColumns?.forEach((column) => columns.add(column));
  });
  return [...columns].sort().join(",");
}

function alignHistoricalChartRows(
  bars: QmdLiveBar[],
  indicators: HistoricalIndicator[],
  indicatorsRequired: boolean,
) {
  if (!indicatorsRequired) return { bars, indicators: [] };
  const indicatorTimes = new Set(indicators.map((row) => row.bar_start));
  const alignedBars = bars.filter((row) => indicatorTimes.has(row.bar_start));
  const barTimes = new Set(alignedBars.map((row) => row.bar_start));
  return {
    bars: alignedBars,
    indicators: indicators.filter((row) => barTimes.has(row.bar_start)),
  };
}

function updateHistoryCursor(ref: MutableRefObject<ChartHistoryCursor | null>, payload: QmdBarHistory) {
  ref.current = {
    asOf: payload.as_of,
    nextBefore: payload.next_before,
    previousSessionBefore: payload.previous_session_before,
    sessionDate: payload.earliest_session_date,
  };
}

function closedQmdSnapshotRows<T extends { bar_start: string }>(payload: QmdSnapshot<T>, timeframe: string, cutoffMs = Date.now()): T[] {
  const closed = closedRowsAtCutoff(payload.history ?? [], timeframe, cutoffMs);
  const current = payload.current;
  if (!current) return closed;
  const currentStart = Date.parse(current.bar_start);
  return Number.isFinite(currentStart) && currentStart <= cutoffMs ? mergeRowsByTime(closed, [current]) : closed;
}

function closedRowsAtCutoff<T extends { bar_start: string }>(rows: T[], timeframe: string, cutoffMs = Date.now()): T[] {
  const durationMs = timeframeDurationMs(timeframe);
  return rows.filter((row) => {
    const closeMetadata = row as T & { bar_end?: string; is_closed?: boolean };
    if (closeMetadata.is_closed === false) return false;
    const startMs = Date.parse(row.bar_start);
    const endMs = closeMetadata.bar_end ? Date.parse(closeMetadata.bar_end) : startMs + durationMs;
    return Number.isFinite(startMs) && Number.isFinite(endMs) && endMs <= cutoffMs;
  });
}

function timeframeDurationMs(timeframe: string): number {
  if (timeframe === "1d") return 24 * 60 * 60 * 1_000;
  if (timeframe === "1mo") return 30 * 24 * 60 * 60 * 1_000;
  const match = /^(\d+)(ms|s|m|h)$/.exec(timeframe.trim().toLowerCase());
  if (!match) return 60_000;
  const value = Number(match[1]);
  const unitMs = match[2] === "ms" ? 1 : match[2] === "s" ? 1_000 : match[2] === "m" ? 60_000 : 3_600_000;
  return value * unitMs;
}

function mergeRowsByTime<T extends { bar_start: string }>(existing: T[], incoming: T[]): T[] {
  const nextRows = normalizedRowsByTime(incoming);
  if (!nextRows.length) return existing;
  const merged: T[] = [];
  let leftIndex = 0;
  let rightIndex = 0;
  let changed = false;
  while (leftIndex < existing.length || rightIndex < nextRows.length) {
    const left = existing[leftIndex];
    const right = nextRows[rightIndex];
    const leftTime = left ? barStartTime(left) : Number.POSITIVE_INFINITY;
    const rightTime = right ? barStartTime(right) : Number.POSITIVE_INFINITY;
    if (!right || (left && leftTime < rightTime)) {
      merged.push(left);
      leftIndex += 1;
      continue;
    }
    if (!left || rightTime < leftTime) {
      merged.push(right);
      rightIndex += 1;
      changed = true;
      continue;
    }
    const replacement = shallowRowEqual(left, right) ? left : right;
    merged.push(replacement);
    changed ||= replacement !== left;
    leftIndex += 1;
    rightIndex += 1;
  }
  if (!changed && merged.length === existing.length) return existing;
  return merged;
}

function mergeHistoricalChartPage(
  currentBars: QmdLiveBar[],
  currentIndicators: HistoricalIndicator[],
  incomingBars: QmdLiveBar[],
  incomingIndicators: HistoricalIndicator[],
  rowBudget: number,
) {
  const existingTimes = new Set(currentBars.map(barStartTime));
  const availableSlots = Math.max(0, rowBudget - currentBars.length);
  const newBars = incomingBars.filter((row) => !existingTimes.has(barStartTime(row)));
  const admittedBars = availableSlots < newBars.length ? newBars.slice(newBars.length - availableSlots) : newBars;
  const bars = limitRowsToLatest(mergeRowsByTime(currentBars, admittedBars), rowBudget);
  const admittedTimes = new Set(bars.map(barStartTime));
  const indicators = limitRowsToLatest(
    mergeRowsByTime(currentIndicators, incomingIndicators.filter((row) => admittedTimes.has(barStartTime(row)))),
    rowBudget,
  ).filter((row) => admittedTimes.has(barStartTime(row)));
  return { atCapacity: bars.length >= rowBudget, bars, indicators };
}

function limitRowsToLatest<T>(rows: T[], rowBudget: number): T[] {
  return rows.length <= rowBudget ? rows : rows.slice(rows.length - rowBudget);
}

function limitLiveRowsWithHysteresis<T>(rows: T[], rowBudget: number): T[] {
  const evictionChunk = Math.max(250, Math.min(2_000, Math.floor(rowBudget * 0.2)));
  return rows.length <= rowBudget + evictionChunk ? rows : rows.slice(rows.length - rowBudget);
}

function normalizedRowsByTime<T extends { bar_start: string }>(rows: T[]): T[] {
  const valid = rows.filter((row) => row && Number.isFinite(barStartTime(row)));
  let ordered = true;
  for (let index = 1; index < valid.length; index += 1) {
    if (barStartTime(valid[index - 1]) > barStartTime(valid[index])) {
      ordered = false;
      break;
    }
  }
  const sorted = ordered ? valid : [...valid].sort((left, right) => barStartTime(left) - barStartTime(right));
  if (sorted.length < 2) return sorted;
  const deduplicated: T[] = [];
  sorted.forEach((row) => {
    if (deduplicated.length && barStartTime(deduplicated[deduplicated.length - 1]) === barStartTime(row)) deduplicated[deduplicated.length - 1] = row;
    else deduplicated.push(row);
  });
  return deduplicated;
}

const barStartTimeCache = new WeakMap<object, number>();

function barStartTime(row: { bar_start: string }): number {
  const cached = barStartTimeCache.get(row);
  if (cached !== undefined) return cached;
  const parsed = Date.parse(row.bar_start);
  const value = Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
  barStartTimeCache.set(row, value);
  return value;
}

function shallowRowEqual<T extends object>(left: T, right: T): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  return leftKeys.every((key) => leftRecord[key] === rightRecord[key]);
}

function extendedSessionRegions(bars: QmdLiveBar[]) {
  const sessions = new Set(bars.map((bar) => marketSessionDate(bar.bar_start)).filter(Boolean));
  return [...sessions].sort().flatMap((sessionDate) => [
    {
      color: "var(--chart-premarket)",
      end: dateInTimeZone(sessionDate, "09:30", "America/New_York").getTime() / 1000,
      label: "Premarket",
      start: dateInTimeZone(sessionDate, "04:00", "America/New_York").getTime() / 1000,
    },
    {
      color: "var(--chart-after-hours)",
      end: dateInTimeZone(sessionDate, "20:00", "America/New_York").getTime() / 1000,
      label: "After hours",
      start: dateInTimeZone(sessionDate, "16:00", "America/New_York").getTime() / 1000,
    },
  ]);
}

function marketSessionDate(timestamp: string) {
  const instant = new Date(timestamp);
  if (Number.isNaN(instant.getTime())) return "";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "2-digit", timeZone: "America/New_York", year: "numeric" }).formatToParts(instant).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function canvasLiveStreamUrl(kind: "bars" | "indicators", symbol: string, timeframe: string) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/trading/canvas-live-chart/stream/${kind}/${encodeURIComponent(symbol)}${query({ limit: 500, timeframe })}`;
}

export function CanvasConfigurationPage() {
  return <CanvasWorkspaceSurface canvasId={MAIN_CANVAS_ID} manager />;
}

export function CanvasFocusPage() {
  const params = new URLSearchParams(window.location.search);
  const canvasId = params.get("canvas") || MAIN_CANVAS_ID;
  const requestedInstanceId = params.get("container") || undefined;
  const requestedNewsId = params.get("news") || undefined;
  const requestedSecCik = params.get("sec_cik") || undefined;
  const requestedSecAccession = params.get("sec_accession") || undefined;
  return <CanvasWorkspaceSurface canvasId={canvasId} manager={false} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} />;
}

function CanvasWorkspaceSurface({ canvasId, manager, requestedInstanceId, requestedNewsId, requestedSecAccession, requestedSecCik }: { canvasId: string; manager: boolean; requestedInstanceId?: string; requestedNewsId?: string; requestedSecAccession?: string; requestedSecCik?: string }) {
  const [initialCanvasState] = useState<CanvasWorkspaceState | null>(() => focusCanvasState(canvasId, requestedInstanceId));
  const [registry, setRegistry] = useState<CanvasRegistry>(readCanvasRegistry);
  const [previewContext, setPreviewContext] = useState<CanvasPreviewContext>(readPreviewContext);
  const [preview, setPreview] = useState<CanvasPreview | null>(null);
  const [contextReady, setContextReady] = useState(false);
  const [contextError, setContextError] = useState("");
  const [workspaceState, setWorkspaceState] = useState<CanvasWorkspaceState | null>(initialCanvasState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [defaultSaved, setDefaultSaved] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);
  const [linkPopoverContainerId, setLinkPopoverContainerId] = useState<string | null>(null);
  const [settingsContainerId, setSettingsContainerId] = useState<string | null>(null);

  const currentCanvas = registry.canvases.find((canvas) => canvas.id === canvasId) ?? { id: canvasId, label: canvasId === MAIN_CANVAS_ID ? "Main" : "Focus canvas" };
  const primaryChartId = (workspaceState?.openIds ?? []).find((id) => workspaceContainerKind(id, workspaceState) === "chart") ?? "chart";
  const primarySettings = instanceSettings(registry, primaryChartId);
  const dedicatedContainers = new Set<WorkspaceContainerId>(["chart", "facts", "microstructure", "news", "ticker_news", "news_detail", "sec", "ticker_sec", "sec_detail"]);
  const previewContainerKey = (workspaceState?.openIds ?? []).filter((id) => !dedicatedContainers.has(workspaceContainerKind(id, workspaceState))).sort().join(",");
  const activeLinkGroup = registry.linkAssignments[primaryChartId] ?? "none";
  const activeSymbol = activeLinkGroup === "none" ? primarySettings.chart.symbol : registry.linkContexts[activeLinkGroup].symbol;
  const chartCutoffMs = useMemo(() => dateInTimeZone(previewContext.sessionDate, previewContext.previewTime, "America/New_York").getTime(), [previewContext]);
  const previewClocks = useMemo(() => previewClockReadings(previewContext), [previewContext]);
  const clockIcons = [Clock3, MapPin];
  const marketStatus = useMemo(() => historicalMarketStatus(previewContext.sessionDate, previewContext.previewTime), [previewContext]);

  useEffect(() => {
    if (canvasId !== NEWS_READER_CANVAS_ID && canvasId !== SEC_READER_CANVAS_ID) return;
    if (canvasId === NEWS_READER_CANVAS_ID) ensureNewsReaderCanvas();
    setRegistry(readCanvasRegistry());
  }, [canvasId]);

  useEffect(() => {
    writeCanvasRegistry(registry);
  }, [registry]);

  useEffect(() => {
    window.localStorage.setItem(CANVAS_PREVIEW_CONTEXT_STORAGE_KEY, JSON.stringify(previewContext));
  }, [previewContext]);

  useEffect(() => {
    if (!linkPopoverContainerId) return;
    const dismissLinkPopover = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const popover = target.closest("[data-canvas-link-popover]");
      const trigger = target.closest("[data-canvas-link-trigger]");
      if (popover?.getAttribute("data-canvas-link-popover") === linkPopoverContainerId || trigger?.getAttribute("data-canvas-link-trigger") === linkPopoverContainerId) return;
      setLinkPopoverContainerId(null);
    };
    document.addEventListener("pointerdown", dismissLinkPopover, true);
    return () => document.removeEventListener("pointerdown", dismissLinkPopover, true);
  }, [linkPopoverContainerId]);

  useEffect(() => {
    const syncSharedCanvasState = (event: StorageEvent) => {
      if (event.key === CANVAS_REGISTRY_STORAGE_KEY) setRegistry(readCanvasRegistry());
      if (event.key === CANVAS_PREVIEW_CONTEXT_STORAGE_KEY) setPreviewContext(readPreviewContext());
    };
    window.addEventListener("storage", syncSharedCanvasState);
    const syncLocalCanvasRegistry = () => setRegistry(readCanvasRegistry());
    window.addEventListener(CANVAS_REGISTRY_UPDATED_EVENT, syncLocalCanvasRegistry);
    return () => {
      window.removeEventListener("storage", syncSharedCanvasState);
      window.removeEventListener(CANVAS_REGISTRY_UPDATED_EVENT, syncLocalCanvasRegistry);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api<CanvasContext>("/api/trading/canvas-context", { timeoutMs: 20000 })
      .then((payload) => {
        if (cancelled) return;
        if (!payload.session_date) {
          setContextError("QMD History has no covered market day.");
          setLoading(false);
          return;
        }
        setPreviewContext({ previewTime: payload.preview_time || "09:45", sessionDate: payload.session_date });
        setContextError("");
      })
      .catch(() => { if (!cancelled) { setContextError("Historical coverage is temporarily unavailable."); setLoading(false); } })
      .finally(() => { if (!cancelled) setContextReady(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!contextReady || contextError) return;
    if (!previewContainerKey) {
      setPreview(null);
      setLoading(false);
      setError("");
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<CanvasPreview>("/api/trading/canvas-preview", {
      body: JSON.stringify({
        chart_symbol: activeSymbol,
        chart_timeframe: "1m",
        preview_time: previewContext.previewTime,
        session_date: previewContext.sessionDate,
      }),
      method: "POST",
      signal: controller.signal,
      timeoutMs: 60000,
    }).then((payload) => { if (!controller.signal.aborted) setPreview(payload); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [activeSymbol, contextError, contextReady, previewContainerKey, previewContext.previewTime, previewContext.sessionDate]);

  const metaForContainer = useMemo(() => (definition: WorkspaceContainerDefinition): WorkspaceWindowMeta => {
    if (definition.id === "chart") {
      return {
        detail: "Canonical QMD bars using the container's own timeframe and indicator configuration.",
        freshness: previewContext.previewTime,
        sourceLabel: "QMD History + Live",
        status: contextError ? "error" : "ready",
      };
    }
    if (definition.id === "microstructure") {
      return {
        detail: "Canonical historical NBBO updates and trade prints decoded once against the same event sequence and active clock.",
        freshness: previewContext.previewTime,
        sourceLabel: "QMD History",
        status: contextError ? "error" : "ready",
      };
    }
    if (definition.id === "facts") {
      return {
        detail: "Canonical issuer, market publication, SEC, FINRA, QMD daily-volume, and persisted IBKR reference facts at the shared clock.",
        freshness: previewContext.previewTime,
        sourceLabel: "Point-in-time facts",
        status: contextError ? "error" : "ready",
      };
    }
    const sourceError = preview?.errors[definition.id] ?? preview?.errors[definition.id === "sec" ? "sec" : definition.id === "xbrl" ? "xbrl" : ""];
    const newsContainer = ["news", "ticker_news", "news_detail"].includes(definition.id);
    const secContainer = ["sec", "ticker_sec", "sec_detail"].includes(definition.id);
    return {
      detail: `${definition.title} rendered at the shared configuration clock.`,
      freshness: previewContext.previewTime,
      sourceLabel: sourceError ? "Unavailable" : definition.id === "scanner" ? "QMD History" : newsContainer || secContainer || definition.id === "xbrl" ? "Point-in-time" : "IBKR preview",
      status: sourceError ? "error" : newsContainer || secContainer || preview ? "ready" : "idle",
    };
  }, [contextError, preview, previewContext.previewTime]);

  const canvasTargets = registry.canvases.map((canvas, index) => ({
    color: ["var(--primary)", "var(--info)", "var(--success)", "var(--warning)"][index % 4],
    id: canvas.id,
    isCurrent: canvas.id === canvasId,
    label: canvas.label,
  }));

  function updateRegistry(update: (current: CanvasRegistry) => CanvasRegistry) {
    setRegistry((current) => update(current));
  }

  function updateLinkContext(group: CanvasAssignedLinkGroupId, patch: Partial<CanvasLinkContext>) {
    updateRegistry((current) => ({
      ...current,
      linkContexts: { ...current.linkContexts, [group]: { ...current.linkContexts[group], ...patch } },
    }));
  }

  function updateInstanceSettings(instanceId: string, update: ContainerSettings | ((current: ContainerSettings) => ContainerSettings)) {
    updateRegistry((current) => {
      const existing = instanceSettings(current, instanceId);
      const next = typeof update === "function" ? update(existing) : update;
      return { ...current, instanceSettings: { ...current.instanceSettings, [instanceId]: normalizeSettings(next) } };
    });
  }

  function setContainerLink(instanceId: string, containerId: WorkspaceContainerId, group: CanvasLinkGroupId) {
    if (!containerSupportsSymbolLink(containerId)) return;
    updateRegistry((current) => {
      const previousGroup = current.linkAssignments[instanceId] ?? "none";
      const linkAssignments = { ...current.linkAssignments, [instanceId]: group };
      const linkOwners = { ...current.linkOwners };
      if (previousGroup !== "none" && previousGroup !== group && linkOwners[previousGroup] === instanceId) {
        const nextOwner = Object.keys(linkAssignments).find((candidateId) => candidateId !== instanceId && linkAssignments[candidateId] === previousGroup);
        if (nextOwner) linkOwners[previousGroup] = nextOwner;
        else delete linkOwners[previousGroup];
      }
      if (group !== "none" && (!linkOwners[group] || linkAssignments[linkOwners[group]!] !== group)) linkOwners[group] = instanceId;
      return { ...current, linkAssignments, linkOwners };
    });
  }

  function registerContainerInstance(instanceId: string) {
    updateRegistry((current) => current.instanceSettings[instanceId]
      ? current
      : { ...current, instanceSettings: { ...current.instanceSettings, [instanceId]: cloneDefaultSettings() } });
  }

  function openNewCanvas(instanceId?: string, sourceLayout?: WorkspaceWindowLayout) {
    const containerId = instanceId ? workspaceContainerKind(instanceId, workspaceState) : undefined;
    const created = createCanvasRecord(registry, containerId ? `${containerInstanceTitle(containerId, instanceId!, workspaceState, registry)} focus` : undefined);
    const sourceState = registry.defaultState ?? workspaceState;
    const inheritedIds = sourceState?.openIds.length ? sourceState.openIds : ALL_CONTAINER_IDS;
    const state: CanvasWorkspaceState = instanceId && containerId
      ? {
          groups: {},
          instances: { [instanceId]: containerId },
          layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
          layouts: { [instanceId]: focusLayout(sourceLayout) },
          openIds: [instanceId],
        }
      : {
          groups: sourceState?.groups ?? {},
          instances: sourceState?.instances ?? Object.fromEntries(inheritedIds.map((id) => [id, workspaceContainerKind(id, sourceState)])),
          layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
          layouts: sourceState
            ? normalizeInheritedLayouts(sourceState.layouts, inheritedIds)
            : createFocusLayouts(inheritedIds),
          openIds: [...inheritedIds],
        };
    writeCanvasWorkspaceState(created.canvas.id, state);
    setRegistry(created.registry);
    window.open(focusCanvasUrl(created.canvas.id, instanceId), "_blank", "noopener,noreferrer");
  }

  function moveContainer(instanceId: string, targetCanvasId: string, sourceLayout: WorkspaceWindowLayout) {
    const containerId = workspaceContainerKind(instanceId, workspaceState);
    const target = readCanvasWorkspaceState(targetCanvasId) ?? { groups: {}, instances: {}, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts: {}, openIds: [] };
    const openIds = target.openIds.includes(instanceId) ? target.openIds : [...target.openIds, instanceId];
    const targetContainsFullscreenWindow = target.openIds.some((id) => target.layouts[id]?.fullscreen);
    const layouts = target.openIds.length === 0
      ? { ...target.layouts, [instanceId]: focusLayout(sourceLayout) }
      : targetContainsFullscreenWindow
        ? createFocusLayouts(openIds)
        : { ...target.layouts, [instanceId]: offsetLayout(sourceLayout, target.openIds.length) };
    writeCanvasWorkspaceState(targetCanvasId, {
      groups: target.groups,
      instances: { ...target.instances, [instanceId]: containerId },
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts,
      openIds,
    });
  }

  function moveGroup(groupId: string, targetCanvasId: string, sourceState: CanvasWorkspaceState) {
    const target = readCanvasWorkspaceState(targetCanvasId) ?? { groups: {}, instances: {}, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts: {}, openIds: [] };
    const offset = target.openIds.length ? 18 * ((target.openIds.length % 5) + 1) : 0;
    const movedLayouts = Object.fromEntries(Object.entries(sourceState.layouts).map(([id, layout]) => [id, { ...layout, x: layout.x + offset, y: layout.y + offset }]));
    const highest = Math.max(0, ...Object.values(target.layouts).map((layout) => layout.z), ...Object.values(target.groups).map((group) => group.z));
    const movedGroups = Object.fromEntries(Object.entries(sourceState.groups).map(([id, group]) => [id, {
      ...group,
      fullscreen: target.openIds.length === 0 && id === groupId,
      minimized: false,
      z: id === groupId ? highest + 1 : group.z,
    }]));
    writeCanvasWorkspaceState(targetCanvasId, {
      groups: { ...target.groups, ...movedGroups },
      instances: { ...target.instances, ...sourceState.instances },
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts: { ...target.layouts, ...movedLayouts },
      openIds: [...new Set([...target.openIds, ...sourceState.openIds])],
    });
  }

  function openGroupCanvas(groupId: string, sourceState: CanvasWorkspaceState) {
    const created = createCanvasRecord(registry, "Grouped focus");
    const groups = Object.fromEntries(Object.entries(sourceState.groups).map(([id, group]) => [id, { ...group, fullscreen: id === groupId, minimized: false }]));
    const state = { ...sourceState, groups, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION };
    writeCanvasWorkspaceState(created.canvas.id, state);
    setRegistry(created.registry);
    window.open(focusCanvasUrl(created.canvas.id), "_blank", "noopener,noreferrer");
  }

  function saveDefaultLayout() {
    if (!workspaceState) return;
    const defaultState = {
      ...workspaceState,
      groups: Object.fromEntries(Object.entries(workspaceState.groups).map(([id, group]) => [id, { ...group, fullscreen: false, minimized: false }])),
      layouts: Object.fromEntries(Object.entries(workspaceState.layouts).map(([id, layout]) => [id, { ...layout, fullscreen: false, minimized: false }])),
    };
    updateRegistry((current) => ({ ...current, defaultState }));
    setDefaultSaved(true);
  }

  function removeCanvas(canvasToRemove: string) {
    setRegistry((current) => removeCanvasRecord(current, canvasToRemove));
  }

  return (
    <div className={manager ? "canvas-config-page" : "canvas-config-page canvas-focus-page"}>
      <header className="canvas-config-toolbar">
        <div className="canvas-clock-control" aria-label="Preview clock">
          <div className="canvas-clock-zones" aria-label="Preview time zones">
            {previewClocks.map((clock, index) => {
              const Icon = clockIcons[index];
              return <span key={clock.label}><Icon aria-hidden="true" size={15} /><span><small>{clock.label}</small><strong>{clock.value}</strong>{clock.detail ? <em>{clock.detail}</em> : null}</span></span>;
            })}
          </div>
        </div>
        <MarketStatusBadge value={marketStatus} />
        <div aria-label="Replay and Backtest Debug controls" className="canvas-mode-context-slot" />
        {manager ? <div className="canvas-toolbar-actions"><button className="button secondary compact canvas-set-default" disabled={!workspaceState} onClick={saveDefaultLayout} type="button"><Save size={13} /> {defaultSaved ? "Default saved" : "Set default"}</button><button aria-expanded={managementOpen} aria-label="Canvas management" className="button secondary compact canvas-management-toggle" onClick={() => setManagementOpen((open) => !open)} type="button"><PanelRightOpen size={13} /> Manage</button></div> : null}
      </header>

      {contextError || error ? <div className="canvas-inline-error">{contextError || error}</div> : null}

      <TradingWorkspace
        allowMultipleInstances
        canPopOut
        canvasTargets={canvasTargets}
        clockLabel=""
        commandBarVisible={false}
        compact
        defaultOpenIds={manager ? ALL_CONTAINER_IDS : initialCanvasState?.openIds ?? []}
        defaultStateOverride={manager ? registry.defaultState ?? null : initialCanvasState}
        definitionsOverride={TRADING_WORKSPACE_CONTAINERS}
        historicalSourceReady={!error}
        initialStateOverride={manager ? null : initialCanvasState}
        layoutPreset={manager ? "global" : "focus"}
        managementContent={manager ? <CanvasManager registry={registry} onCreate={() => openNewCanvas()} onOpen={(id) => window.open(focusCanvasUrl(id), "_blank", "noopener,noreferrer")} onRemove={removeCanvas} /> : null}
        managementOpen={manager && managementOpen}
        metaForContainer={metaForContainer}
        mode="replay"
        onContainerAdded={registerContainerInstance}
        onMoveContainerToCanvas={moveContainer}
        onMoveGroupToCanvas={moveGroup}
        onManagementClose={() => setManagementOpen(false)}
        onPopOutContainer={openNewCanvas}
        onPopOutGroup={openGroupCanvas}
        onStateChange={setWorkspaceState}
        renderContainer={(definition, instanceId) => {
          const settings = instanceSettings(registry, instanceId);
          const linkable = definition.linkScope === "single-symbol";
          const group = linkable ? registry.linkAssignments[instanceId] ?? "none" : "none";
          const linkContext = group === "none" ? { symbol: settings.chart.symbol } : registry.linkContexts[group];
          const symbolEditable = linkable && (group === "none" || registry.linkOwners[group] === instanceId);
          const linkedContainers: LinkedContainerState[] = group === "none" ? [] : (workspaceState?.openIds ?? [])
            .filter((candidateId) => {
              const candidateKind = workspaceContainerKind(candidateId, workspaceState);
              return containerSupportsSymbolLink(candidateKind) && registry.linkAssignments[candidateId] === group;
            })
            .map((candidateId) => {
              const candidateKind = workspaceContainerKind(candidateId, workspaceState);
              const candidate = TRADING_WORKSPACE_CONTAINERS.find((item) => item.id === candidateKind)!;
              return { status: metaForContainer(candidate).status, symbol: registry.linkContexts[group].symbol, title: containerInstanceTitle(candidateKind, candidateId, workspaceState, registry) };
            });
          return <ContainerPreview
            canvasId={canvasId}
            chartCutoffMs={chartCutoffMs}
            definition={definition}
            instanceId={instanceId}
            linkOpen={linkPopoverContainerId === instanceId}
            linkContext={linkContext}
            linkGroup={group}
            linkedContainers={linkedContainers}
            loading={loading}
            onLinkChange={(nextGroup) => setContainerLink(instanceId, definition.id, nextGroup)}
            onLinkContextChange={(patch) => {
              if (group !== "none") updateLinkContext(group, patch);
              else if (patch.symbol) updateInstanceSettings(instanceId, (current) => ({ ...current, chart: { ...current.chart, symbol: patch.symbol!.trim().toUpperCase() } }));
            }}
            preview={preview}
            previewContext={previewContext}
            requestedNewsId={requestedNewsId}
            requestedSecAccession={requestedSecAccession}
            requestedSecCik={requestedSecCik}
            settings={settings}
            settingsOpen={settingsContainerId === instanceId}
            symbolEditable={symbolEditable}
            updateSettings={(update) => updateInstanceSettings(instanceId, update)}
          />;
        }}
        runLabel={currentCanvas.label}
        runStatus={preview ? "running" : "idle"}
        showHealth={false}
        storageKeyOverride={canvasWorkspaceStorageKey(canvasId)}
        linkColorForContainer={(definition, instanceId) => definition.linkScope === "single-symbol" ? canvasLinkGroupDefinition(registry.linkAssignments[instanceId] ?? "none")?.color : undefined}
        titleBarActionsForContainer={(definition, instanceId) => {
          const linkable = definition.linkScope === "single-symbol";
          const group = linkable ? registry.linkAssignments[instanceId] ?? "none" : "none";
          const groupDefinition = canvasLinkGroupDefinition(group);
          const linkOpen = linkPopoverContainerId === instanceId;
          const settingsOpen = settingsContainerId === instanceId;
          return <>
            {linkable ? <button
              aria-expanded={linkOpen}
              aria-label={`Link ${definition.title}`}
              className="workspace-window-link-action"
              data-canvas-link-trigger={instanceId}
              data-active={linkOpen ? "true" : "false"}
              onClick={() => { setSettingsContainerId(null); setLinkPopoverContainerId((current) => current === instanceId ? null : instanceId); }}
              title={groupDefinition ? `${groupDefinition.label} link group; change color or unlink` : "Choose a link color"}
              type="button"
            ><Link2 size={11} />{groupDefinition ? <i aria-hidden="true" className="canvas-link-title-swatch" /> : null}<span>{groupDefinition?.label ?? "Link"}</span></button> : null}
            <button
              aria-expanded={settingsOpen}
              aria-label={`Configure ${definition.title}`}
              className="toolbar-button compact workspace-window-settings-action"
              data-active={settingsOpen ? "true" : "false"}
              onClick={() => { setLinkPopoverContainerId(null); setSettingsContainerId((current) => current === instanceId ? null : instanceId); }}
              title={`Configure ${definition.title}`}
              type="button"
            ><Settings2 size={11} /></button>
          </>;
        }}
        titleForContainer={(definition, instanceId) => containerInstanceTitle(definition.id, instanceId, workspaceState, registry)}
        workspaceBadge={manager ? "Main" : "Focus"}
      />
    </div>
  );
}

function CanvasManager({ onCreate, onOpen, onRemove, registry }: { onCreate: () => void; onOpen: (id: string) => void; onRemove: (id: string) => void; registry: CanvasRegistry }) {
  return <section aria-label="Canvas manager" className="canvas-manager-strip"><strong>Canvases</strong><div className="canvas-manager-items">{registry.canvases.map((canvas) => <article key={canvas.id} data-main={canvas.id === MAIN_CANVAS_ID ? "true" : "false"}>{canvas.id === MAIN_CANVAS_ID ? <><span>{canvas.label}</span><small>default authority</small></> : <><button aria-label={`Open ${canvas.label}`} className="canvas-manager-open" onClick={() => onOpen(canvas.id)} title="Open canvas in a new page" type="button"><span>{canvas.label}</span><ExternalLink size={11} /></button><button aria-label={`Remove ${canvas.label}`} className="toolbar-button compact" onClick={() => onRemove(canvas.id)} title="Remove canvas" type="button"><Trash2 size={12} /></button></>}</article>)}</div><button className="button secondary compact" onClick={onCreate} type="button"><Plus size={13} /> New canvas</button></section>;
}

type SettingsUpdater = (update: ContainerSettings | ((current: ContainerSettings) => ContainerSettings)) => void;

function ContainerPreview({ canvasId, chartCutoffMs, definition, instanceId, linkContext, linkGroup, linkedContainers, linkOpen, loading, onLinkChange, onLinkContextChange, preview, previewContext, requestedNewsId, requestedSecAccession, requestedSecCik, settings, settingsOpen, symbolEditable, updateSettings }: {
  canvasId: string;
  chartCutoffMs: number;
  definition: WorkspaceContainerDefinition;
  instanceId: string;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  linkedContainers: LinkedContainerState[];
  linkOpen: boolean;
  loading: boolean;
  onLinkChange: (group: CanvasLinkGroupId) => void;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  preview: CanvasPreview | null;
  previewContext: CanvasPreviewContext;
  requestedNewsId?: string;
  requestedSecAccession?: string;
  requestedSecCik?: string;
  settings: ContainerSettings;
  settingsOpen: boolean;
  symbolEditable: boolean;
  updateSettings: SettingsUpdater;
}) {
  const overlayOpen = linkOpen || settingsOpen;
  return <div className="canvas-container-preview">
    {linkOpen ? <div className="canvas-container-settings" aria-label={`${definition.title} link configuration`} data-canvas-link-popover={instanceId}><div className="canvas-link-guide"><strong>Link color</strong><small>Same color = linked</small></div><LinkColorPicker containerTitle={definition.title} onChange={onLinkChange} value={linkGroup} /><LinkedContainerList containerTitle={definition.title} containers={linkedContainers} /></div> : null}
    {settingsOpen ? <div className="canvas-container-settings" aria-label={`${definition.title} settings`}>{containerFields(definition.id, settings, linkContext, updateSettings, onLinkContextChange)}</div> : null}
    <div className={overlayOpen ? "canvas-container-content configuration-open" : "canvas-container-content"}>{definition.id === "chart"
      ? <ChartContainerPreview cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} linkGroup={linkGroup} onLinkContextChange={onLinkContextChange} previewContext={previewContext} settings={settings} symbolEditable={symbolEditable} updateSettings={updateSettings} />
      : definition.id === "microstructure"
        ? <QuotesTapeContainer end={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.microstructure} start={dateInTimeZone(previewContext.sessionDate, "04:00", "America/New_York").toISOString()} symbol={linkContext.symbol} />
      : definition.id === "facts"
        ? <StockFactsContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} symbol={linkContext.symbol} />
      : definition.id === "news"
        ? <AllNewsContainer asOf={new Date(chartCutoffMs).toISOString()} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, news: { ...state.news, ...patch } }))} settings={settings.news} />
      : definition.id === "ticker_news"
        ? <TickerNewsContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.ticker_news} symbol={linkContext.symbol} />
      : definition.id === "news_detail"
        ? <NewsDetailContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} requestedNewsId={requestedNewsId} />
      : definition.id === "sec"
        ? <AllSecContainer asOf={new Date(chartCutoffMs).toISOString()} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, sec: { ...state.sec, ...patch } }))} settings={settings.sec} />
      : definition.id === "ticker_sec"
        ? <TickerSecContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.ticker_sec} symbol={linkContext.symbol} />
      : definition.id === "sec_detail"
        ? <SecDetailContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} requestedAccession={requestedSecAccession} requestedCik={requestedSecCik} />
      : loading && !preview
        ? <div className="canvas-preview-loading">Loading {definition.title.toLowerCase()}…</div>
        : renderPreview(definition.id, preview, settings, linkGroup, onLinkContextChange)}</div>
  </div>;
}

function LinkedContainerList({ containerTitle, containers }: { containerTitle: string; containers: LinkedContainerState[] }) {
  const presentations = useTickerPresentations(containers.map((container) => container.symbol));
  return <div aria-label={`${containerTitle} linked containers`} className="canvas-linked-container-list">
    {containers.length ? containers.map((container) => <div className="canvas-linked-container-row" key={container.title}><span>{container.title}</span><strong><TickerIdentity logoUrl={presentations[container.symbol]?.logo_url} ticker={container.symbol} /></strong><em data-status={container.status}><i aria-hidden="true" />{statusLabel(container.status)}</em></div>) : <small>No containers use this color</small>}
  </div>;
}

function LinkColorPicker({ containerTitle, onChange, value }: { containerTitle: string; onChange: (group: CanvasLinkGroupId) => void; value: CanvasLinkGroupId }) {
  return <div aria-label={`${containerTitle} link color`} className="canvas-link-picker" role="group">
    {CANVAS_LINK_GROUPS.map((group) => <button
      aria-label={`Assign ${containerTitle} to ${group.label}`}
      aria-pressed={value === group.id}
      className="canvas-link-color-choice"
      key={group.id}
      onClick={() => onChange(group.id)}
      style={{ "--canvas-link-choice-color": group.color } as CSSProperties}
      title={group.label}
      type="button"
    ><span aria-hidden="true">{value === group.id ? <Check size={12} /> : null}</span></button>)}
    <button aria-label={`Unlink ${containerTitle}`} aria-pressed={value === "none"} className="canvas-link-unlink" onClick={() => onChange("none")} title="Unlink" type="button"><Unlink size={12} /></button>
  </div>;
}

function renderPreview(id: WorkspaceContainerId, preview: CanvasPreview | null, settings: ContainerSettings, linkGroup: CanvasLinkGroupId, onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void) {
  if (!preview) return <EmptyState label="No preview data" />;
  if (id === "scanner") return <PreviewTable columns={settings.scanner.showActivity ? ["symbol", "last", "change_pct", "volume", "trade_count"] : ["symbol", "last", "change_pct"]} onSymbolSelect={linkGroup === "none" ? undefined : (symbol) => onLinkContextChange({ symbol })} rows={preview.scanner.slice(0, settings.scanner.limit)} />;
  if (id === "portfolio") return <PortfolioPreview data={preview.portfolio} settings={settings.portfolio} />;
  if (id === "orders") return <PreviewTable columns={settings.orders.showOrderIds ? ["orderId", "ticker", "side", "orderType", "quantity", "status"] : ["ticker", "side", "orderType", "quantity", "status"]} rows={preview.orders.slice(0, settings.orders.limit)} />;
  if (id === "fills") return <PreviewTable columns={settings.fills.showCommission ? ["time", "ticker", "side", "shares", "price", "commission"] : ["time", "ticker", "side", "shares", "price"]} rows={preview.fills.slice(0, settings.fills.limit)} />;
  if (id === "strategy") return <StrategyPreview data={preview.strategy} showSignals={settings.strategy.showSignals} />;
  if (id === "xbrl") return <PreviewTable columns={settings.xbrl.showPeriod ? ["filed_at_utc", "tag", "value", "unit_code", "fiscal_period"] : ["filed_at_utc", "tag", "value", "unit_code"]} rows={preview.xbrl.slice(0, settings.xbrl.limit)} />;
  return <PreviewTable columns={["time", "category", "event", "detail"]} rows={preview.journal.slice(0, settings.journal.limit)} />;
}

type ChartContainerPreviewProps = {
  cutoffMs: number;
  instanceId: string;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  previewContext: CanvasPreviewContext;
  settings: ContainerSettings;
  symbolEditable: boolean;
  updateSettings: SettingsUpdater;
};

const ChartContainerPreview = memo(function ChartContainerPreview({ cutoffMs, instanceId, linkContext, onLinkContextChange, previewContext, settings, symbolEditable, updateSettings }: ChartContainerPreviewProps) {
  const liveChart = useCanvasLiveChart(linkContext.symbol, settings.chart.timeframe, cutoffMs, previewContext.sessionDate, settings.chart.visibleIndicators);
  const presentations = useTickerPresentations([linkContext.symbol]);
  return <ChartPreview changeAsOf={new Date(cutoffMs).toISOString()} instanceId={instanceId} linkContext={linkContext} liveChart={liveChart} logoUrl={presentations[linkContext.symbol]?.logo_url} onLinkContextChange={onLinkContextChange} settings={settings} symbolEditable={symbolEditable} updateSettings={updateSettings} />;
}, chartContainerPreviewPropsEqual);

function chartContainerPreviewPropsEqual(previous: ChartContainerPreviewProps, next: ChartContainerPreviewProps) {
  const previousChart = previous.settings.chart;
  const nextChart = next.settings.chart;
  return previous.instanceId === next.instanceId
    && previous.cutoffMs === next.cutoffMs
    && previous.linkGroup === next.linkGroup
    && previous.linkContext.symbol === next.linkContext.symbol
    && previous.previewContext.sessionDate === next.previewContext.sessionDate
    && previous.previewContext.previewTime === next.previewContext.previewTime
    && previous.symbolEditable === next.symbolEditable
    && previousChart.symbol === nextChart.symbol
    && previousChart.timeframe === nextChart.timeframe
    && previousChart.showVolume === nextChart.showVolume
    && stringArraysEqual(previousChart.visibleIndicators, nextChart.visibleIndicators);
}

function stringArraysEqual(previous: readonly string[], next: readonly string[]) {
  return previous.length === next.length && previous.every((value, index) => value === next[index]);
}

function ChartPreview({ changeAsOf, instanceId, linkContext, liveChart, logoUrl, onLinkContextChange, settings, symbolEditable, updateSettings }: { changeAsOf: string; instanceId: string; linkContext: CanvasLinkContext; liveChart: CanvasLiveChartState; logoUrl?: string; onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void; settings: ContainerSettings; symbolEditable: boolean; updateSettings: SettingsUpdater }) {
  const indicators = liveChart.indicators;
  const visibleIndicators = liveChart.indicatorsAvailable ? settings.chart.visibleIndicators : [];
  const timeframe = settings.chart.timeframe;
  const payload = useMemo<ChartPayload>(() => ({
    candles: liveChart.bars.map((bar) => ({ close: bar.close, high: bar.high, low: bar.low, open: bar.open, time: Date.parse(bar.bar_start) / 1000 })),
    markers: [],
    oscillator_series: historicalIndicatorSeries(indicators, "oscillator", visibleIndicators),
    overlay_series: historicalIndicatorSeries(indicators, "price", visibleIndicators),
    price_zones: historicalMarketLevelZones(indicators, liveChart.bars, visibleIndicators),
    regions: MACRO_TIMEFRAMES.has(timeframe) ? [] : extendedSessionRegions(liveChart.bars),
    volume: settings.chart.showVolume ? liveChart.bars.map((bar) => ({ color: bar.close >= bar.open ? "var(--success)" : "var(--danger)", time: Date.parse(bar.bar_start) / 1000, value: bar.volume })) : [],
  }), [indicators, liveChart.bars, settings.chart.showVolume, timeframe, visibleIndicators]);
  function updateChart(symbol: string, nextTimeframe: CanvasChartTimeframe) {
    updateSettings((current) => ({ ...current, chart: { ...current.chart, symbol, timeframe: nextTimeframe } }));
    onLinkContextChange({ symbol });
  }
  const latestBar = liveChart.bars[liveChart.bars.length - 1];
  const sessionDate = latestBar?.session_date || latestBar?.bar_start.slice(0, 10);
  const emptyMessage = liveChart.connected
    ? `Waiting for the first live ${linkContext.symbol} ${timeframe} bar.`
    : "Start QMD Gateway to stream canonical live bars.";
  return <ChartPanel canLoadEarlier={liveChart.canLoadEarlier} displayItemOptions={liveChart.indicatorsAvailable ? CHART_INDICATORS : []} emptyMessage={emptyMessage} enableFullscreen={false} errorMessage={liveChart.error || liveChart.historyError} featureOptions={[]} indicatorOptions={[]} initialFitMode="recent" infoMessage={liveChart.historyNotice} loading={liveChart.loading} loadingEarlier={liveChart.loadingEarlier} onLoadEarlier={liveChart.loadEarlier} onTickerChange={(symbol) => updateChart(symbol.toUpperCase(), timeframe)} onTimeframeChange={(nextTimeframe) => updateChart(linkContext.symbol, nextTimeframe as CanvasChartTimeframe)} onVisibleColumnsChange={(nextVisibleIndicators) => updateSettings((current) => ({ ...current, chart: { ...current.chart, visibleIndicators: nextVisibleIndicators } }))} payload={payload} periodEnd={sessionDate} periodStart={sessionDate} settingsStorageKey={`${CANVAS_SETTINGS_STORAGE_KEY}.${instanceId}`} ticker={linkContext.symbol} tickerChangeAsOf={changeAsOf} tickerEditable={symbolEditable} tickerLogoUrl={logoUrl} timeframe={timeframe} timeframes={HISTORICAL_TIMEFRAMES} visibleColumns={visibleIndicators} />;
}

function historicalMarketLevelZones(rows: HistoricalIndicator[], bars: HistoricalBar[], visibleIndicators: string[]): NonNullable<ChartPayload["price_zones"]> {
  if (!rows.length || !bars.length || !visibleIndicators.includes("indicator.qmd_generic_structure")) return [];
  const chartEnd = Date.parse(bars[bars.length - 1].bar_end || bars[bars.length - 1].bar_start) / 1000 + 1;
  const zones: NonNullable<ChartPayload["price_zones"]> = [];
  pushCurrentStructureLevels(zones, rows, chartEnd);
  pushStructureZoneSegments(zones, rows, chartEnd, {
    compactLabel: "SUP", label: "Unified support", prefix: "qmd_structure_support", scope: "unified", side: "support",
  });
  pushStructureZoneSegments(zones, rows, chartEnd, {
    compactLabel: "RES", label: "Unified resistance", prefix: "qmd_structure_resistance", scope: "unified", side: "resistance",
  });
  ([
    ["micro", "μ-S", "μ-R", "Micro"],
    ["tactical", "T-S", "T-R", "Tactical"],
    ["context", "C-S", "C-R", "Context"],
  ] as const).forEach(([scope, supportLabel, resistanceLabel, title]) => {
    pushStructureZoneSegments(zones, rows, chartEnd, {
      compactLabel: supportLabel, label: `${title} support`, prefix: `qmd_structure_${scope}_support`, scope, side: "support",
    });
    pushStructureZoneSegments(zones, rows, chartEnd, {
      compactLabel: resistanceLabel, label: `${title} resistance`, prefix: `qmd_structure_${scope}_resistance`, scope, side: "resistance",
    });
  });
  pushStructureSwingLevels(zones, rows, chartEnd);
  pushStructureEvents(zones, rows, chartEnd);
  pushGenericStructureReferences(zones, rows, chartEnd);
  return zones;
}

function pushStructureSwingLevels(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
) {
  ([
    ["micro", "μH", "μL", "Micro"],
    ["tactical", "TH", "TL", "Tactical"],
    ["context", "CH", "CL", "Context"],
  ] as const).forEach(([scope, highTag, lowTag, title]) => {
    ([
      ["high", highTag, "var(--danger)", "swing-high"],
      ["low", lowTag, "var(--success)", "swing-low"],
    ] as const).forEach(([side, compactLabel, color, annotationKind]) => {
      pushTrailingLevelZones(zones, rows, `qmd_structure_${scope}_swing_${side}`, chartEnd, LEVEL_SOURCE_HISTORY_BARS, {
        annotationKind,
        borderStyle: "solid",
        borderWidth: scope === "context" ? 2 : 1,
        color,
        compactLabel,
        defaultVisible: false,
        displayItemId: "indicator.qmd_generic_structure",
        fillOpacity: 0.018,
        historicalLabelsDefault: false,
        historicalTagLimitDefault: 0,
        label: `${title} swing ${side}`,
        legendLabel: "Swing references",
        minPixelHeight: 3,
        renderMode: "line",
        settingsId: "indicator.qmd_generic_structure.swings",
      });
    });
  });
}

type StructureZoneSpec = {
  compactLabel: string;
  label: string;
  prefix: string;
  scope: "unified" | "micro" | "tactical" | "context";
  side: "support" | "resistance";
};

function pushStructureZoneSegments(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
  spec: StructureZoneSpec,
) {
  const segments: Array<StructureZoneSpec & {
    confidence: number;
    endIndex: number;
    lower: number;
    price: number;
    startIndex: number;
    strength: number;
    upper: number;
  }> = [];
  const firstIndex = Math.max(0, rows.length - LEVEL_SOURCE_HISTORY_BARS);
  let segmentStart = firstIndex;
  let segmentPrice = finiteNumber(rows[firstIndex]?.[`${spec.prefix}_price`]);
  let segmentLower = finiteNumber(rows[firstIndex]?.[`${spec.prefix}_lower`]);
  let segmentUpper = finiteNumber(rows[firstIndex]?.[`${spec.prefix}_upper`]);
  let segmentStrength = boundedUnit(rows[firstIndex]?.[`${spec.prefix}_strength`]);
  let segmentConfidence = boundedUnit(rows[firstIndex]?.[`${spec.prefix}_confidence`]);
  for (let index = firstIndex + 1; index <= rows.length; index += 1) {
    const nextPrice = index < rows.length ? finiteNumber(rows[index]?.[`${spec.prefix}_price`]) : Number.NaN;
    const nextLower = index < rows.length ? finiteNumber(rows[index]?.[`${spec.prefix}_lower`]) : Number.NaN;
    const nextUpper = index < rows.length ? finiteNumber(rows[index]?.[`${spec.prefix}_upper`]) : Number.NaN;
    const nextStrength = index < rows.length ? boundedUnit(rows[index]?.[`${spec.prefix}_strength`]) : Number.NaN;
    const nextConfidence = index < rows.length ? boundedUnit(rows[index]?.[`${spec.prefix}_confidence`]) : Number.NaN;
    if (index < rows.length
      && structureValueMatches(nextPrice, segmentPrice)
      && structureValueMatches(nextLower, segmentLower)
      && structureValueMatches(nextUpper, segmentUpper)
      && evidenceBucket(nextStrength) === evidenceBucket(segmentStrength)
      && evidenceBucket(nextConfidence) === evidenceBucket(segmentConfidence)) continue;
    segments.push({
      ...spec,
      confidence: segmentConfidence,
      endIndex: index,
      lower: segmentLower,
      price: segmentPrice,
      startIndex: segmentStart,
      strength: segmentStrength,
      upper: segmentUpper,
    });
    segmentStart = index;
    segmentPrice = nextPrice;
    segmentLower = nextLower;
    segmentUpper = nextUpper;
    segmentStrength = nextStrength;
    segmentConfidence = nextConfidence;
  }

  const historicalPolicy = {
    context: { maxSegments: 3, minConfidence: 0.5, minDurationSeconds: 300, minStrength: 0.45 },
    micro: { maxSegments: 0, minConfidence: 1, minDurationSeconds: Number.POSITIVE_INFINITY, minStrength: 1 },
    tactical: { maxSegments: 2, minConfidence: 0.5, minDurationSeconds: 120, minStrength: 0.5 },
    unified: { maxSegments: 3, minConfidence: 0.45, minDurationSeconds: 60, minStrength: 0.5 },
  }[spec.scope];
  const historical = segments
    .filter((segment) => {
      if (segment.endIndex >= rows.length || !(segment.price > 0)) return false;
      const start = rowTimestamp(rows[segment.startIndex]);
      const end = rowTimestamp(rows[Math.min(rows.length - 1, segment.endIndex)]);
      return Number.isFinite(start)
        && Number.isFinite(end)
        && end - start >= historicalPolicy.minDurationSeconds
        && segment.strength >= historicalPolicy.minStrength
        && segment.confidence >= historicalPolicy.minConfidence;
    })
    .sort((left, right) => {
      const leftStart = rowTimestamp(rows[left.startIndex]);
      const leftEnd = rowTimestamp(rows[Math.min(rows.length - 1, left.endIndex)]);
      const rightStart = rowTimestamp(rows[right.startIndex]);
      const rightEnd = rowTimestamp(rows[Math.min(rows.length - 1, right.endIndex)]);
      const leftRank = left.strength * left.confidence * Math.log1p(Math.max(0, leftEnd - leftStart));
      const rightRank = right.strength * right.confidence * Math.log1p(Math.max(0, rightEnd - rightStart));
      return rightRank - leftRank || rightEnd - leftEnd;
    })
    .slice(0, historicalPolicy.maxSegments)
    .sort((left, right) => left.startIndex - right.startIndex);

  historical.forEach((segment) => {
    pushStructureZoneSegment(zones, rows, segment.startIndex, segment.endIndex, chartEnd, segment);
  });
}

function evidenceBucket(value: number) {
  return Number.isFinite(value) ? Math.floor(Math.max(0, Math.min(1, value)) * 10 + 1e-9) : -1;
}

function pushCurrentStructureLevels(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
) {
  const latestIndex = rows.length - 1;
  const latest = rows[latestIndex];
  const candidates = Array.isArray(latest?.qmd_structure_active_levels)
    ? latest.qmd_structure_active_levels.filter(isQmdStructureLevelCandidate)
    : [];
  if (!candidates.length) return;
  const startIndex = Math.max(0, latestIndex - 11);
  const start = rowTimestamp(rows[startIndex]);
  if (!Number.isFinite(start) || !(chartEnd > start)) return;

  ([
    ["support", 1, "var(--success)", "S"],
    ["resistance", -1, "var(--danger)", "R"],
  ] as const).forEach(([sideName, side, color, shortSide]) => {
    const sideCandidates = candidates
      .filter((candidate) => candidate.side === side)
      .sort((left, right) => left.distance - right.distance || right.evidence_score - left.evidence_score);
    const strongest = sideCandidates.reduce<QmdStructureLevelCandidate | null>(
      (best, candidate) => !best || candidate.evidence_score > best.evidence_score ? candidate : best,
      null,
    );
    sideCandidates.forEach((candidate, index) => {
      const confidence = boundedUnit(candidate.confidence);
      const strength = boundedUnit(candidate.strength);
      const strongestLevel = strongest === candidate;
      zones.push({
        annotationKind: side > 0 ? "liquidity-support" : "liquidity-resistance",
        axisLabelDefault: index === 0,
        borderColor: color,
        borderOpacity: 0,
        borderWidth: 0,
        color,
        compactLabel: `${shortSide}${index + 1}${strongestLevel ? "*" : ""} · ${Math.round(confidence * 100)}%`,
        confidence,
        currentLevelDistanceRank: index + 1,
        currentLevelSide: sideName,
        currentLevelStrongest: strongestLevel,
        defaultVisible: true,
        displayItemId: "indicator.qmd_generic_structure",
        end: chartEnd,
        extendToRightEdge: true,
        fillColor: color,
        fillOpacity: 0.04 + 0.16 * confidence,
        historicalLabelsDefault: false,
        historicalTagLimitDefault: 0,
        label: `${sideName === "support" ? "Support" : "Resistance"} ${index + 1} · ${candidate.scale} · ${percentLabel(confidence)} confidence · ${percentLabel(strength)} strength`,
        latest: true,
        legendLabel: "Current support & resistance",
        lower: candidate.lower > 0 ? candidate.lower : candidate.price,
        minPixelHeight: 15,
        settingsId: "indicator.qmd_generic_structure.current-levels",
        start,
        strength,
        upper: candidate.upper > 0 ? candidate.upper : candidate.price,
      });
    });
  });
}

function isQmdStructureLevelCandidate(value: unknown): value is QmdStructureLevelCandidate {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<QmdStructureLevelCandidate>;
  return Number.isFinite(candidate.price)
    && Number(candidate.price) > 0
    && Number.isFinite(candidate.lower)
    && Number.isFinite(candidate.upper)
    && Number.isFinite(candidate.confidence)
    && Number.isFinite(candidate.strength)
    && Number.isFinite(candidate.distance)
    && Number.isFinite(candidate.evidence_score)
    && (candidate.side === 1 || candidate.side === -1);
}

function pushStructureZoneSegment(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  startIndex: number,
  endIndex: number,
  chartEnd: number,
  spec: StructureZoneSpec & { confidence: number; lower: number; price: number; strength: number; upper: number },
) {
  if (!(spec.price > 0) || startIndex >= rows.length) return;
  const latest = endIndex >= rows.length;
  const activeWindowBars = spec.scope === "micro" ? 10 : spec.scope === "tactical" ? 18 : spec.scope === "context" ? 30 : 16;
  const visualStartIndex = latest ? Math.max(startIndex, rows.length - activeWindowBars) : startIndex;
  const start = rowTimestamp(rows[visualStartIndex]);
  const end = endIndex < rows.length ? rowTimestamp(rows[endIndex]) : chartEnd;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
  const support = spec.side === "support";
  const unified = spec.scope === "unified";
  const scopeOpacity = spec.scope === "micro" ? 0.55 : spec.scope === "tactical" ? 0.72 : spec.scope === "context" ? 0.62 : 1;
  const color = support ? "var(--success)" : "var(--danger)";
  zones.push({
    annotationKind: support ? "liquidity-support" : "liquidity-resistance",
    axisLabelDefault: unified,
    borderColor: color,
    borderOpacity: (0.22 + 0.48 * spec.confidence) * scopeOpacity,
    borderStyle: spec.scope === "context" ? "dashed" : spec.scope === "micro" ? "dotted" : "solid",
    borderWidth: unified ? 1.25 + 1.25 * spec.confidence : 0.75 + 0.75 * spec.confidence,
    color,
    compactLabel: spec.compactLabel,
    confidence: spec.confidence,
    defaultVisible: false,
    displayItemId: "indicator.qmd_generic_structure",
    end,
    fillColor: color,
    fillOpacity: (unified ? 0.035 : 0.01) + spec.strength * (unified ? 0.09 : 0.04) * scopeOpacity,
    historicalLabelsDefault: false,
    historicalTagLimitDefault: unified ? 3 : 0,
    label: `${spec.label} · ${percentLabel(spec.strength)} strength · ${percentLabel(spec.confidence)} confidence`,
    latest,
    legendLabel: unified ? "Selected structure zones" : `${spec.scope[0].toUpperCase()}${spec.scope.slice(1)} zones`,
    lower: spec.lower > 0 ? spec.lower : spec.price,
    minPixelHeight: unified ? 8 : 4,
    settingsId: `indicator.qmd_generic_structure.${unified ? "selected-zones" : `${spec.scope}-zones`}`,
    start,
    strength: spec.strength,
    upper: spec.upper > 0 ? spec.upper : spec.price,
  });
}

function pushStructureEvents(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
) {
  const firstIndex = Math.max(0, rows.length - LEVEL_SOURCE_HISTORY_BARS);
  const loadedStart = rowTimestamp(rows[firstIndex]);
  const events: Array<{ confirmedAt: number; direction: number; end: number; kind: string; pivotAt: number; price: number; scale: string }> = [];
  let previousId = firstIndex > 0 ? String(rows[firstIndex - 1]?.qmd_structure_event_id || "") : "";
  for (let index = firstIndex; index < rows.length; index += 1) {
    const eventId = String(rows[index]?.qmd_structure_event_id || "");
    const kind = String(rows[index]?.qmd_structure_event_kind || "").toLowerCase();
    if (!eventId || eventId === "0" || eventId === previousId || !["bos", "choch"].includes(kind)) {
      previousId = eventId;
      continue;
    }
    const price = finiteNumber(rows[index]?.qmd_structure_event_price);
    const direction = finiteNumber(rows[index]?.qmd_structure_event_direction);
    const pivotAt = finiteNumber(rows[index]?.qmd_structure_event_pivot_at_ms) / 1000;
    const confirmedAt = finiteNumber(rows[index]?.qmd_structure_event_at_ms) / 1000;
    const end = index + 1 < rows.length ? rowTimestamp(rows[index + 1]) : chartEnd;
    if (!(price > 0) || confirmedAt < loadedStart || !Number.isFinite(pivotAt) || !Number.isFinite(confirmedAt) || !(end > pivotAt)) {
      previousId = eventId;
      continue;
    }
    const scale = String(rows[index]?.qmd_structure_event_scale || "").toLowerCase();
    events.push({ confirmedAt, direction, end, kind, pivotAt, price, scale });
    previousId = eventId;
  }
  events.slice(-8).forEach(({ confirmedAt, direction, end, kind, pivotAt, price, scale }) => {
    const bullish = direction > 0;
    const label = kind === "choch" ? "CHoCH" : "BoS";
    zones.push({
      annotationKind: kind as "bos" | "choch",
      borderColor: bullish ? "var(--success)" : "var(--danger)",
      borderOpacity: 0.82,
      borderStyle: kind === "choch" ? "dashed" : "solid",
      borderWidth: scale === "context" ? 3 : scale === "tactical" ? 2.5 : 2,
      color: bullish ? "var(--success)" : "var(--danger)",
      compactLabel: `${label}${bullish ? "+" : "-"}`,
      displayItemId: "indicator.qmd_generic_structure",
      end,
      eventTime: confirmedAt,
      fillOpacity: 0,
      historicalLabelsDefault: true,
      historicalTagLimitDefault: 5,
      label: `${label}${bullish ? "+" : "-"} · ${scale || "structure"} · ${formatLevelPrice(price)}`,
      latest: false,
      legendLabel: "Structure breaks",
      lower: price,
      minPixelHeight: 1,
      renderMode: "line",
      settingsId: "indicator.qmd_generic_structure.breaks",
      start: pivotAt,
      upper: price,
      zoneHeightMode: "price",
    });
  });
}

function pushGenericStructureReferences(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
) {
  const specs = [
    ["qmd_structure_session_high", "Session high", "Sess H", "var(--info)", "session", "Session H/L", false],
    ["qmd_structure_session_low", "Session low", "Sess L", "var(--info)", "session", "Session H/L", false],
    ["qmd_structure_premarket_high", "Premarket high", "PM H", "var(--warning)", "premarket", "Premarket H/L", true],
    ["qmd_structure_premarket_low", "Premarket low", "PM L", "var(--warning)", "premarket", "Premarket H/L", true],
    ["qmd_structure_opening_range_high", "Opening range high", "OR H", "var(--foreground)", "opening-range", "Opening range", true],
    ["qmd_structure_opening_range_low", "Opening range low", "OR L", "var(--foreground)", "opening-range", "Opening range", true],
    ["qmd_structure_trade_volume_poc", "Eligible-trade volume POC", "POC", "var(--primary)", "poc", "Trade-volume POC", true],
    ["qmd_structure_luld_upper", "Estimated LULD upper", "LULD U", "var(--danger)", "luld", "Estimated LULD", false],
    ["qmd_structure_luld_lower", "Estimated LULD lower", "LULD L", "var(--danger)", "luld", "Estimated LULD", false],
    ["qmd_structure_52_week_high", "52-week high", "52W H", "var(--warning)", "52-week", "52-week H/L", false],
    ["qmd_structure_52_week_low", "52-week low", "52W L", "var(--info)", "52-week", "52-week H/L", false],
    ["qmd_structure_prior_month_high", "Prior-month high", "PrevM H", "var(--primary)", "prior-month", "Prior month H/L/C", false],
    ["qmd_structure_prior_month_low", "Prior-month low", "PrevM L", "var(--primary)", "prior-month", "Prior month H/L/C", false],
    ["qmd_structure_prior_month_close", "Prior-month close", "PrevM C", "var(--muted-foreground)", "prior-month", "Prior month H/L/C", false],
    ["qmd_structure_nearest_round", "Nearest round price", "Round", "var(--muted-foreground)", "round", "Round price", false],
  ] as const;
  specs.forEach(([column, label, compactLabel, color, settingsSuffix, legendLabel, axisLabelDefault]) => {
    const settingsGroup = ["session", "premarket"].includes(settingsSuffix)
      ? "session-levels"
      : ["52-week", "prior-month"].includes(settingsSuffix)
        ? "higher-timeframe"
        : settingsSuffix;
    const groupedLegendLabel = settingsGroup === "session-levels"
      ? "Session & premarket"
      : settingsGroup === "higher-timeframe"
        ? "Higher-timeframe levels"
        : legendLabel;
    pushTrailingLevelZones(zones, rows, column, chartEnd, LEVEL_SOURCE_HISTORY_BARS, {
      annotationKind: "level",
      axisLabelDefault,
      color,
      compactLabel,
      defaultVisible: ["opening-range", "poc"].includes(settingsGroup),
      displayItemId: "indicator.qmd_generic_structure",
      fillOpacity: 0.025,
      historicalLabelsDefault: false,
      historicalTagLimitDefault: 0,
      label,
      legendLabel: groupedLegendLabel,
      minPixelHeight: 3,
      renderMode: "line",
      settingsId: `indicator.qmd_generic_structure.reference.${settingsGroup}`,
    });
  });
}

type LevelZoneStyle = {
  annotationKind: "level" | "liquidity-resistance" | "liquidity-support" | "swing-high" | "swing-low";
  axisLabelDefault?: boolean;
  borderStyle?: string;
  borderWidth?: number;
  color: string;
  compactLabel: string;
  confidence?: number;
  defaultVisible?: boolean;
  displayItemId: string;
  fillOpacity: number;
  historicalLabelsDefault?: boolean;
  historicalTagLimitDefault?: number;
  label: string;
  legendLabel: string;
  minPixelHeight: number;
  renderMode?: "line" | "zone";
  settingsId: string;
  strength?: number;
};

const LEVEL_SOURCE_HISTORY_BARS = 500;


function pushTrailingLevelZones(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  column: string,
  chartEnd: number,
  barCount: number,
  style: LevelZoneStyle,
) {
  const firstIndex = Math.max(0, rows.length - Math.max(1, barCount));
  let segmentStart = firstIndex;
  let segmentValue = finiteNumber(rows[firstIndex]?.[column]);
  for (let index = firstIndex + 1; index <= rows.length; index += 1) {
    const nextValue = index < rows.length ? finiteNumber(rows[index][column]) : Number.NaN;
    if (index < rows.length && pricesMatch(nextValue, segmentValue)) continue;
    pushHistoricalLevelSegment(zones, rows, segmentStart, index, segmentValue, chartEnd, style);
    segmentStart = index;
    segmentValue = nextValue;
  }
}


function pushHistoricalLevelSegment(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  startIndex: number,
  endIndex: number,
  value: number,
  chartEnd: number,
  style: LevelZoneStyle,
) {
  if (!(value > 0) || startIndex >= rows.length) return;
  const start = rowTimestamp(rows[startIndex]);
  const end = endIndex < rows.length ? rowTimestamp(rows[endIndex]) : chartEnd;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
  zones.push({
    annotationKind: style.annotationKind,
    axisLabelDefault: style.axisLabelDefault,
    borderColor: style.color,
    borderOpacity: Math.min(0.4, style.fillOpacity * 2.5),
    borderStyle: style.borderStyle ?? "solid",
    borderWidth: style.borderWidth ?? 1,
    color: style.color,
    compactLabel: style.compactLabel,
    confidence: style.confidence,
    defaultVisible: style.defaultVisible,
    displayItemId: style.displayItemId,
    end,
    fillColor: style.color,
    fillOpacity: style.fillOpacity,
    historicalLabelsDefault: style.historicalLabelsDefault,
    historicalTagLimitDefault: style.historicalTagLimitDefault,
    label: `${style.label} · ${formatLevelPrice(value)}`,
    latest: endIndex >= rows.length,
    legendLabel: style.legendLabel,
    lower: value,
    minPixelHeight: style.minPixelHeight,
    renderMode: style.renderMode ?? "zone",
    settingsId: style.settingsId,
    start,
    strength: style.strength,
    upper: value,
    zoneHeightMode: "fixed_px",
  });
}

function rowTimestamp(row?: HistoricalIndicator) { return row ? Date.parse(String(row.bar_start)) / 1000 : Number.NaN; }

function finiteNumber(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function boundedUnit(value: unknown) {
  return Math.max(0, Math.min(1, finiteNumber(value)));
}

function pricesMatch(left: number, right: number) {
  return left > 0 && right > 0 && Math.abs(left - right) <= Math.max(0.00005, Math.abs(right) * 1e-8);
}

function structureValueMatches(left: number, right: number) {
  return (left <= 0 && right <= 0) || pricesMatch(left, right);
}

function percentLabel(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function formatLevelPrice(value: number) {
  return value >= 1 ? `$${value.toFixed(2)}` : `$${value.toFixed(4)}`;
}

function historicalIndicatorSeries(rows: HistoricalIndicator[], target: "oscillator" | "price", visibleIndicators: string[]): ChartPayload["overlay_series"] {
  const visible = new Set(visibleIndicators);
  const latestMicrostructure = [...rows].reverse().find((row) => Number.isFinite(Number(row.microstructure_unified_signal)));
  const latestAnchoredFlow = [...rows].reverse().find((row) => Number.isFinite(Number(row.microstructure_cumulative_level1_ofi)) && Number.isFinite(Number(row.microstructure_cumulative_signed_volume_delta)));
  return INDICATOR_SERIES.filter((spec) => visible.has(spec.displayItemId) && (spec.pane === "price" ? "price" : "oscillator") === target).map((spec) => ({
    ...( "autoscaleMax" in spec ? { autoscaleMax: spec.autoscaleMax, autoscaleMin: spec.autoscaleMin } : {}),
    ...( "autoscaleScope" in spec ? { autoscaleScope: spec.autoscaleScope } : {}),
    ...( "axisTitle" in spec ? { axisTitle: spec.axisTitle } : {}),
    color: spec.color,
    ...( "colorMode" in spec ? { colorMode: spec.colorMode } : {}),
    column: spec.column,
    data: rows.map((row) => indicatorSeriesPoint(row, spec.column, "colorMode" in spec ? spec.colorMode : undefined)).filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value)),
    ...( "defaultVisible" in spec ? { defaultVisible: Boolean(spec.defaultVisible) } : {}),
    displayItemId: spec.displayItemId,
    label: spec.column === "microstructure_unified_signal"
      ? microstructureUnifiedLabel(latestMicrostructure)
      : spec.column === "microstructure_anchored_flow_relationship"
        ? anchoredFlowRelationshipLabel(latestAnchoredFlow)
        : spec.label,
    ...( "lastValueVisible" in spec ? { lastValueVisible: Boolean(spec.lastValueVisible) } : {}),
    ...( "lineStyle" in spec ? { lineStyle: spec.lineStyle } : {}),
    lineWidth: "lineWidth" in spec ? spec.lineWidth : 1,
    ...( "opacity" in spec ? { opacity: spec.opacity } : {}),
    paneKey: spec.pane,
    ...( "priceScaleId" in spec ? { priceScaleId: spec.priceScaleId } : {}),
    style: "style" in spec ? spec.style : "line",
  }));
}

function indicatorSeriesPoint(row: HistoricalIndicator, column: string, colorMode?: string) {
  const time = Date.parse(String(row.bar_start)) / 1000;
  if (column === "microstructure_anchored_flow_relationship") {
    const relationship = anchoredFlowRelationship(String(row.microstructure_anchored_flow_relationship || "neutral"), Number(row.microstructure_anchored_flow_relationship_score));
    return { color: relationship.color, time, value: relationship.value };
  }
  return {
    ...(colorMode === "confidence-sign" ? { confidence: boundedUnit(row.qmd_structure_confidence) } : {}),
    ...(column === "microstructure_unified_signal"
      ? { tone: microstructureActionTone(String(row.microstructure_unified_action || "WAIT")) }
      : qmdDirectionalColumn(column)
        ? { tone: microstructureValueTone(Number(row[column])) }
        : {}),
    time,
    value: Number(row[column]),
  };
}

function anchoredFlowRelationship(value: string, score: number) {
  if (value === "bullish_confirmation") return { color: "var(--success)", label: "Bullish confirmation", value: 1 };
  if (value === "bearish_confirmation") return { color: "var(--danger)", label: "Bearish confirmation", value: -1 };
  if (value === "bullish_absorption") return { color: "var(--info)", label: "Bullish absorption", value: 0.55 };
  if (value === "bearish_absorption") return { color: "var(--warning)", label: "Bearish absorption", value: -0.55 };
  return { color: "var(--muted-foreground)", label: "Neutral", value: Number.isFinite(score) ? score : 0 };
}

function anchoredFlowRelationshipLabel(row?: HistoricalIndicator) {
  if (!row) return "Relationship · waiting";
  return `Relationship · ${anchoredFlowRelationship(String(row.microstructure_anchored_flow_relationship || "neutral"), Number(row.microstructure_anchored_flow_relationship_score)).label}`;
}

function microstructureActionTone(action: string): "buy" | "neutral" | "sell" {
  if (action.toUpperCase() === "BUY") return "buy";
  if (action.toUpperCase() === "SELL") return "sell";
  return "neutral";
}

function qmdDirectionalColumn(column: string) {
  return column.startsWith("microstructure_")
    && !column.endsWith("_confidence")
    && column !== "microstructure_regime_reliability"
    && column !== "microstructure_arrival_rate_per_second";
}

function microstructureValueTone(value: number): "buy" | "neutral" | "sell" {
  if (value > 0) return "buy";
  if (value < 0) return "sell";
  return "neutral";
}

function microstructureUnifiedLabel(row?: HistoricalIndicator) {
  const action = String(row?.microstructure_unified_action || "WAIT").toUpperCase();
  const confidence = Number(row?.microstructure_unified_confidence);
  return `Signal ${action}${Number.isFinite(confidence) ? ` · ${Math.round(confidence)}%` : ""}`;
}

function PreviewTable({ columns, onSymbolSelect, rows }: { columns: string[]; onSymbolSelect?: (symbol: string) => void; rows: PreviewRow[] }) {
  const tickerColumns = columns.filter(isPreviewTickerColumn);
  const presentations = useTickerPresentations(rows.flatMap((row) => tickerColumns.map((column) => String(row[column] || ""))));
  if (!rows.length) return <EmptyState label="No point-in-time rows" />;
  return <div className="canvas-preview-table-wrap"><table className="canvas-preview-table"><thead><tr>{columns.map((column) => <th key={column}>{labelFor(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={previewRowKey(row, columns, index)}>{columns.map((column) => <td key={column}><PreviewCell column={column} onSymbolSelect={onSymbolSelect} presentations={presentations} row={row} /></td>)}</tr>)}</tbody></table></div>;
}

function PreviewCell({ column, onSymbolSelect, presentations, row }: { column: string; onSymbolSelect?: (symbol: string) => void; presentations: ReturnType<typeof useTickerPresentations>; row: PreviewRow }) {
  if (isPreviewTickerColumn(column)) {
    const ticker = String(row[column] || "").trim().toUpperCase();
    const identity = <TickerIdentity logoUrl={presentations[ticker]?.logo_url} ticker={ticker} />;
    return column === "symbol" && onSymbolSelect ? <button className="canvas-symbol-link" onClick={() => onSymbolSelect(ticker)} type="button">{identity}</button> : identity;
  }
  if (isPreviewTimeColumn(column)) return <MarketTime value={String(row[column] || "")} />;
  return formatCell(row[column], column);
}

function isPreviewTickerColumn(column: string) { return ["symbol", "ticker", "candidate_massive_ticker"].includes(column.toLowerCase()); }
function isPreviewTimeColumn(column: string) { const normalized = column.toLowerCase(); return normalized === "time" || normalized.endsWith("_time") || normalized.endsWith("_at") || normalized.endsWith("_at_utc"); }

function PortfolioPreview({ data, settings }: { data: CanvasPreview["portfolio"]; settings: ContainerSettings["portfolio"] }) {
  return <div className="canvas-portfolio-preview"><div className="canvas-metric-row"><Metric label="Net liquidation" value={money(data.summary.netLiquidation)} /><Metric label="Available" value={money(data.summary.availableFunds)} />{settings.showPnl ? <Metric label="Unrealized P&L" value={money(data.summary.unrealizedPnl)} /> : null}</div>{settings.showPositions ? <PreviewTable columns={["ticker", "position", "mktPrice", "avgCost", "unrealizedPnl"]} rows={data.positions} /> : null}</div>;
}

function StrategyPreview({ data, showSignals }: { data: CanvasPreview["strategy"]; showSignals: boolean }) {
  return <div className="canvas-strategy-preview"><div><span>Strategy</span><strong>{data.strategy_id}</strong></div><div><span>Revision</span><strong>v{data.revision}</strong></div><div><span>State</span><strong>{data.state}</strong></div>{showSignals ? <PreviewTable columns={["time", "symbol", "signal", "value"]} rows={data.signals} /> : null}</div>;
}

function containerFields(id: WorkspaceContainerId, settings: ContainerSettings, linkContext: CanvasLinkContext, updateSettings: SettingsUpdater, onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void) {
  if (id === "microstructure") return <><TextField label="Symbol" onChange={(value) => { const symbol = value.toUpperCase(); updateSettings((state) => ({ ...state, chart: { ...state.chart, symbol } })); onLinkContextChange({ symbol }); }} value={linkContext.symbol} /><div className="canvas-settings-note">The symbol follows the selected link color. Quotes and trades share one QMD event stream; each table retains its latest 1,024 decoded rows at the shared historical clock.</div></>;
  if (id === "facts") return <><TextField label="Symbol" onChange={(value) => { const symbol = value.toUpperCase(); updateSettings((state) => ({ ...state, chart: { ...state.chart, symbol } })); onLinkContextChange({ symbol }); }} value={linkContext.symbol} /><div className="canvas-settings-note">Facts follow the selected link color and shared point-in-time clock. Reported values remain distinct from explicitly labeled estimates, ranges, and upper bounds.</div></>;
  const settingsId = id as keyof ContainerSettings;
  const current = settings[settingsId] as Record<string, unknown>;
  function patch(value: Record<string, unknown>) { updateSettings((state) => ({ ...state, [id]: { ...(state[settingsId] as Record<string, unknown>), ...value } })); }
  if (id === "chart") return <><TextField label="Symbol" onChange={(value) => { patch({ symbol: value.toUpperCase() }); onLinkContextChange({ symbol: value.toUpperCase() }); }} value={linkContext.symbol} /><SelectField label="Bar interval" onChange={(value) => patch({ timeframe: value as CanvasChartTimeframe })} optionLabel={formatChartTimeframe} options={HISTORICAL_TIMEFRAMES} value={settings.chart.timeframe} /><CheckField checked={Boolean(current.showVolume)} label="Show volume" onChange={(value) => patch({ showVolume: value })} /></>;
  if (id === "portfolio") return <><CheckField checked={Boolean(current.showPositions)} label="Show positions" onChange={(value) => patch({ showPositions: value })} /><CheckField checked={Boolean(current.showPnl)} label="Show P&L" onChange={(value) => patch({ showPnl: value })} /></>;
  if (id === "strategy") return <CheckField checked={Boolean(current.showSignals)} label="Show recent signals" onChange={(value) => patch({ showSignals: value })} />;
  if (id === "scanner") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showActivity)} label="Show market activity" onChange={(value) => patch({ showActivity: value })} /></>;
  if (id === "orders") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showOrderIds)} label="Show order IDs" onChange={(value) => patch({ showOrderIds: value })} /></>;
  if (id === "fills") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showCommission)} label="Show commission" onChange={(value) => patch({ showCommission: value })} /></>;
  if (id === "news") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["1", "6", "24", "168", "720"]} value={String(current.lookbackHours)} /><SelectField label="News type" onChange={(value) => patch({ kind: value })} options={["all", "company", "insights", "analyst", "multi", "ai", "market"]} value={String(current.kind)} /><SelectField label="Text coverage" onChange={(value) => patch({ content: value })} options={["all", "full", "title"]} value={String(current.content)} /></>;
  if (id === "ticker_news") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["24", "72", "168", "720"]} value={String(current.lookbackHours)} /><CheckField checked={Boolean(current.showTeaser)} label="Show teaser" onChange={(value) => patch({ showTeaser: value })} /><div className="canvas-settings-note">Ticker comes from the selected link color. Hot, cold, and old states use the shared clock.</div></>;
  if (id === "news_detail") return <div className="canvas-settings-note">This reader follows the most recently selected news article in this canvas.</div>;
  if (id === "sec") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["24", "72", "168", "720", "8760"]} value={String(current.lookbackHours)} /><SelectField label="Content" onChange={(value) => patch({ content: value })} options={["all", "readable", "xbrl"]} value={String(current.content)} /><div className="canvas-settings-note">Search, ticker, and filing labels are available in the container query bar. Results are constrained to the shared point-in-time clock.</div></>;
  if (id === "ticker_sec") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["24", "72", "168", "720", "8760"]} value={String(current.lookbackHours)} /><div className="canvas-settings-note">Ticker follows the selected link color. Hot means accepted within four hours, cold within 24 hours, and old is older.</div></>;
  if (id === "sec_detail") return <div className="canvas-settings-note">This reader follows the most recently selected filing in this canvas.</div>;
  if (id === "xbrl") return <><NumberField label="Last N facts" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showPeriod)} label="Show fiscal period" onChange={(value) => patch({ showPeriod: value })} /></>;
  return <NumberField label="Last N events" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} />;
}

function TextField({ label, onChange, value }: { label: string; onChange: (value: string) => void; value: string }) { return <label><span>{label}</span><input onChange={(event) => onChange(event.target.value)} value={value} /></label>; }
function NumberField({ label, max = 20, onChange, value }: { label: string; max?: number; onChange: (value: number) => void; value: number }) { return <label><span>{label}</span><input max={max} min={1} onChange={(event) => onChange(Math.max(1, Math.min(max, Number(event.target.value))))} type="number" value={value} /></label>; }
function SelectField({ label, onChange, optionLabel = (option) => option, options, value }: { label: string; onChange: (value: string) => void; optionLabel?: (value: string) => string; options: readonly string[]; value: string }) { return <label><span>{label}</span><select onChange={(event) => onChange(event.target.value)} value={value}>{options.map((option) => <option key={option} value={option}>{optionLabel(option)}</option>)}</select></label>; }
function CheckField({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) { return <label className="canvas-check-field"><input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" /><span>{label}</span></label>; }
function Metric({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong>{value}</strong></div>; }
function EmptyState({ label }: { label: string }) { return <div className="canvas-preview-empty">{label}</div>; }

function readSettings(): ContainerSettings {
  try {
    const stored = JSON.parse(window.localStorage.getItem(CANVAS_SETTINGS_STORAGE_KEY) ?? "{}") as Partial<ContainerSettings>;
    return normalizeSettings(stored);
  } catch {
    return cloneDefaultSettings();
  }
}

function normalizeSettings(stored: Partial<ContainerSettings>): ContainerSettings {
  const storedIndicators = Array.isArray(stored.chart?.visibleIndicators) ? stored.chart.visibleIndicators : DEFAULT_SETTINGS.chart.visibleIndicators;
  const replacedStructure = storedIndicators.some((id) => ["indicator.qmd_liquidity_levels", "indicator.market_structure_levels", "indicator.qmd_level_confluence"].includes(id));
  const canonicalIndicators = storedIndicators.filter((id) => !["indicator.qmd_liquidity_levels", "indicator.market_structure_levels", "indicator.qmd_level_confluence"].includes(id));
  if (replacedStructure && !canonicalIndicators.includes("indicator.qmd_generic_structure")) canonicalIndicators.push("indicator.qmd_generic_structure");
  const migratedIndicators = stored.version === DEFAULT_SETTINGS.version || canonicalIndicators.includes("indicator.macd") ? canonicalIndicators : [...canonicalIndicators, "indicator.macd"];
  const visibleIndicators = stored.version === DEFAULT_SETTINGS.version || migratedIndicators.includes("indicator.microstructure_outlook") ? migratedIndicators : [...migratedIndicators, "indicator.microstructure_outlook"];
  const timeframe = HISTORICAL_TIMEFRAMES.includes(stored.chart?.timeframe as CanvasChartTimeframe) ? stored.chart!.timeframe! : DEFAULT_SETTINGS.chart.timeframe;
  return {
    version: DEFAULT_SETTINGS.version,
    chart: { ...DEFAULT_SETTINGS.chart, ...(stored.chart ?? {}), timeframe, visibleIndicators: [...visibleIndicators] },
    microstructure: { limit: 1024 },
    fills: { ...DEFAULT_SETTINGS.fills, ...(stored.fills ?? {}) },
    journal: { ...DEFAULT_SETTINGS.journal, ...(stored.journal ?? {}) },
    news: { ...DEFAULT_SETTINGS.news, ...(stored.news ?? {}) },
    ticker_news: { ...DEFAULT_SETTINGS.ticker_news, ...(stored.ticker_news ?? {}) },
    news_detail: {},
    orders: { ...DEFAULT_SETTINGS.orders, ...(stored.orders ?? {}) },
    portfolio: { ...DEFAULT_SETTINGS.portfolio, ...(stored.portfolio ?? {}) },
    scanner: { ...DEFAULT_SETTINGS.scanner, ...(stored.scanner ?? {}) },
    sec: { ...DEFAULT_SETTINGS.sec, ...(stored.sec ?? {}) },
    ticker_sec: { ...DEFAULT_SETTINGS.ticker_sec, ...(stored.ticker_sec ?? {}) },
    sec_detail: {},
    strategy: { ...DEFAULT_SETTINGS.strategy, ...(stored.strategy ?? {}) },
    xbrl: { ...DEFAULT_SETTINGS.xbrl, ...(stored.xbrl ?? {}) },
  };
}

function cloneDefaultSettings() { return normalizeSettings(DEFAULT_SETTINGS); }
function instanceSettings(registry: CanvasRegistry, instanceId: string) {
  const stored = registry.instanceSettings[instanceId] as Partial<ContainerSettings> | undefined;
  return stored ? normalizeSettings(stored) : instanceId === "chart" ? readSettings() : cloneDefaultSettings();
}
function readPreviewContext(): CanvasPreviewContext { try { const parsed = JSON.parse(window.localStorage.getItem(CANVAS_PREVIEW_CONTEXT_STORAGE_KEY) || "null") as CanvasPreviewContext | null; return parsed?.sessionDate && parsed?.previewTime ? parsed : { previewTime: "09:45", sessionDate: previousWeekdayIsoDate() }; } catch { return { previewTime: "09:45", sessionDate: previousWeekdayIsoDate() }; } }
function previousWeekdayIsoDate() { const value = new Date(); value.setDate(value.getDate() - 1); while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1); const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000); return local.toISOString().slice(0, 10); }
function previewClockReadings(context: CanvasPreviewContext) {
  const instant = dateInTimeZone(context.sessionDate, context.previewTime, "America/New_York");
  const format = (timeZone: string, includeDate: boolean) => {
    const detail = includeDate ? new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "short", timeZone, year: "numeric" }).format(instant) : "";
    const value = new Intl.DateTimeFormat("en-US", { hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone }).format(instant);
    return { detail, value };
  };
  return [
    { label: "ET", ...format("America/New_York", true) },
    { label: "VAN", ...format("America/Vancouver", true) },
  ];
}
function dateInTimeZone(date: string, time: string, timeZone: string) {
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  const desiredUtc = Date.UTC(year, month - 1, day, hour, minute);
  let instant = new Date(desiredUtc);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { day: "2-digit", hour: "2-digit", hourCycle: "h23", minute: "2-digit", month: "2-digit", timeZone, year: "numeric" }).formatToParts(instant).filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
    const representedUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute);
    instant = new Date(instant.getTime() + desiredUtc - representedUtc);
  }
  return instant;
}
function labelFor(value: string) { return value.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2"); }
function formatChartTimeframe(value: string) {
  if (value === "100ms") return "100 milliseconds";
  if (value === "1d") return "Daily";
  if (value === "1mo") return "Monthly";
  const match = value.match(/^(\d+)([smh])$/);
  if (!match) return value;
  const count = Number(match[1]);
  const unit = match[2] === "s" ? "second" : match[2] === "m" ? "minute" : "hour";
  return `${count} ${unit}${count === 1 ? "" : "s"}`;
}
function statusLabel(value: WorkspaceWindowStatus) { return value.charAt(0).toUpperCase() + value.slice(1); }
function previewRowKey(row: PreviewRow, columns: string[], index: number) { return `${columns.map((column) => String(row[column] ?? "")).join("|")}|${index}`; }
function money(value: unknown) { return typeof value === "number" ? new Intl.NumberFormat("en-US", { currency: "USD", style: "currency" }).format(value) : "—"; }
function formatPreviewDate(value?: string) { if (!value) return "this date"; return new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T12:00:00Z`)); }
function formatCell(value: unknown, column: string) { if (value === null || value === undefined || value === "") return "—"; if (column.includes("time") || column.includes("at_utc")) { const date = new Date(String(value)); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }).format(date); } if (typeof value === "number") return new Intl.NumberFormat("en-US", { maximumFractionDigits: column.includes("pct") ? 2 : 4 }).format(value); if (Array.isArray(value)) return value.join(", "); return String(value); }
function containerTitle(id: WorkspaceContainerId) { return TRADING_WORKSPACE_CONTAINERS.find((definition) => definition.id === id)?.title ?? id; }
function workspaceContainerKind(instanceId: string, state?: CanvasWorkspaceState | null): WorkspaceContainerId {
  const stored = state?.instances[instanceId];
  if (stored) return stored;
  return TRADING_WORKSPACE_CONTAINERS.find((definition) => instanceId === definition.id || instanceId.startsWith(`${definition.id}-`))?.id ?? "chart";
}
function containerInstanceTitle(kind: WorkspaceContainerId, instanceId: string, state: CanvasWorkspaceState | null, registry: CanvasRegistry) {
  const matchingIds = (state?.openIds ?? [instanceId]).filter((candidateId) => workspaceContainerKind(candidateId, state) === kind);
  if (kind === "chart") {
    const timeframe = instanceSettings(registry, instanceId).chart.timeframe;
    const matchingTimeframeIds = matchingIds.filter((candidateId) => instanceSettings(registry, candidateId).chart.timeframe === timeframe);
    const duplicateIndex = matchingTimeframeIds.indexOf(instanceId);
    const readableTimeframe = formatChartTimeframe(timeframe).replace(/\b\w/g, (letter) => letter.toUpperCase());
    const base = timeframe === "1d" ? "Daily Chart" : timeframe === "1mo" ? "Monthly Chart" : `${readableTimeframe} Chart`;
    return matchingTimeframeIds.length > 1 && duplicateIndex >= 0 ? `${base} ${duplicateIndex + 1}` : base;
  }
  const index = matchingIds.indexOf(instanceId);
  const base = containerTitle(kind);
  return matchingIds.length > 1 && index >= 0 ? `${base} ${index + 1}` : base;
}
function focusCanvasState(canvasId: string, requestedInstanceId?: string): CanvasWorkspaceState | null {
  const stored = readCanvasWorkspaceState(canvasId);
  if (!requestedInstanceId) return stored;
  const kind = workspaceContainerKind(requestedInstanceId, stored);
  return { groups: {}, instances: { [requestedInstanceId]: kind }, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts: createFocusLayouts([requestedInstanceId]), openIds: [requestedInstanceId] };
}
function normalizeInheritedLayouts(layouts: Record<string, WorkspaceWindowLayout>, ids: string[]) {
  const fallback = createFocusLayouts(ids);
  return Object.fromEntries(ids.map((id) => [id, { ...(layouts[id] ?? fallback[id]), fullscreen: false, minimized: false }]));
}
function focusLayout(source?: WorkspaceWindowLayout): WorkspaceWindowLayout { const scale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale")) || 1; return { fullscreen: true, h: Math.max(320, Math.floor(window.innerHeight / scale) - 62), minimized: false, w: Math.max(680, Math.floor(window.innerWidth / scale)), x: 0, y: 0, z: Math.max(1, source?.z ?? 1) }; }
function offsetLayout(source: WorkspaceWindowLayout, index: number): WorkspaceWindowLayout { const offset = (index % 6) * 18; return { ...source, fullscreen: false, minimized: false, x: offset, y: offset, z: index + 1 }; }

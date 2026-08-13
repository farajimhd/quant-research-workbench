import { Activity, ArrowDown, ArrowUp, ArrowUpDown, BadgeDollarSign, BarChart3, BookOpen, BriefcaseBusiness, Check, ChevronDown, ChevronRight, CircleDollarSign, Clock3, ExternalLink, Filter, Gauge, HelpCircle, Landmark, Link2, MapPin, PanelRightOpen, Plus, RefreshCcw, Search, Save, Settings2, ShieldCheck, Target, Trash2, TriangleAlert, Unlink, WalletCards, X } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MutableRefObject, type ReactNode } from "react";
import type { UTCTimestamp } from "lightweight-charts";

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
  canvasRuntimeRegistryStorageKey,
  canvasRuntimeWorkspaceStorageKey,
  canvasWorkspaceStorageKey,
  createCanvasRecord,
  focusCanvasUrl,
  ensureNewsReaderCanvas,
  readCanvasRegistry,
  readCanvasRuntimeRegistry,
  readCanvasRuntimeOverlayRecord,
  readCanvasWorkspaceState,
  readCanvasWorkspaceStateByStorageKey,
  readReplayCanvasFocusHandoff,
  removeCanvasRecord,
  replayFocusCanvasUrl,
  rebaseCanvasRuntimeOverlay,
  snapshotCanvasWorkspaceState,
  writeReplayCanvasFocusHandoff,
  writeCanvasRegistry,
  writeCanvasRuntimeOverlayRecord,
  writeCanvasWorkspaceState,
  type CanvasAssignedLinkGroupId,
  type CanvasChartTimeframe,
  type CanvasLinkContext,
  type CanvasLinkGroupId,
  type CanvasRegistry,
  type CanvasRuntimeRebase,
  type CanvasWorkspaceState,
} from "../app/canvasWorkspace";
import { latestReplayRun, useReplayRunEvents, type CanvasReplayRun } from "../app/replayRun";
import { ChartPanel, type ChartCatalogKnowledge, type ChartDisplayItem, type ChartPayload, type LiveEntryLine } from "../app/components/ChartPanel";
import { AllNewsContainer, NEWS_ARTICLE_CLASS_OPTIONS, NewsDetailContainer, TickerNewsContainer } from "../app/components/NewsContainers";
import { AllSecContainer, SecDetailContainer, TickerSecContainer } from "../app/components/SecContainers";
import { MarketTime } from "../app/components/MarketTime";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";
import { ChartsQuotesMarketLayout, QuotesTapeContainer, type ChartsQuotesLayoutSettings } from "../app/components/MarketMicrostructureContainers";
import { MarketScannerContainer, migrateMarketScannerSettings, SCANNER_TIMEFRAMES, SignalStreamContainer, StrategyActivityContainer, WatchUniverseContainer, type MarketScannerSettings, type ScannerCustomColumn, type ScannerSnapshotMeta, type ScannerTimeframe, type SignalStreamSettings, type StrategyActivitySettings, type WatchlistRuntimeResponse, type WatchUniverseSettings } from "../app/components/MarketScreenerContainers";
import { StockFactsContainer } from "../app/components/StockFactsContainer";
import { XbrlAnalysisContainer, type XbrlAnalysisSettings } from "../app/components/XbrlAnalysisContainer";
import { TickerIdentity, TickerIdentityWithChange, useTickerPresentations } from "../app/components/TickerIdentity";
import { TRADING_WORKSPACE_LAYOUT_VERSION, TradingWorkspace, createFocusLayouts } from "../app/components/TradingWorkspace";
import type { WorkspaceWindowLayout, WorkspaceWindowMeta, WorkspaceWindowStatus } from "../app/components/WorkspaceCanvas";
import { TRADING_WORKSPACE_CONTAINERS, containerSupportsCanvasLink, containerSupportsSymbolLink, type WorkspaceContainerDefinition, type WorkspaceContainerId } from "../app/tradingWorkspace";
import type { TradingWorkspaceMode } from "../app/tradingWorkspace";
import { DEFAULT_STRATEGY_CHART_PRESENTATION, strategyInvalidationZones, strategyPresentationMarkers, type StrategyAction, type StrategyChartPresentation, type StrategyDecisionEvent } from "../app/strategyPresentation";

type HistoricalBar = { bar_end?: string; bar_start: string; close: number; high: number; is_closed?: boolean; last_event_ts?: string; low: number; open: number; volume: number };
const EMPTY_STRATEGY_DECISIONS: StrategyDecisionEvent[] = [];
type QmdStructureLevelCandidate = {
  level_id: number;
  confidence: number;
  created_at_ms: number;
  distance: number;
  evidence_score: number;
  hold_count: number;
  last_test_at_ms: number;
  lower: number;
  lifecycle: string;
  price: number;
  promotions: Array<{ timeframe: string; promoted_at_ms: number; score: number }>;
  footprint_session_date: string;
  footprint_as_of_ms: number;
  footprint: Array<{ offset_ticks: number; price: number; total_volume: number; buy_volume: number; sell_volume: number; neutral_volume: number; trade_count: number; largest_trade: number }>;
  total_volume: number;
  buy_volume: number;
  sell_volume: number;
  neutral_volume: number;
  trade_count: number;
  side: number;
  strength: number;
  touch_count: number;
  upper: number;
};
type QmdStructureTimeframeState = {
  timeframe: string;
  direction: number;
  swing_high: number;
  swing_low: number;
  support?: Record<string, unknown>;
  resistance?: Record<string, unknown>;
  promoted_level_count: number;
};
type HistoricalIndicator = {
  bar_start: string;
  qmd_structure_active_levels?: QmdStructureLevelCandidate[];
  qmd_structure_timeframe_states?: QmdStructureTimeframeState[];
} & Record<string, number | string | QmdStructureLevelCandidate[] | QmdStructureTimeframeState[] | undefined>;
type PreviewRow = Record<string, unknown>;
type PnlCandleTimeframe = "30m" | "1h" | "1d" | "1M";
type PnlCandle = {
  bucket_start: string;
  bucket_end: string;
  open: string | number;
  high: string | number;
  low: string | number;
  close: string | number;
  net_change: string | number;
  episode_count: number;
};
type PerformanceJournalReport = {
  schema_version: number;
  episode_definition: string;
  summary: Record<string, string | number | null>;
  episodes: PreviewRow[];
  equity_curve: Array<{ time: string; value: string | number; drawdown: string | number }>;
  pnl_candles: Record<PnlCandleTimeframe, PnlCandle[]>;
  strategies: PreviewRow[];
  execution: Record<string, unknown> & { venues?: PreviewRow[] };
  risk: Record<string, string | number | null>;
  scope: Record<string, string | number | null>;
};
type CanonicalTradingPreview = {
  schema_version: number;
  mode: string;
  provider: string;
  as_of: string;
  complete: boolean;
  stale: boolean;
  stale_reason: string;
  accounts: PreviewRow[];
  account_values: PreviewRow[];
  ledger: PreviewRow[];
  positions: PreviewRow[];
  orders: PreviewRow[];
  executions: PreviewRow[];
  closed_trades: PreviewRow[];
  activity: PreviewRow[];
  closed_trades_note: string;
  performance_snapshot?: PerformanceSnapshot;
  performance_journal: PerformanceJournalReport;
  portfolio: {
    metrics: Record<string, string | number>;
    exposure: { long_value?: string | number; short_value?: string | number; net_value?: string | number; gross_value?: string | number; by_currency?: Record<string, string | number>; by_asset_class?: Record<string, string | number> };
    position_count: number;
    working_order_count: number;
    pending_commission_count: number;
    management?: {
      schema_version: number;
      complete: boolean;
      stale: boolean;
      stale_reason: string;
      accounts: Array<{
        account_key: string;
        account_id: string;
        account_class: string;
        mode: string;
        sync_state: string;
        control_mode: string;
        observed_at: string;
        stale_reason: string;
        policy: Record<string, unknown> & { identity?: string };
        available_policies: Array<Record<string, unknown> & { identity: string }>;
        strategy_allocations: Record<string, number>;
        disabled_strategy_allocations: string[];
        metrics: Record<string, string | number>;
        position_count: number;
        working_order_count: number;
        reservations: PreviewRow[];
        allocations: PreviewRow[];
        reconciliation: PreviewRow[];
        continuous_risk: PreviewRow;
        managed_order_groups: Array<{
          group_id: string;
          state: PreviewRow & {
            state?: string;
            current_limit_price?: number | null;
            protection_required_quantity?: number;
            protection_coverage_quantity?: number;
            intent?: PreviewRow & {
              ticker?: string;
              execution_policy?: PreviewRow;
              protection_profile?: PreviewRow;
            };
          };
        }>;
        pending_operational_commands: PreviewRow[];
      }>;
      groups: PreviewRow[];
      recent_decisions: PreviewRow[];
      operational_metrics?: {
        portfolio: {
          decision_count: number;
          disposition_counts: Record<string, number>;
          active_reservation_count: number;
          active_reserved_notional: number;
          reconciliation_issue_count: number;
          pending_command_count: number;
        };
        oms: {
          managed_group_count: number;
          outcome_unknown_count: number;
          unprotected_quantity: number;
          reconciliation_event_count: number;
          reconciliation_failure_count: number;
          last_reconciliation_at?: string | null;
        };
      };
    };
  };
};
type PerformanceSnapshot = {
  as_of: string;
  session_date: string;
  net_pnl_today: string | number;
  open_position_count: number;
  unrealized_pnl: string | number;
  realized_pnl_today: string | number;
  available_cash: string | number;
  available_cash_basis: "available_funds" | "total_cash" | string;
  source?: "performance_snapshot" | "canonical_state_v2";
};
type LivePerformanceState = { data: PerformanceSnapshot | null; status: "loading" | "ready" | "stale" | "error" };
type PerformanceSnapshotResponse = {
  as_of: string;
  complete: boolean;
  stale: boolean;
  stale_reason: string;
  performance_snapshot: PerformanceSnapshot;
};
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
  scanner_meta?: ScannerSnapshotMeta;
  sec: PreviewRow[];
  strategy: {
    automatic: boolean;
    fixture?: boolean;
    revision: number;
    signals: PreviewRow[];
    order_management?: PreviewRow[];
    run_id?: string;
    runtime_mode?: "backtest" | "backtest_debug" | "replay";
    state: string;
    strategy_id: string;
    name?: string;
    profile_id?: string;
    profile_revision?: number;
    deployment?: PreviewRow;
    capabilities?: Array<{ capability_id: string; enabled?: boolean; settings?: PreviewRow }>;
    assignment?: PreviewRow | null;
    assignments?: PreviewRow[];
    definition?: { config?: { direction?: string; parameters?: Record<string, unknown>; parameter_space?: Record<string, unknown> }; name?: string };
    taxonomy?: { indicators?: PreviewRow[]; signals?: PreviewRow[]; presentation?: Partial<StrategyChartPresentation> };
  };
  trading: CanonicalTradingPreview;
  xbrl: PreviewRow[];
};

type CanvasScannerSnapshot = {
  as_of: string;
  errors: Record<string, string>;
  meta: ScannerSnapshotMeta;
  rows: Record<string, unknown>[];
  signal_rows?: Record<string, unknown>[];
  watchlist_runtime?: WatchlistRuntimeResponse;
};
type CanvasContext = { coverage: { event_count: number; session_date: string | null; ticker_count: number }; preview_time: string; session_date: string | null };
type QmdLiveBar = HistoricalBar & { session_date?: string };
type QmdLiveChartPayload = {
  bars: { current?: QmdLiveBar | null; history?: QmdLiveBar[] };
  errors?: Record<string, string>;
  indicators: { current?: HistoricalIndicator | null; history?: HistoricalIndicator[] };
  source?: string;
  stream_interval_ms?: number;
};
type QmdBarHistory = {
  as_of: string;
  market_signal_events: QmdMarketSignalEvent[];
  earliest_session_date: string;
  has_more: boolean;
  has_more_in_session: boolean;
  history: QmdLiveBar[];
  indicators: HistoricalIndicator[];
  indicators_available: boolean;
  indicator_provenance?: QmdIndicatorProvenance;
  structure_events: QmdStructureEvent[];
  structure_level_history: QmdStructureLevelCandidate[];
  next_before: string;
  previous_session_before: string;
  stage?: "bars" | "full";
  ticker: string;
  timeframe: string;
};
type QmdIndicatorProvenance = {
  as_of?: string;
  complete?: boolean;
  calculation_revision?: string;
  corporate_action_revision?: string;
  engine_version?: string;
  indicator_schema_version?: number;
  source?: { source_plan_hash?: string; tiers?: string[] };
  stale_reason?: string;
  warm_up?: { recommended_minimum_bars?: number; returned_bars?: number; status?: string };
};
type QmdMarketSignalEvent = {
  schema_version: number;
  signal_version: number;
  engine_version: string;
  event_id: string;
  signal_id: string;
  signal_key: string;
  producer: string;
  ticker: string;
  working_timeframe: string;
  confirmation_timeframe?: string | null;
  observed_at: string;
  effective_at: string;
  state: "triggered" | "updated" | "resolved" | "expired";
  direction: "bullish" | "bearish" | "neutral";
  score: number;
  rank_score: number;
  confidence: number;
  trigger_reason: string;
  resolution_reason?: string;
  reference_price: number;
  invalidation_price?: number | null;
};
type QmdStructureEvent = {
  algorithm_version: number;
  event_id: number;
  sym: string;
  level_id?: number;
  timeframe: string;
  event_kind: string;
  direction: number;
  price: number;
  lower: number;
  upper: number;
  strength: number;
  confidence: number;
  lifecycle?: string;
  total_volume?: number;
  buy_volume?: number;
  sell_volume?: number;
  neutral_volume?: number;
  trade_count?: number;
  pivot_at: string;
  confirmed_at: string;
};
type ChartHistoryCursor = {
  asOf: string;
  nextBefore: string;
  previousSessionBefore: string;
  sessionDate: string;
};
type CanvasLiveChartState = {
  bars: QmdLiveBar[];
  canLoadEarlier: boolean;
  connected: boolean;
  marketSignalEvents: QmdMarketSignalEvent[];
  error: string;
  historyError: string;
  historyNotice: string;
  indicators: HistoricalIndicator[];
  indicatorsAvailable: boolean;
  indicatorProvenance?: QmdIndicatorProvenance;
  structureEvents: QmdStructureEvent[];
  structureLevelHistory: QmdStructureLevelCandidate[];
  lastUpdateAt: string;
  loadEarlier: () => void;
  loading: boolean;
  loadingEarlier: boolean;
  pointInTime: boolean;
};

type CanvasChartSettings = { showVolume: boolean; symbol: string; timeframe: CanvasChartTimeframe; visibleIndicators: string[] };
type ContainerSettings = {
  version: 27;
  chart: CanvasChartSettings;
  charts_quotes: {
    daily: CanvasChartSettings;
    layout: ChartsQuotesLayoutSettings;
    main: CanvasChartSettings;
    month: CanvasChartSettings;
  };
  microstructure: { limit: number };
  fills: { limit: number; showCommission: boolean };
  positions: { limit: number; showPnl: boolean };
  closed_trades: { limit: number; showFees: boolean };
  activity: { limit: number };
  performance_journal: { limit: number; showRiskMultiple: boolean };
  news: { content: string; endDate: string; kind: string; limit: number; lookbackHours: number; rangeMode: "custom" | "preset"; startDate: string; ticker: string };
  ticker_news: { lookbackHours: number; showTeaser: boolean };
  news_detail: Record<string, never>;
  orders: { limit: number; showOrderIds: boolean };
  portfolio: { showExposure: boolean; showPnl: boolean };
  scanner: MarketScannerSettings;
  signal_stream: SignalStreamSettings;
  watchlist: WatchUniverseSettings;
  strategy_activity: StrategyActivitySettings;
  sec: { content: string; endDate: string; label: string; limit: number; lookbackHours: number; rangeMode: "custom" | "preset"; startDate: string; ticker: string };
  ticker_sec: { lookbackHours: number };
  sec_detail: Record<string, never>;
  strategy: { showSignals: boolean };
  xbrl: XbrlAnalysisSettings;
};

type CanvasPreviewContext = { previewTime: string; sessionDate: string };
type CanvasRuntimeMode = Extract<TradingWorkspaceMode, "backtest" | "backtest_debug" | "live" | "paper"> | "canvas" | "replay" | "research";
type LinkedContainerState = { status: WorkspaceWindowStatus; symbol: string; title: string };

const ALL_CONTAINER_IDS = TRADING_WORKSPACE_CONTAINERS.map((definition) => definition.id);
const MANAGER_DEFAULT_CONTAINER_IDS: WorkspaceContainerId[] = ["scanner", "chart", "portfolio", "positions", "orders"];
const DEFAULT_WATCHLIST_TAB_IDS = ["top-large-cap-gainers", "top-mid-cap-gainers", "top-small-cap-gainers", "top-penny-gainers"];
const DEFAULT_SETTINGS: ContainerSettings = {
  version: 27,
  chart: { showVolume: true, symbol: "AAPL", timeframe: "1m", visibleIndicators: ["indicator.vwap", "indicator.macd", "indicator.flow_structure_composite", "strategy.presentation"] },
  charts_quotes: {
    main: { showVolume: true, symbol: "AAPL", timeframe: "10s", visibleIndicators: ["indicator.macd", "strategy.presentation"] },
    month: { showVolume: true, symbol: "AAPL", timeframe: "1mo", visibleIndicators: [] },
    daily: { showVolume: true, symbol: "AAPL", timeframe: "1d", visibleIndicators: [] },
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
  signal_stream: { columns: [], customColumns: [], limit: 250, preset: "All" },
  watchlist: { columns: [], customColumns: [], limit: 50, watchlistId: DEFAULT_WATCHLIST_TAB_IDS[0], watchlistIds: DEFAULT_WATCHLIST_TAB_IDS },
  strategy_activity: { eventType: "", limit: 250, runId: "", strategyId: "", ticker: "" },
  sec: { content: "all", endDate: "", label: "", limit: 100, lookbackHours: 168, rangeMode: "preset", startDate: "", ticker: "" },
  ticker_sec: { lookbackHours: 720 },
  sec_detail: {},
  strategy: { showSignals: true },
  xbrl: { metricLimit: 8, showRawTags: true },
};

const HISTORICAL_TIMEFRAMES: CanvasChartTimeframe[] = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h", "1d", "1w", "1mo", "1y"];
const ENRICHED_QMD_TIMEFRAMES = new Set<CanvasChartTimeframe>(["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"]);
const MACRO_TIMEFRAMES = new Set<CanvasChartTimeframe>(["1d", "1w", "1mo", "1y"]);
const INDICATOR_GUIDES: Record<string, ChartCatalogKnowledge> = {
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
const CHART_INDICATORS: ChartDisplayItem[] = [
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

function useCanvasHistoricalChart(symbol: string, timeframe: CanvasChartTimeframe, cutoffMs: number, sessionDate: string, visibleIndicatorIds: string[], liveTail = false): CanvasLiveChartState {
  const pointInTime = !liveTail;
  const indicatorColumns = useMemo(() => requestedIndicatorColumns(visibleIndicatorIds), [visibleIndicatorIds]);
  const rowBudget = useMemo(() => chartRowBudget(indicatorColumns), [indicatorColumns]);
  const [state, setState] = useState<Omit<CanvasLiveChartState, "loadEarlier">>({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", historyNotice: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, marketSignalEvents: [], pointInTime, structureEvents: [], structureLevelHistory: [] });
  const historyCursorRef = useRef<ChartHistoryCursor | null>(null);
  const historyRequestRef = useRef(false);
  const historyAbortRef = useRef<AbortController | null>(null);
  const prefetchAbortRef = useRef<AbortController | null>(null);
  const prefetchedHistoryRef = useRef<{ key: string; payload: QmdBarHistory } | null>(null);
  const requestKeyRef = useRef("");
  const loadedCutoffRef = useRef(0);

  const prefetchEarlier = useCallback((requestKey: string, cursor: ChartHistoryCursor | null) => {
    const ticker = symbol.trim().toUpperCase();
    const request = earlierChartHistoryRequest(cursor, ticker, timeframe, indicatorColumns);
    if (!request || requestKeyRef.current !== requestKey) return;
    prefetchAbortRef.current?.abort();
    prefetchedHistoryRef.current = null;
    const controller = new AbortController();
    prefetchAbortRef.current = controller;
    api<QmdBarHistory>(`/api/trading/canvas-chart/history${query(request.params)}`, { signal: controller.signal, timeoutMs: 120000 })
      .then((payload) => {
        if (!controller.signal.aborted && requestKeyRef.current === requestKey) {
          prefetchedHistoryRef.current = { key: request.key, payload };
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (prefetchAbortRef.current === controller) prefetchAbortRef.current = null;
      });
  }, [indicatorColumns, symbol, timeframe]);

  const loadEarlier = useCallback(() => {
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    const cursor = historyCursorRef.current;
    if (!cursor || historyRequestRef.current || requestKeyRef.current !== requestKey) return;
    if (!cursor.nextBefore && !cursor.previousSessionBefore) return;
    const request = earlierChartHistoryRequest(cursor, ticker, timeframe, indicatorColumns);
    if (!request) return;
    const controller = new AbortController();
    historyAbortRef.current = controller;
    historyRequestRef.current = true;
    setState((current) => ({ ...current, historyError: "", loadingEarlier: true }));
    const prefetched = prefetchedHistoryRef.current?.key === request.key
      ? prefetchedHistoryRef.current.payload
      : null;
    prefetchAbortRef.current?.abort();
    if (prefetched) prefetchedHistoryRef.current = null;
    const page = prefetched
      ? Promise.resolve(prefetched)
      : api<QmdBarHistory>(`/api/trading/canvas-chart/history${query(request.params)}`, { signal: controller.signal, timeoutMs: 120000 });
    page
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
            marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
            historyError: "",
            historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : "",
            indicators: merged.indicators,
            indicatorsAvailable: payload.indicators_available,
            indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
            structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
            structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
          };
        });
        prefetchEarlier(requestKey, historyCursorRef.current);
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
  }, [cutoffMs, indicatorColumns, prefetchEarlier, rowBudget, symbol, timeframe]);

  useEffect(() => {
    let active = true;
    const historyController = new AbortController();
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    historyAbortRef.current?.abort();
    prefetchAbortRef.current?.abort();
    prefetchedHistoryRef.current = null;
    historyAbortRef.current = historyController;
    requestKeyRef.current = requestKey;
    historyCursorRef.current = null;
    historyRequestRef.current = false;
    loadedCutoffRef.current = cutoffMs;
    setState({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", historyNotice: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, marketSignalEvents: [], pointInTime, structureEvents: [], structureLevelHistory: [] });

    const fetchHistoricalPage = () => {
      historyRequestRef.current = true;
      const requestParams = { as_of: new Date(cutoffMs).toISOString(), row_limit: chartPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe };
      const progressive = ENRICHED_QMD_TIMEFRAMES.has(timeframe);
      const barsRequest = progressive
        ? api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, stage: "bars" })}`, { signal: historyController.signal, timeoutMs: 120000 })
        : api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, indicator_columns: indicatorColumns, stage: "full" })}`, { signal: historyController.signal, timeoutMs: 120000 });
      barsRequest
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
              marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
              historyError: "",
              historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : progressive ? "Loading requested indicators..." : liveTail ? "Historical base loaded; connecting the QMD live tail..." : "",
              indicators: merged.indicators,
              indicatorsAvailable: progressive ? current.indicatorsAvailable : payload.indicators_available,
              indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
              structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
              structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
              loading: false,
            };
          });
          if (!progressive) {
            prefetchEarlier(requestKey, historyCursorRef.current);
            return null;
          }
          return api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, indicator_columns: indicatorColumns, stage: "full" })}`, { signal: historyController.signal, timeoutMs: 120000 });
        })
        .then((payload) => {
          if (!payload || !active || requestKeyRef.current !== requestKey) return;
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
              marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
              historyError: "",
              historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : "",
              indicators: merged.indicators,
              indicatorsAvailable: payload.indicators_available,
              indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
              structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
              structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
            };
          });
          prefetchEarlier(requestKey, historyCursorRef.current);
        })
        .catch((reason) => {
          if (historyController.signal.aborted) return;
          if (!active || requestKeyRef.current !== requestKey) return;
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason), historyNotice: "", loading: false }));
        })
        .finally(() => {
          historyRequestRef.current = false;
          if (historyAbortRef.current === historyController) historyAbortRef.current = null;
        });
    };

    fetchHistoricalPage();

    return () => {
      active = false;
      if (requestKeyRef.current === requestKey) requestKeyRef.current = "";
      historyController.abort();
      prefetchAbortRef.current?.abort();
      prefetchedHistoryRef.current = null;
    };
  }, [indicatorColumns, pointInTime, prefetchEarlier, rowBudget, sessionDate, symbol, timeframe]);

  useEffect(() => {
    if (liveTail) return;
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    if (!ticker || cutoffMs <= loadedCutoffRef.current || requestKeyRef.current !== requestKey) return;
    loadedCutoffRef.current = cutoffMs;
    const controller = new AbortController();
    api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ as_of: new Date(cutoffMs).toISOString(), indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe })}`, {
      signal: controller.signal,
      timeoutMs: 120000,
    })
      .then((payload) => {
        if (controller.signal.aborted || requestKeyRef.current !== requestKey) return;
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
            indicators: merged.indicators,
            indicatorsAvailable: payload.indicators_available,
            indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
            lastUpdateAt: new Date().toISOString(),
            marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
            structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
            structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
          };
        });
      })
      .catch((reason) => {
        if (!controller.signal.aborted && requestKeyRef.current === requestKey) {
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
        }
      });
    return () => controller.abort();
  }, [cutoffMs, indicatorColumns, liveTail, rowBudget, sessionDate, symbol, timeframe]);

  useEffect(() => {
    if (!liveTail) return;
    const ticker = symbol.trim().toUpperCase();
    if (!ticker) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const refresh = async () => {
      if (cancelled || controller || document.visibilityState === "hidden") {
        if (!cancelled) timer = window.setTimeout(refresh, 1_000);
        return;
      }
      const request = new AbortController();
      controller = request;
      try {
        const payload = await api<QmdLiveChartPayload>(`/api/trading/canvas-live-chart${query({ row_limit: chartPageSize(timeframe), symbol: ticker, timeframe })}`, { signal: request.signal, timeoutMs: 30_000 });
        if (cancelled || request.signal.aborted) return;
        const bars = [...(payload.bars.history ?? []), ...(payload.bars.current ? [payload.bars.current] : [])];
        const indicators = [...(payload.indicators.history ?? []), ...(payload.indicators.current ? [payload.indicators.current] : [])];
        const liveError = Object.values(payload.errors ?? {}).filter(Boolean).join("; ");
        setState((current) => {
          const mergedBars = limitLiveRowsWithHysteresis(mergeRowsByTime(current.bars, bars), rowBudget);
          const admittedTimes = new Set(mergedBars.map(barStartTime));
          return {
            ...current,
            bars: mergedBars,
            connected: true,
            error: liveError,
            historyNotice: liveError ? `Live bars are current; one derived stream is partial: ${liveError}` : "QMD live tail connected; current-bar replacements are applied by bar timestamp.",
            indicators: limitLiveRowsWithHysteresis(mergeRowsByTime(current.indicators, indicators), rowBudget).filter((row) => admittedTimes.has(barStartTime(row))),
            lastUpdateAt: new Date().toISOString(),
            loading: false,
            pointInTime: false,
          };
        });
        const minimumRefreshMs = ["1d", "1w", "1mo", "1y"].includes(timeframe) ? 15_000 : timeframeDurationMs(timeframe) >= 300_000 ? 5_000 : 1_000;
        timer = window.setTimeout(refresh, Math.max(minimumRefreshMs, payload.stream_interval_ms ?? 1_000));
      } catch (reason) {
        if (!cancelled && !request.signal.aborted) {
          setState((current) => ({ ...current, connected: false, error: reason instanceof Error ? reason.message : String(reason), historyNotice: "Live tail is stale; the chart is retaining the last complete historical/live snapshot while reconnecting." }));
          timer = window.setTimeout(refresh, 5_000);
        }
      } finally {
        if (controller === request) controller = null;
      }
    };
    void refresh();
    const resume = () => {
      if (document.visibilityState !== "visible" || cancelled || controller) return;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      void refresh();
    };
    document.addEventListener("visibilitychange", resume);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [liveTail, rowBudget, symbol, timeframe]);

  return { ...state, loadEarlier };
}

function mergeStructureEvents(current: QmdStructureEvent[], incoming: QmdStructureEvent[] | undefined) {
  const byId = new Map<number, QmdStructureEvent>();
  [...current, ...(incoming ?? [])].forEach((event) => {
    if (
      Number.isFinite(event.event_id)
      && event.event_id > 0
      && ["level_promoted", "structure_crossed", "structure_break", "bos", "choch"].includes(event.event_kind)
    ) {
      byId.set(event.event_id, event);
    }
  });
  return retainStructureEventsPerTimeframe(
    [...byId.values()].sort((left, right) =>
      Date.parse(left.confirmed_at) - Date.parse(right.confirmed_at) || left.event_id - right.event_id),
    () => true,
  );
}

function mergeStructureLevelHistory(
  current: QmdStructureLevelCandidate[],
  incoming: QmdStructureLevelCandidate[] | undefined,
) {
  const byIdentity = new Map<string, QmdStructureLevelCandidate>();
  [...current, ...(incoming ?? [])].forEach((level) => {
    if (!isQmdStructureLevelCandidate(level)) return;
    const key = `${level.footprint_session_date}:${level.created_at_ms}:${level.side}:${Number(level.price).toFixed(8)}`;
    const existing = byIdentity.get(key);
    if (!existing || level.footprint_as_of_ms >= existing.footprint_as_of_ms) {
      byIdentity.set(key, level);
    }
  });
  return [...byIdentity.values()]
    .sort((left, right) =>
      left.footprint_as_of_ms - right.footprint_as_of_ms
      || left.created_at_ms - right.created_at_ms)
    .slice(-4_000);
}

function mergeMarketSignalEvents(current: QmdMarketSignalEvent[], incoming: QmdMarketSignalEvent[] | undefined) {
  const merged = new Map<string, QmdMarketSignalEvent>();
  [...current, ...(incoming ?? [])].forEach((event) => {
    if (event.event_id) merged.set(event.event_id, event);
  });
  return [...merged.values()]
    .sort((left, right) => Date.parse(left.effective_at) - Date.parse(right.effective_at)
      || left.event_id.localeCompare(right.event_id))
    .slice(-10_000);
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

function indicatorProvenanceNotice(provenance?: QmdIndicatorProvenance): string {
  if (!provenance?.engine_version) return "";
  const sourceTier = provenance.source?.tiers?.length ? provenance.source.tiers.join(" + ") : "QMD source plan";
  const source = `${sourceTier}; price basis ${provenance.corporate_action_revision ?? "unknown"}; calculation ${provenance.calculation_revision ?? provenance.engine_version}`;
  const warmUp = provenance.warm_up?.status === "partial_in_response"
    ? `warm-up ${provenance.warm_up.returned_bars ?? 0}/${provenance.warm_up.recommended_minimum_bars ?? 50} bars`
    : "warm-up satisfied";
  const coverage = provenance.complete === false ? "partial source coverage" : "source coverage complete";
  return `${provenance.engine_version} · indicators v${provenance.indicator_schema_version ?? "?"} · ${source} · ${warmUp} · ${coverage}`;
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

function earlierChartHistoryRequest(
  cursor: ChartHistoryCursor | null,
  ticker: string,
  timeframe: CanvasChartTimeframe,
  indicatorColumns: string,
): { key: string; params: Record<string, string | number | undefined> } | null {
  if (!cursor || (!cursor.nextBefore && !cursor.previousSessionBefore)) return null;
  const params = cursor.nextBefore
    ? { as_of: cursor.asOf, before_bar: cursor.nextBefore, indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), session_date: cursor.sessionDate, symbol: ticker, timeframe }
    : { before: cursor.previousSessionBefore, indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), symbol: ticker, timeframe };
  return {
    key: [ticker, timeframe, indicatorColumns, cursor.asOf, cursor.sessionDate, cursor.nextBefore, cursor.previousSessionBefore].join("|"),
    params,
  };
}

function closedRowsAtCutoff<T extends { bar_start: string }>(rows: T[], timeframe: string, cutoffMs = Date.now()): T[] {
  const durationMs = timeframeDurationMs(timeframe);
  return rows.filter((row) => {
    const closeMetadata = row as T & { bar_end?: string; is_closed?: boolean };
    // QMD History builds the current macro period causally from completed
    // daily bars through the requested as-of clock. It remains an explicitly
    // partial macro row, but it is still the correct last candle to present.
    // Other open bars stay excluded from historical Canvas charts.
    if (closeMetadata.is_closed === false && !["1w", "1mo", "1y"].includes(timeframe)) return false;
    const startMs = Date.parse(row.bar_start);
    const endMs = closeMetadata.bar_end ? Date.parse(closeMetadata.bar_end) : startMs + durationMs;
    return Number.isFinite(startMs) && Number.isFinite(endMs) && endMs <= cutoffMs;
  });
}

function timeframeDurationMs(timeframe: string): number {
  if (timeframe === "1d") return 24 * 60 * 60 * 1_000;
  if (timeframe === "1w") return 7 * 24 * 60 * 60 * 1_000;
  if (timeframe === "1mo") return 30 * 24 * 60 * 60 * 1_000;
  if (timeframe === "1y") return 365 * 24 * 60 * 60 * 1_000;
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
function qmdMarketSignalChartMarkers(
  events: QmdMarketSignalEvent[],
  bars: HistoricalBar[],
  visibleIndicators: string[],
): ChartPayload["markers"] {
  if (!visibleIndicators.includes("indicator.qmd_market_signals") || !events.length || !bars.length) {
    return [];
  }
  const intervals = bars.map((bar) => ({
    end: Date.parse(bar.bar_end || "") || Date.parse(bar.bar_start) + 1,
    start: Date.parse(bar.bar_start),
    time: Date.parse(bar.bar_start) / 1000,
  }));
  return events
    .filter((event) => event.state === "triggered" && ["bullish", "bearish"].includes(event.direction))
    .map((event) => {
      const effectiveAt = Date.parse(event.effective_at);
      if (!Number.isFinite(effectiveAt)) return null;
      const interval = intervals.find((candidate) => effectiveAt >= candidate.start && effectiveAt < candidate.end)
        ?? intervals.find((candidate) => candidate.start >= effectiveAt);
      if (!interval) return null;
      const bullish = event.direction === "bullish";
      return {
        color: bullish ? "var(--success)" : "var(--danger)",
        displayItemId: "indicator.qmd_market_signals",
        position: bullish ? "belowBar" : "aboveBar",
        shape: bullish ? "arrowUp" : "arrowDown",
        size: 1,
        text: `${Math.round(boundedUnit(event.confidence) * 100)}% · ${event.working_timeframe}`,
        time: interval.time as UTCTimestamp,
      } satisfies NonNullable<ChartPayload["markers"]>[number];
    })
    .filter((marker): marker is NonNullable<typeof marker> => marker !== null);
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

const LIVE_ACCOUNT_KEYS_STORAGE_KEY = "quant-research-workbench.real-live-trading.account-keys";
const LIVE_PERFORMANCE_STORAGE_KEY = "quant-research-workbench.canvas.live-performance-v1";

function readLiveAccountKeys(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_ACCOUNT_KEYS_STORAGE_KEY) || "null");
    if (Array.isArray(parsed)) return parsed.map((item) => String(item)).filter(Boolean);
  } catch {
    // A malformed preference must not prevent the Canvas from loading.
  }
  return ["paper"];
}

function currentLivePreviewContext(now = new Date()): CanvasPreviewContext {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    timeZone: "America/New_York",
  }).formatToParts(now).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return { previewTime: `${parts.hour === "24" ? "00" : parts.hour}:${parts.minute}`, sessionDate: marketSessionDate(now.toISOString()) };
}

function liveAccountSignature(accountKeys: string[]) {
  return [...accountKeys].map((item) => String(item)).filter(Boolean).sort().join(",");
}

function readCachedLivePerformance(accountKeys: string[]): PerformanceSnapshot | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_PERFORMANCE_STORAGE_KEY) || "null") as { account_signature?: string; data?: PerformanceSnapshot } | null;
    if (parsed?.account_signature === liveAccountSignature(accountKeys) && parsed.data?.as_of) return parsed.data;
  } catch {
    // Cached presentation state is optional; canonical broker state remains authoritative.
  }
  return null;
}

function writeCachedLivePerformance(accountKeys: string[], data: PerformanceSnapshot) {
  try {
    window.localStorage.setItem(LIVE_PERFORMANCE_STORAGE_KEY, JSON.stringify({ account_signature: liveAccountSignature(accountKeys), data }));
  } catch {
    // Storage restrictions must not interrupt live refreshes.
  }
}

function normalizePerformanceSnapshot(payload: CanonicalTradingPreview): PerformanceSnapshot | null {
  if (payload.performance_snapshot) return { ...payload.performance_snapshot, source: "performance_snapshot" };
  const metrics = payload.portfolio?.metrics;
  if (!metrics || !payload.as_of) return null;
  const sessionDate = marketSessionDate(payload.as_of);
  const realizedToday = (payload.performance_journal?.episodes || []).reduce((total, row) => {
    const closedAt = String(row.closed_at || "");
    return marketSessionDate(closedAt) === sessionDate ? total + finiteNumber(row.net_pnl) : total;
  }, 0);
  const unrealized = finiteNumber(metrics.unrealized_pnl);
  const hasAvailableFunds = payload.account_values.some((row) => String(row.key || "").toLowerCase() === "availablefunds" && String(row.segment || "base").toLowerCase() === "base")
    || payload.ledger.some((row) => {
      if (!row.is_base || !row.values || typeof row.values !== "object") return false;
      return Object.keys(row.values as Record<string, unknown>).some((key) => key.toLowerCase() === "availablefunds");
    });
  return {
    as_of: payload.as_of,
    session_date: sessionDate,
    net_pnl_today: realizedToday + unrealized,
    open_position_count: payload.positions.filter((row) => finiteNumber(row.quantity) !== 0).length,
    unrealized_pnl: unrealized,
    realized_pnl_today: realizedToday,
    available_cash: hasAvailableFunds ? finiteNumber(metrics.available_funds) : finiteNumber(metrics.total_cash),
    available_cash_basis: hasAvailableFunds ? "available_funds" : "total_cash",
    source: "canonical_state_v2",
  };
}

function useLivePerformanceState(enabled = true, requestedAccountKeys?: string[]): LivePerformanceState {
  const requestedSignature = requestedAccountKeys?.join(",") ?? "";
  const [accountKeys, setAccountKeys] = useState(() => requestedAccountKeys?.length ? requestedAccountKeys : readLiveAccountKeys());
  const [state, setState] = useState<LivePerformanceState>(() => {
    const cached = readCachedLivePerformance(accountKeys);
    return { data: cached, status: cached ? "stale" : "loading" };
  });

  useEffect(() => {
    if (!enabled) return;
    if (requestedAccountKeys?.length) {
      setAccountKeys(requestedAccountKeys);
      return;
    }
    const syncAccounts = (event: StorageEvent) => {
      if (event.key === LIVE_ACCOUNT_KEYS_STORAGE_KEY) setAccountKeys(readLiveAccountKeys());
    };
    window.addEventListener("storage", syncAccounts);
    return () => window.removeEventListener("storage", syncAccounts);
  }, [enabled, requestedSignature]);

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, status: "loading" });
      return;
    }
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const cached = readCachedLivePerformance(accountKeys);
    setState({ data: cached, status: cached ? "stale" : "loading" });
    const schedule = () => {
      if (!cancelled) timer = window.setTimeout(load, 15_000);
    };
    const load = async () => {
      if (cancelled || controller) return;
      if (document.visibilityState === "hidden") {
        schedule();
        return;
      }
      const request = new AbortController();
      controller = request;
      const parameters = { account_keys: accountKeys.join(","), account_type: accountKeys[0] || "paper", mode: "paper" };
      try {
        let performance: PerformanceSnapshot;
        let stale = false;
        try {
          const compact = await api<PerformanceSnapshotResponse>(`/api/trading/performance-snapshot${query(parameters)}`, { signal: request.signal, timeoutMs: 45_000 });
          performance = { ...compact.performance_snapshot, source: "performance_snapshot" };
          stale = compact.stale;
        } catch (reason) {
          if ((reason as { status?: number })?.status !== 404) throw reason;
          const payload = await api<CanonicalTradingPreview>(`/api/trading/state${query(parameters)}`, { signal: request.signal, timeoutMs: 45_000 });
          const normalized = normalizePerformanceSnapshot(payload);
          if (!normalized) throw new Error("Canonical performance evidence is unavailable");
          performance = normalized;
          stale = payload.stale;
        }
        if (!cancelled) {
          writeCachedLivePerformance(accountKeys, performance);
          setState({ data: performance, status: stale ? "stale" : "ready" });
        }
      } catch {
        if (!cancelled && !request.signal.aborted) setState((current) => ({ data: current.data, status: "error" }));
      } finally {
        if (controller === request) controller = null;
        schedule();
      }
    };
    load();
    const refreshVisible = () => {
      if (document.visibilityState !== "visible" || controller) return;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      void load();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [accountKeys.join(","), enabled]);

  return state;
}

function CanvasPerformanceStrip({ state }: { state: LivePerformanceState }) {
  const snapshot = state.data;
  const rows = [
    { icon: BadgeDollarSign, label: "Net P&L", tone: performanceTone(snapshot?.net_pnl_today), value: performanceMoney(snapshot?.net_pnl_today, true), detail: "Today's realized net P&L plus current unrealized P&L." },
    { icon: BriefcaseBusiness, label: "Open", tone: Number(snapshot?.open_position_count || 0) > 0 ? "info" : "neutral", value: snapshot ? String(snapshot.open_position_count) : "—", detail: "Current non-zero positions across the selected broker accounts." },
    { icon: CircleDollarSign, label: "Unrealized", tone: performanceTone(snapshot?.unrealized_pnl), value: performanceMoney(snapshot?.unrealized_pnl, true), detail: "Mark-to-market P&L on currently open positions." },
    { icon: WalletCards, label: "Realized today", tone: performanceTone(snapshot?.realized_pnl_today), value: performanceMoney(snapshot?.realized_pnl_today, true), detail: "Net P&L from flat-to-flat trade episodes closed today in New York market time." },
    { icon: Landmark, label: "Available cash", tone: "neutral", value: performanceMoney(snapshot?.available_cash, false), detail: !snapshot ? "Waiting for the canonical trading snapshot." : snapshot.available_cash_basis === "available_funds" ? "Broker available funds across the selected accounts." : "Total cash fallback; broker available funds were not published." },
  ];
  const freshness = snapshot?.as_of ? new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(new Date(snapshot.as_of)) : "";
  const sourceDetail = snapshot?.source === "canonical_state_v2" ? " · normalized from canonical state v2" : "";
  return <section aria-label="Live trading performance" className="canvas-performance-strip" data-status={state.status} title={freshness ? `Canonical trading snapshot as of ${freshness} ET${sourceDetail}` : "Canonical trading snapshot is loading"}>
    <div className="canvas-performance-title"><Activity aria-hidden="true" size={13} /><span>Performance</span><i aria-hidden="true" /></div>
    {rows.map(({ detail, icon: Icon, label, tone, value }) => <div className="canvas-performance-metric" data-tone={tone} key={label} title={detail}>
      <span><Icon aria-hidden="true" size={11} />{label}</span>
      <strong>{value}</strong>
    </div>)}
  </section>;
}

function performanceTone(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric === 0) return "neutral";
  return numeric > 0 ? "positive" : "negative";
}

function performanceMoney(value: unknown, signed: boolean) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const compact = Math.abs(numeric) >= 100_000;
  const formatted = new Intl.NumberFormat("en-US", {
    currency: "USD",
    maximumFractionDigits: compact ? 1 : 0,
    notation: compact ? "compact" : "standard",
    signDisplay: signed ? "exceptZero" : "auto",
    style: "currency",
  }).format(numeric);
  return formatted.replace("-$", "−$");
}

export function CanvasConfigurationPage() {
  return <CanvasWorkspaceSurface canvasId={MAIN_CANVAS_ID} manager />;
}

export type ApprovedCanvasProfile = {
  available: boolean;
  canvas_revision: string;
  configuration_revision: number;
  content_hash: string;
  profile: CanvasRegistry;
  revision_id: string;
  schema_version: number;
};

export function ApprovedCanvasRuntimePage({ accountKeys, mode, modeControls }: { accountKeys: string[]; mode: "live" | "paper"; modeControls?: ReactNode }) {
  const [approved, setApproved] = useState<ApprovedCanvasProfile | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api<ApprovedCanvasProfile>("/api/trading/configuration/canvas-profile", { timeoutMs: 20_000 })
      .then((payload) => {
        if (cancelled) return;
        if (!payload.available || !payload.profile) throw new Error("Publish an approved configuration with a Canvas profile before opening a trading workspace.");
        setApproved(payload);
      })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, []);
  if (error) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-inline-error">{error}</div></div>;
  if (!approved) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-empty-state"><strong>Loading approved Canvas</strong><span>Resolving the published default for this {mode} workspace.</span></div></div>;
  return <CanvasWorkspaceSurface accountKeys={accountKeys} approvedCanvas={approved} canvasId={MAIN_CANVAS_ID} manager={false} modeControls={modeControls} runtimeMode={mode} />;
}

export function CanvasFocusPage() {
  const params = new URLSearchParams(window.location.search);
  const replayRunId = params.get("replay_run") || undefined;
  const replayFocusToken = params.get("replay_focus") || undefined;
  if (replayRunId && replayFocusToken) return <ReplayCanvasFocusPage focusToken={replayFocusToken} runId={replayRunId} />;
  const canvasId = params.get("canvas") || MAIN_CANVAS_ID;
  const requestedInstanceId = params.get("container") || undefined;
  const requestedNewsId = params.get("news") || undefined;
  const requestedSecCik = params.get("sec_cik") || undefined;
  const requestedSecAccession = params.get("sec_accession") || undefined;
  return <ApprovedCanvasFocusPage canvasId={canvasId} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} />;
}

function ApprovedCanvasFocusPage({ canvasId, requestedInstanceId, requestedNewsId, requestedSecAccession, requestedSecCik }: { canvasId: string; requestedInstanceId?: string; requestedNewsId?: string; requestedSecAccession?: string; requestedSecCik?: string }) {
  const [approved, setApproved] = useState<ApprovedCanvasProfile | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api<ApprovedCanvasProfile>("/api/trading/configuration/canvas-profile", { timeoutMs: 20_000 })
      .then((payload) => {
        if (cancelled) return;
        if (!payload.available || !payload.profile) throw new Error("Publish an approved configuration with a Canvas profile before opening a trading workspace.");
        setApproved(payload);
      })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, []);
  if (error) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-inline-error">{error}</div></div>;
  if (!approved) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-empty-state"><strong>Loading approved Canvas</strong><span>Resolving the published default and this workspace's saved overlay.</span></div></div>;
  return <CanvasWorkspaceSurface approvedCanvas={approved} canvasId={canvasId} manager={false} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} />;
}

function ReplayCanvasFocusPage({ focusToken, runId }: { focusToken: string; runId: string }) {
  const [handoff] = useState(() => readReplayCanvasFocusHandoff(focusToken));
  const [run, setRun] = useState<CanvasReplayRun | null>(null);
  const [error, setError] = useState(handoff ? "" : "This Replay focus link is missing or expired.");
  const mergeFocusRun = useCallback((update: CanvasReplayRun) => {
    setRun((current) => {
      const latest = latestReplayRun(current, update);
      return handoff ? { ...latest, canvas_profile: { ...handoff.profile, defaultState: handoff.state } } : latest;
    });
  }, [handoff]);

  useEffect(() => {
    if (!handoff) return;
    let cancelled = false;
    api<CanvasReplayRun>(`/api/trading/replay/runs/${encodeURIComponent(runId)}`, { timeoutMs: 20_000 })
      .then((payload) => { if (!cancelled) mergeFocusRun(payload); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, [handoff, mergeFocusRun, runId]);

  useReplayRunEvents(handoff ? runId : undefined, mergeFocusRun, setError);

  if (error && !run) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-inline-error">{error}</div></div>;
  if (!run) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-empty-state"><strong>Opening Replay focus canvas</strong><span>Restoring the selected container against the active run clock.</span></div></div>;
  return <CanvasWorkspaceSurface canvasId={MAIN_CANVAS_ID} manager={false} replayRun={run} runtimeWorkspaceId={focusToken} />;
}

export function CanvasWorkspaceSurface({ accountKeys, approvedCanvas, canvasId, manager, modeControls, replayRun, requestedInstanceId, requestedNewsId, requestedSecAccession, requestedSecCik, runtimeMode: requestedRuntimeMode, runtimeWorkspaceId }: { accountKeys?: string[]; approvedCanvas?: ApprovedCanvasProfile; canvasId: string; manager: boolean; modeControls?: ReactNode; replayRun?: CanvasReplayRun; requestedInstanceId?: string; requestedNewsId?: string; requestedSecAccession?: string; requestedSecCik?: string; runtimeMode?: CanvasRuntimeMode; runtimeWorkspaceId?: string }) {
  const runtimeMode: CanvasRuntimeMode = replayRun?.mode === "backtest" || replayRun?.mode === "backtest_debug" ? replayRun.mode : replayRun ? "replay" : requestedRuntimeMode ?? "canvas";
  const liveMode = runtimeMode === "live" || runtimeMode === "paper";
  const resolvedAccountKeys = accountKeys?.length ? accountKeys : readLiveAccountKeys();
  const accountSignature = [...resolvedAccountKeys].sort().join(".") || runtimeMode;
  const runtimeBase = replayRun?.canvas_profile ?? approvedCanvas?.profile;
  const runtimeRevision = replayRun?.configuration_content_hash || replayRun?.canvas_revision || approvedCanvas?.content_hash || approvedCanvas?.canvas_revision || "draft";
  const runtimeScope = replayRun ? `${runtimeMode}.${replayRun.run_id}.${runtimeWorkspaceId || "main"}` : liveMode ? `${runtimeMode}.${accountSignature}` : runtimeMode === "research" ? `research.${runtimeWorkspaceId || canvasId}` : approvedCanvas ? "canvas" : "configuration";
  const runtimeRegistryStorageKey = runtimeBase ? canvasRuntimeRegistryStorageKey(runtimeScope, runtimeRevision) : "";
  const workspaceStorageKey = runtimeBase
    ? canvasRuntimeWorkspaceStorageKey(runtimeScope, runtimeRevision, canvasId)
    : canvasWorkspaceStorageKey(canvasId);
  const [overlayEpoch, setOverlayEpoch] = useState(0);
  const [runtimeRebase, setRuntimeRebase] = useState<CanvasRuntimeRebase | null>(() => {
    if (!runtimeBase) return null;
    const previous = readCanvasRuntimeOverlayRecord(runtimeScope, canvasId);
    return previous && previous.revision !== runtimeRevision
      ? rebaseCanvasRuntimeOverlay(previous, runtimeBase, canvasId)
      : null;
  });
  const initialCanvasState = useMemo<CanvasWorkspaceState | null>(() => runtimeBase
    ? runtimeCanvasState(runtimeBase, workspaceStorageKey, canvasId, requestedInstanceId)
    : focusCanvasState(canvasId, requestedInstanceId), [canvasId, overlayEpoch, requestedInstanceId, runtimeBase, workspaceStorageKey]);
  const [registry, setRegistry] = useState<CanvasRegistry>(() => runtimeBase
    ? readCanvasRuntimeRegistry(runtimeBase, runtimeRegistryStorageKey)
    : readCanvasRegistry());
  const [previewContext, setPreviewContext] = useState<CanvasPreviewContext>(() => replayRun ? replayPreviewContext(replayRun) : liveMode ? currentLivePreviewContext() : readPreviewContext());
  const [preview, setPreview] = useState<CanvasPreview | null>(null);
  const [contextReady, setContextReady] = useState(Boolean(replayRun || liveMode));
  const [contextError, setContextError] = useState("");
  const [workspaceState, setWorkspaceState] = useState<CanvasWorkspaceState | null>(initialCanvasState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [defaultSaved, setDefaultSaved] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);
  const [linkPopoverContainerId, setLinkPopoverContainerId] = useState<string | null>(null);
  const [settingsContainerId, setSettingsContainerId] = useState<string | null>(null);
  const managementEnabled = manager || Boolean(runtimeBase);

  const currentCanvas = registry.canvases.find((canvas) => canvas.id === canvasId) ?? { id: canvasId, label: canvasId === MAIN_CANVAS_ID ? "Main" : "Focus canvas" };
  const primaryChartId = (workspaceState?.openIds ?? []).find((id) => workspaceContainerKind(id, workspaceState) === "chart") ?? "chart";
  const primarySettings = instanceSettings(registry, primaryChartId);
  const dedicatedContainers = new Set<WorkspaceContainerId>(["chart", "charts_quotes", "facts", "microstructure", "news", "ticker_news", "news_detail", "sec", "ticker_sec", "sec_detail", "xbrl", "scanner", "watchlist", "strategy_activity"]);
  const previewContainerKey = (workspaceState?.openIds ?? []).filter((id) => !dedicatedContainers.has(workspaceContainerKind(id, workspaceState))).sort().join(",");
  const scannerContainerKey = (workspaceState?.openIds ?? []).filter((id) => ["scanner", "signal_stream", "watchlist"].includes(workspaceContainerKind(id, workspaceState))).sort().join(",");
  const scannerTechnicalWindows = useMemo(() => {
    const values = new Set<string>();
    for (const instanceId of (workspaceState?.openIds ?? [])) {
      const kind = workspaceContainerKind(instanceId, workspaceState);
      if (!["scanner", "signal_stream", "watchlist"].includes(kind)) continue;
      const settings = instanceSettings(registry, instanceId);
      const list = kind === "scanner" ? settings.scanner : kind === "signal_stream" ? settings.signal_stream : settings.watchlist;
      for (const column of list.customColumns) {
        if (!list.columns.includes(column.key)) continue;
        if (column.timeframe) values.add(column.timeframe);
        else if (column.anchor) values.add(column.anchor);
      }
    }
    return [...SCANNER_TIMEFRAMES.filter((value) => values.has(value)), ...["extended_session", "regular_session"].filter((value) => values.has(value))].join(",");
  }, [registry, scannerContainerKey, workspaceState]);
  const activeLinkGroup = registry.linkAssignments[primaryChartId] ?? "none";
  const activeSymbol = activeLinkGroup === "none" ? primarySettings.chart.symbol : registry.linkContexts[activeLinkGroup].symbol;
  const chartCutoffMs = useMemo(() => dateInTimeZone(previewContext.sessionDate, previewContext.previewTime, "America/New_York").getTime(), [previewContext]);
  const scannerCutoffMs = replayRun ? Math.floor(chartCutoffMs / 15_000) * 15_000 : chartCutoffMs;
  const historicalScanner = useCanvasScannerSnapshot({
    cutoffMs: scannerCutoffMs,
    enabled: Boolean(scannerContainerKey) && contextReady && !liveMode,
    technicalWindows: scannerTechnicalWindows,
  });
  const liveScanner = useCanvasLiveScannerSnapshot(Boolean(scannerContainerKey) && contextReady && liveMode);
  const { error: scannerError, loading: scannerLoading, snapshot: scannerSnapshot } = liveMode ? liveScanner : historicalScanner;
  const previewClocks = useMemo(() => previewClockReadings(previewContext), [previewContext]);
  const clockIcons = [Clock3, MapPin];
  const marketStatus = useMemo(() => historicalMarketStatus(previewContext.sessionDate, previewContext.previewTime), [previewContext]);
  const livePerformance = useLivePerformanceState(!replayRun, liveMode ? resolvedAccountKeys : undefined);
  const performanceState: LivePerformanceState = replayRun
    ? {
        data: preview?.trading.performance_snapshot ?? null,
        status: preview?.trading.stale ? "stale" : preview ? "ready" : "loading",
      }
    : livePerformance;

  useEffect(() => {
    if (runtimeBase) return;
    if (canvasId !== NEWS_READER_CANVAS_ID && canvasId !== SEC_READER_CANVAS_ID) return;
    if (canvasId === NEWS_READER_CANVAS_ID) ensureNewsReaderCanvas();
    setRegistry(readCanvasRegistry());
  }, [canvasId, runtimeBase]);

  useEffect(() => {
    if (runtimeBase && runtimeRegistryStorageKey) {
      window.localStorage.setItem(runtimeRegistryStorageKey, JSON.stringify(registry));
      return;
    }
    writeCanvasRegistry(registry);
  }, [registry, runtimeBase, runtimeRegistryStorageKey]);

  useEffect(() => {
    if (!runtimeBase || runtimeRebase || !workspaceState) return;
    const baseWorkspace = runtimeBase.workspaceStates?.[canvasId]
      ?? (canvasId === MAIN_CANVAS_ID ? runtimeBase.defaultState : undefined)
      ?? null;
    writeCanvasRuntimeOverlayRecord(runtimeScope, canvasId, {
      baseRegistry: runtimeBase,
      baseWorkspace,
      overlayRegistry: registry,
      overlayWorkspace: snapshotCanvasWorkspaceState(workspaceState),
      revision: runtimeRevision,
      schemaVersion: 1,
      updatedAt: new Date().toISOString(),
    });
  }, [canvasId, registry, runtimeBase, runtimeRebase, runtimeRevision, runtimeScope, workspaceState]);

  useEffect(() => {
    if (replayRun || liveMode) return;
    window.localStorage.setItem(CANVAS_PREVIEW_CONTEXT_STORAGE_KEY, JSON.stringify(previewContext));
  }, [liveMode, previewContext, replayRun]);

  useEffect(() => {
    if (!liveMode) return;
    const update = () => setPreviewContext(currentLivePreviewContext());
    update();
    const timer = window.setInterval(update, 15_000);
    return () => window.clearInterval(timer);
  }, [liveMode]);

  useEffect(() => {
    if (!replayRun) return;
    const next = replayPreviewContext(replayRun);
    setPreviewContext((current) => current.previewTime === next.previewTime && current.sessionDate === next.sessionDate ? current : next);
    setContextReady(true);
    setContextError(replayRun.error || "");
  }, [replayRun?.current_time, replayRun?.error, replayRun?.session_date]);

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
    if (runtimeBase) {
      const syncRuntimeCanvasRegistry = (event: StorageEvent) => {
        if (event.key === runtimeRegistryStorageKey) setRegistry(readCanvasRuntimeRegistry(runtimeBase, runtimeRegistryStorageKey));
      };
      window.addEventListener("storage", syncRuntimeCanvasRegistry);
      return () => window.removeEventListener("storage", syncRuntimeCanvasRegistry);
    }
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
  }, [runtimeBase, runtimeRegistryStorageKey]);

  useEffect(() => {
    if (replayRun || liveMode) return;
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
  }, [liveMode, replayRun]);

  useEffect(() => {
    if (!contextReady) return;
    if (!previewContainerKey) {
      setPreview(null);
      setLoading(false);
      setError("");
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const request = replayRun
      ? api<CanvasPreview>(`/api/trading/${runtimeMode}/runs/${encodeURIComponent(replayRun.run_id)}/canvas${query({ symbol: activeSymbol })}`, {
          signal: controller.signal,
          timeoutMs: 60000,
        })
      : api<CanvasPreview>("/api/trading/canvas-preview", {
          body: JSON.stringify({
            chart_symbol: activeSymbol,
            chart_timeframe: "1m",
            preview_time: previewContext.previewTime,
            session_date: previewContext.sessionDate,
          }),
          method: "POST",
          signal: controller.signal,
          timeoutMs: 60000,
        }).then(async (payload) => {
          if (!liveMode) return payload;
          const trading = await api<CanonicalTradingPreview>(`/api/trading/state${query({ account_keys: resolvedAccountKeys.join(","), account_type: resolvedAccountKeys[0] || "paper", mode: runtimeMode })}`, { signal: controller.signal, timeoutMs: 45_000 });
          return { ...payload, preview_kind: `${runtimeMode}_runtime`, trading };
        });
    request.then((payload) => { if (!controller.signal.aborted) setPreview(payload); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [accountSignature, activeSymbol, contextError, contextReady, liveMode, previewContainerKey, previewContext.previewTime, previewContext.sessionDate, replayRun?.run_id, runtimeMode]);

  const metaForContainer = useMemo(() => (definition: WorkspaceContainerDefinition): WorkspaceWindowMeta => {
    if (definition.id === "chart") {
      return {
        detail: "Canonical QMD bars using the container's own timeframe and indicator configuration.",
        freshness: previewContext.previewTime,
        sourceLabel: "QMD History + Live",
        status: contextError ? "error" : "ready",
      };
    }
    if (definition.id === "charts_quotes") {
      return {
        detail: liveMode ? "Canonical history merged with the current QMD bar tail, live NBBO updates, and trade prints." : "Canonical historical charts, NBBO updates, and trade prints for one symbol at the active Replay or Canvas clock.",
        freshness: previewContext.previewTime,
        sourceLabel: liveMode ? "QMD History + Live" : "QMD History",
        status: contextError ? "error" : "ready",
      };
    }
    if (definition.id === "microstructure") {
      return {
        detail: liveMode ? "Canonical live NBBO updates and trade prints decoded once from the active QMD event sequence." : "Canonical historical NBBO updates and trade prints decoded once against the same event sequence and active clock.",
        freshness: previewContext.previewTime,
        sourceLabel: liveMode ? "QMD Live" : "QMD History",
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
    if (["scanner", "signal_stream", "watchlist"].includes(definition.id)) {
      return {
        detail: scannerError
          ? `The point-in-time scanner request failed: ${scannerError}`
          : scannerLoading
            ? "Building and enriching the complete point-in-time market cross-section."
            : scannerSnapshot
              ? `${definition.title} rendered from the complete ${liveMode ? "live" : "point-in-time"} market cross-section.`
              : "Waiting for the point-in-time market cross-section.",
        freshness: previewContext.previewTime,
        sourceLabel: scannerError ? "Unavailable" : liveMode ? "QMD Live" : "QMD History",
        status: scannerError ? "error" : scannerLoading ? "connecting" : scannerSnapshot ? "ready" : "idle",
      };
    }
    if (
      replayRun
      && ["activity", "closed_trades", "fills", "journal", "orders", "performance_journal", "portfolio", "positions", "strategy"].includes(definition.id)
    ) {
      return {
        detail: `${definition.title} projected from this ${runtimeMode === "backtest_debug" ? "Backtest Debug" : runtimeMode === "backtest" ? "Backtest" : "Replay"} run's canonical simulated broker state and durable journal.`,
        freshness: previewContext.previewTime,
        sourceLabel: "Replay run",
        status: replayRun.error ? "error" : preview ? "ready" : "connecting",
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
  }, [contextError, liveMode, preview, previewContext.previewTime, replayRun, runtimeMode, scannerError, scannerLoading, scannerSnapshot]);

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
    if (!containerSupportsCanvasLink(containerId)) return;
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

  function openChartsQuotesForTicker(tickerValue: string) {
    const symbol = tickerValue.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.\-]{0,15}$/.test(symbol)) return;
    const instanceId = nextAvailableContainerInstanceId("charts_quotes", [
      ...Object.keys(registry.instanceSettings),
      ...Object.keys(registry.linkAssignments),
      ...(workspaceState?.openIds ?? []),
    ]);
    const settings = instanceSettings(registry, instanceId);
    const focusedSettings = normalizeSettings({
      ...settings,
      chart: { ...settings.chart, symbol },
      charts_quotes: {
        daily: { ...settings.charts_quotes.daily, symbol },
        layout: settings.charts_quotes.layout,
        main: { ...settings.charts_quotes.main, symbol },
        month: { ...settings.charts_quotes.month, symbol },
      },
    });
    const state: CanvasWorkspaceState = {
      groups: {},
      instances: { [instanceId]: "charts_quotes" },
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts: { [instanceId]: focusLayout() },
      openIds: [instanceId],
    };
    const profile: CanvasRegistry = {
      ...registry,
      instanceSettings: {
        ...registry.instanceSettings,
        [instanceId]: focusedSettings,
      },
    };
    if (replayRun) {
      openReplayFocus(profile, state);
      return;
    }
    const created = createCanvasRecord(profile, `${symbol} Charts & Quotes`);
    writeCanvasWorkspaceState(created.canvas.id, state);
    writeCanvasRegistry(created.registry);
    setRegistry(created.registry);
    window.open(focusCanvasUrl(created.canvas.id, instanceId), "_blank", "noopener,noreferrer");
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

  function openReplayFocus(profile: CanvasRegistry, state: CanvasWorkspaceState) {
    if (!replayRun) return false;
    const token = writeReplayCanvasFocusHandoff(profile, state);
    const focusedWindow = window.open(replayFocusCanvasUrl(replayRun.run_id, token), "_blank");
    if (focusedWindow) focusedWindow.opener = null;
    return Boolean(focusedWindow);
  }

  function openReplayContainerCanvas(instanceId: string, sourceLayout: WorkspaceWindowLayout) {
    const containerId = workspaceContainerKind(instanceId, workspaceState);
    return openReplayFocus(registry, {
      groups: {},
      instances: { [instanceId]: containerId },
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts: { [instanceId]: focusLayout(sourceLayout) },
      openIds: [instanceId],
    });
  }

  function openReplayGroupCanvas(groupId: string, sourceState: CanvasWorkspaceState) {
    const groups = Object.fromEntries(Object.entries(sourceState.groups).map(([id, group]) => [id, {
      ...group,
      fullscreen: id === groupId,
      minimized: false,
    }]));
    return openReplayFocus(registry, { ...sourceState, groups, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION });
  }

  function openReplayConfiguredCanvas(targetCanvasId: string) {
    const state = registry.workspaceStates?.[targetCanvasId]
      ?? (targetCanvasId === MAIN_CANVAS_ID ? registry.defaultState : undefined);
    if (state) openReplayFocus(registry, snapshotCanvasWorkspaceState(state));
  }

  function openRuntimeConfiguredCanvas(targetCanvasId: string) {
    if (replayRun) {
      openReplayConfiguredCanvas(targetCanvasId);
      return;
    }
    window.open(focusCanvasUrl(targetCanvasId), "_blank", "noopener,noreferrer");
  }

  function resetRuntimeOverlay() {
    if (!runtimeBase) return;
    window.localStorage.removeItem(runtimeRegistryStorageKey);
    window.localStorage.removeItem(workspaceStorageKey);
    setRegistry(runtimeBase);
    setWorkspaceState(runtimeCanvasState(runtimeBase, workspaceStorageKey, canvasId, requestedInstanceId, false));
    setRuntimeRebase(null);
    setOverlayEpoch((value) => value + 1);
  }

  function saveRuntimeWorkspace() {
    if (!runtimeBase || !workspaceState) return;
    const created = createCanvasRecord(registry, `${currentCanvas.label} workspace`);
    const state = snapshotCanvasWorkspaceState(workspaceState);
    const nextRegistry: CanvasRegistry = {
      ...created.registry,
      workspaceStates: {
        ...(created.registry.workspaceStates ?? {}),
        [created.canvas.id]: state,
      },
    };
    const targetStorageKey = canvasRuntimeWorkspaceStorageKey(
      runtimeScope,
      runtimeRevision,
      created.canvas.id,
    );
    window.localStorage.setItem(targetStorageKey, JSON.stringify(state));
    window.localStorage.setItem(runtimeRegistryStorageKey, JSON.stringify(nextRegistry));
    setRegistry(nextRegistry);
    if (replayRun) {
      openReplayFocus(nextRegistry, state);
      return;
    }
    const focusedWindow = window.open(
      focusCanvasUrl(created.canvas.id),
      "_blank",
      "noopener,noreferrer",
    );
    if (focusedWindow) focusedWindow.opener = null;
  }

  function applyRuntimeRebase() {
    if (!runtimeBase || !runtimeRebase) return;
    window.localStorage.setItem(runtimeRegistryStorageKey, JSON.stringify(runtimeRebase.registry));
    if (runtimeRebase.workspace) window.localStorage.setItem(workspaceStorageKey, JSON.stringify(runtimeRebase.workspace));
    else window.localStorage.removeItem(workspaceStorageKey);
    setRegistry(runtimeRebase.registry);
    setWorkspaceState(runtimeRebase.workspace);
    setRuntimeRebase(null);
    setOverlayEpoch((value) => value + 1);
  }

  function keepApprovedAfterRebase() {
    if (!runtimeBase) return;
    window.localStorage.removeItem(runtimeRegistryStorageKey);
    window.localStorage.removeItem(workspaceStorageKey);
    setRegistry(runtimeBase);
    setWorkspaceState(runtimeCanvasState(runtimeBase, workspaceStorageKey, canvasId, requestedInstanceId, false));
    setRuntimeRebase(null);
    setOverlayEpoch((value) => value + 1);
  }

  function saveDefaultLayout() {
    if (!workspaceState) return;
    const defaultState = snapshotCanvasWorkspaceState(workspaceState);
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
        {contextError && !replayRun ? <span className="canvas-context-warning" title={contextError}>Saved clock</span> : null}
        <div className="canvas-mode-context-slot">{modeControls}<CanvasPerformanceStrip state={performanceState} /></div>
        {managementEnabled ? <div className="canvas-toolbar-actions">{manager ? <button className="button secondary compact canvas-set-default" disabled={!workspaceState} onClick={saveDefaultLayout} type="button"><Save size={13} /> {defaultSaved ? "Default saved" : "Set default"}</button> : null}<button aria-expanded={managementOpen} aria-label="Canvas management" className="button secondary compact canvas-management-toggle" onClick={() => setManagementOpen((open) => !open)} type="button"><PanelRightOpen size={13} /> Manage</button></div> : null}
      </header>

      {contextError && replayRun ? <div aria-live="assertive" className="canvas-inline-error replay-runtime-error"><TriangleAlert aria-hidden="true" size={15} /><div><strong>{runtimeMode === "backtest_debug" ? "Backtest Debug" : runtimeMode === "backtest" ? "Backtest" : "Replay"} stopped</strong><span>{contextError}</span></div></div> : null}
      {error ? <div className="canvas-inline-error">{error}</div> : null}

      <TradingWorkspace
        key={`${workspaceStorageKey}:${overlayEpoch}`}
        allowMultipleInstances
        canPopOut={!runtimeBase || (Boolean(replayRun) && runtimeMode === "replay")}
        canvasTargets={runtimeBase ? [] : canvasTargets}
        clockLabel=""
        commandBarVisible={false}
        compact
        defaultOpenIds={manager ? MANAGER_DEFAULT_CONTAINER_IDS : initialCanvasState?.openIds ?? MANAGER_DEFAULT_CONTAINER_IDS}
        defaultStateOverride={manager ? registry.defaultState ?? null : initialCanvasState}
        definitionsOverride={TRADING_WORKSPACE_CONTAINERS}
        historicalSourceReady={!error}
        initialStateOverride={manager ? null : initialCanvasState}
        layoutPreset={managementEnabled ? "global" : "focus"}
        managementContent={manager
          ? <CanvasManager registry={registry} onCreate={() => openNewCanvas()} onOpen={(id) => window.open(focusCanvasUrl(id), "_blank", "noopener,noreferrer")} onRemove={removeCanvas} />
          : runtimeBase
            ? <><CanvasManager availableCanvasIds={new Set(Object.keys(registry.workspaceStates ?? {}))} registry={registry} onOpen={openRuntimeConfiguredCanvas} /><RuntimeCanvasScope mode={runtimeMode === "backtest_debug" ? "Backtest Debug" : runtimeMode === "backtest" ? "Backtest" : runtimeMode === "replay" ? "Replay" : runtimeMode === "research" ? "Research" : runtimeMode === "live" ? "Live" : runtimeMode === "paper" ? "Paper" : "Canvas"} onApplyRebase={applyRuntimeRebase} onKeepApproved={keepApprovedAfterRebase} onReset={resetRuntimeOverlay} onSaveAs={saveRuntimeWorkspace} rebase={runtimeRebase} revision={replayRun?.canvas_revision || approvedCanvas?.canvas_revision || runtimeRevision} /></>
            : null}
        managementOpen={managementEnabled && managementOpen}
        metaForContainer={metaForContainer}
        mode={runtimeMode === "canvas" || runtimeMode === "research" ? "replay" : runtimeMode}
        onContainerAdded={registerContainerInstance}
        onMoveContainerToCanvas={runtimeBase ? undefined : moveContainer}
        onMoveGroupToCanvas={runtimeBase ? undefined : moveGroup}
        onManagementClose={() => setManagementOpen(false)}
        onPopOutContainer={replayRun && runtimeMode === "replay" ? openReplayContainerCanvas : runtimeBase ? undefined : openNewCanvas}
        onPopOutGroup={replayRun && runtimeMode === "replay" ? openReplayGroupCanvas : runtimeBase ? undefined : openGroupCanvas}
        onStateChange={setWorkspaceState}
        renderContainer={(definition, instanceId) => {
          const settings = instanceSettings(registry, instanceId);
          const linkable = containerSupportsCanvasLink(definition.id);
          const group = linkable ? registry.linkAssignments[instanceId] ?? "none" : "none";
          const linkContext = group === "none" ? { symbol: settings.chart.symbol } : registry.linkContexts[group];
          const symbolEditable = containerSupportsSymbolLink(definition.id) && (group === "none" || registry.linkOwners[group] === instanceId);
          const linkedContainers: LinkedContainerState[] = group === "none" ? [] : (workspaceState?.openIds ?? [])
            .filter((candidateId) => {
              const candidateKind = workspaceContainerKind(candidateId, workspaceState);
              return containerSupportsCanvasLink(candidateKind) && registry.linkAssignments[candidateId] === group;
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
            liveMode={liveMode}
            onLinkChange={(nextGroup) => setContainerLink(instanceId, definition.id, nextGroup)}
            onLinkContextChange={(patch) => {
              if (group !== "none") updateLinkContext(group, patch);
              else if (patch.symbol) updateInstanceSettings(instanceId, (current) => {
                const symbol = patch.symbol!.trim().toUpperCase();
                if (definition.id !== "charts_quotes") return { ...current, chart: { ...current.chart, symbol } };
                return {
                  ...current,
                  chart: { ...current.chart, symbol },
                  charts_quotes: {
                    daily: { ...current.charts_quotes.daily, symbol },
                    layout: current.charts_quotes.layout,
                    main: { ...current.charts_quotes.main, symbol },
                    month: { ...current.charts_quotes.month, symbol },
                  },
                };
              });
            }}
            preview={preview}
            scannerError={scannerError}
            scannerLoading={scannerLoading}
            scannerSnapshot={scannerSnapshot}
            onTickerWorkspaceOpen={openChartsQuotesForTicker}
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
        storageKeyOverride={workspaceStorageKey}
        linkColorForContainer={(definition, instanceId) => containerSupportsCanvasLink(definition.id) ? canvasLinkGroupDefinition(registry.linkAssignments[instanceId] ?? "none")?.color : undefined}
        titleBarActionsForContainer={(definition, instanceId) => {
          const linkable = containerSupportsCanvasLink(definition.id);
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
        workspaceBadge={runtimeMode === "backtest_debug" ? "Backtest Debug" : runtimeMode === "backtest" ? "Backtest" : runtimeMode === "replay" ? "Replay" : runtimeMode === "research" ? "Research" : runtimeMode === "live" ? "Live" : runtimeMode === "paper" ? "Paper" : approvedCanvas ? "Canvas" : manager ? "Main" : "Focus"}
      />
    </div>
  );
}

function CanvasManager({ availableCanvasIds, onCreate, onOpen, onRemove, registry }: { availableCanvasIds?: Set<string>; onCreate?: () => void; onOpen: (id: string) => void; onRemove?: (id: string) => void; registry: CanvasRegistry }) {
  const configurationMode = Boolean(onCreate && onRemove);
  return <section aria-label="Canvas manager" className="canvas-manager-strip">
    <header><div><strong>Canvases</strong><small>{configurationMode ? "Separate saved workspaces" : "Approved Replay profile"}</small></div>{onCreate ? <button aria-label="New canvas" className="button secondary compact" onClick={onCreate} type="button"><Plus size={13} /> New</button> : null}</header>
    <div className="canvas-manager-items">{registry.canvases.map((canvas) => {
      const available = configurationMode || availableCanvasIds?.has(canvas.id) || (canvas.id === MAIN_CANVAS_ID && Boolean(registry.defaultState));
      const defaultCanvas = canvas.id === MAIN_CANVAS_ID;
      const disabled = configurationMode ? defaultCanvas : !available;
      return <article key={canvas.id} data-main={defaultCanvas ? "true" : "false"}>
      <button aria-label={disabled ? `${canvas.label} is unavailable` : `Open ${canvas.label}`} className="canvas-manager-open" disabled={disabled} onClick={() => onOpen(canvas.id)} title={disabled ? (configurationMode ? "Default Canvas" : "No saved layout was captured") : "Open Canvas in a new page"} type="button"><span>{canvas.label}</span><small>{configurationMode && defaultCanvas ? "Default" : available ? "Open" : "Unavailable"}</small>{disabled ? null : <ExternalLink size={11} />}</button>
      {defaultCanvas || !onRemove ? null : <button aria-label={`Remove ${canvas.label}`} className="toolbar-button compact" onClick={() => onRemove(canvas.id)} title="Remove canvas" type="button"><Trash2 size={12} /></button>}
    </article>;
    })}</div>
  </section>;
}

function RuntimeCanvasScope({ mode, onApplyRebase, onKeepApproved, onReset, onSaveAs, rebase, revision }: { mode: "Backtest" | "Backtest Debug" | "Canvas" | "Live" | "Paper" | "Replay" | "Research"; onApplyRebase: () => void; onKeepApproved: () => void; onReset: () => void; onSaveAs: () => void; rebase: CanvasRuntimeRebase | null; revision: string }) {
  return <section aria-label={`${mode} layout scope`} className="replay-layout-scope">
    <ShieldCheck aria-hidden="true" size={15} />
    <div><strong>{mode} workspace overlay</strong><small>{rebase ? `A newer approved Canvas is available. Three-way rebase found ${rebase.conflicts.length} conflict${rebase.conflicts.length === 1 ? "" : "s"}.` : `Starts from approved Canvas ${revision.slice(0, 10)}. Changes persist only for this revision and never rewrite Configuration defaults.`}</small>{rebase?.conflicts.length ? <span title={rebase.conflicts.join("\n")}>{rebase.conflicts.slice(0, 3).join(", ")}{rebase.conflicts.length > 3 ? ` +${rebase.conflicts.length - 3}` : ""}</span> : null}</div>
    {rebase ? <><button className="button primary compact" onClick={onApplyRebase} type="button"><RefreshCcw size={12} /> Apply rebase</button><button className="button secondary compact" onClick={onKeepApproved} type="button">Keep approved</button></> : null}
    <button className="button secondary compact" onClick={onSaveAs} type="button"><Save size={12} /> Save as workspace</button>
    <button className="button secondary compact" onClick={onReset} type="button"><RefreshCcw size={12} /> Reset to approved</button>
  </section>;
}

type SettingsUpdater = (update: ContainerSettings | ((current: ContainerSettings) => ContainerSettings)) => void;

function ContainerPreview({ canvasId, chartCutoffMs, definition, instanceId, linkContext, linkGroup, linkedContainers, linkOpen, liveMode, loading, onLinkChange, onLinkContextChange, onTickerWorkspaceOpen, preview, previewContext, requestedNewsId, requestedSecAccession, requestedSecCik, scannerError, scannerLoading, scannerSnapshot, settings, settingsOpen, symbolEditable, updateSettings }: {
  canvasId: string;
  chartCutoffMs: number;
  definition: WorkspaceContainerDefinition;
  instanceId: string;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  linkedContainers: LinkedContainerState[];
  linkOpen: boolean;
  liveMode: boolean;
  loading: boolean;
  onLinkChange: (group: CanvasLinkGroupId) => void;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  onTickerWorkspaceOpen: (ticker: string) => void;
  preview: CanvasPreview | null;
  scannerError: string;
  scannerLoading: boolean;
  scannerSnapshot: CanvasScannerSnapshot | null;
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
        ? <ChartContainerPreview cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} linkGroup={linkGroup} liveMode={liveMode} onLinkContextChange={onLinkContextChange} previewContext={previewContext} settings={settings} strategy={preview?.strategy} symbolEditable={symbolEditable} trading={preview?.trading} updateSettings={updateSettings} />
      : definition.id === "charts_quotes"
        ? <ChartsQuotesContainerPreview cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} liveMode={liveMode} onLinkContextChange={onLinkContextChange} previewContext={previewContext} settings={settings} strategy={preview?.strategy} symbolEditable={symbolEditable} trading={preview?.trading} updateSettings={updateSettings} />
      : definition.id === "microstructure"
        ? <QuotesTapeContainer end={liveMode ? undefined : new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.microstructure} start={liveMode ? undefined : dateInTimeZone(previewContext.sessionDate, "04:00", "America/New_York").toISOString()} symbol={linkContext.symbol} />
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
      : definition.id === "xbrl"
        ? <XbrlAnalysisContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.xbrl} symbol={linkContext.symbol} />
      : definition.id === "scanner"
        ? (scannerLoading || scannerSnapshot?.meta.status === "building") && !scannerSnapshot?.rows.length
          ? <div className="canvas-preview-loading">Building the complete historical scanner snapshot…</div>
          : scannerError && !scannerSnapshot
            ? <div className="canvas-inline-error">Historical scanner unavailable: {scannerError}</div>
            : <MarketScannerContainer asOf={scannerSnapshot?.as_of ?? new Date(chartCutoffMs).toISOString()} meta={scannerSnapshot?.meta ?? preview?.scanner_meta} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, scanner: { ...state.scanner, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} rows={scannerSnapshot?.rows ?? preview?.scanner ?? []} settings={settings.scanner} />
      : definition.id === "signal_stream"
        ? (scannerLoading || scannerSnapshot?.meta.status === "building") && !scannerSnapshot?.rows.length
          ? <div className="canvas-preview-loading">Loading the historical signal cross-section…</div>
          : scannerError && !scannerSnapshot
            ? <div className="canvas-inline-error">Historical signals unavailable: {scannerError}</div>
            : <SignalStreamContainer asOf={new Date(chartCutoffMs).toISOString()} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, signal_stream: { ...state.signal_stream, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} scannerRows={scannerSnapshot?.signal_rows ?? []} settings={settings.signal_stream} strategySignals={preview?.strategy.signals ?? []} />
      : definition.id === "watchlist"
        ? (scannerLoading || scannerSnapshot?.meta.status === "building") && !scannerSnapshot?.rows.length
          ? <div className="canvas-preview-loading">Loading the historical watchlist snapshot…</div>
          : scannerError && !scannerSnapshot
            ? <div className="canvas-inline-error">Historical watchlist unavailable: {scannerError}</div>
            : <WatchUniverseContainer asOf={new Date(chartCutoffMs).toISOString()} onSettingsChange={(change) => updateSettings((state) => ({ ...state, watchlist: { ...state.watchlist, ...(typeof change === "function" ? change(state.watchlist) : change) } }))} onTickerSelect={onTickerWorkspaceOpen} runtime={scannerSnapshot?.watchlist_runtime ?? null} scannerRows={scannerSnapshot?.rows ?? preview?.scanner ?? []} settings={settings.watchlist} />
      : definition.id === "strategy_activity"
        ? <StrategyActivityContainer asOf={new Date(chartCutoffMs).toISOString()} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, strategy_activity: { ...state.strategy_activity, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} settings={settings.strategy_activity} />
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
  if (id === "portfolio") return <PortfolioPreview data={preview.trading} settings={settings.portfolio} />;
  if (id === "positions") return <PositionsPreview data={preview.trading} onSymbolSelect={linkGroup === "none" ? undefined : (symbol) => onLinkContextChange({ symbol })} settings={settings.positions} />;
  if (id === "orders") return <OrdersPreview data={preview.trading} onSymbolSelect={linkGroup === "none" ? undefined : (symbol) => onLinkContextChange({ symbol })} settings={settings.orders} />;
  if (id === "fills") return <ExecutionsPreview data={preview.trading} settings={settings.fills} />;
  if (id === "closed_trades") return <ClosedTradesPreview data={preview.trading} settings={settings.closed_trades} />;
  if (id === "activity") return <ActivityPreview data={preview.trading} settings={settings.activity} />;
  if (id === "performance_journal") return <TradingJournalPreview data={preview.trading} settings={settings.performance_journal} />;
  if (id === "strategy") return <StrategyPreview data={preview.strategy} showSignals={settings.strategy.showSignals} />;
  return <EmptyState label="This diagnostic surface has no preview renderer." />;
}

type ChartContainerPreviewProps = {
  cutoffMs: number;
  instanceId: string;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  liveMode: boolean;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  previewContext: CanvasPreviewContext;
  settings: ContainerSettings;
  strategy?: CanvasPreview["strategy"];
  symbolEditable: boolean;
  trading?: CanonicalTradingPreview;
  updateSettings: SettingsUpdater;
};

const ChartContainerPreview = memo(function ChartContainerPreview({ cutoffMs, instanceId, linkContext, liveMode, onLinkContextChange, previewContext, settings, strategy, symbolEditable, trading, updateSettings }: ChartContainerPreviewProps) {
  const liveChart = useCanvasHistoricalChart(linkContext.symbol, settings.chart.timeframe, cutoffMs, previewContext.sessionDate, settings.chart.visibleIndicators, liveMode);
  const presentations = useTickerPresentations([linkContext.symbol]);
  const strategyDecisions = useMemo(() => strategyDecisionEvents(strategy), [strategy]);
  const strategyPresentation = useMemo(() => resolvedStrategyPresentation(strategy), [strategy]);
  return <ChartPreview changeAsOf={new Date(cutoffMs).toISOString()} chartSettings={settings.chart} instanceId={instanceId} linkContext={linkContext} liveChart={liveChart} logoUrl={presentations[linkContext.symbol]?.logo_url} onChartSettingsChange={(next) => updateSettings((current) => ({ ...current, chart: next }))} onLinkContextChange={onLinkContextChange} strategyDecisions={strategyDecisions} strategyPresentation={strategyPresentation} symbolEditable={symbolEditable} trading={trading} />;
}, chartContainerPreviewPropsEqual);

function ChartsQuotesContainerPreview({ cutoffMs, instanceId, linkContext, liveMode, onLinkContextChange, previewContext, settings, strategy, symbolEditable, trading, updateSettings }: Omit<ChartContainerPreviewProps, "linkGroup">) {
  const main = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.main.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.main.visibleIndicators, liveMode);
  const month = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.month.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.month.visibleIndicators, liveMode);
  const daily = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.daily.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.daily.visibleIndicators, liveMode);
  const presentations = useTickerPresentations([linkContext.symbol]);
  const logoUrl = presentations[linkContext.symbol]?.logo_url;
  const changeAsOf = new Date(cutoffMs).toISOString();
  const strategyDecisions = useMemo(() => strategyDecisionEvents(strategy), [strategy]);
  const strategyPresentation = useMemo(() => resolvedStrategyPresentation(strategy), [strategy]);
  const updateSlot = (slot: "daily" | "main" | "month", next: CanvasChartSettings) => {
    updateSettings((current) => ({ ...current, charts_quotes: { ...current.charts_quotes, [slot]: next } }));
  };
  const proposalBar = main.bars.at(-1);
  const proposalMarketSnapshot = proposalBar ? {
    freshness: main.error || main.loading ? "unavailable" : "ready",
    observed_at: proposalBar.last_event_ts || proposalBar.bar_end || proposalBar.bar_start,
    reference_price: proposalBar.close,
    source_sequence: proposalBar.last_event_ts || proposalBar.bar_start,
    source: liveMode ? "qmd_live_chart_bar" : "qmd_history_chart_bar",
    tick_size: 0.01,
  } : null;
  const chartProps = { changeAsOf, linkContext, logoUrl, onLinkContextChange, strategyDecisions, strategyPresentation, symbolEditable: false, toolbarVariant: "compact" as const, trading };
  return <ChartsQuotesMarketLayout
    dailyChart={<ChartPreview {...chartProps} baseHeight={255} chartSettings={settings.charts_quotes.daily} fillHeight instanceId={`${instanceId}.daily`} liveChart={daily} onChartSettingsChange={(next) => updateSlot("daily", { ...next, timeframe: "1d" })} timeframes={["1d"]} />}
    end={liveMode ? undefined : changeAsOf}
    layout={settings.charts_quotes.layout}
    mainChart={<ChartPreview {...chartProps} baseHeight={460} chartSettings={settings.charts_quotes.main} fillHeight instanceId={`${instanceId}.main`} liveChart={main} onChartSettingsChange={(next) => updateSlot("main", next)} timeframes={HISTORICAL_TIMEFRAMES} />}
    monthChart={<ChartPreview {...chartProps} baseHeight={255} chartSettings={settings.charts_quotes.month} fillHeight instanceId={`${instanceId}.month`} liveChart={month} onChartSettingsChange={(next) => updateSlot("month", { ...next, timeframe: "1mo" })} timeframes={["1mo"]} />}
    onLayoutChange={(layout) => updateSettings((current) => ({ ...current, charts_quotes: { ...current.charts_quotes, layout } }))}
    onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined}
    start={liveMode ? undefined : dateInTimeZone(previewContext.sessionDate, "04:00", "America/New_York").toISOString()}
    symbol={linkContext.symbol}
    reservedPanel={<StrategyOrderEntry marketSnapshot={proposalMarketSnapshot} runtimeMode={liveMode ? String(trading?.mode || "") : undefined} strategy={strategy} symbol={linkContext.symbol} trading={trading} />}
  />;
}

function chartContainerPreviewPropsEqual(previous: ChartContainerPreviewProps, next: ChartContainerPreviewProps) {
  const previousChart = previous.settings.chart;
  const nextChart = next.settings.chart;
  return previous.instanceId === next.instanceId
    && previous.cutoffMs === next.cutoffMs
    && previous.liveMode === next.liveMode
    && previous.linkGroup === next.linkGroup
    && previous.linkContext.symbol === next.linkContext.symbol
    && previous.previewContext.sessionDate === next.previewContext.sessionDate
    && previous.previewContext.previewTime === next.previewContext.previewTime
    && tradingPositionSignature(previous.trading, previous.linkContext.symbol) === tradingPositionSignature(next.trading, next.linkContext.symbol)
    && strategyPresentationSignature(previous.strategy) === strategyPresentationSignature(next.strategy)
    && previous.symbolEditable === next.symbolEditable
    && previousChart.symbol === nextChart.symbol
    && previousChart.timeframe === nextChart.timeframe
    && previousChart.showVolume === nextChart.showVolume
    && stringArraysEqual(previousChart.visibleIndicators, nextChart.visibleIndicators);
}

function tradingPositionSignature(trading: CanonicalTradingPreview | undefined, symbol: string) {
  const row = trading?.positions.find((position) => nestedValue(position, "instrument", "symbol") === symbol);
  return row ? `${row.account_id}:${row.quantity}:${row.average_price}:${row.market_price}:${row.unrealized_pnl}:${row.source_event_time}` : "";
}

function strategyDecisionEvents(strategy: CanvasPreview["strategy"] | undefined): StrategyDecisionEvent[] {
  if (!strategy || strategy.fixture) return [];
  return strategy.signals.flatMap((row, index) => {
    const action = String(row.action || "wait").toLowerCase();
    const effectiveAt = String(row.effective_at || row.event_time || row.time || "");
    const ticker = String(row.ticker || row.symbol || "").trim().toUpperCase();
    if (!isStrategyAction(action) || !ticker || !Number.isFinite(Date.parse(effectiveAt))) return [];
    const directionValue = String(row.direction || "neutral").toLowerCase();
    const direction = directionValue === "bullish" || directionValue === "bearish" ? directionValue : "neutral";
    return [{
      action,
      confidence: Number(row.confidence || row.signal_confidence || 0),
      direction,
      effective_at: effectiveAt,
      event_id: String(row.event_id || row.signal_id || `${strategy.strategy_id}:${strategy.revision}:${index}`),
      invalidation_price: nullableNumber(row.invalidation_price),
      reference_price: nullableNumber(row.reference_price || row.value),
      score: Number(row.score || row.signal_score || row.magnitude || 0),
      strategy_id: strategy.strategy_id,
      strategy_revision: strategy.revision,
      ticker,
    }];
  });
}

function resolvedStrategyPresentation(strategy: CanvasPreview["strategy"] | undefined): StrategyChartPresentation {
  return { ...DEFAULT_STRATEGY_CHART_PRESENTATION, ...(strategy?.taxonomy?.presentation ?? {}) };
}

function strategyPresentationSignature(strategy: CanvasPreview["strategy"] | undefined) {
  if (!strategy || strategy.fixture) return "";
  return JSON.stringify({
    presentation: resolvedStrategyPresentation(strategy),
    revision: strategy.revision,
    signals: strategy.signals.map((row) => [
      row.event_id || row.signal_id,
      row.effective_at || row.event_time || row.time,
      row.action,
      row.confidence || row.signal_confidence,
      row.invalidation_price,
    ]),
    strategy_id: strategy.strategy_id,
  });
}

function isStrategyAction(value: string): value is StrategyAction {
  return ["enter_long", "add_long", "reduce_long", "take_profit", "exit", "hold", "wait"].includes(value);
}

function nullableNumber(value: unknown) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function stringArraysEqual(previous: readonly string[], next: readonly string[]) {
  return previous.length === next.length && previous.every((value, index) => value === next[index]);
}

function ChartPreview({
  baseHeight,
  changeAsOf,
  chartSettings,
  instanceId,
  linkContext,
  liveChart,
  logoUrl,
  onChartSettingsChange,
  onLinkContextChange,
  symbolEditable,
  timeframes = HISTORICAL_TIMEFRAMES,
  toolbarVariant = "full",
  fillHeight = false,
  strategyDecisions = EMPTY_STRATEGY_DECISIONS,
  strategyPresentation = DEFAULT_STRATEGY_CHART_PRESENTATION,
  trading,
}: {
  baseHeight?: number;
  changeAsOf: string;
  chartSettings: CanvasChartSettings;
  instanceId: string;
  linkContext: CanvasLinkContext;
  liveChart: CanvasLiveChartState;
  logoUrl?: string;
  onChartSettingsChange: (next: CanvasChartSettings) => void;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  symbolEditable: boolean;
  timeframes?: CanvasChartTimeframe[];
  toolbarVariant?: "full" | "compact";
  fillHeight?: boolean;
  strategyDecisions?: StrategyDecisionEvent[];
  strategyPresentation?: StrategyChartPresentation;
  trading?: CanonicalTradingPreview;
}) {
  const indicators = liveChart.indicators;
  const visibleIndicators = liveChart.indicatorsAvailable ? chartSettings.visibleIndicators : [];
  const timeframe = chartSettings.timeframe;
  const payload = useMemo<ChartPayload>(() => {
    const marketSignalMarkers = qmdMarketSignalChartMarkers(
      liveChart.marketSignalEvents,
      liveChart.bars,
      visibleIndicators,
    );
    const strategyMarkers = strategyPresentationMarkers(
      strategyDecisions.filter((event) => event.ticker === linkContext.symbol),
      liveChart.bars,
      strategyPresentation,
    );
    const strategyInvalidations = strategyInvalidationZones(
      strategyDecisions.filter((event) => event.ticker === linkContext.symbol),
      liveChart.bars,
      strategyPresentation,
    );
    return {
      candles: liveChart.bars.map((bar) => ({ close: bar.close, high: bar.high, low: bar.low, open: bar.open, time: Date.parse(bar.bar_start) / 1000 })),
      markers: [...(marketSignalMarkers ?? []), ...strategyMarkers],
      oscillator_series: historicalIndicatorSeries(indicators, "oscillator", visibleIndicators),
      overlay_series: historicalIndicatorSeries(indicators, "price", visibleIndicators),
      price_zones: [
        ...historicalMarketLevelZones(indicators, liveChart.bars, liveChart.structureEvents, liveChart.structureLevelHistory, visibleIndicators, timeframe),
        ...strategyInvalidations,
      ],
      regions: MACRO_TIMEFRAMES.has(timeframe) ? [] : extendedSessionRegions(liveChart.bars),
      volume: chartSettings.showVolume ? liveChart.bars.map((bar) => ({ color: bar.close >= bar.open ? "var(--success)" : "var(--danger)", time: Date.parse(bar.bar_start) / 1000, value: bar.volume })) : [],
    };
  }, [chartSettings.showVolume, indicators, linkContext.symbol, liveChart.bars, liveChart.marketSignalEvents, liveChart.structureEvents, liveChart.structureLevelHistory, strategyDecisions, strategyPresentation, timeframe, visibleIndicators]);
  function updateChart(symbol: string, nextTimeframe: CanvasChartTimeframe) {
    onChartSettingsChange({ ...chartSettings, symbol, timeframe: nextTimeframe });
    onLinkContextChange({ symbol });
  }
  const latestBar = liveChart.bars[liveChart.bars.length - 1];
  const sessionDate = latestBar?.session_date || latestBar?.bar_start.slice(0, 10);
  const activePosition = trading?.positions.find((row) => nestedValue(row, "instrument", "symbol") === linkContext.symbol && Number(row.quantity || 0) !== 0);
  const quantity = Number(activePosition?.quantity || 0);
  const averagePrice = Number(activePosition?.average_price || 0);
  const positionLine = activePosition && averagePrice > 0 ? {
    color: quantity > 0 ? "var(--success)" : "var(--danger)",
    labelParts: [{ text: quantity > 0 ? "LONG" : "SHORT", tone: "label" }, { text: `${Math.abs(quantity).toLocaleString()} @ ${money(averagePrice)}`, tone: "price" }],
    pnl: Number(activePosition.unrealized_pnl || 0),
    price: averagePrice,
    quantity,
  } satisfies LiveEntryLine : null;
  const emptyMessage = `No closed ${linkContext.symbol} ${timeframe} bars are available from QMD History at this Canvas clock.`;
  const infoMessage = [liveChart.historyNotice, indicatorProvenanceNotice(liveChart.indicatorProvenance)].filter(Boolean).join(" · ");
  return <ChartPanel baseHeight={baseHeight} canLoadEarlier={liveChart.canLoadEarlier} displayItemOptions={liveChart.indicatorsAvailable ? CHART_INDICATORS : []} emptyMessage={emptyMessage} enableFullscreen={false} errorMessage={liveChart.error || liveChart.historyError} featureOptions={[]} fillHeight={fillHeight} indicatorOptions={[]} initialFitMode="recent" infoMessage={infoMessage} liveEntryLine={positionLine} loading={liveChart.loading} loadingEarlier={liveChart.loadingEarlier} onLoadEarlier={liveChart.loadEarlier} onTickerChange={(symbol) => updateChart(symbol.toUpperCase(), timeframe)} onTimeframeChange={(nextTimeframe) => updateChart(linkContext.symbol, nextTimeframe as CanvasChartTimeframe)} onVisibleColumnsChange={(nextVisibleIndicators) => onChartSettingsChange({ ...chartSettings, visibleIndicators: nextVisibleIndicators })} payload={payload} periodEnd={sessionDate} periodStart={sessionDate} settingsStorageKey={`${CANVAS_SETTINGS_STORAGE_KEY}.${instanceId}`} ticker={linkContext.symbol} tickerChangeAsOf={changeAsOf} tickerEditable={symbolEditable} tickerLogoUrl={logoUrl} timeframe={timeframe} timeframes={timeframes} toolbarVariant={toolbarVariant} visibleColumns={visibleIndicators} />;
}

function historicalMarketLevelZones(
  rows: HistoricalIndicator[],
  bars: HistoricalBar[],
  structureEvents: QmdStructureEvent[],
  structureLevelHistory: QmdStructureLevelCandidate[],
  visibleIndicators: string[],
  timeframe: CanvasChartTimeframe,
): NonNullable<ChartPayload["price_zones"]> {
  if (!rows.length || !bars.length) return [];
  const chartEnd = Date.parse(bars[bars.length - 1].bar_end || bars[bars.length - 1].bar_start) / 1000 + 1;
  const zones: NonNullable<ChartPayload["price_zones"]> = [];
  if (visibleIndicators.includes("indicator.qmd_generic_structure")) {
    pushCurrentStructureLevels(zones, rows, chartEnd, timeframe);
    pushEventStructureSwingLevels(
      zones,
      structureEvents.length ? structureEvents : structureEventsFromSampledRows(rows),
      chartEnd,
      timeframe,
    );
    pushStructureEvents(
      zones,
      structureEvents.length ? structureEvents : structureEventsFromSampledRows(rows),
      chartEnd,
      timeframe,
    );
  }
  if (visibleIndicators.includes("indicator.qmd_level_footprint")) {
    pushLevelVolumeFootprint(
      zones,
      rows,
      structureLevelHistory,
      structureEvents.length ? structureEvents : structureEventsFromSampledRows(rows),
      chartEnd,
      timeframe,
      String(rows[rows.length - 1]?.session_date ?? ""),
    );
  }
  if (visibleIndicators.includes("indicator.qmd_reference_levels")) {
    pushGenericStructureReferences(zones, rows, chartEnd);
  }
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
        label: `${title} swing ${side}`,
        legendLabel: `${title} · Swing references`,
        minPixelHeight: 3,
        renderMode: "line",
        settingsId: `indicator.qmd_generic_structure.${scope}-swings`,
      });
    });
  });
}

type StructureZoneSpec = {
  compactLabel: string;
  label: string;
  prefix: string;
  scope: "micro" | "tactical" | "context";
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
  timeframe: CanvasChartTimeframe,
) {
  const latestIndex = rows.length - 1;
  const latest = rows[latestIndex];
  const candidates = Array.isArray(latest?.qmd_structure_active_levels)
    ? latest.qmd_structure_active_levels
      .filter(isQmdStructureLevelCandidate)
      .filter((candidate) => candidate.promotions.some((promotion) => promotion.timeframe === timeframe))
    : [];
  if (!candidates.length) return;
  const startIndex = latestIndex;
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
        label: `${sideName === "support" ? "Support" : "Resistance"} ${index + 1} · ${timeframe} promoted · ${percentLabel(confidence)} confidence · ${percentLabel(strength)} strength · ${formatQuantity(candidate.total_volume)} traded (${formatQuantity(candidate.buy_volume)} buy / ${formatQuantity(candidate.sell_volume)} sell)`,
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

function pushLevelVolumeFootprint(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  structureLevelHistory: QmdStructureLevelCandidate[],
  structureEvents: QmdStructureEvent[],
  chartEnd: number,
  selectedTimeframe: CanvasChartTimeframe,
  footprintSessionDate: string,
) {
  if (!footprintSessionDate) return;
  const encounteredById = new Map<string, QmdStructureLevelCandidate>();
  const levelKey = (candidate: QmdStructureLevelCandidate) =>
    `${candidate.footprint_session_date}:${candidate.created_at_ms}:${candidate.side}:${candidate.price.toFixed(8)}`;
  structureLevelHistory.forEach((candidate) => {
    if (
      isQmdStructureLevelCandidate(candidate)
      && candidate.footprint_session_date === footprintSessionDate
    ) {
      encounteredById.set(levelKey(candidate), candidate);
    }
  });
  rows.forEach((row) => {
    if (!Array.isArray(row.qmd_structure_active_levels)) return;
    row.qmd_structure_active_levels
      .filter(isQmdStructureLevelCandidate)
      .filter((candidate) => candidate.footprint_session_date === footprintSessionDate)
      .forEach((candidate) => {
        const key = levelKey(candidate);
        const existing = encounteredById.get(key);
        if (!existing || candidate.footprint_as_of_ms >= existing.footprint_as_of_ms) {
          encounteredById.set(key, candidate);
        }
      });
  });
  const binsByPrice = new Map<string, {
    asOfMs: number;
    buyVolume: number;
    neutralVolume: number;
    price: number;
    sellVolume: number;
    totalVolume: number;
  }>();
  encounteredById.forEach((candidate) => {
    const asOfMs = Number(candidate.footprint_as_of_ms) || 0;
    candidate.footprint.forEach((bin) => {
      if (!(Number(bin.price) > 0) || !(Number(bin.total_volume) > 0)) return;
      const key = Number(bin.price).toFixed(8);
      const current = binsByPrice.get(key);
      if (current && current.asOfMs > asOfMs) return;
      binsByPrice.set(key, {
        asOfMs,
        buyVolume: Math.max(0, Number(bin.buy_volume) || 0),
        neutralVolume: Math.max(0, Number(bin.neutral_volume) || 0),
        price: Number(bin.price),
        sellVolume: Math.max(0, Number(bin.sell_volume) || 0),
        totalVolume: Math.max(0, Number(bin.total_volume) || 0),
      });
    });
  });
  const orderedBins = [...binsByPrice.values()].sort((left, right) => left.price - right.price);
  orderedBins.forEach((bin, index) => {
    const nextPrice = orderedBins[index + 1]?.price;
    const previousPrice = orderedBins[index - 1]?.price;
    const tick = nextPrice && nextPrice > bin.price
      ? nextPrice - bin.price
      : previousPrice && bin.price > previousPrice
        ? bin.price - previousPrice
        : Math.max(bin.price * 0.00002, 0.0001);
    zones.push({
      annotationKind: "level-footprint",
      color: "var(--muted-foreground)",
      defaultVisible: true,
      displayItemId: "indicator.qmd_level_footprint",
      end: chartEnd,
      fillOpacity: 0.5,
      label: `${formatLevelPrice(bin.price)} · ${formatQuantity(bin.totalVolume)} traded (${formatQuantity(bin.buyVolume)} buy / ${formatQuantity(bin.sellVolume)} sell / ${formatQuantity(bin.neutralVolume)} neutral)`,
      latest: true,
      legendLabel: "Level volume footprint",
      lower: bin.price,
      buyVolume: bin.buyVolume,
      neutralVolume: bin.neutralVolume,
      preset: "axis-history",
      presetDefault: "axis-history",
      renderMode: "zone",
      sellVolume: bin.sellVolume,
      settingsId: "indicator.qmd_level_footprint",
      start: chartEnd,
      tone: "neutral",
      totalVolume: bin.totalVolume,
      upper: bin.price + tick,
    });
  });
  structureEvents
    .filter((event) =>
      event.event_kind === "level_promoted"
      && event.timeframe === selectedTimeframe
      && Number(event.price) > 0
      && Number(event.total_volume) > 0)
    .forEach((event) => {
      const totalVolume = Number(event.total_volume) || 0;
      const buyVolume = Math.max(0, Number(event.buy_volume) || 0);
      const sellVolume = Math.max(0, Number(event.sell_volume) || 0);
      const neutralVolume = Math.max(0, Number(event.neutral_volume) || 0);
      const start = Date.parse(event.pivot_at) / 1000;
      if (!Number.isFinite(start)) return;
      zones.push({
        annotationKind: "swing-footprint",
        buyVolume,
        color: "var(--muted-foreground)",
        defaultVisible: true,
        displayItemId: "indicator.qmd_level_footprint",
        end: start,
        label: `${selectedTimeframe} swing footprint · ${formatLevelPrice(Number(event.price))} · ${formatQuantity(totalVolume)} traded · ${percentLabel(buyVolume / totalVolume)} buy / ${percentLabel(sellVolume / totalVolume)} sell / ${percentLabel(neutralVolume / totalVolume)} neutral · ${formatQuantity(Number(event.trade_count) || 0)} trades`,
        latest: false,
        legendLabel: "Level volume footprint",
        lower: Number(event.price),
        neutralVolume,
        preset: "swing-rails",
        presetDefault: "axis-history",
        renderMode: "line",
        sellVolume,
        settingsId: "indicator.qmd_level_footprint",
        start,
        tone: Number(event.direction) < 0 ? "sell" : "buy",
        totalVolume,
        upper: Number(event.price),
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
    && typeof candidate.footprint_session_date === "string"
    && candidate.footprint_session_date.length > 0
    && Number.isFinite(candidate.footprint_as_of_ms)
    && Number(candidate.footprint_as_of_ms) > 0
    && Array.isArray(candidate.promotions)
    && Array.isArray(candidate.footprint)
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
  const scopeOpacity = spec.scope === "micro" ? 0.55 : spec.scope === "tactical" ? 0.72 : spec.scope === "context" ? 0.62 : 1;
  const color = support ? "var(--success)" : "var(--danger)";
  zones.push({
    annotationKind: support ? "liquidity-support" : "liquidity-resistance",
    axisLabelDefault: false,
    borderColor: color,
    borderOpacity: (0.22 + 0.48 * spec.confidence) * scopeOpacity,
    borderStyle: spec.scope === "context" ? "dashed" : spec.scope === "micro" ? "dotted" : "solid",
    borderWidth: 0.75 + 0.75 * spec.confidence,
    color,
    compactLabel: spec.compactLabel,
    confidence: spec.confidence,
    defaultVisible: false,
    displayItemId: "indicator.qmd_generic_structure",
    end,
    fillColor: color,
    fillOpacity: 0.01 + spec.strength * 0.04 * scopeOpacity,
    historicalLabelsDefault: false,
    label: `${spec.label} · ${percentLabel(spec.strength)} strength · ${percentLabel(spec.confidence)} confidence`,
    latest,
    legendLabel: `${spec.scope[0].toUpperCase()}${spec.scope.slice(1)} zones`,
    lower: spec.lower > 0 ? spec.lower : spec.price,
    minPixelHeight: 4,
    settingsId: `indicator.qmd_generic_structure.${spec.scope}-zones`,
    start,
    strength: spec.strength,
    upper: spec.upper > 0 ? spec.upper : spec.price,
  });
}

const QMD_STRUCTURE_TIMEFRAMES = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"] as const;
const QMD_STRUCTURE_HISTORY_LIMIT = 1_000;

function qmdStructureSwingLayerId(timeframe: string) {
  return `indicator.qmd_generic_structure.v9.${timeframe}.swings`;
}

function qmdStructureBreakLayerId(timeframe: string) {
  return `indicator.qmd_generic_structure.v9.${timeframe}.breaks`;
}

function qmdStructureTimeframeSeconds(timeframe: string) {
  const values: Record<string, number> = {
    "100ms": 0.1,
    "1s": 1,
    "5s": 5,
    "10s": 10,
    "30s": 30,
    "1m": 60,
    "5m": 300,
    "1h": 3_600,
  };
  return values[timeframe] ?? 0;
}

function retainStructureEventsPerTimeframe(
  events: QmdStructureEvent[],
  predicate: (event: QmdStructureEvent) => boolean,
) {
  const retainedIds = new Set(
    QMD_STRUCTURE_TIMEFRAMES.flatMap((timeframe) =>
      events
        .filter((event) => event.timeframe === timeframe && predicate(event))
        .slice(-QMD_STRUCTURE_HISTORY_LIMIT)
        .map((event) => event.event_id),
    ),
  );
  return events.filter((event) => retainedIds.has(event.event_id));
}

function pushEventStructureSwingLevels(
  zones: NonNullable<ChartPayload["price_zones"]>,
  events: QmdStructureEvent[],
  chartEnd: number,
  selectedTimeframe: CanvasChartTimeframe,
) {
  const ordered = [...events].sort((left, right) =>
    Date.parse(left.confirmed_at) - Date.parse(right.confirmed_at) || left.event_id - right.event_id);
  const lifecycleByLevel = new Map<number, QmdStructureEvent[]>();
  ordered.forEach((event) => {
    if (!Number.isFinite(event.level_id) || Number(event.level_id) <= 0) return;
    const levelId = Number(event.level_id);
    const levelEvents = lifecycleByLevel.get(levelId) ?? [];
    levelEvents.push(event);
    lifecycleByLevel.set(levelId, levelEvents);
  });
  const promoted = retainStructureEventsPerTimeframe(
    ordered,
    (event) => event.event_kind === "level_promoted",
  );
  promoted.forEach((event, eventIndex) => {
      const timeframe = event.timeframe as CanvasChartTimeframe;
      const start = Date.parse(event.pivot_at) / 1000;
      const promotedAt = Date.parse(event.confirmed_at);
      const price = Number(event.price);
      if (!Number.isFinite(start) || !(price > 0)) return;
      const terminal = lifecycleByLevel.get(Number(event.level_id))?.find((candidate) =>
        Date.parse(candidate.confirmed_at) >= promotedAt
        && ["structure_crossed", "bos", "choch", "structure_break"].includes(candidate.event_kind));
      const nextSameSide = promoted.slice(eventIndex + 1).find((candidate) =>
        candidate.timeframe === event.timeframe && Math.sign(Number(candidate.direction)) === Math.sign(Number(event.direction)));
      const end = Math.min(
        chartEnd,
        terminal ? Date.parse(terminal.confirmed_at) / 1000 : chartEnd,
        nextSameSide ? Date.parse(nextSameSide.pivot_at) / 1000 : chartEnd,
      );
      if (!Number.isFinite(end) || end <= start) return;
      const swingHigh = Number(event.direction) < 0;
      const color = swingHigh ? "var(--danger)" : "var(--success)";
      zones.push({
        annotationKind: swingHigh ? "swing-high" : "swing-low",
        axisLabelDefault: false,
        borderColor: color,
        borderOpacity: 0.5,
        borderStyle: "solid",
        borderWidth: 4,
        color,
        compactLabel: swingHigh ? "SH" : "SL",
        confidence: Number(event.confidence || 0),
        defaultVisible: timeframe === selectedTimeframe,
        displayItemId: "indicator.qmd_generic_structure",
        end,
        fillOpacity: 0,
        historicalLabelsDefault: timeframe === selectedTimeframe,
        historyTimeframeSeconds: qmdStructureTimeframeSeconds(timeframe),
        label: `${timeframe} local swing ${swingHigh ? "high" : "low"} · ${formatLevelPrice(price)} · causal confirmation ${event.confirmed_at} · ${percentLabel(Number(event.confidence || 0))} confidence`,
        latest: !terminal,
        legendLabel: `${timeframe} · Swing levels`,
        lower: price,
        minPixelHeight: 1,
        opacityDefault: 0.5,
        renderMode: "line",
        settingsId: qmdStructureSwingLayerId(timeframe),
        start,
        strength: Number(event.strength || 0),
        tone: swingHigh ? "sell" : "buy",
        upper: price,
        zoneHeightMode: "price",
      });
    });
}

function structureEventsFromSampledRows(rows: HistoricalIndicator[]): QmdStructureEvent[] {
  const events: QmdStructureEvent[] = [];
  let previousId = "";
  rows.forEach((row) => {
    const eventId = String(row.qmd_structure_event_id || "");
    const eventKind = String(row.qmd_structure_event_kind || "").toLowerCase();
    if (!eventId || eventId === "0" || eventId === previousId || !["bos", "choch", "structure_break"].includes(eventKind)) {
      previousId = eventId;
      return;
    }
    const confirmedAtMs = finiteNumber(row.qmd_structure_event_at_ms);
    const pivotAtMs = finiteNumber(row.qmd_structure_event_pivot_at_ms);
    const price = finiteNumber(row.qmd_structure_event_price);
    if (!(confirmedAtMs > 0) || !(pivotAtMs > 0) || !(price > 0)) {
      previousId = eventId;
      return;
    }
    events.push({
      algorithm_version: 0,
      confidence: finiteNumber(row.qmd_structure_confidence),
      confirmed_at: new Date(confirmedAtMs).toISOString(),
      direction: finiteNumber(row.qmd_structure_event_direction),
      event_id: Number(eventId),
      event_kind: eventKind,
      lower: price,
      pivot_at: new Date(pivotAtMs).toISOString(),
      price,
      timeframe: String(row.qmd_structure_event_timeframe || "").toLowerCase(),
      strength: finiteNumber(row.qmd_structure_strength),
      sym: "",
      upper: price,
    });
    previousId = eventId;
  });
  return events;
}

function pushStructureEvents(
  zones: NonNullable<ChartPayload["price_zones"]>,
  events: QmdStructureEvent[],
  chartEnd: number,
  selectedTimeframe: CanvasChartTimeframe,
) {
  retainStructureEventsPerTimeframe(
    events,
    (event) => ["bos", "choch", "structure_break"].includes(String(event.event_kind || "").toLowerCase()),
  )
    .forEach((event) => {
    const confirmedAt = Date.parse(event.confirmed_at) / 1000;
    const direction = Number(event.direction || 0);
    const kind = String(event.event_kind || "").toLowerCase();
    const pivotAt = Date.parse(event.pivot_at) / 1000;
    const price = Number(event.price || 0);
    const scale = String(event.timeframe || "").toLowerCase();
    const end = Math.min(chartEnd, confirmedAt);
    if (!(price > 0) || !Number.isFinite(pivotAt) || !Number.isFinite(confirmedAt) || !(end > pivotAt)) return;
    const bullish = direction > 0;
    const label = kind === "choch" ? "CHoCH" : kind === "bos" ? "BoS" : "Break";
    if (!QMD_STRUCTURE_TIMEFRAMES.includes(scale as typeof QMD_STRUCTURE_TIMEFRAMES[number])) return;
    zones.push({
      annotationKind: kind === "structure_break" ? "structure-break" : kind === "choch" ? "choch" : "bos",
      borderColor: bullish ? "var(--success)" : "var(--danger)",
      borderOpacity: 0.82,
      borderStyle: "dashed",
      borderWidth: 1,
      color: bullish ? "var(--success)" : "var(--danger)",
      compactLabel: label,
      displayItemId: "indicator.qmd_generic_structure",
      end,
      eventTime: confirmedAt,
      fillOpacity: 0,
      historicalLabelsDefault: scale === selectedTimeframe,
      historyTimeframeSeconds: qmdStructureTimeframeSeconds(scale),
      label: `${bullish ? "Bullish" : "Bearish"} ${label} · ${scale || "structure"} · ${formatLevelPrice(price)}`,
      latest: false,
      defaultVisible: scale === selectedTimeframe,
      legendLabel: `${scale} · Structure breaks`,
      lower: price,
      minPixelHeight: 1,
      renderMode: "line",
      settingsId: qmdStructureBreakLayerId(scale),
      start: pivotAt,
      tone: bullish ? "buy" : "sell",
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
    ["qmd_structure_session_high", "Extended-session high", "Sess H", "var(--info)", "session", "Extended session H/L", false, "sell"],
    ["qmd_structure_session_low", "Extended-session low", "Sess L", "var(--info)", "session", "Extended session H/L", false, "buy"],
    ["qmd_structure_opening_range_high", "Opening range high", "OR H", "var(--foreground)", "opening-range", "Opening range", true, "sell"],
    ["qmd_structure_opening_range_low", "Opening range low", "OR L", "var(--foreground)", "opening-range", "Opening range", true, "buy"],
    ["qmd_structure_trade_volume_poc", "Eligible-trade volume POC", "POC", "var(--primary)", "poc", "Trade-volume POC", true, "neutral"],
    ["qmd_structure_luld_upper", "Estimated LULD upper", "LULD U", "var(--danger)", "luld", "Estimated LULD", false, "sell"],
    ["qmd_structure_luld_lower", "Estimated LULD lower", "LULD L", "var(--success)", "luld", "Estimated LULD", false, "buy"],
    ["qmd_structure_52_week_high", "52-week high", "52W H", "var(--warning)", "52-week", "52-week H/L", false, "sell"],
    ["qmd_structure_52_week_low", "52-week low", "52W L", "var(--info)", "52-week", "52-week H/L", false, "buy"],
    ["qmd_structure_prior_month_high", "Prior-month high", "PrevM H", "var(--primary)", "prior-month", "Prior month H/L/C", false, "sell"],
    ["qmd_structure_prior_month_low", "Prior-month low", "PrevM L", "var(--primary)", "prior-month", "Prior month H/L/C", false, "buy"],
    ["qmd_structure_prior_month_close", "Prior-month close", "PrevM C", "var(--muted-foreground)", "prior-month", "Prior month H/L/C", false, "neutral"],
  ] as const;
  specs.forEach(([column, label, compactLabel, color, settingsSuffix, legendLabel, axisLabelDefault, tone]) => {
    const settingsGroup = settingsSuffix === "session"
      ? "session-levels"
      : ["52-week", "prior-month"].includes(settingsSuffix)
        ? "higher-timeframe"
        : settingsSuffix;
    const groupedLegendLabel = settingsGroup === "session-levels"
      ? "Extended session H/L"
      : settingsGroup === "higher-timeframe"
        ? "Higher-timeframe levels"
        : legendLabel;
    pushTrailingLevelZones(zones, rows, column, chartEnd, LEVEL_SOURCE_HISTORY_BARS, {
      annotationKind: settingsGroup === "luld" ? "luld-line" : "level",
      axisLabelDefault,
      color,
      compactLabel,
      defaultVisible: ["opening-range", "poc"].includes(settingsGroup),
      displayItemId: "indicator.qmd_reference_levels",
      fillOpacity: 0.025,
      historicalLabelsDefault: false,
      label,
      legendLabel: groupedLegendLabel,
      minPixelHeight: 3,
      renderMode: "line",
      settingsId: `indicator.qmd_reference_levels.${settingsGroup}`,
      tone: tone === "neutral" ? undefined : tone,
    });
  });
}

type LevelZoneStyle = {
  annotationKind: "level" | "luld-line" | "liquidity-resistance" | "liquidity-support" | "swing-high" | "swing-low";
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
  label: string;
  legendLabel: string;
  minPixelHeight: number;
  renderMode?: "line" | "zone";
  settingsId: string;
  strength?: number;
  tone?: "buy" | "sell";
};

const LEVEL_SOURCE_HISTORY_BARS = 1000;


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
    label: `${style.label} · ${formatLevelPrice(value)}`,
    latest: endIndex >= rows.length,
    legendLabel: style.legendLabel,
    lower: value,
    minPixelHeight: style.minPixelHeight,
    renderMode: style.renderMode ?? "zone",
    settingsId: style.settingsId,
    start,
    strength: style.strength,
    tone: style.tone,
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
  const latestComposite = [...rows].reverse().find((row) => Number.isFinite(Number(row.flow_structure_composite_score)));
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
    label: spec.column === "flow_structure_composite_score"
      ? flowStructureCompositeLabel(latestComposite)
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
    ...(colorMode === "confidence-sign" ? { confidence: boundedUnit(column === "flow_structure_composite_score" ? row.flow_structure_composite_confidence : row.qmd_structure_confidence) } : {}),
    ...(column === "flow_structure_composite_score"
      ? { tone: flowStructureBiasTone(String(row.flow_structure_composite_bias || "neutral")) }
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

function flowStructureBiasTone(bias: string): "buy" | "neutral" | "sell" {
  if (bias.toLowerCase() === "bullish") return "buy";
  if (bias.toLowerCase() === "bearish") return "sell";
  return "neutral";
}

function flowStructureCompositeLabel(row?: HistoricalIndicator) {
  const bias = String(row?.flow_structure_composite_bias || "neutral").toUpperCase();
  const confidence = boundedUnit(row?.flow_structure_composite_confidence);
  return `Composite ${bias} · ${Math.round(confidence * 100)}%`;
}

function PreviewTable({ columns, onSymbolSelect, rows }: { columns: string[]; onSymbolSelect?: (symbol: string) => void; rows: PreviewRow[] }) {
  const tickerColumns = columns.filter(isPreviewTickerColumn);
  const presentations = useTickerPresentations(rows.flatMap((row) => tickerColumns.map((column) => String(row[column] || ""))));
  if (!rows.length) return <EmptyState label="No point-in-time rows" />;
  return <div className="canvas-preview-table-wrap"><table className="canvas-preview-table"><thead><tr>{columns.map((column) => <th key={column}>{labelFor(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={previewRowKey(row, columns, index)}>{columns.map((column) => <td className={`preview-cell-${column.replace(/[^a-z0-9_-]/gi, "-")}`} data-tone={cellTone(row[column], column)} key={column}><PreviewCell column={column} onSymbolSelect={onSymbolSelect} presentations={presentations} row={row} /></td>)}</tr>)}</tbody></table></div>;
}

type TradingDataTableProps = {
  columns: string[];
  defaultSort?: string;
  filterColumn?: string;
  filterLabel?: string;
  onSymbolSelect?: (symbol: string) => void;
  renderExpanded?: (row: PreviewRow) => ReactNode;
  rows: PreviewRow[];
  searchPlaceholder: string;
};

function TradingDataTable({ columns, defaultSort, filterColumn, filterLabel = "All", onSymbolSelect, renderExpanded, rows, searchPlaceholder }: TradingDataTableProps) {
  const [queryText, setQueryText] = useState("");
  const [filterValue, setFilterValue] = useState("all");
  const [sortColumn, setSortColumn] = useState(defaultSort || columns[0] || "");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [expandedKey, setExpandedKey] = useState("");
  const tickerColumns = columns.filter(isPreviewTickerColumn);
  const presentations = useTickerPresentations(rows.flatMap((row) => tickerColumns.map((column) => String(row[column] || ""))));
  const filterOptions = useMemo(() => filterColumn ? Array.from(new Set(rows.map((row) => String(row[filterColumn] ?? "").trim()).filter(Boolean))).sort((left, right) => left.localeCompare(right)) : [], [filterColumn, rows]);
  const visibleRows = useMemo(() => {
    const queryValue = queryText.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      if (filterColumn && filterValue !== "all" && String(row[filterColumn] ?? "") !== filterValue) return false;
      if (!queryValue) return true;
      return columns.some((column) => searchableValue(row[column]).includes(queryValue));
    });
    return [...filtered].sort((left, right) => compareTradingValues(left[sortColumn], right[sortColumn]) * (sortDirection === "asc" ? 1 : -1));
  }, [columns, filterColumn, filterValue, queryText, rows, sortColumn, sortDirection]);
  function changeSort(column: string) {
    if (sortColumn === column) setSortDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSortColumn(column); setSortDirection("desc"); }
  }
  return <div className="trading-table-shell">
    <div className="trading-table-toolbar">
      <label className="trading-table-search"><Search aria-hidden="true" size={14} /><input aria-label={searchPlaceholder} onChange={(event) => setQueryText(event.target.value)} placeholder={searchPlaceholder} value={queryText} /></label>
      {filterColumn ? <label className="trading-table-filter"><Filter aria-hidden="true" size={13} /><select aria-label={`Filter by ${filterLabel}`} onChange={(event) => setFilterValue(event.target.value)} value={filterValue}><option value="all">{filterLabel}</option>{filterOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select></label> : null}
      <span className="trading-table-count">{visibleRows.length} of {rows.length}</span>
    </div>
    {!visibleRows.length ? <EmptyState label={rows.length ? "No rows match the active search and filter" : "No point-in-time rows"} /> : <div className="canvas-preview-table-wrap"><table className="canvas-preview-table trading-data-table"><thead><tr>{renderExpanded ? <th aria-label="Expand row" className="trading-expand-column" /> : null}{columns.map((column) => <th aria-sort={sortColumn === column ? (sortDirection === "asc" ? "ascending" : "descending") : "none"} key={column}><button onClick={() => changeSort(column)} type="button"><span>{labelFor(column)}</span>{sortColumn === column ? sortDirection === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} /> : <ArrowUpDown size={11} />}</button></th>)}</tr></thead><tbody>{visibleRows.map((row, index) => {
      const key = previewRowKey(row, columns, index);
      const expanded = expandedKey === key;
      return <FragmentRow columns={columns} expanded={expanded} key={key} onExpand={renderExpanded ? () => setExpandedKey(expanded ? "" : key) : undefined} onSymbolSelect={onSymbolSelect} presentations={presentations} renderExpanded={renderExpanded} row={row} />;
    })}</tbody></table></div>}
  </div>;
}

function FragmentRow({ columns, expanded, onExpand, onSymbolSelect, presentations, renderExpanded, row }: { columns: string[]; expanded: boolean; onExpand?: () => void; onSymbolSelect?: (symbol: string) => void; presentations: ReturnType<typeof useTickerPresentations>; renderExpanded?: (row: PreviewRow) => ReactNode; row: PreviewRow }) {
  return <>{<tr className={expanded ? "is-expanded" : undefined}>{renderExpanded ? <td className="trading-expand-column"><button aria-label={expanded ? "Collapse row" : "Expand row"} aria-expanded={expanded} onClick={onExpand} type="button">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button></td> : null}{columns.map((column) => <td className={`preview-cell-${column.replace(/[^a-z0-9_-]/gi, "-")}`} data-tone={cellTone(row[column], column)} key={column}><PreviewCell column={column} onSymbolSelect={onSymbolSelect} presentations={presentations} row={row} /></td>)}</tr>}{expanded && renderExpanded ? <tr className="trading-expanded-row"><td colSpan={columns.length + 1}>{renderExpanded(row)}</td></tr> : null}</>;
}

function searchableValue(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value).toLowerCase();
  return String(value).toLowerCase();
}

function compareTradingValues(left: unknown, right: unknown) {
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  if (left !== "" && right !== "" && Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber;
  const leftDate = Date.parse(String(left || ""));
  const rightDate = Date.parse(String(right || ""));
  if (Number.isFinite(leftDate) && Number.isFinite(rightDate)) return leftDate - rightDate;
  return String(left ?? "").localeCompare(String(right ?? ""), undefined, { numeric: true, sensitivity: "base" });
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

function PortfolioPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["portfolio"] }) {
  const metrics = data.portfolio.metrics;
  const exposure = data.portfolio.exposure;
  const ledgerRows = data.ledger.map((row) => ({ account: row.account_id, currency: row.currency, cash: nestedValue(row, "values", "cashbalance", "cashBalance"), settled: nestedValue(row, "values", "settledcash", "settledCash"), net_liquidation: nestedValue(row, "values", "netliquidationvalue", "netLiquidationValue") }));
  return <section className="trading-preview trading-portfolio-preview">
    <TradingFreshness data={data} />
    <div className="trading-primary-metrics">
      <TradingMetric label="Net liquidation" value={money(metrics.net_liquidation)} tone="primary" />
      <TradingMetric label="Available funds" value={money(metrics.available_funds)} tone="positive" />
      <TradingMetric label="Excess liquidity" value={money(metrics.excess_liquidity)} tone="positive" />
      <TradingMetric label="Buying power" value={money(metrics.buying_power)} />
      {settings.showPnl ? <TradingMetric label="Unrealized P&L" value={signedMoney(metrics.unrealized_pnl)} tone={numberTone(metrics.unrealized_pnl)} /> : null}
      {settings.showPnl ? <TradingMetric label="Realized P&L" value={signedMoney(metrics.realized_pnl)} tone={numberTone(metrics.realized_pnl)} /> : null}
    </div>
    {settings.showExposure ? <div className="trading-exposure-grid"><TradingMetric label="Long exposure" value={money(exposure.long_value)} tone="positive" /><TradingMetric label="Short exposure" value={money(exposure.short_value)} tone="negative" /><TradingMetric label="Net exposure" value={signedMoney(exposure.net_value)} tone={numberTone(exposure.net_value)} /><TradingMetric label="Gross exposure" value={money(exposure.gross_value)} /></div> : null}
    <div className="trading-secondary-heading"><strong>Cash ledger</strong><span>Every broker currency; BASE is not substituted for local balances</span></div>
    <PreviewTable columns={["account", "currency", "cash", "settled", "net_liquidation"]} rows={ledgerRows} />
    {data.portfolio.management ? <PortfolioManagementPreview data={data} management={data.portfolio.management} /> : null}
  </section>;
}

function PortfolioManagementPreview({ data, management }: { data: CanonicalTradingPreview; management: NonNullable<CanonicalTradingPreview["portfolio"]["management"]> }) {
  const [accounts, setAccounts] = useState(management.accounts);
  const [operationalMetrics, setOperationalMetrics] = useState(management.operational_metrics);
  const [pending, setPending] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    setAccounts(management.accounts);
    setOperationalMetrics(management.operational_metrics);
  }, [management]);
  const operational = data.mode === "live" || data.mode === "paper";
  const command = async (
    accountKey: string,
    value: "pause_entries" | "resume_entries" | "reduce_only" | "reconcile" | "select_policy" | "disable_strategy" | "enable_strategy" | "kill_entries" | "emergency_flatten",
    detail: Record<string, string> = {},
  ) => {
    const commandKey = `${accountKey}:${value}`;
    setPending(commandKey);
    setMessage("");
    try {
      const result = await api<{
        control_mode?: string;
        disabled_strategy_allocations?: string[];
        execution_required?: boolean;
        policy?: Record<string, unknown> & { identity?: string };
        portfolio_management?: typeof management;
      }>(
        `/api/trading/portfolio-management/${encodeURIComponent(accountKey)}/commands`,
        {
          body: JSON.stringify({ account_keys: accountKey, account_type: data.mode, command: value, detail, reason: "Canvas operator command" }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      if (result.portfolio_management) {
        setAccounts(result.portfolio_management.accounts);
        setOperationalMetrics(result.portfolio_management.operational_metrics);
      }
      else setAccounts((current) => current.map((row) => row.account_key === accountKey ? {
        ...row,
        ...(result.control_mode ? { control_mode: result.control_mode } : {}),
        ...(result.policy ? { policy: result.policy } : {}),
        ...(result.disabled_strategy_allocations ? { disabled_strategy_allocations: result.disabled_strategy_allocations } : {}),
      } : row));
      setMessage(
        result.execution_required
          ? "Command queued for fresh validation by the authenticated trading runtime."
          : value === "reconcile"
          ? "Broker reconciliation completed."
          : "Portfolio control updated.",
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPending("");
    }
  };
  return <section className="portfolio-management-preview" aria-label="Portfolio management">
    <div className="trading-secondary-heading"><strong>Portfolio management</strong><span>IBKR-authoritative state · account-specific policy · portfolio approval before OMS</span></div>
    {management.stale ? <div className="trading-disclosure" data-tone="negative">Entries blocked: {management.stale_reason || "broker state is stale"}</div> : null}
    {message ? <div className="trading-disclosure" role="status">{message}</div> : null}
    {operationalMetrics ? <div className="portfolio-management-metrics" aria-label="Portfolio and OMS operational metrics">
      <TradingMetric label="Decisions" value={String(operationalMetrics.portfolio.decision_count)} />
      <TradingMetric label="Rejected" value={String(operationalMetrics.portfolio.disposition_counts.rejected || 0)} tone={operationalMetrics.portfolio.disposition_counts.rejected ? "negative" : "positive"} />
      <TradingMetric label="Active reservations" value={String(operationalMetrics.portfolio.active_reservation_count)} />
      <TradingMetric label="Reserved notional" value={money(operationalMetrics.portfolio.active_reserved_notional)} />
      <TradingMetric label="OMS groups" value={String(operationalMetrics.oms.managed_group_count)} />
      <TradingMetric label="Unknown outcome" value={String(operationalMetrics.oms.outcome_unknown_count)} tone={operationalMetrics.oms.outcome_unknown_count ? "negative" : "positive"} />
      <TradingMetric label="Reconcile failures" value={String(operationalMetrics.oms.reconciliation_failure_count)} tone={operationalMetrics.oms.reconciliation_failure_count ? "negative" : "positive"} />
      <TradingMetric label="Unprotected quantity" value={formatQuantity(operationalMetrics.oms.unprotected_quantity)} tone={operationalMetrics.oms.unprotected_quantity ? "negative" : "positive"} />
    </div> : null}
    <div className="portfolio-management-account-list">
      {accounts.map((account) => {
        const metrics = account.metrics;
        const isPending = pending.startsWith(`${account.account_key}:`);
        const riskState = String(account.continuous_risk?.state || "");
        const activeGroups = (account.managed_order_groups ?? []).filter((row) => !["filled", "cancelled", "rejected", "policy_blocked"].includes(String(row.state.state || "")));
        const protectionDeficits = activeGroups.filter((row) =>
          Number(row.state.protection_required_quantity || 0) > Number(row.state.protection_coverage_quantity || 0));
        return <article className="portfolio-management-account" data-sync={account.sync_state} key={account.account_key}>
          <header>
            <div><strong>{account.account_key}</strong><span>{account.account_class} · {String(account.policy.identity || "unversioned policy")}</span></div>
            <div className="portfolio-management-status"><span data-state={account.sync_state}>{labelFor(account.sync_state)}</span><span data-state={account.control_mode}>{labelFor(account.control_mode)}</span>{riskState ? <span data-state={riskState}>{labelFor(riskState)}</span> : null}</div>
          </header>
          <div className="portfolio-management-metrics">
            <TradingMetric label="Eligible equity" value={money(metrics.eligible_equity)} />
            <TradingMetric label="Gross headroom" value={money(metrics.gross_headroom)} tone="positive" />
            <TradingMetric label="Reserved" value={money(metrics.reserved_notional)} />
            <TradingMetric label="Risk headroom" value={money(metrics.planned_risk_headroom)} />
            <TradingMetric label="Positions" value={String(account.position_count)} />
            <TradingMetric label="Working orders" value={String(account.working_order_count)} />
            <TradingMetric label="Daily loss" value={money(metrics.daily_loss || 0)} tone={Number(metrics.daily_loss || 0) > 0 ? "negative" : undefined} />
            <TradingMetric label="Drawdown" value={money(metrics.drawdown || 0)} tone={Number(metrics.drawdown || 0) > 0 ? "negative" : undefined} />
          </div>
          <div className="portfolio-management-evidence">
            <span>{(account.reservations ?? []).length} reservations</span>
            <span>{(account.allocations ?? []).length} allocations</span>
            <span data-tone={(account.reconciliation ?? []).length ? "negative" : "positive"}>{(account.reconciliation ?? []).length} reconciliation differences</span>
            <span data-tone={protectionDeficits.length ? "negative" : "positive"}>{protectionDeficits.length ? `${protectionDeficits.length} protection deficits` : "Protection reconciled"}</span>
            <span>{activeGroups.length} managed order groups</span>
            <span>{(account.pending_operational_commands ?? []).filter((row) => row.status === "pending").length} pending operator commands</span>
            <span>{account.observed_at ? <>As of <MarketTime value={account.observed_at} /></> : "No broker watermark"}</span>
          </div>
          {operational ? <div className="portfolio-management-controls">
            <label className="portfolio-policy-select">
              <span>Policy revision</span>
              <select
                aria-label={`Policy revision for ${account.account_key}`}
                disabled={isPending}
                onChange={(event) => void command(account.account_key, "select_policy", { policy_identity: event.target.value })}
                value={String(account.policy.identity || "")}
              >
                {(account.available_policies ?? []).map((policy) => <option key={policy.identity} value={policy.identity}>{policy.identity}</option>)}
              </select>
            </label>
            {account.control_mode === "enabled"
              ? <button className="button secondary compact" disabled={isPending} onClick={() => void command(account.account_key, "pause_entries")} type="button">Pause entries</button>
              : <button className="button secondary compact" disabled={isPending} onClick={() => void command(account.account_key, "resume_entries")} type="button">Resume entries</button>}
            <button className="button secondary compact" disabled={isPending || account.control_mode === "reduce_only"} onClick={() => void command(account.account_key, "reduce_only")} type="button">Reduce only</button>
            <button className="button secondary compact" disabled={isPending} onClick={() => void command(account.account_key, "reconcile")} type="button"><RefreshCcw size={12} /> Reconcile</button>
            <button className="button secondary compact" data-tone="negative" disabled={isPending} onClick={() => void command(account.account_key, "kill_entries")} type="button">Kill entries</button>
            <button
              className="button secondary compact"
              data-tone="negative"
              disabled={isPending}
              onClick={() => {
                if (window.confirm(`Emergency flatten ${account.account_key}? This queues bounded liquidation for every confirmed position in the account.`)) {
                  void command(account.account_key, "emergency_flatten");
                }
              }}
              type="button"
            >
              Emergency flatten
            </button>
            {Object.entries(account.strategy_allocations).map(([strategyId, fraction]) => {
              const disabled = (account.disabled_strategy_allocations ?? []).includes(strategyId);
              return <button
                className="button secondary compact portfolio-strategy-control"
                data-disabled={disabled || undefined}
                disabled={isPending}
                key={strategyId}
                onClick={() => void command(account.account_key, disabled ? "enable_strategy" : "disable_strategy", { strategy_id: strategyId })}
                title={`${disabled ? "Enable" : "Disable"} ${strategyId} entries for this account`}
                type="button"
              >
                {strategyId} {Math.round(Number(fraction) * 100)}% · {disabled ? "Disabled" : "Enabled"}
              </button>;
            })}
          </div> : <div className="trading-disclosure">Replay and Backtest use the same policy evidence with a simulated broker; operational controls are available only in Live and Paper.</div>}
          {activeGroups.length ? <details className="trading-disclosure">
            <summary>Adaptive execution and protection evidence</summary>
            <PreviewTable
              columns={["ticker", "state", "execution_policy", "protection_profile", "current_limit", "protection"]}
              rows={activeGroups.map((row) => ({
                ticker: String(row.state.intent?.ticker || ""),
                state: String(row.state.state || ""),
                execution_policy: String((row.state.intent?.execution_policy as PreviewRow | undefined)?.policy_id || "legacy"),
                protection_profile: String((row.state.intent?.protection_profile as PreviewRow | undefined)?.profile_id || "legacy"),
                current_limit: row.state.current_limit_price ?? "",
                protection: `${Number(row.state.protection_coverage_quantity || 0)} / ${Number(row.state.protection_required_quantity || 0)}`,
              }))}
            />
          </details> : null}
        </article>;
      })}
    </div>
    {management.groups.length ? <><div className="trading-secondary-heading"><strong>Aggregate groups</strong><span>Cross-account caps without implicit routing or mirrored orders</span></div><PreviewTable columns={["group_id", "gross_exposure", "gross_headroom", "sync_state"]} rows={management.groups} /></> : null}
  </section>;
}

function PositionsPreview({ data, onSymbolSelect, settings }: { data: CanonicalTradingPreview; onSymbolSelect?: (symbol: string) => void; settings: ContainerSettings["positions"] }) {
  const [view, setView] = useState<"open" | "closed" | "timeline">("open");
  const openRows = data.positions.map((row) => {
    const symbol = nestedValue(row, "instrument", "symbol");
    const account = String(row.account_id || "");
    const quantity = Number(row.quantity || 0);
    const averagePrice = Number(row.average_price || 0);
    const mark = Number(row.market_price || 0);
    const returnPct = averagePrice > 0 ? ((mark - averagePrice) / averagePrice) * 100 * (quantity < 0 ? -1 : 1) : 0;
    const relatedOrders = data.orders.filter((order) => String(order.account_id || "") === account && nestedValue(order, "instrument", "symbol") === symbol && !terminalOrderState(String(order.lifecycle_state || "")));
    const relatedExecutions = data.executions.filter((execution) => String(execution.account_id || "") === account && nestedValue(execution, "instrument", "symbol") === symbol);
    return { account, symbol, side: quantity > 0 ? "Long" : quantity < 0 ? "Short" : "Flat", quantity, average_price: row.average_price, mark: row.market_price, return_pct: returnPct, market_value: row.market_value, unrealized_pnl: row.unrealized_pnl, realized_pnl: row.realized_pnl, working_orders: relatedOrders.length, fills: relatedExecutions.length, updated_at: row.source_event_time, _position: row, _orders: relatedOrders, _executions: relatedExecutions };
  }).filter((row) => row.quantity !== 0);
  const closedRows = data.closed_trades.map((row) => ({ closed_at: row.closed_at, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, quantity: row.quantity, entry_price: row.entry_price, exit_price: row.exit_price, gross_pnl: row.gross_pnl, fees: row.fees, net_pnl: row.net_pnl, account: row.account_id, _trade: row }));
  const timelineRows = data.activity.filter((row) => ["position_observed", "position_snapshot_completed", "execution_reported", "commission_reported"].includes(String(row.event_type || ""))).map((row) => ({ time: row.source_event_time, event: row.event_type, account: row.account_id, order_id: row.broker_order_id, execution_id: row.execution_id, provider: row.provider }));
  const netPnl = openRows.reduce((total, row) => total + Number(row.unrealized_pnl || 0), 0);
  const grossValue = openRows.reduce((total, row) => total + Math.abs(Number(row.market_value || 0)), 0);
  const winners = openRows.filter((row) => Number(row.unrealized_pnl || 0) > 0).length;
  const openColumns = settings.showPnl ? ["symbol", "side", "quantity", "average_price", "mark", "return_pct", "market_value", "unrealized_pnl", "working_orders", "fills", "account", "updated_at"] : ["symbol", "side", "quantity", "average_price", "mark", "market_value", "working_orders", "fills", "account", "updated_at"];
  return <section className="trading-preview trading-position-manager"><TradingFreshness data={data} />
    <div className="trading-summary-strip"><TradingMetric label="Open positions" value={String(openRows.length)} /><TradingMetric label="Winning" value={`${winners}/${openRows.length}`} tone={winners ? "positive" : "neutral"} /><TradingMetric label="Open P&L" value={signedMoney(netPnl)} tone={numberTone(netPnl)} /><TradingMetric label="Gross exposure" value={money(grossValue)} /></div>
    <TradingTabs active={view} onChange={(value) => setView(value as typeof view)} tabs={[{ id: "open", label: "Open", count: openRows.length }, { id: "closed", label: "Closed", count: closedRows.length }, { id: "timeline", label: "Timeline", count: timelineRows.length }]} />
    {view === "open" ? <TradingDataTable columns={openColumns} defaultSort="market_value" filterColumn="side" filterLabel="All directions" onSymbolSelect={onSymbolSelect} renderExpanded={(row) => <PositionDetail row={row} />} rows={openRows.slice(0, settings.limit)} searchPlaceholder="Search symbol, account, side…" /> : null}
    {view === "closed" ? <><div className="trading-disclosure">{data.closed_trades_note}</div><TradingDataTable columns={settings.showPnl ? ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl", "account"] : ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "account"]} defaultSort="closed_at" filterColumn="side" filterLabel="All directions" onSymbolSelect={onSymbolSelect} rows={closedRows.slice(0, settings.limit)} searchPlaceholder="Search closed positions…" /></> : null}
    {view === "timeline" ? <TradingDataTable columns={["time", "event", "account", "order_id", "execution_id", "provider"]} defaultSort="time" filterColumn="event" filterLabel="All events" rows={timelineRows.slice(0, settings.limit)} searchPlaceholder="Search position history…" /> : null}
  </section>;
}

function PositionDetail({ row }: { row: PreviewRow }) {
  const orders = (row._orders as PreviewRow[] | undefined) ?? [];
  const executions = (row._executions as PreviewRow[] | undefined) ?? [];
  const position = (row._position as PreviewRow | undefined) ?? {};
  const orderRows = orders.map(orderTableRow);
  const executionRows = executions.map(executionTableRow);
  return <div className="trading-row-detail"><div className="trading-detail-facts"><span><small>Contract</small><strong>{String(nestedValue(position, "instrument", "conid") || "—")}</strong></span><span><small>Asset / currency</small><strong>{String(nestedValue(position, "instrument", "security_type") || "—")} · {String(nestedValue(position, "instrument", "currency") || "—")}</strong></span><span><small>Model</small><strong>{String(position.model || "Default")}</strong></span><span><small>Snapshot</small><strong>{String(position.snapshot_id || "—")}</strong></span></div><div className="trading-related-grid"><section><header><strong>Working orders</strong><span>{orders.length}</span></header>{orders.length ? <PreviewTable columns={["status", "side", "remaining", "type", "limit", "stop", "order_id"]} rows={orderRows} /> : <p>No working orders for this position.</p>}</section><section><header><strong>Recent fills</strong><span>{executions.length}</span></header>{executions.length ? <PreviewTable columns={["time", "side", "quantity", "price", "exchange", "commission"]} rows={executionRows} /> : <p>No execution evidence in the loaded window.</p>}</section></div></div>;
}

function OrdersPreview({ data, onSymbolSelect, settings }: { data: CanonicalTradingPreview; onSymbolSelect?: (symbol: string) => void; settings: ContainerSettings["orders"] }) {
  const [view, setView] = useState<"working" | "all" | "fills">("working");
  const orderRows: PreviewRow[] = data.orders.map((row) => ({ ...orderTableRow(row), _order: row, _executions: data.executions.filter((execution) => String(execution.account_id || "") === String(row.account_id || "") && String(execution.broker_order_id || "") === String(row.broker_order_id || "")) }));
  const workingRows = orderRows.filter((row) => !terminalOrderState(String(row.status || "")));
  const executionRows = data.executions.map(executionTableRow);
  const filledCount = orderRows.filter((row) => String(row.status) === "filled").length;
  const rejectedCount = orderRows.filter((row) => String(row.status) === "rejected").length;
  const columns = settings.showOrderIds ? ["status", "broker_status", "symbol", "side", "progress", "remaining", "type", "limit", "stop", "tif", "account", "order_id", "updated_at"] : ["status", "symbol", "side", "progress", "remaining", "type", "limit", "stop", "tif", "account", "updated_at"];
  const activeRows = view === "working" ? workingRows : orderRows;
  return <section className="trading-preview trading-order-manager"><TradingFreshness data={data} />
    <div className="trading-summary-strip"><TradingMetric label="Working" value={String(workingRows.length)} tone={workingRows.length ? "primary" : "neutral"} /><TradingMetric label="Filled" value={String(filledCount)} tone={filledCount ? "positive" : "neutral"} /><TradingMetric label="Rejected" value={String(rejectedCount)} tone={rejectedCount ? "negative" : "neutral"} /><TradingMetric label="Executions" value={String(executionRows.length)} /></div>
    <TradingTabs active={view} onChange={(value) => setView(value as typeof view)} tabs={[{ id: "working", label: "Working", count: workingRows.length }, { id: "all", label: "All orders", count: orderRows.length }, { id: "fills", label: "Fills", count: executionRows.length }]} />
    {view !== "fills" ? <TradingDataTable columns={columns} defaultSort="updated_at" filterColumn="status" filterLabel="All statuses" onSymbolSelect={onSymbolSelect} renderExpanded={(row) => <OrderDetail row={row} />} rows={activeRows.slice(0, settings.limit)} searchPlaceholder="Search orders, symbols, IDs…" /> : <TradingDataTable columns={["time", "symbol", "side", "quantity", "price", "exchange", "commission", "fee_state", "account", "order_id", "execution_id"]} defaultSort="time" filterColumn="side" filterLabel="All sides" onSymbolSelect={onSymbolSelect} rows={executionRows.slice(0, settings.limit)} searchPlaceholder="Search fills, venues, order IDs…" />}
  </section>;
}

function OrderDetail({ row }: { row: PreviewRow }) {
  const order = (row._order as PreviewRow | undefined) ?? {};
  const executions = ((row._executions as PreviewRow[] | undefined) ?? []).map(executionTableRow);
  return <div className="trading-row-detail"><div className="trading-detail-facts"><span><small>Client order</small><strong>{String(order.client_order_id || "—")}</strong></span><span><small>Command</small><strong>{String(order.command_id || "—")}</strong></span><span><small>Parent</small><strong>{String(order.parent_order_id || "—")}</strong></span><span><small>Broker message</small><strong>{String(order.warning || order.rejection_reason || "None")}</strong></span></div><section className="trading-fill-evidence"><header><strong>Execution evidence</strong><span>{executions.length} fill{executions.length === 1 ? "" : "s"}</span></header>{executions.length ? <PreviewTable columns={["time", "execution_id", "side", "quantity", "price", "exchange", "commission", "fee_state"]} rows={executions} /> : <p>This order has no fills in the loaded execution window.</p>}</section></div>;
}

function ExecutionsPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["fills"] }) {
  const rows = data.executions.map(executionTableRow);
  const columns = settings.showCommission ? ["time", "symbol", "side", "quantity", "price", "exchange", "commission", "fee_state", "net_amount", "account", "order_id", "execution_id"] : ["time", "symbol", "side", "quantity", "price", "exchange", "account", "order_id", "execution_id"];
  return <section className="trading-preview"><TradingFreshness data={data} /><div className="trading-disclosure">Advanced immutable execution audit. For routine management, use Orders &amp; Fills where each order expands into its related executions.</div><TradingDataTable columns={columns} defaultSort="time" filterColumn="side" filterLabel="All sides" rows={rows.slice(0, settings.limit)} searchPlaceholder="Search immutable execution evidence…" /></section>;
}

function ClosedTradesPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["closed_trades"] }) {
  const rows = data.closed_trades.map((row) => ({ closed_at: row.closed_at, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, quantity: row.quantity, entry_price: row.entry_price, exit_price: row.exit_price, gross_pnl: row.gross_pnl, fees: row.fees, net_pnl: row.net_pnl, account: row.account_id }));
  const columns = settings.showFees ? ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl", "account"] : ["closed_at", "symbol", "side", "quantity", "entry_price", "exit_price", "gross_pnl", "net_pnl", "account"];
  return <section className="trading-preview"><div className="trading-disclosure">Advanced derived round-trip audit. The Position Manager provides the normal open, closed, and lifecycle workflow. {data.closed_trades_note}</div><TradingDataTable columns={columns} defaultSort="closed_at" filterColumn="side" filterLabel="All sides" rows={rows.slice(0, settings.limit)} searchPlaceholder="Search derived round trips…" /></section>;
}

function TradingTabs({ active, onChange, tabs }: { active: string; onChange: (id: string) => void; tabs: Array<{ count: number; id: string; label: string }> }) {
  return <div aria-label="Trading view" className="trading-view-tabs" role="tablist">{tabs.map((tab) => <button aria-selected={active === tab.id} className={active === tab.id ? "active" : undefined} key={tab.id} onClick={() => onChange(tab.id)} role="tab" type="button"><span>{tab.label}</span><strong>{tab.count}</strong></button>)}</div>;
}

function orderTableRow(row: PreviewRow): PreviewRow {
  const filled = Number(row.filled_quantity || 0);
  const total = Number(row.total_quantity || 0);
  return { status: row.lifecycle_state, broker_status: row.broker_status_raw, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, progress: `${filled}/${total}`, filled, total, remaining: row.remaining_quantity, type: row.order_type, limit: row.limit_price, stop: row.stop_price, tif: row.time_in_force, account: row.account_id, order_id: row.broker_order_id, client_id: row.client_order_id, updated_at: row.source_event_time };
}

function executionTableRow(row: PreviewRow): PreviewRow {
  return { time: row.source_event_time, execution_id: row.execution_id, symbol: nestedValue(row, "instrument", "symbol"), side: row.side, quantity: row.quantity, price: row.price, exchange: row.exchange, commission: row.commission, fee_state: row.commission_status, net_amount: row.net_amount, account: row.account_id, order_id: row.broker_order_id };
}

function terminalOrderState(status: string) { return ["filled", "cancelled", "rejected", "expired", "inactive"].includes(status.toLowerCase()); }

function ActivityPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["activity"] }) {
  const rows = data.activity.map((row) => ({ time: row.source_event_time, event: row.event_type, account: row.account_id, order_id: row.broker_order_id, client_id: row.client_order_id, execution_id: row.execution_id, provider: row.provider, correlation: row.correlation_id }));
  return <section className="trading-preview"><TradingFreshness data={data} /><PreviewTable columns={["time", "event", "account", "order_id", "client_id", "execution_id", "provider", "correlation"]} rows={rows.slice(0, settings.limit)} /></section>;
}

function TradingJournalPreview({ data, settings }: { data: CanonicalTradingPreview; settings: ContainerSettings["performance_journal"] }) {
  const [view, setView] = useState<"overview" | "strategies" | "trades" | "execution" | "risk">("overview");
  const [pnlTimeframe, setPnlTimeframe] = useState<PnlCandleTimeframe>("30m");
  const [guideOpen, setGuideOpen] = useState(false);
  const report = data.performance_journal;
  const summary = report?.summary ?? {};
  const scope = report?.scope ?? {};
  const risk = report?.risk ?? {};
  const execution = report?.execution ?? {};
  const episodes = (report?.episodes ?? []).slice(0, settings.limit).map((row) => ({
    closed_at: row.closed_at,
    symbol: nestedValue(row, "instrument", "symbol"),
    side: row.side,
    strategy: row.strategy_id || "Unattributed",
    revision: Number(row.strategy_revision || 0) ? `v${row.strategy_revision}` : "—",
    setup: row.setup || "—",
    quantity: row.quantity,
    entry_price: row.entry_price,
    exit_price: row.exit_price,
    net_pnl: row.net_pnl,
    risk_multiple: row.risk_multiple,
    duration: compactDuration(Number(row.duration_seconds || 0)),
    exit_reason: row.exit_reason || "—",
    _episode: row,
  }));
  const strategyRows = (report?.strategies ?? []).map((row) => ({
    strategy: row.strategy_id,
    revision: Number(row.strategy_revision || 0) ? `v${row.strategy_revision}` : "—",
    trades: row.episode_count,
    net_pnl: row.net_pnl,
    win_rate_pct: ratioPct(row.win_rate),
    expectancy: row.expectancy,
    profit_factor: row.profit_factor,
    payoff_ratio: row.payoff_ratio,
    max_drawdown: row.maximum_drawdown,
  }));
  const tabs = [
    { id: "overview", label: "Overview", count: Number(summary.episode_count || 0) },
    { id: "strategies", label: "Strategies", count: strategyRows.length },
    { id: "trades", label: "Trades", count: episodes.length },
    { id: "execution", label: "Execution", count: Number(execution.fill_count || 0) },
    { id: "risk", label: "Risk", count: Number(summary.loss_count || 0) },
  ];
  if (!report) return <section className="trading-preview"><TradingFreshness data={data} /><EmptyState label="Performance journal is unavailable for this trading state" /></section>;
  return <section className="trading-preview performance-journal">
    <header className="performance-journal-header">
      <div><span>Decision record</span><strong>Trading performance</strong><small>Flat-to-flat episodes · net of available fees</small></div>
      <div className="performance-journal-scope"><span>{Number(scope.episode_count || 0)} episodes</span><span>{ratioPct(scope.attribution_coverage)} attributed</span><button onClick={() => setGuideOpen(true)} type="button"><HelpCircle size={14} /> Guide</button></div>
    </header>
    <TradingFreshness data={data} />
    <div className="performance-kpi-grid">
      <JournalMetric detail="Closed episode profit after recorded commissions and fees." label="Net P&L" tone={numberTone(summary.net_pnl)} value={signedMoney(summary.net_pnl)} />
      <JournalMetric detail="Average expected dollars per closed trade episode." label="Expectancy" tone={numberTone(summary.expectancy)} value={signedMoney(summary.expectancy)} />
      <JournalMetric detail="Gross winning dollars divided by gross losing dollars." label="Profit factor" tone={metricThresholdTone(summary.profit_factor, 1)} value={ratioNumber(summary.profit_factor)} />
      <JournalMetric detail="Winning episodes divided by all closed episodes." label="Win rate" tone={metricThresholdTone(summary.win_rate, 0.5)} value={ratioPct(summary.win_rate)} />
      <JournalMetric detail="Average winning episode divided by average losing episode." label="Payoff" tone={metricThresholdTone(summary.payoff_ratio, 1)} value={ratioNumber(summary.payoff_ratio)} />
      <JournalMetric detail="Largest peak-to-trough decline in cumulative closed P&L." label="Max drawdown" tone={Number(summary.maximum_drawdown || 0) > 0 ? "negative" : "neutral"} value={money(summary.maximum_drawdown)} />
    </div>
    <TradingTabs active={view} onChange={(value) => setView(value as typeof view)} tabs={tabs} />
    {view === "overview" ? <div className="performance-overview-stack"><div className="performance-overview-grid"><section className="performance-chart-card"><header><div><strong>Net P&L trajectory</strong><span>Cumulative closed-episode P&L</span></div><b data-tone={numberTone(summary.net_pnl)}>{signedMoney(summary.net_pnl)}</b></header><JournalAreaChart rows={report.equity_curve} /></section><section className="performance-diagnosis"><header><strong>Edge snapshot</strong><span>Read together, never from win rate alone</span></header><div><JournalFact label="Average win" tone="positive" value={money(summary.average_win)} /><JournalFact label="Average loss" tone="negative" value={money(summary.average_loss)} /><JournalFact label="Largest win" tone="positive" value={money(summary.largest_win)} /><JournalFact label="Largest loss" tone="negative" value={money(summary.largest_loss)} /><JournalFact label="Average hold" value={compactDuration(Number(summary.average_duration_seconds || 0))} /><JournalFact label="Fees" tone={Number(summary.total_fees || 0) > 0 ? "negative" : "neutral"} value={money(summary.total_fees)} /></div></section></div><JournalPnlCandleChart candles={report.pnl_candles?.[pnlTimeframe] ?? []} onTimeframeChange={setPnlTimeframe} timeframe={pnlTimeframe} /></div> : null}
    {view === "strategies" ? <div className="performance-strategy-view"><StrategyComparisonChart rows={strategyRows} /><TradingDataTable columns={["strategy", "revision", "trades", "net_pnl", "win_rate_pct", "expectancy", "profit_factor", "payoff_ratio", "max_drawdown"]} defaultSort="net_pnl" filterColumn="strategy" filterLabel="All strategies" rows={strategyRows} searchPlaceholder="Search strategies and revisions…" /></div> : null}
    {view === "trades" ? <TradingDataTable columns={settings.showRiskMultiple ? ["closed_at", "symbol", "side", "strategy", "revision", "setup", "quantity", "entry_price", "exit_price", "net_pnl", "risk_multiple", "duration", "exit_reason"] : ["closed_at", "symbol", "side", "strategy", "revision", "setup", "quantity", "entry_price", "exit_price", "net_pnl", "duration", "exit_reason"]} defaultSort="closed_at" filterColumn="strategy" filterLabel="All strategies" renderExpanded={(row) => <JournalEpisodeDetail row={row} />} rows={episodes} searchPlaceholder="Search trades, symbols, setups, exits…" /> : null}
    {view === "execution" ? <ExecutionJournalView execution={execution} /> : null}
    {view === "risk" ? <RiskJournalView risk={risk} summary={summary} /> : null}
    {guideOpen ? <TradingJournalGuide onClose={() => setGuideOpen(false)} /> : null}
  </section>;
}

function JournalMetric({ detail, label, tone, value }: { detail: string; label: string; tone: "negative" | "neutral" | "positive"; value: string }) {
  return <div className={`journal-metric tone-${tone}`} title={detail}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function JournalFact({ label, tone = "neutral", value }: { label: string; tone?: "negative" | "neutral" | "positive"; value: string }) {
  return <span className={`journal-fact tone-${tone}`}><small>{label}</small><strong>{value}</strong></span>;
}

function JournalAreaChart({ rows }: { rows: Array<{ time: string; value: string | number; drawdown: string | number }> }) {
  if (!rows.length) return <EmptyState label="Close at least one flat-to-flat episode to build the performance curve" />;
  const values = rows.map((row) => Number(row.value || 0));
  const { maximum, minimum, ticks } = journalChartDomain(values, true);
  const plot = { bottom: 132, left: 52, right: 424, top: 14 };
  const x = (index: number) => rows.length === 1 ? (plot.left + plot.right) / 2 : plot.left + (index / (rows.length - 1)) * (plot.right - plot.left);
  const y = (value: number) => plot.top + ((maximum - value) / (maximum - minimum)) * (plot.bottom - plot.top);
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const zeroY = y(0);
  const area = `${x(0)},${zeroY} ${points} ${x(rows.length - 1)},${zeroY}`;
  const lineColor = values[values.length - 1] >= 0 ? "var(--success)" : "var(--danger)";
  return <svg aria-label="Cumulative net profit and loss with dollar axis" className="journal-area-chart" preserveAspectRatio="none" role="img" viewBox="0 0 440 154"><defs><linearGradient id="journal-equity-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={lineColor} stopOpacity="0.28" /><stop offset="1" stopColor={lineColor} stopOpacity="0.02" /></linearGradient></defs>{ticks.map((tick) => <g className="journal-chart-grid" key={tick}><line x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} /><text textAnchor="end" x={plot.left - 7} y={y(tick) + 3}>{formatMoneyAxis(tick)}</text></g>)}<line className="journal-chart-zero" x1={plot.left} x2={plot.right} y1={zeroY} y2={zeroY} /><polygon fill="url(#journal-equity-fill)" points={area} /><polyline fill="none" points={points} stroke={lineColor} strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" /><text x={plot.left} y="151">{formatJournalDate(rows[0].time)}</text><text textAnchor="end" x={plot.right} y="151">{formatJournalDate(rows[rows.length - 1].time)}</text></svg>;
}

function JournalPnlCandleChart({ candles, onTimeframeChange, timeframe }: { candles: PnlCandle[]; onTimeframeChange: (value: PnlCandleTimeframe) => void; timeframe: PnlCandleTimeframe }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const rows = candles.slice(-120);
  const selectedIndex = hoveredIndex !== null && hoveredIndex < rows.length ? hoveredIndex : rows.length - 1;
  const selected = rows[selectedIndex];
  const values = rows.flatMap((row) => [Number(row.low), Number(row.high)]);
  const { maximum, minimum, ticks } = journalChartDomain(values, false);
  const plot = { bottom: 204, left: 58, right: 782, top: 20 };
  const times = rows.map((row) => new Date(row.bucket_start).getTime());
  const firstTime = times.length ? Math.min(...times) : 0;
  const lastTime = times.length ? Math.max(...times) : firstTime;
  const x = (index: number) => rows.length === 1 ? (plot.left + plot.right) / 2 : plot.left + ((times[index] - firstTime) / Math.max(1, lastTime - firstTime)) * (plot.right - plot.left);
  const y = (value: number) => plot.top + ((maximum - value) / (maximum - minimum)) * (plot.bottom - plot.top);
  const bodyWidth = Math.max(4, Math.min(14, (plot.right - plot.left) / Math.max(8, rows.length * 1.8)));
  const timeframes: Array<{ id: PnlCandleTimeframe; label: string; title: string }> = [{ id: "30m", label: "30m", title: "30 minutes" }, { id: "1h", label: "1h", title: "1 hour" }, { id: "1d", label: "1D", title: "1 day" }, { id: "1M", label: "1M", title: "1 month" }];
  function selectTimeframe(value: PnlCandleTimeframe) {
    setHoveredIndex(null);
    onTimeframeChange(value);
  }
  return <section className="performance-candle-card"><header><div><strong>Realized P&L candles</strong><span>Cumulative net P&L OHLC after each closed trade episode</span></div><div aria-label="P&L candle timeframe" className="journal-timeframe-tabs" role="group">{timeframes.map((option) => <button aria-pressed={timeframe === option.id} className={timeframe === option.id ? "is-active" : undefined} key={option.id} onClick={() => selectTimeframe(option.id)} title={option.title} type="button">{option.label}</button>)}</div></header>{selected ? <div className="journal-candle-readout"><span>{formatPnlCandleTime(selected.bucket_start, timeframe)}</span><span>O <b>{money(selected.open)}</b></span><span>H <b>{money(selected.high)}</b></span><span>L <b>{money(selected.low)}</b></span><span>C <b data-tone={numberTone(selected.close)}>{money(selected.close)}</b></span><span>Change <b data-tone={numberTone(selected.net_change)}>{signedMoney(selected.net_change)}</b></span><span>{selected.episode_count} {selected.episode_count === 1 ? "episode" : "episodes"}</span></div> : null}{rows.length ? <div className="journal-candle-scroll"><svg aria-label={`${timeframe} cumulative realized profit and loss candles`} className="journal-candle-chart" onMouseLeave={() => setHoveredIndex(null)} preserveAspectRatio="none" role="img" style={{ minWidth: `${Math.max(700, rows.length * 8)}px` }} viewBox="0 0 800 232">{ticks.map((tick) => <g className="journal-chart-grid" key={tick}><line x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} /><text textAnchor="end" x={plot.left - 8} y={y(tick) + 3}>{formatMoneyAxis(tick)}</text></g>)}{rows.map((row, index) => { const open = Number(row.open); const close = Number(row.close); const high = Number(row.high); const low = Number(row.low); const up = close >= open; const center = x(index); const bodyTop = Math.min(y(open), y(close)); const bodyHeight = Math.max(2, Math.abs(y(open) - y(close))); return <g aria-label={`${formatPnlCandleTime(row.bucket_start, timeframe)} open ${money(open)}, high ${money(high)}, low ${money(low)}, close ${money(close)}`} className={`${up ? "is-up" : "is-down"}${selectedIndex === index ? " is-selected" : ""}`} key={row.bucket_start} onFocus={() => setHoveredIndex(index)} onMouseEnter={() => setHoveredIndex(index)} role="img" tabIndex={0}><line className="journal-candle-wick" x1={center} x2={center} y1={y(high)} y2={y(low)} /><rect className="journal-candle-body" height={bodyHeight} width={bodyWidth} x={center - bodyWidth / 2} y={bodyTop} /></g>; })}{rows.length === 1 ? <text textAnchor="middle" x={(plot.left + plot.right) / 2} y="226">{formatPnlCandleTime(rows[0].bucket_start, timeframe)}</text> : <><text x={plot.left} y="226">{formatPnlCandleTime(rows[0].bucket_start, timeframe)}</text>{rows.length > 2 ? <text textAnchor="middle" x={(plot.left + plot.right) / 2} y="226">{formatPnlCandleTime(rows[Math.floor(rows.length / 2)].bucket_start, timeframe)}</text> : null}<text textAnchor="end" x={plot.right} y="226">{formatPnlCandleTime(rows[rows.length - 1].bucket_start, timeframe)}</text></>}</svg></div> : <EmptyState label={`No closed episodes are available for ${timeframe} P&L candles`} />}</section>;
}

function journalChartDomain(values: number[], includeZero: boolean) {
  const finite = values.filter(Number.isFinite);
  const rawMinimum = finite.length ? Math.min(...finite, ...(includeZero ? [0] : [])) : 0;
  const rawMaximum = finite.length ? Math.max(...finite, ...(includeZero ? [0] : [])) : 1;
  const rawSpan = rawMaximum - rawMinimum || Math.max(1, Math.abs(rawMaximum) * 0.1);
  const minimum = rawMinimum - rawSpan * 0.08;
  const maximum = rawMaximum + rawSpan * 0.08;
  return { maximum, minimum, ticks: Array.from({ length: 5 }, (_, index) => maximum - ((maximum - minimum) * index) / 4) };
}

function StrategyComparisonChart({ rows }: { rows: PreviewRow[] }) {
  if (!rows.length) return <EmptyState label="No attributed or unattributed strategy episodes in this scope" />;
  const maximum = Math.max(1, ...rows.map((row) => Math.abs(Number(row.net_pnl || 0))));
  return <section className="strategy-comparison-chart"><header><strong>Net result by strategy revision</strong><span>Width is relative net P&L; use expectancy and sample size before ranking.</span></header>{rows.slice(0, 8).map((row) => { const value = Number(row.net_pnl || 0); return <div key={`${row.strategy}-${row.revision}`}><span>{String(row.strategy)} <small>{String(row.revision)}</small></span><i><b data-tone={numberTone(value)} style={{ width: `${Math.max(2, Math.abs(value) / maximum * 100)}%` }} /></i><strong data-tone={numberTone(value)}>{signedMoney(value)}</strong></div>; })}</section>;
}

function JournalEpisodeDetail({ row }: { row: PreviewRow }) {
  const episode = (row._episode as PreviewRow | undefined) ?? {};
  const episodeId = String(episode.episode_id || "");
  const [annotation, setAnnotation] = useState({ note: "", tags: [] as string[], review_status: "unreviewed", setup_override: "" });
  const [annotationState, setAnnotationState] = useState<"idle" | "loading" | "saving" | "saved" | "error">("loading");
  useEffect(() => {
    let active = true;
    setAnnotationState("loading");
    api<{ note?: string; tags?: string[]; review_status?: string; setup_override?: string }>(`/api/trading/journal/episodes/${encodeURIComponent(episodeId)}/annotation`)
      .then((payload) => { if (active) { setAnnotation({ note: payload.note ?? "", tags: payload.tags ?? [], review_status: payload.review_status ?? "unreviewed", setup_override: payload.setup_override ?? "" }); setAnnotationState("idle"); } })
      .catch(() => { if (active) setAnnotationState("error"); });
    return () => { active = false; };
  }, [episodeId]);
  async function saveAnnotation() {
    setAnnotationState("saving");
    try {
      const saved = await api<typeof annotation>(`/api/trading/journal/episodes/${encodeURIComponent(episodeId)}/annotation`, { method: "PUT", body: JSON.stringify(annotation) });
      setAnnotation(saved);
      setAnnotationState("saved");
    } catch { setAnnotationState("error"); }
  }
  return <div className="trading-row-detail journal-episode-detail"><div className="trading-detail-facts"><span><small>Episode ID</small><strong>{episodeId || "—"}</strong></span><span><small>Run</small><strong>{String(episode.run_id || "Unattributed")}</strong></span><span><small>Execution IDs</small><strong>{Array.isArray(episode.execution_ids) ? episode.execution_ids.join(", ") : "—"}</strong></span><span><small>Order IDs</small><strong>{Array.isArray(episode.order_ids) ? episode.order_ids.join(", ") : "—"}</strong></span></div><p>One episode begins when the position leaves flat and ends when it returns to flat. Scale-ins and partial exits remain one strategy decision.</p><section className="journal-review-editor"><header><div><strong>Review record</strong><span>Stored durably against this deterministic episode ID</span></div><em data-state={annotationState}>{annotationState === "loading" ? "Loading…" : annotationState === "saving" ? "Saving…" : annotationState === "saved" ? "Saved" : annotationState === "error" ? "Could not save" : "Ready"}</em></header><div><label><span>Status</span><select onChange={(event) => setAnnotation((current) => ({ ...current, review_status: event.target.value }))} value={annotation.review_status}><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="follow_up">Follow up</option></select></label><label><span>Setup override</span><input onChange={(event) => setAnnotation((current) => ({ ...current, setup_override: event.target.value }))} placeholder={String(episode.setup || "Optional reviewed setup")} value={annotation.setup_override} /></label><label className="journal-review-tags"><span>Tags</span><input onChange={(event) => setAnnotation((current) => ({ ...current, tags: event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean) }))} placeholder="A+, followed plan, late entry" value={annotation.tags.join(", ")} /></label><label className="journal-review-note"><span>Review note</span><textarea onChange={(event) => setAnnotation((current) => ({ ...current, note: event.target.value }))} placeholder="What was planned, what happened, and what should be repeated or changed?" value={annotation.note} /></label></div><button disabled={!episodeId || annotationState === "saving" || annotationState === "loading"} onClick={saveAnnotation} type="button"><Save size={13} /> Save review</button></section></div>;
}

function ExecutionJournalView({ execution }: { execution: Record<string, unknown> }) {
  const venues = (execution.venues as PreviewRow[] | undefined) ?? [];
  return <div className="execution-journal-view"><div className="trading-summary-strip"><TradingMetric label="Fill notional" value={money(execution.fill_notional)} tone="primary" /><TradingMetric label="Recorded fees" value={money(execution.total_fees)} tone={Number(execution.total_fees || 0) > 0 ? "negative" : "neutral"} /><TradingMetric label="Average fill" value={formatQuantity(execution.average_fill_size)} /><TradingMetric label="Pending fees" value={String(execution.pending_fee_count || 0)} tone={Number(execution.pending_fee_count || 0) ? "negative" : "neutral"} /></div><section className="execution-quality-card"><header><strong>Execution quality</strong><span>Positive slippage is adverse to the trade direction.</span></header><div><JournalFact label="Signal slippage" tone={slippageTone(execution.average_signal_slippage)} value={basisPoints(execution.average_signal_slippage)} /><JournalFact label="Arrival slippage" tone={slippageTone(execution.average_arrival_slippage)} value={basisPoints(execution.average_arrival_slippage)} /><JournalFact label="Slippage coverage" value={ratioPct(execution.slippage_coverage)} /><JournalFact label="Rejected orders" tone={Number(execution.rejected_order_count || 0) ? "negative" : "neutral"} value={String(execution.rejected_order_count || 0)} /></div></section><TradingDataTable columns={["venue", "notional", "share_pct"]} defaultSort="notional" rows={venues.map((row) => ({ ...row, share_pct: ratioPct(row.share) }))} searchPlaceholder="Search execution venues…" /></div>;
}

function RiskJournalView({ risk, summary }: { risk: Record<string, string | number | null>; summary: Record<string, string | number | null> }) {
  return <div className="risk-journal-view"><section><header><ShieldCheck size={16} /><div><strong>Risk discipline</strong><span>Coverage states are shown explicitly; missing plans are never treated as zero risk.</span></div></header><div className="risk-journal-grid"><JournalFact label="Max drawdown" tone={Number(risk.maximum_drawdown || 0) ? "negative" : "neutral"} value={money(risk.maximum_drawdown)} /><JournalFact label="Loss streak" tone={Number(risk.maximum_losing_streak || 0) > 2 ? "negative" : "neutral"} value={String(risk.maximum_losing_streak || 0)} /><JournalFact label="Win streak" tone="positive" value={String(risk.maximum_winning_streak || 0)} /><JournalFact label="Planned-risk coverage" value={ratioPct(risk.planned_risk_coverage)} /><JournalFact label="Average R" tone={numberTone(risk.average_r_multiple)} value={ratioNumber(risk.average_r_multiple)} /><JournalFact label="Average hold" value={compactDuration(Number(summary.average_duration_seconds || 0))} /></div></section><section className="risk-coverage"><header><Target size={16} /><div><strong>Excursion evidence</strong><span>MAE and MFE require price-path observations while the episode is open.</span></div></header><div><JournalFact label="MAE coverage" value={ratioPct(risk.mae_coverage)} /><JournalFact label="Average MAE" tone="negative" value={money(risk.average_mae)} /><JournalFact label="MFE coverage" value={ratioPct(risk.mfe_coverage)} /><JournalFact label="Average MFE" tone="positive" value={money(risk.average_mfe)} /></div></section></div>;
}

function TradingJournalGuide({ onClose }: { onClose: () => void }) {
  return <div className="journal-guide-backdrop" role="presentation"><section aria-label="Trading journal guide" aria-modal="true" className="journal-guide-modal" role="dialog"><header><div><BookOpen size={20} /><span><strong>How to read the Trading Journal</strong><small>Performance evidence, not a broker confirmation or tax-lot statement</small></span></div><button aria-label="Close guide" onClick={onClose} type="button"><X size={18} /></button></header><div className="journal-guide-grid"><article><Gauge size={17} /><strong>Trade episode</strong><p>One account, instrument, and strategy position from flat to flat. Scale-ins and partial exits stay together so win rate counts decisions rather than FIFO fragments.</p></article><article><Activity size={17} /><strong>Expectancy</strong><p>Win rate × average win minus loss rate × average loss. Positive expectancy after fees is more important than win rate by itself.</p></article><article><BarChart3 size={17} /><strong>Profit factor and payoff</strong><p>Profit factor compares all winning dollars with all losing dollars. Payoff compares the average winner with the average loser.</p></article><article><BarChart3 size={17} /><strong>Realized P&amp;L candles</strong><p>Each candle is cumulative closed-episode net P&amp;L: open is the prior cumulative result; high and low are the best and worst levels reached inside the bucket; close is its final level. Choose 30 minutes, 1 hour, 1 day, or 1 month. Buckets use New York time and empty buckets are omitted. This is realized trading performance, not account equity or open-position P&amp;L.</p></article><article><ShieldCheck size={17} /><strong>Drawdown and R</strong><p>Drawdown measures peak-to-trough closed P&amp;L decline. R-multiple divides net P&amp;L by the risk planned before entry and is unavailable when no plan was recorded.</p></article><article><Target size={17} /><strong>MAE and MFE</strong><p>Maximum adverse and favorable excursion describe the worst and best open-trade path. Coverage is shown because broker fills alone cannot reconstruct the entire price path.</p></article><article><BookOpen size={17} /><strong>Attribution</strong><p>Strategy reports require strategy ID and revision on the opening execution. Manual or older broker activity remains explicitly Unattributed instead of being guessed.</p></article></div></section></div>;
}

function TradingFreshness({ data }: { data: CanonicalTradingPreview }) {
  return <div className={`trading-freshness ${data.stale ? "is-stale" : "is-current"}`}><strong>{data.complete && !data.stale ? "Complete broker state" : data.stale ? "Stale or partial state" : "Snapshot assembling"}</strong><span>{data.provider.replaceAll("_", " ")} · {data.mode} · <MarketTime value={data.as_of} /></span>{data.stale_reason ? <em>{data.stale_reason}</em> : null}</div>;
}

function TradingMetric({ label, tone = "neutral", value }: { label: string; tone?: "neutral" | "negative" | "positive" | "primary"; value: string }) {
  return <div className={`trading-metric tone-${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

function nestedValue(row: PreviewRow, container: string, ...keys: string[]) {
  const nested = row[container];
  if (!nested || typeof nested !== "object") return "";
  const record = nested as PreviewRow;
  for (const key of keys) if (record[key] !== undefined && record[key] !== null) return record[key];
  return "";
}

function signedMoney(value: unknown) { const number = Number(value || 0); return `${number > 0 ? "+" : ""}${money(number)}`; }
function numberTone(value: unknown): "negative" | "positive" | "neutral" { const number = Number(value || 0); return number > 0 ? "positive" : number < 0 ? "negative" : "neutral"; }

function StrategyOrderEntry({ marketSnapshot, runtimeMode, strategy, symbol, trading }: { marketSnapshot?: Record<string, unknown> | null; runtimeMode?: string; strategy?: CanvasPreview["strategy"]; symbol: string; trading?: CanonicalTradingPreview }) {
  const initialAssignment = strategy?.assignment ?? null;
  const [assignment, setAssignment] = useState<PreviewRow | null>(initialAssignment);
  const [accountId, setAccountId] = useState(String(initialAssignment?.account_id || trading?.accounts[0]?.alias || trading?.accounts[0]?.account_id || ""));
  const linkedPosition = trading?.positions.find((row) => String(nestedValue(row, "instrument", "symbol") || row.ticker || "").toUpperCase() === symbol);
  const [conid, setConid] = useState(String(initialAssignment?.conid || nestedValue(linkedPosition ?? {}, "instrument", "conid") || linkedPosition?.conid || ""));
  const [mode, setMode] = useState<"manage" | "request" | "automatic">("request");
  const [reenter, setReenter] = useState(Boolean((initialAssignment?.permissions as PreviewRow | undefined)?.reenter));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [proposalAuthority, setProposalAuthority] = useState<"manual" | "semi_automatic">("manual");
  const [proposalQuantity, setProposalQuantity] = useState(1);
  const [proposalStop, setProposalStop] = useState("");
  const [proposalTarget, setProposalTarget] = useState("");
  const configuredCapabilities = (strategy?.capabilities ?? []).filter((capability) => capability.enabled !== false);
  const readOnlyBacktest = strategy?.runtime_mode === "backtest" || strategy?.runtime_mode === "backtest_debug";
  const runId = strategy?.run_id || "";
  const interactiveReplay = Boolean(runId && !readOnlyBacktest);
  const interactiveLive = runtimeMode === "live" || runtimeMode === "paper";

  useEffect(() => {
    setAssignment(strategy?.assignment ?? null);
  }, [strategy?.assignment]);

  async function createAssignment() {
    if (readOnlyBacktest) {
      setMessage("Backtest assignments are pinned by the Run Plan and are read-only in Canvas.");
      return;
    }
    if (!accountId.trim() || !Number(conid)) {
      setMessage("Account and IBKR conid are required.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const assignmentEndpoint = interactiveReplay
        ? `/api/trading/replay/runs/${encodeURIComponent(runId)}/assignments`
        : "/api/trading/strategy-assignments";
      const created = await api<PreviewRow>(assignmentEndpoint, {
        body: JSON.stringify({
          account_id: accountId.trim(),
          conid: Number(conid),
          permissions: {
            add: true,
            enter: mode !== "manage",
            exit: true,
            observe: true,
            reduce: true,
            reenter: mode !== "manage" && reenter,
          },
          source: "canvas_order_entry",
          strategy_id: strategy?.strategy_id || "long-momentum-campaign",
          strategy_revision: strategy?.revision || 1,
          ticker: symbol,
        }),
        method: "POST",
      });
      setAssignment(created);
      setMessage(mode === "manage" ? "Management will attach after a confirmed fill." : "Strategy assignment armed.");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function command(commandName: string) {
    if (readOnlyBacktest) {
      setMessage("Backtest state is immutable inspection evidence; rerun with a new approved configuration to change it.");
      return;
    }
    const assignmentId = String(assignment?.assignment_id || "");
    if (!assignmentId) return;
    setBusy(true);
    setMessage("");
    try {
      const commandEndpoint = interactiveReplay
        ? `/api/trading/replay/runs/${encodeURIComponent(runId)}/assignments/${encodeURIComponent(assignmentId)}/commands`
        : `/api/trading/strategy-assignments/${encodeURIComponent(assignmentId)}/commands`;
      const updated = await api<PreviewRow>(commandEndpoint, {
        body: JSON.stringify({ command: commandName }),
        method: "POST",
      });
      setAssignment(updated);
      setMessage(commandName.replaceAll("_", " "));
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitTradeProposal() {
    if (!interactiveReplay && !interactiveLive) {
      setMessage(readOnlyBacktest ? "Backtest proposals are immutable run evidence and cannot be submitted after the fact." : "Trade proposals require a Replay, Paper, or Live runtime workspace.");
      return;
    }
    if (!accountId.trim() || !Number(conid) || !marketSnapshot || marketSnapshot.freshness !== "ready") {
      setMessage("A simulated account, point-in-time conid, and ready chart snapshot are required.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const proposalEndpoint = interactiveReplay
        ? `/api/trading/replay/runs/${encodeURIComponent(runId)}/trade-proposals`
        : `/api/trading/${runtimeMode}/trade-proposals`;
      const result = await api<{ decision?: { status: string }; proposal_id: string; status?: string }>(proposalEndpoint, {
        body: JSON.stringify({
          account_id: accountId.trim(),
          action: "enter_long",
          authority: proposalAuthority,
          conid: Number(conid),
          invalidation_price: proposalStop ? Number(proposalStop) : null,
          market_snapshot: marketSnapshot,
          profit_target_price: proposalTarget ? Number(proposalTarget) : null,
          quantity: proposalQuantity,
          reason: "Confirmed from the Canvas chart order-entry panel",
          ticker: symbol,
        }),
        method: "POST",
      });
      setMessage(result.decision
        ? `Proposal ${result.proposal_id.slice(0, 8)} · Portfolio ${result.decision.status}`
        : `Proposal ${result.proposal_id.slice(0, 8)} · ${String(result.status || "validated").replaceAll("_", " ")}`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const status = String(assignment?.status || "not assigned");
  return <section className="strategy-order-entry">
    <header><span><strong>Order entry</strong><small>{strategy?.name || "Long Momentum Campaign"}</small></span><em data-state={status}>{status.replaceAll("_", " ")}</em></header>
    {configuredCapabilities.length ? <div className="strategy-order-capabilities">
      <span>Included management</span>
      {configuredCapabilities.map((capability) => <div key={capability.capability_id}><strong>{labelFor(capability.capability_id)}</strong><small>{String(capability.settings?.mode || "automatic").replaceAll("_", " ")}</small></div>)}
    </div> : null}
    {!assignment ? <>
      <label><span>Account</span><input onChange={(event) => setAccountId(event.target.value)} placeholder="IBKR account" value={accountId} /></label>
      <label><span>Conid</span><input inputMode="numeric" onChange={(event) => setConid(event.target.value.replace(/\D/g, ""))} placeholder="Contract ID" value={conid} /></label>
      <label><span>Authority</span><select onChange={(event) => setMode(event.target.value as typeof mode)} value={mode}><option value="request">Strategy entry</option><option value="manage">Manage after fill</option><option value="automatic">Fully automatic</option></select></label>
      <label className="strategy-order-check"><input checked={reenter} disabled={mode === "manage"} onChange={(event) => setReenter(event.target.checked)} type="checkbox" /><span>Allow re-entry</span></label>
      <button disabled={busy || readOnlyBacktest} onClick={createAssignment} type="button">{busy ? "Saving…" : readOnlyBacktest ? "Pinned by Run Plan" : mode === "manage" ? "Attach plan" : "Arm strategy"}</button>
    </> : <>
      <div className="strategy-order-summary"><span><small>Symbol</small><strong>{symbol}</strong></span><span><small>Account</small><strong>{String(assignment.account_id)}</strong></span></div>
      <div className="strategy-order-actions">
        <button disabled={busy || readOnlyBacktest || status === "paused"} onClick={() => command("request_entry")} type="button">Request entry</button>
        <button disabled={busy || readOnlyBacktest || status === "paused"} onClick={() => command("force_entry")} type="button">Force + attach</button>
        <button disabled={busy || readOnlyBacktest} onClick={() => command(status === "paused" ? "resume" : "pause")} type="button">{status === "paused" ? "Resume" : "Pause"}</button>
        <button className="danger" disabled={busy || readOnlyBacktest} onClick={() => command("disable_after_exit")} type="button">Disable after exit</button>
      </div>
      <div className="strategy-order-proposal">
        <span><strong>Chart trade proposal</strong><small>Snapshot is revalidated by the run, then Portfolio and OMS retain exclusive authority.</small></span>
        <label><span>Authority</span><select onChange={(event) => setProposalAuthority(event.target.value as typeof proposalAuthority)} value={proposalAuthority}><option value="manual">Manual confirm</option><option value="semi_automatic">Semi-automatic</option></select></label>
        <label><span>Quantity</span><input min={1} onChange={(event) => setProposalQuantity(Math.max(1, Number(event.target.value) || 1))} type="number" value={proposalQuantity} /></label>
        <label><span>Stop price</span><input min={0.01} onChange={(event) => setProposalStop(event.target.value)} placeholder="Optional" step="0.01" type="number" value={proposalStop} /></label>
        <label><span>Target price</span><input min={0.01} onChange={(event) => setProposalTarget(event.target.value)} placeholder="Optional" step="0.01" type="number" value={proposalTarget} /></label>
        <button disabled={busy || (!interactiveReplay && !interactiveLive) || !marketSnapshot || marketSnapshot.freshness !== "ready"} onClick={submitTradeProposal} type="button">Confirm proposal</button>
      </div>
      <small className="strategy-order-disclosure">Commands are persisted here. Orders are placed only by the shared runtime after causal evaluation and risk validation.</small>
    </>}
    {message ? <p role="status">{message}</p> : null}
  </section>;
}

function StrategyPreview({ data, showSignals }: { data: CanvasPreview["strategy"]; showSignals: boolean }) {
  const config = data.definition?.config;
  const parameters = flattenStrategyParameters(config?.parameters ?? {});
  const searchSpace = flattenStrategyParameters(config?.parameter_space ?? {});
  const inputs = [...(data.taxonomy?.indicators ?? []).map((row) => ({ ...row, input_kind: "Indicator" })), ...(data.taxonomy?.signals ?? []).map((row) => ({ ...row, input_kind: "Signal" }))];
  return <div className="canvas-strategy-preview">
    <header className="strategy-definition-header"><div><span>Strategy definition</span><strong>{data.name || data.definition?.name || data.strategy_id}</strong><small>{config?.direction === "long_only" ? "Long only" : String(config?.direction || "")} · immutable v{data.revision}</small></div><em data-state={data.state}>{data.state.replaceAll("_", " ")}</em></header>
    <section className="strategy-definition-section"><header><strong>Evidence contract</strong><span>Each input keeps its own timeframe, role, freshness, score, and confidence requirements.</span></header><PreviewTable columns={["input_kind", "key", "timeframe", "role", "maximum_age_ms", "weight", "minimum_score", "minimum_confidence"]} rows={inputs} /></section>
    <section className="strategy-definition-grid"><div><header><strong>Resolved revision</strong><span>Exact values used by replay and live</span></header><PreviewTable columns={["parameter", "value"]} rows={parameters} /></div><div><header><strong>Hyperparameter space</strong><span>Candidate values; never passed unresolved to live execution</span></header><PreviewTable columns={["parameter", "value"]} rows={searchSpace} /></div></section>
    {showSignals ? <section className="strategy-definition-section"><header><strong>Saved decisions</strong><span>Only durable records at or before the Canvas clock are drawn on charts.</span></header><PreviewTable columns={["effective_at", "ticker", "action", "reason", "score", "confidence", "reference_price", "invalidation_price"]} rows={data.signals} /></section> : null}
    <section className="strategy-definition-section"><header><strong>Order management</strong><span>Durable broker commands, policy decisions, state transitions, and measured local submission latency.</span></header><PreviewTable columns={["event_time", "state", "event", "action", "entity_type", "decision_to_submit_ms", "message_ids", "confirmed", "rejection_reason"]} rows={data.order_management ?? []} /></section>
  </div>;
}

function flattenStrategyParameters(value: Record<string, unknown>, prefix = ""): PreviewRow[] {
  return Object.entries(value).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) return flattenStrategyParameters(item as Record<string, unknown>, path);
    return [{ parameter: path, value: Array.isArray(item) ? item.join(", ") : String(item ?? "—") }];
  });
}

function containerFields(id: WorkspaceContainerId, settings: ContainerSettings, linkContext: CanvasLinkContext, updateSettings: SettingsUpdater, onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void) {
  if (id === "microstructure") return <><TextField label="Symbol" onChange={(value) => { const symbol = value.toUpperCase(); updateSettings((state) => ({ ...state, chart: { ...state.chart, symbol } })); onLinkContextChange({ symbol }); }} value={linkContext.symbol} /><div className="canvas-settings-note">The symbol follows the selected link color. Quotes and trades share one QMD event stream; each table retains its latest 1,024 decoded rows at the shared historical clock.</div></>;
  if (id === "facts") return <><TextField label="Symbol" onChange={(value) => { const symbol = value.toUpperCase(); updateSettings((state) => ({ ...state, chart: { ...state.chart, symbol } })); onLinkContextChange({ symbol }); }} value={linkContext.symbol} /><div className="canvas-settings-note">Facts follow the selected link color and shared point-in-time clock. Reported values remain distinct from explicitly labeled estimates, ranges, and upper bounds.</div></>;
  const settingsId = id as keyof ContainerSettings;
  const current = settings[settingsId] as Record<string, unknown>;
  function patch(value: Record<string, unknown>) { updateSettings((state) => ({ ...state, [id]: { ...(state[settingsId] as Record<string, unknown>), ...value } })); }
  if (id === "chart") return <><TextField label="Symbol" onChange={(value) => { patch({ symbol: value.toUpperCase() }); onLinkContextChange({ symbol: value.toUpperCase() }); }} value={linkContext.symbol} /><SelectField label="Bar interval" onChange={(value) => patch({ timeframe: value as CanvasChartTimeframe })} optionLabel={formatChartTimeframe} options={HISTORICAL_TIMEFRAMES} value={settings.chart.timeframe} /><CheckField checked={Boolean(current.showVolume)} label="Show volume" onChange={(value) => patch({ showVolume: value })} /></>;
  if (id === "portfolio") return <><CheckField checked={Boolean(current.showExposure)} label="Show exposure" onChange={(value) => patch({ showExposure: value })} /><CheckField checked={Boolean(current.showPnl)} label="Show P&L" onChange={(value) => patch({ showPnl: value })} /></>;
  if (id === "strategy") return <CheckField checked={Boolean(current.showSignals)} label="Show recent signals" onChange={(value) => patch({ showSignals: value })} />;
  if (id === "charts_quotes") return <div className="canvas-settings-note">The main chart starts at 10 seconds with MACD. Its timeframe, indicators, pane layout, and appearance persist from controls inside the chart. The lower charts remain fixed to monthly and daily horizons. Drag the dividers between rows and columns to persist the workspace proportions.</div>;
  if (id === "scanner") return <><NumberField label="Maximum rows" max={5000} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">Columns, sorting, and filters are managed inside Scanner and persist with this container instance.</div></>;
  if (id === "signal_stream") return <><NumberField label="Maximum events" max={5000} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">Market rules are reconstructed from canonical data. Strategy events remain durable records owned by the strategy runtime.</div></>;
  if (id === "watchlist") return <><NumberField label="Maximum rows" max={500} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">QMD Market Discovery owns Watchlist membership and its causal history. Canvas only chooses which published Watchlist to present.</div></>;
  if (id === "strategy_activity") return <><NumberField label="Maximum events" max={5000} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">Filters remain local to this container. Events come from the durable Trading Journal and are never reconstructed in the browser.</div></>;
  if (id === "orders") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showOrderIds)} label="Show order IDs" onChange={(value) => patch({ showOrderIds: value })} /></>;
  if (id === "fills") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showCommission)} label="Show commission" onChange={(value) => patch({ showCommission: value })} /></>;
  if (id === "positions") return <><NumberField label="Rows" max={100} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showPnl)} label="Show P&L" onChange={(value) => patch({ showPnl: value })} /></>;
  if (id === "closed_trades") return <><NumberField label="Rows" max={100} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showFees)} label="Show fees" onChange={(value) => patch({ showFees: value })} /></>;
  if (id === "activity") return <NumberField label="Rows" max={100} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} />;
  if (id === "performance_journal") return <><NumberField label="Trade rows" max={500} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showRiskMultiple)} label="Show risk multiple" onChange={(value) => patch({ showRiskMultiple: value })} /><div className="canvas-settings-note">Reports count flat-to-flat episodes, not FIFO realization fragments. Strategy revisions stay separate.</div></>;
  if (id === "news") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["1", "6", "24", "168", "720"]} value={String(current.lookbackHours)} /><SelectField label="Article class" onChange={(value) => patch({ kind: value })} optionLabel={(value) => NEWS_ARTICLE_CLASS_OPTIONS.find((option) => option.value === value)?.label ?? value} options={NEWS_ARTICLE_CLASS_OPTIONS.map((option) => option.value)} value={String(current.kind)} /><SelectField label="Text coverage" onChange={(value) => patch({ content: value })} options={["all", "full", "title"]} value={String(current.content)} /></>;
  if (id === "ticker_news") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["24", "72", "168", "720"]} value={String(current.lookbackHours)} /><CheckField checked={Boolean(current.showTeaser)} label="Show teaser" onChange={(value) => patch({ showTeaser: value })} /><div className="canvas-settings-note">Ticker comes from the selected link color. Hot, cold, and old states use the shared clock.</div></>;
  if (id === "news_detail") return <div className="canvas-settings-note">This reader follows the most recently selected news article in this canvas.</div>;
  if (id === "sec") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["24", "72", "168", "720", "8760"]} value={String(current.lookbackHours)} /><SelectField label="Content" onChange={(value) => patch({ content: value })} options={["all", "readable", "xbrl"]} value={String(current.content)} /><div className="canvas-settings-note">Search, ticker, and filing labels are available in the container query bar. Results are constrained to the shared point-in-time clock.</div></>;
  if (id === "ticker_sec") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["24", "72", "168", "720", "8760"]} value={String(current.lookbackHours)} /><div className="canvas-settings-note">Ticker follows the selected link color. Hot means accepted within four hours, cold within 24 hours, and old is older.</div></>;
  if (id === "sec_detail") return <div className="canvas-settings-note">This reader follows the most recently selected filing in this canvas.</div>;
  if (id === "xbrl") return <><NumberField label="Decision metrics" onChange={(value) => patch({ metricLimit: Math.max(3, Math.min(18, value)) })} value={Number(current.metricLimit)} /><CheckField checked={Boolean(current.showRawTags)} label="Show taxonomy tags" onChange={(value) => patch({ showRawTags: value })} /><div className="canvas-settings-note">The causal score, trajectory, and five financial facets always remain visible. This setting controls supporting decision metrics and audit detail.</div></>;
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
  const obsoleteDecisionIndicators = ["indicator.microstructure_outlook", "indicator.qmd_architecture", "indicator.qmd_structural_pressure", "indicator.qmd_decision"];
  const replacedStructure = storedIndicators.some((id) => ["indicator.qmd_liquidity_levels", "indicator.market_structure_levels", "indicator.qmd_level_confluence"].includes(id));
  const replacedDecision = storedIndicators.some((id) => obsoleteDecisionIndicators.includes(id));
  const canonicalIndicators = storedIndicators.filter((id) => ![
    "indicator.qmd_liquidity_levels",
    "indicator.market_structure_levels",
    "indicator.qmd_level_confluence",
    ...obsoleteDecisionIndicators,
  ].includes(id));
  if (replacedStructure && !canonicalIndicators.includes("indicator.qmd_generic_structure")) canonicalIndicators.push("indicator.qmd_generic_structure");
  if (replacedDecision && !canonicalIndicators.includes("indicator.flow_structure_composite")) canonicalIndicators.push("indicator.flow_structure_composite");
  const migratedIndicators = stored.version === DEFAULT_SETTINGS.version || canonicalIndicators.includes("indicator.macd") ? canonicalIndicators : [...canonicalIndicators, "indicator.macd"];
  const visibleIndicators = stored.version === DEFAULT_SETTINGS.version
    ? migratedIndicators
    : Array.from(new Set([...migratedIndicators, "indicator.flow_structure_composite", "strategy.presentation"]));
  const timeframe = HISTORICAL_TIMEFRAMES.includes(stored.chart?.timeframe as CanvasChartTimeframe) ? stored.chart!.timeframe! : DEFAULT_SETTINGS.chart.timeframe;
  const storedPerformance = stored.performance_journal as (Partial<ContainerSettings["performance_journal"]> & { showFees?: boolean }) | undefined;
  const storedWatchlist = stored.watchlist as Partial<WatchUniverseSettings> | undefined;
  const storedWatchlistIds = Array.isArray(storedWatchlist?.watchlistIds) ? storedWatchlist.watchlistIds : [];
  const normalizedStoredWatchlistIds = Array.from(new Set([
    ...storedWatchlistIds.filter((value): value is string => typeof value === "string" && Boolean(value.trim())),
    String(storedWatchlist?.watchlistId ?? ""),
  ].filter(Boolean)));
  const legacyDefaultWatchlist = normalizedStoredWatchlistIds.length === 0
    || (normalizedStoredWatchlistIds.length === 1 && normalizedStoredWatchlistIds[0] === "top-penny-gainers");
  const migrateLegacyWatchlist = Number(stored.version ?? 0) < 27 && legacyDefaultWatchlist;
  const watchlistIds = migrateLegacyWatchlist
    ? [...DEFAULT_WATCHLIST_TAB_IDS]
    : normalizedStoredWatchlistIds;
  return {
    version: DEFAULT_SETTINGS.version,
    chart: { ...DEFAULT_SETTINGS.chart, ...(stored.chart ?? {}), timeframe, visibleIndicators: [...visibleIndicators] },
    charts_quotes: {
      main: normalizeChartSlot(stored.charts_quotes?.main, DEFAULT_SETTINGS.charts_quotes.main),
      month: { ...normalizeChartSlot(stored.charts_quotes?.month, DEFAULT_SETTINGS.charts_quotes.month), timeframe: "1mo" },
      daily: { ...normalizeChartSlot(stored.charts_quotes?.daily, DEFAULT_SETTINGS.charts_quotes.daily), timeframe: "1d" },
      layout: normalizeChartsQuotesLayout(stored.charts_quotes?.layout),
    },
    microstructure: { limit: 1024 },
    fills: { ...DEFAULT_SETTINGS.fills, ...(stored.fills ?? {}) },
    positions: { ...DEFAULT_SETTINGS.positions, ...(stored.positions ?? {}) },
    closed_trades: { ...DEFAULT_SETTINGS.closed_trades, ...(stored.closed_trades ?? {}) },
    activity: { ...DEFAULT_SETTINGS.activity, ...(stored.activity ?? {}) },
    performance_journal: {
      ...DEFAULT_SETTINGS.performance_journal,
      ...(storedPerformance ?? {}),
      showRiskMultiple: storedPerformance?.showRiskMultiple ?? storedPerformance?.showFees ?? DEFAULT_SETTINGS.performance_journal.showRiskMultiple,
    },
    news: { ...DEFAULT_SETTINGS.news, ...(stored.news ?? {}) },
    ticker_news: { ...DEFAULT_SETTINGS.ticker_news, ...(stored.ticker_news ?? {}) },
    news_detail: {},
    orders: { ...DEFAULT_SETTINGS.orders, ...(stored.orders ?? {}) },
    portfolio: { ...DEFAULT_SETTINGS.portfolio, ...(stored.portfolio ?? {}) },
    scanner: migrateMarketScannerSettings(
      normalizeTechnicalListSettings(DEFAULT_SETTINGS.scanner, stored.scanner),
      stored.version,
    ),
    signal_stream: {
      ...normalizeTechnicalListSettings(DEFAULT_SETTINGS.signal_stream, stored.signal_stream),
      preset: normalizeSignalStreamPreset(stored.signal_stream?.preset),
    },
    watchlist: {
      ...DEFAULT_SETTINGS.watchlist,
      ...(stored.watchlist ?? {}),
      columns: Number(stored.version ?? 0) < 25 ? [] : normalizeScannerColumnKeys(stored.watchlist?.columns, stored.watchlist?.customColumns),
      customColumns: normalizeScannerCustomColumns(stored.watchlist?.customColumns),
      watchlistId: migrateLegacyWatchlist ? watchlistIds[0] : watchlistIds.includes(String(storedWatchlist?.watchlistId ?? "")) ? String(storedWatchlist?.watchlistId) : watchlistIds[0] ?? "",
      watchlistIds,
    },
    strategy_activity: { ...DEFAULT_SETTINGS.strategy_activity, ...(stored.strategy_activity ?? {}) },
    sec: { ...DEFAULT_SETTINGS.sec, ...(stored.sec ?? {}) },
    ticker_sec: { ...DEFAULT_SETTINGS.ticker_sec, ...(stored.ticker_sec ?? {}) },
    sec_detail: {},
    strategy: { ...DEFAULT_SETTINGS.strategy, ...(stored.strategy ?? {}) },
    xbrl: {
      metricLimit: Number((stored.xbrl as { metricLimit?: number; limit?: number } | undefined)?.metricLimit ?? (stored.xbrl as { limit?: number } | undefined)?.limit ?? DEFAULT_SETTINGS.xbrl.metricLimit),
      showRawTags: Boolean((stored.xbrl as { showRawTags?: boolean; showPeriod?: boolean } | undefined)?.showRawTags ?? (stored.xbrl as { showPeriod?: boolean } | undefined)?.showPeriod ?? DEFAULT_SETTINGS.xbrl.showRawTags),
    },
  };
}

function normalizeSignalStreamPreset(value: unknown) {
  const preset = String(value || "");
  if (["All", "Market", "News", "SEC", "Strategy"].includes(preset)) return preset;
  return DEFAULT_SETTINGS.signal_stream.preset;
}

function normalizeChartSlot(stored: Partial<CanvasChartSettings> | undefined, defaults: CanvasChartSettings): CanvasChartSettings {
  const timeframe = HISTORICAL_TIMEFRAMES.includes(stored?.timeframe as CanvasChartTimeframe) ? stored!.timeframe! : defaults.timeframe;
  const visibleIndicators = Array.isArray(stored?.visibleIndicators) ? stored.visibleIndicators.filter((value): value is string => typeof value === "string") : defaults.visibleIndicators;
  const required = defaults.visibleIndicators.includes("strategy.presentation") ? ["strategy.presentation"] : [];
  return { ...defaults, ...(stored ?? {}), timeframe, visibleIndicators: Array.from(new Set([...visibleIndicators, ...required])) };
}

function normalizeChartsQuotesLayout(stored: Partial<ChartsQuotesLayoutSettings> | undefined): ChartsQuotesLayoutSettings {
  const tapeColumnPercent = normalizeLayoutValue(stored?.tapeColumnPercent, 14, 38, DEFAULT_SETTINGS.charts_quotes.layout.tapeColumnPercent);
  const storedReserved = Number(stored?.reservedColumnPercent);
  const hasReservedColumn = Number.isFinite(storedReserved);
  const storedMonth = Number(stored?.monthColumnPercent);
  const migratedMonth = !hasReservedColumn && Number.isFinite(storedMonth)
    ? storedMonth * (100 - tapeColumnPercent) / 100
    : stored?.monthColumnPercent;
  const monthColumnPercent = normalizeLayoutValue(migratedMonth, 20, 68, DEFAULT_SETTINGS.charts_quotes.layout.monthColumnPercent);
  const reservedColumnPercent = normalizeLayoutValue(
    stored?.reservedColumnPercent,
    12,
    80 - monthColumnPercent,
    DEFAULT_SETTINGS.charts_quotes.layout.reservedColumnPercent,
  );
  return {
    lowerRowPercent: normalizeLayoutValue(stored?.lowerRowPercent, 22, 58, DEFAULT_SETTINGS.charts_quotes.layout.lowerRowPercent),
    monthColumnPercent,
    reservedColumnPercent,
    tapeColumnPercent,
  };
}

function normalizeLayoutValue(value: unknown, minimum: number, maximum: number, fallback: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(minimum, Math.min(maximum, numeric)) : fallback;
}

function normalizeTechnicalListSettings<T extends MarketScannerSettings | SignalStreamSettings>(
  defaults: T,
  stored: Partial<T> | undefined,
): T {
  return {
    ...defaults,
    ...(stored ?? {}),
    columns: normalizeScannerColumnKeys(stored?.columns, stored?.customColumns),
    customColumns: normalizeScannerCustomColumns(stored?.customColumns),
  };
}

function normalizeScannerCustomColumns(value: unknown): ScannerCustomColumn[] {
  if (!Array.isArray(value)) return [];
  const allowedMetrics = new Set(["change_pct", "dollar_volume", "high", "low", "quote_count", "range_pct", "relative_volume", "trade_count", "volume", "vwap", "vwap_distance_pct"]);
  const unique = new Map<string, ScannerCustomColumn>();
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const metric = String(record.metric ?? "");
    if (!allowedMetrics.has(metric)) continue;
    if (["vwap", "vwap_distance_pct"].includes(metric)) {
      const anchor = record.anchor === "regular_session" ? "regular_session" : "extended_session";
      const source = record.source === "trade_price" ? "trade_price" : "hlc3";
      const key = `technical__${metric}__${anchor}__${source}`;
      unique.set(key, { anchor, key, metric: metric as ScannerCustomColumn["metric"], source });
      continue;
    }
    if (metric === "relative_volume") {
      const key = "technical__relative_volume__extended_session";
      unique.set(key, { anchor: "extended_session", key, lookbackSessions: 20, metric: "relative_volume" });
      continue;
    }
    const timeframe = String(record.timeframe ?? "");
    if (!SCANNER_TIMEFRAMES.includes(timeframe as ScannerTimeframe)) continue;
    const key = `technical__${metric}__${timeframe}`;
    unique.set(key, { key, metric: metric as ScannerCustomColumn["metric"], timeframe: timeframe as ScannerTimeframe });
  }
  return [...unique.values()];
}

function normalizeScannerColumnKeys(columns: unknown, customColumns: unknown): string[] {
  if (!Array.isArray(columns)) return [];
  const migrated = new Map<string, string>();
  if (Array.isArray(customColumns)) {
    for (const item of customColumns) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      const oldKey = String(record.key ?? "");
      const metric = String(record.metric ?? "");
      if (!oldKey || !metric) continue;
      if (["vwap", "vwap_distance_pct"].includes(metric)) {
        const anchor = record.anchor === "regular_session" ? "regular_session" : "extended_session";
        const source = record.source === "trade_price" ? "trade_price" : "hlc3";
        migrated.set(oldKey, `technical__${metric}__${anchor}__${source}`);
      } else if (metric === "relative_volume") {
        migrated.set(oldKey, "technical__relative_volume__extended_session");
      }
    }
  }
  return columns.map(String).map((key) => migrated.get(key) ?? key).filter((key, index, values) => values.indexOf(key) === index);
}

function cloneDefaultSettings() { return normalizeSettings(DEFAULT_SETTINGS); }
function instanceSettings(registry: CanvasRegistry, instanceId: string) {
  const stored = registry.instanceSettings[instanceId] as Partial<ContainerSettings> | undefined;
  return stored ? normalizeSettings(stored) : instanceId === "chart" ? readSettings() : cloneDefaultSettings();
}
function useCanvasScannerSnapshot({ cutoffMs, enabled, technicalWindows }: { cutoffMs: number; enabled: boolean; technicalWindows: string }) {
  const [snapshot, setSnapshot] = useState<CanvasScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState("");
  const targetRef = useRef({ asOf: "", enabled: false, key: "", technicalWindows: "" });
  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const loadedKeyRef = useRef("");
  const retryTimerRef = useRef<number | null>(null);

  const pump = useCallback(() => {
    if (!mountedRef.current || inFlightRef.current || !targetRef.current.enabled) return;
    inFlightRef.current = true;
    void (async () => {
      try {
        while (mountedRef.current) {
          const target = targetRef.current;
          if (!target.enabled || target.key === loadedKeyRef.current) break;
          setLoading(true);
          setError("");
          try {
            const payload = await api<CanvasScannerSnapshot>(`/api/trading/canvas-scanner${query({
              as_of: target.asOf,
              lookback_minutes: 15,
              technical_windows: target.technicalWindows,
            })}`, { timeoutMs: 90_000 });
            if (!mountedRef.current) return;
            if (!targetRef.current.enabled) return;
            setSnapshot(payload);
            setError("");
            loadedKeyRef.current = target.key;
            const refreshStatus = payload.meta?.status === "building" || payload.meta?.status === "error"
              ? payload.meta.status
              : payload.meta?.refresh_status === "building" || payload.meta?.refresh_status === "error"
                ? payload.meta.refresh_status
                : payload.meta?.qmd_derived_status;
            if (
              targetRef.current.key === target.key
              && (refreshStatus === "building" || refreshStatus === "error")
            ) {
              const retryDelay = refreshStatus === "building" ? 5_000 : 60_000;
              if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
              retryTimerRef.current = window.setTimeout(() => {
                retryTimerRef.current = null;
                loadedKeyRef.current = "";
                pump();
              }, retryDelay);
            }
          } catch (reason) {
            if (!mountedRef.current) return;
            loadedKeyRef.current = target.key;
            setError(reason instanceof Error ? reason.message : String(reason));
            break;
          }
          if (targetRef.current.key === target.key) break;
        }
      } finally {
        inFlightRef.current = false;
        if (!mountedRef.current) return;
        setLoading(false);
        if (targetRef.current.enabled && targetRef.current.key !== loadedKeyRef.current) {
          window.queueMicrotask(pump);
        }
      }
    })();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const asOf = new Date(cutoffMs).toISOString();
    const key = `${asOf}:${technicalWindows}`;
    targetRef.current = { asOf, enabled, key, technicalWindows };
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (!enabled) {
      loadedKeyRef.current = "";
      setSnapshot(null);
      setLoading(false);
      setError("");
      return;
    }
    pump();
  }, [cutoffMs, enabled, pump, technicalWindows]);

  return { error, loading, snapshot };
}

function useCanvasLiveScannerSnapshot(enabled: boolean) {
  const [snapshot, setSnapshot] = useState<CanvasScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) {
      setSnapshot(null);
      setLoading(false);
      setError("");
      return;
    }
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const load = async () => {
      if (cancelled || controller) return;
      const request = new AbortController();
      controller = request;
      try {
        const payload = await api<{ market_time?: string; provider?: string; rows?: Record<string, unknown>[]; session_date?: string; watchlist_runtime?: WatchlistRuntimeResponse }>("/api/real-live-trading/scanner?row_limit=500", { signal: request.signal, timeoutMs: 45_000 });
        if (cancelled || request.signal.aborted) return;
        const rows = payload.rows ?? [];
        const asOfContext = payload.session_date && payload.market_time
          ? dateInTimeZone(payload.session_date, payload.market_time, "America/New_York")
          : new Date();
        setSnapshot({
          as_of: asOfContext.toISOString(),
          errors: {},
          meta: { complete_universe: true, row_count: rows.length, source: payload.provider || "qmd-gateway", status: "ready" } as ScannerSnapshotMeta,
          rows,
          signal_rows: [],
          watchlist_runtime: payload.watchlist_runtime,
        });
        setError("");
      } catch (reason) {
        if (!cancelled && !request.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (controller === request) controller = null;
        if (!cancelled) {
          setLoading(false);
          timer = window.setTimeout(load, 15_000);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [enabled]);
  return { error, loading, snapshot };
}
function readPreviewContext(): CanvasPreviewContext { try { const parsed = JSON.parse(window.localStorage.getItem(CANVAS_PREVIEW_CONTEXT_STORAGE_KEY) || "null") as CanvasPreviewContext | null; return parsed?.sessionDate && parsed?.previewTime ? parsed : { previewTime: "09:45", sessionDate: previousWeekdayIsoDate() }; } catch { return { previewTime: "09:45", sessionDate: previousWeekdayIsoDate() }; } }
function replayPreviewContext(run: CanvasReplayRun): CanvasPreviewContext {
  const current = new Date(["created", "warming"].includes(run.status) ? run.requested_start : run.current_time);
  const previewTime = new Intl.DateTimeFormat("en-CA", {
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    second: "2-digit",
    timeZone: "America/New_York",
  }).format(current);
  return { previewTime, sessionDate: run.session_date };
}
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
  const [hour, minute, second = 0] = time.split(":").map(Number);
  const desiredUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  let instant = new Date(desiredUtc);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { day: "2-digit", hour: "2-digit", hourCycle: "h23", minute: "2-digit", month: "2-digit", second: "2-digit", timeZone, year: "numeric" }).formatToParts(instant).filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
    const representedUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
    instant = new Date(instant.getTime() + desiredUtc - representedUtc);
  }
  return instant;
}
function labelFor(value: string) { return value.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2"); }
function formatChartTimeframe(value: string) {
  if (value === "100ms") return "100 milliseconds";
  if (value === "1d") return "Daily";
  if (value === "1w") return "Weekly";
  if (value === "1mo") return "Monthly";
  if (value === "1y") return "Yearly";
  const match = value.match(/^(\d+)([smh])$/);
  if (!match) return value;
  const count = Number(match[1]);
  const unit = match[2] === "s" ? "second" : match[2] === "m" ? "minute" : "hour";
  return `${count} ${unit}${count === 1 ? "" : "s"}`;
}
function statusLabel(value: WorkspaceWindowStatus) { return value.charAt(0).toUpperCase() + value.slice(1); }
function previewRowKey(row: PreviewRow, columns: string[], index: number) { return `${columns.map((column) => String(row[column] ?? "")).join("|")}|${index}`; }
function money(value: unknown) { const number = typeof value === "number" ? value : Number(value); return Number.isFinite(number) ? new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: 2, style: "currency" }).format(number) : "—"; }
function ratioPct(value: unknown) { const number = Number(value); return Number.isFinite(number) ? `${(number * 100).toFixed(number * 100 >= 10 ? 1 : 2)}%` : "—"; }
function ratioNumber(value: unknown) { const number = Number(value); return Number.isFinite(number) ? `${number.toFixed(2)}×` : "—"; }
function metricThresholdTone(value: unknown, threshold: number): "negative" | "neutral" | "positive" { const number = Number(value); return !Number.isFinite(number) ? "neutral" : number > threshold ? "positive" : number < threshold ? "negative" : "neutral"; }
function compactDuration(seconds: number) { if (!Number.isFinite(seconds) || seconds < 0) return "—"; if (seconds < 60) return `${Math.round(seconds)}s`; if (seconds < 3600) return `${Math.round(seconds / 60)}m`; return `${(seconds / 3600).toFixed(seconds < 36_000 ? 1 : 0)}h`; }
function formatJournalDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("en-US", { day: "numeric", hour: "numeric", minute: "2-digit", month: "short", timeZone: "America/New_York" }).format(date); }
function formatMoneyAxis(value: number) {
  if (!Number.isFinite(value)) return "";
  const absolute = Math.abs(value);
  const divisor = absolute >= 1_000_000 ? 1_000_000 : absolute >= 1_000 ? 1_000 : 1;
  const suffix = divisor === 1_000_000 ? "M" : divisor === 1_000 ? "K" : "";
  const precision = divisor === 1 || absolute / divisor >= 100 ? 0 : absolute / divisor >= 10 ? 1 : 2;
  return `${value < 0 ? "-" : ""}$${(absolute / divisor).toFixed(precision)}${suffix}`;
}
function formatPnlCandleTime(value: string, timeframe: PnlCandleTimeframe) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const shared = { timeZone: "America/New_York" } as const;
  if (timeframe === "30m" || timeframe === "1h") return new Intl.DateTimeFormat("en-US", { ...shared, day: "numeric", hour: "numeric", minute: "2-digit", month: "short" }).format(date);
  if (timeframe === "1d") return new Intl.DateTimeFormat("en-US", { ...shared, day: "numeric", month: "short", year: "2-digit" }).format(date);
  return new Intl.DateTimeFormat("en-US", { ...shared, month: "short", year: "numeric" }).format(date);
}
function basisPoints(value: unknown) { const number = Number(value); return Number.isFinite(number) ? `${number > 0 ? "+" : ""}${number.toFixed(2)} bp` : "—"; }
function slippageTone(value: unknown): "negative" | "neutral" | "positive" { const number = Number(value); return !Number.isFinite(number) || number === 0 ? "neutral" : number > 0 ? "negative" : "positive"; }
function formatQuantity(value: unknown) { const number = Number(value); return Number.isFinite(number) ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(number) : "—"; }
function formatPreviewDate(value?: string) { if (!value) return "this date"; return new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T12:00:00Z`)); }
function formatCell(value: unknown, column: string) { if (value === null || value === undefined || value === "") return "—"; if (column.includes("time") || column.includes("at_utc")) { const date = new Date(String(value)); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(date); } const numeric = typeof value === "number" ? value : /^-?\d+(?:\.\d+)?$/.test(String(value)) ? Number(value) : Number.NaN; if (Number.isFinite(numeric)) { if (isMoneyColumn(column)) return new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: 4, minimumFractionDigits: column.includes("price") || column === "mark" || column === "limit" || column === "stop" ? 2 : 0, style: "currency" }).format(numeric); return new Intl.NumberFormat("en-US", { maximumFractionDigits: column.includes("pct") ? 2 : 4 }).format(numeric); } if (Array.isArray(value)) return value.join(", "); return String(value); }
function isMoneyColumn(column: string) { return ["price", "mark", "limit", "stop", "market_value", "average_price", "unrealized_pnl", "realized_pnl", "gross_pnl", "net_pnl", "fees", "commission", "net_amount", "cash", "settled", "net_liquidation", "entry_price", "exit_price", "expectancy", "max_drawdown", "notional"].some((key) => column === key || column.endsWith(`_${key}`)); }
function cellTone(value: unknown, column: string) {
  if (["unrealized_pnl", "realized_pnl", "gross_pnl", "net_pnl", "return_pct", "expectancy", "risk_multiple"].includes(column)) { const number = Number(value); return number > 0 ? "positive" : number < 0 ? "negative" : "neutral"; }
  const normalized = String(value || "").toLowerCase();
  if (column === "side") return ["buy", "long"].includes(normalized) ? "positive" : ["sell", "short"].includes(normalized) ? "negative" : "neutral";
  if (column === "status") return ["filled"].includes(normalized) ? "positive" : ["rejected", "cancelled", "expired", "inactive"].includes(normalized) ? "negative" : ["working", "partially_filled", "pending_submission", "trigger_pending"].includes(normalized) ? "primary" : "neutral";
  if (column === "fee_state" && normalized === "pending") return "warning";
  return "neutral";
}
function containerTitle(id: WorkspaceContainerId) { return TRADING_WORKSPACE_CONTAINERS.find((definition) => definition.id === id)?.title ?? id; }
function workspaceContainerKind(instanceId: string, state?: CanvasWorkspaceState | null): WorkspaceContainerId {
  const stored = state?.instances[instanceId];
  if (stored) return stored;
  return TRADING_WORKSPACE_CONTAINERS.find((definition) => instanceId === definition.id || instanceId.startsWith(`${definition.id}-`))?.id ?? "chart";
}

function nextAvailableContainerInstanceId(kind: WorkspaceContainerId, existingIds: string[]): string {
  const used = new Set(existingIds);
  if (!used.has(kind)) return kind;
  let counter = 2;
  while (used.has(`${kind}-${counter}`)) counter += 1;
  return `${kind}-${counter}`;
}
function containerInstanceTitle(kind: WorkspaceContainerId, instanceId: string, state: CanvasWorkspaceState | null, registry: CanvasRegistry) {
  const matchingIds = (state?.openIds ?? [instanceId]).filter((candidateId) => workspaceContainerKind(candidateId, state) === kind);
  if (kind === "chart") {
    const timeframe = instanceSettings(registry, instanceId).chart.timeframe;
    const matchingTimeframeIds = matchingIds.filter((candidateId) => instanceSettings(registry, candidateId).chart.timeframe === timeframe);
    const duplicateIndex = matchingTimeframeIds.indexOf(instanceId);
    const readableTimeframe = formatChartTimeframe(timeframe).replace(/\b\w/g, (letter) => letter.toUpperCase());
    const base = timeframe === "1d" ? "Daily Chart" : timeframe === "1w" ? "Weekly Chart" : timeframe === "1mo" ? "Monthly Chart" : timeframe === "1y" ? "Yearly Chart" : `${readableTimeframe} Chart`;
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
function runtimeCanvasState(profile: CanvasRegistry, storageKey: string, canvasId: string, requestedInstanceId?: string, useStored = true): CanvasWorkspaceState | null {
  const approved = profile.workspaceStates?.[canvasId] ?? (canvasId === MAIN_CANVAS_ID ? profile.defaultState : undefined) ?? null;
  const state = (useStored ? readCanvasWorkspaceStateByStorageKey(storageKey) : null) ?? approved;
  if (!requestedInstanceId) return state;
  const kind = workspaceContainerKind(requestedInstanceId, state);
  return { groups: {}, instances: { [requestedInstanceId]: kind }, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts: createFocusLayouts([requestedInstanceId]), openIds: [requestedInstanceId] };
}
function normalizeInheritedLayouts(layouts: Record<string, WorkspaceWindowLayout>, ids: string[]) {
  const fallback = createFocusLayouts(ids);
  return Object.fromEntries(ids.map((id) => [id, { ...(layouts[id] ?? fallback[id]), fullscreen: false, minimized: false }]));
}
function focusLayout(source?: WorkspaceWindowLayout): WorkspaceWindowLayout { const scale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale")) || 1; return { fullscreen: true, h: Math.max(320, Math.floor(window.innerHeight / scale) - 62), minimized: false, w: Math.max(680, Math.floor(window.innerWidth / scale)), x: 0, y: 0, z: Math.max(1, source?.z ?? 1) }; }
function offsetLayout(source: WorkspaceWindowLayout, index: number): WorkspaceWindowLayout { const offset = (index % 6) * 18; return { ...source, fullscreen: false, minimized: false, x: offset, y: offset, z: index + 1 }; }

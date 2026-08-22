import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import {
  Activity,
  BarChart3,
  Banknote,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  CircleDollarSign,
  ClipboardList,
  Clock3,
  FolderOpen,
  LayoutGrid,
  PauseCircle,
  Play,
  RefreshCw,
  Save,
  ShieldAlert,
  SkipForward,
  StepForward,
  TableProperties,
  Target,
  TrendingUp,
  WalletCards,
  X,
} from "lucide-react";
import type { Time } from "lightweight-charts";

import { api, query } from "../api/client";
import { ChartPanel, type ChartPayload, type LiveEntryLine } from "../app/components/ChartPanel";
import { DataTable, type BackendQueryPreset, type BackendTableQuery } from "../app/components/DataTable";
import { PageIntro } from "../app/components/PageIntro";
import { Tabs } from "../app/components/Tabs";
import {
  WorkspaceCanvasManager,
  WorkspaceWindow,
  WorkspaceWindowManager,
  type WorkspaceCanvasTarget as LiveCanvasTarget,
  type WorkspaceWindowId as WindowId,
  type WorkspaceWindowLayout as WindowLayout,
} from "../app/components/WorkspaceCanvas";
import type {
  CatalogPayload,
  RecordRow,
  ReviewPayload,
  ScannerSnapshot,
  ScannerSnapshotPayload,
  Scope,
  SignalRow,
} from "../features/live-trading/contracts";
import {
  buildClosedTrade,
  buildLiveEntryLine,
  positionExposure,
  realizedPnlFromTrades,
  reducePosition,
  upsertPosition,
  type OrderRow,
  type PositionRow,
  type StageOrderContext,
  type TradeRow,
} from "../features/live-trading/portfolio";
import {
  buildMarketStateRow,
  buildMarketStateRows,
  emptyScannerQuery,
  enrichLiveCandidate,
  latestLiveChartRow,
  marketStateTableColumns,
  normalizeLiveScannerQuery,
  quoteFromRow,
  rowMatchesBackendQuery,
  scannerQueryFromConditions,
} from "../features/live-trading/scanner";
import {
  addClockMinutes,
  clockTimestampSeconds,
  clockToMinutes,
  dateOffset,
  isAfterClock,
  previousSessionDate,
  rowTimestampSeconds,
  type TradingSession,
} from "../features/live-trading/time";
import {
  LiveNewsDetailPopover,
  LiveNewsSection,
  liveNewsItems,
  newsTickerCount,
  type LiveNewsArticle,
} from "../features/live-trading/LiveNewsPanel";
import { ChartTradePanel, LiveField, LiveSelect } from "../features/live-trading/LiveChartTradePanel";
import { integer, money, numberValue, percent, stringValue } from "../features/live-trading/liveTradingFormat";
import { MetricsDock } from "../features/live-trading/LiveMetricsDock";
import {
  LIVE_FEATURE_GROUPS,
  availableSessionDates,
  castOpenChartPayload,
  dayOpenOnlyChartPayload,
  loadChart,
  openOnlyChartPayload,
  trimChartPayload,
} from "../features/live-trading/liveChartData";
import type { ChartWindow, DecisionState, LiveClockMode, SavedCanvasLayout, ScannerQueryGroup } from "../features/live-trading/liveWorkspaceContracts";
import {
  CORE_WINDOW_IDS,
  buildDefaultCanvasLayout,
  buildLiveWindowSummaries,
  chartOpenAtTime,
  coreWindowTitle,
  liveWorkspaceMinHeight,
  signedMetricTone,
} from "../features/live-trading/liveWorkspacePresentation";

type LivePreloadCheck = {
  expected_sessions: number;
  group: string;
  label: string;
  message?: string;
  missing_sessions: string[];
  ready_sessions: number;
  rows: number;
  status: string;
  timeframe: string;
};

type LivePreloadPayload = {
  checks: LivePreloadCheck[];
  progress: number;
  session_date: string;
  status: string;
};

type LiveNextSignalPayload = {
  complete?: boolean;
  found: boolean;
  last_checked_time?: string;
  next_start_time?: string | null;
  snapshot: ScannerSnapshot;
  steps: number;
};

type LiveNewsSummary = {
  live_news_count: number;
  live_news_items: LiveNewsArticle[];
  live_news_latest_time: string;
  live_news_latest_title: string;
  live_news_recency: string;
  live_news_recent: boolean;
};

type LiveNewsPayload = {
  articles: LiveNewsArticle[];
  bar_time: string;
  by_ticker: Record<string, LiveNewsSummary>;
  session_date: string;
};






const LIVE_SESSION_STORAGE_KEY = "quant-research-workbench.live-trading.session";
const LIVE_LAYOUT_STORAGE_KEY = "quant-research-workbench.live-trading.layout";
const LIVE_LAYOUT_VERSION = 4;
const LIVE_LAYOUTS_STORAGE_KEY = "quant-research-workbench.live-trading.named-layouts";
const LIVE_SHARED_STATE_STORAGE_KEY = "quant-research-workbench.live-trading.shared-state";
const LIVE_SAVED_SIMULATIONS_STORAGE_KEY = "quant-research-workbench.live-trading.saved-simulations";
const LIVE_SETUP_STORAGE_KEY = "quant-research-workbench.live-trading.scanner-queries.v2";
const LIVE_SCANNER_QUERY_STORAGE_KEY = "quant-research-workbench.live-trading.scanner-query.v2";
const LIVE_CHART_VISIBILITY_STORAGE_KEY = "quant-research-workbench.live-trading.chart-visibility.v1";
const LIVE_SIGNAL_SEARCH_BATCH_MINUTES = 10;
const LIVE_STARTING_CASH = 10_000;
const LIVE_PORTFOLIO_EXPANDED_HEIGHT = 360;
const MAIN_DISPLAY_ITEMS = ["indicator.vwap", "indicator.tema_trend", "indicator.macd"];
const LOWER_DISPLAY_ITEMS = ["indicator.vwap"];
const LIVE_SCANNER_COLUMNS = [
  "ticker",
  "bar_time_market",
  "minute_of_day",
  "current_open",
  "last_close",
  "last_open",
  "last_high",
  "last_low",
  "last_vwap",
  "last_day_high_so_far",
  "last_day_low_so_far",
  "last_day_volume_so_far",
  "last_day_dollar_volume_so_far",
  "last_day_open",
  "last_gap_pct",
  "last_return_5",
  "last_volume",
  "last_recent_volume_5",
  "last_transactions",
  "last_transactions_vs_prior_3",
  "last_bearish_volume_divergence_score",
  "last_double_timeframe_bearish_volume_divergence_score",
  "current_open_above_last_2_body_high",
  "spread_bps_abs",
];

const LIVE_SIGNAL_COLUMNS = [
  "ticker",
  "live_news_recency",
  "bar_time_market",
  "live_signal_time",
  "current_open",
  "last_volume",
  "last_return_5",
  "last_transactions",
  "last_transactions_vs_prior_3",
  "live_signal_query",
  "last_close",
  "last_day_volume_so_far",
  "last_day_max_change_pct",
  "last_day_current_change_pct",
  "last_vwap",
  "live_bias",
  "live_reasons",
  "live_risks",
];

const LIVE_MARKET_STATE_COLUMNS = [
  "ticker",
  "live_news_recency",
  "current_open",
  "last_volume",
  "last_day_volume_so_far",
  "last_recent_volume_5",
  "last_return_5",
  "last_gap_pct",
  "last_day_max_change_pct",
  "last_day_current_change_pct",
  "last_close",
  "last_transactions",
  "last_transactions_vs_prior_3",
  "last_day_dollar_volume_so_far",
  "last_day_open",
  "last_day_high_so_far",
  "last_vwap",
  "last_bearish_volume_divergence_score",
];


const DEFAULT_SCANNER_QUERY_GROUPS: ScannerQueryGroup[] = [
  {
    id: "squeeze-up-5m",
    name: "5% Squeeze Up in 5m",
    query: scannerQueryFromConditions([
      { column: "current_open", id: "price", operator: "between", value: "1", valueSecondary: "50" },
      { column: "last_volume", id: "volume", operator: "gt", value: "8000" },
      { column: "last_return_5", id: "return", operator: "gt", value: "0.05" },
      { column: "last_transactions", id: "transactions", operator: "gt", value: "100" },
    ]),
  },
];


export function LiveTradingPage({ onTopbarCenterChange }: { onTopbarCenterChange?: Dispatch<SetStateAction<ReactNode>> }) {
  const canvasId = useMemo(() => new URLSearchParams(window.location.search).get("replayCanvas") || "main", []);
  const isChildCanvas = canvasId !== "main";
  const initialCanvas = useMemo(() => readStoredCanvas(canvasId, isChildCanvas), [canvasId, isChildCanvas]);
  const initialSharedState = useMemo(() => readSharedTradingState(), []);
  const [scope, setScope] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [session, setSession] = useState<TradingSession>(() => readStoredSession() ?? { barTime: "04:00", sessionDate: "" });
  const [started, setStarted] = useState(isChildCanvas);
  const [tradingStarted, setTradingStarted] = useState(false);
  const [scannerQueryGroups, setScannerQueryGroups] = useState<ScannerQueryGroup[]>(readStoredScannerQueryGroups);
  const [scannerQueryName, setScannerQueryName] = useState(() => readStoredScannerQueryName() || DEFAULT_SCANNER_QUERY_GROUPS[0]?.name || "Scanner Query");
  const [snapshot, setSnapshot] = useState<ScannerSnapshot | null>(null);
  const [marketSnapshot, setMarketSnapshot] = useState<ScannerSnapshot | null>(null);
  const [signalRows, setSignalRows] = useState<SignalRow[]>([]);
  const [scannerQuery, setScannerQuery] = useState<BackendTableQuery>(() => normalizeLiveScannerQuery(readStoredScannerQuery()) ?? DEFAULT_SCANNER_QUERY_GROUPS[0]?.query ?? emptyScannerQuery());
  const [preloadStatus, setPreloadStatus] = useState<LivePreloadPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [liveClockMode, setLiveClockMode] = useState<LiveClockMode>("idle");
  const [liveClockMessage, setLiveClockMessage] = useState("");
  const [simulationSaveMessage, setSimulationSaveMessage] = useState("");
  const [secondsPerMinute, setSecondsPerMinute] = useState("10");
  const [lastActionTime, setLastActionTime] = useState("");
  const [startPreloading, setStartPreloading] = useState(false);
  const [selectedRow, setSelectedRow] = useState<Record<string, unknown> | null>(null);
  const [mainTimeframe, setMainTimeframe] = useState("1m");
  const [mainVisibleColumns, setMainVisibleColumns] = useState<string[]>(MAIN_DISPLAY_ITEMS);
  const [compactVisibleColumns, setCompactVisibleColumns] = useState<string[]>(LOWER_DISPLAY_ITEMS);
  const [headerCollapsed, setHeaderCollapsed] = useState(true);
  const [lowerChartVisibility, setLowerChartVisibility] = useState(readStoredLiveChartVisibility);
  const showDayChart = lowerChartVisibility.day;
  const showFiveMinuteChart = lowerChartVisibility.fiveMinute;
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>(initialSharedState.decisions);
  const [orders, setOrders] = useState<OrderRow[]>(initialSharedState.orders);
  const [positions, setPositions] = useState<PositionRow[]>(initialSharedState.positions);
  const [trades, setTrades] = useState<TradeRow[]>(initialSharedState.trades);
  const [portfolioTab, setPortfolioTab] = useState("Trades");
  const [portfolioDetailsOpen, setPortfolioDetailsOpen] = useState(false);
  const [tradeDraft, setTradeDraft] = useState({ limit: "", quantity: "3000", side: "BUY" as "BUY" | "SELL", stop: "", type: "LIMIT" });
  const [layouts, setLayouts] = useState<Record<WindowId, WindowLayout>>(initialCanvas.layouts);
  const [openWindows, setOpenWindows] = useState<WindowId[]>(initialCanvas.windows);
  const [chartWindows, setChartWindows] = useState<ChartWindow[]>(initialCanvas.chartWindows);
  const [layoutName, setLayoutName] = useState("Momentum Desk");
  const [savedLayouts, setSavedLayouts] = useState<SavedCanvasLayout[]>(readSavedCanvasLayouts);
  const [selectedLayoutName, setSelectedLayoutName] = useState("");
  const [canvasTargetsVersion, setCanvasTargetsVersion] = useState(0);
  const canvasRemovedRef = useRef(false);
  const ordersRef = useRef(orders);
  const positionsRef = useRef(positions);
  const tradesRef = useRef(trades);
  const seekCancelRef = useRef(0);
  const paceRunRef = useRef(0);
  const liveClockModeRef = useRef<LiveClockMode>("idle");
  const warmedChartCacheKeysRef = useRef(new Set<string>());
  const lastChartOpenRef = useRef<{ id: string; openedAt: number } | null>(null);
  const scannerQueryKey = useMemo(() => JSON.stringify(scannerQuery), [scannerQuery]);

  useEffect(() => {
    liveClockModeRef.current = liveClockMode;
  }, [liveClockMode]);

  useEffect(() => {
    let active = true;
    api<Scope>("/api/market-data/scope").then((payload) => {
      if (!active) return;
      setScope(payload);
      setSession((current) => ({ ...current, sessionDate: current.sessionDate || payload.end_date || payload.start_date }));
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!scope) return;
    let active = true;
    api<ReviewPayload>(`/api/market-data/review${query({ processed_root: scope.processed_root, start_date: scope.start_date, end_date: scope.end_date })}`).then((payload) => {
      if (!active) return;
      setReview(payload);
      const latestSession = availableSessionDates(payload.records).at(-1);
      setSession((current) => ({ ...current, sessionDate: current.sessionDate || latestSession || "" }));
    });
    api<CatalogPayload>(`/api/market-data/catalog${query({ processed_root: scope.processed_root })}`).then((payload) => {
      if (active) setCatalog(payload);
    });
    return () => {
      active = false;
    };
  }, [scope]);

  useEffect(() => {
    window.localStorage.setItem(LIVE_SETUP_STORAGE_KEY, JSON.stringify(scannerQueryGroups));
  }, [scannerQueryGroups]);

  useEffect(() => {
    window.localStorage.setItem(LIVE_SCANNER_QUERY_STORAGE_KEY, JSON.stringify(scannerQuery));
  }, [scannerQuery]);

  useEffect(() => {
    window.localStorage.setItem(`${LIVE_SCANNER_QUERY_STORAGE_KEY}.name`, scannerQueryName);
  }, [scannerQueryName]);

  useEffect(() => {
    window.localStorage.setItem(LIVE_CHART_VISIBILITY_STORAGE_KEY, JSON.stringify(lowerChartVisibility));
  }, [lowerChartVisibility]);

  const sessions = useMemo(() => availableSessionDates(review?.records ?? []), [review]);
  const selectedTicker = stringValue(selectedRow, "ticker");
  const selectedOpen = numberValue(selectedRow, "current_open") || numberValue(selectedRow, "open");
  const selectedProfile = selectedRow ? enrichLiveCandidate(selectedRow, scannerQueryName) : null;
  const scannerRows = useMemo(
    () =>
      (snapshot?.rows ?? [])
        .map((row) => enrichLiveCandidate(row, scannerQueryName))
        .sort((a, b) => numberValue(b, "live_priority") - numberValue(a, "live_priority")),
    [scannerQueryName, snapshot]
  );
  const marketRows = useMemo(
    () => buildMarketStateRows(marketSnapshot?.rows ?? []),
    [marketSnapshot]
  );
  const portfolioMetrics = useMemo(
    () => buildPortfolioMetrics({ orders, positions, trades }),
    [orders, positions, trades]
  );
  const globalMetrics = useMemo(
    () => buildGlobalLiveMetrics({ decisions, lastActionTime, liveClockMode, preloadStatus, scannerRows: signalRows, secondsPerMinute, session, snapshot }),
    [decisions, lastActionTime, liveClockMode, preloadStatus, secondsPerMinute, session, signalRows, snapshot]
  );
  const liveWindowSummaries = useMemo(
    () => buildLiveWindowSummaries(openWindows, chartWindows, layouts),
    [chartWindows, layouts, openWindows]
  );
  const workspaceMinHeight = useMemo(
    () => liveWorkspaceMinHeight(openWindows, layouts, headerCollapsed),
    [headerCollapsed, layouts, openWindows]
  );
  const canvasTargets = useMemo(() => listKnownLiveCanvases(canvasId), [canvasId, canvasTargetsVersion]);
  const topbarWorkspaceInfo = useMemo(() => {
    const knownCanvasCount = canvasTargets.length || 1;
    const canvasLabel = isChildCanvas ? `Child canvas ${canvasId.replace(/^canvas-/, "")}` : "Main canvas";
    const layoutLabel = selectedLayoutName || layoutName || "Unsaved layout";
    const pageLabel = `${knownCanvasCount} canvas${knownCanvasCount === 1 ? "" : "es"}`;
    const windowNames = liveWindowSummaries.map((windowItem) => windowItem.title);
    const windowLabel = windowNames.length ? windowNames.slice(0, 4).join(", ") : "No containers";
    const extraWindowCount = Math.max(0, windowNames.length - 4);
    return {
      detail: `${layoutLabel} - ${pageLabel} - ${windowLabel}${extraWindowCount ? ` +${extraWindowCount}` : ""}`,
      title: `Replay Trading - ${canvasLabel}`,
    };
  }, [canvasId, canvasTargets.length, isChildCanvas, layoutName, liveWindowSummaries, selectedLayoutName]);

  useEffect(() => {
    if (!started || !onTopbarCenterChange) {
      onTopbarCenterChange?.(null);
      return;
    }
    onTopbarCenterChange(
      <button className="live-topbar-session" onClick={() => setHeaderCollapsed((value) => !value)} type="button">
        <span>{topbarWorkspaceInfo.title}</span>
        <strong>{topbarWorkspaceInfo.detail}</strong>
        {headerCollapsed ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
      </button>
    );
    return () => onTopbarCenterChange(null);
  }, [headerCollapsed, onTopbarCenterChange, started, topbarWorkspaceInfo]);

  useEffect(() => {
    if (!selectedRow && scannerRows.length) setSelectedRow(scannerRows[0]);
  }, [scannerRows, selectedRow]);

  useEffect(() => {
    ordersRef.current = orders;
  }, [orders]);

  useEffect(() => {
    positionsRef.current = positions;
  }, [positions]);

  useEffect(() => {
    tradesRef.current = trades;
  }, [trades]);

  useEffect(() => {
    if (!started) return;
    const payload = { decisions, orders, positions, trades };
    window.localStorage.setItem(LIVE_SHARED_STATE_STORAGE_KEY, JSON.stringify(payload));
  }, [decisions, orders, positions, started, trades]);

  useEffect(() => {
    if (canvasRemovedRef.current) return;
    const payload = { chartWindows, layoutVersion: LIVE_LAYOUT_VERSION, layouts, windows: openWindows };
    window.localStorage.setItem(canvasStorageKey(canvasId), JSON.stringify(payload));
    setCanvasTargetsVersion((version) => version + 1);
  }, [canvasId, chartWindows, layouts, openWindows]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === LIVE_SHARED_STATE_STORAGE_KEY && event.newValue) {
        try {
          const parsed = JSON.parse(event.newValue) as { decisions?: Record<string, DecisionState>; orders?: OrderRow[]; positions?: PositionRow[]; trades?: TradeRow[] };
          const incomingOrders = parsed.orders ?? [];
          const incomingPositions = (parsed.positions ?? []).filter((row) => row.quantity > 0);
          const incomingTrades = parsed.trades ?? [];
          const hasNewOrders = incomingOrders.length > ordersRef.current.length;
          const hasNewTrades = incomingTrades.length > tradesRef.current.length;
          const hasPositionState = JSON.stringify(incomingPositions) !== JSON.stringify(positionsRef.current);
          const isInitialSync = ordersRef.current.length === 0 && tradesRef.current.length === 0 && !positionsRef.current.length;
          if (hasNewOrders || hasNewTrades || hasPositionState || isInitialSync) {
            setDecisions(parsed.decisions ?? {});
            setOrders(incomingOrders);
            setPositions(incomingPositions);
            setTrades(incomingTrades);
          }
        } catch {
          // Ignore malformed cross-tab state.
        }
      }
      if (event.key === canvasStorageKey(canvasId) && event.newValue) {
        try {
          const parsed = JSON.parse(event.newValue) as Partial<{ chartWindows: ChartWindow[]; layoutVersion: number; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] }> | null;
          if (!parsed || parsed.layoutVersion !== LIVE_LAYOUT_VERSION) return;
          setLayouts((current) => ({ ...current, ...(parsed.layouts ?? {}) }));
          setOpenWindows(Array.isArray(parsed.windows) ? parsed.windows : []);
          setChartWindows(Array.isArray(parsed.chartWindows) ? parsed.chartWindows : []);
        } catch {
          // Ignore malformed canvas state from another tab.
        }
      }
      if (event.key === canvasStorageKey(canvasId) && event.newValue === null) {
        canvasRemovedRef.current = true;
        const defaults = buildDefaultCanvasLayout(isChildCanvas);
        setLayouts(defaults.layouts);
        setOpenWindows([]);
        setChartWindows([]);
        setStarted(false);
        setTradingStarted(false);
      }
      if (event.key?.startsWith(`${LIVE_LAYOUT_STORAGE_KEY}.`)) {
        setCanvasTargetsVersion((version) => version + 1);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [canvasId, isChildCanvas]);

  useEffect(() => {
    if (!started || !scope || !session.sessionDate || isChildCanvas) return;
    let canceled = false;
    const processedRoot = scope.processed_root;
    async function loadInitialScanner() {
      setSession((current) => ({ ...current, barTime: "04:00" }));
      const initialScanner = await loadScannerAt("04:00", { warmCharts: false });
      await warmChartCacheForRows(initialScanner?.snapshot.rows ?? []);
      if (canceled) return;
      setLiveClockMode("ready");
      setLiveClockMessage("Data is ready. Initial scanner is loaded from the first bar. Press Start Trading to begin paced simulation.");
    }
    async function preloadSessionData() {
      setLoading(true);
      setPreloadStatus(null);
      setSnapshot(null);
      setMarketSnapshot(null);
      setSelectedRow(null);
      setLastActionTime("");
      warmedChartCacheKeysRef.current.clear();
      setTradingStarted(false);
      setLiveClockMode("loading_data");
      setLiveClockMessage("Loading provider data for the selected trading day.");
      try {
        const payload =
          preloadStatus?.session_date === session.sessionDate && preloadStatus.status === "ready"
            ? preloadStatus
            : await api<LivePreloadPayload>(`/api/live-trading/preload${query({ processed_root: processedRoot, session_date: session.sessionDate })}`);
        if (canceled) return;
        setPreloadStatus(payload);
        if (payload.status === "ready") {
          await loadInitialScanner();
        } else {
          setLiveClockMode("paused");
          setLiveClockMessage("Some provider artifacts are missing. Review the preload status before starting.");
        }
      } catch (requestError) {
        if (canceled) return;
        setLiveClockMode("paused");
        setLiveClockMessage(requestError instanceof Error ? requestError.message : "Data preload failed.");
      } finally {
        if (!canceled) setLoading(false);
      }
    }
    void preloadSessionData();
    return () => {
      canceled = true;
    };
  }, [isChildCanvas, scope, started, session.sessionDate]);

  useEffect(() => {
    if (!started || !tradingStarted || !scope || !session.sessionDate || liveClockMode !== "running") return;
    const seconds = Math.max(1, Number(secondsPerMinute) || 10);
    const runId = ++paceRunRef.current;
    const timer = window.setTimeout(() => {
      if (paceRunRef.current !== runId || liveClockModeRef.current !== "running") return;
      const nextTime = addClockMinutes(session.barTime, 1);
      if (!nextTime || isAfterClock(nextTime, "20:00")) {
        if (paceRunRef.current !== runId || liveClockModeRef.current !== "running") return;
        setLiveClockMode("complete");
        setLiveClockMessage("Session clock reached the end of supported trading time.");
        return;
      }
      if (paceRunRef.current !== runId || liveClockModeRef.current !== "running") return;
      setSession((current) => ({ ...current, barTime: nextTime }));
      loadScannerAt(nextTime);
    }, seconds * 1000);
    return () => window.clearTimeout(timer);
  }, [liveClockMode, scope, secondsPerMinute, session.barTime, session.sessionDate, started, tradingStarted]);

  useEffect(() => {
    if (!started || tradingStarted || !scope || !session.sessionDate || liveClockMode !== "ready") return;
    void loadScannerAt(session.barTime || "04:00");
  }, [scannerQueryKey]);

  async function startTrading() {
    if (!scope || startPreloading || loading) return;
    const nextSession = { ...session, barTime: "04:00", sessionDate: session.sessionDate || sessions.at(-1) || "" };
    if (!nextSession.sessionDate) return;
    canvasRemovedRef.current = false;
    setStartPreloading(true);
    setLoading(true);
    setPreloadStatus(null);
    setLiveClockMode("loading_data");
    setLiveClockMessage("Loading bars and Benzinga news before opening the workspace.");
    try {
      const payload = await api<LivePreloadPayload>(`/api/live-trading/preload${query({ processed_root: scope.processed_root, session_date: nextSession.sessionDate })}`);
      setPreloadStatus(payload);
      if (payload.status !== "ready") {
        setLiveClockMode("paused");
        setLiveClockMessage("Some provider artifacts are missing. Review the preload status before starting.");
        return;
      }
    } catch (requestError) {
      setLiveClockMode("paused");
      setLiveClockMessage(requestError instanceof Error ? requestError.message : "Data preload failed.");
      return;
    } finally {
      setLoading(false);
      setStartPreloading(false);
    }
    window.localStorage.setItem(LIVE_SESSION_STORAGE_KEY, JSON.stringify(nextSession));
    window.localStorage.removeItem(LIVE_SHARED_STATE_STORAGE_KEY);
    setSession(nextSession);
    setDecisions({});
    setOrders([]);
    setPositions([]);
    setTrades([]);
    setSignalRows([]);
    setMarketSnapshot(null);
    setSimulationSaveMessage("");
    setLastActionTime("");
    setTradingStarted(false);
    setStarted(true);
  }

  function startTradingSimulation() {
    if (liveClockMode !== "ready" || loading) return;
    setTradingStarted(true);
    beginTradingClock();
  }

  function beginTradingClock() {
    if (liveClockMode === "loading_data" || liveClockMode === "seeking") return;
    paceRunRef.current += 1;
    seekCancelRef.current += 1;
    setLiveClockMode("running");
    setLiveClockMessage("Simulation is pacing from the current bar. Use Next Signal to fast-forward.");
  }

  function pauseTradingClock() {
    paceRunRef.current += 1;
    seekCancelRef.current += 1;
    setLiveClockMode("paused");
    setLiveClockMessage("Live clock paused.");
  }

  function refreshCurrentBar() {
    loadScannerAt(session.barTime);
  }

  function advanceOneBar() {
    paceRunRef.current += 1;
    seekCancelRef.current += 1;
    const nextTime = addClockMinutes(session.barTime, 1);
    if (!nextTime || isAfterClock(nextTime, "20:00")) {
      setLiveClockMode("complete");
      setLiveClockMessage("Session clock reached the end of supported trading time.");
      return;
    }
    setLiveClockMode("paused");
    setSession((current) => ({ ...current, barTime: nextTime }));
    loadScannerAt(nextTime);
  }

  async function seekNextSignal() {
    if (!tradingStarted) return;
    paceRunRef.current += 1;
    const runId = seekCancelRef.current + 1;
    seekCancelRef.current = runId;
    setLiveClockMode("seeking");
    setLiveClockMessage("Fast-forwarding to the next scanner signal.");
    try {
      const searchStart = lastActionTime === session.barTime ? addClockMinutes(session.barTime, 1) || session.barTime : session.barTime;
      const found = await runUntilNextAction(searchStart, () => seekCancelRef.current !== runId);
      if (seekCancelRef.current !== runId) return;
      setLiveClockMode(found ? "running" : "complete");
      setLiveClockMessage(found ? "Scanner signal found. Live clock is pacing from this timestamp." : "No scanner signal found before the session cutoff.");
    } catch (requestError) {
      if (seekCancelRef.current !== runId) return;
      setLiveClockMode("paused");
      setLiveClockMessage(requestError instanceof Error ? requestError.message : "Scanner fast-forward failed.");
    }
  }

  function toggleLiveClock() {
    if (!tradingStarted) {
      startTradingSimulation();
      return;
    }
    if (liveClockMode === "ready" || liveClockMode === "idle" || liveClockMode === "complete") {
      beginTradingClock();
      return;
    }
    if (liveClockMode === "running" || liveClockMode === "seeking") {
      pauseTradingClock();
      return;
    }
    beginTradingClock();
  }

  async function loadScannerAt(barTime: string, options: { warmCharts?: boolean } = {}) {
    if (!scope || !session.sessionDate) return null;
    setLoading(true);
    setError("");
    try {
      const signalPayload = await api<ScannerSnapshotPayload>(
        `/api/market-data/scanner-snapshot${query({
          processed_root: scope.processed_root,
          session_date: session.sessionDate,
          timeframe: "1m",
          bar_time: barTime,
          feature_groups: LIVE_FEATURE_GROUPS.join(","),
          columns: LIVE_SCANNER_COLUMNS.join(","),
          table_query: JSON.stringify(scannerQuery),
          row_limit: 1000,
        })}`
      );
      const marketPayload = await loadMarketStateAt(barTime);
      const newsPayload = await loadNewsAt(barTime, [
        ...signalPayload.snapshot.rows.map((row) => stringValue(row, "ticker")),
        ...(marketPayload?.snapshot.rows ?? []).map((row) => stringValue(row, "ticker")),
      ]);
      const enrichedRows = signalPayload.snapshot.rows
        .map((row) => mergeLiveNews(row, newsPayload))
        .map((row) => enrichLiveCandidate(row, scannerQueryName))
        .filter((row) => rowMatchesBackendQuery(row, scannerQuery));
      const enrichedSnapshot = {
        ...signalPayload.snapshot,
        columns: appendNewsColumns(signalPayload.snapshot.columns),
        rows: enrichedRows,
      };
      if (marketPayload?.snapshot) {
        setMarketSnapshot({
          ...marketPayload.snapshot,
          columns: appendNewsColumns(marketPayload.snapshot.columns),
          rows: marketPayload.snapshot.rows.map((row) => mergeLiveNews(row, newsPayload)),
        });
      }
      const firstRow = enrichedRows.find((row) => stringValue(row, "live_setup_group")) ?? null;
      setSnapshot(enrichedSnapshot);
      setSelectedRow(firstRow);
      if (enrichedRows.length) appendSignalRows(enrichedRows, barTime);
      if (options.warmCharts !== false) void warmChartCacheForRows(enrichedRows);
      if (firstRow) setLastActionTime(barTime);
      return { firstRow, marketSnapshot: marketPayload?.snapshot ?? null, snapshot: enrichedSnapshot };
    } catch (requestError) {
      setSnapshot(null);
      setMarketSnapshot(null);
      setSelectedRow(null);
      setError(requestError instanceof Error ? requestError.message : "Scanner request failed.");
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function runUntilNextAction(startTime: string, shouldStop: () => boolean) {
    if (!scope || !session.sessionDate || shouldStop() || isAfterClock(startTime, "20:00")) return false;
    setLoading(true);
    setError("");
    try {
      let searchStart = startTime;
      let checkedMinutes = 0;
      while (!shouldStop() && !isAfterClock(searchStart, "20:00")) {
        const payload = await api<LiveNextSignalPayload>(
          `/api/live-trading/next-signal${query({
            processed_root: scope.processed_root,
            session_date: session.sessionDate,
            start_time: searchStart,
            feature_groups: LIVE_FEATURE_GROUPS.join(","),
            columns: LIVE_SCANNER_COLUMNS.join(","),
            table_query: JSON.stringify(scannerQuery),
            row_limit: 1000,
            max_steps: LIVE_SIGNAL_SEARCH_BATCH_MINUTES,
          })}`
        );
        if (shouldStop()) return false;
        checkedMinutes += payload.steps || 0;
        const checkedTime = payload.last_checked_time || payload.snapshot.bar_time || searchStart;
        setSession((current) => ({ ...current, barTime: checkedTime }));
        let marketPayload: ScannerSnapshotPayload | null = null;
        try {
          marketPayload = await loadMarketStateAt(checkedTime);
        } catch {
          // The signal search is still usable if the market-state snapshot fails.
        }
        const newsPayload = await loadNewsAt(checkedTime, [
          ...payload.snapshot.rows.map((row) => stringValue(row, "ticker")),
          ...(marketPayload?.snapshot.rows ?? []).map((row) => stringValue(row, "ticker")),
        ]);
        const enrichedRows = payload.snapshot.rows
          .map((row) => mergeLiveNews(row, newsPayload))
          .map((row) => enrichLiveCandidate(row, scannerQueryName))
          .filter((row) => rowMatchesBackendQuery(row, scannerQuery));
        setSnapshot({ ...payload.snapshot, columns: appendNewsColumns(payload.snapshot.columns), rows: enrichedRows });
        if (marketPayload?.snapshot) {
          setMarketSnapshot({
            ...marketPayload.snapshot,
            columns: appendNewsColumns(marketPayload.snapshot.columns),
            rows: marketPayload.snapshot.rows.map((row) => mergeLiveNews(row, newsPayload)),
          });
        }
        const firstRow = enrichedRows.find((row) => stringValue(row, "live_setup_group")) ?? enrichedRows[0] ?? null;
        setSelectedRow(firstRow);
        setLiveClockMessage(`Searching scanner signals at ${checkedTime} ET (${checkedMinutes} minutes checked).`);
        await new Promise((resolve) => window.requestAnimationFrame(resolve));
        if (payload.found && firstRow) {
          appendSignalRows(enrichedRows, payload.snapshot.bar_time || checkedTime);
          await warmChartCacheForRows(enrichedRows);
          setLastActionTime(payload.snapshot.bar_time);
          return true;
        }
        if (payload.complete) return false;
        searchStart = payload.next_start_time || addClockMinutes(checkedTime, 1) || checkedTime;
      }
      return false;
    } catch (requestError) {
      setSnapshot(null);
      setMarketSnapshot(null);
      setSelectedRow(null);
      setError(requestError instanceof Error ? requestError.message : "Scanner fast-forward failed.");
      return false;
    } finally {
      setLoading(false);
    }
  }

  function markDecision(state: DecisionState) {
    if (!selectedTicker) return;
    setDecisions((current) => ({ ...current, [selectedTicker]: state }));
    if (state === "approved") stageOrder("BUY", "STAGED");
  }

  function stageOrder(side = tradeDraft.side, status = "STAGED", context?: Partial<StageOrderContext>) {
    const symbol = context?.symbol || selectedTicker;
    if (!symbol) return;
    const contextRow = context?.row ?? selectedProfile;
    const requestedQuantity = Math.max(0, Math.floor(context?.quantity ?? Number(tradeDraft.quantity) ?? 0));
    const heldPosition = side === "SELL" ? positionsRef.current.find((row) => row.symbol === symbol) : undefined;
    const quantity = side === "SELL" ? Math.min(requestedQuantity, Math.floor(heldPosition?.quantity ?? 0)) : requestedQuantity;
    if (quantity <= 0) return;
    const draftLimit = Number(tradeDraft.limit);
    const draftStop = Number(tradeDraft.stop);
    const limit = context?.limit ?? (Number.isFinite(draftLimit) && draftLimit > 0 ? draftLimit : numberValue(contextRow, "suggested_entry") || selectedOpen);
    const stop = context?.stop ?? (Number.isFinite(draftStop) && draftStop > 0 ? draftStop : numberValue(contextRow, "suggested_stop"));
    const type = context?.type ?? tradeDraft.type;
    const mark = context?.mark ?? (selectedOpen || limit);
    const order: OrderRow = {
      id: `${Date.now()}-${symbol}-${side}`,
      limit,
      quantity,
      side,
      status,
      stop,
      symbol,
      timestamp: `${session.sessionDate} ${session.barTime}`,
      type,
    };
    setOrders((current) => [order, ...current]);
    if (side === "BUY" && status !== "CANCELED") {
      setPositions((current) => upsertPosition(current, symbol, quantity, limit, stop, mark, session.sessionDate, session.barTime));
    } else if (side === "SELL" && status !== "CANCELED" && heldPosition) {
      setTrades((current) => [buildClosedTrade(heldPosition, quantity, mark, session.sessionDate, session.barTime, order.id), ...current]);
      setPositions((current) => reducePosition(current, symbol, quantity, mark));
    }
  }

  function appendSignalRows(rows: Record<string, unknown>[], barTime: string) {
    const stampedRows = rows.map((row) => ({
      ...buildMarketStateRow(row),
      live_signal_id: `${stringValue(row, "ticker") || "unknown"}|${rowTimestampSeconds(row, session.sessionDate, barTime) ?? barTime}|${scannerQueryName}`,
      live_signal_query: scannerQueryName || "Scanner Query",
      live_signal_time: barTime,
    }));
    setSignalRows((current) => {
      const existingIds = new Set(current.map((row) => String(row.live_signal_id || "")));
      const fresh = stampedRows.filter((row) => !existingIds.has(String(row.live_signal_id || "")));
      if (!fresh.length) return current;
      return [...fresh, ...current].slice(0, 1000);
    });
  }

  async function loadMarketStateAt(barTime: string) {
    if (!scope || !session.sessionDate) return null;
    const payload = await api<ScannerSnapshotPayload>(
      `/api/market-data/scanner-snapshot${query({
        processed_root: scope.processed_root,
        session_date: session.sessionDate,
        timeframe: "1m",
        bar_time: barTime,
        feature_groups: LIVE_FEATURE_GROUPS.join(","),
        columns: LIVE_SCANNER_COLUMNS.join(","),
        row_limit: 5000,
        table_query: JSON.stringify({
          conditions: [],
          matchMode: "all",
          sortColumn: "last_day_volume_so_far",
          sortDirection: "desc",
        }),
      })}`
    );
    return payload;
  }

  async function loadNewsAt(barTime: string, tickers: string[]) {
    if (!scope || !session.sessionDate) return null;
    const uniqueTickers = Array.from(new Set(tickers.map((ticker) => ticker.trim().toUpperCase()).filter(Boolean))).slice(0, 5000);
    try {
      return await api<LiveNewsPayload>("/api/live-trading/news-at", {
        body: JSON.stringify({
          bar_time: barTime,
          processed_root: scope.processed_root,
          session_date: session.sessionDate,
          tickers: uniqueTickers,
        }),
        method: "POST",
      });
    } catch {
      return null;
    }
  }

  const markPositionToMarket = useCallback((symbol: string, mark: number) => {
    if (!symbol || !Number.isFinite(mark) || mark <= 0) return;
    setPositions((current) =>
      current.map((position) => {
        if (position.symbol !== symbol) return position;
        const unrealizedPnl = (mark - position.avg_price) * position.quantity;
        const unrealizedPnlPct = position.avg_price > 0 ? (mark / position.avg_price) - 1 : 0;
        if (
          Math.abs(position.mark - mark) < 0.000001 &&
          Math.abs(position.unrealized_pnl - unrealizedPnl) < 0.000001 &&
          Math.abs(position.unrealized_pnl_pct - unrealizedPnlPct) < 0.000001
        ) {
          return position;
        }
        return {
          ...position,
          mark,
          unrealized_pnl: unrealizedPnl,
          unrealized_pnl_pct: unrealizedPnlPct,
        };
      })
    );
  }, []);

  function saveScannerQueryGroup(name: string, savedQuery: BackendTableQuery) {
    const trimmedName = name.trim() || "Scanner Query";
    const id = stableScannerQueryId(trimmedName);
    const normalizedQuery = normalizeLiveScannerQuery(savedQuery) ?? savedQuery;
    setScannerQueryGroups((current) => [
      { id, name: trimmedName, query: normalizedQuery },
      ...current.filter((item) => item.id !== id && item.name !== trimmedName),
    ]);
    setScannerQuery(normalizedQuery);
    setScannerQueryName(trimmedName);
  }

  function deleteScannerQueryGroup(id: string) {
    setScannerQueryGroups((current) => current.filter((item) => item.id !== id));
  }

  async function warmChartCacheForRows(rows: Record<string, unknown>[]) {
    if (!scope || !session.sessionDate || !rows.length) return;
    const tickers = Array.from(new Set(rows.map((row) => stringValue(row, "ticker")).filter(Boolean)))
      .filter((ticker) => !warmedChartCacheKeysRef.current.has(`${session.sessionDate}:${ticker}`))
      .slice(0, 24);
    if (!tickers.length) return;
    try {
      await api(
        `/api/live-trading/warm-charts${query({
          processed_root: scope.processed_root,
          session_date: session.sessionDate,
          tickers: tickers.join(","),
          max_tickers: tickers.length,
        })}`
      );
      tickers.forEach((ticker) => warmedChartCacheKeysRef.current.add(`${session.sessionDate}:${ticker}`));
    } catch {
      // Chart cache warming is an optimization; chart requests still work without it.
    }
  }

  function updateLayout(id: WindowId, patch: Partial<WindowLayout>) {
    setLayouts((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }

  function bringWindowForward(id: WindowId) {
    setLayouts((current) => {
      const topZ = Math.max(0, ...Object.values(current).map((layout) => layout.z));
      if (current[id].z >= topZ) return current;
      return { ...current, [id]: { ...current[id], z: topZ + 1 } };
    });
  }

  function openChartForRow(row: Record<string, unknown>) {
    const ticker = stringValue(row, "ticker").trim().toUpperCase();
    if (!ticker) return;
    const id = `chart-${ticker}`;
    const now = window.performance.now();
    if (lastChartOpenRef.current?.id === id && now - lastChartOpenRef.current.openedAt < 250) return;
    lastChartOpenRef.current = { id, openedAt: now };
    const chartRow = row.ticker === ticker ? row : { ...row, ticker };
    setSelectedRow(chartRow);
    setChartWindows((current) => [{ id, row: chartRow, ticker }, ...current.filter((chart) => chart.id !== id)]);
    setOpenWindows((current) => [id, ...current.filter((windowId) => windowId !== id)]);
    setLayouts((current) => {
      const chartDefaults = current.chart ?? buildDefaultCanvasLayout(false).layouts.chart;
      const existingChartIds = Object.keys(current).filter((layoutId) => layoutId.startsWith("chart-") && layoutId !== id);
      const shifted = Object.fromEntries(
        Object.entries(current).map(([layoutId, layout]) => {
          const shiftedIndex = existingChartIds.indexOf(layoutId);
          return shiftedIndex >= 0
            ? [layoutId, { ...layout, h: chartDefaults.h, w: chartDefaults.w, x: chartDefaults.x + (shiftedIndex + 1) * (chartDefaults.w + 10), y: chartDefaults.y, z: Math.max(1, layout.z - 1) }]
            : [layoutId, layout];
        })
      ) as Record<WindowId, WindowLayout>;
      return { ...shifted, [id]: { ...chartDefaults, x: chartDefaults.x, z: Math.max(0, ...Object.values(current).map((layout) => layout.z)) + 1 } };
    });
  }

  function closeWindow(id: WindowId) {
    setOpenWindows((current) => current.filter((windowId) => windowId !== id));
    setChartWindows((current) => current.filter((chart) => chart.id !== id));
  }

  function moveWindowToCanvas(windowId: WindowId, targetCanvasId: string) {
    if (targetCanvasId === canvasId) {
      updateLayout(windowId, { minimized: false });
      bringWindowForward(windowId);
      return;
    }
    const targetState = readCanvasLayoutState(targetCanvasId);
    const sourceLayout = layouts[windowId] ?? buildDefaultCanvasLayout(targetCanvasId !== "main").layouts.chart;
    const chart = chartWindows.find((item) => item.id === windowId);
    const targetLayouts = {
      ...targetState.layouts,
      [windowId]: { ...sourceLayout, minimized: false, z: Math.max(0, ...Object.values(targetState.layouts).map((layout) => layout.z)) + 1 },
    };
    const targetChartWindows = chart
      ? [chart, ...targetState.chartWindows.filter((item) => item.id !== chart.id)]
      : targetState.chartWindows.filter((item) => item.id !== windowId);
    writeCanvasState(targetCanvasId, {
      chartWindows: targetChartWindows,
      layouts: targetLayouts,
      windows: [windowId, ...targetState.windows.filter((id) => id !== windowId)],
    });
    closeWindow(windowId);
    setCanvasTargetsVersion((version) => version + 1);
  }

  function createChildCanvas(windowId?: WindowId) {
    const nextCanvasId = `canvas-${Date.now()}`;
    writeCanvasState(nextCanvasId, buildDefaultCanvasLayout(true));
    if (windowId) moveWindowToCanvas(windowId, nextCanvasId);
    setCanvasTargetsVersion((version) => version + 1);
    openCanvasInNewTab(nextCanvasId);
  }

  function openCanvasInNewTab(targetCanvasId: string) {
    const url = new URL(window.location.href);
    url.searchParams.set("replayCanvas", targetCanvasId);
    url.hash = "replay-trading";
    window.open(url.toString(), "_blank", "noopener,noreferrer");
  }

  function removeCanvas(targetCanvasId: string) {
    if (targetCanvasId === "main" || targetCanvasId === canvasId) return;
    window.localStorage.removeItem(canvasStorageKey(targetCanvasId));
    window.localStorage.removeItem(canvasTransferKey(targetCanvasId));
    setCanvasTargetsVersion((version) => version + 1);
  }

  function saveNamedLayout() {
    const name = layoutName.trim() || "Momentum Desk";
    const nextLayout: SavedCanvasLayout = { chartWindows, layoutVersion: LIVE_LAYOUT_VERSION, layouts, name, windows: openWindows };
    setSavedLayouts((current) => {
      const next = [nextLayout, ...current.filter((item) => item.name !== name)];
      window.localStorage.setItem(LIVE_LAYOUTS_STORAGE_KEY, JSON.stringify(next));
      return next;
    });
    setSelectedLayoutName(name);
  }

  function loadNamedLayout(name: string) {
    setSelectedLayoutName(name);
    const saved = savedLayouts.find((item) => item.name === name);
    if (!saved) return;
    setLayouts(saved.layouts);
    setOpenWindows(saved.windows);
    setChartWindows(saved.chartWindows);
  }

  function saveSimulation() {
    const savedAt = new Date().toISOString();
    const simulation = {
      decisions,
      id: `${session.sessionDate || "session"}-${savedAt}`,
      lastActionTime,
      orders,
      positions,
      savedAt,
      scannerQuery,
      scannerQueryName,
      session,
      snapshot,
    };
    try {
      const previous = JSON.parse(window.localStorage.getItem(LIVE_SAVED_SIMULATIONS_STORAGE_KEY) || "[]") as unknown[];
      window.localStorage.setItem(LIVE_SAVED_SIMULATIONS_STORAGE_KEY, JSON.stringify([simulation, ...previous].slice(0, 50)));
      setSimulationSaveMessage(`Saved ${session.sessionDate || "simulation"} at ${session.barTime} ET.`);
    } catch {
      setSimulationSaveMessage("Could not save this simulation in browser storage.");
    }
  }

  function closeSession() {
    paceRunRef.current += 1;
    seekCancelRef.current += 1;
    setStarted(false);
    setTradingStarted(false);
    setLiveClockMode("idle");
    setLiveClockMessage("");
    setSnapshot(null);
    setSelectedRow(null);
  }

  function togglePortfolioDetails() {
    setPortfolioDetailsOpen((isOpen) => {
      const nextOpen = !isOpen;
      setLayouts((current) => {
        const defaults = buildDefaultCanvasLayout(false).layouts;
        const topZ = Math.max(0, ...Object.values(current).map((layout) => layout.z));
        return {
          ...current,
          portfolio: nextOpen
            ? { ...current.portfolio, fullscreen: false, h: LIVE_PORTFOLIO_EXPANDED_HEIGHT, minimized: false, z: topZ + 1 }
            : { ...defaults.portfolio, z: topZ + 1 },
        };
      });
      return nextOpen;
    });
  }

  if (!started) {
    return (
      <LiveTradingStart
        loading={loading || startPreloading}
        message={liveClockMessage}
        preloadStatus={preloadStatus}
        scope={scope}
        session={session}
        sessions={sessions}
        onSessionChange={setSession}
        onStart={startTrading}
      />
    );
  }

  const liveClockControlDisabled =
    liveClockMode === "loading_data" ||
    (!tradingStarted && liveClockMode !== "ready") ||
    (loading && liveClockMode !== "running" && liveClockMode !== "seeking");

  return (
    <>
      {!headerCollapsed ? (
        <section className="live-top-shell">
          <div className="live-top-content">
            <PageIntro
              groupLabel="Replay Trading"
              title="Workspace Layout"
              description="Saved canvas layout and multi-monitor workspace controls."
              actions={
                <div className="live-session-toolbar layout-only">
                  <LiveField label="Layout name" type="text" value={layoutName} onChange={setLayoutName} />
                  <LiveSelect label="Load layout" value={selectedLayoutName} values={["", ...savedLayouts.map((layout) => layout.name)]} onChange={loadNamedLayout} />
                  <button className="button secondary" onClick={saveNamedLayout} type="button">
                    <Save size={15} /> Save Layout
                  </button>
                  <button className="button secondary" onClick={() => setOpenWindows((current) => Array.from(new Set([...current, ...CORE_WINDOW_IDS])))} type="button">
                    <FolderOpen size={15} /> Core Containers
                  </button>
                  <button className="button secondary" onClick={() => createChildCanvas()} type="button">
                    <LayoutGrid size={15} /> Child Canvas
                  </button>
                </div>
              }
            />
            <WorkspaceCanvasManager
              canvases={canvasTargets}
              onCreate={() => createChildCanvas()}
              onOpen={openCanvasInNewTab}
              onRemove={removeCanvas}
            />
            <WorkspaceWindowManager
              canvasTargets={canvasTargets}
              windows={liveWindowSummaries}
              onClose={closeWindow}
              onFocus={(id) => {
                updateLayout(id, { minimized: false });
                bringWindowForward(id);
              }}
              onMinimize={(id, minimized) => updateLayout(id, { minimized })}
              onMoveToCanvas={moveWindowToCanvas}
              onPopOut={createChildCanvas}
              onShowCoreWindows={() => setOpenWindows((current) => Array.from(new Set([...current, ...CORE_WINDOW_IDS])))}
            />
            {error ? <div className="preview-sample-status error">{error}</div> : null}
            {snapshot?.reason ? <div className="preview-sample-status error">{snapshot.reason}</div> : null}
          </div>
        </section>
      ) : null}
      <section className="live-global-status-strip" aria-label="Live session state">
        <div className="live-global-status-cells" style={{ gridTemplateColumns: `repeat(${Math.max(globalMetrics.items.length, 1)}, minmax(108px, 1fr))` }}>
          {globalMetrics.items.map((item) => (
            <article className="live-global-status-card" data-tone={item.tone} key={item.label}>
              <span className="live-debug-metric-icon">{item.icon}</span>
              <span className="live-debug-metric-label">{item.label}</span>
              <strong>{item.value}</strong>
            </article>
          ))}
        </div>
        <div className="live-global-status-actions" aria-label="Simulation controls">
          <label className="live-pace-control">
            <span>Pace</span>
            <input min="1" step="1" type="number" value={secondsPerMinute} onChange={(event) => setSecondsPerMinute(event.target.value)} />
          </label>
          <button className="button secondary compact" disabled={!tradingStarted || loading} onClick={refreshCurrentBar} type="button">
            <RefreshCw size={14} /> Refresh
          </button>
          <button className="button secondary compact" disabled={!tradingStarted || loading} onClick={advanceOneBar} type="button">
            <StepForward size={14} /> Next Bar
          </button>
          <button className="button primary compact" disabled={!tradingStarted || loading || liveClockMode === "seeking"} onClick={() => void seekNextSignal()} type="button">
            {loading || liveClockMode === "seeking" ? <span className="loading-spinner" aria-hidden="true" /> : <SkipForward size={14} />} Next Signal
          </button>
          <button className="button secondary compact" disabled={!started} onClick={saveSimulation} title={simulationSaveMessage || "Save this simulation when you want to keep it"} type="button">
            <Save size={14} /> Save Simulation
          </button>
          <button className="button secondary compact" disabled={liveClockControlDisabled} onClick={toggleLiveClock} type="button">
            {liveClockMode === "running" || liveClockMode === "seeking" ? <PauseCircle size={14} /> : <Play size={14} />} {!tradingStarted ? "Start Trading" : liveClockMode === "running" || liveClockMode === "seeking" ? "Pause" : "Resume"}
          </button>
          <button className="button secondary compact" onClick={closeSession} type="button">
            <X size={14} /> Close
          </button>
        </div>
      </section>
      <section className={headerCollapsed ? "live-workspace compact" : "live-workspace"} aria-label="Replay trading workspace" data-workspace-canvas style={{ minHeight: workspaceMinHeight }}>
        <MetricsDock metrics={portfolioMetrics} />
        {!openWindows.length ? <div className="live-empty-canvas">This canvas is empty. Open scanner rows here or pop containers into this canvas from another tab.</div> : null}
        {openWindows.map((windowId) => {
          const layout = layouts[windowId] ?? layouts.chart ?? buildDefaultCanvasLayout(false).layouts.chart;
          if (windowId === "scanner") {
            return (
              <WorkspaceWindow key={windowId} canvasTargets={canvasTargets} id={windowId} layout={layout} title="Scanner" icon={<TrendingUp size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onMoveToCanvas={moveWindowToCanvas} onPopOut={createChildCanvas}>
                <ScannerContainer
                  loading={loading}
                  marketRows={marketRows}
                  marketSnapshot={marketSnapshot}
                  query={scannerQuery}
                  queryGroups={scannerQueryGroups}
                  queryName={scannerQueryName}
                  rows={scannerRows}
                  selectedTicker={selectedTicker}
                  signalRows={signalRows}
                  snapshot={snapshot}
                  onDeleteQueryGroup={deleteScannerQueryGroup}
                  onQueryChange={(nextQuery) => setScannerQuery(normalizeLiveScannerQuery(nextQuery) ?? nextQuery)}
                  onQueryNameChange={setScannerQueryName}
                  onRowSelect={openChartForRow}
                  onSaveQueryGroup={saveScannerQueryGroup}
                />
              </WorkspaceWindow>
            );
          }
          if (windowId === "portfolio") {
            return (
              <WorkspaceWindow key={windowId} canvasTargets={canvasTargets} id={windowId} layout={layout} title="Portfolio" icon={<WalletCards size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onMoveToCanvas={moveWindowToCanvas} onPopOut={createChildCanvas}>
                <PortfolioContainer
                  detailsOpen={portfolioDetailsOpen}
                  orders={orders}
                  positions={positions}
                  selectedTab={portfolioTab}
                  trades={trades}
                  onTabChange={setPortfolioTab}
                  onToggleDetails={togglePortfolioDetails}
                />
              </WorkspaceWindow>
            );
          }
          const chart = chartWindows.find((item) => item.id === windowId);
          if (!chart || !scope) return null;
          return (
            <WorkspaceWindow key={windowId} canvasTargets={canvasTargets} id={windowId} layout={layout} title={chart.ticker} icon={<BarChart3 size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onMoveToCanvas={moveWindowToCanvas} onPopOut={createChildCanvas}>
              <LiveChartWindow
                catalog={catalog}
                chart={chart}
                compactVisibleColumns={compactVisibleColumns}
                draft={tradeDraft}
                mainTimeframe={mainTimeframe}
                mainVisibleColumns={mainVisibleColumns}
                marketRows={marketRows}
                orders={orders}
                positions={positions}
                scannerRows={scannerRows}
                scope={scope}
                session={session}
                sessions={sessions}
                showDayChart={showDayChart}
                showFiveMinuteChart={showFiveMinuteChart}
                trades={trades}
                onDraftChange={setTradeDraft}
                onMainTimeframeChange={setMainTimeframe}
                onMainVisibleColumnsChange={setMainVisibleColumns}
                onCompactVisibleColumnsChange={setCompactVisibleColumns}
                onMarkPosition={markPositionToMarket}
                onStage={stageOrder}
                onToggleDayChart={() => setLowerChartVisibility((current) => ({ ...current, day: !current.day }))}
                onToggleFiveMinuteChart={() => setLowerChartVisibility((current) => ({ ...current, fiveMinute: !current.fiveMinute }))}
              />
            </WorkspaceWindow>
          );
        })}
      </section>
    </>
  );
}

function LiveTradingStart({
  loading,
  message,
  onSessionChange,
  onStart,
  preloadStatus,
  scope,
  session,
  sessions,
}: {
  loading: boolean;
  message: string;
  onSessionChange: (session: TradingSession) => void;
  onStart: () => void;
  preloadStatus: LivePreloadPayload | null;
  scope: Scope | null;
  session: TradingSession;
  sessions: string[];
}) {
  const preloadProgress = startPreloadProgress(preloadStatus);
  return (
    <>
      <PageIntro
        groupLabel="Replay Trading"
        title="Start Replay Session"
        description="Choose the trading date. The workspace opens, validates the required provider data, then waits for you to press Start."
      />
      <section className="live-start-panel panel">
        <div className="live-start-copy">
          <span>Session Setup</span>
          <strong>{session.sessionDate || "Select a session"}</strong>
          <p>Historical sessions run as open-by-open simulation. The same boundary can later point to live broker and data-provider connectors.</p>
        </div>
        <div className="live-start-form">
          <LiveSelect label="Trading date" value={session.sessionDate} values={sessions} onChange={(value) => onSessionChange({ ...session, sessionDate: value })} />
          <div className="live-start-path">
            <span>Processed data</span>
            <strong>{scope?.processed_root ?? "Loading..."}</strong>
          </div>
          <div className="live-start-progress-grid" aria-label="Session preload progress">
            <LiveStartProgress label="Bars" progress={preloadProgress.bars} status={preloadProgress.barsStatus} />
            <LiveStartProgress label="News" progress={preloadProgress.news} status={preloadProgress.newsStatus} />
          </div>
          {message ? <div aria-live="polite" className="live-start-message" role="status">{loading ? <span className="loading-spinner" aria-hidden="true" /> : null}<span>{message}</span></div> : null}
          <button className="button primary" disabled={!session.sessionDate || loading} onClick={onStart} type="button">
            {loading ? <span className="loading-spinner" aria-hidden="true" /> : <Play size={15} />} {loading ? "Loading..." : "Load Session"}
          </button>
        </div>
      </section>
    </>
  );
}

function LiveStartProgress({ label, progress, status }: { label: string; progress: number; status: string }) {
  const tone = status === "ready" ? "success" : status === "error" || status === "missing_auth" ? "danger" : status === "missing" ? "warning" : "info";
  return (
    <div className="live-start-progress" data-tone={tone}>
      <div>
        <span>{label}</span>
        <strong>{status || "waiting"}</strong>
      </div>
      <b><span style={{ width: `${Math.max(4, Math.round(progress * 100))}%` }} /></b>
    </div>
  );
}

function ScannerContainer({
  loading,
  marketRows,
  marketSnapshot,
  onDeleteQueryGroup,
  onQueryChange,
  onQueryNameChange,
  onRowSelect,
  onSaveQueryGroup,
  query,
  queryGroups,
  queryName,
  rows,
  selectedTicker,
  signalRows,
  snapshot,
}: {
  loading: boolean;
  marketRows: Record<string, unknown>[];
  marketSnapshot: ScannerSnapshot | null;
  query: BackendTableQuery;
  queryGroups: ScannerQueryGroup[];
  queryName: string;
  rows: Record<string, unknown>[];
  selectedTicker: string;
  signalRows: SignalRow[];
  snapshot: ScannerSnapshot | null;
  onDeleteQueryGroup: (id: string) => void;
  onQueryChange: (query: BackendTableQuery) => void;
  onQueryNameChange: (value: string) => void;
  onRowSelect: (row: Record<string, unknown>) => void;
  onSaveQueryGroup: (name: string, query: BackendTableQuery) => void;
}) {
  const queryPresets: BackendQueryPreset[] = queryGroups.map((group) => ({ id: group.id, label: group.name, query: group.query }));
  return (
    <div className="live-scanner-stack">
      <section className="live-scanner-table live-scanner-signals">
        <DataTable
          backendQuery={{
            columns: snapshot?.columns?.length ? snapshot.columns : LIVE_SCANNER_COLUMNS,
            loading,
            onChange: onQueryChange,
            onDeletePreset: onDeleteQueryGroup,
            onNameChange: onQueryNameChange,
            onSavePreset: onSaveQueryGroup,
            presets: queryPresets,
            queryName,
            value: query,
          }}
          columns={LIVE_SIGNAL_COLUMNS}
          defaultSort={{ column: "live_signal_time", direction: "desc" }}
          empty={loading ? "Loading scanner..." : "No scanner signals detected yet."}
          fitToContent
          isRowSelected={(row) => stringValue(row, "ticker") === selectedTicker}
          onRowClick={onRowSelect}
          preserveFiltersOnDataChange
          rows={signalRows}
          title={`Signals${rows.length ? ` (${rows.length} current)` : ""}`}
          transposeHelper
        />
      </section>
      <section className="live-scanner-table live-scanner-market">
        <DataTable
          columns={marketStateTableColumns(marketSnapshot?.columns ?? [])}
          defaultSort={{ column: "last_day_volume_so_far", direction: "desc" }}
          empty={loading ? "Loading market state..." : "Market state will load at the current simulation time."}
          isRowSelected={(row) => stringValue(row, "ticker") === selectedTicker}
          onRowClick={onRowSelect}
          preserveFiltersOnDataChange
          rows={marketRows}
          title="Market State"
          transposeHelper
        />
      </section>
    </div>
  );
}


function PortfolioPositions({ positions }: { positions: PositionRow[] }) {
  return (
    <section className="live-portfolio-positions" aria-label="Open positions">
      <div className="live-portfolio-positions-header">
        <span>Open Positions</span>
        <strong>{positions.length}</strong>
      </div>
      {positions.length ? (
        <div className="live-portfolio-position-list">
          {positions.map((position) => {
            const pnlTone = position.unrealized_pnl >= 0 ? "positive" : "negative";
            return (
              <article className={`live-portfolio-position-card ${pnlTone}`} key={position.symbol}>
                <div className="live-portfolio-position-main">
                  <strong>{position.symbol}</strong>
                  <span>{integer(position.quantity)} sh</span>
                </div>
                <div>
                  <span>Avg</span>
                  <strong>{money(position.avg_price)}</strong>
                </div>
                <div>
                  <span>Mark</span>
                  <strong>{money(position.mark)}</strong>
                </div>
                <div>
                  <span>P/L</span>
                  <strong>{money(position.unrealized_pnl)}</strong>
                  <small>{percent(position.unrealized_pnl_pct)}</small>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="live-empty-positions">No open positions.</div>
      )}
    </section>
  );
}

function PortfolioContainer({
  detailsOpen,
  onToggleDetails,
  onTabChange,
  orders,
  positions,
  selectedTab,
  trades,
}: {
  detailsOpen: boolean;
  onToggleDetails: () => void;
  onTabChange: (tab: string) => void;
  orders: OrderRow[];
  positions: PositionRow[];
  selectedTab: string;
  trades: TradeRow[];
}) {
  const tabs = ["Open Positions", "P/L", "Trades", "Orders"];
  const activeTab = tabs.includes(selectedTab) ? selectedTab : tabs[0];
  return (
    <div className={detailsOpen ? "live-container-stack portfolio-expanded" : "live-container-stack"}>
      <PortfolioPositions positions={positions} />
      <button className="live-portfolio-expand-button" onClick={onToggleDetails} title={detailsOpen ? "Hide tabs" : "Show tabs"} type="button">
        {detailsOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {detailsOpen ? (
        <>
          <Tabs tabs={tabs} active={activeTab} onChange={onTabChange} />
          {activeTab === "Open Positions" ? <DataTable rows={positions} empty="No open positions." /> : null}
          {activeTab === "P/L" ? <DataTable rows={buildProfitLossRows(positions, trades)} empty="No P/L rows." /> : null}
          {activeTab === "Trades" ? <DataTable rows={trades} empty="No completed trades yet." /> : null}
          {activeTab === "Orders" ? <DataTable rows={orders} empty="No staged orders." /> : null}
        </>
      ) : null}
    </div>
  );
}

function LiveChartWindow({
  catalog,
  chart,
  compactVisibleColumns,
  draft,
  mainTimeframe,
  mainVisibleColumns,
  marketRows,
  onCompactVisibleColumnsChange,
  onDraftChange,
  onMainTimeframeChange,
  onMainVisibleColumnsChange,
  onMarkPosition,
  onStage,
  onToggleDayChart,
  onToggleFiveMinuteChart,
  orders,
  positions,
  scannerRows,
  scope,
  session,
  sessions,
  showDayChart,
  showFiveMinuteChart,
  trades,
}: {
  catalog: CatalogPayload | null;
  chart: ChartWindow;
  compactVisibleColumns: string[];
  draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string };
  mainTimeframe: string;
  mainVisibleColumns: string[];
  marketRows: Record<string, unknown>[];
  orders: OrderRow[];
  positions: PositionRow[];
  scannerRows: Record<string, unknown>[];
  scope: Scope;
  session: TradingSession;
  sessions: string[];
  showDayChart: boolean;
  showFiveMinuteChart: boolean;
  trades: TradeRow[];
  onCompactVisibleColumnsChange: (columns: string[]) => void;
  onDraftChange: (draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string }) => void;
  onMainTimeframeChange: (timeframe: string) => void;
  onMainVisibleColumnsChange: (columns: string[]) => void;
  onMarkPosition: (symbol: string, mark: number) => void;
  onStage: (side?: "BUY" | "SELL", status?: string, context?: Partial<StageOrderContext>) => void;
  onToggleDayChart: () => void;
  onToggleFiveMinuteChart: () => void;
}) {
  const [mainPayload, setMainPayload] = useState<ChartPayload | null>(null);
  const [dayPayload, setDayPayload] = useState<ChartPayload | null>(null);
  const [fiveMinutePayload, setFiveMinutePayload] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [dayChartLoading, setDayChartLoading] = useState(false);
  const [fiveMinuteChartLoading, setFiveMinuteChartLoading] = useState(false);
  const [chartErrors, setChartErrors] = useState({ day: "", fiveMinute: "", main: "" });
  const chartError = [chartErrors.main, showDayChart ? chartErrors.day : "", showFiveMinuteChart ? chartErrors.fiveMinute : ""].filter(Boolean).join(" ");
  const liveRow = latestLiveChartRow(chart, marketRows, scannerRows);
  const selectedTime = clockTimestampSeconds(session.sessionDate, session.barTime) ?? rowTimestampSeconds(chart.row, session.sessionDate, session.barTime);
  const selectedOpen =
    chartOpenAtTime(mainPayload, selectedTime) ||
    numberValue(liveRow, "current_open") ||
    numberValue(liveRow, "open");
  const quote = quoteFromRow(liveRow, selectedOpen, { preferMarketQuote: false });
  const position = positions.find((row) => row.symbol === chart.ticker);
  const availableCash = availableCashFromState(positions, trades);
  const liveEntryLine = buildLiveEntryLine(position, quote.bid);
  useEffect(() => {
    if (position && quote.bid > 0) onMarkPosition(chart.ticker, quote.bid);
  }, [chart.ticker, onMarkPosition, position, quote.bid]);
  function closeLivePosition() {
    if (!position || position.quantity <= 0) return;
    onStage("SELL", "STAGED", {
      limit: quote.bid,
      mark: quote.bid,
      quantity: position.quantity,
      row: liveRow,
      side: "SELL",
      status: "STAGED",
      stop: position.stop,
      symbol: chart.ticker,
      type: "LIMIT",
    });
  }
  const mainOpenOnlyPayload = useMemo(() => {
    if (mainTimeframe === "1d") return dayOpenOnlyChartPayload(mainPayload, session.sessionDate, selectedOpen, selectedTime);
    if (mainTimeframe === "5m") return castOpenChartPayload(mainPayload, selectedTime, selectedOpen);
    return openOnlyChartPayload(mainPayload, selectedTime, selectedOpen);
  }, [mainPayload, mainTimeframe, selectedOpen, selectedTime, session.sessionDate]);
  const dayOpenOnlyPayload = useMemo(
    () => dayOpenOnlyChartPayload(dayPayload, session.sessionDate, selectedOpen, selectedTime),
    [dayPayload, selectedOpen, selectedTime, session.sessionDate]
  );
  const fiveMinuteOpenOnlyPayload = useMemo(
    () => castOpenChartPayload(fiveMinutePayload, selectedTime, selectedOpen),
    [fiveMinutePayload, selectedOpen, selectedTime]
  );

  useEffect(() => {
    let active = true;
    setChartLoading(true);
    setMainPayload(null);
    setChartErrors((current) => ({ ...current, main: "" }));
    loadChart(scope.processed_root, session.sessionDate, session.sessionDate, mainTimeframe, chart.ticker, mainVisibleColumns)
      .then((payload) => { if (active) setMainPayload(payload); })
      .catch((reason) => { if (active) setChartErrors((current) => ({ ...current, main: reason instanceof Error ? reason.message : "Main chart failed to load." })); })
      .finally(() => {
        if (active) setChartLoading(false);
      });
    return () => {
      active = false;
    };
  }, [chart.ticker, mainTimeframe, mainVisibleColumns, scope.processed_root, session.sessionDate]);

  useEffect(() => {
    if (!showDayChart) return;
    let active = true;
    setDayChartLoading(true);
    setDayPayload(null);
    setChartErrors((current) => ({ ...current, day: "" }));
    loadChart(scope.processed_root, dateOffset(session.sessionDate, -60), session.sessionDate, "1d", chart.ticker, [])
      .then((payload) => { if (active) setDayPayload(payload); })
      .catch((reason) => { if (active) setChartErrors((current) => ({ ...current, day: reason instanceof Error ? reason.message : "Daily chart failed to load." })); })
      .finally(() => { if (active) setDayChartLoading(false); });
    return () => { active = false; };
  }, [chart.ticker, scope.processed_root, session.sessionDate, showDayChart]);

  useEffect(() => {
    if (!showFiveMinuteChart) return;
    let active = true;
    setFiveMinuteChartLoading(true);
    setFiveMinutePayload(null);
    setChartErrors((current) => ({ ...current, fiveMinute: "" }));
    const start = previousSessionDate(sessions, session.sessionDate, 2);
    loadChart(scope.processed_root, start, session.sessionDate, "5m", chart.ticker, compactVisibleColumns)
      .then((payload) => { if (active) setFiveMinutePayload(payload); })
      .catch((reason) => { if (active) setChartErrors((current) => ({ ...current, fiveMinute: reason instanceof Error ? reason.message : "Five-minute chart failed to load." })); })
      .finally(() => { if (active) setFiveMinuteChartLoading(false); });
    return () => { active = false; };
  }, [chart.ticker, compactVisibleColumns, scope.processed_root, session.sessionDate, sessions, showFiveMinuteChart]);

  return (
    <ChartsContainer
      catalog={catalog}
      chartError={chartError}
      chartLoading={chartLoading}
      dayChartLoading={dayChartLoading}
      compactVisibleColumns={compactVisibleColumns}
      dayPayload={dayOpenOnlyPayload}
      fiveMinutePayload={fiveMinuteOpenOnlyPayload}
      fiveMinuteChartLoading={fiveMinuteChartLoading}
      mainPayload={mainOpenOnlyPayload}
      mainTimeframe={mainTimeframe}
      mainVisibleColumns={mainVisibleColumns}
      position={position}
      quote={quote}
      availableCash={availableCash}
      draft={draft}
      orders={orders}
      row={liveRow}
      selectedTicker={chart.ticker}
      session={session}
      showDayChart={showDayChart}
      showFiveMinuteChart={showFiveMinuteChart}
      liveEntryLine={liveEntryLine}
      onCompactVisibleColumnsChange={onCompactVisibleColumnsChange}
      onDraftChange={onDraftChange}
      onLiveEntryClose={closeLivePosition}
      onMainTimeframeChange={onMainTimeframeChange}
      onMainVisibleColumnsChange={onMainVisibleColumnsChange}
      onStage={onStage}
      onToggleDayChart={onToggleDayChart}
      onToggleFiveMinuteChart={onToggleFiveMinuteChart}
    />
  );
}

function ChartsContainer({
  availableCash,
  catalog,
  chartError,
  chartLoading,
  compactVisibleColumns,
  dayChartLoading,
  dayPayload,
  draft,
  fiveMinutePayload,
  fiveMinuteChartLoading,
  liveEntryLine,
  mainPayload,
  mainTimeframe,
  mainVisibleColumns,
  onCompactVisibleColumnsChange,
  onDraftChange,
  onLiveEntryClose,
  onMainTimeframeChange,
  onMainVisibleColumnsChange,
  onStage,
  onToggleDayChart,
  onToggleFiveMinuteChart,
  orders,
  position,
  quote,
  row,
  selectedTicker,
  session,
  showDayChart,
  showFiveMinuteChart,
}: {
  availableCash: number;
  catalog: CatalogPayload | null;
  chartError: string;
  chartLoading: boolean;
  compactVisibleColumns: string[];
  dayChartLoading: boolean;
  dayPayload: ChartPayload | null;
  draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string };
  fiveMinutePayload: ChartPayload | null;
  fiveMinuteChartLoading: boolean;
  liveEntryLine: LiveEntryLine | null;
  mainPayload: ChartPayload | null;
  mainTimeframe: string;
  mainVisibleColumns: string[];
  orders: OrderRow[];
  position?: PositionRow;
  quote: ReturnType<typeof quoteFromRow>;
  row: Record<string, unknown>;
  selectedTicker: string;
  session: TradingSession;
  showDayChart: boolean;
  showFiveMinuteChart: boolean;
  onCompactVisibleColumnsChange: (columns: string[]) => void;
  onDraftChange: (draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string }) => void;
  onLiveEntryClose: () => void;
  onMainTimeframeChange: (timeframe: string) => void;
  onMainVisibleColumnsChange: (columns: string[]) => void;
  onStage: (side?: "BUY" | "SELL", status?: string, context?: Partial<StageOrderContext>) => void;
  onToggleDayChart: () => void;
  onToggleFiveMinuteChart: () => void;
}) {
  const mainOptions = mainPayload?.options;
  const compactOptions = fiveMinutePayload?.options ?? dayPayload?.options;
  const lowerChartCount = Number(showDayChart) + Number(showFiveMinuteChart);
  return (
    <div className="live-chart-trade-layout">
      <div className={lowerChartCount ? "live-chart-stack" : "live-chart-stack no-lower"}>
        <div className="live-main-chart-frame">
          <div className="live-chart-view-toggle" aria-label="Lower chart visibility">
            <button className={showDayChart ? "active" : ""} onClick={onToggleDayChart} type="button">
              Daily
            </button>
            <button className={showFiveMinuteChart ? "active" : ""} onClick={onToggleFiveMinuteChart} type="button">
              5m
            </button>
          </div>
          <ChartPanel
            catalogColumns={catalog?.columns ?? []}
            displayItemOptions={mainOptions?.display_items ?? catalog?.displayItems ?? []}
            emptyMessage="Select a scanner row to load charts."
            errorMessage={chartError}
            enableFullscreen={false}
            featureOptions={mainOptions?.feature_columns ?? []}
            indicatorOptions={mainOptions?.standard_indicators ?? MAIN_DISPLAY_ITEMS}
            initialFitMode="recent"
            loading={chartLoading}
            liveEntryLine={liveEntryLine}
            onPeriodChange={() => undefined}
            onTickerChange={() => undefined}
            onTimeframeChange={onMainTimeframeChange}
            onVisibleColumnsChange={onMainVisibleColumnsChange}
            payload={mainPayload}
            periodEnd={session.sessionDate}
            periodStart={session.sessionDate}
            ticker={selectedTicker}
            tickerInputWidth={130}
            timeframe={mainTimeframe}
            timeframes={["1m", "5m", "1d"]}
            visibleColumns={mainVisibleColumns}
            onLiveEntryClose={onLiveEntryClose}
          />
        </div>
        {lowerChartCount ? (
          <div className={lowerChartCount === 1 ? "live-lower-chart-grid single" : "live-lower-chart-grid"}>
            {showDayChart ? (
              <div className="live-compact-chart">
                <div className="live-compact-chart-header">
                  <span>Daily / 60 days</span>
                  <button className="toolbar-button compact" onClick={onToggleDayChart} title="Hide daily chart" type="button">
                    <X size={12} />
                  </button>
                </div>
                <ChartPanel
                  catalogColumns={catalog?.columns ?? []}
                  displayItemOptions={[]}
                  emptyMessage="No daily chart data."
                  errorMessage={chartError}
                  enableFullscreen={false}
                  featureOptions={[]}
                  indicatorOptions={[]}
                  loading={dayChartLoading}
                  daySeparatorsVisible={false}
                  onTickerChange={() => undefined}
                  onTimeframeChange={() => undefined}
                  onVisibleColumnsChange={() => undefined}
                  payload={dayPayload}
                  showIndicatorControls={false}
                  ticker={selectedTicker}
                  timeframe="1d"
                  timeframes={["1d"]}
                  visibleColumns={[]}
                />
              </div>
            ) : null}
            {showFiveMinuteChart ? (
              <div className="live-compact-chart">
                <div className="live-compact-chart-header">
                  <span>5m / last day</span>
                  <button className="toolbar-button compact" onClick={onToggleFiveMinuteChart} title="Hide 5m chart" type="button">
                    <X size={12} />
                  </button>
                </div>
                <ChartPanel
                  catalogColumns={catalog?.columns ?? []}
                  displayItemOptions={compactOptions?.display_items ?? catalog?.displayItems ?? []}
                  emptyMessage="No 5m chart data."
                  errorMessage={chartError}
                  enableFullscreen={false}
                  featureOptions={compactOptions?.feature_columns ?? []}
                  indicatorOptions={LOWER_DISPLAY_ITEMS}
                  loading={fiveMinuteChartLoading}
                  initialFitMode="last_market_day"
                  onTickerChange={() => undefined}
                  onTimeframeChange={() => undefined}
                  onVisibleColumnsChange={onCompactVisibleColumnsChange}
                  payload={fiveMinutePayload}
                  ticker={selectedTicker}
                  timeframe="5m"
                  timeframes={["5m"]}
                  visibleColumns={compactVisibleColumns}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
      <ChartTradePanel
        availableCash={availableCash}
        draft={draft}
        orders={orders}
        position={position}
        quote={quote}
        row={row}
        selectedTicker={selectedTicker}
        session={session}
        onDraftChange={onDraftChange}
        onStage={onStage}
      />
    </div>
  );
}










function startPreloadProgress(preloadStatus: LivePreloadPayload | null) {
  if (!preloadStatus) return { bars: 0, barsStatus: "waiting", news: 0, newsStatus: "waiting" };
  const newsCheck = preloadStatus.checks.find((check) => check.group === "news");
  const barChecks = preloadStatus.checks.filter((check) => check.group !== "news");
  const barsReady = barChecks.reduce((total, check) => total + check.ready_sessions, 0);
  const barsExpected = barChecks.reduce((total, check) => total + check.expected_sessions, 0);
  return {
    bars: barsExpected ? barsReady / barsExpected : 0,
    barsStatus: barChecks.length && barChecks.every((check) => check.status === "ready") ? "ready" : "loading",
    news: newsCheck?.expected_sessions ? newsCheck.ready_sessions / newsCheck.expected_sessions : 0,
    newsStatus: newsCheck?.status ?? "waiting",
  };
}

function appendNewsColumns(columns: string[]) {
  const newsColumns = ["live_news_recency", "live_news_count", "live_news_latest_title"];
  return [...columns, ...newsColumns.filter((column) => !columns.includes(column))];
}

function mergeLiveNews(row: Record<string, unknown>, payload: LiveNewsPayload | null): Record<string, unknown> {
  const ticker = stringValue(row, "ticker").trim().toUpperCase();
  const summary = ticker ? payload?.by_ticker?.[ticker] : null;
  if (!summary) {
    return {
      ...row,
      live_news_count: 0,
      live_news_items: [],
      live_news_latest_time: "",
      live_news_latest_title: "",
      live_news_recency: "none",
      live_news_recent: false,
    };
  }
  return {
    ...row,
    live_news_count: summary.live_news_count ?? 0,
    live_news_items: summary.live_news_items ?? [],
    live_news_latest_time: summary.live_news_latest_time ?? "",
    live_news_latest_title: summary.live_news_latest_title ?? "",
    live_news_recency: summary.live_news_recency ?? "none",
    live_news_recent: Boolean(summary.live_news_recent),
  };
}














function openPositionCost(positions: PositionRow[]) {
  return positions.reduce((total, row) => total + row.avg_price * row.quantity, 0);
}

function availableCashFromState(positions: PositionRow[], trades: TradeRow[]) {
  return Math.max(0, LIVE_STARTING_CASH + realizedPnlFromTrades(trades) - openPositionCost(positions));
}

function buildProfitLossRows(positions: PositionRow[], trades: TradeRow[]) {
  return [
    ...positions.map((row) => ({
      avg_price: row.avg_price,
      mark: row.mark,
      pnl: row.unrealized_pnl,
      pnl_pct: row.unrealized_pnl_pct,
      quantity: row.quantity,
      status: "OPEN",
      symbol: row.symbol,
    })),
    ...trades.map((row) => ({
      entry_price: row.entry_price,
      exit_price: row.exit_price,
      pnl: row.gross_pnl,
      pnl_pct: row.gross_pnl_pct,
      quantity: row.quantity,
      status: "CLOSED",
      symbol: row.symbol,
    })),
  ];
}

function buildPortfolioMetrics({ orders, positions, trades }: { orders: OrderRow[]; positions: PositionRow[]; trades: TradeRow[] }) {
  const realized = realizedPnlFromTrades(trades);
  const unrealized = positions.reduce((total, row) => total + row.unrealized_pnl, 0);
  const exposure = positionExposure(positions);
  const cash = availableCashFromState(positions, trades);
  const stagedOrders = orders.filter((order) => order.status === "STAGED").length;
  const fills = orders.filter((order) => order.status === "FILLED").length;
  const wins = trades.filter((trade) => trade.gross_pnl > 0).length;
  const winRate = trades.length ? wins / trades.length : 0;
  return {
    items: [
      { icon: <Banknote size={14} />, label: "Total P/L", tone: signedMetricTone(realized + unrealized), value: money(realized + unrealized) },
      { icon: <CircleDollarSign size={14} />, label: "Realized P/L", tone: signedMetricTone(realized), value: money(realized) },
      { icon: <Activity size={14} />, label: "Unrealized P/L", tone: signedMetricTone(unrealized), value: money(unrealized) },
      { icon: <Banknote size={14} />, label: "Cash", tone: cash > LIVE_STARTING_CASH ? "success" : cash < LIVE_STARTING_CASH ? "warning" : "muted", value: money(cash) },
      { icon: <Banknote size={14} />, label: "Equity", tone: signedMetricTone(realized + unrealized), value: money(LIVE_STARTING_CASH + realized + unrealized) },
      { icon: <BarChart3 size={14} />, label: "Exposure", tone: exposure ? "info" : "muted", value: money(exposure) },
      { icon: <WalletCards size={14} />, label: "Open Positions", tone: positions.length ? "info" : "muted", value: integer(positions.length) },
      { icon: <ClipboardList size={14} />, label: "Orders", tone: orders.length ? "info" : "muted", value: integer(orders.length) },
      { icon: <CheckCircle2 size={14} />, label: "Trades", tone: trades.length ? "success" : "muted", value: integer(trades.length) },
      { icon: <Save size={14} />, label: "Staged", tone: stagedOrders ? "warning" : "muted", value: integer(stagedOrders) },
      { icon: <CheckCircle2 size={14} />, label: "Fills", tone: fills ? "success" : "muted", value: integer(fills) },
      { icon: <ShieldAlert size={14} />, label: "Win Rate", tone: trades.length ? signedMetricTone(winRate - 0.5) : "muted", value: percent(winRate) },
    ],
  };
}

function buildGlobalLiveMetrics({
  decisions,
  lastActionTime,
  liveClockMode,
  preloadStatus,
  scannerRows,
  secondsPerMinute,
  session,
  snapshot,
}: {
  decisions: Record<string, DecisionState>;
  lastActionTime: string;
  liveClockMode: LiveClockMode;
  preloadStatus: LivePreloadPayload | null;
  scannerRows: Record<string, unknown>[];
  secondsPerMinute: string;
  session: TradingSession;
  snapshot: ScannerSnapshot | null;
}) {
  const decisionsCount = Object.keys(decisions).length;
  const preloadProgress = preloadStatus ? preloadStatus.progress : liveClockMode === "loading_data" ? 0.45 : 0;
  const modeValue = (
    <span className="live-mode-value">
      <span>{formatLiveMode(liveClockMode)}</span>
      {liveClockMode === "loading_data" ? (
        <span className="live-mode-progress" aria-label="Loading data">
          <span style={{ width: `${Math.max(8, Math.round(preloadProgress * 100))}%` }} />
        </span>
      ) : null}
    </span>
  );
  return {
    items: [
      { icon: <Clock3 size={14} />, label: "Date", tone: "info", value: session.sessionDate || "-" },
      { icon: <Clock3 size={14} />, label: "Clock", tone: liveClockMode === "running" ? "success" : liveClockMode === "seeking" ? "warning" : "muted", value: `${session.barTime} ET` },
      { icon: <Activity size={14} />, label: "Mode", tone: liveClockMode === "running" ? "success" : liveClockMode === "seeking" || liveClockMode === "loading_data" ? "warning" : "muted", value: modeValue },
      { icon: <TableProperties size={14} />, label: "Raw Scanner Rows", tone: snapshot?.row_count ? "info" : "muted", value: integer(snapshot?.row_count ?? 0) },
      { icon: <TrendingUp size={14} />, label: "Signals", tone: scannerRows.length ? "success" : "muted", value: integer(scannerRows.length) },
      { icon: <Target size={14} />, label: "Decisions", tone: decisionsCount ? "info" : "muted", value: integer(decisionsCount) },
      { icon: <SkipForward size={14} />, label: "Replay Pace", tone: "info", value: `${Math.max(1, Number(secondsPerMinute) || 10)}s / 1m` },
      { icon: <CheckCircle2 size={14} />, label: "Last Signal", tone: lastActionTime ? "success" : "muted", value: lastActionTime || "-" },
    ],
  };
}

function formatLiveMode(mode: LiveClockMode) {
  if (mode === "loading_data") return "loading data";
  return mode;
}





function readStoredSession(): TradingSession | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(LIVE_SESSION_STORAGE_KEY) || "null");
    return value?.sessionDate ? value : null;
  } catch {
    return null;
  }
}

function canvasStorageKey(canvasId: string) {
  return `${LIVE_LAYOUT_STORAGE_KEY}.${canvasId}`;
}

function canvasTransferKey(canvasId: string) {
  return `${LIVE_LAYOUT_STORAGE_KEY}.transfer.${canvasId}`;
}

function writeCanvasState(canvasId: string, state: { chartWindows: ChartWindow[]; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] }) {
  window.localStorage.setItem(canvasStorageKey(canvasId), JSON.stringify({ ...state, layoutVersion: LIVE_LAYOUT_VERSION }));
}

function readCanvasLayoutState(canvasId: string): { chartWindows: ChartWindow[]; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] } {
  const defaults = buildDefaultCanvasLayout(canvasId !== "main");
  try {
    const parsed = JSON.parse(window.localStorage.getItem(canvasStorageKey(canvasId)) || "null") as Partial<{ chartWindows: ChartWindow[]; layoutVersion: number; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] }> | null;
    if (!parsed || parsed.layoutVersion !== LIVE_LAYOUT_VERSION) return defaults;
    return {
      chartWindows: Array.isArray(parsed.chartWindows) ? parsed.chartWindows : defaults.chartWindows,
      layouts: { ...defaults.layouts, ...(parsed.layouts ?? {}) },
      windows: Array.isArray(parsed.windows) ? parsed.windows : defaults.windows,
    };
  } catch {
    return defaults;
  }
}

function listKnownLiveCanvases(currentCanvasId: string): LiveCanvasTarget[] {
  const colors = ["#2563eb", "#16a34a", "#f97316", "#9333ea", "#0891b2", "#dc2626", "#4f46e5"];
  try {
    const canvasIds = new Set<string>(["main", currentCanvasId]);
    const prefix = `${LIVE_LAYOUT_STORAGE_KEY}.`;
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith(prefix)) continue;
      const suffix = key.slice(prefix.length);
      if (!suffix) continue;
      canvasIds.add(suffix.startsWith("transfer.") ? suffix.slice("transfer.".length) : suffix);
    }
    return Array.from(canvasIds)
      .sort((a, b) => (a === "main" ? -1 : b === "main" ? 1 : a.localeCompare(b)))
      .map((id, index) => ({
        color: colors[index % colors.length],
        id,
        isCurrent: id === currentCanvasId,
        label: id === "main" ? "Main" : `Canvas ${index}`,
      }));
  } catch {
    return [{ color: colors[0], id: currentCanvasId, isCurrent: true, label: currentCanvasId === "main" ? "Main" : "Canvas 1" }];
  }
}

function readStoredCanvas(canvasId: string, isChildCanvas: boolean): { chartWindows: ChartWindow[]; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] } {
  const defaults = buildDefaultCanvasLayout(isChildCanvas);
  const transfer = readCanvasTransfer(canvasId);
  if (transfer) {
    const chartWindows = transfer.chartWindows.filter((chart) => chart.id === transfer.windowId);
    return {
      chartWindows,
      layouts: { ...defaults.layouts, [transfer.windowId]: transfer.layout ?? defaults.layouts.chart },
      windows: [transfer.windowId],
    };
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(canvasStorageKey(canvasId)) || "null") as Partial<{ chartWindows: ChartWindow[]; layoutVersion: number; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] }> | null;
    if (!parsed) return defaults;
    if (parsed.layoutVersion !== LIVE_LAYOUT_VERSION) return defaults;
    return {
      chartWindows: Array.isArray(parsed.chartWindows) ? parsed.chartWindows : defaults.chartWindows,
      layouts: { ...defaults.layouts, ...(parsed.layouts ?? {}) },
      windows: Array.isArray(parsed.windows) ? parsed.windows : defaults.windows,
    };
  } catch {
    return defaults;
  }
}

function readCanvasTransfer(canvasId: string): { chartWindows: ChartWindow[]; layout?: WindowLayout; windowId: WindowId } | null {
  try {
    const key = canvasTransferKey(canvasId);
    const parsed = JSON.parse(window.localStorage.getItem(key) || "null");
    window.localStorage.removeItem(key);
    return parsed?.windowId ? parsed : null;
  } catch {
    return null;
  }
}

function readSavedCanvasLayouts(): SavedCanvasLayout[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_LAYOUTS_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((layout) => layout?.layoutVersion === LIVE_LAYOUT_VERSION) : [];
  } catch {
    return [];
  }
}

function readSharedTradingState(): { decisions: Record<string, DecisionState>; orders: OrderRow[]; positions: PositionRow[]; trades: TradeRow[] } {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_SHARED_STATE_STORAGE_KEY) || "null");
    return {
      decisions: parsed?.decisions ?? {},
      orders: Array.isArray(parsed?.orders) ? parsed.orders : [],
      positions: Array.isArray(parsed?.positions) ? parsed.positions : [],
      trades: Array.isArray(parsed?.trades) ? parsed.trades : [],
    };
  } catch {
    return { decisions: {}, orders: [], positions: [], trades: [] };
  }
}

function readStoredScannerQueryGroups(): ScannerQueryGroup[] {
  try {
    const defaultGroupById = new Map(DEFAULT_SCANNER_QUERY_GROUPS.map((group) => [group.id, group]));
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_SETUP_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) && parsed.length
      ? parsed
          .filter((item): item is ScannerQueryGroup => Boolean(item?.id && item?.name && item?.query?.conditions))
          .map((item) => defaultGroupById.get(item.id) ?? { ...item, query: normalizeLiveScannerQuery(item.query) ?? item.query })
      : DEFAULT_SCANNER_QUERY_GROUPS;
  } catch {
    return DEFAULT_SCANNER_QUERY_GROUPS;
  }
}

function readStoredScannerQuery(): BackendTableQuery | null {
  try {
    const storedName = readStoredScannerQueryName();
    const defaultGroup = DEFAULT_SCANNER_QUERY_GROUPS.find((group) => group.name === storedName);
    if (defaultGroup) return defaultGroup.query;
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_SCANNER_QUERY_STORAGE_KEY) || "null");
    return parsed?.conditions ? parsed : null;
  } catch {
    return null;
  }
}

function readStoredScannerQueryName() {
  try {
    return window.localStorage.getItem(`${LIVE_SCANNER_QUERY_STORAGE_KEY}.name`) || "";
  } catch {
    return "";
  }
}

function readStoredLiveChartVisibility() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_CHART_VISIBILITY_STORAGE_KEY) || "null") as Partial<{ day: boolean; fiveMinute: boolean }> | null;
    return {
      day: typeof parsed?.day === "boolean" ? parsed.day : true,
      fiveMinute: typeof parsed?.fiveMinute === "boolean" ? parsed.fiveMinute : true,
    };
  } catch {
    return { day: true, fiveMinute: true };
  }
}

function stableScannerQueryId(name: string) {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `query-${Date.now()}`;
}

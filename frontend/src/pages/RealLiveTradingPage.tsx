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
  Eye,
  FolderOpen,
  Info,
  LayoutGrid,
  Play,
  RefreshCw,
  Save,
  ShieldAlert,
  TableProperties,
  Target,
  TrendingUp,
  WalletCards,
  X,
} from "lucide-react";
import type { Time } from "lightweight-charts";

import { api, query } from "../api/client";
import { ChartPanel, type ChartPayload, type LiveEntryLine } from "../app/components/ChartPanel";
import { liveMarketStatus, type MarketStatus } from "../app/components/MarketStatusBadge";
import { DataTable, type BackendQueryPreset, type BackendTableQuery } from "../app/components/DataTable";
import { MetricRatio } from "../app/components/MetricRatio";
import { PageIntro } from "../app/components/PageIntro";
import { Tabs } from "../app/components/Tabs";
import { TradingModeLaunch, type TradingLaunchCheck } from "../app/components/TradingModeLaunch";
import { useWallClock } from "../app/components/useWallClock";
import {
  CANVAS_REGISTRY_UPDATED_EVENT,
  LIVE_OBSERVATION_CANVAS_ID,
  readCanvasRegistry,
  readCanvasWorkspaceState,
  writeCanvasRegistry,
  writeCanvasWorkspaceState,
  type CanvasWorkspaceState,
} from "../app/canvasWorkspace";
import { TRADING_WORKSPACE_LAYOUT_VERSION, createFocusLayouts } from "../app/components/TradingWorkspace";
import { usePollingTask } from "../app/hooks/usePollingTask";
import {
  normalizePreflightPayload,
  normalizeUniversePreviewPayload,
  objectValue,
  optionalRecord,
  recordValues,
  stringValues,
  type CatalogPayload,
  type RealLiveAccountConfig,
  type RealLiveAccountKey,
  type RealLiveAccountsPayload,
  type RealLiveGatewayStatusPayload,
  type RealLivePortfolioPayload,
  type RealLivePreflightCheck,
  type RealLivePreflightPayload,
  type RealLiveProgressStep,
  type RealLiveScannerPayload,
  type RealLiveSessionBaselineStatus,
  type RealLiveUniversePreviewPayload,
  type RecordRow,
  type ReviewPayload,
  type ScannerSnapshot,
  type Scope,
  type SignalRow,
} from "../features/live-trading/contracts";
import {
  brokerAvailableFunds,
  brokerPnlRows,
  buildClosedTrade,
  buildLiveEntryLine,
  buildProfitLossRows,
  normalizeRealLiveExecution,
  normalizeRealLiveOrder,
  normalizeRealLivePosition,
  portfolioBalanceRows,
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
  normalizeRealLiveScannerRow,
  quoteFromRow,
  rowMatchesBackendQuery,
  scannerQueryFromConditions,
} from "../features/live-trading/scanner";
import {
  addClockMinutes,
  clockTimestampSeconds,
  currentExchangeSession,
  dateOffset,
  formatExchangeClock,
  formatLocalClock,
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
import { ApprovedCanvasRuntimePage, CanvasWorkspaceSurface } from "./CanvasConfigurationPage";
import {
  WorkspaceCanvasManager,
  WorkspaceWindow,
  WorkspaceWindowManager,
  type WorkspaceCanvasTarget as LiveCanvasTarget,
  type WorkspaceWindowId as WindowId,
  type WorkspaceWindowLayout as WindowLayout,
} from "../app/components/WorkspaceCanvas";

type GateProgressStep = {
  detail: string;
  duration?: string;
  id: string;
  label: string;
  message: string;
  progress?: number;
  status: string;
  statusLabel: string;
  tone: "danger" | "info" | "muted" | "success" | "warning";
};






const LIVE_SESSION_STORAGE_KEY = "quant-research-workbench.real-live-trading.session";
const LIVE_LAYOUT_STORAGE_KEY = "quant-research-workbench.real-live-trading.layout";
const LIVE_LAYOUT_VERSION = 4;
const LIVE_LAYOUTS_STORAGE_KEY = "quant-research-workbench.real-live-trading.named-layouts";
const LIVE_SHARED_STATE_STORAGE_KEY = "quant-research-workbench.real-live-trading.shared-state";
const LIVE_SETUP_STORAGE_KEY = "quant-research-workbench.real-live-trading.scanner-queries.v2";
const LIVE_SCANNER_QUERY_STORAGE_KEY = "quant-research-workbench.real-live-trading.scanner-query.v2";
const LIVE_CHART_VISIBILITY_STORAGE_KEY = "quant-research-workbench.real-live-trading.chart-visibility.v1";
const LIVE_ACCOUNT_KEYS_STORAGE_KEY = "quant-research-workbench.real-live-trading.account-keys";
const LIVE_FEATURE_GROUPS = ["core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure"];
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

const REAL_LIVE_SCANNER_COLUMNS = [
  "ticker",
  "bar_time_market",
  "current_open",
  "bid",
  "ask",
  "spread_bps_abs",
  "scanner_score",
  "signal_type",
  "market_state",
  "short_setup",
  "float_profile",
  "trade_rate_10s",
  "trade_accel_10s_60s",
  "tape_imbalance",
  "last_return_5",
  "last_day_volume_so_far",
  "last_day_dollar_volume_so_far",
  "last_transactions",
  "provider",
  "live_priority",
  "live_news_recency",
  "live_news_count",
  "live_news_latest_title",
];

const REAL_LIVE_MARKET_COLUMNS = [
  "ticker",
  "bar_time_market",
  "current_open",
  "bid",
  "ask",
  "spread_bps_abs",
  "scanner_score",
  "market_state",
  "short_setup",
  "float_profile",
  "float_rotation",
  "trade_count_10s",
  "trade_count_60s",
  "trade_rate_10s",
  "trade_rate_60s",
  "trade_accel_10s_60s",
  "volume_rate_10s",
  "notional_rate_10s",
  "buy_pressure",
  "sell_pressure",
  "tape_imbalance",
  "quote_pressure",
  "price_vs_vwap_pct",
  "last_day_current_change_pct",
  "last_day_volume_so_far",
  "last_day_dollar_volume_so_far",
  "last_transactions",
  "provider",
  "live_priority",
  "live_news_recency",
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


export function RealLiveTradingPage({ onMarketStatusChange, onTopbarCenterChange }: { onMarketStatusChange?: Dispatch<SetStateAction<MarketStatus>>; onTopbarCenterChange?: Dispatch<SetStateAction<ReactNode>> }) {
  const canvasId = useMemo(() => new URLSearchParams(window.location.search).get("liveCanvas") || "main", []);
  const isChildCanvas = canvasId !== "main";
  const initialCanvas = useMemo(() => readStoredCanvas(canvasId, isChildCanvas), [canvasId, isChildCanvas]);
  const initialSharedState = useMemo(() => readSharedTradingState(), []);
  const [availableAccounts, setAvailableAccounts] = useState<RealLiveAccountConfig[]>(defaultRealLiveAccounts);
  const [selectedAccountKeys, setSelectedAccountKeys] = useState<RealLiveAccountKey[]>(readStoredAccountKeys);
  const [preflightStatus, setPreflightStatus] = useState<RealLivePreflightPayload | null>(null);
  const [universePreview, setUniversePreview] = useState<RealLiveUniversePreviewPayload | null>(null);
  const [universePreviewLoading, setUniversePreviewLoading] = useState(false);
  const [scannerSetupPresetId, setScannerSetupPresetId] = useState("top_gainers_pct");
  const [scannerSetupRowLimit, setScannerSetupRowLimit] = useState(200);
  const [sessionBaseline, setSessionBaseline] = useState<RealLiveSessionBaselineStatus>({ status: "not_started" });
  const [gatewayStatus, setGatewayStatus] = useState<RealLiveGatewayStatusPayload | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [session, setSession] = useState<TradingSession>(() => readStoredSession() ?? currentExchangeSession());
  const wallClockMs = useWallClock(1_000);
  const wallClock = useMemo(() => new Date(wallClockMs), [wallClockMs]);
  const localClock = useMemo(() => formatLocalClock(wallClock), [wallClock]);
  const exchangeClock = useMemo(() => formatExchangeClock(wallClock), [wallClock]);
  const [started, setStarted] = useState(isChildCanvas);
  const [observing, setObserving] = useState(false);
  const [scannerQueryGroups, setScannerQueryGroups] = useState<ScannerQueryGroup[]>(readStoredScannerQueryGroups);
  const [scannerQueryName, setScannerQueryName] = useState(() => readStoredScannerQueryName() || DEFAULT_SCANNER_QUERY_GROUPS[0]?.name || "Scanner Query");
  const [snapshot, setSnapshot] = useState<ScannerSnapshot | null>(null);
  const [marketSnapshot, setMarketSnapshot] = useState<ScannerSnapshot | null>(null);
  const [signalRows, setSignalRows] = useState<SignalRow[]>([]);
  const [scannerQuery, setScannerQuery] = useState<BackendTableQuery>(() => normalizeLiveScannerQuery(readStoredScannerQuery()) ?? DEFAULT_SCANNER_QUERY_GROUPS[0]?.query ?? emptyScannerQuery());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [liveClockMode, setLiveClockMode] = useState<LiveClockMode>("idle");
  const [liveClockMessage, setLiveClockMessage] = useState("");
  const [lastActionTime, setLastActionTime] = useState("");
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
  const [portfolioSnapshot, setPortfolioSnapshot] = useState<RealLivePortfolioPayload | null>(null);
  const [portfolioTab, setPortfolioTab] = useState("P/L");
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
  const positionsRef = useRef(positions);
  const autoPreflightRequestedRef = useRef(false);
  const seekCancelRef = useRef(0);
  const paceRunRef = useRef(0);
  const liveClockModeRef = useRef<LiveClockMode>("idle");
  const warmedChartCacheKeysRef = useRef(new Set<string>());
  const lastChartOpenRef = useRef<{ id: string; openedAt: number } | null>(null);
  const scannerQueryKey = useMemo(() => JSON.stringify(scannerQuery), [scannerQuery]);
  const scannerSetupPreset = useMemo(
    () => SCANNER_SETUP_PRESETS.find((preset) => preset.id === scannerSetupPresetId) ?? SCANNER_SETUP_PRESETS[0],
    [scannerSetupPresetId],
  );

  useEffect(() => {
    liveClockModeRef.current = liveClockMode;
  }, [liveClockMode]);

  useEffect(() => {
    const controller = new AbortController();
    api<Scope>("/api/market-data/scope", { signal: controller.signal }).then(setScope).catch(() => undefined);
    return () => {
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api<RealLiveAccountsPayload>("/api/real-live-trading/accounts", { signal: controller.signal }).then((payload) => {
      const accounts = payload.accounts?.length ? payload.accounts : defaultRealLiveAccounts();
      setAvailableAccounts(accounts);
      setSelectedAccountKeys((current) => ensureSelectedAccountKeys(accounts, current));
    }).catch(() => {
      if (!controller.signal.aborted) setAvailableAccounts(defaultRealLiveAccounts());
    });
    return () => {
      controller.abort();
    };
  }, []);

  const loadUniversePreview = useCallback(async (options?: { refreshEnrichment?: boolean }) => {
    setUniversePreviewLoading(true);
    try {
      const payload = normalizeUniversePreviewPayload(await api<unknown>(`/api/real-live-trading/market-gateway/universe-preview${query({
        refresh_enrichment: options?.refreshEnrichment ? "1" : "0",
        row_limit: 0,
        snapshot_row_limit: scannerSetupRowLimit,
        snapshot_sort_column: scannerSetupPreset.column,
        snapshot_sort_direction: scannerSetupPreset.direction,
      })}`));
      setUniversePreview(payload);
    } catch (requestError) {
      setUniversePreview({
        can_query_universe: false,
        columns: [],
        errors: [{ message: requestError instanceof Error ? requestError.message : "Universe preview request failed.", scope: "request" }],
        filters: {},
        joined_snapshot_row_count: 0,
        massive_snapshot_row_count: 0,
        persistence: { status: "failed" },
        preview_columns: [],
        progress_steps: [],
        pulled_at_utc: "",
        read_database: "",
        read_url: "",
        reference_columns: [],
        reference_row_count: 0,
        reference_rows: [],
        row_count: 0,
        rows: [],
        run_id: "",
        scanner_row_count: 0,
        session_date: "",
        snapshot_columns: [],
        snapshot_rows: [],
        startup_enrichment: { status: "failed", message: requestError instanceof Error ? requestError.message : "Universe preview request failed." },
        tables: [],
        universe_query: "",
        write_database: "",
        write_url: "",
      });
    } finally {
      setUniversePreviewLoading(false);
    }
  }, [scannerSetupPreset, scannerSetupRowLimit]);

  const loadGatewayStatus = useCallback(async (signal?: AbortSignal) => {
    const payload = await api<RealLiveGatewayStatusPayload>("/api/real-live-trading/market-gateway/status", { signal });
    setGatewayStatus(payload);
    if (payload.session_baseline) setSessionBaseline(payload.session_baseline);
    return payload;
  }, []);

  useEffect(() => {
    const serviceCore = gatewayStatus?.qmd_service_core;
    onMarketStatusChange?.(liveMarketStatus(serviceCore && typeof serviceCore === "object" ? serviceCore as Record<string, unknown> : null));
  }, [gatewayStatus, onMarketStatusChange]);

  const sessionBaselinePolling = started && !["written", "written_with_errors", "failed", "disabled", "cancelled"].includes(sessionBaseline.status || "");
  usePollingTask({
    enabled: !isChildCanvas,
    initialDelayMs: 0,
    intervalMs: sessionBaselinePolling ? 5_000 : 10_000,
    onError: () => onMarketStatusChange?.(liveMarketStatus(null)),
    restartKey: sessionBaselinePolling ? "baseline-active" : "baseline-settled",
    task: async (signal) => {
      await loadGatewayStatus(signal);
    },
  });

  useEffect(() => {
    if (started || isChildCanvas || autoPreflightRequestedRef.current || !availableAccounts.length) return;
    autoPreflightRequestedRef.current = true;
    void checkConnections(ensureSelectedAccountKeys(availableAccounts, selectedAccountKeys));
  }, [availableAccounts, isChildCanvas, selectedAccountKeys, started]);

  useEffect(() => {
    if (!scope) return;
    const controller = new AbortController();
    api<ReviewPayload>(`/api/market-data/review${query({ processed_root: scope.processed_root, start_date: scope.start_date, end_date: scope.end_date })}`, { signal: controller.signal }).then(setReview).catch(() => undefined);
    api<CatalogPayload>(`/api/market-data/catalog${query({ processed_root: scope.processed_root })}`, { signal: controller.signal }).then(setCatalog).catch(() => undefined);
    return () => {
      controller.abort();
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
    () => buildPortfolioMetrics({ orders, positions, snapshot: portfolioSnapshot, trades }),
    [orders, portfolioSnapshot, positions, trades]
  );
  const availableBrokerCash = useMemo(() => brokerAvailableFunds(portfolioSnapshot), [portfolioSnapshot]);
  const selectedAccounts = useMemo(() => selectedAccountList(availableAccounts, selectedAccountKeys), [availableAccounts, selectedAccountKeys]);
  const primaryAccountKey = selectedAccountKeys[0] || "paper";
  const globalMetrics = useMemo(
    () => buildGlobalLiveMetrics({ decisions, exchangeClock, lastActionTime, liveClockMode, localClock, scannerRows: signalRows, selectedAccounts, session, sessionBaseline, snapshot }),
    [decisions, exchangeClock, lastActionTime, liveClockMode, localClock, selectedAccounts, session, sessionBaseline, signalRows, snapshot]
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
      title: `Live Trading - ${canvasLabel}`,
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
    positionsRef.current = positions;
  }, [positions]);

  useEffect(() => {
    if (!started) return;
    const payload = { decisions };
    window.localStorage.setItem(LIVE_SHARED_STATE_STORAGE_KEY, JSON.stringify(payload));
  }, [decisions, started]);

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
          const parsed = JSON.parse(event.newValue) as { decisions?: Record<string, DecisionState> };
          setDecisions(parsed.decisions ?? {});
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
      }
      if (event.key?.startsWith(`${LIVE_LAYOUT_STORAGE_KEY}.`)) {
        setCanvasTargetsVersion((version) => version + 1);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [canvasId, isChildCanvas]);

  useEffect(() => {
    const exchangeSession = currentExchangeSession(wallClock);
    setSession((current) => {
      if (current.barTime === exchangeSession.barTime && current.sessionDate === exchangeSession.sessionDate) return current;
      window.localStorage.setItem(LIVE_SESSION_STORAGE_KEY, JSON.stringify(exchangeSession));
      return exchangeSession;
    });
  }, [wallClock]);

  useEffect(() => {
    window.localStorage.setItem(LIVE_ACCOUNT_KEYS_STORAGE_KEY, JSON.stringify(selectedAccountKeys));
    setPreflightStatus((current) => (current && sameAccountKeySet(current.selected_account_keys, selectedAccountKeys) ? current : null));
  }, [selectedAccountKeys]);

  usePollingTask({
    enabled: started && !isChildCanvas,
    initialDelayMs: 0,
    intervalMs: 15_000,
    restartKey: `${scannerQueryKey}:${selectedAccountKeys.join(",")}`,
    task: async (signal) => {
      await refreshLiveWorkspace({ signal, warmCharts: false });
    },
  });

  async function checkConnections(keys = selectedAccountKeys) {
    const accountKeys = ensureSelectedAccountKeys(availableAccounts, keys);
    setLoading(true);
    setError("");
    setLiveClockMode("loading_data");
    setLiveClockMessage("Checking Massive data and IBKR Client Portal connectivity.");
    try {
      const payload = normalizePreflightPayload(await api<unknown>(`/api/real-live-trading/preflight${query({ account_keys: accountKeys.join(","), account_type: accountKeys[0] || "paper" })}`));
      if (payload.accounts?.length) setAvailableAccounts(payload.accounts);
      if (payload.selected_account_keys?.length) setSelectedAccountKeys(payload.selected_account_keys);
      setPreflightStatus(payload);
      setLiveClockMode(payload.ready ? "ready" : "paused");
      setLiveClockMessage(payload.ready ? "Connections are ready." : "One or more live trading connections are blocked.");
      return payload;
    } catch (requestError) {
      setPreflightStatus(null);
      setLiveClockMode("paused");
      setLiveClockMessage(requestError instanceof Error ? requestError.message : "Connection check failed.");
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function enterLiveWorkspace() {
    if (loading) return;
    const payload = preflightStatus && sameAccountKeySet(preflightStatus.selected_account_keys, selectedAccountKeys) ? preflightStatus : await checkConnections(selectedAccountKeys);
    if (!payload?.ready) return;
    canvasRemovedRef.current = false;
    window.localStorage.removeItem(LIVE_SHARED_STATE_STORAGE_KEY);
    setDecisions({});
    setOrders([]);
    setPositions([]);
    setTrades([]);
    setPortfolioSnapshot(null);
    setSignalRows([]);
    setSnapshot(null);
    setMarketSnapshot(null);
    setSelectedRow(null);
    setLastActionTime("");
    setSessionBaseline({ status: "pending" });
    setStarted(true);
    setLiveClockMode("running");
    setLiveClockMessage("Live workspace is connected. Scanner and portfolio refresh automatically.");
    await api<RealLiveGatewayStatusPayload>("/api/real-live-trading/market-gateway/start", { method: "POST" }).then((gatewayPayload) => {
      if (gatewayPayload.session_baseline) setSessionBaseline(gatewayPayload.session_baseline);
    }).catch((requestError) => {
      setSessionBaseline({ status: "failed", error: requestError instanceof Error ? requestError.message : "Market gateway start failed." });
      setLiveClockMessage(requestError instanceof Error ? `Market gateway start failed; REST fallback remains available. ${requestError.message}` : "Market gateway start failed; REST fallback remains available.");
    });
    await refreshLiveWorkspace({ warmCharts: true });
  }

  function enterObservationWorkspace() {
    ensureLiveObservationCanvas();
    setObserving(true);
  }

  async function refreshLiveWorkspace(options: { signal?: AbortSignal; warmCharts?: boolean } = {}) {
    await Promise.all([loadScannerAt(session.barTime, options), loadBrokerPortfolio(options.signal)]);
  }

  function refreshCurrentBar() {
    void refreshLiveWorkspace({ warmCharts: false });
  }

  async function loadScannerAt(barTime: string, options: { signal?: AbortSignal; warmCharts?: boolean } = {}) {
    setLoading(true);
    setError("");
    try {
      const scannerPayload = await api<RealLiveScannerPayload>("/api/real-live-trading/scanner?row_limit=500", { signal: options.signal });
      const exchangeSession = { barTime: scannerPayload.market_time || barTime || session.barTime, sessionDate: scannerPayload.session_date || session.sessionDate };
      const liveRows = scannerPayload.rows.map((row) => normalizeRealLiveScannerRow(row, exchangeSession));
      const rawMarketRows = scannerPayload.market_rows?.length ? scannerPayload.market_rows : scannerPayload.rows;
      const marketRowsPayload = rawMarketRows.map((row) => buildMarketStateRow(normalizeRealLiveScannerRow(row, exchangeSession)));
      const enrichedRows = liveRows
        .map((row) => enrichLiveCandidate(row, scannerQueryName))
        .filter((row) => rowMatchesBackendQuery(row, scannerQuery));
      const enrichedSnapshot = {
        bar_time: exchangeSession.barTime,
        columns: REAL_LIVE_SCANNER_COLUMNS,
        feature_groups: ["massive", "live"],
        row_count: enrichedRows.length,
        rows: enrichedRows,
        session_date: exchangeSession.sessionDate,
        timeframe: "live",
      };
      const marketStateSnapshot = {
        bar_time: exchangeSession.barTime,
        columns: REAL_LIVE_MARKET_COLUMNS,
        feature_groups: ["massive", "live"],
        row_count: marketRowsPayload.length,
        rows: marketRowsPayload,
        session_date: exchangeSession.sessionDate,
        timeframe: "live",
      };
      const firstRow = enrichedRows.find((row) => stringValue(row, "live_setup_group")) ?? enrichedRows[0] ?? null;
      setSession(exchangeSession);
      setSnapshot(enrichedSnapshot);
      setMarketSnapshot(marketStateSnapshot);
      setSelectedRow(firstRow);
      if (enrichedRows.length) appendSignalRows(enrichedRows, exchangeSession.barTime);
      if (options.warmCharts !== false) void warmChartCacheForRows(enrichedRows);
      if (firstRow) setLastActionTime(exchangeSession.barTime);
      setLiveClockMode("running");
      setLiveClockMessage(scannerPayload.gateway_error ? `Live scanner used REST fallback at ${exchangeSession.barTime} ET. ${scannerPayload.gateway_error}` : `Live scanner refreshed from ${scannerPayload.provider} at ${exchangeSession.barTime} ET.`);
      return { firstRow, marketSnapshot: marketStateSnapshot, snapshot: enrichedSnapshot };
    } catch (requestError) {
      if (options.signal?.aborted) return null;
      setSnapshot(null);
      setMarketSnapshot(null);
      setSelectedRow(null);
      setLiveClockMode("paused");
      setError(requestError instanceof Error ? requestError.message : "Live scanner request failed.");
      return null;
    } finally {
      if (!options.signal?.aborted) setLoading(false);
    }
  }

  async function loadBrokerPortfolio(signal?: AbortSignal) {
    try {
      const accountKeys = ensureSelectedAccountKeys(availableAccounts, selectedAccountKeys);
      const payload = await api<RealLivePortfolioPayload>(`/api/real-live-trading/portfolio${query({ account_keys: accountKeys.join(","), account_type: accountKeys[0] || "paper" })}`, { signal });
      setPortfolioSnapshot(payload);
      setPositions(payload.positions.map(normalizeRealLivePosition).filter((position) => position.symbol && position.quantity !== 0));
      setOrders(payload.orders.map(normalizeRealLiveOrder).filter((order) => order.symbol));
      setTrades((payload.executions ?? []).map(normalizeRealLiveExecution).filter((trade) => trade.symbol || trade.execution_id));
    } catch (requestError) {
      if (signal?.aborted) return;
      setError((current) => current || (requestError instanceof Error ? requestError.message : "IBKR portfolio request failed."));
    }
  }

  async function loadMarketStateAt(barTime: string) {
    if (!snapshot) return null;
    const marketSnapshot = {
      bar_time: barTime,
      columns: REAL_LIVE_MARKET_COLUMNS,
      feature_groups: ["massive", "live"],
      row_count: snapshot.rows.length,
      rows: snapshot.rows.map(buildMarketStateRow),
      session_date: session.sessionDate,
      timeframe: "live",
    };
    return { snapshot: marketSnapshot };
  }

  async function loadNewsAt(_barTime: string, _tickers: string[]) {
    return null;
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
    const order: OrderRow = {
      account_key: selectedAccountKeys.join(","),
      account_keys: selectedAccountKeys,
      account_label: selectedAccounts.map((account) => account.label).join(", "),
      account_type: primaryAccountKey,
      avg_fill_price: null,
      filled_quantity: 0,
      id: `${Date.now()}-${symbol}-${side}`,
      last_fill_price: null,
      limit,
      quantity,
      remaining_quantity: quantity,
      side,
      status,
      stop,
      symbol,
      timestamp: `${session.sessionDate} ${session.barTime}`,
      type,
    };
    setOrders((current) => [order, ...current]);
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
        `/api/real-live-trading/warm-charts${query({
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
    url.searchParams.set("liveCanvas", targetCanvasId);
    url.hash = "real-live-trading";
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

  function closeSession() {
    paceRunRef.current += 1;
    seekCancelRef.current += 1;
    setStarted(false);
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
    if (observing) {
      return <CanvasWorkspaceSurface
        canvasId={LIVE_OBSERVATION_CANVAS_ID}
        manager={false}
        modeControls={<div className="live-global-status-actions" aria-label="Live observation controls">
          <span className="live-observation-badge"><Eye aria-hidden="true" size={13} /> Read only</span>
          <button className="button secondary compact" onClick={() => setObserving(false)} type="button"><X size={14} /> Exit monitor</button>
        </div>}
        readOnly
        runtimeMode="live"
      />;
    }
    return (
      <RealLiveTradingGate
        loading={loading}
        gatewayStatus={gatewayStatus}
        message={liveClockMessage}
        preflightStatus={preflightStatus}
        onCheck={() => void checkConnections()}
        onEnter={() => void enterLiveWorkspace()}
        onObserve={enterObservationWorkspace}
      />
    );
  }

  if (started) {
    const runtimeMode = selectedAccounts.some((account) => account.trading_mode === "live") ? "live" : "paper";
    return <ApprovedCanvasRuntimePage
      accountKeys={selectedAccountKeys}
      mode={runtimeMode}
      modeControls={<div className="live-global-status-actions" aria-label={`${runtimeMode} workspace controls`}>
        <button className="button secondary compact" disabled={loading} onClick={refreshCurrentBar} type="button"><RefreshCw size={14} /> Refresh</button>
        <button className="button secondary compact" disabled={loading} onClick={() => void checkConnections()} type="button"><CheckCircle2 size={14} /> Check</button>
        <button className="button secondary compact" onClick={closeSession} type="button"><X size={14} /> Account Gate</button>
      </div>}
    />;
  }

  return (
    <>
      {!headerCollapsed ? (
        <section className="live-top-shell">
          <div className="live-top-content">
            <PageIntro
              groupLabel="Live Trading"
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
        <div className="live-global-status-actions" aria-label="Live workspace controls">
          <button className="button secondary compact" disabled={loading} onClick={refreshCurrentBar} type="button">
            <RefreshCw size={14} /> Refresh
          </button>
          <button className="button secondary compact" disabled={loading} onClick={() => void checkConnections()} type="button">
            <CheckCircle2 size={14} /> Check
          </button>
          <button className="button secondary compact" onClick={closeSession} type="button">
            <X size={14} /> Account Gate
          </button>
        </div>
      </section>
      <section className={headerCollapsed ? "live-workspace compact" : "live-workspace"} aria-label="Live trading workspace" data-workspace-canvas style={{ minHeight: workspaceMinHeight }}>
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
                  portfolioSnapshot={portfolioSnapshot}
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
                availableCash={availableBrokerCash}
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

function RealLiveTradingGate({
  gatewayStatus,
  loading,
  message,
  onCheck,
  onEnter,
  onObserve,
  preflightStatus,
}: {
  gatewayStatus: RealLiveGatewayStatusPayload | null;
  loading: boolean;
  message: string;
  onCheck: () => void;
  onEnter: () => void;
  onObserve: () => void;
  preflightStatus: RealLivePreflightPayload | null;
}) {
  const qmdReady = isQmdGatewayReady(gatewayStatus);
  const checks: TradingLaunchCheck[] = [
    ...(preflightStatus?.checks ?? []).map((check) => ({ ...check, evidence: check.message, summary: check.message })),
    { evidence: qmdReady ? "Live event and historical context routes are available." : "Waiting for the QMD service core.", id: "qmd_runtime", label: "Market data runtime", required: true, status: qmdReady ? "ready" : "blocked" },
  ];
  const ready = Boolean(preflightStatus?.ready && qmdReady);
  const accountAuthority = preflightStatus?.selected_accounts?.map((account) => account.label).join(", ") || "the account binding managed in Accounts & Sessions";
  return <TradingModeLaunch
    actionLabel="Open Live Canvas"
    actionSummary={<>Manual orders, Trading Actions, and enabled strategies will use <strong>{accountAuthority}</strong>. Scanner and portfolio data load after Canvas opens.</>}
    checking={loading || !preflightStatus}
    checks={checks}
    description="Trade manually, use configured Trading Actions, or operate enabled strategies against real-time market data. Account bindings and approved configuration are managed separately."
    error={!loading && !preflightStatus ? message : ""}
    eyebrow="Live"
    icon={Activity}
    onAction={onEnter}
    onRefresh={onCheck}
    ready={ready}
    setupEyebrow="Session authority"
    setupTitle="Managed configuration"
    title="Open the live workspace"
  >
    <div className="mode-launch-authority live-observation-launch">
      <span>Market observation</span>
      <strong>Scanner, Watchlists, Signal Stream, and live news</strong>
      <small>Open a persisted read-only Canvas using QMD Live and News. No account, Portfolio, OMS, broker connection, or approved trading release is required. BarGPT forecasts appear when its service and configured Data Fields are available, but do not block the monitor.</small>
      <div><button className="button primary compact" onClick={onObserve} type="button"><Eye aria-hidden="true" size={14} /> Observe Live</button><button className="button secondary compact" onClick={() => { window.location.hash = "#market-discovery-configuration"; }} type="button">Market Discovery</button></div>
    </div>
    <div className="mode-launch-authority">
      <span>Configuration authority</span>
      <strong>{preflightStatus ? "Approved release" : "Resolving approved release"}</strong>
      <small>The Session Profile selects market data, clock, account, Portfolio, and OMS routes. Enabled Strategy Deployments run independently and may be observed here.</small>
      <div><button className="button secondary compact" onClick={() => { window.location.hash = "#revision-configuration"; }} type="button">Approved Releases</button><button className="button secondary compact" onClick={() => { window.location.hash = "#account-configuration"; }} type="button">Accounts &amp; Sessions</button></div>
    </div>
  </TradingModeLaunch>;
}

function ensureLiveObservationCanvas() {
  const registry = readCanvasRegistry();
  const existing = readCanvasWorkspaceState(LIVE_OBSERVATION_CANVAS_ID) ?? registry.workspaceStates?.[LIVE_OBSERVATION_CANVAS_ID];
  const requiredInstances = {
    "live-monitor-news": "news",
    "live-monitor-scanner": "scanner",
    "live-monitor-signal-stream": "signal_stream",
    "live-monitor-watchlist": "watchlist",
  } as const;
  const legacyIds: Record<string, keyof typeof requiredInstances> = {
    news: "live-monitor-news",
    scanner: "live-monitor-scanner",
    signal_stream: "live-monitor-signal-stream",
    watchlist: "live-monitor-watchlist",
  };
  const migrateId = (instanceId: string) => legacyIds[instanceId] ?? instanceId;
  const requiredIds = Object.keys(requiredInstances);
  const openIds = [...new Set([...(existing?.openIds ?? []).map(migrateId), ...requiredIds])];
  const fallbackLayouts = createFocusLayouts(openIds);
  const migratedLayouts = Object.fromEntries(
    Object.entries(existing?.layouts ?? {}).map(([instanceId, layout]) => [migrateId(instanceId), layout]),
  );
  const migratedInstances = Object.fromEntries(
    Object.entries(existing?.instances ?? {}).map(([instanceId, kind]) => [migrateId(instanceId), kind]),
  );
  const state: CanvasWorkspaceState = {
    groups: existing?.groups ?? {},
    instances: { ...migratedInstances, ...requiredInstances },
    layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
    layouts: { ...fallbackLayouts, ...migratedLayouts },
    openIds,
  };
  const canvases = registry.canvases.some((canvas) => canvas.id === LIVE_OBSERVATION_CANVAS_ID)
    ? registry.canvases
    : [...registry.canvases, { id: LIVE_OBSERVATION_CANVAS_ID, label: "Live Monitor" }];
  writeCanvasWorkspaceState(LIVE_OBSERVATION_CANVAS_ID, state);
  writeCanvasRegistry({
    ...registry,
    canvases,
    instanceSettings: {
      ...registry.instanceSettings,
      "live-monitor-news": registry.instanceSettings["live-monitor-news"] ?? {
        news: { content: "all", endDate: "", kind: "all", limit: 100, lookbackHours: 6, rangeMode: "preset", startDate: "", ticker: "" },
        version: 28,
      },
    },
    workspaceStates: { ...(registry.workspaceStates ?? {}), [LIVE_OBSERVATION_CANVAS_ID]: state },
  });
  window.dispatchEvent(new CustomEvent(CANVAS_REGISTRY_UPDATED_EVENT));
}

const SCANNER_SETUP_TAB = "Scanner Setup";
const SCANNER_SETUP_ROW_LIMITS = [50, 100, 200, 500, 1000];
const SCANNER_SETUP_PRESETS = [
  { column: "snapshot_todays_change_pct", direction: "desc", id: "top_gainers_pct", label: "Top Gainers %" },
  { column: "snapshot_todays_change", direction: "desc", id: "top_gainers_dollars", label: "Top Gainers $" },
  { column: "snapshot_day_volume", direction: "desc", id: "top_volume", label: "Top Volume" },
  { column: "snapshot_trade_count", direction: "desc", id: "most_trades", label: "Most Trades" },
  { column: "snapshot_spread_bps", direction: "asc", id: "tight_spread", label: "Tight Spread" },
  { column: "massive_float", direction: "asc", id: "low_float", label: "Low Float" },
  { column: "massive_short_interest", direction: "desc", id: "short_interest", label: "Short Interest" },
] as const;

type ScannerSetupPreset = (typeof SCANNER_SETUP_PRESETS)[number];

function LiveUniversePreviewPanel({
  loading,
  onRefresh,
  onRefreshEnrichment,
  preview,
  scannerSetupPreset,
  scannerSetupPresetId,
  scannerSetupRowLimit,
  setScannerSetupPresetId,
  setScannerSetupRowLimit,
}: {
  loading: boolean;
  onRefresh: () => void;
  onRefreshEnrichment: () => void;
  preview: RealLiveUniversePreviewPayload | null;
  scannerSetupPreset: ScannerSetupPreset;
  scannerSetupPresetId: string;
  scannerSetupRowLimit: number;
  setScannerSetupPresetId: Dispatch<SetStateAction<string>>;
  setScannerSetupRowLimit: Dispatch<SetStateAction<number>>;
}) {
  const [activePreviewTab, setActivePreviewTab] = useState(SCANNER_SETUP_TAB);
  const errors = preview?.errors ?? [];
  const tableRows = preview?.tables ?? [];
  const columnRows = preview?.columns ?? [];
  const referenceRows = preview?.reference_rows?.length ? preview.reference_rows : preview?.rows ?? [];
  const snapshotRows = preview?.snapshot_rows ?? [];
  const referenceColumns = preview?.reference_columns?.length ? preview.reference_columns : preview?.preview_columns?.length ? preview.preview_columns : Object.keys(referenceRows[0] ?? {}).length ? Object.keys(referenceRows[0] ?? {}) : ["candidate_massive_ticker", "ibkr_conid", "exchange_code", "currency_code", "issuer_name", "logo_relative_path"];
  const snapshotColumns = preview?.snapshot_columns?.length ? preview.snapshot_columns : Object.keys(snapshotRows[0] ?? {}).length ? Object.keys(snapshotRows[0] ?? {}) : ["candidate_massive_ticker", "ibkr_conid", "snapshot_last_price", "snapshot_day_volume", "snapshot_bid", "snapshot_ask", "snapshot_spread_bps"];
  const persistence = preview?.persistence ?? {};
  const enrichment = preview?.startup_enrichment ?? {};
  const scannerSetupRows = snapshotRows;
  return (
    <section className="live-universe-preview panel" aria-label="Initial database universe preview">
      <div className="live-universe-preview-header">
        <div>
          <span>Initial Database Pull</span>
          <strong>{preview?.can_query_universe ? `${integer(preview.reference_row_count ?? preview.row_count)} reference rows loaded` : "Waiting for ClickHouse universe"}</strong>
        </div>
        <div className="live-universe-preview-actions">
          <button className="button secondary" disabled={loading} onClick={onRefresh} type="button">
            {loading ? <span className="loading-spinner" aria-hidden="true" /> : <RefreshCw size={15} />} Refresh
          </button>
          <button className="button secondary" disabled={loading} onClick={onRefreshEnrichment} type="button">
            {loading ? <span className="loading-spinner" aria-hidden="true" /> : <RefreshCw size={15} />} Refresh Float/Short
          </button>
        </div>
      </div>
      <div className="live-universe-summary-grid">
        <LiveUniverseMetric label="Read URL" value={preview?.read_url || "not loaded"} />
        <LiveUniverseMetric label="Read DB" value={preview?.read_database || "not loaded"} />
        <LiveUniverseMetric label="Write DB" value={preview?.write_database || "not loaded"} />
        <LiveUniverseMetric label="Reference Rows" value={integer(preview?.reference_row_count ?? preview?.row_count ?? 0)} />
        <LiveUniverseMetric label="Massive Rows" value={integer(preview?.massive_snapshot_row_count ?? 0)} />
        <LiveUniverseMetric label="Joined Rows" value={integer(preview?.joined_snapshot_row_count ?? 0)} />
        <LiveUniverseMetric label="Scanner Rows" value={integer(preview?.scanner_row_count ?? 0)} />
        <LiveUniverseMetric label="Float/Short" value={stringValue(enrichment, "status") || "not_started"} tone={stringValue(enrichment, "status") === "ready" ? "success" : stringValue(enrichment, "status") === "failed" ? "danger" : "info"} />
        <LiveUniverseMetric label="Preview Mode" value={stringValue(persistence, "status") || "read_only_preview"} tone="info" />
        <LiveUniverseMetric label="Pulled At" value={preview?.pulled_at_utc ? preview.pulled_at_utc.slice(0, 19) : "not loaded"} />
        <LiveUniverseMetric label="Errors" value={integer(errors.length)} tone={errors.length ? "danger" : "success"} />
      </div>
      {errors.length ? (
        <div className="live-universe-errors">
          {errors.map((error, index) => (
            <div key={`${stringValue(error, "scope")}-${index}`}>
              <strong>{stringValue(error, "scope") || "error"}</strong>
              <span>{stringValue(error, "message") || "Unknown database error."}</span>
            </div>
          ))}
        </div>
      ) : null}
      <div className="live-universe-tab-group">
        <div className="live-universe-tabs-header">
          <Tabs active={activePreviewTab} onChange={setActivePreviewTab} tabs={[SCANNER_SETUP_TAB, "Reference Pull", "Tables", "Columns"]} />
          <span>
            {activePreviewTab === SCANNER_SETUP_TAB
              ? `${integer(scannerSetupRows.length)} of ${integer(snapshotRows.length)} rows`
              : activePreviewTab === "Reference Pull"
                ? `${integer(referenceRows.length)} reference rows shown`
                : activePreviewTab === "Tables"
                  ? `${integer(tableRows.length)} tables`
                  : `${integer(columnRows.length)} columns`}
          </span>
        </div>
        <div className={activePreviewTab === SCANNER_SETUP_TAB ? "live-universe-preview-table scanner-setup" : "live-universe-preview-table"}>
          {activePreviewTab === SCANNER_SETUP_TAB ? (
            <>
              <div className="live-universe-subtitle">
                <strong>Scanner Setup</strong>
                <span>Massive snapshot joined to the tradable reference universe</span>
              </div>
              <div className="live-scanner-setup-bar" aria-label="Scanner setup controls">
                <div className="live-scanner-setup-presets" role="group" aria-label="Scanner sort preset">
                  {SCANNER_SETUP_PRESETS.map((preset) => (
                    <button
                      aria-pressed={scannerSetupPreset.id === preset.id}
                      className={scannerSetupPreset.id === preset.id ? "active" : undefined}
                      key={preset.id}
                      onClick={() => setScannerSetupPresetId(preset.id)}
                      title={`Sort by ${preset.column}`}
                      type="button"
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
                <label className="live-scanner-setup-select">
                  <span>Rows</span>
                  <select onChange={(event) => setScannerSetupRowLimit(Number(event.target.value))} value={scannerSetupRowLimit}>
                    {SCANNER_SETUP_ROW_LIMITS.map((limit) => (
                      <option key={limit} value={limit}>
                        {integer(limit)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="live-scanner-setup-help"
                  title="Float: micro/low floats can move faster; large floats usually need more volume. Short: squeeze watch and crowded short mark higher short-squeeze potential; short-sale pressure marks active short-side volume."
                  type="button"
                >
                  <Info size={15} />
                </button>
                <strong>{integer(scannerSetupRows.length)} returned</strong>
              </div>
              <DataTable columns={snapshotColumns} defaultSort={{ column: scannerSetupPreset.column, direction: scannerSetupPreset.direction }} empty={loading ? "Loading Massive snapshot rows..." : "No joined snapshot rows loaded."} fitToContent rows={scannerSetupRows} title="Scanner Setup" />
            </>
          ) : activePreviewTab === "Reference Pull" ? (
            <>
              <div className="live-universe-subtitle">
                <strong>Reference Pull</strong>
                <span>ClickHouse reference universe with IBKR conids and logos</span>
              </div>
              <DataTable columns={referenceColumns} empty={loading ? "Loading reference rows..." : "No reference rows loaded."} fitToContent rows={referenceRows} title="Live Startup Reference Pull" />
            </>
          ) : activePreviewTab === "Tables" ? (
            <>
              <div className="live-universe-subtitle">
                <strong>Tables</strong>
                <span>ClickHouse source metadata</span>
              </div>
              <DataTable columns={["name", "engine", "total_rows", "total_bytes"]} empty="No tables returned." fitToContent rows={tableRows} title="ClickHouse Tables" />
            </>
          ) : (
            <>
              <div className="live-universe-subtitle">
                <strong>Columns</strong>
                <span>ClickHouse source schema metadata</span>
              </div>
              <DataTable columns={["table", "name", "type", "position"]} empty="No columns returned." fitToContent rows={columnRows} title="ClickHouse Columns" />
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function LiveGateProgressList({ steps }: { steps: GateProgressStep[] }) {
  return (
    <div className="live-gate-progress-list">
      {steps.map((step, index) => (
        <article className="live-gate-progress-step" data-tone={step.tone} key={step.id}>
          <div className="live-gate-progress-index">{index + 1}</div>
          <div className="live-gate-progress-body">
            <div className="live-gate-progress-main">
              <div>
                <strong>{step.label}</strong>
                <span>{step.detail}</span>
              </div>
              <em>{step.statusLabel}</em>
            </div>
            <div className="live-gate-progress-meter" data-mode={step.tone === "warning" && step.progress === undefined ? "indeterminate" : "determinate"}>
              {step.tone === "warning" && step.progress === undefined ? <span className="loading-spinner" aria-hidden="true" /> : null}
              <div aria-hidden="true">
                <span style={{ width: `${step.progress ?? 42}%` }} />
              </div>
            </div>
            <p>{step.message}</p>
            <small>{step.duration || "pending"}</small>
          </div>
        </article>
      ))}
    </div>
  );
}

function formatGateStepStatus(status: string) {
  const labels: Record<string, string> = {
    blocked: "Blocked",
    complete: "Done",
    deferred: "Later",
    error: "Error",
    failed: "Failed",
    pending: "Pending",
    read_only_preview: "Read-only",
    ready: "Ready",
    running: "Running",
    success: "Done",
    waiting: "Waiting",
  };
  return labels[status] ?? status.replace(/_/g, " ");
}

function LiveUniverseMetric({ label, tone = "info", value }: { label: string; tone?: "danger" | "info" | "success"; value: string }) {
  return (
    <article className="live-universe-metric" data-tone={tone}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function LiveCheckCard({ check }: { check: RealLivePreflightCheck }) {
  const tone = check.status === "ready" ? "success" : check.status === "blocked" || check.status === "error" || check.status === "missing_auth" ? "danger" : check.status === "missing" ? "warning" : "info";
  return (
    <article className="live-check-card" data-tone={tone}>
      <div>
        <span>{check.label}</span>
        <strong>{check.status || "waiting"}</strong>
      </div>
      {check.message ? <p>{check.message}</p> : null}
    </article>
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
          empty={loading ? "Loading market state..." : "Market state will load from the live scanner."}
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
              <article className={`live-portfolio-position-card ${pnlTone}`} key={`${position.account_key || "account"}-${position.conid || position.symbol}`}>
                <div className="live-portfolio-position-main">
                  <strong>{position.symbol}</strong>
                  <span>{position.account_label ? `${position.account_label} - ` : ""}{integer(position.quantity)} sh</span>
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
  portfolioSnapshot,
  positions,
  selectedTab,
  trades,
}: {
  detailsOpen: boolean;
  onToggleDetails: () => void;
  onTabChange: (tab: string) => void;
  orders: OrderRow[];
  portfolioSnapshot: RealLivePortfolioPayload | null;
  positions: PositionRow[];
  selectedTab: string;
  trades: TradeRow[];
}) {
  const tabs = ["P/L", "Fills", "Orders", "Balances", "Errors"];
  const activeTab = tabs.includes(selectedTab) ? selectedTab : tabs[0];
  const balanceRows = portfolioBalanceRows(portfolioSnapshot);
  const errorRows = portfolioSnapshot?.errors ?? [];
  return (
    <div className={detailsOpen ? "live-container-stack portfolio-expanded" : "live-container-stack"}>
      <PortfolioPositions positions={positions} />
      <button className="live-portfolio-expand-button" onClick={onToggleDetails} title={detailsOpen ? "Hide tabs" : "Show tabs"} type="button">
        {detailsOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {detailsOpen ? (
        <>
          <Tabs tabs={tabs} active={activeTab} onChange={onTabChange} />
          {activeTab === "P/L" ? <DataTable rows={buildProfitLossRows(positions, trades, portfolioSnapshot)} empty="No broker P/L rows." /> : null}
          {activeTab === "Fills" ? <DataTable rows={trades} empty="No broker executions yet." /> : null}
          {activeTab === "Orders" ? <DataTable rows={orders} empty="No live orders." /> : null}
          {activeTab === "Balances" ? <DataTable rows={balanceRows} empty="No broker balance rows." /> : null}
          {activeTab === "Errors" ? <DataTable rows={errorRows} empty="No broker portfolio errors." /> : null}
        </>
      ) : null}
    </div>
  );
}

function LiveChartWindow({
  availableCash,
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
  availableCash: number;
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
  const quote = quoteFromRow(liveRow, selectedOpen);
  const position = positions.find((row) => row.symbol === chart.ticker);
  const liveEntryLine = buildLiveEntryLine(position, quote.bid);
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









function availableSessionDates(records: RecordRow[]) {
  return Array.from(new Set(records.filter((record) => record.exists && record.group === "bars" && record.timeframe === "1m").map((record) => record.session_date))).sort();
}

function defaultRealLiveAccounts(): RealLiveAccountConfig[] {
  return [
    { account_class: "paper", account_id: "", account_key: "paper", configured: false, label: "Paper", trading_mode: "paper" },
    { account_class: "cash", account_id: "", account_key: "cash", configured: false, label: "Cash", trading_mode: "live" },
    { account_class: "margin", account_id: "", account_key: "margin", configured: false, label: "Margin", trading_mode: "live" },
    { account_class: "rrsp", account_id: "", account_key: "rrsp", configured: false, label: "RRSP", trading_mode: "live" },
  ];
}

function selectedAccountList(accounts: RealLiveAccountConfig[], selectedKeys: string[]) {
  const selected = selectedKeys.map((key) => accounts.find((account) => account.account_key === key)).filter((account): account is RealLiveAccountConfig => Boolean(account));
  return selected.length ? selected : accounts.filter((account) => account.account_key === "paper").slice(0, 1);
}

function ensureSelectedAccountKeys(accounts: RealLiveAccountConfig[], selectedKeys: string[]) {
  const accountKeys = new Set(accounts.map((account) => account.account_key));
  const selected = selectedKeys.filter((key) => accountKeys.has(key));
  if (selected.length) return selected;
  return accounts.some((account) => account.account_key === "paper") ? ["paper"] : accounts.slice(0, 1).map((account) => account.account_key);
}

function sameAccountKeySet(left: string[] = [], right: string[] = []) {
  const normalizedLeft = [...new Set(left.filter(Boolean))].sort();
  const normalizedRight = [...new Set(right.filter(Boolean))].sort();
  return normalizedLeft.length === normalizedRight.length && normalizedLeft.every((key, index) => key === normalizedRight[index]);
}

function toggleSelectedAccount(accountKey: string, accounts: RealLiveAccountConfig[], setSelected: Dispatch<SetStateAction<string[]>>) {
  setSelected((current) => {
    const active = new Set(ensureSelectedAccountKeys(accounts, current));
    if (active.has(accountKey)) {
      active.delete(accountKey);
    } else {
      active.add(accountKey);
    }
    return ensureSelectedAccountKeys(accounts, Array.from(active));
  });
}

function loadChart(processedRoot: string, startDate: string, endDate: string, timeframe: string, ticker: string, displayItems: string[]) {
  return api<ChartPayload>(
    `/api/market-data/chart${query({
      processed_root: processedRoot,
      start_date: startDate,
      end_date: endDate,
      timeframe,
      ticker,
      feature_groups: LIVE_FEATURE_GROUPS.join(","),
      display_items: displayItems.join(","),
      min_confidence: 0.4,
    })}`
  );
}

function openOnlyChartPayload(payload: ChartPayload | null, cutoffTime: number | null, currentOpen: number): ChartPayload | null {
  return castOpenChartPayload(payload, cutoffTime, currentOpen);
}

function castOpenChartPayload(payload: ChartPayload | null, cutoffTime: number | null, currentOpen: number): ChartPayload | null {
  if (!payload || !cutoffTime) return payload;
  const priorCandles = payload.candles.filter((candle) => candle.time < cutoffTime);
  const open = currentOpen || priorCandles.at(-1)?.close || 0;
  const currentCandle = open > 0 ? [{ time: cutoffTime, open, high: open, low: open, close: open }] : [];
  const trimmed = trimChartPayload(payload, cutoffTime) ?? payload;
  return {
    ...trimmed,
    candles: [...priorCandles, ...currentCandle],
    markers: payload.markers.filter((marker) => Number(marker.time) < cutoffTime),
    volume: [...payload.volume.filter((point) => Number(point.time) < cutoffTime), { color: "rgba(37, 99, 235, 0.25)", time: cutoffTime, value: 0 }],
  };
}

function trimChartPayload(payload: ChartPayload | null, cutoffTime: number | null): ChartPayload | null {
  if (!payload || !cutoffTime) return payload;
  return {
    ...payload,
    candles: payload.candles.filter((candle) => candle.time < cutoffTime),
    markers: payload.markers.filter((marker) => Number(marker.time) < cutoffTime),
    oscillator_series: payload.oscillator_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < cutoffTime) })),
    overlay_series: payload.overlay_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < cutoffTime) })),
    price_zones: (payload.price_zones ?? []).filter((zone) => zone.start < cutoffTime).map((zone) => ({ ...zone, end: Math.min(zone.end, cutoffTime) })),
    regions: payload.regions.filter((region) => region.start < cutoffTime).map((region) => ({ ...region, end: Math.min(region.end, cutoffTime) })),
    trade_annotations: [],
    volume: payload.volume.filter((point) => Number(point.time) < cutoffTime),
  };
}

function dayOpenOnlyChartPayload(payload: ChartPayload | null, sessionDate: string, currentOpen: number, cutoffTime: number | null): ChartPayload | null {
  if (!payload || !sessionDate) return payload;
  const dayStart = Date.parse(`${sessionDate}T00:00:00-04:00`);
  const sessionDayTime = Number.isFinite(dayStart) ? Math.floor(dayStart / 1000) : cutoffTime;
  if (!sessionDayTime || !cutoffTime) return payload;
  const priorCandles = payload.candles.filter((candle) => candle.time < sessionDayTime).slice(-60);
  const priorOscillators = payload.oscillator_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < sessionDayTime).slice(-60) }));
  const priorOverlays = payload.overlay_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < sessionDayTime).slice(-60) }));
  if (!currentOpen) {
    return {
      ...payload,
      candles: priorCandles,
      markers: [],
      oscillator_series: priorOscillators,
      overlay_series: priorOverlays,
      price_zones: [],
      regions: [],
      trade_annotations: [],
      volume: [],
    };
  }
  return {
    ...payload,
    candles: [...priorCandles, { time: cutoffTime, open: currentOpen, high: currentOpen, low: currentOpen, close: currentOpen }],
    markers: [],
    oscillator_series: priorOscillators,
    overlay_series: priorOverlays,
    price_zones: [],
    regions: [],
    trade_annotations: [],
    volume: [],
  };
}









function buildPortfolioMetrics({ orders, positions, snapshot, trades }: { orders: OrderRow[]; positions: PositionRow[]; snapshot: RealLivePortfolioPayload | null; trades: TradeRow[] }) {
  const brokerPnl = brokerPnlRows(snapshot);
  const realized = positions.reduce((total, row) => total + (row.realized_pnl ?? 0), 0);
  const unrealized = brokerPnl.length ? brokerPnl.reduce((total, row) => total + numberValue(row, "unrealized_pnl"), 0) : positions.reduce((total, row) => total + row.unrealized_pnl, 0);
  const exposure = positionExposure(positions);
  const balances = portfolioBalanceRows(snapshot);
  const cash = brokerAvailableFunds(snapshot);
  const equity = balances.reduce((total, row) => total + numberValue(row, "net_liquidation"), 0);
  const connection = snapshot?.connection ?? {};
  const stagedOrders = orders.filter((order) => order.status === "STAGED").length;
  const fills = orders.filter((order) => order.status === "FILLED").length;
  const wins = trades.filter((trade) => trade.gross_pnl > 0).length;
  const winRate = trades.length ? wins / trades.length : 0;
  const errors = snapshot?.errors?.length ?? 0;
  return {
    items: [
      { icon: <WalletCards size={14} />, label: "Source", tone: snapshot ? "success" : "muted", value: snapshot?.source?.toUpperCase() || "IBKR" },
      { icon: <Activity size={14} />, label: "Portfolio Conn", tone: connection.portfolio === "blocked" ? "danger" : connection.portfolio ? "success" : "muted", value: connection.portfolio || "waiting" },
      { icon: <ClipboardList size={14} />, label: "Order Conn", tone: connection.iserver === "blocked" ? "danger" : connection.iserver ? "success" : "muted", value: connection.iserver || "waiting" },
      { icon: <Banknote size={14} />, label: "Total P/L", tone: signedMetricTone(realized + unrealized), value: money(realized + unrealized) },
      { icon: <CircleDollarSign size={14} />, label: "Realized P/L", tone: signedMetricTone(realized), value: money(realized) },
      { icon: <Activity size={14} />, label: "Unrealized P/L", tone: signedMetricTone(unrealized), value: money(unrealized) },
      { icon: <Banknote size={14} />, label: "Available", tone: cash ? "info" : "muted", value: money(cash) },
      { icon: <Banknote size={14} />, label: "Net Liq", tone: equity ? "info" : "muted", value: money(equity) },
      { icon: <BarChart3 size={14} />, label: "Exposure", tone: exposure ? "info" : "muted", value: money(exposure) },
      { icon: <WalletCards size={14} />, label: "Open Positions", tone: positions.length ? "info" : "muted", value: integer(positions.length) },
      { icon: <ClipboardList size={14} />, label: "Orders", tone: orders.length ? "info" : "muted", value: integer(orders.length) },
      { icon: <CheckCircle2 size={14} />, label: "Fills", tone: trades.length ? "success" : "muted", value: integer(trades.length) },
      { icon: <Save size={14} />, label: "Staged", tone: stagedOrders ? "warning" : "muted", value: integer(stagedOrders) },
      { icon: <CheckCircle2 size={14} />, label: "Filled Orders", tone: fills ? "success" : "muted", value: integer(fills) },
      { icon: <ShieldAlert size={14} />, label: "Win Rate", tone: trades.length ? signedMetricTone(winRate - 0.5) : "muted", value: percent(winRate) },
      { icon: <ShieldAlert size={14} />, label: "Broker Errors", tone: errors ? "danger" : "muted", value: integer(errors) },
    ],
  };
}

function buildGlobalLiveMetrics({
  decisions,
  exchangeClock,
  lastActionTime,
  liveClockMode,
  localClock,
  scannerRows,
  selectedAccounts,
  session,
  sessionBaseline,
  snapshot,
}: {
  decisions: Record<string, DecisionState>;
  exchangeClock: string;
  lastActionTime: string;
  liveClockMode: LiveClockMode;
  localClock: string;
  scannerRows: Record<string, unknown>[];
  selectedAccounts: RealLiveAccountConfig[];
  session: TradingSession;
  sessionBaseline: RealLiveSessionBaselineStatus;
  snapshot: ScannerSnapshot | null;
}) {
  const decisionsCount = Object.keys(decisions).length;
  const accountLabel = selectedAccounts.length > 1 ? `${selectedAccounts.length} mirrored` : selectedAccounts[0]?.label || "Paper";
  const accountTone = selectedAccounts.some((account) => account.trading_mode !== "paper") ? "warning" : "info";
  const baselineStatus = sessionBaseline.status || "not_started";
  const baselineTone = baselineStatus === "written" || baselineStatus === "written_with_errors" ? "success" : baselineStatus === "pending" ? "warning" : baselineStatus === "failed" ? "danger" : "muted";
  const baselineValue = baselineStatus === "written" || baselineStatus === "written_with_errors"
    ? `${integer(sessionBaseline.scanner_rows_written ?? sessionBaseline.scanner_row_count ?? 0)} rows`
    : baselineStatus;
  const modeValue = (
    <span className="live-mode-value">
      <span>{formatLiveMode(liveClockMode)}</span>
    </span>
  );
  return {
    items: [
      { icon: <Banknote size={14} />, label: "Accounts", tone: accountTone, value: accountLabel },
      { icon: <Clock3 size={14} />, label: "Exchange", tone: "info", value: exchangeClock || `${session.barTime} ET` },
      { icon: <Clock3 size={14} />, label: "Local", tone: "info", value: localClock || "-" },
      { icon: <Activity size={14} />, label: "Mode", tone: liveClockMode === "running" ? "success" : liveClockMode === "loading_data" ? "warning" : "muted", value: modeValue },
      { icon: <TableProperties size={14} />, label: "Scanner Rows", tone: snapshot?.row_count ? "info" : "muted", value: integer(snapshot?.row_count ?? 0) },
      { icon: <TrendingUp size={14} />, label: "Signals", tone: scannerRows.length ? "success" : "muted", value: integer(scannerRows.length) },
      { icon: <Save size={14} />, label: "Baseline", tone: baselineTone, value: baselineValue },
      { icon: <Target size={14} />, label: "Decisions", tone: decisionsCount ? "info" : "muted", value: integer(decisionsCount) },
      { icon: <CheckCircle2 size={14} />, label: "Last Refresh", tone: lastActionTime ? "success" : "muted", value: lastActionTime || "-" },
    ],
  };
}

function formatLiveMode(mode: LiveClockMode) {
  if (mode === "loading_data") return "loading data";
  return mode;
}

function buildGateProgressSteps({
  gatewayStatus,
  loading,
  preflightStatus,
  selectedAccountKeys,
  universePreview,
  universePreviewLoading,
}: {
  gatewayStatus: RealLiveGatewayStatusPayload | null;
  loading: boolean;
  preflightStatus: RealLivePreflightPayload | null;
  selectedAccountKeys: string[];
  universePreview: RealLiveUniversePreviewPayload | null;
  universePreviewLoading: boolean;
}): GateProgressStep[] {
  const errors = universePreview?.errors ?? [];
  const backendStepsById = new Map((universePreview?.progress_steps ?? []).map((step) => [step.id, step]));
  const preflightChecks = preflightStatus?.checks ?? [];
  const approvalChecks = preflightChecks.filter((check) => check.id.startsWith("approved_"));
  const massiveCheck = preflightChecks.find((check) => check.id === "massive_rest" || check.id === "massive_api_key");
  const qmdCheck = preflightChecks.find((check) => check.id === "qmd_live");
  const runtimeCheck = preflightChecks.find((check) => check.id === "shared_trading_runtime");
  const ibkrChecks = preflightChecks.filter((check) => check.id.includes("ibkr") || check.id.includes("account_env"));
  const qmdStatus = gatewayStatus?.qmd_gateway && typeof gatewayStatus.qmd_gateway === "object" ? gatewayStatus.qmd_gateway as Record<string, unknown> : null;
  const qmdMetrics = qmdStatus?.metrics && typeof qmdStatus.metrics === "object" ? qmdStatus.metrics as Record<string, unknown> : {};
  const qmdReady = isQmdGatewayReady(gatewayStatus);
  const qmdMessage = qmdStatus
    ? qmdReady
      ? `${integer(numberValue(qmdMetrics, "symbols_seen"))} symbols, ${integer(numberValue(qmdMetrics, "events_received"))} events`
      : stringValue(qmdStatus, "message") || stringValue(qmdStatus, "status") || "QMD gateway is not ready."
    : qmdCheck?.message || "Checking dedicated quote/trade gateway.";
  const requestError = errors.find((error) => ["request", "connection"].includes(stringValue(error, "scope")));
  const metadataError = errors.find((error) => ["tables", "columns"].includes(stringValue(error, "scope")));
  const persistenceStatus = stringValue(universePreview?.persistence, "status") || "read_only_preview";
  const enrichmentStatus = stringValue(universePreview?.startup_enrichment, "status");
  const metadataDetail = requestError ? stringValue(requestError, "message") : metadataError ? stringValue(metadataError, "message") : universePreview ? `${integer(universePreview.tables.length)} tables, ${integer(universePreview.columns.length)} columns inspected` : "Waiting for ClickHouse metadata.";
  const metadataStatus = universePreviewLoading && !universePreview ? "running" : requestError || metadataError ? "error" : universePreview ? "complete" : "waiting";
  const backendStep = (id: string, label: string, waitingMessage: string) => {
    const step = backendStepsById.get(id);
    if (step) return progressStepFromBackend(step, label);
    if (universePreviewLoading && ["reference_query", "massive_snapshot"].includes(id)) {
      return makeGateProgressStep({
        detail: "Parallel",
        id,
        label,
        message: waitingMessage,
        status: "running",
      });
    }
    return makeGateProgressStep({
      detail: universePreviewLoading ? "Waiting inputs" : "Waiting",
      id,
      label,
      message: universePreviewLoading ? waitingMessage : "Waiting for the startup preview request.",
      status: "waiting",
    });
  };
  return [
    makeGateProgressStep({
      detail: selectedAccountKeys.length ? `${selectedAccountKeys.length} account${selectedAccountKeys.length > 1 ? "s" : ""} selected` : "Select at least one account before entering the workspace.",
      id: "account_selection",
      label: "Account selection",
      message: selectedAccountKeys.length ? "Order routing will use the selected account set." : "At least one account is required.",
      status: selectedAccountKeys.length ? "complete" : "waiting",
    }),
    makeGateProgressStep({
      detail: approvalChecks.length && approvalChecks.every((check) => check.status === "ready") ? "Pinned release" : "Publication required",
      id: "approved_configuration",
      label: "Approved configuration",
      message: firstBlockedMessage(approvalChecks) || (approvalChecks.length ? "The selected account and Run Plan are bound by the published release." : loading ? "Resolving the published release and Run Plan." : "Connection check starts automatically on page load."),
      status: loading && !approvalChecks.length ? "running" : approvalChecks.length && approvalChecks.every((check) => check.status === "ready") ? "complete" : approvalChecks.length ? "blocked" : "waiting",
    }),
    makeGateProgressStep({
      detail: "Provider",
      id: "massive_rest",
      label: "Massive REST enrichment",
      message: massiveCheck?.message || (loading ? "Validating Massive REST credentials and reference access." : "Connection check starts automatically on page load."),
      status: loading && !massiveCheck ? "running" : massiveCheck?.status === "ready" ? "complete" : massiveCheck?.required === false ? "optional" : massiveCheck ? "blocked" : "waiting",
    }),
    makeGateProgressStep({
      detail: "Quotes/trades",
      id: "qmd_gateway",
      label: "QMD gateway",
      message: qmdMessage,
      status: qmdReady ? "complete" : qmdStatus ? "blocked" : "running",
    }),
    makeGateProgressStep({
      detail: runtimeCheck?.status === "ready" ? "Portfolio + OMS" : "Execution required",
      id: "shared_trading_runtime",
      label: "Shared trading runtime",
      message: runtimeCheck?.message || (loading ? "Checking the manual, semi-automatic, and strategy execution loop." : "Connection check starts automatically on page load."),
      status: loading && !runtimeCheck ? "running" : runtimeCheck?.status === "ready" ? "complete" : runtimeCheck ? "blocked" : "waiting",
    }),
    makeGateProgressStep({
      detail: ibkrChecks.length ? `${ibkrChecks.filter((check) => check.status === "ready").length}/${ibkrChecks.length}` : "Gateway",
      id: "ibkr_client_portal",
      label: "IBKR Client Portal",
      message: firstBlockedMessage(ibkrChecks) || (ibkrChecks.length ? "Selected account gateway, auth, account, and portfolio checks completed." : loading ? "Checking Client Portal auth and selected account access." : "Connection check starts automatically on page load."),
      status: loading && !ibkrChecks.length ? "running" : ibkrChecks.length && ibkrChecks.every((check) => check.status === "ready") ? "complete" : ibkrChecks.length ? "blocked" : "waiting",
    }),
    makeGateProgressStep({
      detail: "Metadata",
      id: "metadata",
      label: "ClickHouse metadata",
      message: metadataDetail,
      status: metadataStatus,
    }),
    backendStep("reference_query", "Reference universe", "Reading ticker, conid, issuer, exchange, and logo references from ClickHouse."),
    backendStep("massive_snapshot", "Massive snapshot", "Pulling the latest full-market Massive snapshot."),
    backendStep("snapshot_join", "Snapshot join", "Joining the ClickHouse reference universe to the Massive snapshot."),
    backendStep("scanner_enrichment", "Float and short data", "Loading daily cached Massive float, short-interest, and short-volume enrichment."),
    makeGateProgressStep({
      detail: persistenceStatus === "read_only_preview" ? "Read-only" : formatGateStepStatus(persistenceStatus),
      id: "read_only_preview",
      label: "Preview persistence policy",
      message: persistenceStatus === "read_only_preview" ? `Startup preview is read-only. Float/short cache: ${enrichmentStatus || "not_started"}.` : requestError ? "Read-only preview could not be confirmed because the API request failed." : `Preview returned persistence status: ${persistenceStatus}`,
      status: persistenceStatus,
    }),
    makeGateProgressStep({
      detail: preflightStatus?.ready && qmdReady && universePreview?.can_query_universe ? "Ready" : "Waiting",
      id: "session_entry",
      label: "Session entry",
      message: preflightStatus?.ready && qmdReady && universePreview?.can_query_universe ? "Enter Workspace can create a trading_session_id and start async baseline recording." : "Requires ready broker, market-data gateway, and read-only universe preview.",
      status: preflightStatus?.ready && qmdReady && universePreview?.can_query_universe ? "ready" : "waiting",
    }),
  ];
}

function isQmdGatewayReady(gatewayStatus: RealLiveGatewayStatusPayload | null) {
  const qmdStatus = gatewayStatus?.qmd_gateway && typeof gatewayStatus.qmd_gateway === "object" ? gatewayStatus.qmd_gateway as Record<string, unknown> : null;
  return Boolean(qmdStatus && ["running", "ready"].includes(stringValue(qmdStatus, "status")));
}

function progressStepFromBackend(step: RealLiveProgressStep, label = step.label): GateProgressStep {
  return makeGateProgressStep({
    detail: step.duration_ms === null || step.duration_ms === undefined ? formatGateStepStatus(step.status || "waiting") : "Timed",
    duration: typeof step.duration_ms === "number" ? `${Math.round(step.duration_ms)} ms` : "",
    id: step.id,
    label,
    message: step.detail || "No detail returned.",
    status: step.status || "waiting",
  });
}

function makeGateProgressStep({ detail, duration = "", id, label, message, progress, status }: { detail: string; duration?: string; id: string; label: string; message: string; progress?: number; status: string }): GateProgressStep {
  return {
    detail,
    duration: duration || defaultGateDuration(status),
    id,
    label,
    message,
    progress: progress ?? progressForGateStatus(status),
    status,
    statusLabel: formatGateStepStatus(status),
    tone: gateToneFromStatus(status),
  };
}

function defaultGateDuration(status: string) {
  if (["success", "complete", "ready", "read_only_preview"].includes(status)) return "done";
  if (["failed", "error", "blocked"].includes(status)) return "blocked";
  if (["running", "pending", "deferred"].includes(status)) return "running";
  return "pending";
}

function firstBlockedMessage(checks: RealLivePreflightCheck[]) {
  return checks.find((check) => check.status !== "ready")?.message || "";
}

function progressForGateStatus(status: string) {
  if (["success", "complete", "ready", "read_only_preview"].includes(status)) return 100;
  if (["failed", "error", "blocked"].includes(status)) return 100;
  if (["waiting", "not_started"].includes(status)) return 0;
  return undefined;
}

function gateToneFromStatus(status: string): GateProgressStep["tone"] {
  if (["success", "complete", "ready", "read_only_preview"].includes(status)) return "success";
  if (["failed", "error", "blocked"].includes(status)) return "danger";
  if (["running", "pending", "deferred"].includes(status)) return "warning";
  if (["waiting", "not_started"].includes(status)) return "muted";
  return "info";
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


function optionalNumber(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function readStoredAccountKeys(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_ACCOUNT_KEYS_STORAGE_KEY) || "null");
    if (Array.isArray(parsed)) return parsed.map((item) => String(item)).filter(Boolean);
    const legacy = window.localStorage.getItem("quant-research-workbench.real-live-trading.account-type");
    return legacy ? [legacy] : ["paper"];
  } catch {
    return ["paper"];
  }
}

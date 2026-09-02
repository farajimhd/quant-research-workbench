import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import {
  Activity,
  BarChart3,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  Eye,
  FolderOpen,
  Info,
  LayoutGrid,
  Play,
  RefreshCw,
  Save,
  TrendingUp,
  WalletCards,
  X,
} from "lucide-react";
import type { Time } from "lightweight-charts";

import { api, query } from "../api/client";
import { liveMarketStatus, type MarketStatus } from "../app/components/MarketStatusBadge";
import { DataTable, type BackendTableQuery } from "../app/components/DataTable";
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
  buildClosedTrade,
  normalizeRealLiveExecution,
  normalizeRealLiveOrder,
  normalizeRealLivePosition,
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
  normalizeLiveScannerQuery,
  normalizeRealLiveScannerRow,
  rowMatchesBackendQuery,
  scannerQueryFromConditions,
} from "../features/live-trading/scanner";
import {
  addClockMinutes,
  currentExchangeSession,
  formatExchangeClock,
  formatLocalClock,
  isAfterClock,
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
import { LiveField, LiveSelect } from "../features/live-trading/LiveChartTradePanel";
import {
  LOWER_DISPLAY_ITEMS,
  MAIN_DISPLAY_ITEMS,
} from "../features/live-trading/LiveChartsContainer";
import { LiveChartWindow } from "../features/live-trading/LiveChartWindow";
import { LiveScannerContainer } from "../features/live-trading/LiveScannerContainer";
import { LivePortfolioContainer } from "../features/live-trading/LivePortfolioContainer";
import {
  buildBrokerGlobalLiveMetrics,
  buildBrokerPortfolioMetrics,
} from "../features/live-trading/liveMetrics";
import { integer, numberValue, stringValue } from "../features/live-trading/liveTradingFormat";
import { MetricsDock } from "../features/live-trading/LiveMetricsDock";
import {
  LIVE_FEATURE_GROUPS,
  availableSessionDates,
  trimChartPayload,
} from "../features/live-trading/liveChartData";
import type { ChartWindow, DecisionState, LiveClockMode, SavedCanvasLayout, ScannerQueryGroup } from "../features/live-trading/liveWorkspaceContracts";
import {
  CORE_WINDOW_IDS,
  buildDefaultCanvasLayout,
  buildLiveWindowSummaries,
  coreWindowTitle,
  liveWorkspaceMinHeight,
} from "../features/live-trading/liveWorkspacePresentation";
import { createLiveWorkspaceStorage } from "../features/live-trading/liveWorkspaceStorage";
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






const LIVE_ACCOUNT_KEYS_STORAGE_KEY = "quant-research-workbench.real-live-trading.account-keys";
const LIVE_PORTFOLIO_EXPANDED_HEIGHT = 360;

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

const {
  canvasStorageKey,
  canvasTransferKey,
  keys: {
    chartVisibility: LIVE_CHART_VISIBILITY_STORAGE_KEY,
    layout: LIVE_LAYOUT_STORAGE_KEY,
    namedLayouts: LIVE_LAYOUTS_STORAGE_KEY,
    scannerQuery: LIVE_SCANNER_QUERY_STORAGE_KEY,
    session: LIVE_SESSION_STORAGE_KEY,
    setup: LIVE_SETUP_STORAGE_KEY,
    sharedState: LIVE_SHARED_STATE_STORAGE_KEY,
  },
  layoutVersion: LIVE_LAYOUT_VERSION,
  listKnownLiveCanvases,
  readCanvasLayoutState,
  readCanvasTransfer,
  readSavedCanvasLayouts,
  readSharedTradingState,
  readStoredCanvas,
  readStoredLiveChartVisibility,
  readStoredScannerQuery,
  readStoredScannerQueryGroups,
  readStoredScannerQueryName,
  readStoredSession,
  stableScannerQueryId,
  writeCanvasState,
} = createLiveWorkspaceStorage({
  defaultScannerQueryGroups: DEFAULT_SCANNER_QUERY_GROUPS,
  normalizeScannerQuery: normalizeLiveScannerQuery,
  prefix: "quant-research-workbench.real-live-trading",
});


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
    () => buildBrokerPortfolioMetrics({ orders, positions, snapshot: portfolioSnapshot, trades }),
    [orders, portfolioSnapshot, positions, trades]
  );
  const availableBrokerCash = useMemo(() => brokerAvailableFunds(portfolioSnapshot), [portfolioSnapshot]);
  const selectedAccounts = useMemo(() => selectedAccountList(availableAccounts, selectedAccountKeys), [availableAccounts, selectedAccountKeys]);
  const primaryAccountKey = selectedAccountKeys[0] || "paper";
  const globalMetrics = useMemo(
    () => buildBrokerGlobalLiveMetrics({ decisions, exchangeClock, lastActionTime, liveClockMode, localClock, scannerRows: signalRows, selectedAccounts, session, sessionBaseline, snapshot }),
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
      window.localStorage.setItem(LIVE_SESSION_STORAGE_KEY, JSON.stringify(exchangeSession));
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
                <LiveScannerContainer
                  loading={loading}
                  marketEmptyMessage="Market state will load from the live scanner."
                  marketRows={marketRows}
                  marketSnapshot={marketSnapshot}
                  query={scannerQuery}
                  queryGroups={scannerQueryGroups}
                  queryName={scannerQueryName}
                  rows={scannerRows}
                  signalRows={signalRows}
                  snapshot={snapshot}
                  onDeleteQueryGroup={deleteScannerQueryGroup}
                  onQueryChange={(nextQuery) => setScannerQuery(normalizeLiveScannerQuery(nextQuery) ?? nextQuery)}
                  onQueryNameChange={setScannerQueryName}
                  onSaveQueryGroup={saveScannerQueryGroup}
                />
              </WorkspaceWindow>
            );
          }
          if (windowId === "portfolio") {
            return (
              <WorkspaceWindow key={windowId} canvasTargets={canvasTargets} id={windowId} layout={layout} title="Portfolio" icon={<WalletCards size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onMoveToCanvas={moveWindowToCanvas} onPopOut={createChildCanvas}>
                <LivePortfolioContainer
                  detailsOpen={portfolioDetailsOpen}
                  mode="broker"
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
                preferMarketQuote
                scannerRows={scannerRows}
                scope={scope}
                session={session}
                sessions={sessions}
                showDayChart={showDayChart}
                showFiveMinuteChart={showFiveMinuteChart}
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
        version: 29,
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

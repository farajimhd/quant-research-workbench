import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type Dispatch, type PointerEvent, type ReactNode, type SetStateAction } from "react";
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
  ExternalLink,
  Flame,
  FolderOpen,
  LayoutGrid,
  Maximize2,
  Megaphone,
  Minimize2,
  Move,
  Newspaper,
  Play,
  RefreshCw,
  Save,
  Settings,
  ShieldAlert,
  TableProperties,
  Target,
  TrendingUp,
  WalletCards,
  X,
} from "lucide-react";
import type { Time } from "lightweight-charts";

import { api, query } from "../api/client";
import { ChartPanel, type ChartCatalogItem, type ChartDisplayItem, type ChartPayload, type LiveEntryLine } from "../app/components/ChartPanel";
import { DataTable, type BackendQueryPreset, type BackendTableQuery } from "../app/components/DataTable";
import { PageIntro } from "../app/components/PageIntro";
import { Tabs } from "../app/components/Tabs";

type Scope = {
  processed_root: string;
  raw_root: string;
  spread_root: string;
  start_date: string;
  end_date: string;
};

type RecordRow = {
  columns: string[];
  exists: boolean;
  group: string;
  key: string;
  path: string;
  session_date: string;
  timeframe: string;
};

type ReviewPayload = {
  records: RecordRow[];
};

type CatalogPayload = {
  columns: ChartCatalogItem[];
  displayItems?: ChartDisplayItem[];
};

type ScannerSnapshot = {
  bar_time: string;
  columns: string[];
  feature_groups: string[];
  reason?: string;
  row_count: number;
  rows: Record<string, unknown>[];
  session_date: string;
  timeframe: string;
};

type SignalRow = Record<string, unknown>;

type ScannerSnapshotPayload = {
  snapshot: ScannerSnapshot;
};

type RealLiveAccountKey = string;

type RealLiveAccountConfig = {
  account_class: string;
  account_id: string;
  account_key: RealLiveAccountKey;
  configured: boolean;
  label: string;
  trading_mode: "paper" | "live" | string;
};

type RealLiveAccountsPayload = {
  accounts: RealLiveAccountConfig[];
};

type RealLivePreflightCheck = {
  details?: Record<string, unknown>;
  id: string;
  label: string;
  message?: string;
  status: "ready" | "blocked" | string;
};

type RealLivePreflightPayload = {
  account_id: string;
  account_type: string;
  accounts: RealLiveAccountConfig[];
  broker?: { base_url?: string; name?: string };
  checks: RealLivePreflightCheck[];
  data_provider?: { base_url?: string; name?: string };
  ready: boolean;
  selected_account_keys: string[];
  selected_accounts: RealLiveAccountConfig[];
};

type RealLiveScannerPayload = {
  gateway_error?: string;
  market_row_count?: number;
  market_rows?: Record<string, unknown>[];
  market_time: string;
  provider: string;
  row_count: number;
  rows: Record<string, unknown>[];
  session_date: string;
  status?: Record<string, unknown>;
};

type RealLiveUniversePreviewPayload = {
  can_query_universe: boolean;
  columns: Record<string, unknown>[];
  errors: Record<string, unknown>[];
  filters: Record<string, unknown>;
  joined_snapshot_row_count?: number;
  massive_snapshot_row_count?: number;
  persistence?: Record<string, unknown>;
  preview_columns: string[];
  progress_steps?: RealLiveProgressStep[];
  pulled_at_utc?: string;
  read_database: string;
  read_url: string;
  reference_columns?: string[];
  reference_row_count?: number;
  reference_rows?: Record<string, unknown>[];
  row_count: number;
  rows: Record<string, unknown>[];
  run_id?: string;
  session_date?: string;
  snapshot_columns?: string[];
  snapshot_rows?: Record<string, unknown>[];
  tables: Record<string, unknown>[];
  universe_query: string;
  write_database: string;
  write_url: string;
};

type RealLiveProgressStep = {
  detail?: string;
  duration_ms?: number | null;
  id: string;
  label: string;
  status: string;
};

type GateProgressStep = {
  detail: string;
  duration?: string;
  id: string;
  label: string;
  status: string;
  tone: "danger" | "info" | "muted" | "success" | "warning";
};

type RealLiveSessionBaselineStatus = {
  enabled?: boolean;
  error?: string;
  errors?: Record<string, unknown>[];
  joined_snapshot_row_count?: number;
  massive_snapshot_row_count?: number;
  pulled_at_utc?: string;
  reference_row_count?: number;
  scanner_row_count?: number;
  scanner_rows_written?: number;
  started_at_utc?: string;
  status?: string;
  trading_session_id?: string;
};

type RealLiveGatewayStatusPayload = {
  session_baseline?: RealLiveSessionBaselineStatus;
  trading_session_id?: string;
  [key: string]: unknown;
};

type RealLivePortfolioPayload = {
  as_of?: string;
  account_id: string;
  account_type: string;
  accounts: RealLiveAccountConfig[];
  balances?: Record<string, unknown>[];
  connection?: Record<string, string>;
  errors?: Record<string, unknown>[];
  executions?: Record<string, unknown>[];
  ledger?: Record<string, unknown>;
  orders: Record<string, unknown>[];
  pnl?: Record<string, unknown>[];
  portfolios?: Record<string, unknown>[];
  positions: Record<string, unknown>[];
  selected_account_keys?: string[];
  source?: string;
  summary?: Record<string, unknown>;
};

type LiveNewsArticle = {
  age_minutes: number;
  body_text?: string;
  channels: string[];
  pdf_text?: string;
  published_et: string;
  recency: string;
  tags: string[];
  teaser?: string;
  ticker: string;
  ticker_count?: number;
  tickers?: string[];
  title: string;
  url: string;
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

type TradingSession = {
  barTime: string;
  sessionDate: string;
};

type ScannerQueryGroup = {
  id: string;
  name: string;
  query: BackendTableQuery;
};

type WindowId = string;

type WindowLayout = {
  fullscreen: boolean;
  h: number;
  minimized: boolean;
  w: number;
  x: number;
  y: number;
  z: number;
};

type ChartWindow = {
  id: WindowId;
  row: Record<string, unknown>;
  ticker: string;
};

type SavedCanvasLayout = {
  chartWindows: ChartWindow[];
  layouts: Record<WindowId, WindowLayout>;
  layoutVersion?: number;
  name: string;
  windows: WindowId[];
};

type LiveClockMode = "idle" | "loading_data" | "ready" | "seeking" | "running" | "paused" | "complete";

type LiveWindowSummary = {
  fullscreen: boolean;
  id: WindowId;
  minimized: boolean;
  title: string;
  type: "core" | "chart";
  z: number;
};

type LiveCanvasTarget = {
  color: string;
  id: string;
  isCurrent: boolean;
  label: string;
};

type DecisionState = "approved" | "skipped" | "watching";

type OrderRow = {
  account_class?: string;
  account_id?: string;
  account_key?: string;
  account_label?: string;
  account_type?: string;
  account_keys?: string[];
  avg_fill_price?: number | null;
  broker_order_id?: string;
  client_order_id?: string;
  conid?: string;
  filled_quantity?: number;
  id: string;
  last_fill_price?: number | null;
  limit: number;
  quantity: number;
  remaining_quantity?: number;
  side: "BUY" | "SELL";
  status: string;
  stop: number;
  symbol: string;
  timestamp: string;
  type: string;
};

type PositionRow = {
  account_class?: string;
  account_id?: string;
  account_key?: string;
  account_label?: string;
  asset_class?: string;
  conid?: string;
  currency?: string;
  market_value?: number;
  realized_pnl?: number | null;
  avg_price: number;
  entry_session_date?: string;
  entry_time?: string;
  mark: number;
  quantity: number;
  stop: number;
  symbol: string;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
};

type TradeRow = {
  account_class?: string;
  account_id?: string;
  account_key?: string;
  account_label?: string;
  broker_order_id?: string;
  commission?: number | null;
  conid?: string;
  entry_price: number;
  entry_session_date?: string;
  entry_time?: string;
  execution_id?: string;
  exit_order_id?: string;
  exit_price: number;
  exit_session_date: string;
  exit_time: string;
  gross_pnl: number;
  gross_pnl_pct: number;
  id: string;
  quantity: number;
  side: "LONG";
  symbol: string;
};

type StageOrderContext = {
  limit: number;
  mark: number;
  quantity: number;
  row: Record<string, unknown> | null;
  side: "BUY" | "SELL";
  status: string;
  stop: number;
  symbol: string;
  type: string;
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
const LIVE_METRICS_DOCK_HEIGHT = 86;
const LIVE_PORTFOLIO_DEFAULT_HEIGHT = 210;
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

const CORE_WINDOW_IDS: WindowId[] = ["portfolio", "scanner"];

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

function buildDefaultCanvasLayout(childCanvas: boolean): { chartWindows: ChartWindow[]; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] } {
  const width = Math.max(1180, window.innerWidth - 112);
  const height = Math.max(780, window.innerHeight - 86);
  const gap = 10;
  const margin = 12;
  const metricsH = LIVE_METRICS_DOCK_HEIGHT;
  const mainY = margin + metricsH + gap;
  const availableH = Math.max(420, height - mainY - margin);
  const leftW = Math.max(250, Math.round(width * 0.2));
  const portfolioH = Math.min(LIVE_PORTFOLIO_DEFAULT_HEIGHT, Math.max(170, Math.round(availableH * 0.34)));
  const scannerH = Math.max(240, availableH - portfolioH - gap);
  const chartX = margin + leftW + gap;
  const chartW = Math.round(width * 0.58);
  const layouts: Record<WindowId, WindowLayout> = {
    portfolio: { fullscreen: false, h: portfolioH, minimized: false, w: leftW, x: margin, y: mainY, z: 3 },
    scanner: { fullscreen: false, h: scannerH, minimized: false, w: leftW, x: margin, y: mainY + portfolioH + gap, z: 1 },
    chart: { fullscreen: false, h: availableH, minimized: false, w: chartW, x: chartX, y: mainY, z: 4 },
  };
  return { chartWindows: [], layouts, windows: childCanvas ? [] : [...CORE_WINDOW_IDS] };
}

export function RealLiveTradingPage({ onTopbarCenterChange }: { onTopbarCenterChange?: Dispatch<SetStateAction<ReactNode>> }) {
  const canvasId = useMemo(() => new URLSearchParams(window.location.search).get("liveCanvas") || "main", []);
  const isChildCanvas = canvasId !== "main";
  const initialCanvas = useMemo(() => readStoredCanvas(canvasId, isChildCanvas), [canvasId, isChildCanvas]);
  const initialSharedState = useMemo(() => readSharedTradingState(), []);
  const [availableAccounts, setAvailableAccounts] = useState<RealLiveAccountConfig[]>(defaultRealLiveAccounts);
  const [selectedAccountKeys, setSelectedAccountKeys] = useState<RealLiveAccountKey[]>(readStoredAccountKeys);
  const [preflightStatus, setPreflightStatus] = useState<RealLivePreflightPayload | null>(null);
  const [universePreview, setUniversePreview] = useState<RealLiveUniversePreviewPayload | null>(null);
  const [universePreviewLoading, setUniversePreviewLoading] = useState(false);
  const [sessionBaseline, setSessionBaseline] = useState<RealLiveSessionBaselineStatus>({ status: "not_started" });
  const [scope, setScope] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [session, setSession] = useState<TradingSession>(() => readStoredSession() ?? currentExchangeSession());
  const [localClock, setLocalClock] = useState(() => formatLocalClock(new Date()));
  const [exchangeClock, setExchangeClock] = useState(() => formatExchangeClock(new Date()));
  const [started, setStarted] = useState(isChildCanvas);
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
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    api<RealLiveAccountsPayload>("/api/real-live-trading/accounts").then((payload) => {
      if (!active) return;
      const accounts = payload.accounts?.length ? payload.accounts : defaultRealLiveAccounts();
      setAvailableAccounts(accounts);
      setSelectedAccountKeys((current) => ensureSelectedAccountKeys(accounts, current));
    }).catch(() => {
      if (active) setAvailableAccounts(defaultRealLiveAccounts());
    });
    return () => {
      active = false;
    };
  }, []);

  const loadUniversePreview = useCallback(async () => {
    setUniversePreviewLoading(true);
    try {
      const payload = await api<RealLiveUniversePreviewPayload>("/api/real-live-trading/market-gateway/universe-preview?row_limit=50");
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
        session_date: "",
        snapshot_columns: [],
        snapshot_rows: [],
        tables: [],
        universe_query: "",
        write_database: "",
        write_url: "",
      });
    } finally {
      setUniversePreviewLoading(false);
    }
  }, []);

  const loadGatewayStatus = useCallback(async () => {
    const payload = await api<RealLiveGatewayStatusPayload>("/api/real-live-trading/market-gateway/status");
    if (payload.session_baseline) setSessionBaseline(payload.session_baseline);
    return payload;
  }, []);

  useEffect(() => {
    if (started || isChildCanvas) return;
    void loadUniversePreview();
  }, [isChildCanvas, loadUniversePreview, started]);

  useEffect(() => {
    if (!scope) return;
    let active = true;
    api<ReviewPayload>(`/api/market-data/review${query({ processed_root: scope.processed_root, start_date: scope.start_date, end_date: scope.end_date })}`).then((payload) => {
      if (!active) return;
      setReview(payload);
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
    const knownPageCount = canvasTargets.length || 1;
    const canvasLabel = isChildCanvas ? `Child canvas ${canvasId.replace(/^canvas-/, "")}` : "Main canvas";
    const layoutLabel = selectedLayoutName || layoutName || "Unsaved layout";
    const pageLabel = `${knownPageCount} page${knownPageCount === 1 ? "" : "s"}`;
    const windowNames = liveWindowSummaries.map((windowItem) => windowItem.title);
    const windowLabel = windowNames.length ? windowNames.slice(0, 4).join(", ") : "No windows";
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
    const updateClocks = () => {
      const now = new Date();
      const exchangeSession = currentExchangeSession(now);
      setLocalClock(formatLocalClock(now));
      setExchangeClock(formatExchangeClock(now));
      setSession(exchangeSession);
      window.localStorage.setItem(LIVE_SESSION_STORAGE_KEY, JSON.stringify(exchangeSession));
    };
    updateClocks();
    const timer = window.setInterval(updateClocks, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    window.localStorage.setItem(LIVE_ACCOUNT_KEYS_STORAGE_KEY, JSON.stringify(selectedAccountKeys));
    setPreflightStatus((current) => (current && sameAccountKeySet(current.selected_account_keys, selectedAccountKeys) ? current : null));
  }, [selectedAccountKeys]);

  useEffect(() => {
    if (!started || isChildCanvas) return;
    let canceled = false;
    const refresh = async () => {
      if (canceled) return;
      await refreshLiveWorkspace({ warmCharts: false });
    };
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 15000);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [isChildCanvas, scannerQueryKey, selectedAccountKeys, started]);

  useEffect(() => {
    if (!started || isChildCanvas) return;
    let canceled = false;
    const poll = async () => {
      try {
        const payload = await loadGatewayStatus();
        const status = payload.session_baseline?.status || "";
        if (!canceled && ["written", "written_with_errors", "failed", "disabled", "cancelled"].includes(status)) {
          window.clearInterval(timer);
        }
      } catch {
        if (!canceled) setSessionBaseline((current) => ({ ...current, status: current.status || "unknown" }));
      }
    };
    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, 5000);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [isChildCanvas, loadGatewayStatus, started]);

  async function checkConnections(keys = selectedAccountKeys) {
    const accountKeys = ensureSelectedAccountKeys(availableAccounts, keys);
    setLoading(true);
    setError("");
    setLiveClockMode("loading_data");
    setLiveClockMessage("Checking Massive data and IBKR Client Portal connectivity.");
    try {
      const payload = await api<RealLivePreflightPayload>(`/api/real-live-trading/preflight${query({ account_keys: accountKeys.join(","), account_type: accountKeys[0] || "paper" })}`);
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

  async function refreshLiveWorkspace(options: { warmCharts?: boolean } = {}) {
    await Promise.all([loadScannerAt(session.barTime, options), loadBrokerPortfolio()]);
  }

  function refreshCurrentBar() {
    void refreshLiveWorkspace({ warmCharts: false });
  }

  async function loadScannerAt(barTime: string, options: { warmCharts?: boolean } = {}) {
    setLoading(true);
    setError("");
    try {
      const scannerPayload = await api<RealLiveScannerPayload>("/api/real-live-trading/scanner?row_limit=500");
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
      setSnapshot(null);
      setMarketSnapshot(null);
      setSelectedRow(null);
      setLiveClockMode("paused");
      setError(requestError instanceof Error ? requestError.message : "Live scanner request failed.");
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function loadBrokerPortfolio() {
    try {
      const accountKeys = ensureSelectedAccountKeys(availableAccounts, selectedAccountKeys);
      const payload = await api<RealLivePortfolioPayload>(`/api/real-live-trading/portfolio${query({ account_keys: accountKeys.join(","), account_type: accountKeys[0] || "paper" })}`);
      setPortfolioSnapshot(payload);
      setPositions(payload.positions.map(normalizeRealLivePosition).filter((position) => position.symbol && position.quantity !== 0));
      setOrders(payload.orders.map(normalizeRealLiveOrder).filter((order) => order.symbol));
      setTrades((payload.executions ?? []).map(normalizeRealLiveExecution).filter((trade) => trade.symbol || trade.execution_id));
    } catch (requestError) {
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
    return (
      <RealLiveTradingGate
        accounts={availableAccounts}
        loading={loading}
        message={liveClockMessage}
        preflightStatus={preflightStatus}
        selectedAccountKeys={selectedAccountKeys}
        universePreview={universePreview}
        universePreviewLoading={universePreviewLoading}
        onCheck={() => void checkConnections()}
        onEnter={() => void enterLiveWorkspace()}
        onRefreshUniverse={() => void loadUniversePreview()}
        onToggleAccount={(accountKey) => toggleSelectedAccount(accountKey, availableAccounts, setSelectedAccountKeys)}
      />
    );
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
                    <FolderOpen size={15} /> Core Windows
                  </button>
                  <button className="button secondary" onClick={() => createChildCanvas()} type="button">
                    <LayoutGrid size={15} /> Child Canvas
                  </button>
                </div>
              }
            />
            <LiveCanvasManager
              canvases={canvasTargets}
              onCreate={() => createChildCanvas()}
              onOpen={openCanvasInNewTab}
              onRemove={removeCanvas}
            />
            <LiveWindowManager
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
      <section className={headerCollapsed ? "live-workspace compact" : "live-workspace"} aria-label="Live trading workspace" style={{ minHeight: workspaceMinHeight }}>
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
  accounts,
  loading,
  message,
  onCheck,
  onEnter,
  onRefreshUniverse,
  onToggleAccount,
  preflightStatus,
  selectedAccountKeys,
  universePreview,
  universePreviewLoading,
}: {
  accounts: RealLiveAccountConfig[];
  loading: boolean;
  message: string;
  onCheck: () => void;
  onEnter: () => void;
  onRefreshUniverse: () => void;
  onToggleAccount: (accountKey: string) => void;
  preflightStatus: RealLivePreflightPayload | null;
  selectedAccountKeys: string[];
  universePreview: RealLiveUniversePreviewPayload | null;
  universePreviewLoading: boolean;
}) {
  const ready = Boolean(preflightStatus?.ready);
  const selectedAccounts = selectedAccountList(accounts, selectedAccountKeys);
  const selectedLabel = selectedAccounts.length ? selectedAccounts.map((account) => account.label).join(", ") : "No account selected";
  const mirrorMode = selectedAccounts.length > 1;
  const progressSteps = buildGateProgressSteps({
    loading,
    preflightStatus,
    selectedAccountKeys,
    universePreview,
    universePreviewLoading,
  });
  const completedSteps = progressSteps.filter((step) => step.tone === "success").length;
  const blockedSteps = progressSteps.filter((step) => step.tone === "danger").length;
  const activeSteps = progressSteps.filter((step) => step.tone === "warning").length;
  const readinessTone = ready && universePreview?.can_query_universe ? "success" : blockedSteps ? "danger" : activeSteps ? "warning" : "muted";
  const readinessLabel = ready && universePreview?.can_query_universe ? "Ready" : blockedSteps ? "Blocked" : activeSteps ? "Checking" : "Waiting";
  return (
    <section className="live-gate-shell" aria-label="Live trading gate">
      <div className="live-gate-console panel" data-tone={readinessTone}>
        <div className="live-gate-toolbar">
          <div className="live-gate-title">
            <span>Live Trading Setup</span>
            <strong>Session Gate</strong>
            <p>Choose accounts, verify broker and data access, then create the live trading session.</p>
          </div>
          <div className="live-start-actions">
            <button className="button secondary" disabled={loading} onClick={onCheck} type="button">
              {loading ? <span className="loading-spinner" aria-hidden="true" /> : <CheckCircle2 size={15} />} Check Connections
            </button>
            <button className="button secondary" disabled={universePreviewLoading} onClick={onRefreshUniverse} type="button">
              {universePreviewLoading ? <span className="loading-spinner" aria-hidden="true" /> : <RefreshCw size={15} />} Refresh Data
            </button>
            <button className="button primary" disabled={!ready || loading || !selectedAccountKeys.length} onClick={onEnter} type="button">
              <Play size={15} /> Enter Workspace
            </button>
          </div>
        </div>
        <div className="live-gate-status-strip" aria-label="Gate status summary">
          <div>
            <span>State</span>
            <strong>{readinessLabel}</strong>
          </div>
          <div>
            <span>Progress</span>
            <strong>{completedSteps}/{progressSteps.length}</strong>
          </div>
          <div>
            <span>Accounts</span>
            <strong>{selectedAccounts.length ? selectedAccounts.length : "-"}</strong>
          </div>
          <div>
            <span>Joined Universe</span>
            <strong>{integer(universePreview?.joined_snapshot_row_count ?? 0)}</strong>
          </div>
          <div>
            <span>Preview Policy</span>
            <strong>Read-only</strong>
          </div>
        </div>
        <div className="live-gate-setup-grid">
          <div className="live-gate-control-stack">
            <section className="live-gate-section" aria-label="Account selection">
            <div className="live-gate-section-heading">
              <span>Accounts</span>
              <strong>{selectedLabel}</strong>
              <small>{mirrorMode ? "Mirrored orders will be sent to each selected account." : "Default is paper. Select more accounts only when mirroring is intended."}</small>
            </div>
            <div className="live-account-card-grid compact" role="group" aria-label="Accounts">
              {accounts.map((account) => {
                const selected = selectedAccountKeys.includes(account.account_key);
                return (
                  <button className={selected ? "live-account-card selected" : "live-account-card"} key={account.account_key} onClick={() => onToggleAccount(account.account_key)} type="button">
                    <span className="live-account-card-top">
                      <strong>{account.label}</strong>
                      <em data-mode={account.trading_mode}>{account.trading_mode === "paper" ? "Paper" : "Live"}</em>
                    </span>
                    <span>{account.account_class}</span>
                    <small>{account.account_id || (account.configured ? "Configured" : "Missing account id")}</small>
                  </button>
                );
              })}
            </div>
            </section>
            <section className="live-gate-section" aria-label="Connection checks">
            <div className="live-gate-section-heading">
              <span>Connections</span>
              <strong>{preflightStatus?.account_id || "Massive and IBKR"}</strong>
              <small>{message || "Run connection checks before entering the workspace."}</small>
            </div>
            <div className="live-check-card-grid" aria-label="Live connection checks">
              {(preflightStatus?.checks ?? []).map((check) => (
                <LiveCheckCard key={check.id} check={check} />
              ))}
              {!preflightStatus ? (
                <>
                  <LiveCheckCard check={{ id: "massive-waiting", label: "Massive data", status: "waiting" }} />
                  <LiveCheckCard check={{ id: "ibkr-waiting", label: "IBKR broker", status: "waiting" }} />
                </>
              ) : null}
            </div>
            </section>
          </div>
          <aside className="live-gate-progress" aria-label="Initial page progress report">
            <div className="live-gate-section-heading">
              <span>Progress Report</span>
              <strong>{activeSteps ? "Running checks" : blockedSteps ? "Needs attention" : "Validation path"}</strong>
            </div>
            <LiveGateProgressList steps={progressSteps} />
          </aside>
        </div>
      </div>
      <LiveUniversePreviewPanel loading={universePreviewLoading} onRefresh={onRefreshUniverse} preview={universePreview} />
    </section>
  );
}

function LiveUniversePreviewPanel({ loading, onRefresh, preview }: { loading: boolean; onRefresh: () => void; preview: RealLiveUniversePreviewPayload | null }) {
  const errors = preview?.errors ?? [];
  const tableRows = preview?.tables ?? [];
  const columnRows = preview?.columns ?? [];
  const referenceRows = preview?.reference_rows?.length ? preview.reference_rows : preview?.rows ?? [];
  const snapshotRows = preview?.snapshot_rows ?? [];
  const referenceColumns = preview?.reference_columns?.length ? preview.reference_columns : preview?.preview_columns?.length ? preview.preview_columns : Object.keys(referenceRows[0] ?? {}).length ? Object.keys(referenceRows[0] ?? {}) : ["candidate_massive_ticker", "ibkr_conid", "exchange_code", "currency_code", "issuer_name", "logo_relative_path"];
  const snapshotColumns = preview?.snapshot_columns?.length ? preview.snapshot_columns : Object.keys(snapshotRows[0] ?? {}).length ? Object.keys(snapshotRows[0] ?? {}) : ["candidate_massive_ticker", "ibkr_conid", "snapshot_last_price", "snapshot_day_volume", "snapshot_bid", "snapshot_ask", "snapshot_spread_bps"];
  const persistence = preview?.persistence ?? {};
  return (
    <section className="live-universe-preview panel" aria-label="Initial database universe preview">
      <div className="live-universe-preview-header">
        <div>
          <span>Initial Database Pull</span>
          <strong>{preview?.can_query_universe ? `${integer(preview.reference_row_count ?? preview.row_count)} reference rows loaded` : "Waiting for ClickHouse universe"}</strong>
        </div>
        <button className="button secondary" disabled={loading} onClick={onRefresh} type="button">
          {loading ? <span className="loading-spinner" aria-hidden="true" /> : <RefreshCw size={15} />} Refresh
        </button>
      </div>
      <div className="live-universe-summary-grid">
        <LiveUniverseMetric label="Read URL" value={preview?.read_url || "not loaded"} />
        <LiveUniverseMetric label="Read DB" value={preview?.read_database || "not loaded"} />
        <LiveUniverseMetric label="Write DB" value={preview?.write_database || "not loaded"} />
        <LiveUniverseMetric label="Reference Rows" value={integer(preview?.reference_row_count ?? preview?.row_count ?? 0)} />
        <LiveUniverseMetric label="Massive Rows" value={integer(preview?.massive_snapshot_row_count ?? 0)} />
        <LiveUniverseMetric label="Joined Rows" value={integer(preview?.joined_snapshot_row_count ?? 0)} />
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
      <div className="live-universe-query">
        <span>Universe Query</span>
        <pre>{preview?.universe_query || "The query will appear after the gateway reads the configured ClickHouse source."}</pre>
      </div>
      <div className="live-universe-preview-grid">
        <div className="live-universe-preview-table">
          <div className="live-universe-subtitle">
            <strong>Reference Pull</strong>
            <span>{integer(referenceRows.length)} shown</span>
          </div>
          <DataTable columns={referenceColumns} empty={loading ? "Loading reference rows..." : "No reference rows loaded."} fitToContent rows={referenceRows} title="Live Startup Reference Pull" />
        </div>
        <div className="live-universe-preview-table">
          <div className="live-universe-subtitle">
            <strong>Massive Snapshot Join</strong>
            <span>{integer(snapshotRows.length)} shown</span>
          </div>
          <DataTable columns={snapshotColumns} empty={loading ? "Loading Massive snapshot rows..." : "No joined snapshot rows loaded."} fitToContent rows={snapshotRows} title="Live Startup Massive Snapshot Join" />
        </div>
        <div className="live-universe-preview-side">
          <div className="live-universe-preview-table compact">
            <div className="live-universe-subtitle">
              <strong>Tables</strong>
              <span>{integer(tableRows.length)}</span>
            </div>
            <DataTable columns={["name", "engine", "total_rows", "total_bytes"]} empty="No tables returned." fitToContent rows={tableRows} />
          </div>
          <div className="live-universe-preview-table compact">
            <div className="live-universe-subtitle">
              <strong>Columns</strong>
              <span>{integer(columnRows.length)}</span>
            </div>
            <DataTable columns={["table", "name", "type", "position"]} empty="No columns returned." fitToContent rows={columnRows} />
          </div>
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
            <div>
              <strong>{step.label}</strong>
              <span>{formatGateStepStatus(step.status)}</span>
            </div>
            <p>{step.detail}</p>
            {step.duration ? <small>{step.duration}</small> : null}
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

function WorkspaceWindow({
  canvasTargets,
  children,
  icon,
  id,
  layout,
  onClose,
  onFocus,
  onLayoutChange,
  onMoveToCanvas,
  onPopOut,
  title,
}: {
  canvasTargets: LiveCanvasTarget[];
  children: ReactNode;
  icon: ReactNode;
  id: WindowId;
  layout: WindowLayout;
  onClose: (id: WindowId) => void;
  onFocus: (id: WindowId) => void;
  onLayoutChange: (id: WindowId, patch: Partial<WindowLayout>) => void;
  onMoveToCanvas: (id: WindowId, canvasId: string) => void;
  onPopOut: (id: WindowId) => void;
  title: string;
}) {
  const style = layout.fullscreen
    ? { height: "calc(100% - 24px)", left: 12, top: 12, width: "calc(100% - 24px)", zIndex: 1000 + layout.z }
    : { height: layout.minimized ? 34 : layout.h, left: layout.x, top: layout.y, width: layout.w, zIndex: layout.z };

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (layout.fullscreen) return;
    const originX = event.clientX;
    const originY = event.clientY;
    const startX = layout.x;
    const startY = layout.y;
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = event.currentTarget;
    const move = (moveEvent: globalThis.PointerEvent) => {
      onLayoutChange(id, { x: Math.max(0, startX + moveEvent.clientX - originX), y: Math.max(0, startY + moveEvent.clientY - originY) });
    };
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
  }

  function startResize(event: PointerEvent<HTMLDivElement>) {
    if (layout.fullscreen || layout.minimized) return;
    event.stopPropagation();
    const originX = event.clientX;
    const originY = event.clientY;
    const startW = layout.w;
    const startH = layout.h;
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = event.currentTarget;
    const move = (moveEvent: globalThis.PointerEvent) => {
      onLayoutChange(id, {
        h: Math.max(240, startH + moveEvent.clientY - originY),
        w: Math.max(320, startW + moveEvent.clientX - originX),
      });
    };
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
  }

  return (
    <section className="live-window" data-window-kind={id.startsWith("chart-") ? "chart" : id} style={style} onPointerDown={() => onFocus(id)}>
      <div className="live-window-header" onPointerDown={startDrag}>
        <div className="live-window-title">
          <Move size={13} />
          {icon}
          <strong>{title}</strong>
        </div>
        <div className="live-window-actions" onPointerDown={(event) => event.stopPropagation()}>
          <div className="live-canvas-target-row" aria-label={`Move ${title} to canvas`}>
            {canvasTargets.map((target) => (
              <button
                className={target.isCurrent ? "live-canvas-target active" : "live-canvas-target"}
                key={target.id}
                onClick={() => onMoveToCanvas(id, target.id)}
                style={{ "--canvas-color": target.color } as CSSProperties}
                title={target.isCurrent ? `Current: ${target.label}` : `Move to ${target.label}`}
                type="button"
              >
                {target.label.replace("Canvas ", "C").replace("Main", "M")}
              </button>
            ))}
          </div>
          <button className="toolbar-button compact" onClick={() => onPopOut(id)} title="Move to new child canvas" type="button">
            <ExternalLink size={12} />
          </button>
          <button className="toolbar-button compact" onClick={() => onLayoutChange(id, { minimized: !layout.minimized })} title={layout.minimized ? "Restore" : "Minimize"} type="button">
            <Minimize2 size={12} />
          </button>
          <button className="toolbar-button compact" onClick={() => onLayoutChange(id, { fullscreen: !layout.fullscreen, minimized: false })} title={layout.fullscreen ? "Exit fullscreen" : "Fullscreen"} type="button">
            {layout.fullscreen ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
          </button>
          <button className="toolbar-button compact" onClick={() => onClose(id)} title="Close" type="button">
            <X size={12} />
          </button>
        </div>
      </div>
      {!layout.minimized ? <div className="live-window-body">{children}</div> : null}
      {!layout.minimized ? <div className="live-window-resize" onPointerDown={startResize} /> : null}
    </section>
  );
}

function LiveWindowManager({
  canvasTargets,
  onClose,
  onFocus,
  onMinimize,
  onMoveToCanvas,
  onPopOut,
  onShowCoreWindows,
  windows,
}: {
  canvasTargets: LiveCanvasTarget[];
  onClose: (id: WindowId) => void;
  onFocus: (id: WindowId) => void;
  onMinimize: (id: WindowId, minimized: boolean) => void;
  onMoveToCanvas: (id: WindowId, canvasId: string) => void;
  onPopOut: (id: WindowId) => void;
  onShowCoreWindows: () => void;
  windows: LiveWindowSummary[];
}) {
  return (
    <section className="live-window-manager" aria-label="Open workspace windows">
      <div className="live-window-manager-heading">
        <div>
          <span>Open Windows</span>
          <strong>{windows.length ? `${windows.length} active` : "No active windows"}</strong>
        </div>
        <button className="button secondary compact" onClick={onShowCoreWindows} type="button">
          <FolderOpen size={14} /> Core Windows
        </button>
      </div>
      {windows.length ? (
        <div className="live-window-chip-grid">
          {windows.map((windowItem) => (
            <article className="live-window-chip" data-type={windowItem.type} key={windowItem.id}>
              <button className="live-window-chip-main" onClick={() => onFocus(windowItem.id)} type="button">
                {windowItem.type === "chart" ? <BarChart3 size={14} /> : <LayoutGrid size={14} />}
                <span>{windowItem.title}</span>
                <small>{windowItem.minimized ? "Minimized" : windowItem.fullscreen ? "Fullscreen" : `Layer ${windowItem.z}`}</small>
              </button>
              <div className="live-window-chip-actions">
                <div className="live-canvas-target-row" aria-label={`Move ${windowItem.title} to canvas`}>
                  {canvasTargets.map((target) => (
                    <button
                      className={target.isCurrent ? "live-canvas-target active" : "live-canvas-target"}
                      key={target.id}
                      onClick={() => onMoveToCanvas(windowItem.id, target.id)}
                      style={{ "--canvas-color": target.color } as CSSProperties}
                      title={target.isCurrent ? `Current: ${target.label}` : `Move to ${target.label}`}
                      type="button"
                    >
                      {target.label.replace("Canvas ", "C").replace("Main", "M")}
                    </button>
                  ))}
                </div>
                <button className="toolbar-button compact" onClick={() => onFocus(windowItem.id)} title="Show window" type="button">
                  <Eye size={13} />
                </button>
                <button className="toolbar-button compact" onClick={() => onMinimize(windowItem.id, !windowItem.minimized)} title={windowItem.minimized ? "Restore window" : "Minimize window"} type="button">
                  {windowItem.minimized ? <Maximize2 size={13} /> : <Minimize2 size={13} />}
                </button>
                <button className="toolbar-button compact" onClick={() => onPopOut(windowItem.id)} title="Move to new child canvas" type="button">
                  <ExternalLink size={13} />
                </button>
                <button className="toolbar-button compact" onClick={() => onClose(windowItem.id)} title="Close window" type="button">
                  <X size={13} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="live-empty-positions">No open windows on this canvas.</div>
      )}
    </section>
  );
}

function LiveCanvasManager({
  canvases,
  onCreate,
  onOpen,
  onRemove,
}: {
  canvases: LiveCanvasTarget[];
  onCreate: () => void;
  onOpen: (canvasId: string) => void;
  onRemove: (canvasId: string) => void;
}) {
  return (
    <section className="live-canvas-manager" aria-label="Workspace canvases">
      <div className="live-window-manager-heading">
        <div>
          <span>Canvases</span>
          <strong>{canvases.length} page{canvases.length === 1 ? "" : "s"}</strong>
        </div>
        <button className="button secondary compact" onClick={onCreate} type="button">
          <LayoutGrid size={14} /> New Canvas
        </button>
      </div>
      <div className="live-canvas-chip-grid">
        {canvases.map((canvas) => (
          <article className={canvas.isCurrent ? "live-canvas-chip active" : "live-canvas-chip"} key={canvas.id} style={{ "--canvas-color": canvas.color } as CSSProperties}>
            <button className="live-canvas-chip-main" onClick={() => onOpen(canvas.id)} type="button" title={`Open ${canvas.label} in a new tab`}>
              <span>{canvas.label}</span>
              <small>{canvas.isCurrent ? "Current page" : canvas.id}</small>
            </button>
            <div className="live-window-chip-actions">
              <button className="toolbar-button compact" onClick={() => onOpen(canvas.id)} title="Open canvas in new tab" type="button">
                <ExternalLink size={13} />
              </button>
              <button
                className="toolbar-button compact"
                disabled={canvas.id === "main" || canvas.isCurrent}
                onClick={() => onRemove(canvas.id)}
                title={canvas.id === "main" ? "Main canvas cannot be removed" : canvas.isCurrent ? "Current canvas cannot be removed from itself" : "Remove canvas"}
                type="button"
              >
                <X size={13} />
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
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

function MetricsDock({ metrics }: { metrics: ReturnType<typeof buildPortfolioMetrics> }) {
  return (
    <section className="live-metrics-dock" aria-label="Portfolio metrics">
      <div className="live-debug-metric-strip" style={{ gridTemplateColumns: `repeat(${Math.max(metrics.items.length, 1)}, minmax(106px, 1fr))` }}>
        {metrics.items.map((item) => (
          <article className="live-debug-metric-card" data-tone={item.tone} key={item.label}>
            <span className="live-debug-metric-icon">{item.icon}</span>
            <span className="live-debug-metric-label">{item.label}</span>
            <strong>{item.value}</strong>
          </article>
        ))}
      </div>
    </section>
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
  const [chartError, setChartError] = useState("");
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
    setChartError("");
    const dayStart = dateOffset(session.sessionDate, -60);
    const fiveMinuteStart = previousSessionDate(sessions, session.sessionDate, 2);
    Promise.allSettled([
      loadChart(scope.processed_root, session.sessionDate, session.sessionDate, mainTimeframe, chart.ticker, mainVisibleColumns),
      loadChart(scope.processed_root, dayStart, session.sessionDate, "1d", chart.ticker, []),
      loadChart(scope.processed_root, fiveMinuteStart, session.sessionDate, "5m", chart.ticker, compactVisibleColumns),
    ])
      .then(([mainResult, dayResult, fiveResult]) => {
        if (!active) return;
        setMainPayload(mainResult.status === "fulfilled" ? mainResult.value : null);
        setDayPayload(dayResult.status === "fulfilled" ? dayResult.value : null);
        setFiveMinutePayload(fiveResult.status === "fulfilled" ? fiveResult.value : null);
        const firstError = [mainResult, dayResult, fiveResult].find((result) => result.status === "rejected");
        setChartError(firstError && firstError.status === "rejected" ? firstError.reason?.message ?? "One chart failed to load." : "");
      })
      .finally(() => {
        if (active) setChartLoading(false);
      });
    return () => {
      active = false;
    };
  }, [chart.ticker, compactVisibleColumns, mainTimeframe, mainVisibleColumns, scope.processed_root, session.sessionDate, sessions]);

  return (
    <ChartsContainer
      catalog={catalog}
      chartError={chartError}
      chartLoading={chartLoading}
      compactVisibleColumns={compactVisibleColumns}
      dayPayload={dayOpenOnlyPayload}
      fiveMinutePayload={fiveMinuteOpenOnlyPayload}
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
  dayPayload,
  draft,
  fiveMinutePayload,
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
  dayPayload: ChartPayload | null;
  draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string };
  fiveMinutePayload: ChartPayload | null;
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
                  loading={chartLoading}
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
                  loading={chartLoading}
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

function ChartTradePanel({
  availableCash,
  draft,
  onDraftChange,
  onStage,
  orders,
  position,
  quote,
  row,
  selectedTicker,
  session,
}: {
  availableCash: number;
  draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string };
  onDraftChange: (draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string }) => void;
  onStage: (side?: "BUY" | "SELL", status?: string, context?: Partial<StageOrderContext>) => void;
  orders: OrderRow[];
  position?: PositionRow;
  quote: ReturnType<typeof quoteFromRow>;
  row: Record<string, unknown>;
  selectedTicker: string;
  session: TradingSession;
}) {
  const [strategy, setStrategy] = useState("Manual");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [strategySettings, setStrategySettings] = useState({ orderType: "LIMIT", sizeMode: "risk_pct", sizeValue: "10", stopBufferPct: "3" });
  const stopBufferRatio = Math.max(0, Number(strategySettings.stopBufferPct || 3) / 100);
  const bufferedStop = quote.bid * (1 - stopBufferRatio);
  const suggestedStop = numberValue(row, "suggested_stop");
  const vwapStop = numberValue(row, "last_vwap") || bufferedStop;
  const longStop = Math.max(0, Math.min(suggestedStop || vwapStop, bufferedStop, quote.ask || Number.POSITIVE_INFINITY));
  const entryQuantity = calculateLiveOrderQuantity({
    availableCash,
    entry: quote.ask,
    mode: strategySettings.sizeMode,
    side: "BUY",
    stop: longStop,
    value: strategySettings.sizeValue,
  });
  const openOrders = orders.filter((order) => order.symbol === selectedTicker && order.status === "STAGED").length;
  const spreadRatio = quote.ask > 0 ? quote.spread / quote.ask : 0;
  const spreadTone = spreadRatio >= 0.02 || quote.spread >= 0.05 ? "danger" : spreadRatio >= 0.01 || quote.spread >= 0.02 ? "warning" : "success";
  const transactionsTone = quote.transactions <= 0 ? "muted" : quote.transactions < 100 ? "warning" : quote.transactions >= 300 ? "success" : "info";
  const volumeTone = quote.volume <= 0 ? "muted" : quote.volume < 8000 ? "warning" : quote.volume >= 50000 ? "success" : "info";
  const liquidityStats = [
    {
      detail: quote.transactions >= 300 ? "strong" : quote.transactions >= 100 ? "ok" : quote.transactions > 0 ? "thin" : "none",
      label: "Transactions",
      strength: quote.transactionsMarketStrength,
      tone: transactionsTone,
      value: integer(quote.transactions),
      warning: transactionsTone === "warning",
    },
    {
      detail: quote.volume >= 50000 ? "strong" : quote.volume >= 8000 ? "ok" : quote.volume > 0 ? "light" : "none",
      label: "Volume",
      strength: quote.volumeMarketStrength,
      tone: volumeTone,
      value: integer(quote.volume),
      warning: volumeTone === "warning",
    },
  ];
  const spreadWarning = spreadTone === "warning" || spreadTone === "danger";
  const [selectedNewsItem, setSelectedNewsItem] = useState<LiveNewsArticle | null>(null);
  const newsItems = liveNewsItems(row, session);
  const companyNewsItems = newsItems.filter((item) => newsTickerCount(item) <= 1);
  const otherNewsItems = newsItems.filter((item) => newsTickerCount(item) > 1);
  const newsRecency = stringValue(row, "live_news_recency") || "none";
  const actions = buildStrategyTradeActions({
    entryQuantity,
    longStop,
    orderType: strategySettings.orderType,
    position,
    quote,
    selectedTicker,
    strategy,
  });

  function stageStrategyAction(action: LiveStrategyTradeAction) {
    if (action.disabled) return;
    const context = {
      limit: action.limit,
      mark: quote.bid,
      quantity: action.quantity,
      row,
      side: action.side,
      status: "STAGED",
      stop: action.stop,
      symbol: selectedTicker,
      type: action.type,
    };
    onStage(action.side, "STAGED", context);
    onDraftChange({ ...draft, limit: action.limit.toFixed(4), quantity: String(action.quantity), side: action.side, stop: action.stop.toFixed(4), type: action.type });
  }

  return (
    <aside className="live-chart-trade-panel">
      <div className="live-chart-trade-header">
        <strong>{selectedTicker}</strong>
        <div className={position ? "live-trade-status active" : "live-trade-status"}>
          {position ? "In Position" : "Flat"}
        </div>
      </div>
      <div className="live-market-panel">
        <div className="live-inside-market">
          <div className="live-quote-price bid">
            <span>Bid</span>
            <strong>{money(quote.bid)}</strong>
          </div>
          <div className={`live-spread-badge ${spreadTone}`}>
            <span>
              {spreadWarning ? <ShieldAlert size={12} aria-hidden="true" /> : null}
              Spread
            </span>
            <strong>{money(quote.spread)}</strong>
            <small>{spreadRatio > 0 ? percent(spreadRatio) : "n/a"}</small>
          </div>
          <div className="live-quote-price ask">
            <span>Ask</span>
            <strong>{money(quote.ask)}</strong>
          </div>
        </div>
        <div className="live-market-health-list" aria-label="Market quality">
          {liquidityStats.map((stat) => (
            <div
              key={stat.label}
              className={`live-market-row ${stat.tone} has-strength`}
              style={marketStrengthStyle(stat.strength)}
              title={`${stat.label} market percentile: ${percent(stat.strength)}`}
            >
              <span>
                {stat.label}
                {stat.warning ? <ShieldAlert size={12} aria-hidden="true" /> : null}
              </span>
              <strong>{stat.value}</strong>
              <em>{stat.detail}</em>
            </div>
          ))}
        </div>
      </div>
      <div className={`live-news-card ${newsRecency}`} aria-label="Ticker news">
        <div className="live-news-card-header">
          <div>
            <span>News</span>
            <strong>{newsItems.length ? `${newsItems.length} headlines` : "No headlines yet"}</strong>
          </div>
          <div className="live-news-summary-pills" aria-label="News summary">
            <em>Company {companyNewsItems.length}</em>
            <em>Other {otherNewsItems.length}</em>
          </div>
        </div>
        <div className="live-news-sections">
          <LiveNewsSection empty="No single-company headlines by this bar." items={companyNewsItems} title="Company News" onOpen={setSelectedNewsItem} />
          <LiveNewsSection
            collapsible
            defaultOpen={false}
            empty="No multi-ticker or analyst headlines by this bar."
            items={otherNewsItems}
            title="Other / Analyst News"
            onOpen={setSelectedNewsItem}
          />
        </div>
      </div>
      {selectedNewsItem ? <LiveNewsDetailPopover item={selectedNewsItem} onClose={() => setSelectedNewsItem(null)} /> : null}
      <div className="live-execution-panel">
        <div className="live-strategy-row">
          <LiveSelect label="Strategy" value={strategy} values={["Manual", "Momentum Assist"]} onChange={setStrategy} />
          <button className={settingsOpen ? "icon-button active" : "icon-button"} title="Strategy settings" type="button" onClick={() => setSettingsOpen((current) => !current)}>
            <Settings size={15} />
          </button>
        </div>
        {settingsOpen ? (
          <div className="live-strategy-settings">
            <LiveSelect label="Sizing" value={strategySettings.sizeMode} values={["risk_pct", "dollar", "cash_pct", "shares"]} onChange={(value) => setStrategySettings((current) => ({ ...current, sizeMode: value }))} />
            <LiveField label={sizeModeLabel(strategySettings.sizeMode)} type="number" value={strategySettings.sizeValue} onChange={(value) => setStrategySettings((current) => ({ ...current, sizeValue: value }))} />
            <LiveSelect label="Order Type" value={strategySettings.orderType} values={["LIMIT", "MARKET", "STOP"]} onChange={(value) => setStrategySettings((current) => ({ ...current, orderType: value }))} />
            <LiveField label="Stop Buffer %" type="number" value={strategySettings.stopBufferPct} onChange={(value) => setStrategySettings((current) => ({ ...current, stopBufferPct: value }))} />
          </div>
        ) : null}
        <div className={`live-action-panel count-${actions.length}${actions.length > 2 ? " many" : ""}`}>
          {actions.map((action) => (
            <button
              key={action.id}
              className={`live-strategy-action ${action.tone}`}
              disabled={action.disabled || !selectedTicker || action.quantity <= 0}
              title={action.description}
              type="button"
              onClick={() => stageStrategyAction(action)}
            >
              <span>{action.label}</span>
              <strong>{action.side === "BUY" ? "Buy" : action.label}</strong>
              <small>{integer(action.quantity)} sh</small>
              <em>{money(action.limit)}</em>
            </button>
          ))}
        </div>
        <dl className="live-execution-summary">
          <div><dt>Size</dt><dd>{integer(entryQuantity)} sh</dd></div>
          <div><dt>Risk</dt><dd>{money(Math.max(0, quote.ask - longStop) * entryQuantity)}</dd></div>
          <div><dt>Cash</dt><dd>{money(availableCash)}</dd></div>
          <div><dt>Staged</dt><dd>{integer(openOrders)}</dd></div>
        </dl>
        {position ? (
          <div className={(quote.bid - position.avg_price) * position.quantity >= 0 ? "live-chart-position-strip positive" : "live-chart-position-strip negative"}>
            <div>
              <span>{integer(position.quantity)} sh</span>
              <strong>{money(position.avg_price)}</strong>
            </div>
            <div>
              <span>P/L</span>
              <strong>{money((quote.bid - position.avg_price) * position.quantity)}</strong>
              <small>{percent(position.avg_price > 0 ? quote.bid / position.avg_price - 1 : 0)}</small>
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function LiveNewsSection({
  collapsible = false,
  defaultOpen = true,
  empty,
  items,
  onOpen,
  title,
}: {
  collapsible?: boolean;
  defaultOpen?: boolean;
  empty: string;
  items: LiveNewsArticle[];
  onOpen: (item: LiveNewsArticle) => void;
  title: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const showBody = !collapsible || open;
  return (
    <section className={showBody ? "live-news-section" : "live-news-section collapsed"}>
      <button className={collapsible ? "live-news-section-title collapsible" : "live-news-section-title"} type="button" onClick={() => collapsible && setOpen((current) => !current)}>
        <span>{title}</span>
        <strong>{collapsible ? `${items.length} ${open ? "Hide" : "Show"}` : items.length}</strong>
      </button>
      {showBody && items.length ? (
        <div className="live-news-list">
          {items.map((item, index) => (
            <LiveNewsItem item={item} key={`${item.published_et}-${index}`} onOpen={onOpen} />
          ))}
        </div>
      ) : showBody ? (
        <p>{empty}</p>
      ) : null}
    </section>
  );
}

function LiveNewsItem({ item, onOpen }: { item: LiveNewsArticle; onOpen: (item: LiveNewsArticle) => void }) {
  const indicator = liveNewsIndicator(item);
  const NewsIcon = indicator.icon;
  return (
    <button className="live-news-item-button" type="button" onClick={() => onOpen(item)} title={item.title}>
      <div className="live-news-meta">
        <time dateTime={item.published_et}>{formatNewsDateTime(item.published_et)}</time>
      </div>
      <div className="live-news-title-row">
        <NewsIcon className={`live-news-type-icon ${indicator.className}`} size={15} aria-label={indicator.label} />
        <strong>{item.title}</strong>
      </div>
      <div className="live-news-labels" aria-label="News labels">
        <span className={`live-news-category-label ${indicator.className}`}>{indicator.label}</span>
        {newsLabels(item).map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
    </button>
  );
}

function LiveNewsDetailPopover({ item, onClose }: { item: LiveNewsArticle; onClose: () => void }) {
  const indicator = liveNewsIndicator(item);
  const NewsIcon = indicator.icon;
  const bodyText = [item.teaser, item.body_text].map((value) => String(value || "").trim()).filter(Boolean).join("\n\n");
  const pdfText = String(item.pdf_text || "").trim();
  const [textZoom, setTextZoom] = useState(1);
  const textZoomLabel = `${Math.round(textZoom * 100)}%`;
  const textZoomStyle = { "--live-news-text-zoom": textZoom } as CSSProperties;
  return (
    <div className="live-news-detail-backdrop" role="presentation" onMouseDown={onClose}>
      <aside
        className="live-news-detail-popover"
        role="dialog"
        aria-modal="true"
        aria-label="News details"
        style={textZoomStyle}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="live-news-detail-header">
          <div>
            <span className={`live-news-detail-category ${indicator.className}`}>
              <NewsIcon size={15} />
              {indicator.label}
            </span>
            <h3>{item.title}</h3>
            <time dateTime={item.published_et}>{formatNewsDateTime(item.published_et)}</time>
          </div>
          <div className="live-news-detail-actions">
            <div className="live-news-zoom-control" aria-label="Article text zoom">
              <button type="button" title="Decrease text size" onClick={() => setTextZoom((value) => Math.max(0.9, Number((value - 0.1).toFixed(2))))}>
                A-
              </button>
              <span>{textZoomLabel}</span>
              <button type="button" title="Increase text size" onClick={() => setTextZoom((value) => Math.min(1.6, Number((value + 0.1).toFixed(2))))}>
                A+
              </button>
            </div>
            <button className="icon-button" type="button" title="Close news" onClick={onClose}>
              <X size={15} />
            </button>
          </div>
        </header>
        <div className="live-news-detail-labels">
          {newsLabels(item, 8).map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
        <div className="live-news-detail-scroll">
          <section>
            <h4>Article</h4>
            {bodyText ? <p className="live-news-detail-text">{bodyText}</p> : <p className="muted">No article text was cached for this headline.</p>}
          </section>
          {pdfText ? (
            <section>
              <h4>PDF Text</h4>
              <p className="live-news-detail-text">{pdfText}</p>
            </section>
          ) : null}
          {item.url ? (
            <section>
              <h4>Source</h4>
              <p className="live-news-detail-source">{item.url}</p>
            </section>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

type LiveStrategyTradeAction = {
  description: string;
  disabled?: boolean;
  id: string;
  label: string;
  limit: number;
  quantity: number;
  side: "BUY" | "SELL";
  stop: number;
  tone: "buy" | "sell" | "neutral";
  type: string;
};

function buildStrategyTradeActions({
  entryQuantity,
  longStop,
  orderType,
  position,
  quote,
  selectedTicker,
  strategy,
}: {
  entryQuantity: number;
  longStop: number;
  orderType: string;
  position?: PositionRow;
  quote: ReturnType<typeof quoteFromRow>;
  selectedTicker: string;
  strategy: string;
}): LiveStrategyTradeAction[] {
  const closeQuantity = Math.max(0, Math.floor(position?.quantity ?? 0));
  const commonClose = {
    description: position ? `Sell ${integer(closeQuantity)} shares at ${money(quote.bid)}` : "Requires an open position",
    disabled: !position || closeQuantity <= 0,
    id: `${strategy}-close`,
    label: "Close",
    limit: quote.bid,
    quantity: closeQuantity,
    side: "SELL" as const,
    stop: position?.stop ?? 0,
    tone: "sell" as const,
    type: "LIMIT",
  };

  if (strategy === "Momentum Assist") {
    return [
      {
        description: `Buy ${integer(entryQuantity)} shares at ${money(quote.ask)}`,
        disabled: !selectedTicker || entryQuantity <= 0,
        id: "momentum-enter",
        label: "Enter",
        limit: quote.ask,
        quantity: entryQuantity,
        side: "BUY",
        stop: longStop,
        tone: "buy",
        type: orderType,
      },
      {
        description: position ? `Sell ${integer(closeQuantity)} shares at ${money(quote.bid)}` : "Requires an open position",
        disabled: !position || closeQuantity <= 0,
        id: "momentum-pocket",
        label: "Pocket",
        limit: quote.bid,
        quantity: closeQuantity,
        side: "SELL",
        stop: position?.stop ?? 0,
        tone: "neutral",
        type: "LIMIT",
      },
      commonClose,
    ];
  }

  return [
    {
      description: `Buy ${integer(entryQuantity)} shares at ${money(quote.ask)}`,
      disabled: !selectedTicker || entryQuantity <= 0,
      id: "manual-buy",
      label: "Buy Ask",
      limit: quote.ask,
      quantity: entryQuantity,
      side: "BUY",
      stop: longStop,
      tone: "buy",
      type: orderType,
    },
    commonClose,
  ];
}

function LiveField({
  label,
  onChange,
  step,
  type,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  step?: string;
  type: string;
  value: string;
}) {
  return (
    <label className="live-field">
      <span>{label}</span>
      <input step={step} type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function LiveSelect({ label, onChange, value, values }: { label: string; onChange: (value: string) => void; value: string; values: string[] }) {
  return (
    <label className="live-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {values.length ? values.map((item) => <option key={item} value={item}>{item}</option>) : <option value={value}>{value || "-"}</option>}
      </select>
    </label>
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

function normalizeRealLiveScannerRow(row: Record<string, unknown>, session: TradingSession): Record<string, unknown> {
  const ticker = stringValue(row, "symbol") || stringValue(row, "ticker");
  const lastPrice = numberValue(row, "current_open") || numberValue(row, "last_price") || numberValue(row, "price");
  const bid = numberValue(row, "bid");
  const ask = numberValue(row, "ask");
  const dayVolume = numberValue(row, "last_day_volume_so_far") || numberValue(row, "day_volume");
  const tradeCount = numberValue(row, "last_transactions") || numberValue(row, "trade_count");
  const dayChange = numberValue(row, "last_day_current_change_pct") || numberValue(row, "day_change_pct");
  const dayNotional = numberValue(row, "last_day_dollar_volume_so_far") || numberValue(row, "day_notional") || dayVolume * lastPrice;
  const vwap = numberValue(row, "last_vwap") || lastPrice;
  return {
    ...row,
    ticker,
    bar_time_market: stringValue(row, "bar_time_market") || `${session.sessionDate}T${session.barTime}:00-04:00`,
    current_open: lastPrice,
    last_close: lastPrice,
    last_open: lastPrice,
    last_high: lastPrice,
    last_low: lastPrice,
    last_return_5: dayChange,
    last_volume: dayVolume,
    last_recent_volume_5: dayVolume,
    last_transactions: tradeCount,
    last_transactions_vs_prior_3: 0,
    last_day_open: lastPrice > 0 && dayChange > -0.99 ? lastPrice / (1 + dayChange) : lastPrice,
    last_day_high_so_far: lastPrice,
    last_day_low_so_far: lastPrice,
    last_day_volume_so_far: dayVolume,
    last_day_dollar_volume_so_far: dayNotional,
    last_vwap: vwap,
    bid,
    ask,
    spread_bps_abs: numberValue(row, "spread_bps_abs") || numberValue(row, "spread_bps"),
    live_bias: stringValue(row, "market_state") || (dayChange > 0 ? "bullish" : dayChange < 0 ? "bearish" : "neutral"),
    live_news_count: 0,
    live_news_items: [],
    live_news_latest_time: "",
    live_news_latest_title: "",
    live_news_recency: "none",
    live_news_recent: false,
    live_setup_group: stringValue(row, "signal_type") || stringValue(row, "market_state") || "massive-live",
    suggested_entry: ask || lastPrice,
    suggested_stop: bid || lastPrice * 0.97,
  };
}

function normalizeRealLivePosition(row: Record<string, unknown>): PositionRow {
  const symbol = stringValue(row, "symbol");
  const quantity = numberValue(row, "quantity");
  const avgPrice = numberValue(row, "avg_price");
  const mark = numberValue(row, "mark_price") || avgPrice;
  const unrealizedPnl = numberValue(row, "unrealized_pnl") || (mark - avgPrice) * quantity;
  return {
    account_class: stringValue(row, "account_class"),
    account_id: stringValue(row, "account_id"),
    account_key: stringValue(row, "account_key"),
    account_label: stringValue(row, "account_label"),
    asset_class: stringValue(row, "asset_class"),
    avg_price: avgPrice,
    conid: stringValue(row, "conid"),
    currency: stringValue(row, "currency"),
    mark,
    market_value: optionalNumber(row, "market_value") ?? mark * quantity,
    quantity,
    realized_pnl: optionalNumber(row, "realized_pnl"),
    stop: 0,
    symbol,
    unrealized_pnl: unrealizedPnl,
    unrealized_pnl_pct: avgPrice > 0 ? unrealizedPnl / (avgPrice * Math.abs(quantity || 1)) : 0,
  };
}

function normalizeRealLiveOrder(row: Record<string, unknown>): OrderRow {
  const quantity = numberValue(row, "quantity");
  const filled = numberValue(row, "filled_quantity");
  const brokerOrderId = stringValue(row, "broker_order_id");
  return {
    account_class: stringValue(row, "account_class"),
    account_id: stringValue(row, "account_id"),
    account_key: stringValue(row, "account_key"),
    account_label: stringValue(row, "account_label"),
    account_type: stringValue(row, "account_key"),
    avg_fill_price: optionalNumber(row, "avg_fill_price"),
    broker_order_id: brokerOrderId,
    client_order_id: stringValue(row, "client_order_id"),
    conid: stringValue(row, "conid"),
    filled_quantity: filled,
    id: `${stringValue(row, "account_key") || "account"}-${brokerOrderId || stringValue(row, "client_order_id") || `${stringValue(row, "symbol")}-${stringValue(row, "submitted_at")}`}`,
    last_fill_price: optionalNumber(row, "last_fill_price"),
    limit: numberValue(row, "limit_price"),
    quantity,
    remaining_quantity: numberValue(row, "remaining_quantity") || Math.max(0, quantity - filled),
    side: stringValue(row, "side") === "SELL" ? "SELL" : "BUY",
    status: stringValue(row, "status") || "UNKNOWN",
    stop: 0,
    symbol: stringValue(row, "symbol"),
    timestamp: stringValue(row, "submitted_at"),
    type: stringValue(row, "order_type"),
  };
}

function normalizeRealLiveExecution(row: Record<string, unknown>): TradeRow {
  const fillPrice = numberValue(row, "fill_price");
  const quantity = numberValue(row, "filled_quantity");
  const timestamp = stringValue(row, "timestamp");
  const sideText = stringValue(row, "side");
  return {
    account_class: stringValue(row, "account_class"),
    account_id: stringValue(row, "account_id"),
    account_key: stringValue(row, "account_key"),
    account_label: stringValue(row, "account_label"),
    broker_order_id: stringValue(row, "broker_order_id"),
    commission: optionalNumber(row, "commission"),
    conid: stringValue(row, "conid"),
    entry_price: sideText === "BUY" ? fillPrice : 0,
    entry_time: timestamp,
    execution_id: stringValue(row, "execution_id"),
    exit_order_id: stringValue(row, "broker_order_id"),
    exit_price: sideText === "SELL" ? fillPrice : 0,
    exit_session_date: timestamp.split(" ")[0] || "",
    exit_time: timestamp,
    gross_pnl: numberValue(row, "gross_amount"),
    gross_pnl_pct: 0,
    id: `${stringValue(row, "account_key") || "account"}-${stringValue(row, "execution_id") || stringValue(row, "broker_order_id") || `${stringValue(row, "symbol")}-${timestamp}`}`,
    quantity,
    side: "LONG",
    symbol: stringValue(row, "symbol"),
  };
}

function scannerQueryFromConditions(conditions: BackendTableQuery["conditions"]): BackendTableQuery {
  return {
    conditions,
    matchMode: "all",
    sortColumn: "last_return_5",
    sortDirection: "desc",
  };
}

function emptyScannerQuery(): BackendTableQuery {
  return { conditions: [], matchMode: "all", sortDirection: "asc" };
}

function normalizeLiveScannerQuery(query: BackendTableQuery | null): BackendTableQuery | null {
  if (!query) return null;
  return {
    ...query,
    conditions: (query.conditions ?? []).map((condition) => ({
      ...condition,
      column: condition.column === "last_5m_return" ? "last_return_5" : condition.column,
    })),
    sortColumn: query.sortColumn === "last_5m_return" ? "last_return_5" : query.sortColumn,
  };
}

function rowMatchesBackendQuery(row: Record<string, unknown>, query: BackendTableQuery | null) {
  const conditions = query?.conditions ?? [];
  if (!conditions.length) return true;
  const results = conditions.map((condition) => rowMatchesBackendCondition(row, condition));
  return (query?.matchMode ?? "all") === "any" ? results.some(Boolean) : results.every(Boolean);
}

function rowMatchesBackendCondition(row: Record<string, unknown>, condition: BackendTableQuery["conditions"][number]) {
  const column = condition.column === "last_5m_return" ? "last_return_5" : condition.column;
  const value = row[column];
  const operator = condition.operator ?? "contains";
  if (operator === "is_null") return isBlankLiveValue(value);
  if (operator === "is_not_null") return !isBlankLiveValue(value);
  if (isBlankLiveValue(value)) return false;
  if (operator === "contains" || operator === "starts_with" || operator === "ends_with") {
    const left = String(value).toLowerCase();
    const right = String(condition.value ?? "").toLowerCase();
    if (!right) return false;
    if (operator === "contains") return left.includes(right);
    if (operator === "starts_with") return left.startsWith(right);
    return left.endsWith(right);
  }
  const leftNumber = Number(value);
  const rightNumber = Number(condition.value);
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    if (operator === "eq") return leftNumber === rightNumber;
    if (operator === "ne") return leftNumber !== rightNumber;
    if (operator === "gt") return leftNumber > rightNumber;
    if (operator === "gte") return leftNumber >= rightNumber;
    if (operator === "lt") return leftNumber < rightNumber;
    if (operator === "lte") return leftNumber <= rightNumber;
    if (operator === "between") {
      const secondaryNumber = Number(condition.valueSecondary);
      if (!Number.isFinite(secondaryNumber)) return false;
      const lower = Math.min(rightNumber, secondaryNumber);
      const upper = Math.max(rightNumber, secondaryNumber);
      return leftNumber >= lower && leftNumber <= upper;
    }
  }
  const leftText = String(value);
  const rightText = String(condition.value ?? "");
  if (operator === "eq") return leftText === rightText;
  if (operator === "ne") return leftText !== rightText;
  return false;
}

function isBlankLiveValue(value: unknown) {
  return value === null || value === undefined || value === "" || (typeof value === "number" && !Number.isFinite(value));
}

function buildMarketStateRows(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  const marketRows = rows.map(buildMarketStateRow);
  const transactionValues = sortedPositiveValues(marketRows.map((row) => numberValue(row, "last_transactions")));
  const dollarVolumeValues = sortedPositiveValues(marketRows.map((row) => numberValue(row, "last_bar_dollar_volume")));
  return marketRows
    .map((row) => ({
      ...row,
      last_dollar_volume_market_strength: percentileRank(numberValue(row, "last_bar_dollar_volume"), dollarVolumeValues),
      last_transactions_market_strength: percentileRank(numberValue(row, "last_transactions"), transactionValues),
    }))
    .sort((a, b) => numberValue(b, "last_day_volume_so_far") - numberValue(a, "last_day_volume_so_far"));
}

function buildMarketStateRow(row: Record<string, unknown>): Record<string, unknown> {
  const dayOpen = numberValue(row, "last_day_open");
  const dayHigh = numberValue(row, "last_day_high_so_far");
  const currentOpen = numberValue(row, "current_open") || numberValue(row, "open");
  const lastClose = numberValue(row, "last_close");
  const currentReference = currentOpen || lastClose;
  return {
    ...row,
    last_bar_dollar_volume: currentReference > 0 ? numberValue(row, "last_volume") * currentReference : null,
    last_day_current_change_pct: dayOpen > 0 && currentReference > 0 ? (currentReference / dayOpen) - 1 : null,
    last_day_max_change_pct: dayOpen > 0 && dayHigh > 0 ? (dayHigh / dayOpen) - 1 : null,
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

function enrichLiveCandidate(row: Record<string, unknown>, queryName: string): Record<string, unknown> {
  const currentOpen = numberValue(row, "current_open") || numberValue(row, "open");
  const lastVwap = numberValue(row, "last_vwap");
  const lastClose = numberValue(row, "last_close");
  const lastOpen = numberValue(row, "last_open");
  const dayHigh = numberValue(row, "last_day_high_so_far");
  const lastLow = numberValue(row, "last_low");
  const last5mReturn = numberValue(row, "last_return_5") || numberValue(row, "last_5m_return");
  const transactions = numberValue(row, "last_transactions");
  const txRatio = numberValue(row, "last_transactions_vs_prior_3");
  const bvd = numberValue(row, "last_bearish_volume_divergence_score");
  const aboveVwap = lastVwap > 0 && currentOpen > lastVwap;
  const breakingBody = Boolean(row.current_open_above_last_2_body_high);
  const nearDayHigh = dayHigh > 0 && currentOpen >= dayHigh * 0.995;
  const lastRed = lastClose > 0 && lastOpen > 0 && lastClose < lastOpen;
  const extendedVwap = lastVwap > 0 ? (currentOpen / lastVwap) - 1 : 0;
  const reasons = [
    queryName || "Query match",
    `5m ${percent(last5mReturn)}`,
    `${integer(transactions)} tx`,
    `${number(txRatio, 1)}x tx`,
    aboveVwap ? `open > VWAP by ${percent(extendedVwap)}` : "",
    breakingBody ? "body break" : "",
    nearDayHigh ? "near day high" : "",
  ].filter(Boolean);
  const risks = [
    !aboveVwap ? "below VWAP" : "",
    lastRed ? "last candle red" : "",
    bvd > 50 ? `BVD ${number(bvd, 0)}` : "",
    extendedVwap > 0.12 ? `extended ${percent(extendedVwap)} from VWAP` : "",
  ].filter(Boolean);
  const priority = 100 + last5mReturn * 100 + Math.min(25, txRatio) + (aboveVwap ? 10 : 0) + (breakingBody ? 8 : 0) - risks.length * 8;
  const bias = risks.length >= 2 ? "Risk" : aboveVwap && !lastRed ? "Ready" : "Watch";
  const stopBase = lastVwap > 0 ? lastVwap * 0.99 : Math.min(lastLow || currentOpen * 0.98, currentOpen * 0.98);
  return {
    ...buildMarketStateRow(row),
    body_break_open: breakingBody,
    day_high_pressure: nearDayHigh,
    live_bias: bias,
    live_priority: priority,
    live_reasons: reasons.join(" | "),
    live_risks: risks.join(" | "),
    live_setup_group: queryName || "Query Match",
    open_vs_vwap_pct: extendedVwap,
    suggested_entry: currentOpen || lastClose,
    suggested_stop: stopBase,
  };
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

function marketStateTableColumns(snapshotColumns: string[]) {
  const hiddenColumns = new Set(["live_news_count", "live_news_latest_title", "live_news_latest_time"]);
  const importantColumns = [
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
    "last_day_low_so_far",
    "last_vwap",
    "last_bearish_volume_divergence_score",
    "last_double_timeframe_bearish_volume_divergence_score",
    "spread_bps_abs",
  ];
  return [
    ...importantColumns,
    ...snapshotColumns.filter((column) => !importantColumns.includes(column) && !hiddenColumns.has(column)),
  ];
}

function latestLiveChartRow(chart: ChartWindow, marketRows: Record<string, unknown>[], scannerRows: Record<string, unknown>[]) {
  const ticker = chart.ticker.trim().toUpperCase();
  const matchesTicker = (row: Record<string, unknown>) => stringValue(row, "ticker").trim().toUpperCase() === ticker;
  const marketRow = marketRows.find(matchesTicker);
  const scannerRow = scannerRows.find(matchesTicker);
  return {
    ...chart.row,
    ...(scannerRow ?? {}),
    ...(marketRow ?? {}),
  };
}

function quoteFromRow(row: Record<string, unknown>, fallbackOpen: number) {
  const last = fallbackOpen || numberValue(row, "current_open") || numberValue(row, "open") || numberValue(row, "last_close");
  const ask = numberValue(row, "ask") || last;
  const bid = numberValue(row, "bid") || Math.max(0, ask - 0.01);
  return {
    ask,
    bid,
    spread: Math.max(0, ask - bid),
    transactions: numberValue(row, "last_transactions"),
    transactionsMarketStrength: numberValue(row, "last_transactions_market_strength"),
    volume: numberValue(row, "last_volume"),
    volumeMarketStrength: numberValue(row, "last_dollar_volume_market_strength"),
  };
}

function liveNewsItems(row: Record<string, unknown>, session: TradingSession): LiveNewsArticle[] {
  const value = row.live_news_items;
  if (!Array.isArray(value)) return [];
  const cutoffSeconds = clockTimestampSeconds(session.sessionDate, session.barTime);
  return value
    .filter((item): item is LiveNewsArticle => Boolean(item && typeof item === "object" && "title" in item))
    .filter((item) => {
      if (!cutoffSeconds) return true;
      const publishedSeconds = Math.floor(Date.parse(item.published_et) / 1000);
      return Number.isFinite(publishedSeconds) && publishedSeconds <= cutoffSeconds;
    })
    .slice(0, 8);
}

function formatNewsDateTime(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "";
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    timeZone: "America/New_York",
    timeZoneName: "short",
    year: "numeric",
  }).format(new Date(timestamp));
}

function newsLabels(item: LiveNewsArticle, maxLabels = 3) {
  const labels = [...(item.tickers?.length ? item.tickers : [item.ticker]), ...(item.channels ?? []), ...(item.tags ?? [])]
    .map((label) => String(label || "").trim())
    .filter(Boolean);
  return Array.from(new Set(labels)).slice(0, maxLabels);
}

function liveNewsIndicator(item: LiveNewsArticle): { className: string; icon: typeof Newspaper; label: string } {
  if (newsTickerCount(item) > 1) return { className: "multi", icon: Newspaper, label: "Market News" };
  if ((item.recency || "").toLowerCase() === "hot") return { className: "hot-company", icon: Megaphone, label: "Company News" };
  return { className: "company", icon: Flame, label: "Company News" };
}

function newsTickerCount(item: LiveNewsArticle) {
  if (Number.isFinite(item.ticker_count) && Number(item.ticker_count) > 0) return Number(item.ticker_count);
  return item.tickers?.length || 1;
}

function marketStrengthStyle(value: number): CSSProperties {
  const strength = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return { "--strength": `${Math.round(strength * 100)}%` } as CSSProperties;
}

function sortedPositiveValues(values: number[]) {
  return values.filter((value) => Number.isFinite(value) && value > 0).sort((left, right) => left - right);
}

function percentileRank(value: number, sortedValues: number[]) {
  if (!Number.isFinite(value) || value <= 0 || !sortedValues.length) return 0;
  let low = 0;
  let high = sortedValues.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (sortedValues[mid] <= value) low = mid + 1;
    else high = mid;
  }
  return low / sortedValues.length;
}

function calculateLiveOrderQuantity({
  availableCash,
  entry,
  mode,
  side,
  stop,
  value,
}: {
  availableCash: number;
  entry: number;
  mode: string;
  side: "BUY" | "SELL";
  stop: number;
  value: string;
}) {
  const numeric = Math.max(0, Number(value) || 0);
  if (!entry || entry <= 0) return 0;
  if (mode === "shares") return Math.floor(numeric);
  const cashCapShares = Math.floor(availableCash / entry);
  if (mode === "dollar") return Math.max(0, Math.min(cashCapShares, Math.floor(numeric / entry)));
  if (mode === "cash_pct") return Math.max(0, Math.min(cashCapShares, Math.floor((availableCash * numeric / 100) / entry)));
  const riskPerShare = Math.max(0.0001, side === "SELL" ? stop - entry : entry - stop);
  return Math.max(0, Math.min(cashCapShares, Math.floor((availableCash * numeric / 100) / riskPerShare)));
}

function sizeModeLabel(mode: string) {
  if (mode === "dollar") return "Dollars";
  if (mode === "cash_pct") return "% Cash";
  if (mode === "shares") return "Shares";
  return "% Risk";
}

function buildLiveEntryLine(position: PositionRow | undefined, currentBid: number): LiveEntryLine | null {
  if (!position || !position.quantity || !position.avg_price) return null;
  const pnl = (currentBid - position.avg_price) * position.quantity;
  return {
    color: "#2563eb",
    pnl,
    price: position.avg_price,
    quantity: position.quantity,
  };
}

function upsertPosition(rows: PositionRow[], symbol: string, quantity: number, price: number, stop: number, mark: number, entrySessionDate?: string, entryTime?: string): PositionRow[] {
  const existing = rows.find((row) => row.symbol === symbol);
  const nextQuantity = (existing?.quantity ?? 0) + quantity;
  const avgPrice = existing ? ((existing.avg_price * existing.quantity) + (price * quantity)) / Math.max(1, nextQuantity) : price;
  const row = {
    avg_price: avgPrice,
    entry_session_date: existing?.entry_session_date ?? entrySessionDate,
    entry_time: existing?.entry_time ?? entryTime,
    mark,
    quantity: nextQuantity,
    stop,
    symbol,
    unrealized_pnl: (mark - avgPrice) * nextQuantity,
    unrealized_pnl_pct: avgPrice > 0 ? (mark / avgPrice) - 1 : 0,
  };
  return [row, ...rows.filter((item) => item.symbol !== symbol)];
}

function reducePosition(rows: PositionRow[], symbol: string, quantity: number, mark: number): PositionRow[] {
  return rows.flatMap((row) => {
    if (row.symbol !== symbol) return [row];
    const nextQuantity = Math.max(0, row.quantity - quantity);
    if (nextQuantity <= 0) return [];
    return [{
      ...row,
      mark,
      quantity: nextQuantity,
      unrealized_pnl: (mark - row.avg_price) * nextQuantity,
      unrealized_pnl_pct: row.avg_price > 0 ? (mark / row.avg_price) - 1 : 0,
    }];
  });
}

function buildClosedTrade(position: PositionRow, quantity: number, exitPrice: number, exitSessionDate: string, exitTime: string, exitOrderId: string): TradeRow {
  const closedQuantity = Math.max(0, Math.min(quantity, position.quantity));
  const grossPnl = (exitPrice - position.avg_price) * closedQuantity;
  return {
    entry_price: position.avg_price,
    entry_session_date: position.entry_session_date,
    entry_time: position.entry_time,
    exit_order_id: exitOrderId,
    exit_price: exitPrice,
    exit_session_date: exitSessionDate,
    exit_time: exitTime,
    gross_pnl: grossPnl,
    gross_pnl_pct: position.avg_price > 0 ? (exitPrice / position.avg_price) - 1 : 0,
    id: `${exitOrderId}-trade`,
    quantity: closedQuantity,
    side: "LONG",
    symbol: position.symbol,
  };
}

function realizedPnlFromTrades(trades: TradeRow[]) {
  return trades.reduce((total, row) => total + row.gross_pnl, 0);
}

function positionExposure(positions: PositionRow[]) {
  return positions.reduce((total, row) => total + (row.market_value ?? row.mark * row.quantity), 0);
}

function buildProfitLossRows(positions: PositionRow[], trades: TradeRow[], snapshot: RealLivePortfolioPayload | null) {
  const brokerPnl = brokerPnlRows(snapshot);
  return [
    ...brokerPnl,
    ...positions.map((row) => ({
      account: row.account_label,
      avg_price: row.avg_price,
      mark: row.mark,
      pnl: row.unrealized_pnl,
      pnl_pct: row.unrealized_pnl_pct,
      quantity: row.quantity,
      status: "OPEN",
      symbol: row.symbol,
    })),
    ...trades.map((row) => ({
      account: row.account_label,
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

function portfolioBalanceRows(snapshot: RealLivePortfolioPayload | null): Record<string, unknown>[] {
  return (snapshot?.balances ?? []).filter((row) => row && typeof row === "object");
}

function brokerPnlRows(snapshot: RealLivePortfolioPayload | null): Record<string, unknown>[] {
  return (snapshot?.pnl ?? []).filter((row) => row && typeof row === "object").map((row) => ({ ...row, status: "BROKER_PNL" }));
}

function brokerAvailableFunds(snapshot: RealLivePortfolioPayload | null) {
  const balances = portfolioBalanceRows(snapshot);
  const available = balances.reduce((total, row) => total + numberValue(row, "available_funds"), 0);
  if (available > 0) return available;
  return balances.reduce((total, row) => total + numberValue(row, "cash"), 0);
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
  loading,
  preflightStatus,
  selectedAccountKeys,
  universePreview,
  universePreviewLoading,
}: {
  loading: boolean;
  preflightStatus: RealLivePreflightPayload | null;
  selectedAccountKeys: string[];
  universePreview: RealLiveUniversePreviewPayload | null;
  universePreviewLoading: boolean;
}): GateProgressStep[] {
  const errors = universePreview?.errors ?? [];
  const backendSteps = universePreview?.progress_steps ?? [];
  const requestError = errors.find((error) => ["request", "connection"].includes(stringValue(error, "scope")));
  const metadataError = errors.find((error) => ["tables", "columns"].includes(stringValue(error, "scope")));
  const persistenceStatus = stringValue(universePreview?.persistence, "status") || "read_only_preview";
  return [
    {
      detail: selectedAccountKeys.length ? `${selectedAccountKeys.length} account${selectedAccountKeys.length > 1 ? "s" : ""} selected` : "Select at least one account before entering the workspace.",
      id: "account_selection",
      label: "Account selection",
      status: selectedAccountKeys.length ? "complete" : "waiting",
      tone: selectedAccountKeys.length ? "success" : "muted",
    },
    {
      detail: preflightStatus?.checks?.length ? `${preflightStatus.checks.filter((check) => check.status === "ready").length} of ${preflightStatus.checks.length} checks ready` : "Massive and IBKR checks have not been run yet.",
      id: "connection_check",
      label: "Connection checks",
      status: loading ? "running" : preflightStatus?.ready ? "complete" : preflightStatus ? "blocked" : "waiting",
      tone: loading ? "warning" : preflightStatus?.ready ? "success" : preflightStatus ? "danger" : "muted",
    },
    {
      detail: requestError ? stringValue(requestError, "message") : metadataError ? stringValue(metadataError, "message") : universePreview ? `${integer(universePreview.tables.length)} tables, ${integer(universePreview.columns.length)} columns inspected` : "Waiting for ClickHouse metadata.",
      id: "metadata",
      label: "ClickHouse metadata",
      status: universePreviewLoading ? "running" : requestError || metadataError ? "error" : universePreview ? "complete" : "waiting",
      tone: universePreviewLoading ? "warning" : requestError || metadataError ? "danger" : universePreview ? "success" : "muted",
    },
    ...backendSteps.map((step) => progressStepFromBackend(step, universePreviewLoading)),
    {
      detail: persistenceStatus === "read_only_preview" ? "Initial page validation does not create a trading session or write replay rows." : requestError ? "Read-only preview could not be confirmed because the API request failed." : `Preview returned persistence status: ${persistenceStatus}`,
      id: "read_only_preview",
      label: "Preview persistence policy",
      status: persistenceStatus,
      tone: persistenceStatus === "read_only_preview" ? "success" : persistenceStatus === "failed" ? "danger" : "warning",
    },
    {
      detail: preflightStatus?.ready && universePreview?.can_query_universe ? "Entering the workspace will create trading_session_id and start async baseline recording." : "Requires ready connections and a valid read-only universe preview.",
      id: "session_entry",
      label: "Session entry",
      status: preflightStatus?.ready && universePreview?.can_query_universe ? "ready" : "waiting",
      tone: preflightStatus?.ready && universePreview?.can_query_universe ? "success" : "muted",
    },
  ];
}

function progressStepFromBackend(step: RealLiveProgressStep, loading: boolean): GateProgressStep {
  const status = step.status || (loading ? "running" : "waiting");
  return {
    detail: step.detail || "No detail returned.",
    duration: typeof step.duration_ms === "number" ? `${Math.round(step.duration_ms)} ms` : "",
    id: step.id,
    label: step.label,
    status,
    tone: gateToneFromStatus(status),
  };
}

function gateToneFromStatus(status: string): GateProgressStep["tone"] {
  if (["success", "complete", "ready", "read_only_preview"].includes(status)) return "success";
  if (["failed", "error", "blocked"].includes(status)) return "danger";
  if (["running", "pending", "deferred"].includes(status)) return "warning";
  if (["waiting", "not_started"].includes(status)) return "muted";
  return "info";
}

function buildLiveWindowSummaries(openWindows: WindowId[], chartWindows: ChartWindow[], layouts: Record<WindowId, WindowLayout>): LiveWindowSummary[] {
  return openWindows
    .map((id) => {
      const chart = chartWindows.find((item) => item.id === id);
      const layout = layouts[id];
      return {
        fullscreen: Boolean(layout?.fullscreen),
        id,
        minimized: Boolean(layout?.minimized),
        title: chart?.ticker ?? coreWindowTitle(id),
        type: chart ? "chart" as const : "core" as const,
        z: layout?.z ?? 0,
      };
    })
    .sort((a, b) => b.z - a.z);
}

function liveWorkspaceMinHeight(openWindows: WindowId[], layouts: Record<WindowId, WindowLayout>, compact: boolean) {
  const viewportHeight = typeof window === "undefined" ? 1024 : window.innerHeight;
  const baseHeight = Math.max(viewportHeight, compact ? 960 : 900);
  return openWindows.reduce((height, id) => {
    const layout = layouts[id];
    if (!layout || layout.fullscreen) return height;
    const windowHeight = layout.minimized ? 34 : layout.h;
    return Math.max(height, layout.y + windowHeight + 24);
  }, baseHeight);
}

function coreWindowTitle(id: WindowId) {
  if (id === "portfolio") return "Portfolio";
  if (id === "scanner") return "Scanner";
  return id;
}

function signedMetricTone(value: number) {
  if (value > 0) return "success";
  if (value < 0) return "danger";
  return "muted";
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

function previousSessionDate(sessions: string[], sessionDate: string, countBack: number) {
  const index = sessions.indexOf(sessionDate);
  if (index < 0) return dateOffset(sessionDate, -countBack);
  return sessions[Math.max(0, index - countBack)] ?? sessionDate;
}

function dateOffset(value: string, days: number) {
  const parsed = new Date(`${value}T00:00:00`);
  parsed.setDate(parsed.getDate() + days);
  return parsed.toISOString().slice(0, 10);
}

function addClockMinutes(clock: string, minutes: number) {
  const [hourText, minuteText] = clock.split(":");
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return "";
  const total = hour * 60 + minute + minutes;
  const nextHour = Math.floor(total / 60);
  const nextMinute = total % 60;
  if (nextHour < 0 || nextHour > 23) return "";
  return `${String(nextHour).padStart(2, "0")}:${String(nextMinute).padStart(2, "0")}`;
}

function isAfterClock(clock: string, cutoff: string) {
  return clockToMinutes(clock) > clockToMinutes(cutoff);
}

function clockToMinutes(clock: string) {
  const [hourText, minuteText] = clock.split(":");
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return 0;
  return hour * 60 + minute;
}

function rowTimestampSeconds(row: Record<string, unknown>, sessionDate: string, fallbackClock: string) {
  const raw = stringValue(row, "bar_time_market") || `${sessionDate}T${fallbackClock}:00-04:00`;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

function clockTimestampSeconds(sessionDate: string, clock: string) {
  if (!sessionDate || !clock) return null;
  const parsed = Date.parse(`${sessionDate}T${clock}:00-04:00`);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

function chartOpenAtTime(payload: ChartPayload | null, timestamp: number | null) {
  if (!payload || !timestamp) return 0;
  const candle = payload.candles.find((item) => item.time === timestamp);
  return candle?.open ?? 0;
}

function stringValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  return value === null || value === undefined ? "" : String(value);
}

function numberValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
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

function currentExchangeSession(now = new Date()): TradingSession {
  const parts = exchangeDateParts(now);
  return { barTime: `${parts.hour}:${parts.minute}`, sessionDate: `${parts.year}-${parts.month}-${parts.day}` };
}

function formatExchangeClock(now = new Date()) {
  const parts = exchangeDateParts(now);
  return `${parts.hour}:${parts.minute}:${parts.second} ET`;
}

function formatLocalClock(now = new Date()) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    second: "2-digit",
  }).format(now);
}

function exchangeDateParts(now: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "2-digit",
    second: "2-digit",
    timeZone: "America/New_York",
    year: "numeric",
  }).formatToParts(now);
  const value = (type: string) => parts.find((part) => part.type === type)?.value || "00";
  return {
    day: value("day"),
    hour: value("hour") === "24" ? "00" : value("hour"),
    minute: value("minute"),
    month: value("month"),
    second: value("second"),
    year: value("year"),
  };
}

function splitList(value: unknown) {
  return String(value || "").split("|").map((item) => item.trim()).filter(Boolean);
}

function money(value: number) {
  if (!Number.isFinite(value)) return "-";
  return `${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(Math.abs(value) >= 10 ? 2 : 4)}`;
}

function percent(value: number) {
  if (!Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(Math.abs(value) >= 0.1 ? 1 : 2)}%`;
}

function integer(value: number) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString() : "-";
}

function number(value: number, digits: number) {
  return Number.isFinite(value) ? value.toFixed(digits) : "-";
}

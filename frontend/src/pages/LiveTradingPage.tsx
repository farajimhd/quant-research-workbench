import { useEffect, useMemo, useRef, useState, type Dispatch, type PointerEvent, type ReactNode, type SetStateAction } from "react";
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
  FolderOpen,
  LayoutGrid,
  Maximize2,
  Minimize2,
  Move,
  PauseCircle,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings,
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
import { ChartPanel, type ChartCatalogItem, type ChartDisplayItem, type ChartPayload } from "../app/components/ChartPanel";
import { DataTable, type BackendTableQuery } from "../app/components/DataTable";
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

type ScannerSnapshotPayload = {
  snapshot: ScannerSnapshot;
};

type TradingSession = {
  barTime: string;
  sessionDate: string;
};

type ScannerSetupGroup = {
  enabled: boolean;
  id: string;
  minLast5mReturn: number;
  minPrice: number;
  maxPrice: number;
  minTransactions: number;
  minTransactionsRatio: number;
  minVolume: number;
  name: string;
  requireAboveVwap: boolean;
  requireBodyBreak: boolean;
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

type LiveClockMode = "idle" | "seeking" | "running" | "paused" | "complete";

type LiveWindowSummary = {
  fullscreen: boolean;
  id: WindowId;
  minimized: boolean;
  title: string;
  type: "core" | "chart";
  z: number;
};

type DecisionState = "approved" | "skipped" | "watching";

type OrderRow = {
  id: string;
  limit: number;
  quantity: number;
  side: "BUY" | "SELL";
  status: string;
  stop: number;
  symbol: string;
  timestamp: string;
  type: string;
};

type PositionRow = {
  avg_price: number;
  mark: number;
  quantity: number;
  stop: number;
  symbol: string;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
};

const LIVE_SESSION_STORAGE_KEY = "quant-research-workbench.live-trading.session";
const LIVE_LAYOUT_STORAGE_KEY = "quant-research-workbench.live-trading.layout";
const LIVE_LAYOUT_VERSION = 2;
const LIVE_LAYOUTS_STORAGE_KEY = "quant-research-workbench.live-trading.named-layouts";
const LIVE_SHARED_STATE_STORAGE_KEY = "quant-research-workbench.live-trading.shared-state";
const LIVE_SETUP_STORAGE_KEY = "quant-research-workbench.live-trading.scanner-setups";
const LIVE_FEATURE_GROUPS = ["core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure"];
const LIVE_PORTFOLIO_COLLAPSED_HEIGHT = 224;
const LIVE_PORTFOLIO_EXPANDED_HEIGHT = LIVE_PORTFOLIO_COLLAPSED_HEIGHT * 3;
const MAIN_DISPLAY_ITEMS = ["vwap", "tema9", "tema20", "macd"];
const LOWER_DISPLAY_ITEMS = ["vwap", "tema9", "tema20"];
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
  "last_5m_return",
  "last_volume",
  "last_transactions",
  "last_transactions_vs_prior_3",
  "last_bearish_volume_divergence_score",
  "last_double_timeframe_bearish_volume_divergence_score",
  "current_open_above_last_2_body_high",
  "spread_bps_abs",
];

const CORE_WINDOW_IDS: WindowId[] = ["portfolio", "scanner", "trade"];

const DEFAULT_SETUP_GROUPS: ScannerSetupGroup[] = [
  {
    enabled: true,
    id: "pop-liquidity",
    maxPrice: 10,
    minLast5mReturn: 0.05,
    minPrice: 1,
    minTransactions: 150,
    minTransactionsRatio: 3,
    minVolume: 8_000,
    name: "Pop Liquidity",
    requireAboveVwap: false,
    requireBodyBreak: false,
  },
  {
    enabled: true,
    id: "vwap-reclaim",
    maxPrice: 10,
    minLast5mReturn: 0.02,
    minPrice: 1,
    minTransactions: 100,
    minTransactionsRatio: 1.5,
    minVolume: 5_000,
    name: "VWAP Reclaim",
    requireAboveVwap: true,
    requireBodyBreak: true,
  },
  {
    enabled: false,
    id: "day-high-pressure",
    maxPrice: 10,
    minLast5mReturn: 0.03,
    minPrice: 1,
    minTransactions: 120,
    minTransactionsRatio: 2,
    minVolume: 8_000,
    name: "Day High Pressure",
    requireAboveVwap: true,
    requireBodyBreak: false,
  },
];

function buildDefaultCanvasLayout(childCanvas: boolean): { chartWindows: ChartWindow[]; layouts: Record<WindowId, WindowLayout>; windows: WindowId[] } {
  const width = Math.max(1180, window.innerWidth - 112);
  const height = Math.max(780, window.innerHeight - 86);
  const gap = 10;
  const margin = 12;
  const portfolioH = LIVE_PORTFOLIO_COLLAPSED_HEIGHT;
  const mainY = margin + portfolioH + gap;
  const availableH = Math.max(420, height - mainY - margin);
  const leftW = Math.max(250, Math.round(width * 0.2));
  const scannerH = Math.round(availableH * 0.65) - Math.round(gap / 2);
  const tradeH = availableH - scannerH - gap;
  const chartX = margin + leftW + gap;
  const chartW = Math.round(width * 0.4);
  const layouts: Record<WindowId, WindowLayout> = {
    portfolio: { fullscreen: false, h: portfolioH, minimized: false, w: width - margin * 2, x: margin, y: margin, z: 3 },
    scanner: { fullscreen: false, h: scannerH, minimized: false, w: leftW, x: margin, y: mainY, z: 1 },
    trade: { fullscreen: false, h: tradeH, minimized: false, w: leftW, x: margin, y: mainY + scannerH + gap, z: 2 },
    chart: { fullscreen: false, h: availableH, minimized: false, w: chartW, x: chartX, y: mainY, z: 4 },
  };
  return { chartWindows: [], layouts, windows: childCanvas ? [] : [...CORE_WINDOW_IDS] };
}

export function LiveTradingPage({ onTopbarCenterChange }: { onTopbarCenterChange?: Dispatch<SetStateAction<ReactNode>> }) {
  const canvasId = useMemo(() => new URLSearchParams(window.location.search).get("liveCanvas") || "main", []);
  const isChildCanvas = canvasId !== "main";
  const initialCanvas = useMemo(() => readStoredCanvas(canvasId, isChildCanvas), [canvasId, isChildCanvas]);
  const initialSharedState = useMemo(readSharedTradingState, []);
  const [scope, setScope] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [session, setSession] = useState<TradingSession>(() => readStoredSession() ?? { barTime: "04:00", sessionDate: "" });
  const [started, setStarted] = useState(isChildCanvas);
  const [setupGroups, setSetupGroups] = useState<ScannerSetupGroup[]>(readStoredSetupGroups);
  const [newSetupName, setNewSetupName] = useState("");
  const [snapshot, setSnapshot] = useState<ScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [liveClockMode, setLiveClockMode] = useState<LiveClockMode>("idle");
  const [liveClockMessage, setLiveClockMessage] = useState("");
  const [secondsPerMinute, setSecondsPerMinute] = useState("10");
  const [lastActionTime, setLastActionTime] = useState("");
  const [selectedRow, setSelectedRow] = useState<Record<string, unknown> | null>(null);
  const [mainTimeframe, setMainTimeframe] = useState("1m");
  const [mainVisibleColumns, setMainVisibleColumns] = useState<string[]>(MAIN_DISPLAY_ITEMS);
  const [compactVisibleColumns, setCompactVisibleColumns] = useState<string[]>(LOWER_DISPLAY_ITEMS);
  const [headerCollapsed, setHeaderCollapsed] = useState(true);
  const [showDayChart, setShowDayChart] = useState(true);
  const [showFiveMinuteChart, setShowFiveMinuteChart] = useState(true);
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>(initialSharedState.decisions);
  const [orders, setOrders] = useState<OrderRow[]>(initialSharedState.orders);
  const [positions, setPositions] = useState<PositionRow[]>(initialSharedState.positions);
  const [portfolioTab, setPortfolioTab] = useState("Open Positions");
  const [portfolioDetailsOpen, setPortfolioDetailsOpen] = useState(false);
  const [tradeDraft, setTradeDraft] = useState({ limit: "", quantity: "3000", side: "BUY" as "BUY" | "SELL", stop: "", type: "LIMIT" });
  const [layouts, setLayouts] = useState<Record<WindowId, WindowLayout>>(initialCanvas.layouts);
  const [openWindows, setOpenWindows] = useState<WindowId[]>(initialCanvas.windows);
  const [chartWindows, setChartWindows] = useState<ChartWindow[]>(initialCanvas.chartWindows);
  const [layoutName, setLayoutName] = useState("Momentum Desk");
  const [savedLayouts, setSavedLayouts] = useState<SavedCanvasLayout[]>(readSavedCanvasLayouts);
  const [selectedLayoutName, setSelectedLayoutName] = useState("");
  const seekCancelRef = useRef(0);

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
    window.localStorage.setItem(LIVE_SETUP_STORAGE_KEY, JSON.stringify(setupGroups));
  }, [setupGroups]);

  const sessions = useMemo(() => availableSessionDates(review?.records ?? []), [review]);
  const activeSetups = setupGroups.filter((item) => item.enabled);
  const selectedTicker = stringValue(selectedRow, "ticker");
  const selectedOpen = numberValue(selectedRow, "current_open") || numberValue(selectedRow, "open");
  const selectedProfile = selectedRow ? enrichLiveCandidate(selectedRow, activeSetups) : null;
  const scannerRows = useMemo(
    () =>
      (snapshot?.rows ?? [])
        .map((row) => enrichLiveCandidate(row, activeSetups))
        .filter((row) => stringValue(row, "live_setup_group"))
        .sort((a, b) => numberValue(b, "live_priority") - numberValue(a, "live_priority")),
    [activeSetups, snapshot]
  );
  const portfolioMetrics = useMemo(
    () => buildPortfolioMetrics({ orders, positions }),
    [orders, positions]
  );
  const globalMetrics = useMemo(
    () => buildGlobalLiveMetrics({ decisions, lastActionTime, liveClockMode, scannerRows, secondsPerMinute, session, snapshot }),
    [decisions, lastActionTime, liveClockMode, scannerRows, secondsPerMinute, session, snapshot]
  );
  const liveWindowSummaries = useMemo(
    () => buildLiveWindowSummaries(openWindows, chartWindows, layouts),
    [chartWindows, layouts, openWindows]
  );
  const topbarWorkspaceInfo = useMemo(() => {
    const knownPageCount = countKnownLiveCanvases();
    const canvasLabel = isChildCanvas ? `Child canvas ${canvasId.replace(/^canvas-/, "")}` : "Main canvas";
    const layoutLabel = selectedLayoutName || layoutName || "Unsaved layout";
    const pageLabel = `${knownPageCount} page${knownPageCount === 1 ? "" : "s"}`;
    const windowNames = liveWindowSummaries.map((windowItem) => windowItem.title);
    const windowLabel = windowNames.length ? windowNames.slice(0, 4).join(", ") : "No windows";
    const extraWindowCount = Math.max(0, windowNames.length - 4);
    return {
      detail: `${layoutLabel} - ${pageLabel} - ${windowLabel}${extraWindowCount ? ` +${extraWindowCount}` : ""}`,
      title: `Semi-Auto Trading - ${canvasLabel}`,
    };
  }, [canvasId, isChildCanvas, layoutName, liveWindowSummaries, selectedLayoutName]);

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
    const payload = { decisions, orders, positions };
    window.localStorage.setItem(LIVE_SHARED_STATE_STORAGE_KEY, JSON.stringify(payload));
  }, [decisions, orders, positions]);

  useEffect(() => {
    const payload = { chartWindows, layoutVersion: LIVE_LAYOUT_VERSION, layouts, windows: openWindows };
    window.localStorage.setItem(canvasStorageKey(canvasId), JSON.stringify(payload));
  }, [canvasId, chartWindows, layouts, openWindows]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === LIVE_SHARED_STATE_STORAGE_KEY && event.newValue) {
        try {
          const parsed = JSON.parse(event.newValue) as { decisions?: Record<string, DecisionState>; orders?: OrderRow[]; positions?: PositionRow[] };
          setDecisions(parsed.decisions ?? {});
          setOrders(parsed.orders ?? []);
          setPositions(parsed.positions ?? []);
        } catch {
          // Ignore malformed cross-tab state.
        }
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    if (!started || !scope || !session.sessionDate || isChildCanvas) return;
    let canceled = false;
    const start = session.barTime;
    const runId = seekCancelRef.current + 1;
    seekCancelRef.current = runId;
    setLiveClockMode("seeking");
    setLiveClockMessage("Fast-forwarding to the next scanner signal.");
    runUntilNextAction(start, () => canceled || seekCancelRef.current !== runId)
      .then((found) => {
        if (canceled || seekCancelRef.current !== runId) return;
        if (found) {
          setLiveClockMode("running");
          setLiveClockMessage("Scanner signal found. Live clock is pacing from this timestamp.");
        } else {
          setLiveClockMode("complete");
          setLiveClockMessage("No scanner signal found before the session cutoff.");
        }
      })
      .catch((requestError: Error) => {
        if (canceled || seekCancelRef.current !== runId) return;
        setLiveClockMode("paused");
        setLiveClockMessage(requestError.message || "Scanner fast-forward failed.");
      });
    return () => {
      canceled = true;
    };
  }, [isChildCanvas, scope, started, session.sessionDate]);

  useEffect(() => {
    if (!started || !scope || !session.sessionDate || liveClockMode !== "running") return;
    const seconds = Math.max(1, Number(secondsPerMinute) || 10);
    const timer = window.setTimeout(() => {
      const nextTime = addClockMinutes(session.barTime, 1);
      if (!nextTime || isAfterClock(nextTime, "20:00")) {
        setLiveClockMode("complete");
        setLiveClockMessage("Session clock reached the end of supported trading time.");
        return;
      }
      setSession((current) => ({ ...current, barTime: nextTime }));
      loadScannerAt(nextTime, { revealChart: false });
    }, seconds * 1000);
    return () => window.clearTimeout(timer);
  }, [liveClockMode, scope, secondsPerMinute, session.barTime, session.sessionDate, started]);

  function startTrading() {
    const nextSession = { ...session, barTime: "04:00", sessionDate: session.sessionDate || sessions.at(-1) || "" };
    if (!nextSession.sessionDate) return;
    window.localStorage.setItem(LIVE_SESSION_STORAGE_KEY, JSON.stringify(nextSession));
    setSession(nextSession);
    setStarted(true);
  }

  function refreshCurrentBar() {
    loadScannerAt(session.barTime, { revealChart: true });
  }

  function advanceOneBar() {
    const nextTime = addClockMinutes(session.barTime, 1);
    if (!nextTime || isAfterClock(nextTime, "20:00")) {
      setLiveClockMode("complete");
      setLiveClockMessage("Session clock reached the end of supported trading time.");
      return;
    }
    setLiveClockMode("paused");
    setSession((current) => ({ ...current, barTime: nextTime }));
    loadScannerAt(nextTime, { revealChart: true });
  }

  async function seekNextSignal() {
    const runId = seekCancelRef.current + 1;
    seekCancelRef.current = runId;
    setLiveClockMode("seeking");
    setLiveClockMessage("Fast-forwarding to the next scanner signal.");
    try {
      const found = await runUntilNextAction(session.barTime, () => seekCancelRef.current !== runId);
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
    setLiveClockMode((mode) => {
      if (mode === "running" || mode === "seeking") {
        seekCancelRef.current += 1;
        setLiveClockMessage("Live clock paused.");
        return "paused";
      }
      setLiveClockMessage("Live clock resumed.");
      return "running";
    });
  }

  async function loadScannerAt(barTime: string, options: { revealChart: boolean }) {
    if (!scope || !session.sessionDate) return null;
    setLoading(true);
    setError("");
    try {
      const payload = await api<ScannerSnapshotPayload>(
        `/api/market-data/scanner-snapshot${query({
        processed_root: scope.processed_root,
        session_date: session.sessionDate,
        timeframe: "1m",
        bar_time: barTime,
        feature_groups: LIVE_FEATURE_GROUPS.join(","),
        columns: LIVE_SCANNER_COLUMNS.join(","),
        table_query: JSON.stringify(baseScannerQuery(activeSetups)),
        row_limit: 1000,
        })}`
      );
      const enrichedRows = payload.snapshot.rows.map((row) => enrichLiveCandidate(row, activeSetups));
      const firstRow = enrichedRows.find((row) => stringValue(row, "live_setup_group")) ?? null;
      setSnapshot(payload.snapshot);
      setSelectedRow(firstRow);
      if (firstRow) setLastActionTime(barTime);
      if (firstRow && options.revealChart) openChartForRow(firstRow);
      return { firstRow, snapshot: payload.snapshot };
    } catch (requestError) {
      setSnapshot(null);
      setSelectedRow(null);
      setError(requestError instanceof Error ? requestError.message : "Scanner request failed.");
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function runUntilNextAction(startTime: string, shouldStop: () => boolean) {
    let nextTime = startTime;
    for (let index = 0; index < 960; index += 1) {
      if (shouldStop() || isAfterClock(nextTime, "20:00")) return false;
      setSession((current) => ({ ...current, barTime: nextTime }));
      const result = await loadScannerAt(nextTime, { revealChart: true });
      if (result?.firstRow) {
        setLastActionTime(nextTime);
        return true;
      }
      const advanced = addClockMinutes(nextTime, 1);
      if (!advanced) return false;
      nextTime = advanced;
    }
    return false;
  }

  function markDecision(state: DecisionState) {
    if (!selectedTicker) return;
    setDecisions((current) => ({ ...current, [selectedTicker]: state }));
    if (state === "approved") stageOrder("BUY", "STAGED");
  }

  function stageOrder(side = tradeDraft.side, status = "STAGED") {
    if (!selectedTicker) return;
    const quantity = Math.max(0, Math.floor(Number(tradeDraft.quantity) || 0));
    const limit = Number(tradeDraft.limit) || numberValue(selectedProfile, "suggested_entry") || selectedOpen;
    const stop = Number(tradeDraft.stop) || numberValue(selectedProfile, "suggested_stop");
    const order: OrderRow = {
      id: `${Date.now()}-${selectedTicker}-${side}`,
      limit,
      quantity,
      side,
      status,
      stop,
      symbol: selectedTicker,
      timestamp: `${session.sessionDate} ${session.barTime}`,
      type: tradeDraft.type,
    };
    setOrders((current) => [order, ...current]);
    if (side === "BUY" && status !== "CANCELED") {
      setPositions((current) => upsertPosition(current, selectedTicker, quantity, limit, stop, selectedOpen || limit));
    }
  }

  function addSetupGroup() {
    const name = newSetupName.trim();
    if (!name) return;
    const source = setupGroups[0] ?? DEFAULT_SETUP_GROUPS[0];
    setSetupGroups((current) => [
      ...current,
      { ...source, enabled: true, id: `${Date.now()}-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`, name },
    ]);
    setNewSetupName("");
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
    setSelectedRow(row);
    const ticker = stringValue(row, "ticker");
    if (!ticker) return;
    const id = `chart-${ticker}`;
    setChartWindows((current) => [{ id, row, ticker }, ...current.filter((chart) => chart.id !== id)]);
    setOpenWindows((current) => [id, ...current.filter((windowId) => windowId !== id)]);
    setLayouts((current) => {
      const chartDefaults = current.chart ?? buildDefaultCanvasLayout(false).layouts.chart;
      const existingChartIds = chartWindows.filter((chart) => chart.id !== id).map((chart) => chart.id);
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

  function createChildCanvas(windowId?: WindowId) {
    const nextCanvasId = `canvas-${Date.now()}`;
    if (windowId) {
      const transfer = { chartWindows, layout: layouts[windowId], windowId };
      window.localStorage.setItem(canvasTransferKey(nextCanvasId), JSON.stringify(transfer));
      closeWindow(windowId);
    }
    const url = new URL(window.location.href);
    url.searchParams.set("liveCanvas", nextCanvasId);
    url.hash = "live-trading";
    window.open(url.toString(), "_blank", "noopener,noreferrer");
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
            ? { ...current.portfolio, fullscreen: false, h: LIVE_PORTFOLIO_EXPANDED_HEIGHT, minimized: false, w: Math.max(1180, window.innerWidth - 112) - 24, x: 12, y: 12, z: topZ + 1 }
            : { ...defaults.portfolio, z: topZ + 1 },
        };
      });
      return nextOpen;
    });
  }

  if (!started) {
    return (
      <LiveTradingStart
        scope={scope}
        session={session}
        sessions={sessions}
        onSessionChange={setSession}
        onStart={startTrading}
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
            <LiveWindowManager
              windows={liveWindowSummaries}
              onClose={closeWindow}
              onFocus={(id) => {
                updateLayout(id, { minimized: false });
                bringWindowForward(id);
              }}
              onMinimize={(id, minimized) => updateLayout(id, { minimized })}
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
          <button className="button secondary compact" disabled={loading} onClick={refreshCurrentBar} type="button">
            <RefreshCw size={14} /> Refresh
          </button>
          <button className="button secondary compact" disabled={loading} onClick={advanceOneBar} type="button">
            <StepForward size={14} /> Next Bar
          </button>
          <button className="button primary compact" disabled={loading || liveClockMode === "seeking"} onClick={() => void seekNextSignal()} type="button">
            {loading || liveClockMode === "seeking" ? <span className="loading-spinner" aria-hidden="true" /> : <SkipForward size={14} />} Next Signal
          </button>
          <button className="button secondary compact" disabled={loading && liveClockMode !== "seeking"} onClick={toggleLiveClock} type="button">
            {liveClockMode === "running" || liveClockMode === "seeking" ? <PauseCircle size={14} /> : <Play size={14} />} {liveClockMode === "running" || liveClockMode === "seeking" ? "Pause" : "Resume"}
          </button>
          <button className="button secondary compact" onClick={closeSession} type="button">
            <Settings size={14} /> Close
          </button>
        </div>
      </section>
      <section className={headerCollapsed ? "live-workspace compact" : "live-workspace"} aria-label="Semi-auto trading workspace">
        {!openWindows.length ? <div className="live-empty-canvas">This canvas is empty. Open scanner rows here or pop containers into this canvas from another tab.</div> : null}
        {openWindows.map((windowId) => {
          const layout = layouts[windowId] ?? layouts.chart ?? buildDefaultCanvasLayout(false).layouts.chart;
          if (windowId === "scanner") {
            return (
              <WorkspaceWindow key={windowId} id={windowId} layout={layout} title="Scanner" icon={<TrendingUp size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onPopOut={createChildCanvas}>
                <ScannerContainer
                  activeSetups={activeSetups}
                  loading={loading}
                  newSetupName={newSetupName}
                  rows={scannerRows}
                  selectedTicker={selectedTicker}
                  setupGroups={setupGroups}
                  snapshot={snapshot}
                  onAddSetup={addSetupGroup}
                  onLoad={refreshCurrentBar}
                  onNewSetupNameChange={setNewSetupName}
                  onRowSelect={openChartForRow}
                  onSetupGroupsChange={setSetupGroups}
                />
              </WorkspaceWindow>
            );
          }
          if (windowId === "portfolio") {
            return (
              <WorkspaceWindow key={windowId} id={windowId} layout={layout} title="Portfolio" icon={<WalletCards size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onPopOut={createChildCanvas}>
                <PortfolioContainer
                  detailsOpen={portfolioDetailsOpen}
                  metrics={portfolioMetrics}
                  orders={orders}
                  positions={positions}
                  selectedTab={portfolioTab}
                  onTabChange={setPortfolioTab}
                  onToggleDetails={togglePortfolioDetails}
                />
              </WorkspaceWindow>
            );
          }
          if (windowId === "trade") {
            return (
              <WorkspaceWindow key={windowId} id={windowId} layout={layout} title="Trade" icon={<Target size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onPopOut={createChildCanvas}>
                <TradeContainer
                  decisions={decisions}
                  draft={tradeDraft}
                  selectedOpen={selectedOpen}
                  selectedProfile={selectedProfile}
                  selectedTicker={selectedTicker}
                  onDecision={markDecision}
                  onDraftChange={setTradeDraft}
                  onStage={stageOrder}
                />
              </WorkspaceWindow>
            );
          }
          const chart = chartWindows.find((item) => item.id === windowId);
          if (!chart || !scope) return null;
          return (
            <WorkspaceWindow key={windowId} id={windowId} layout={layout} title={chart.ticker} icon={<BarChart3 size={15} />} onClose={closeWindow} onFocus={bringWindowForward} onLayoutChange={updateLayout} onPopOut={createChildCanvas}>
              <LiveChartWindow
                catalog={catalog}
                chart={chart}
                compactVisibleColumns={compactVisibleColumns}
                mainTimeframe={mainTimeframe}
                mainVisibleColumns={mainVisibleColumns}
                scope={scope}
                session={session}
                sessions={sessions}
                showDayChart={showDayChart}
                showFiveMinuteChart={showFiveMinuteChart}
                onCompactVisibleColumnsChange={setCompactVisibleColumns}
                onMainTimeframeChange={setMainTimeframe}
                onMainVisibleColumnsChange={setMainVisibleColumns}
                onToggleDayChart={() => setShowDayChart((value) => !value)}
                onToggleFiveMinuteChart={() => setShowFiveMinuteChart((value) => !value)}
              />
            </WorkspaceWindow>
          );
        })}
      </section>
    </>
  );
}

function LiveTradingStart({
  onSessionChange,
  onStart,
  scope,
  session,
  sessions,
}: {
  onSessionChange: (session: TradingSession) => void;
  onStart: () => void;
  scope: Scope | null;
  session: TradingSession;
  sessions: string[];
}) {
  return (
    <>
      <PageIntro
        groupLabel="Live Trading"
        title="Start Semi-Auto Session"
        description="Choose the trading date. The workspace loads that session and starts fast-forwarding to the first scanner signal."
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
          <button className="button primary" disabled={!session.sessionDate} onClick={onStart} type="button">
            <Play size={15} /> Start Trading
          </button>
        </div>
      </section>
    </>
  );
}

function WorkspaceWindow({
  children,
  icon,
  id,
  layout,
  onClose,
  onFocus,
  onLayoutChange,
  onPopOut,
  title,
}: {
  children: ReactNode;
  icon: ReactNode;
  id: WindowId;
  layout: WindowLayout;
  onClose: (id: WindowId) => void;
  onFocus: (id: WindowId) => void;
  onLayoutChange: (id: WindowId, patch: Partial<WindowLayout>) => void;
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
    <section className="live-window" style={style} onPointerDown={() => onFocus(id)}>
      <div className="live-window-header" onPointerDown={startDrag}>
        <div className="live-window-title">
          <Move size={13} />
          {icon}
          <strong>{title}</strong>
        </div>
        <div className="live-window-actions" onPointerDown={(event) => event.stopPropagation()}>
          <button className="toolbar-button compact" onClick={() => onPopOut(id)} title="Move to child canvas" type="button">
            <ExternalLink size={12} />
          </button>
          <button className="toolbar-button compact" onClick={() => onLayoutChange(id, { minimized: !layout.minimized })} title={layout.minimized ? "Restore" : "Minimize"} type="button">
            <Minimize2 size={12} />
          </button>
          <button className="toolbar-button compact" onClick={() => onLayoutChange(id, { fullscreen: !layout.fullscreen, minimized: false })} title={layout.fullscreen ? "Exit fullscreen" : "Fullscreen"} type="button">
            <Maximize2 size={12} />
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
  onClose,
  onFocus,
  onMinimize,
  onPopOut,
  onShowCoreWindows,
  windows,
}: {
  onClose: (id: WindowId) => void;
  onFocus: (id: WindowId) => void;
  onMinimize: (id: WindowId, minimized: boolean) => void;
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
                <button className="toolbar-button compact" onClick={() => onFocus(windowItem.id)} title="Show window" type="button">
                  <Eye size={13} />
                </button>
                <button className="toolbar-button compact" onClick={() => onMinimize(windowItem.id, !windowItem.minimized)} title={windowItem.minimized ? "Restore window" : "Minimize window"} type="button">
                  {windowItem.minimized ? <Maximize2 size={13} /> : <Minimize2 size={13} />}
                </button>
                <button className="toolbar-button compact" onClick={() => onPopOut(windowItem.id)} title="Move to child canvas" type="button">
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

function ScannerContainer({
  activeSetups,
  loading,
  newSetupName,
  onAddSetup,
  onLoad,
  onNewSetupNameChange,
  onRowSelect,
  onSetupGroupsChange,
  rows,
  selectedTicker,
  setupGroups,
  snapshot,
}: {
  activeSetups: ScannerSetupGroup[];
  loading: boolean;
  newSetupName: string;
  rows: Record<string, unknown>[];
  selectedTicker: string;
  setupGroups: ScannerSetupGroup[];
  snapshot: ScannerSnapshot | null;
  onAddSetup: () => void;
  onLoad: () => void;
  onNewSetupNameChange: (value: string) => void;
  onRowSelect: (row: Record<string, unknown>) => void;
  onSetupGroupsChange: (groups: ScannerSetupGroup[]) => void;
}) {
  return (
    <div className="live-container-stack">
      <div className="live-scanner-toolbar">
        <div className="live-filter-group-list">
          {setupGroups.map((group) => (
            <button
              className={group.enabled ? "live-filter-chip active" : "live-filter-chip"}
              key={group.id}
              onClick={() => onSetupGroupsChange(setupGroups.map((item) => (item.id === group.id ? { ...item, enabled: !item.enabled } : item)))}
              type="button"
            >
              {group.name}
            </button>
          ))}
        </div>
        <div className="live-new-setup">
          <input placeholder="New setup group" value={newSetupName} onChange={(event) => onNewSetupNameChange(event.target.value)} />
          <button className="button secondary" onClick={onAddSetup} type="button">
            <Plus size={14} /> Add
          </button>
          <button className="button primary" disabled={loading || !activeSetups.length} onClick={onLoad} type="button">
            {loading ? <span className="loading-spinner" aria-hidden="true" /> : <Play size={14} />} Refresh
          </button>
        </div>
      </div>
      <DataTable
        columns={liveTableColumns(snapshot?.columns ?? [])}
        empty={loading ? "Loading scanner..." : "Run scanner to load candidates."}
        isRowSelected={(row) => stringValue(row, "ticker") === selectedTicker}
        onRowClick={onRowSelect}
        preserveFiltersOnDataChange
        rows={rows}
        transposeHelper
      />
    </div>
  );
}

function PortfolioContainer({
  detailsOpen,
  metrics,
  onToggleDetails,
  onTabChange,
  orders,
  positions,
  selectedTab,
}: {
  detailsOpen: boolean;
  metrics: ReturnType<typeof buildPortfolioMetrics>;
  onToggleDetails: () => void;
  onTabChange: (tab: string) => void;
  orders: OrderRow[];
  positions: PositionRow[];
  selectedTab: string;
}) {
  const tabs = ["Open Positions", "P/L", "Trades", "Orders"];
  return (
    <div className={detailsOpen ? "live-container-stack portfolio-expanded" : "live-container-stack"}>
      <div className="live-portfolio-header">
        <div className="live-debug-metric-strip" style={{ gridTemplateColumns: `repeat(${Math.max(metrics.items.length, 1)}, minmax(106px, 1fr))` }}>
          {metrics.items.map((item) => (
            <article className="live-debug-metric-card" data-tone={item.tone} key={item.label}>
              <span className="live-debug-metric-icon">{item.icon}</span>
              <span className="live-debug-metric-label">{item.label}</span>
              <strong>{item.value}</strong>
            </article>
          ))}
        </div>
      </div>
      <div className="live-position-cards">
        {positions.length ? positions.map((position) => <PositionCard key={position.symbol} position={position} />) : <div className="live-empty-positions">No open positions.</div>}
      </div>
      <button className="live-portfolio-expand-button" onClick={onToggleDetails} title={detailsOpen ? "Hide tabs" : "Show tabs"} type="button">
        {detailsOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {detailsOpen ? (
        <>
          <Tabs tabs={tabs} active={selectedTab} onChange={onTabChange} />
          {selectedTab === "Open Positions" ? <DataTable rows={positions} empty="No open positions." /> : null}
          {selectedTab === "P/L" ? <DataTable rows={positions.map((row) => ({ symbol: row.symbol, unrealized_pnl: row.unrealized_pnl, unrealized_pnl_pct: row.unrealized_pnl_pct, mark: row.mark, avg_price: row.avg_price }))} empty="No P/L rows." /> : null}
          {selectedTab === "Trades" ? <DataTable rows={[]} empty="No completed trades yet." /> : null}
          {selectedTab === "Orders" ? <DataTable rows={orders} empty="No staged orders." /> : null}
        </>
      ) : null}
    </div>
  );
}

function PositionCard({ position }: { position: PositionRow }) {
  const pnlTone = position.unrealized_pnl >= 0 ? "positive" : "negative";
  return (
    <article className={`live-position-card ${pnlTone}`}>
      <div>
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
        <span>Stop</span>
        <strong>{money(position.stop)}</strong>
      </div>
      <div>
        <span>P/L</span>
        <strong>{money(position.unrealized_pnl)} / {percent(position.unrealized_pnl_pct)}</strong>
      </div>
    </article>
  );
}

function TradeContainer({
  decisions,
  draft,
  onDecision,
  onDraftChange,
  onStage,
  selectedOpen,
  selectedProfile,
  selectedTicker,
}: {
  decisions: Record<string, DecisionState>;
  draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string };
  onDecision: (state: DecisionState) => void;
  onDraftChange: (draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string }) => void;
  onStage: (side?: "BUY" | "SELL", status?: string) => void;
  selectedOpen: number;
  selectedProfile: Record<string, unknown> | null;
  selectedTicker: string;
}) {
  const currentDecision = selectedTicker ? decisions[selectedTicker] : undefined;
  return (
    <div className="live-container-stack">
      <div className="live-trade-symbol">
        <span>Symbol</span>
        <strong>{selectedTicker || "-"}</strong>
        <small>{currentDecision ? `Marked ${currentDecision}` : "No decision"}</small>
      </div>
      <div className="live-ticket-grid">
        <TicketMetric label="Open" value={selectedOpen ? money(selectedOpen) : "-"} />
        <TicketMetric label="Entry" value={money(numberValue(selectedProfile, "suggested_entry"))} />
        <TicketMetric label="Stop" value={money(numberValue(selectedProfile, "suggested_stop"))} tone="risk" />
        <TicketMetric label="Setup" value={stringValue(selectedProfile, "live_setup_group") || "-"} />
      </div>
      <div className="live-trade-form">
        <LiveSelect label="Side" value={draft.side} values={["BUY", "SELL"]} onChange={(value) => onDraftChange({ ...draft, side: value as "BUY" | "SELL" })} />
        <LiveSelect label="Type" value={draft.type} values={["LIMIT", "MARKET", "STOP"]} onChange={(value) => onDraftChange({ ...draft, type: value })} />
        <LiveField label="Quantity" type="number" value={draft.quantity} onChange={(value) => onDraftChange({ ...draft, quantity: value })} />
        <LiveField label="Limit" type="number" value={draft.limit} onChange={(value) => onDraftChange({ ...draft, limit: value })} />
        <LiveField label="Stop" type="number" value={draft.stop} onChange={(value) => onDraftChange({ ...draft, stop: value })} />
      </div>
      <div className="live-decision-actions">
        <button className="button primary" disabled={!selectedTicker} onClick={() => onDecision("approved")} type="button">
          <Target size={15} /> Approve
        </button>
        <button className="button secondary" disabled={!selectedTicker} onClick={() => onDecision("watching")} type="button">
          <Eye size={15} /> Watch
        </button>
        <button className="button secondary" disabled={!selectedTicker} onClick={() => onDecision("skipped")} type="button">
          <PauseCircle size={15} /> Skip
        </button>
        <button className="button secondary" disabled={!selectedTicker} onClick={() => onStage()} type="button">
          <Save size={15} /> Stage
        </button>
      </div>
      <div className="live-reason-columns">
        <ReasonList icon={<TrendingUp size={15} />} items={splitList(selectedProfile?.live_reasons)} title="Reasons" />
        <ReasonList icon={<ShieldAlert size={15} />} items={splitList(selectedProfile?.live_risks)} title="Risks" />
      </div>
    </div>
  );
}

function LiveChartWindow({
  catalog,
  chart,
  compactVisibleColumns,
  mainTimeframe,
  mainVisibleColumns,
  onCompactVisibleColumnsChange,
  onMainTimeframeChange,
  onMainVisibleColumnsChange,
  onToggleDayChart,
  onToggleFiveMinuteChart,
  scope,
  session,
  sessions,
  showDayChart,
  showFiveMinuteChart,
}: {
  catalog: CatalogPayload | null;
  chart: ChartWindow;
  compactVisibleColumns: string[];
  mainTimeframe: string;
  mainVisibleColumns: string[];
  scope: Scope;
  session: TradingSession;
  sessions: string[];
  showDayChart: boolean;
  showFiveMinuteChart: boolean;
  onCompactVisibleColumnsChange: (columns: string[]) => void;
  onMainTimeframeChange: (timeframe: string) => void;
  onMainVisibleColumnsChange: (columns: string[]) => void;
  onToggleDayChart: () => void;
  onToggleFiveMinuteChart: () => void;
}) {
  const [mainPayload, setMainPayload] = useState<ChartPayload | null>(null);
  const [dayPayload, setDayPayload] = useState<ChartPayload | null>(null);
  const [fiveMinutePayload, setFiveMinutePayload] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState("");
  const selectedOpen = numberValue(chart.row, "current_open") || numberValue(chart.row, "open");
  const selectedTime = rowTimestampSeconds(chart.row, session.sessionDate, session.barTime);
  const mainOpenOnlyPayload = useMemo(() => {
    if (mainTimeframe === "1d") return dayOpenOnlyChartPayload(mainPayload, session.sessionDate, selectedOpen, selectedTime);
    if (mainTimeframe === "5m") return castOpenChartPayload(mainPayload, selectedTime, selectedOpen, `${session.barTime} open`);
    return openOnlyChartPayload(mainPayload, selectedTime, selectedOpen, session.barTime);
  }, [mainPayload, mainTimeframe, selectedOpen, selectedTime, session.barTime, session.sessionDate]);
  const dayOpenOnlyPayload = useMemo(
    () => dayOpenOnlyChartPayload(dayPayload, session.sessionDate, selectedOpen, selectedTime),
    [dayPayload, selectedOpen, selectedTime, session.sessionDate]
  );
  const fiveMinuteOpenOnlyPayload = useMemo(
    () => castOpenChartPayload(fiveMinutePayload, selectedTime, selectedOpen, `${session.barTime} open`),
    [fiveMinutePayload, selectedOpen, selectedTime, session.barTime]
  );

  useEffect(() => {
    let active = true;
    setChartLoading(true);
    setChartError("");
    const dayStart = dateOffset(session.sessionDate, -90);
    const fiveMinuteStart = previousSessionDate(sessions, session.sessionDate, 2);
    Promise.allSettled([
      loadChart(scope.processed_root, session.sessionDate, session.sessionDate, mainTimeframe, chart.ticker, mainVisibleColumns),
      loadChart(scope.processed_root, dayStart, session.sessionDate, "1d", chart.ticker, compactVisibleColumns),
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
      selectedTicker={chart.ticker}
      session={session}
      showDayChart={showDayChart}
      showFiveMinuteChart={showFiveMinuteChart}
      onCompactVisibleColumnsChange={onCompactVisibleColumnsChange}
      onMainTimeframeChange={onMainTimeframeChange}
      onMainVisibleColumnsChange={onMainVisibleColumnsChange}
      onToggleDayChart={onToggleDayChart}
      onToggleFiveMinuteChart={onToggleFiveMinuteChart}
    />
  );
}

function ChartsContainer({
  catalog,
  chartError,
  chartLoading,
  compactVisibleColumns,
  dayPayload,
  fiveMinutePayload,
  mainPayload,
  mainTimeframe,
  mainVisibleColumns,
  onCompactVisibleColumnsChange,
  onMainTimeframeChange,
  onMainVisibleColumnsChange,
  onToggleDayChart,
  onToggleFiveMinuteChart,
  selectedTicker,
  session,
  showDayChart,
  showFiveMinuteChart,
}: {
  catalog: CatalogPayload | null;
  chartError: string;
  chartLoading: boolean;
  compactVisibleColumns: string[];
  dayPayload: ChartPayload | null;
  fiveMinutePayload: ChartPayload | null;
  mainPayload: ChartPayload | null;
  mainTimeframe: string;
  mainVisibleColumns: string[];
  selectedTicker: string;
  session: TradingSession;
  showDayChart: boolean;
  showFiveMinuteChart: boolean;
  onCompactVisibleColumnsChange: (columns: string[]) => void;
  onMainTimeframeChange: (timeframe: string) => void;
  onMainVisibleColumnsChange: (columns: string[]) => void;
  onToggleDayChart: () => void;
  onToggleFiveMinuteChart: () => void;
}) {
  const mainOptions = mainPayload?.options;
  const compactOptions = fiveMinutePayload?.options ?? dayPayload?.options;
  const lowerChartCount = Number(showDayChart) + Number(showFiveMinuteChart);
  return (
    <div className={lowerChartCount ? "live-chart-stack" : "live-chart-stack no-lower"}>
      <div className="live-chart-toggle-row">
        <span>Lower charts</span>
        <button className={showDayChart ? "live-filter-chip active" : "live-filter-chip"} onClick={onToggleDayChart} type="button">
          Daily
        </button>
        <button className={showFiveMinuteChart ? "live-filter-chip active" : "live-filter-chip"} onClick={onToggleFiveMinuteChart} type="button">
          5m
        </button>
      </div>
      <ChartPanel
        catalogColumns={catalog?.columns ?? []}
        displayItemOptions={mainOptions?.display_items ?? catalog?.displayItems ?? []}
        emptyMessage="Select a scanner row to load charts."
        errorMessage={chartError}
        featureOptions={mainOptions?.feature_columns ?? []}
        indicatorOptions={mainOptions?.standard_indicators ?? MAIN_DISPLAY_ITEMS}
        loading={chartLoading}
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
      />
      {lowerChartCount ? (
        <div className={lowerChartCount === 1 ? "live-lower-chart-grid single" : "live-lower-chart-grid"}>
          {showDayChart ? (
            <div className="live-compact-chart">
              <span>Daily / 60 days</span>
              <ChartPanel
                catalogColumns={catalog?.columns ?? []}
                displayItemOptions={compactOptions?.display_items ?? catalog?.displayItems ?? []}
                emptyMessage="No daily chart data."
                errorMessage={chartError}
                featureOptions={compactOptions?.feature_columns ?? []}
                indicatorOptions={compactOptions?.standard_indicators ?? LOWER_DISPLAY_ITEMS}
                loading={chartLoading}
                onTickerChange={() => undefined}
                onTimeframeChange={() => undefined}
                onVisibleColumnsChange={onCompactVisibleColumnsChange}
                payload={dayPayload}
                ticker={selectedTicker}
                timeframe="1d"
                timeframes={["1d"]}
                visibleColumns={compactVisibleColumns}
              />
            </div>
          ) : null}
          {showFiveMinuteChart ? (
            <div className="live-compact-chart">
              <span>5m / last 2 days</span>
              <ChartPanel
                catalogColumns={catalog?.columns ?? []}
                displayItemOptions={compactOptions?.display_items ?? catalog?.displayItems ?? []}
                emptyMessage="No 5m chart data."
                errorMessage={chartError}
                featureOptions={compactOptions?.feature_columns ?? []}
                indicatorOptions={compactOptions?.standard_indicators ?? LOWER_DISPLAY_ITEMS}
                loading={chartLoading}
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
  );
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

function TicketMetric({ label, tone, value }: { label: string; tone?: "risk"; value: string }) {
  return (
    <div className={tone ? `live-ticket-metric ${tone}` : "live-ticket-metric"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReasonList({ icon, items, title }: { icon: ReactNode; items: string[]; title: string }) {
  return (
    <div className="live-reason-list">
      <div className="live-reason-title">{icon}<span>{title}</span></div>
      {items.length ? items.map((item) => <span key={item}>{item}</span>) : <small>-</small>}
    </div>
  );
}

function availableSessionDates(records: RecordRow[]) {
  return Array.from(new Set(records.filter((record) => record.exists && record.group === "bars" && record.timeframe === "1m").map((record) => record.session_date))).sort();
}

function baseScannerQuery(setups: ScannerSetupGroup[]): BackendTableQuery {
  const minPrice = Math.min(...setups.map((setup) => setup.minPrice), 1);
  const maxPrice = Math.max(...setups.map((setup) => setup.maxPrice), 10);
  const minVolume = Math.min(...setups.map((setup) => setup.minVolume), 0);
  const minTransactions = Math.min(...setups.map((setup) => setup.minTransactions), 0);
  return {
    conditions: [
      { column: "current_open", id: "price", operator: "between", value: String(minPrice), valueSecondary: String(maxPrice) },
      { column: "last_volume", id: "volume", operator: "gte", value: String(minVolume) },
      { column: "last_transactions", id: "transactions", operator: "gte", value: String(minTransactions) },
    ],
    matchMode: "all",
    sortColumn: "last_5m_return",
    sortDirection: "desc",
  };
}

function enrichLiveCandidate(row: Record<string, unknown>, setups: ScannerSetupGroup[]): Record<string, unknown> {
  const currentOpen = numberValue(row, "current_open") || numberValue(row, "open");
  const lastVwap = numberValue(row, "last_vwap");
  const lastClose = numberValue(row, "last_close");
  const lastOpen = numberValue(row, "last_open");
  const dayHigh = numberValue(row, "last_day_high_so_far");
  const lastLow = numberValue(row, "last_low");
  const last5mReturn = numberValue(row, "last_5m_return");
  const transactions = numberValue(row, "last_transactions");
  const txRatio = numberValue(row, "last_transactions_vs_prior_3");
  const bvd = numberValue(row, "last_bearish_volume_divergence_score");
  const aboveVwap = lastVwap > 0 && currentOpen > lastVwap;
  const breakingBody = Boolean(row.current_open_above_last_2_body_high);
  const nearDayHigh = dayHigh > 0 && currentOpen >= dayHigh * 0.995;
  const lastRed = lastClose > 0 && lastOpen > 0 && lastClose < lastOpen;
  const extendedVwap = lastVwap > 0 ? (currentOpen / lastVwap) - 1 : 0;
  const matchedSetup = setups.find((setup) => {
    if (currentOpen < setup.minPrice || currentOpen > setup.maxPrice) return false;
    if (last5mReturn < setup.minLast5mReturn) return false;
    if (numberValue(row, "last_volume") < setup.minVolume) return false;
    if (transactions < setup.minTransactions) return false;
    if (txRatio < setup.minTransactionsRatio) return false;
    if (setup.requireAboveVwap && !aboveVwap) return false;
    if (setup.requireBodyBreak && !breakingBody) return false;
    return true;
  });
  const reasons = [
    matchedSetup ? matchedSetup.name : "",
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
  const priority = (matchedSetup ? 100 : 0) + last5mReturn * 100 + Math.min(25, txRatio) + (aboveVwap ? 10 : 0) + (breakingBody ? 8 : 0) - risks.length * 8;
  const bias = !matchedSetup ? "" : risks.length >= 2 ? "Risk" : aboveVwap && !lastRed ? "Ready" : "Watch";
  const stopBase = lastVwap > 0 ? lastVwap * 0.99 : Math.min(lastLow || currentOpen * 0.98, currentOpen * 0.98);
  return {
    ...row,
    body_break_open: breakingBody,
    day_high_pressure: nearDayHigh,
    live_bias: bias,
    live_priority: priority,
    live_reasons: reasons.join(" | "),
    live_risks: risks.join(" | "),
    live_setup_group: matchedSetup?.name ?? "",
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

function openOnlyChartPayload(payload: ChartPayload | null, cutoffTime: number | null, currentOpen: number, barTime: string): ChartPayload | null {
  return castOpenChartPayload(payload, cutoffTime, currentOpen, `${barTime} open`);
}

function castOpenChartPayload(payload: ChartPayload | null, cutoffTime: number | null, currentOpen: number, label: string): ChartPayload | null {
  if (!payload || !cutoffTime) return payload;
  const priorCandles = payload.candles.filter((candle) => candle.time < cutoffTime);
  const open = currentOpen || priorCandles.at(-1)?.close || 0;
  const currentCandle = open > 0 ? [{ time: cutoffTime, open, high: open, low: open, close: open }] : [];
  const trimmed = trimChartPayload(payload, cutoffTime) ?? payload;
  return {
    ...trimmed,
    candles: [...priorCandles, ...currentCandle],
    markers: [
      ...payload.markers.filter((marker) => Number(marker.time) < cutoffTime),
      { color: "#2563EB", position: "inBar", shape: "circle", size: 1.2, text: label, time: cutoffTime as Time },
    ],
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
    markers: [{ color: "#2563EB", position: "inBar", shape: "circle", size: 1.2, text: "1m open", time: cutoffTime as Time }],
    oscillator_series: priorOscillators,
    overlay_series: priorOverlays,
    price_zones: [],
    regions: [],
    trade_annotations: [],
    volume: [],
  };
}

function liveTableColumns(snapshotColumns: string[]) {
  return [
    "ticker",
    "live_setup_group",
    "live_bias",
    "current_open",
    "last_5m_return",
    "last_volume",
    "last_transactions",
    "last_transactions_vs_prior_3",
    "last_vwap",
    "open_vs_vwap_pct",
    "last_bearish_volume_divergence_score",
    "live_reasons",
    "live_risks",
    "suggested_entry",
    "suggested_stop",
    ...snapshotColumns.filter((column) => !["ticker", "current_open", "last_5m_return", "last_volume", "last_transactions", "last_transactions_vs_prior_3", "last_vwap", "last_bearish_volume_divergence_score"].includes(column)),
  ];
}

function upsertPosition(rows: PositionRow[], symbol: string, quantity: number, price: number, stop: number, mark: number): PositionRow[] {
  const existing = rows.find((row) => row.symbol === symbol);
  const nextQuantity = (existing?.quantity ?? 0) + quantity;
  const avgPrice = existing ? ((existing.avg_price * existing.quantity) + (price * quantity)) / Math.max(1, nextQuantity) : price;
  const row = {
    avg_price: avgPrice,
    mark,
    quantity: nextQuantity,
    stop,
    symbol,
    unrealized_pnl: (mark - avgPrice) * nextQuantity,
    unrealized_pnl_pct: avgPrice > 0 ? (mark / avgPrice) - 1 : 0,
  };
  return [row, ...rows.filter((item) => item.symbol !== symbol)];
}

function buildPortfolioMetrics({ orders, positions }: { orders: OrderRow[]; positions: PositionRow[] }) {
  const realized = 0;
  const unrealized = positions.reduce((total, row) => total + row.unrealized_pnl, 0);
  const exposure = positions.reduce((total, row) => total + row.mark * row.quantity, 0);
  const stagedOrders = orders.filter((order) => order.status === "STAGED").length;
  const fills = orders.filter((order) => order.status === "FILLED").length;
  return {
    items: [
      { icon: <Banknote size={14} />, label: "Total P/L", tone: signedMetricTone(realized + unrealized), value: money(realized + unrealized) },
      { icon: <CircleDollarSign size={14} />, label: "Realized P/L", tone: signedMetricTone(realized), value: money(realized) },
      { icon: <Activity size={14} />, label: "Unrealized P/L", tone: signedMetricTone(unrealized), value: money(unrealized) },
      { icon: <Banknote size={14} />, label: "Equity", tone: signedMetricTone(realized + unrealized), value: money(10_000 + realized + unrealized) },
      { icon: <BarChart3 size={14} />, label: "Exposure", tone: exposure ? "info" : "muted", value: money(exposure) },
      { icon: <WalletCards size={14} />, label: "Open Positions", tone: positions.length ? "info" : "muted", value: integer(positions.length) },
      { icon: <ClipboardList size={14} />, label: "Orders", tone: orders.length ? "info" : "muted", value: integer(orders.length) },
      { icon: <Save size={14} />, label: "Staged", tone: stagedOrders ? "warning" : "muted", value: integer(stagedOrders) },
      { icon: <CheckCircle2 size={14} />, label: "Fills", tone: fills ? "success" : "muted", value: integer(fills) },
      { icon: <ShieldAlert size={14} />, label: "Win Rate", tone: "muted", value: "0%" },
    ],
  };
}

function buildGlobalLiveMetrics({
  decisions,
  lastActionTime,
  liveClockMode,
  scannerRows,
  secondsPerMinute,
  session,
  snapshot,
}: {
  decisions: Record<string, DecisionState>;
  lastActionTime: string;
  liveClockMode: LiveClockMode;
  scannerRows: Record<string, unknown>[];
  secondsPerMinute: string;
  session: TradingSession;
  snapshot: ScannerSnapshot | null;
}) {
  const decisionsCount = Object.keys(decisions).length;
  return {
    items: [
      { icon: <Clock3 size={14} />, label: "Date", tone: "info", value: session.sessionDate || "-" },
      { icon: <Clock3 size={14} />, label: "Clock", tone: liveClockMode === "running" ? "success" : liveClockMode === "seeking" ? "warning" : "muted", value: `${session.barTime} ET` },
      { icon: <Activity size={14} />, label: "Mode", tone: liveClockMode === "running" ? "success" : liveClockMode === "seeking" ? "warning" : "muted", value: liveClockMode },
      { icon: <TableProperties size={14} />, label: "Raw Scanner Rows", tone: snapshot?.row_count ? "info" : "muted", value: integer(snapshot?.row_count ?? 0) },
      { icon: <TrendingUp size={14} />, label: "Signals", tone: scannerRows.length ? "success" : "muted", value: integer(scannerRows.length) },
      { icon: <Target size={14} />, label: "Decisions", tone: decisionsCount ? "info" : "muted", value: integer(decisionsCount) },
      { icon: <SkipForward size={14} />, label: "Replay Pace", tone: "info", value: `${Math.max(1, Number(secondsPerMinute) || 10)}s / 1m` },
      { icon: <CheckCircle2 size={14} />, label: "Last Signal", tone: lastActionTime ? "success" : "muted", value: lastActionTime || "-" },
    ],
  };
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

function coreWindowTitle(id: WindowId) {
  if (id === "portfolio") return "Portfolio";
  if (id === "scanner") return "Scanner";
  if (id === "trade") return "Trade";
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

function countKnownLiveCanvases() {
  try {
    const canvasIds = new Set<string>(["main"]);
    const prefix = `${LIVE_LAYOUT_STORAGE_KEY}.`;
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith(prefix)) continue;
      const suffix = key.slice(prefix.length);
      if (!suffix) continue;
      canvasIds.add(suffix.startsWith("transfer.") ? suffix.slice("transfer.".length) : suffix);
    }
    return canvasIds.size;
  } catch {
    return 1;
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

function readSharedTradingState(): { decisions: Record<string, DecisionState>; orders: OrderRow[]; positions: PositionRow[] } {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_SHARED_STATE_STORAGE_KEY) || "null");
    return {
      decisions: parsed?.decisions ?? {},
      orders: Array.isArray(parsed?.orders) ? parsed.orders : [],
      positions: Array.isArray(parsed?.positions) ? parsed.positions : [],
    };
  } catch {
    return { decisions: {}, orders: [], positions: [] };
  }
}

function readStoredSetupGroups(): ScannerSetupGroup[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_SETUP_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) && parsed.length ? parsed : DEFAULT_SETUP_GROUPS;
  } catch {
    return DEFAULT_SETUP_GROUPS;
  }
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

function stringValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  return value === null || value === undefined ? "" : String(value);
}

function numberValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
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

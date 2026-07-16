import { Check, Clock3, ExternalLink, Globe2, Link2, MapPin, PanelRightOpen, Plus, Save, Settings2, Trash2, Unlink } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MutableRefObject } from "react";

import { api, query } from "../api/client";
import {
  CANVAS_PREVIEW_CONTEXT_STORAGE_KEY,
  CANVAS_REGISTRY_STORAGE_KEY,
  CANVAS_SETTINGS_STORAGE_KEY,
  CANVAS_LINK_GROUPS,
  MAIN_CANVAS_ID,
  canvasLinkGroupDefinition,
  canvasWorkspaceStorageKey,
  createCanvasRecord,
  focusCanvasUrl,
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
import { ChartPanel, type ChartDisplayItem, type ChartPayload } from "../app/components/ChartPanel";
import { AllNewsContainer, NewsDetailContainer, TickerNewsContainer } from "../app/components/NewsContainers";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";
import { TRADING_WORKSPACE_LAYOUT_VERSION, TradingWorkspace, createFocusLayouts } from "../app/components/TradingWorkspace";
import type { WorkspaceWindowLayout, WorkspaceWindowMeta, WorkspaceWindowStatus } from "../app/components/WorkspaceCanvas";
import { TRADING_WORKSPACE_CONTAINERS, containerSupportsSymbolLink, type WorkspaceContainerDefinition, type WorkspaceContainerId } from "../app/tradingWorkspace";

type HistoricalBar = { bar_end?: string; bar_start: string; close: number; high: number; is_closed?: boolean; low: number; open: number; volume: number };
type HistoricalIndicator = { bar_start: string } & Record<string, number | string>;
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
  indicators: HistoricalIndicator[];
  indicatorsAvailable: boolean;
  lastUpdateAt: string;
  loadEarlier: () => void;
  loading: boolean;
  loadingEarlier: boolean;
  pointInTime: boolean;
};

type ContainerSettings = {
  version: 3;
  chart: { showVolume: boolean; symbol: string; timeframe: CanvasChartTimeframe; visibleIndicators: string[] };
  fills: { limit: number; showCommission: boolean };
  journal: { limit: number };
  news: { content: string; lookbackHours: number; ticker: string };
  ticker_news: { lookbackHours: number; showTeaser: boolean };
  news_detail: Record<string, never>;
  orders: { limit: number; showOrderIds: boolean };
  portfolio: { showPositions: boolean; showPnl: boolean };
  scanner: { limit: number; showActivity: boolean };
  sec: { limit: number; form: string };
  strategy: { showSignals: boolean };
  xbrl: { limit: number; showPeriod: boolean };
};

type CanvasPreviewContext = { previewTime: string; sessionDate: string };
type LinkedContainerState = { status: WorkspaceWindowStatus; symbol: string; title: string };

const ALL_CONTAINER_IDS = TRADING_WORKSPACE_CONTAINERS.map((definition) => definition.id);
const DEFAULT_SETTINGS: ContainerSettings = {
  version: 3,
  chart: { showVolume: true, symbol: "AAPL", timeframe: "1m", visibleIndicators: ["indicator.vwap", "indicator.macd"] },
  fills: { limit: 5, showCommission: true },
  journal: { limit: 6 },
  news: { content: "all", lookbackHours: 6, ticker: "" },
  ticker_news: { lookbackHours: 72, showTeaser: true },
  news_detail: {},
  orders: { limit: 6, showOrderIds: true },
  portfolio: { showPositions: true, showPnl: true },
  scanner: { limit: 6, showActivity: true },
  sec: { limit: 5, form: "All" },
  strategy: { showSignals: true },
  xbrl: { limit: 6, showPeriod: true },
};

const HISTORICAL_TIMEFRAMES: CanvasChartTimeframe[] = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h", "1d", "1mo"];
const ENRICHED_QMD_TIMEFRAMES = new Set<CanvasChartTimeframe>(["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"]);
const MACRO_TIMEFRAMES = new Set<CanvasChartTimeframe>(["1d", "1mo"]);
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
] as const;

function displayIndicator(id: string, title: string, group: string, sourceColumns: string[], pane = "price"): ChartDisplayItem {
  return { category: pane === "price" ? "Price overlay" : "Oscillator pane", group, id, presentation: { chartRole: pane === "price" ? "overlay" : "oscillator", pane, selectable: true }, sourceColumns, title };
}

function useCanvasLiveChart(symbol: string, timeframe: CanvasChartTimeframe, cutoffMs: number, sessionDate: string): CanvasLiveChartState {
  const pointInTime = cutoffMs < Date.now() - 5_000;
  const [state, setState] = useState<Omit<CanvasLiveChartState, "loadEarlier">>({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, pointInTime });
  const historyCursorRef = useRef<ChartHistoryCursor | null>(null);
  const historyRequestRef = useRef(false);
  const historyAbortRef = useRef<AbortController | null>(null);
  const requestKeyRef = useRef("");

  const loadEarlier = useCallback(() => {
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}`;
    const cursor = historyCursorRef.current;
    if (!cursor || historyRequestRef.current || requestKeyRef.current !== requestKey) return;
    if (!cursor.nextBefore && !cursor.previousSessionBefore) return;
    const controller = new AbortController();
    historyAbortRef.current = controller;
    historyRequestRef.current = true;
    setState((current) => ({ ...current, historyError: "", loadingEarlier: true }));
    const params = cursor.nextBefore
      ? { as_of: cursor.asOf, before_bar: cursor.nextBefore, row_limit: chartPageSize(timeframe), session_date: cursor.sessionDate, symbol: ticker, timeframe }
      : { before: cursor.previousSessionBefore, row_limit: chartPageSize(timeframe), symbol: ticker, timeframe };
    api<QmdBarHistory>(`/api/trading/canvas-live-chart/history${query(params)}`, { signal: controller.signal, timeoutMs: 120000 })
      .then((payload) => {
        if (requestKeyRef.current !== requestKey) return;
        updateHistoryCursor(historyCursorRef, payload);
        setState((current) => ({
          ...current,
          bars: mergeRowsByTime(closedRowsAtCutoff(payload.history, timeframe, cutoffMs), current.bars),
          canLoadEarlier: payload.has_more,
          historyError: "",
          indicators: mergeRowsByTime(closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs), current.indicators),
          indicatorsAvailable: payload.indicators_available,
        }));
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
  }, [cutoffMs, symbol, timeframe]);

  useEffect(() => {
    let active = true;
    const sockets: Partial<Record<"bars" | "indicators", WebSocket>> = {};
    const reconnectTimers: number[] = [];
    const attempts = { bars: 0, indicators: 0 };
    const requestController = new AbortController();
    const historyController = new AbortController();
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}`;
    historyAbortRef.current?.abort();
    historyAbortRef.current = historyController;
    requestKeyRef.current = requestKey;
    historyCursorRef.current = null;
    historyRequestRef.current = false;
    setState({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, pointInTime });

    const applySnapshot = (kind: "bars" | "indicators", payload: QmdSnapshot<QmdLiveBar> | QmdSnapshot<HistoricalIndicator>, live: boolean) => {
      if (!active) return;
      if (payload.error) {
        if (kind === "bars") setState((current) => ({ ...current, connected: false, error: payload.error || "QMD live bars are unavailable.", loading: false }));
        return;
      }
      const rows = kind === "bars"
        ? closedQmdSnapshotRows(payload as QmdSnapshot<QmdLiveBar>, timeframe, cutoffMs)
        : closedQmdSnapshotRows(payload as QmdSnapshot<HistoricalIndicator>, timeframe, cutoffMs);
      setState((current) => ({
        ...current,
        bars: kind === "bars" ? mergeRowsByTime(current.bars, rows as QmdLiveBar[]) : current.bars,
        connected: kind === "bars" && live ? true : current.connected,
        error: kind === "bars" ? "" : current.error,
        indicators: kind === "indicators" ? mergeRowsByTime(current.indicators, rows as HistoricalIndicator[]) : current.indicators,
        lastUpdateAt: kind === "bars" && live ? new Date().toISOString() : current.lastUpdateAt,
        loading: kind === "bars" ? false : current.loading,
      }));
    };

    if (!pointInTime) {
      api<CanvasLiveChartResponse>(`/api/trading/canvas-live-chart${query({ row_limit: 500, symbol: ticker, timeframe })}`, { signal: requestController.signal, timeoutMs: 5000 })
        .then((payload) => {
          if (!active) return;
          const historicalRows = payload.historical_bars?.history ?? [];
          if (payload.historical_bars) updateHistoryCursor(historyCursorRef, payload.historical_bars);
          setState((current) => ({
            ...current,
            bars: mergeRowsByTime(closedRowsAtCutoff(historicalRows, timeframe, cutoffMs), [...closedQmdSnapshotRows(payload.bars, timeframe, cutoffMs), ...current.bars]),
            canLoadEarlier: historicalRows.length > 0 && Boolean(payload.historical_bars?.has_more),
            error: payload.bars.error ?? "",
            historyError: payload.errors.history ?? "",
            loading: false,
          }));
          applySnapshot("indicators", payload.indicators, false);
        })
        .catch((reason) => {
          if (!active || requestController.signal.aborted) return;
          setState((current) => ({ ...current, error: `QMD live chart unavailable: ${reason instanceof Error ? reason.message : String(reason)}`, loading: false }));
        });
    }

    historyRequestRef.current = true;
    api<QmdBarHistory>(`/api/trading/canvas-live-chart/history${query({ as_of: new Date(cutoffMs).toISOString(), row_limit: chartPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe })}`, { signal: historyController.signal, timeoutMs: 120000 })
      .then((payload) => {
        if (!active || requestKeyRef.current !== requestKey) return;
        updateHistoryCursor(historyCursorRef, payload);
        setState((current) => ({
          ...current,
          bars: mergeRowsByTime(closedRowsAtCutoff(payload.history, timeframe, cutoffMs), current.bars),
          canLoadEarlier: payload.has_more,
          historyError: "",
          indicators: mergeRowsByTime(closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs), current.indicators),
          indicatorsAvailable: payload.indicators_available,
          loading: false,
        }));
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
          applySnapshot(kind, payload, true);
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
      reconnectTimers.forEach((timer) => window.clearTimeout(timer));
      Object.values(sockets).forEach((socket) => socket?.close());
    };
  }, [cutoffMs, pointInTime, sessionDate, symbol, timeframe]);

  return { ...state, loadEarlier };
}

function chartPageSize(timeframe: string) {
  return timeframe === "100ms" ? 5_000 : timeframe === "1s" || timeframe === "5s" ? 10_000 : 5_000;
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
  const rows = new Map(existing.map((row) => [row.bar_start, row]));
  incoming.forEach((row) => {
    if (row && typeof row.bar_start === "string" && row.bar_start) rows.set(row.bar_start, row);
  });
  return [...rows.values()].sort((left, right) => Date.parse(left.bar_start) - Date.parse(right.bar_start));
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
  return <CanvasWorkspaceSurface canvasId={canvasId} manager={false} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} />;
}

function CanvasWorkspaceSurface({ canvasId, manager, requestedInstanceId, requestedNewsId }: { canvasId: string; manager: boolean; requestedInstanceId?: string; requestedNewsId?: string }) {
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
  const dedicatedContainers = new Set<WorkspaceContainerId>(["chart", "news", "ticker_news", "news_detail"]);
  const previewContainerKey = (workspaceState?.openIds ?? []).filter((id) => !dedicatedContainers.has(workspaceContainerKind(id, workspaceState))).sort().join(",");
  const activeLinkGroup = registry.linkAssignments[primaryChartId] ?? "none";
  const activeSymbol = activeLinkGroup === "none" ? primarySettings.chart.symbol : registry.linkContexts[activeLinkGroup].symbol;
  const chartCutoffMs = useMemo(() => dateInTimeZone(previewContext.sessionDate, previewContext.previewTime, "America/New_York").getTime(), [previewContext]);
  const previewClocks = useMemo(() => previewClockReadings(previewContext), [previewContext]);
  const clockIcons = [Clock3, MapPin, Globe2];
  const marketStatus = useMemo(() => historicalMarketStatus(previewContext.sessionDate, previewContext.previewTime), [previewContext]);

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
    return () => window.removeEventListener("storage", syncSharedCanvasState);
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
      .catch((reason) => { if (!cancelled) { setContextError(`Historical coverage unavailable: ${reason instanceof Error ? reason.message : String(reason)}`); setLoading(false); } })
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
    const sourceError = preview?.errors[definition.id] ?? preview?.errors[definition.id === "sec" ? "sec" : definition.id === "xbrl" ? "xbrl" : ""];
    const newsContainer = ["news", "ticker_news", "news_detail"].includes(definition.id);
    return {
      detail: `${definition.title} rendered at the shared configuration clock.`,
      freshness: previewContext.previewTime,
      sourceLabel: sourceError ? "Unavailable" : definition.id === "scanner" ? "QMD History" : newsContainer || ["sec", "xbrl"].includes(definition.id) ? "Point-in-time DB" : "IBKR preview",
      status: sourceError ? "error" : newsContainer || preview ? "ready" : "idle",
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
    updateRegistry((current) => ({ ...current, linkAssignments: { ...current.linkAssignments, [instanceId]: group } }));
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
              return <span key={clock.label}><Icon aria-hidden="true" size={15} /><span><small>{clock.label}</small><strong>{clock.value}</strong><em>{clock.detail}</em></span></span>;
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
            onLinkContextChange={(patch) => { if (group !== "none") updateLinkContext(group, patch); }}
            preview={preview}
            previewContext={previewContext}
            requestedNewsId={requestedNewsId}
            settings={settings}
            settingsOpen={settingsContainerId === instanceId}
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

function ContainerPreview({ canvasId, chartCutoffMs, definition, instanceId, linkContext, linkGroup, linkedContainers, linkOpen, loading, onLinkChange, onLinkContextChange, preview, previewContext, requestedNewsId, settings, settingsOpen, updateSettings }: {
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
  settings: ContainerSettings;
  settingsOpen: boolean;
  updateSettings: SettingsUpdater;
}) {
  const overlayOpen = linkOpen || settingsOpen;
  return <div className="canvas-container-preview">
    {linkOpen ? <div className="canvas-container-settings" aria-label={`${definition.title} link configuration`} data-canvas-link-popover={instanceId}><div className="canvas-link-guide"><strong>Link color</strong><small>Same color = linked</small></div><LinkColorPicker containerTitle={definition.title} onChange={onLinkChange} value={linkGroup} /><LinkedContainerList containerTitle={definition.title} containers={linkedContainers} /></div> : null}
    {settingsOpen ? <div className="canvas-container-settings" aria-label={`${definition.title} settings`}>{containerFields(definition.id, settings, linkContext, updateSettings, onLinkContextChange)}</div> : null}
    <div className={overlayOpen ? "canvas-container-content configuration-open" : "canvas-container-content"}>{definition.id === "chart"
      ? <ChartContainerPreview cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} linkGroup={linkGroup} onLinkContextChange={onLinkContextChange} previewContext={previewContext} settings={settings} updateSettings={updateSettings} />
      : definition.id === "news"
        ? <AllNewsContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, news: { ...state.news, ...patch } }))} settings={settings.news} />
      : definition.id === "ticker_news"
        ? <TickerNewsContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} settings={settings.ticker_news} symbol={linkContext.symbol} />
      : definition.id === "news_detail"
        ? <NewsDetailContainer canvasId={canvasId} requestedNewsId={requestedNewsId} />
      : loading && !preview
        ? <div className="canvas-preview-loading">Loading {definition.title.toLowerCase()}…</div>
        : renderPreview(definition.id, preview, settings, linkGroup, onLinkContextChange)}</div>
  </div>;
}

function LinkedContainerList({ containerTitle, containers }: { containerTitle: string; containers: LinkedContainerState[] }) {
  return <div aria-label={`${containerTitle} linked containers`} className="canvas-linked-container-list">
    {containers.length ? containers.map((container) => <div className="canvas-linked-container-row" key={container.title}><span>{container.title}</span><strong>{container.symbol}</strong><em data-status={container.status}><i aria-hidden="true" />{statusLabel(container.status)}</em></div>) : <small>No containers use this color</small>}
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
  if (id === "sec") {
    const rows = settings.sec.form === "All" ? preview.sec : preview.sec.filter((row) => row.form_type === settings.sec.form);
    return <PreviewTable columns={["accepted_at_utc", "form_type", "company_name", "accession_number"]} rows={rows.slice(0, settings.sec.limit)} />;
  }
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
  updateSettings: SettingsUpdater;
};

const ChartContainerPreview = memo(function ChartContainerPreview({ cutoffMs, instanceId, linkContext, onLinkContextChange, previewContext, settings, updateSettings }: ChartContainerPreviewProps) {
  const liveChart = useCanvasLiveChart(linkContext.symbol, settings.chart.timeframe, cutoffMs, previewContext.sessionDate);
  return <ChartPreview instanceId={instanceId} linkContext={linkContext} liveChart={liveChart} onLinkContextChange={onLinkContextChange} settings={settings} updateSettings={updateSettings} />;
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
    && previousChart.symbol === nextChart.symbol
    && previousChart.timeframe === nextChart.timeframe
    && previousChart.showVolume === nextChart.showVolume
    && stringArraysEqual(previousChart.visibleIndicators, nextChart.visibleIndicators);
}

function stringArraysEqual(previous: readonly string[], next: readonly string[]) {
  return previous.length === next.length && previous.every((value, index) => value === next[index]);
}

function ChartPreview({ instanceId, linkContext, liveChart, onLinkContextChange, settings, updateSettings }: { instanceId: string; linkContext: CanvasLinkContext; liveChart: CanvasLiveChartState; onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void; settings: ContainerSettings; updateSettings: SettingsUpdater }) {
  const indicators = liveChart.indicators;
  const visibleIndicators = liveChart.indicatorsAvailable ? settings.chart.visibleIndicators : [];
  const timeframe = settings.chart.timeframe;
  const payload = useMemo<ChartPayload>(() => ({
    candles: liveChart.bars.map((bar) => ({ close: bar.close, high: bar.high, low: bar.low, open: bar.open, time: Date.parse(bar.bar_start) / 1000 })),
    markers: [],
    oscillator_series: historicalIndicatorSeries(indicators, "oscillator", visibleIndicators),
    overlay_series: historicalIndicatorSeries(indicators, "price", visibleIndicators),
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
  return <ChartPanel canLoadEarlier={liveChart.canLoadEarlier} displayItemOptions={liveChart.indicatorsAvailable ? CHART_INDICATORS : []} emptyMessage={emptyMessage} enableFullscreen={false} errorMessage={liveChart.error || liveChart.historyError} featureOptions={[]} indicatorOptions={[]} initialFitMode="recent" loading={liveChart.loading} loadingEarlier={liveChart.loadingEarlier} onLoadEarlier={liveChart.loadEarlier} onTickerChange={(symbol) => updateChart(symbol.toUpperCase(), timeframe)} onTimeframeChange={(nextTimeframe) => updateChart(linkContext.symbol, nextTimeframe as CanvasChartTimeframe)} onVisibleColumnsChange={(nextVisibleIndicators) => updateSettings((current) => ({ ...current, chart: { ...current.chart, visibleIndicators: nextVisibleIndicators } }))} payload={payload} periodEnd={sessionDate} periodStart={sessionDate} settingsStorageKey={`${CANVAS_SETTINGS_STORAGE_KEY}.${instanceId}`} ticker={linkContext.symbol} timeframe={timeframe} timeframes={HISTORICAL_TIMEFRAMES} visibleColumns={visibleIndicators} />;
}

function historicalIndicatorSeries(rows: HistoricalIndicator[], target: "oscillator" | "price", visibleIndicators: string[]): ChartPayload["overlay_series"] {
  const visible = new Set(visibleIndicators);
  return INDICATOR_SERIES.filter((spec) => visible.has(spec.displayItemId) && (spec.pane === "price" ? "price" : "oscillator") === target).map((spec) => ({
    color: spec.color,
    column: spec.column,
    data: rows.map((row) => ({ time: Date.parse(String(row.bar_start)) / 1000, value: Number(row[spec.column]) })).filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value)),
    displayItemId: spec.displayItemId,
    label: spec.label,
    lineWidth: 1,
    paneKey: spec.pane,
    style: "style" in spec ? spec.style : "line",
  }));
}

function PreviewTable({ columns, onSymbolSelect, rows }: { columns: string[]; onSymbolSelect?: (symbol: string) => void; rows: PreviewRow[] }) {
  if (!rows.length) return <EmptyState label="No point-in-time rows" />;
  return <div className="canvas-preview-table-wrap"><table className="canvas-preview-table"><thead><tr>{columns.map((column) => <th key={column}>{labelFor(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={previewRowKey(row, columns, index)}>{columns.map((column) => <td key={column}>{column === "symbol" && onSymbolSelect ? <button className="canvas-symbol-link" onClick={() => onSymbolSelect(String(row[column]))} type="button">{formatCell(row[column], column)}</button> : formatCell(row[column], column)}</td>)}</tr>)}</tbody></table></div>;
}

function PortfolioPreview({ data, settings }: { data: CanvasPreview["portfolio"]; settings: ContainerSettings["portfolio"] }) {
  return <div className="canvas-portfolio-preview"><div className="canvas-metric-row"><Metric label="Net liquidation" value={money(data.summary.netLiquidation)} /><Metric label="Available" value={money(data.summary.availableFunds)} />{settings.showPnl ? <Metric label="Unrealized P&L" value={money(data.summary.unrealizedPnl)} /> : null}</div>{settings.showPositions ? <PreviewTable columns={["ticker", "position", "mktPrice", "avgCost", "unrealizedPnl"]} rows={data.positions} /> : null}</div>;
}

function StrategyPreview({ data, showSignals }: { data: CanvasPreview["strategy"]; showSignals: boolean }) {
  return <div className="canvas-strategy-preview"><div><span>Strategy</span><strong>{data.strategy_id}</strong></div><div><span>Revision</span><strong>v{data.revision}</strong></div><div><span>State</span><strong>{data.state}</strong></div>{showSignals ? <PreviewTable columns={["time", "symbol", "signal", "value"]} rows={data.signals} /> : null}</div>;
}

function containerFields(id: WorkspaceContainerId, settings: ContainerSettings, linkContext: CanvasLinkContext, updateSettings: SettingsUpdater, onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void) {
  const current = settings[id] as Record<string, unknown>;
  function patch(value: Record<string, unknown>) { updateSettings((state) => ({ ...state, [id]: { ...state[id], ...value } })); }
  if (id === "chart") return <><TextField label="Symbol" onChange={(value) => { patch({ symbol: value.toUpperCase() }); onLinkContextChange({ symbol: value.toUpperCase() }); }} value={linkContext.symbol} /><SelectField label="Bar interval" onChange={(value) => patch({ timeframe: value as CanvasChartTimeframe })} optionLabel={formatChartTimeframe} options={HISTORICAL_TIMEFRAMES} value={settings.chart.timeframe} /><CheckField checked={Boolean(current.showVolume)} label="Show volume" onChange={(value) => patch({ showVolume: value })} /></>;
  if (id === "portfolio") return <><CheckField checked={Boolean(current.showPositions)} label="Show positions" onChange={(value) => patch({ showPositions: value })} /><CheckField checked={Boolean(current.showPnl)} label="Show P&L" onChange={(value) => patch({ showPnl: value })} /></>;
  if (id === "strategy") return <CheckField checked={Boolean(current.showSignals)} label="Show recent signals" onChange={(value) => patch({ showSignals: value })} />;
  if (id === "scanner") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showActivity)} label="Show market activity" onChange={(value) => patch({ showActivity: value })} /></>;
  if (id === "orders") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showOrderIds)} label="Show order IDs" onChange={(value) => patch({ showOrderIds: value })} /></>;
  if (id === "fills") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showCommission)} label="Show commission" onChange={(value) => patch({ showCommission: value })} /></>;
  if (id === "news") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["1", "6", "24", "168", "720"]} value={String(current.lookbackHours)} /><SelectField label="Text coverage" onChange={(value) => patch({ content: value })} options={["all", "full", "title"]} value={String(current.content)} /></>;
  if (id === "ticker_news") return <><SelectField label="Lookback hours" onChange={(value) => patch({ lookbackHours: Number(value) })} options={["24", "72", "168", "720"]} value={String(current.lookbackHours)} /><CheckField checked={Boolean(current.showTeaser)} label="Show teaser" onChange={(value) => patch({ showTeaser: value })} /><div className="canvas-settings-note">Ticker comes from the selected link color. Hot and recent states use the shared clock.</div></>;
  if (id === "news_detail") return <div className="canvas-settings-note">This reader follows the most recently selected news article in this canvas.</div>;
  if (id === "sec") return <><NumberField label="Last N filings" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><SelectField label="Form" onChange={(value) => patch({ form: value })} options={["All", "10-K", "10-Q", "8-K"]} value={String(current.form)} /></>;
  if (id === "xbrl") return <><NumberField label="Last N facts" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showPeriod)} label="Show fiscal period" onChange={(value) => patch({ showPeriod: value })} /></>;
  return <NumberField label="Last N events" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} />;
}

function TextField({ label, onChange, value }: { label: string; onChange: (value: string) => void; value: string }) { return <label><span>{label}</span><input onChange={(event) => onChange(event.target.value)} value={value} /></label>; }
function NumberField({ label, onChange, value }: { label: string; onChange: (value: number) => void; value: number }) { return <label><span>{label}</span><input max={20} min={1} onChange={(event) => onChange(Number(event.target.value))} type="number" value={value} /></label>; }
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
  const visibleIndicators = stored.version === DEFAULT_SETTINGS.version || storedIndicators.includes("indicator.macd") ? storedIndicators : [...storedIndicators, "indicator.macd"];
  const timeframe = HISTORICAL_TIMEFRAMES.includes(stored.chart?.timeframe as CanvasChartTimeframe) ? stored.chart!.timeframe! : DEFAULT_SETTINGS.chart.timeframe;
  return {
    version: DEFAULT_SETTINGS.version,
    chart: { ...DEFAULT_SETTINGS.chart, ...(stored.chart ?? {}), timeframe, visibleIndicators: [...visibleIndicators] },
    fills: { ...DEFAULT_SETTINGS.fills, ...(stored.fills ?? {}) },
    journal: { ...DEFAULT_SETTINGS.journal, ...(stored.journal ?? {}) },
    news: { ...DEFAULT_SETTINGS.news, ...(stored.news ?? {}) },
    ticker_news: { ...DEFAULT_SETTINGS.ticker_news, ...(stored.ticker_news ?? {}) },
    news_detail: {},
    orders: { ...DEFAULT_SETTINGS.orders, ...(stored.orders ?? {}) },
    portfolio: { ...DEFAULT_SETTINGS.portfolio, ...(stored.portfolio ?? {}) },
    scanner: { ...DEFAULT_SETTINGS.scanner, ...(stored.scanner ?? {}) },
    sec: { ...DEFAULT_SETTINGS.sec, ...(stored.sec ?? {}) },
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
  const format = (timeZone?: string) => {
    const zone = timeZone ? { timeZone } : {};
    const date = new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "short", year: "numeric", ...zone }).format(instant);
    const time = new Intl.DateTimeFormat("en-US", { hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", ...zone }).format(instant);
    return { detail: date, value: time };
  };
  return [
    { label: "ET", ...format("America/New_York") },
    { label: "Local", ...format() },
    { label: "UTC", ...format("UTC") },
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

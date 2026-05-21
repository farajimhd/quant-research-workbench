import { useEffect, useMemo, useState, type PointerEvent, type ReactNode } from "react";
import {
  BarChart3,
  ChevronDown,
  ChevronUp,
  Eye,
  Maximize2,
  Minimize2,
  Move,
  PauseCircle,
  Play,
  Plus,
  Save,
  Settings,
  ShieldAlert,
  Target,
  TrendingUp,
  WalletCards,
} from "lucide-react";
import type { Time } from "lightweight-charts";

import { api, query } from "../api/client";
import { ChartPanel, type ChartCatalogItem, type ChartDisplayItem, type ChartPayload } from "../app/components/ChartPanel";
import { DataTable, type BackendTableQuery } from "../app/components/DataTable";
import { MetricStrip } from "../app/components/MetricStrip";
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

type WindowId = "scanner" | "portfolio" | "trade" | "charts";

type WindowLayout = {
  fullscreen: boolean;
  h: number;
  minimized: boolean;
  w: number;
  x: number;
  y: number;
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
const LIVE_SETUP_STORAGE_KEY = "quant-research-workbench.live-trading.scanner-setups";
const LIVE_FEATURE_GROUPS = ["core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure"];
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

const DEFAULT_LAYOUT: Record<WindowId, WindowLayout> = {
  scanner: { fullscreen: false, h: 520, minimized: false, w: 670, x: 12, y: 12, z: 1 },
  portfolio: { fullscreen: false, h: 360, minimized: false, w: 670, x: 12, y: 548, z: 2 },
  trade: { fullscreen: false, h: 360, minimized: false, w: 410, x: 700, y: 548, z: 3 },
  charts: { fullscreen: false, h: 895, minimized: false, w: 870, x: 700, y: 12, z: 4 },
};

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

export function LiveTradingPage() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [session, setSession] = useState<TradingSession>(() => readStoredSession() ?? { barTime: "04:00", sessionDate: "" });
  const [started, setStarted] = useState(false);
  const [setupGroups, setSetupGroups] = useState<ScannerSetupGroup[]>(readStoredSetupGroups);
  const [newSetupName, setNewSetupName] = useState("");
  const [snapshot, setSnapshot] = useState<ScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedRow, setSelectedRow] = useState<Record<string, unknown> | null>(null);
  const [mainChartPayload, setMainChartPayload] = useState<ChartPayload | null>(null);
  const [dayChartPayload, setDayChartPayload] = useState<ChartPayload | null>(null);
  const [fiveMinuteChartPayload, setFiveMinuteChartPayload] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState("");
  const [mainTimeframe, setMainTimeframe] = useState("1m");
  const [mainVisibleColumns, setMainVisibleColumns] = useState<string[]>(MAIN_DISPLAY_ITEMS);
  const [compactVisibleColumns, setCompactVisibleColumns] = useState<string[]>(LOWER_DISPLAY_ITEMS);
  const [headerCollapsed, setHeaderCollapsed] = useState(true);
  const [showDayChart, setShowDayChart] = useState(true);
  const [showFiveMinuteChart, setShowFiveMinuteChart] = useState(true);
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>({});
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [portfolioTab, setPortfolioTab] = useState("Open Positions");
  const [tradeDraft, setTradeDraft] = useState({ limit: "", quantity: "3000", side: "BUY" as "BUY" | "SELL", stop: "", type: "LIMIT" });
  const [layouts, setLayouts] = useState<Record<WindowId, WindowLayout>>(readStoredLayout);

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

  useEffect(() => {
    window.localStorage.setItem(LIVE_LAYOUT_STORAGE_KEY, JSON.stringify(layouts));
  }, [layouts]);

  const sessions = useMemo(() => availableSessionDates(review?.records ?? []), [review]);
  const activeSetups = setupGroups.filter((item) => item.enabled);
  const selectedTicker = stringValue(selectedRow, "ticker");
  const selectedOpen = numberValue(selectedRow, "current_open") || numberValue(selectedRow, "open");
  const selectedTime = selectedRow ? rowTimestampSeconds(selectedRow, session.sessionDate, session.barTime) : null;
  const selectedProfile = selectedRow ? enrichLiveCandidate(selectedRow, activeSetups) : null;
  const scannerRows = useMemo(
    () =>
      (snapshot?.rows ?? [])
        .map((row) => enrichLiveCandidate(row, activeSetups))
        .filter((row) => stringValue(row, "live_setup_group"))
        .sort((a, b) => numberValue(b, "live_priority") - numberValue(a, "live_priority")),
    [activeSetups, snapshot]
  );
  const mainOpenOnlyPayload = useMemo(() => {
    if (mainTimeframe === "1d") return dayOpenOnlyChartPayload(mainChartPayload, session.sessionDate, selectedOpen, selectedTime);
    if (mainTimeframe === "5m") return castOpenChartPayload(mainChartPayload, selectedTime, selectedOpen, `${session.barTime} open`);
    return openOnlyChartPayload(mainChartPayload, selectedTime, selectedOpen, session.barTime);
  }, [mainChartPayload, mainTimeframe, selectedOpen, selectedTime, session.barTime, session.sessionDate]);
  const dayOpenOnlyPayload = useMemo(
    () => dayOpenOnlyChartPayload(dayChartPayload, session.sessionDate, selectedOpen, selectedTime),
    [dayChartPayload, selectedOpen, selectedTime, session.sessionDate]
  );
  const fiveMinuteOpenOnlyPayload = useMemo(
    () => castOpenChartPayload(fiveMinuteChartPayload, selectedTime, selectedOpen, `${session.barTime} open`),
    [fiveMinuteChartPayload, selectedOpen, selectedTime, session.barTime]
  );

  useEffect(() => {
    if (!selectedRow && scannerRows.length) setSelectedRow(scannerRows[0]);
  }, [scannerRows, selectedRow]);

  useEffect(() => {
    if (!scope || !selectedTicker || !session.sessionDate || !started) {
      setMainChartPayload(null);
      setDayChartPayload(null);
      setFiveMinuteChartPayload(null);
      return;
    }
    let active = true;
    setChartLoading(true);
    setChartError("");
    const dayStart = dateOffset(session.sessionDate, -90);
    const fiveMinuteStart = previousSessionDate(sessions, session.sessionDate, 2);
    Promise.allSettled([
      loadChart(scope.processed_root, session.sessionDate, session.sessionDate, mainTimeframe, selectedTicker, mainVisibleColumns),
      loadChart(scope.processed_root, dayStart, session.sessionDate, "1d", selectedTicker, compactVisibleColumns),
      loadChart(scope.processed_root, fiveMinuteStart, session.sessionDate, "5m", selectedTicker, compactVisibleColumns),
    ])
      .then(([mainResult, dayResult, fiveResult]) => {
        if (!active) return;
        setMainChartPayload(mainResult.status === "fulfilled" ? mainResult.value : null);
        setDayChartPayload(dayResult.status === "fulfilled" ? dayResult.value : null);
        setFiveMinuteChartPayload(fiveResult.status === "fulfilled" ? fiveResult.value : null);
        const firstError = [mainResult, dayResult, fiveResult].find((result) => result.status === "rejected");
        setChartError(firstError && firstError.status === "rejected" ? firstError.reason?.message ?? "One chart failed to load." : "");
      })
      .finally(() => {
        if (active) setChartLoading(false);
      });
    return () => {
      active = false;
    };
  }, [compactVisibleColumns, mainTimeframe, mainVisibleColumns, scope, selectedTicker, session.sessionDate, sessions, started]);

  function startTrading() {
    const nextSession = { ...session, sessionDate: session.sessionDate || sessions.at(-1) || "" };
    if (!nextSession.sessionDate) return;
    window.localStorage.setItem(LIVE_SESSION_STORAGE_KEY, JSON.stringify(nextSession));
    setSession(nextSession);
    setStarted(true);
  }

  function loadScanner() {
    if (!scope || !session.sessionDate) return;
    setLoading(true);
    setError("");
    api<ScannerSnapshotPayload>(
      `/api/market-data/scanner-snapshot${query({
        processed_root: scope.processed_root,
        session_date: session.sessionDate,
        timeframe: "1m",
        bar_time: session.barTime,
        feature_groups: LIVE_FEATURE_GROUPS.join(","),
        columns: LIVE_SCANNER_COLUMNS.join(","),
        table_query: JSON.stringify(baseScannerQuery(activeSetups)),
        row_limit: 1000,
      })}`
    )
      .then((payload) => {
        setSnapshot(payload.snapshot);
        const firstRow = payload.snapshot.rows.map((row) => enrichLiveCandidate(row, activeSetups)).find((row) => stringValue(row, "live_setup_group")) ?? null;
        setSelectedRow(firstRow);
      })
      .catch((requestError: Error) => {
        setSnapshot(null);
        setSelectedRow(null);
        setError(requestError.message || "Scanner request failed.");
      })
      .finally(() => setLoading(false));
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
      const topZ = Math.max(...Object.values(current).map((layout) => layout.z));
      if (current[id].z >= topZ) return current;
      return { ...current, [id]: { ...current[id], z: topZ + 1 } };
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
      <section className={headerCollapsed ? "live-top-shell collapsed" : "live-top-shell"}>
        <div className="live-top-rail">
          <div>
            <strong>Semi-Auto Trading</strong>
            <span>{session.sessionDate} {session.barTime} ET</span>
          </div>
          <button className="button secondary" onClick={() => setHeaderCollapsed((value) => !value)} type="button">
            {headerCollapsed ? <ChevronDown size={15} /> : <ChevronUp size={15} />} {headerCollapsed ? "Show Session" : "Hide Session"}
          </button>
        </div>
        {!headerCollapsed ? (
          <div className="live-top-content">
            <PageIntro
              groupLabel="Live Trading"
              title="Semi-Auto Trading"
              description="Broker-ready workspace for scanner-led small-cap momentum decisions."
              actions={
                <div className="live-session-toolbar">
                  <LiveSelect label="Date" value={session.sessionDate} values={sessions} onChange={(value) => setSession({ ...session, sessionDate: value })} />
                  <LiveField label="Bar open" type="time" value={session.barTime} onChange={(value) => setSession({ ...session, barTime: value })} />
                  <button className="button primary" disabled={loading} onClick={loadScanner} type="button">
                    {loading ? <span className="loading-spinner" aria-hidden="true" /> : <Play size={15} />} Load
                  </button>
                  <button className="button secondary" onClick={() => setStarted(false)} type="button">
                    <Settings size={15} /> Session
                  </button>
                </div>
              }
            />
            {error ? <div className="preview-sample-status error">{error}</div> : null}
            {snapshot?.reason ? <div className="preview-sample-status error">{snapshot.reason}</div> : null}
          </div>
        ) : null}
      </section>
      <section className={headerCollapsed ? "live-workspace compact" : "live-workspace"} aria-label="Semi-auto trading workspace">
        <WorkspaceWindow id="scanner" layout={layouts.scanner} title="Scanner" icon={<TrendingUp size={15} />} onFocus={bringWindowForward} onLayoutChange={updateLayout}>
          <ScannerContainer
            activeSetups={activeSetups}
            loading={loading}
            newSetupName={newSetupName}
            rows={scannerRows}
            selectedTicker={selectedTicker}
            setupGroups={setupGroups}
            snapshot={snapshot}
            onAddSetup={addSetupGroup}
            onLoad={loadScanner}
            onNewSetupNameChange={setNewSetupName}
            onRowSelect={setSelectedRow}
            onSetupGroupsChange={setSetupGroups}
          />
        </WorkspaceWindow>
        <WorkspaceWindow id="portfolio" layout={layouts.portfolio} title="Portfolio" icon={<WalletCards size={15} />} onFocus={bringWindowForward} onLayoutChange={updateLayout}>
          <PortfolioContainer orders={orders} positions={positions} selectedTab={portfolioTab} onTabChange={setPortfolioTab} />
        </WorkspaceWindow>
        <WorkspaceWindow id="trade" layout={layouts.trade} title="Trade" icon={<Target size={15} />} onFocus={bringWindowForward} onLayoutChange={updateLayout}>
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
        <WorkspaceWindow id="charts" layout={layouts.charts} title="Charts" icon={<BarChart3 size={15} />} onFocus={bringWindowForward} onLayoutChange={updateLayout}>
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
            selectedTicker={selectedTicker}
            session={session}
            showDayChart={showDayChart}
            showFiveMinuteChart={showFiveMinuteChart}
            onCompactVisibleColumnsChange={setCompactVisibleColumns}
            onMainTimeframeChange={setMainTimeframe}
            onMainVisibleColumnsChange={setMainVisibleColumns}
            onToggleDayChart={() => setShowDayChart((value) => !value)}
            onToggleFiveMinuteChart={() => setShowFiveMinuteChart((value) => !value)}
          />
        </WorkspaceWindow>
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
        description="Choose the trading date and starting bar. The workspace will load provider artifacts for that session only."
      />
      <section className="live-start-panel panel">
        <div className="live-start-copy">
          <span>Session Setup</span>
          <strong>{session.sessionDate || "Select a session"}</strong>
          <p>Historical sessions run as open-by-open simulation. The same boundary can later point to live broker and data-provider connectors.</p>
        </div>
        <div className="live-start-form">
          <LiveSelect label="Trading date" value={session.sessionDate} values={sessions} onChange={(value) => onSessionChange({ ...session, sessionDate: value })} />
          <LiveField label="First bar open" type="time" value={session.barTime} onChange={(value) => onSessionChange({ ...session, barTime: value })} />
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
  onFocus,
  onLayoutChange,
  title,
}: {
  children: ReactNode;
  icon: ReactNode;
  id: WindowId;
  layout: WindowLayout;
  onFocus: (id: WindowId) => void;
  onLayoutChange: (id: WindowId, patch: Partial<WindowLayout>) => void;
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
        <div className="live-window-actions">
          <button className="toolbar-button" onClick={() => onLayoutChange(id, { minimized: !layout.minimized })} title={layout.minimized ? "Restore" : "Minimize"} type="button">
            <Minimize2 size={14} />
          </button>
          <button className="toolbar-button" onClick={() => onLayoutChange(id, { fullscreen: !layout.fullscreen, minimized: false })} title={layout.fullscreen ? "Exit fullscreen" : "Fullscreen"} type="button">
            <Maximize2 size={14} />
          </button>
        </div>
      </div>
      {!layout.minimized ? <div className="live-window-body">{children}</div> : null}
      {!layout.minimized ? <div className="live-window-resize" onPointerDown={startResize} /> : null}
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
            {loading ? <span className="loading-spinner" aria-hidden="true" /> : <Play size={14} />} Run
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
  onTabChange,
  orders,
  positions,
  selectedTab,
}: {
  onTabChange: (tab: string) => void;
  orders: OrderRow[];
  positions: PositionRow[];
  selectedTab: string;
}) {
  const realized = 0;
  const unrealized = positions.reduce((total, row) => total + row.unrealized_pnl, 0);
  const tabs = ["Open Positions", "P/L", "Trades", "Orders"];
  return (
    <div className="live-container-stack">
      <MetricStrip
        items={[
          { label: "Equity", value: money(10_000 + unrealized), kind: "status" },
          { label: "Realized", value: money(realized), kind: "status" },
          { label: "Unrealized", value: money(unrealized), kind: "status" },
          { label: "Open Positions", value: positions.length, kind: "number" },
          { label: "Orders", value: orders.length, kind: "number" },
          { label: "Win Rate", value: "0%", kind: "status" },
        ]}
      />
      <Tabs tabs={tabs} active={selectedTab} onChange={onTabChange} />
      {selectedTab === "Open Positions" ? <DataTable rows={positions} empty="No open positions." /> : null}
      {selectedTab === "P/L" ? <DataTable rows={positions.map((row) => ({ symbol: row.symbol, unrealized_pnl: row.unrealized_pnl, unrealized_pnl_pct: row.unrealized_pnl_pct, mark: row.mark, avg_price: row.avg_price }))} empty="No P/L rows." /> : null}
      {selectedTab === "Trades" ? <DataTable rows={[]} empty="No completed trades yet." /> : null}
      {selectedTab === "Orders" ? <DataTable rows={orders} empty="No staged orders." /> : null}
    </div>
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

function readStoredSession(): TradingSession | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(LIVE_SESSION_STORAGE_KEY) || "null");
    return value?.sessionDate ? value : null;
  } catch {
    return null;
  }
}

function readStoredLayout(): Record<WindowId, WindowLayout> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_LAYOUT_STORAGE_KEY) || "{}") as Partial<Record<WindowId, Partial<WindowLayout>>>;
    return (Object.keys(DEFAULT_LAYOUT) as WindowId[]).reduce(
      (layouts, id) => ({ ...layouts, [id]: { ...DEFAULT_LAYOUT[id], ...(parsed[id] ?? {}) } }),
      {} as Record<WindowId, WindowLayout>
    );
  } catch {
    return DEFAULT_LAYOUT;
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

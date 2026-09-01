import { Activity, BadgeDollarSign, BriefcaseBusiness, Check, CircleDollarSign, Clock3, Globe2, Landmark, Link2, MapPin, PanelRightOpen, Pause, Play, RefreshCcw, Search, Save, Settings2, ShieldCheck, TriangleAlert, Unlink, WalletCards } from "lucide-react";
import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MutableRefObject, type ReactNode } from "react";

import { api, apiCached, query, type ApiError } from "../api/client";
import "./HistoricalWorkspace.css";
import "../app/configurationVisuals.css";
import {
  CANVAS_PREVIEW_CONTEXT_STORAGE_KEY,
  CANVAS_REGISTRY_STORAGE_KEY,
  CANVAS_REGISTRY_UPDATED_EVENT,
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
  mergeSharedCanvasProfile,
  readCanvasRegistry,
  readCanvasFocusHandoff,
  readCanvasRuntimeRegistry,
  readCanvasRuntimeOverlayRecord,
  readCanvasWorkspaceState,
  readCanvasWorkspaceStateByStorageKey,
  readReplayCanvasFocusHandoff,
  removeCanvasRecord,
  replayFocusCanvasUrl,
  rebaseCanvasRuntimeOverlay,
  snapshotCanvasWorkspaceState,
  snapshotCanvasProfile,
  snapshotSharedCanvasProfile,
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
import { isTerminalReplayStatus, latestReplayRun, useReplayRunEvents, type CanvasReplayRun } from "../app/replayRun";
import { AllNewsContainer, NEWS_ARTICLE_CLASS_OPTIONS, NewsDetailContainer, TickerNewsContainer } from "../app/components/NewsContainers";
import { AllSecContainer, SecDetailContainer, TickerSecContainer } from "../app/components/SecContainers";
import { LoadingState } from "../app/components/LoadingState";
import { MarketTime } from "../app/components/MarketTime";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";
import { ChartsQuotesMarketLayout, QuotesTapeContainer, type ChartsQuotesLayoutSettings } from "../app/components/MarketMicrostructureContainers";
import { MarketScannerContainer, SCANNER_TIMEFRAMES, SignalStreamContainer, StrategyActivityContainer, WatchUniverseContainer, type StrategyActivitySettings, type WatchlistRuntimeResponse } from "../app/components/MarketScreenerContainers";
import { StockFactsContainer } from "../app/components/StockFactsContainer";
import { XbrlAnalysisContainer, type XbrlAnalysisSettings } from "../app/components/XbrlAnalysisContainer";
import { useWallClock } from "../app/components/useWallClock";
import { TickerIdentity, useTickerPresentations } from "../app/components/TickerIdentity";
import { TRADING_WORKSPACE_LAYOUT_VERSION, TradingWorkspace, createFocusLayouts, type WorkspaceGroupTemplate } from "../app/components/TradingWorkspace";
import type { WorkspaceWindowLayout, WorkspaceWindowMeta, WorkspaceWindowStatus } from "../app/components/WorkspaceCanvas";
import { normalizeWorkspaceGroups } from "../app/workspaceGroups";
import { TRADING_WORKSPACE_CONTAINERS, containerSupportsCanvasLink, containerSupportsSymbolLink, type WorkspaceContainerDefinition, type WorkspaceContainerId } from "../app/tradingWorkspace";
import type { TradingWorkspaceMode } from "../app/tradingWorkspace";
import { DEFAULT_STRATEGY_CHART_PRESENTATION, type StrategyAction, type StrategyChartPresentation, type StrategyDecisionEvent } from "../app/strategyPresentation";

import {
  CanonicalTradingPreview,
  CanvasChartSettings,
  CanvasContext,
  CanvasPreview,
  CanvasPreviewContext,
  CanvasRuntimeMode,
  CanvasScannerSnapshot,
  ContainerSettings,
  LinkedContainerState,
  LivePerformanceState,
  PerformanceSnapshot,
  PerformanceSnapshotResponse,
} from "../features/canvas/contracts";
import {
  ALL_CONTAINER_IDS,
  HISTORICAL_TIMEFRAMES,
  MANAGER_DEFAULT_CONTAINER_IDS,
  READ_ONLY_BLOCKED_CONTAINERS,
} from "../features/canvas/configuration";
import { marketSessionDate, useCanvasHistoricalChart } from "../features/canvas/chartData";
import { finiteNumber } from "../features/canvas/numbers";
import { nestedValue } from "../features/canvas/presentationFormat";
import { cloneDefaultSettings, instanceSettings, normalizeSettings } from "../features/canvas/settings";
import { ensureHistoricalChartsQuotesIndicators, openTickerChartsQuotes } from "../app/tickerNavigation";
import { useCanvasLiveScannerSnapshot, useCanvasScannerSnapshot } from "../features/canvas/scannerData";
import { dateInTimeZone } from "../features/canvas/time";

type CanvasChartPreviewProps = Parameters<typeof import("../features/canvas/chartPresentation").ChartPreview>[0];
const CHARTS_QUOTES_CONTEXT_APPEARANCE_DEFAULTS = {
  daySeparatorsVisible: false,
  legendGutterVisible: false,
  rightLegendGutterVisible: false,
} as const;
const LazyCanvasChartPreview = lazy(() => import("../features/canvas/chartPresentation").then((module) => ({ default: module.ChartPreview })));
type TradingContainerPreviewProps = import("../features/canvas/tradingPresentation").TradingContainerPreviewProps;
type StrategyOrderEntryProps = Parameters<typeof import("../features/canvas/tradingPresentation").StrategyOrderEntry>[0];
const LazyTradingContainerPreview = lazy(() => import("../features/canvas/tradingPresentation").then((module) => ({ default: module.TradingContainerPreview })));
const LazyStrategyOrderEntry = lazy(() => import("../features/canvas/tradingPresentation").then((module) => ({ default: module.StrategyOrderEntry })));
const LazyCanvasManagementPanel = lazy(() => import("../features/canvas/CanvasManagementPanel"));

function ChartPreview(props: CanvasChartPreviewProps) {
  return <Suspense fallback={<LoadingState fill label="Loading chart" />}>
    <LazyCanvasChartPreview {...props} />
  </Suspense>;
}

function TradingContainerPreview(props: TradingContainerPreviewProps) {
  return <Suspense fallback={<LoadingState fill label="Loading trading view" />}>
    <LazyTradingContainerPreview {...props} />
  </Suspense>;
}

function StrategyOrderEntry(props: StrategyOrderEntryProps) {
  return <Suspense fallback={<LoadingState fill label="Loading order entry" />}>
    <LazyStrategyOrderEntry {...props} />
  </Suspense>;
}

const LIVE_ACCOUNT_KEYS_STORAGE_KEY = "quant-research-workbench.real-live-trading.account-keys";
const LIVE_PERFORMANCE_STORAGE_KEY = "quant-research-workbench.canvas.live-performance-v1";
const CHARTS_QUOTES_GROUP_TEMPLATE_ID = "charts-quotes";
const CANVAS_GROUP_TEMPLATES: WorkspaceGroupTemplate[] = [{
  description: "A fixed synchronized market workspace; only its ticker context changes.",
  id: CHARTS_QUOTES_GROUP_TEMPLATE_ID,
  memberSummary: "Intraday, daily, and monthly charts · quote and trade context",
  title: "Charts & Quotes",
}];

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

type EditableCanvasProfile = {
  available: boolean;
  content_hash: string;
  profile: CanvasRegistry | null;
  revision: number;
  schema_version: number;
  updated_at: string;
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
  if (!approved) return <div className="canvas-config-page canvas-focus-page"><LoadingState fill label={`Loading ${mode} workspace`} /></div>;
  return <CanvasWorkspaceSurface accountKeys={accountKeys} approvedCanvas={approved} canvasId={MAIN_CANVAS_ID} manager={false} modeControls={modeControls} runtimeMode={mode} />;
}

export function CanvasFocusPage() {
  const params = new URLSearchParams(window.location.search);
  const canvasFocusToken = params.get("canvas_focus") || undefined;
  const acceptanceRuntimeMode = params.get("runtime_mode");
  const runtimeMode = acceptanceRuntimeMode === "live" || acceptanceRuntimeMode === "paper" ? acceptanceRuntimeMode : undefined;
  if (canvasFocusToken) return <TransientCanvasFocusPage focusToken={canvasFocusToken} runtimeMode={runtimeMode} />;
  const replayRunId = params.get("replay_run") || undefined;
  const replayFocusToken = params.get("replay_focus") || undefined;
  const historicalModeValue = params.get("historical_mode");
  const historicalMode = historicalModeValue === "backtest" || historicalModeValue === "backtest_debug" ? historicalModeValue : "replay";
  if (replayRunId && replayFocusToken) return <ReplayCanvasFocusPage focusToken={replayFocusToken} runId={replayRunId} runMode={historicalMode} />;
  const acceptanceKind = params.get("container_preview") as WorkspaceContainerId | null;
  if (acceptanceKind && TRADING_WORKSPACE_CONTAINERS.some((definition) => definition.id === acceptanceKind)) {
    return <CanvasContainerAcceptancePage
      kind={acceptanceKind}
      requestedNewsId={params.get("news") || undefined}
      requestedSecAccession={params.get("sec_accession") || undefined}
      requestedSecCik={params.get("sec_cik") || undefined}
      runtimeMode={acceptanceRuntimeMode === "live" || acceptanceRuntimeMode === "paper" ? acceptanceRuntimeMode : undefined}
    />;
  }
  const canvasId = params.get("canvas") || MAIN_CANVAS_ID;
  const requestedInstanceId = params.get("container") || undefined;
  const requestedNewsId = params.get("news") || undefined;
  const requestedSecCik = params.get("sec_cik") || undefined;
  const requestedSecAccession = params.get("sec_accession") || undefined;
  if (params.get("canvas_profile") === "draft") return <CanvasWorkspaceSurface canvasId={canvasId} manager={false} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} runtimeMode={runtimeMode} />;
  return <ApprovedCanvasFocusPage canvasId={canvasId} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} runtimeMode={runtimeMode} />;
}

function TransientCanvasFocusPage({ focusToken, runtimeMode }: { focusToken: string; runtimeMode?: Extract<CanvasRuntimeMode, "live" | "paper"> }) {
  const [handoff] = useState(() => readCanvasFocusHandoff(focusToken));
  if (!handoff) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-inline-error">This focus group is missing or expired.</div></div>;
  const profile: CanvasRegistry = {
    ...handoff.profile,
    canvases: [{ id: MAIN_CANVAS_ID, label: handoff.label }],
    defaultState: handoff.state,
    workspaceStates: { [MAIN_CANVAS_ID]: handoff.state },
  };
  const approved: ApprovedCanvasProfile = {
    available: true,
    canvas_revision: `focus-${focusToken}`,
    configuration_revision: 0,
    content_hash: `focus-${focusToken}`,
    profile,
    revision_id: "transient-focus",
    schema_version: 1,
  };
  return <CanvasWorkspaceSurface approvedCanvas={approved} canvasId={MAIN_CANVAS_ID} manager={false} runtimeMode={runtimeMode} runtimeWorkspaceId={focusToken} transient />;
}

function CanvasContainerAcceptancePage({ kind, requestedNewsId, requestedSecAccession, requestedSecCik, runtimeMode }: { kind: WorkspaceContainerId; requestedNewsId?: string; requestedSecAccession?: string; requestedSecCik?: string; runtimeMode?: Extract<CanvasRuntimeMode, "live" | "paper"> }) {
  const instanceId = `${kind}-acceptance`;
  const acceptanceCanvasId = "container-acceptance";
  const approved = useMemo<ApprovedCanvasProfile>(() => {
    const state: CanvasWorkspaceState = {
      groups: {},
      instances: { [instanceId]: kind },
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts: createFocusLayouts([instanceId]),
      openIds: [instanceId],
    };
    const local = readCanvasRegistry();
    const profile: CanvasRegistry = {
      ...local,
      canvases: [{ id: acceptanceCanvasId, label: `${containerTitle(kind)} acceptance` }],
      defaultState: state,
      workspaceStates: { [acceptanceCanvasId]: state },
    };
    return {
      available: true,
      canvas_revision: `container-acceptance-${kind}-v1`,
      configuration_revision: 0,
      content_hash: `container-acceptance-${kind}-v1`,
      profile,
      revision_id: "container-acceptance",
      schema_version: 1,
    };
  }, [acceptanceCanvasId, instanceId, kind]);
  return <div data-canvas-container-acceptance={kind}>
    <CanvasWorkspaceSurface approvedCanvas={approved} canvasId={acceptanceCanvasId} manager={false} requestedInstanceId={instanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} runtimeMode={runtimeMode} />
  </div>;
}

function ApprovedCanvasFocusPage({ canvasId, requestedInstanceId, requestedNewsId, requestedSecAccession, requestedSecCik, runtimeMode }: { canvasId: string; requestedInstanceId?: string; requestedNewsId?: string; requestedSecAccession?: string; requestedSecCik?: string; runtimeMode?: Extract<CanvasRuntimeMode, "live" | "paper"> }) {
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
  if (!approved) return <div className="canvas-config-page canvas-focus-page"><LoadingState fill label="Loading Canvas workspace" /></div>;
  return <CanvasWorkspaceSurface approvedCanvas={approved} canvasId={canvasId} manager={false} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} runtimeMode={runtimeMode} />;
}

function ReplayCanvasFocusPage({ focusToken, runId, runMode }: { focusToken: string; runId: string; runMode: "backtest" | "backtest_debug" | "replay" }) {
  const [handoff] = useState(() => {
    const stored = readReplayCanvasFocusHandoff(focusToken);
    return stored ? { ...stored, profile: ensureHistoricalChartsQuotesIndicators(stored.profile, stored.state) } : null;
  });
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
    const loadRun = async () => {
      try {
        return await api<CanvasReplayRun>(`/api/trading/${runMode}/runs/${encodeURIComponent(runId)}?compact=true`, { timeoutMs: 20_000 });
      } catch (reason) {
        const status = typeof reason === "object" && reason && "status" in reason ? Number((reason as ApiError).status) : 0;
        if (runMode !== "backtest" || status !== 404) throw reason;
        return api<CanvasReplayRun>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/review`, {
          method: "POST",
          timeoutMs: 60_000,
        });
      }
    };
    loadRun()
      .then((payload) => { if (!cancelled) mergeFocusRun(payload); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, [handoff, mergeFocusRun, runId, runMode]);

  useReplayRunEvents(handoff && runMode === "replay" ? runId : undefined, mergeFocusRun, setError);

  if (error && !run) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-inline-error">{error}</div></div>;
  if (!run) return <div className="canvas-config-page canvas-focus-page"><LoadingState fill label="Loading Replay workspace" /></div>;
  return <CanvasWorkspaceSurface canvasId={MAIN_CANVAS_ID} manager={false} modeControls={<ReplayFocusTransportStatus run={run} />} readOnly={runMode !== "replay"} replayRun={run} runtimeWorkspaceId={focusToken} transient />;
}

function ReplayFocusTransportStatus({ run }: { run: CanvasReplayRun }) {
  const active = ["running", "fast_forwarding"].includes(run.status);
  const terminal = isTerminalReplayStatus(run.status);
  const label = terminal ? run.status.replaceAll("_", " ") : active ? "Playing" : "Paused";
  const speed = run.speed === 0 ? "Maximum throughput" : run.speed === 1 ? "1× real time" : `Up to ${run.speed}×`;
  const Icon = active ? Play : Pause;
  return <div aria-label={`Replay ${label} at ${speed}`} className="replay-focus-transport" data-status={run.status} role="status"><Icon aria-hidden="true" size={13} /><span><strong>{label}</strong><small>{speed}</small></span></div>;
}

export function CanvasWorkspaceSurface({ accountKeys, approvedCanvas, canvasId, manager, modeControls, readOnly = false, replayRun, requestedInstanceId, requestedNewsId, requestedSecAccession, requestedSecCik, runtimeMode: requestedRuntimeMode, runtimeWorkspaceId, transient = false }: { accountKeys?: string[]; approvedCanvas?: ApprovedCanvasProfile; canvasId: string; manager: boolean; modeControls?: ReactNode; readOnly?: boolean; replayRun?: CanvasReplayRun; requestedInstanceId?: string; requestedNewsId?: string; requestedSecAccession?: string; requestedSecCik?: string; runtimeMode?: CanvasRuntimeMode; runtimeWorkspaceId?: string; transient?: boolean }) {
  const runtimeMode: CanvasRuntimeMode = replayRun?.mode === "backtest" || replayRun?.mode === "backtest_debug" ? replayRun.mode : replayRun ? "replay" : requestedRuntimeMode ?? "canvas";
  const liveMode = runtimeMode === "live" || runtimeMode === "paper";
  const durableTerminalReview = Boolean(
    replayRun
    && (runtimeMode === "backtest" || runtimeMode === "backtest_debug")
    && isTerminalReplayStatus(replayRun.status),
  );
  const replayRuntimeReady = !replayRun || replayRun.runtime_ready === true || durableTerminalReview;
  const focusRuntimeMode = runtimeMode === "live" || runtimeMode === "paper" ? runtimeMode : undefined;
  const resolvedAccountKeys = readOnly ? [] : accountKeys?.length ? accountKeys : readLiveAccountKeys();
  const accountSignature = [...resolvedAccountKeys].sort().join(".") || runtimeMode;
  const runtimeBase = replayRun?.canvas_profile ?? approvedCanvas?.profile;
  const runtimeRevision = replayRun?.configuration_content_hash || replayRun?.canvas_revision || approvedCanvas?.content_hash || approvedCanvas?.canvas_revision || "draft";
  const runtimeScope = replayRun
    ? runtimeWorkspaceId
      ? `${runtimeMode}.${replayRun.run_id}.${runtimeWorkspaceId}`
      : `${runtimeMode}.${replayRun.execution_mode || "manual"}`
    : liveMode ? `${runtimeMode}.${accountSignature}` : runtimeMode === "research" ? `research.${runtimeWorkspaceId || canvasId}` : approvedCanvas ? "canvas" : "configuration";
  const runtimeRegistryStorageKey = runtimeBase ? canvasRuntimeRegistryStorageKey(runtimeScope, runtimeRevision) : "";
  const workspaceStorageKey = runtimeBase
    ? canvasRuntimeWorkspaceStorageKey(runtimeScope, runtimeRevision, canvasId)
    : canvasWorkspaceStorageKey(canvasId);
  const [overlayEpoch, setOverlayEpoch] = useState(0);
  const [runtimeRebase, setRuntimeRebase] = useState<CanvasRuntimeRebase | null>(() => {
    if (!runtimeBase || transient) return null;
    const previous = readCanvasRuntimeOverlayRecord(runtimeScope, canvasId);
    return previous && previous.revision !== runtimeRevision
      ? rebaseCanvasRuntimeOverlay(previous, runtimeBase, canvasId)
      : null;
  });
  const initialCanvasState = useMemo<CanvasWorkspaceState | null>(() => {
    const state = runtimeBase
      ? runtimeCanvasState(runtimeBase, workspaceStorageKey, canvasId, requestedInstanceId, !transient)
      : focusCanvasState(canvasId, requestedInstanceId);
    return replayRun?.execution_mode === "strategy" && !requestedInstanceId && !transient
      ? strategyReplayCanvasState(runtimeBase && !transient ? readCanvasWorkspaceStateByStorageKey(workspaceStorageKey) : null)
      : state;
  }, [canvasId, overlayEpoch, replayRun?.execution_mode, requestedInstanceId, runtimeBase, transient, workspaceStorageKey]);
  const [registry, setRegistry] = useState<CanvasRegistry>(() => {
    const base = runtimeBase
      ? transient && !replayRun ? runtimeBase : readCanvasRuntimeRegistry(runtimeBase, runtimeRegistryStorageKey)
      : readCanvasRegistry();
    return replayRun?.execution_mode === "strategy" && !transient
      ? strategyReplayRegistry(base, replayRun)
      : base;
  });
  const [previewContext, setPreviewContext] = useState<CanvasPreviewContext>(() => replayRun ? replayPreviewContext(replayRun) : liveMode ? currentLivePreviewContext() : readPreviewContext());
  const liveClockInstant = useWallClock(1_000, liveMode);
  const livePreviewRefreshMs = useWallClock(15_000, liveMode);
  const [preview, setPreview] = useState<CanvasPreview | null>(null);
  const [contextReady, setContextReady] = useState(Boolean(replayRun || liveMode));
  const [contextError, setContextError] = useState("");
  const [workspaceState, setWorkspaceState] = useState<CanvasWorkspaceState | null>(initialCanvasState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [defaultSaved, setDefaultSaved] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);
  const [editableProfileReady, setEditableProfileReady] = useState(Boolean(runtimeBase));
  const editableProfileRevisionRef = useRef(0);
  const [linkPopoverContainerId, setLinkPopoverContainerId] = useState<string | null>(null);
  const [settingsContainerId, setSettingsContainerId] = useState<string | null>(null);
  const managementEnabled = !transient && (manager || Boolean(runtimeBase));
  const workspaceDefinitions = useMemo(() => readOnly
    ? TRADING_WORKSPACE_CONTAINERS.filter((definition) => !READ_ONLY_BLOCKED_CONTAINERS.has(definition.id))
    : TRADING_WORKSPACE_CONTAINERS, [readOnly]);

  const currentCanvas = registry.canvases.find((canvas) => canvas.id === canvasId) ?? { id: canvasId, label: canvasId === MAIN_CANVAS_ID ? "Main" : "Focus canvas" };
  const primaryChartId = (workspaceState?.openIds ?? []).find((id) => workspaceContainerKind(id, workspaceState) === "chart") ?? "chart";
  const primarySettings = instanceSettings(registry, primaryChartId);
  const dedicatedContainers = new Set<WorkspaceContainerId>(["chart", "charts_quotes", "facts", "microstructure", "news", "ticker_news", "news_detail", "sec", "ticker_sec", "sec_detail", "xbrl", "scanner", "signal_stream", "watchlist", "strategy_activity"]);
  const historicalTradingContainers = new Set<WorkspaceContainerId>(["chart", "charts_quotes"]);
  const previewContainerKey = (workspaceState?.openIds ?? []).filter((id) => {
    const kind = workspaceContainerKind(id, workspaceState);
    return !dedicatedContainers.has(kind) || Boolean(replayRun && historicalTradingContainers.has(kind));
  }).sort().join(",");
  const scannerContainerKey = (workspaceState?.openIds ?? []).filter((id) => ["scanner", "watchlist"].includes(workspaceContainerKind(id, workspaceState))).sort().join(",");
  const scannerNeedsDiscoveryRuntime = (workspaceState?.openIds ?? []).some((id) => workspaceContainerKind(id, workspaceState) === "watchlist");
  const scannerTechnicalWindows = useMemo(() => {
    const values = new Set<string>();
    for (const instanceId of (workspaceState?.openIds ?? [])) {
      const kind = workspaceContainerKind(instanceId, workspaceState);
      if (!["scanner", "watchlist"].includes(kind)) continue;
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
  const chartCutoffMs = useMemo(
    () => replayRun
      ? Date.parse(replayRun.current_time)
      : dateInTimeZone(previewContext.sessionDate, previewContext.previewTime, "America/New_York").getTime(),
    [previewContext, replayRun?.current_time],
  );
  const scannerCutoffMs = replayRun ? Math.floor(chartCutoffMs / 15_000) * 15_000 : chartCutoffMs;
  const historicalScanner = useCanvasScannerSnapshot({
    cutoffMs: scannerCutoffMs,
    enabled: Boolean(scannerContainerKey) && contextReady && replayRuntimeReady && !liveMode,
    materializeDiscovery: scannerNeedsDiscoveryRuntime,
    technicalWindows: scannerTechnicalWindows,
  });
  const liveScanner = useCanvasLiveScannerSnapshot(Boolean(scannerContainerKey) && contextReady && liveMode);
  const { error: scannerError, loading: scannerLoading, snapshot: scannerSnapshot } = liveMode ? liveScanner : historicalScanner;
  const previewClocks = useMemo(() => previewClockReadings(previewContext, liveMode ? new Date(liveClockInstant) : undefined), [liveClockInstant, liveMode, previewContext]);
  const clockIcons = [Clock3, MapPin, Globe2];
  const marketStatus = useMemo(() => historicalMarketStatus(previewContext.sessionDate, previewContext.previewTime), [previewContext]);
  const livePerformance = useLivePerformanceState(!readOnly && !replayRun, liveMode ? resolvedAccountKeys : undefined);
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
    if (runtimeBase) return;
    let cancelled = false;
    const personalProfile = snapshotCanvasProfile();
    api<EditableCanvasProfile>("/api/trading/canvas-profile", { timeoutMs: 20_000 })
      .then((payload) => {
        if (cancelled) return;
        editableProfileRevisionRef.current = payload.revision;
        if (payload.available && payload.profile) {
          const restored = mergeSharedCanvasProfile(payload.profile, personalProfile);
          const sharedMainState = restored.workspaceStates?.[MAIN_CANVAS_ID] ?? restored.defaultState;
          if (sharedMainState) writeCanvasWorkspaceState(MAIN_CANVAS_ID, sharedMainState);
          writeCanvasRegistry(restored);
          setRegistry(restored);
          setWorkspaceState(runtimeCanvasState(restored, "", canvasId, requestedInstanceId, false));
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(`Shared Canvas default is unavailable: ${reason instanceof Error ? reason.message : String(reason)}`);
      })
      .finally(() => { if (!cancelled) setEditableProfileReady(true); });
    return () => { cancelled = true; };
  }, [canvasId, requestedInstanceId, runtimeBase]);

  useEffect(() => {
    // A Replay/Backtest focus window is transient in layout ownership only.
    // Its chart settings are a user overlay and must survive reloads so an
    // enabled indicator does not appear to vanish while the run is reviewed.
    if (transient && !replayRun) return;
    if (runtimeBase && runtimeRegistryStorageKey) {
      window.localStorage.setItem(runtimeRegistryStorageKey, JSON.stringify(registry));
      return;
    }
    writeCanvasRegistry(registry);
  }, [registry, replayRun, runtimeBase, runtimeRegistryStorageKey, transient]);

  useEffect(() => {
    if (!runtimeBase || runtimeRebase || !workspaceState || transient) return;
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
  }, [canvasId, registry, runtimeBase, runtimeRebase, runtimeRevision, runtimeScope, transient, workspaceState]);

  useEffect(() => {
    if (replayRun || liveMode) return;
    window.localStorage.setItem(CANVAS_PREVIEW_CONTEXT_STORAGE_KEY, JSON.stringify(previewContext));
  }, [liveMode, previewContext, replayRun]);

  useEffect(() => {
    if (!liveMode) return;
    setPreviewContext(currentLivePreviewContext());
  }, [liveMode, livePreviewRefreshMs]);

  useEffect(() => {
    if (!replayRun) return;
    const next = replayPreviewContext(replayRun);
    setPreviewContext((current) => current.previewTime === next.previewTime && current.sessionDate === next.sessionDate ? current : next);
    setContextReady(true);
    setContextError(replayRun.error || "");
  }, [replayRun?.current_time, replayRun?.error, replayRun?.session_date]);

  useEffect(() => {
    const ticker = String(replayRun?.navigation_action?.ticker || "").trim().toUpperCase();
    if (!ticker) return;
    setRegistry((current) => {
      const group = current.linkAssignments[primaryChartId] ?? "none";
      if (group !== "none") {
        if (current.linkContexts[group].symbol === ticker) return current;
        return {
          ...current,
          linkContexts: {
            ...current.linkContexts,
            [group]: { ...current.linkContexts[group], symbol: ticker },
          },
        };
      }
      const currentSettings = instanceSettings(current, primaryChartId);
      if (currentSettings.chart.symbol === ticker) return current;
      return {
        ...current,
        instanceSettings: {
          ...current.instanceSettings,
          [primaryChartId]: normalizeSettings({
            ...currentSettings,
            chart: { ...currentSettings.chart, symbol: ticker },
          }),
        },
      };
    });
  }, [replayRun?.navigation_action?.sequence]);

  useEffect(() => {
    if (replayRun?.execution_mode !== "strategy" || transient) return;
    setRegistry((current) => strategyReplayRegistry(current, replayRun));
  }, [
    replayRun?.run_id,
    replayRun?.strategy_debug_sources?.signal_stream_ids?.join("|"),
    replayRun?.strategy_debug_sources?.watchlist_ids?.join("|"),
    transient,
  ]);

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
      if (transient) return undefined;
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
  }, [runtimeBase, runtimeRegistryStorageKey, transient]);

  useEffect(() => {
    if (replayRun || liveMode) return;
    let cancelled = false;
    let retryAttempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const loadContext = () => {
      apiCached<CanvasContext>("/api/trading/canvas-context", { timeoutMs: 20_000, ttlMs: 300_000 })
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
        .catch((reason: ApiError) => {
          if (cancelled) return;
          setContextError("Historical coverage is temporarily unavailable; reconnecting automatically.");
          setLoading(false);
          if (reason.retryable === false) return;
          const delays = [1_000, 2_000, 5_000, 10_000, 15_000];
          const delay = delays[Math.min(retryAttempt, delays.length - 1)];
          retryAttempt += 1;
          retryTimer = window.setTimeout(loadContext, delay);
        })
        .finally(() => { if (!cancelled) setContextReady(true); });
    };
    loadContext();
    return () => { cancelled = true; if (retryTimer) window.clearTimeout(retryTimer); };
  }, [liveMode, replayRun]);

  useEffect(() => {
    if (!contextReady) return;
    if (replayRun && !replayRuntimeReady) {
      setPreview(null);
      setLoading(true);
      setError("");
      return;
    }
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
            include_domains: [],
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
  }, [accountSignature, activeSymbol, contextError, contextReady, liveMode, previewContainerKey, previewContext.previewTime, previewContext.sessionDate, replayRun?.current_time, replayRun?.run_id, replayRun?.status, replayRuntimeReady, runtimeMode]);

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
      detail: newsContainer && liveMode
        ? `${definition.title} follows the live News gateway and retains the shared clock for point-in-time context.`
        : `${definition.title} rendered at the shared configuration clock.`,
      freshness: previewContext.previewTime,
      sourceLabel: sourceError
        ? "Unavailable"
        : newsContainer && liveMode
          ? "News Live"
          : definition.id === "scanner"
            ? "QMD History"
            : newsContainer || secContainer || definition.id === "xbrl"
              ? "Point-in-time"
              : "IBKR preview",
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
        const currentCanvasIds = workspaceState?.openIds ?? [];
        const nextOwner = currentCanvasIds.find((candidateId) => candidateId !== instanceId && linkAssignments[candidateId] === previousGroup)
          ?? Object.keys(linkAssignments).find((candidateId) => candidateId !== instanceId && linkAssignments[candidateId] === previousGroup);
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

  function openReusableGroup(templateId: string, tickerValue = activeSymbol) {
    if (templateId !== CHARTS_QUOTES_GROUP_TEMPLATE_ID) return;
    const result = openTickerChartsQuotes(tickerValue, {
      registry,
      replayRunId: replayRun?.run_id,
      historicalRunMode: replayRun?.mode,
      runtimeMode: focusRuntimeMode,
      workspaceState,
    });
    if (result === "popup-blocked") {
      setError("The Charts & Quotes group could not open because the browser blocked the focus window.");
    }
  }

  function openChartsQuotesForTicker(tickerValue: string) {
    openReusableGroup(CHARTS_QUOTES_GROUP_TEMPLATE_ID, tickerValue);
  }

  function openTickerWorkspace(tickerValue: string) {
    openChartsQuotesForTicker(tickerValue);
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
    window.open(focusCanvasUrl(created.canvas.id, instanceId, runtimeBase ? "approved" : "draft", focusRuntimeMode), "_blank", "noopener,noreferrer");
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
    window.open(focusCanvasUrl(created.canvas.id, undefined, runtimeBase ? "approved" : "draft", focusRuntimeMode), "_blank", "noopener,noreferrer");
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
    window.open(focusCanvasUrl(targetCanvasId, undefined, "approved", focusRuntimeMode), "_blank", "noopener,noreferrer");
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
      focusCanvasUrl(created.canvas.id, undefined, "approved", focusRuntimeMode),
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

  async function saveDefaultLayout() {
    if (!workspaceState || !editableProfileReady) return;
    const defaultState = snapshotCanvasWorkspaceState(workspaceState);
    const nextRegistry = { ...registry, defaultState };
    setRegistry(nextRegistry);
    setDefaultSaved(false);
    setError("");
    try {
      const sharedProfile = snapshotSharedCanvasProfile(nextRegistry);
      const saved = await api<EditableCanvasProfile>("/api/trading/canvas-profile", {
        body: JSON.stringify({ expected_revision: editableProfileRevisionRef.current, profile: sharedProfile }),
        method: "PUT",
        timeoutMs: 20_000,
      });
      editableProfileRevisionRef.current = saved.revision;
      setDefaultSaved(true);
    } catch (reason) {
      const status = typeof reason === "object" && reason && "status" in reason ? Number((reason as { status?: number }).status) : 0;
      if (status === 409) {
        const latest = await api<EditableCanvasProfile>("/api/trading/canvas-profile", { timeoutMs: 20_000 }).catch(() => null);
        if (latest) editableProfileRevisionRef.current = latest.revision;
        setError("The shared Canvas default changed in another session. Review this workspace, then save the shared default again.");
        return;
      }
      setError(`Shared Canvas default was not saved: ${reason instanceof Error ? reason.message : String(reason)}`);
    }
  }

  function removeCanvas(canvasToRemove: string) {
    setRegistry((current) => removeCanvasRecord(current, canvasToRemove));
  }

  function renameCanvas(canvasToRename: string, label: string) {
    const nextLabel = label.trim();
    if (!nextLabel) return;
    updateRegistry((current) => ({
      ...current,
      canvases: current.canvases.map((canvas) => canvas.id === canvasToRename ? { ...canvas, label: nextLabel } : canvas),
    }));
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
        <div className="canvas-mode-context-slot">{modeControls}{readOnly ? null : <CanvasPerformanceStrip state={performanceState} />}</div>
        {managementEnabled ? <div className="canvas-toolbar-actions">{manager ? <button className="button secondary compact canvas-set-default" disabled={!workspaceState || !editableProfileReady} onClick={() => void saveDefaultLayout()} title="Save this composition as the shared draft default. Publish it from Trading Configuration before Live or Paper can use it." type="button"><Save size={13} /> {defaultSaved ? "Shared default saved" : "Save shared default"}</button> : null}<button aria-expanded={managementOpen} aria-label="Canvas management" className="button secondary compact canvas-management-toggle" onClick={() => setManagementOpen((open) => !open)} type="button"><PanelRightOpen size={13} /> Manage</button></div> : null}
      </header>

      {(contextError && replayRun) || error ? <div className="canvas-status-stack">
        {contextError && replayRun ? <div aria-live="assertive" className="canvas-inline-error replay-runtime-error"><TriangleAlert aria-hidden="true" size={15} /><div><strong>{runtimeMode === "backtest_debug" ? "Backtest Debug" : runtimeMode === "backtest" ? "Backtest" : "Replay"} stopped</strong><span>{contextError}</span></div></div> : null}
        {error ? <div className="canvas-inline-error">{error}</div> : null}
      </div> : null}

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
        definitionsOverride={workspaceDefinitions}
        excludedContainerIds={readOnly ? [...READ_ONLY_BLOCKED_CONTAINERS] : undefined}
        historicalSourceReady={!error}
        initialStateOverride={manager ? null : initialCanvasState}
        layoutPreset={managementEnabled ? "global" : "focus"}
        groupTemplates={CANVAS_GROUP_TEMPLATES}
        managementContent={manager
          ? <CanvasManager currentCanvasId={canvasId} registry={registry} onCreate={() => openNewCanvas()} onOpen={(id) => window.open(focusCanvasUrl(id, undefined, "draft"), "_blank", "noopener,noreferrer")} onRemove={removeCanvas} onRename={renameCanvas} />
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
        onOpenGroupTemplate={(templateId) => openReusableGroup(templateId)}
        onPopOutContainer={replayRun && runtimeMode === "replay" ? openReplayContainerCanvas : runtimeBase ? undefined : openNewCanvas}
        onPopOutGroup={replayRun && runtimeMode === "replay" ? openReplayGroupCanvas : runtimeBase ? undefined : openGroupCanvas}
        onStateChange={setWorkspaceState}
        persistState={!transient}
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
            readOnly={readOnly}
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
            signalStreamLive={!replayRun}
            signalStreamRunId={replayRun?.run_id}
            strategyActivityFocusSequence={replayRun?.navigation_action?.sequence}
            replayWatchlistRuntime={replayRun?.watchlist_runtime}
            runtimeMode={runtimeMode}
            onTickerWorkspaceOpen={openTickerWorkspace}
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

function CanvasManager(props: import("../features/canvas/CanvasManagementPanel").CanvasManagementPanelProps) {
  return <Suspense fallback={<section aria-label="Canvas manager" className="canvas-manager-strip"><LoadingState label="Loading workspace settings" /></section>}><LazyCanvasManagementPanel {...props} /></Suspense>;
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

function ContainerPreview({ canvasId, chartCutoffMs, definition, instanceId, linkContext, linkGroup, linkedContainers, linkOpen, liveMode, loading, onLinkChange, onLinkContextChange, onTickerWorkspaceOpen, preview, previewContext, readOnly, replayWatchlistRuntime, requestedNewsId, requestedSecAccession, requestedSecCik, runtimeMode, scannerError, scannerLoading, scannerSnapshot, settings, settingsOpen, signalStreamLive, signalStreamRunId, strategyActivityFocusSequence, symbolEditable, updateSettings }: {
  canvasId: string;
  chartCutoffMs: number;
  definition: WorkspaceContainerDefinition;
  instanceId: string;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  linkedContainers: LinkedContainerState[];
  linkOpen: boolean;
  liveMode: boolean;
  readOnly: boolean;
  replayWatchlistRuntime?: WatchlistRuntimeResponse;
  loading: boolean;
  onLinkChange: (group: CanvasLinkGroupId) => void;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  onTickerWorkspaceOpen: (ticker: string) => void;
  preview: CanvasPreview | null;
  scannerError: string;
  scannerLoading: boolean;
  scannerSnapshot: CanvasScannerSnapshot | null;
  signalStreamLive: boolean;
  signalStreamRunId?: string;
  strategyActivityFocusSequence?: number;
  previewContext: CanvasPreviewContext;
  requestedNewsId?: string;
  requestedSecAccession?: string;
  requestedSecCik?: string;
  runtimeMode: CanvasRuntimeMode;
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
        ? <ChartContainerPreview canvasId={canvasId} cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} linkGroup={linkGroup} liveMode={liveMode} onLinkContextChange={onLinkContextChange} previewContext={previewContext} readOnly={readOnly} runtimeMode={runtimeMode} settings={settings} strategy={preview?.strategy} symbolEditable={symbolEditable} trading={preview?.trading} updateSettings={updateSettings} />
      : definition.id === "charts_quotes"
        ? <ChartsQuotesContainerPreview canvasId={canvasId} cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} liveMode={liveMode} onLinkContextChange={onLinkContextChange} previewContext={previewContext} readOnly={readOnly} runtimeMode={runtimeMode} settings={settings} strategy={preview?.strategy} symbolEditable={symbolEditable} trading={preview?.trading} updateSettings={updateSettings} />
      : definition.id === "microstructure"
        ? <QuotesTapeContainer end={liveMode ? undefined : new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.microstructure} start={liveMode ? undefined : dateInTimeZone(previewContext.sessionDate, "04:00", "America/New_York").toISOString()} symbol={linkContext.symbol} />
      : definition.id === "facts"
        ? <StockFactsContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} symbol={linkContext.symbol} />
      : definition.id === "news"
        ? <AllNewsContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, news: { ...state.news, ...patch } }))} settings={settings.news} />
      : definition.id === "ticker_news"
        ? <TickerNewsContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.ticker_news} symbol={linkContext.symbol} />
      : definition.id === "news_detail"
        ? <NewsDetailContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} live={liveMode} requestedNewsId={requestedNewsId} />
      : definition.id === "sec"
        ? <AllSecContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, sec: { ...state.sec, ...patch } }))} settings={settings.sec} />
      : definition.id === "ticker_sec"
        ? <TickerSecContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.ticker_sec} symbol={linkContext.symbol} />
      : definition.id === "sec_detail"
        ? <SecDetailContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} requestedAccession={requestedSecAccession} requestedCik={requestedSecCik} />
      : definition.id === "xbrl"
        ? <XbrlAnalysisContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.xbrl} symbol={linkContext.symbol} />
      : definition.id === "scanner"
        ? (scannerLoading || scannerSnapshot?.meta.status === "building") && !scannerSnapshot?.rows.length
          ? <LoadingState fill label="Loading scanner" />
          : scannerError && !scannerSnapshot
            ? <div className="canvas-inline-error">{liveMode ? "Live" : "Historical"} scanner unavailable: {scannerError}</div>
            : <MarketScannerContainer asOf={scannerSnapshot?.as_of ?? new Date(chartCutoffMs).toISOString()} live={liveMode} meta={scannerSnapshot?.meta ?? preview?.scanner_meta} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, scanner: { ...state.scanner, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} rows={scannerSnapshot?.rows ?? preview?.scanner ?? []} settings={settings.scanner} />
      : definition.id === "signal_stream"
        ? <SignalStreamContainer asOf={new Date(chartCutoffMs).toISOString()} live={signalStreamLive} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, signal_stream: { ...state.signal_stream, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} runId={signalStreamRunId} scannerRows={scannerSnapshot?.rows ?? preview?.scanner ?? []} settings={settings.signal_stream} />
      : definition.id === "watchlist"
        ? (scannerLoading || scannerSnapshot?.meta.status === "building") && !scannerSnapshot?.rows.length
          ? <LoadingState fill label="Loading watchlist" />
          : scannerError && !scannerSnapshot
            ? <div className="canvas-inline-error">{liveMode ? "Live" : "Historical"} watchlist unavailable: {scannerError}</div>
            : <WatchUniverseContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSettingsChange={(change) => updateSettings((state) => ({ ...state, watchlist: { ...state.watchlist, ...(typeof change === "function" ? change(state.watchlist) : change) } }))} onTickerSelect={onTickerWorkspaceOpen} runtime={replayWatchlistRuntime ?? scannerSnapshot?.watchlist_runtime ?? null} scannerRows={scannerSnapshot?.rows ?? preview?.scanner ?? []} settings={settings.watchlist} />
      : definition.id === "strategy_activity"
        ? <StrategyActivityContainer asOf={new Date(chartCutoffMs).toISOString()} focusSequence={strategyActivityFocusSequence} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, strategy_activity: { ...state.strategy_activity, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} runId={signalStreamRunId} settings={settings.strategy_activity} />
      : loading && !preview
        ? <LoadingState fill label={`Loading ${definition.title.toLowerCase()}`} />
        : renderPreview(definition.id, preview, settings, linkGroup, onLinkContextChange, onTickerWorkspaceOpen)}</div>
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

function renderPreview(id: WorkspaceContainerId, preview: CanvasPreview | null, settings: ContainerSettings, linkGroup: CanvasLinkGroupId, onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void, onTickerSelect: (ticker: string) => void) {
  return <TradingContainerPreview id={id} linkGroup={linkGroup} onLinkContextChange={onLinkContextChange} onTickerSelect={onTickerSelect} preview={preview} settings={settings} />;
}

type ChartContainerPreviewProps = {
  canvasId: string;
  cutoffMs: number;
  instanceId: string;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  liveMode: boolean;
  readOnly: boolean;
  runtimeMode: CanvasRuntimeMode;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  previewContext: CanvasPreviewContext;
  settings: ContainerSettings;
  strategy?: CanvasPreview["strategy"];
  symbolEditable: boolean;
  trading?: CanonicalTradingPreview;
  updateSettings: SettingsUpdater;
};

const ChartContainerPreview = memo(function ChartContainerPreview({ canvasId, cutoffMs, instanceId, linkContext, liveMode, onLinkContextChange, previewContext, runtimeMode, settings, strategy, symbolEditable, trading, updateSettings }: ChartContainerPreviewProps) {
  const historicalMode = runtimeMode === "backtest_debug" ? "debug" : runtimeMode === "backtest" ? "backtest" : "replay";
  const fullSession = runtimeMode === "backtest" || runtimeMode === "backtest_debug";
  const liveChart = useCanvasHistoricalChart(linkContext.symbol, settings.chart.timeframe, cutoffMs, previewContext.sessionDate, settings.chart.visibleIndicators, liveMode, true, historicalMode, fullSession);
  const presentations = useTickerPresentations([linkContext.symbol]);
  const strategyDecisions = useMemo(() => strategyDecisionEvents(strategy), [strategy]);
  const strategyPresentation = useMemo(() => resolvedStrategyPresentation(strategy), [strategy]);
  return <ChartPreview canvasId={canvasId} changeAsOf={new Date(cutoffMs).toISOString()} chartSettings={settings.chart} fillHeight fullSessionReview={fullSession} instanceId={instanceId} linkContext={linkContext} liveChart={liveChart} logoUrl={presentations[linkContext.symbol]?.logo_url} onChartSettingsChange={(next) => updateSettings((current) => ({ ...current, chart: next }))} onLinkContextChange={onLinkContextChange} strategyDecisions={strategyDecisions} strategyPresentation={strategyPresentation} symbolEditable={symbolEditable} trading={trading} />;
}, chartContainerPreviewPropsEqual);

function ChartsQuotesContainerPreview({ canvasId, cutoffMs, instanceId, linkContext, liveMode, onLinkContextChange, previewContext, readOnly, runtimeMode, settings, strategy, symbolEditable, trading, updateSettings }: Omit<ChartContainerPreviewProps, "linkGroup">) {
  const historicalMode = runtimeMode === "backtest_debug" ? "debug" : runtimeMode === "backtest" ? "backtest" : "replay";
  const fullSession = runtimeMode === "backtest" || runtimeMode === "backtest_debug";
  const main = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.main.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.main.visibleIndicators, liveMode, true, historicalMode, fullSession);
  const month = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.month.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.month.visibleIndicators, liveMode, true, historicalMode, false);
  const daily = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.daily.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.daily.visibleIndicators, liveMode, true, historicalMode, false);
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
    dailyChart={<ChartPreview {...chartProps} appearanceDefaults={CHARTS_QUOTES_CONTEXT_APPEARANCE_DEFAULTS} baseHeight={255} canvasId={canvasId} chartSettings={settings.charts_quotes.daily} fillHeight instanceId={`${instanceId}.daily`} liveChart={daily} onChartSettingsChange={(next) => updateSlot("daily", { ...next, timeframe: "1d" })} showTradeAnnotations={false} timeframes={["1d"]} />}
    end={liveMode ? undefined : changeAsOf}
    layout={settings.charts_quotes.layout}
    mainChart={<ChartPreview {...chartProps} baseHeight={460} canvasId={canvasId} chartSettings={settings.charts_quotes.main} fillHeight fullSessionReview={fullSession} instanceId={`${instanceId}.main`} liveChart={main} onChartSettingsChange={(next) => updateSlot("main", next)} timeframes={HISTORICAL_TIMEFRAMES} />}
    monthChart={<ChartPreview {...chartProps} appearanceDefaults={CHARTS_QUOTES_CONTEXT_APPEARANCE_DEFAULTS} baseHeight={255} canvasId={canvasId} chartSettings={settings.charts_quotes.month} fillHeight instanceId={`${instanceId}.month`} liveChart={month} onChartSettingsChange={(next) => updateSlot("month", { ...next, timeframe: "1mo" })} showTradeAnnotations={false} timeframes={["1mo"]} />}
    onLayoutChange={(layout) => updateSettings((current) => ({ ...current, charts_quotes: { ...current.charts_quotes, layout } }))}
    onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined}
    start={liveMode ? undefined : dateInTimeZone(previewContext.sessionDate, "04:00", "America/New_York").toISOString()}
    symbol={linkContext.symbol}
    reservedPanel={readOnly ? undefined : <StrategyOrderEntry marketSnapshot={proposalMarketSnapshot} runtimeMode={liveMode ? String(trading?.mode || "") : undefined} strategy={strategy} symbol={linkContext.symbol} trading={trading} />}
  />;
}

function chartContainerPreviewPropsEqual(previous: ChartContainerPreviewProps, next: ChartContainerPreviewProps) {
  const previousChart = previous.settings.chart;
  const nextChart = next.settings.chart;
  return previous.canvasId === next.canvasId
    && previous.instanceId === next.instanceId
    && previous.cutoffMs === next.cutoffMs
    && previous.liveMode === next.liveMode
    && previous.readOnly === next.readOnly
    && previous.runtimeMode === next.runtimeMode
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
  const normalizedSymbol = symbol.toUpperCase();
  const position = trading?.positions.find((row) => String(nestedValue(row, "instrument", "symbol")).toUpperCase() === normalizedSymbol);
  const lifecycles = (trading?.position_lifecycles ?? [])
    .filter((row) => String(nestedValue(row, "instrument", "symbol")).toUpperCase() === normalizedSymbol)
    .map((row) => [row.lifecycle_id, row.status, row.quantity, row.current_quantity, row.entry_price, row.exit_price, row.opened_at, row.closed_at, row.net_pnl, ...(Array.isArray(row.execution_ids) ? row.execution_ids : [])].join(":"));
  const executions = (trading?.executions ?? [])
    .filter((row) => String(nestedValue(row, "instrument", "symbol")).toUpperCase() === normalizedSymbol)
    .map((row) => [row.execution_id, row.quantity, row.price, row.side, row.source_event_time].join(":"));
  return JSON.stringify({
    executions,
    lifecycles,
    position: position ? [position.account_id, position.quantity, position.average_price, position.market_price, position.unrealized_pnl, position.source_event_time] : null,
  });
}

function strategyDecisionEvents(strategy: CanvasPreview["strategy"] | undefined): StrategyDecisionEvent[] {
  if (!strategy || strategy.fixture) return [];
  return [...strategy.signals, ...(strategy.decisions ?? [])].flatMap((row, index) => {
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
  if (id === "signal_stream") return <><NumberField label="Maximum events" max={5000} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">Configured Market Discovery occurrences are append-only and preserve their trigger-time values. Strategy Activity remains a separate durable runtime surface.</div></>;
  if (id === "watchlist") return <><NumberField label="Maximum rows" max={500} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">QMD Market Discovery owns Watchlist membership and its causal history. Canvas only chooses which published Watchlist to present.</div></>;
  if (id === "strategy_activity") return <><NumberField label="Maximum events" max={50000} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">Filters remain local to this container. Events come from the durable Trading Journal and are never reconstructed in the browser.</div></>;
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
function previewClockReadings(context: CanvasPreviewContext, liveInstant?: Date) {
  const instant = liveInstant ?? dateInTimeZone(context.sessionDate, context.previewTime, "America/New_York");
  const format = (timeZone: string | undefined, includeDate: boolean) => {
    const detail = includeDate ? new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "short", timeZone, year: "numeric" }).format(instant) : "";
    const value = new Intl.DateTimeFormat("en-US", { hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone }).format(instant);
    return { detail, value };
  };
  const localTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  return [
    { label: "ET", ...format("America/New_York", true) },
    { label: "Local", ...format(localTimeZone, true) },
    { label: "UTC", ...format("UTC", true) },
  ];
}
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
const STRATEGY_REPLAY_CONTAINER_IDS: WorkspaceContainerId[] = ["strategy_activity", "signal_stream", "watchlist", "orders", "fills", "positions", "closed_trades", "portfolio"];
function strategyReplayLayouts(openIds: string[]): Record<string, WorkspaceWindowLayout> {
  const required: Record<string, WorkspaceWindowLayout> = {
    strategy_activity: { fullscreen: false, h: 440, minimized: false, w: 900, x: 0, y: 0, z: 8 },
    signal_stream: { fullscreen: false, h: 440, minimized: false, w: 840, x: 912, y: 0, z: 7 },
    watchlist: { fullscreen: false, h: 420, minimized: false, w: 1752, x: 0, y: 452, z: 6 },
    orders: { fullscreen: false, h: 430, minimized: false, w: 870, x: 0, y: 884, z: 5 },
    fills: { fullscreen: false, h: 430, minimized: false, w: 870, x: 882, y: 884, z: 4 },
    positions: { fullscreen: false, h: 430, minimized: false, w: 870, x: 0, y: 1326, z: 3 },
    closed_trades: { fullscreen: false, h: 430, minimized: false, w: 870, x: 882, y: 1326, z: 3 },
    portfolio: { fullscreen: false, h: 370, minimized: false, w: 1752, x: 0, y: 1768, z: 2 },
  };
  const extras = openIds.filter((id) => !(id in required));
  const fallback = createFocusLayouts(extras);
  return Object.fromEntries(openIds.map((id, index) => [id, required[id] ?? { ...fallback[id], y: 2160 + index * 24 }]));
}
function strategyReplayCanvasState(state: CanvasWorkspaceState | null): CanvasWorkspaceState {
  if (!state) {
    return {
      groups: {},
      instances: Object.fromEntries(STRATEGY_REPLAY_CONTAINER_IDS.map((id) => [id, id])) as Record<string, WorkspaceContainerId>,
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts: strategyReplayLayouts(STRATEGY_REPLAY_CONTAINER_IDS),
      openIds: [...STRATEGY_REPLAY_CONTAINER_IDS],
    };
  }
  const allowed = new Set<WorkspaceContainerId>(STRATEGY_REPLAY_CONTAINER_IDS);
  const openIds = state.openIds.filter((id) => allowed.has(workspaceContainerKind(id, state)));
  const open = new Set(openIds);
  return {
    groups: normalizeWorkspaceGroups(state.groups, openIds),
    instances: Object.fromEntries(Object.entries(state.instances).filter(([id]) => open.has(id))) as Record<string, WorkspaceContainerId>,
    layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
    layouts: Object.fromEntries(Object.entries(state.layouts).filter(([id]) => open.has(id))),
    openIds,
  };
}
function strategyReplayRegistry(registry: CanvasRegistry, run: CanvasReplayRun): CanvasRegistry {
  const signalStreamIds = run.strategy_debug_sources?.signal_stream_ids ?? [];
  const watchlistIds = run.strategy_debug_sources?.watchlist_ids ?? [];
  const signalSettings = instanceSettings(registry, "signal_stream");
  const watchlistSettings = instanceSettings(registry, "watchlist");
  const activitySettings = instanceSettings(registry, "strategy_activity");
  return {
    ...registry,
    instanceSettings: {
      ...registry.instanceSettings,
      signal_stream: {
        ...signalSettings,
        signal_stream: {
          ...signalSettings.signal_stream,
          signalStreamId: signalStreamIds[0] ?? "",
          signalStreamIds,
        },
      },
      watchlist: {
        ...watchlistSettings,
        watchlist: {
          ...watchlistSettings.watchlist,
          watchlistId: watchlistIds[0] ?? "",
          watchlistIds,
        },
      },
      strategy_activity: {
        ...activitySettings,
        strategy_activity: {
          ...activitySettings.strategy_activity,
          limit: 50_000,
          runId: run.run_id,
        },
      },
    },
  };
}
function normalizeInheritedLayouts(layouts: Record<string, WorkspaceWindowLayout>, ids: string[]) {
  const fallback = createFocusLayouts(ids);
  return Object.fromEntries(ids.map((id) => [id, { ...(layouts[id] ?? fallback[id]), fullscreen: false, minimized: false }]));
}
function focusLayout(source?: WorkspaceWindowLayout): WorkspaceWindowLayout { const scale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale")) || 1; return { fullscreen: true, h: Math.max(320, Math.floor(window.innerHeight / scale) - 62), minimized: false, w: Math.max(680, Math.floor(window.innerWidth / scale)), x: 0, y: 0, z: Math.max(1, source?.z ?? 1) }; }
function offsetLayout(source: WorkspaceWindowLayout, index: number): WorkspaceWindowLayout { const offset = (index % 6) * 18; return { ...source, fullscreen: false, minimized: false, x: offset, y: offset, z: index + 1 }; }

import { Activity, ArrowDown, ArrowUp, ArrowUpDown, BadgeDollarSign, BarChart3, BookOpen, BriefcaseBusiness, Check, ChevronDown, ChevronRight, CircleDollarSign, Clock3, ExternalLink, Filter, Gauge, HelpCircle, Landmark, Link2, MapPin, PanelRightOpen, Plus, RefreshCcw, Search, Save, Settings2, ShieldCheck, Target, Trash2, TriangleAlert, Unlink, WalletCards, X } from "lucide-react";
import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MutableRefObject, type ReactNode } from "react";

import { api, apiCached, query, type ApiError } from "../api/client";
import {
  CANVAS_PREVIEW_CONTEXT_STORAGE_KEY,
  CANVAS_REGISTRY_STORAGE_KEY,
  CANVAS_REGISTRY_UPDATED_EVENT,
  CANVAS_WORKSPACE_UPDATED_EVENT,
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
  hydrateCanvasProfile,
  mergeCanvasProfiles,
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
  snapshotCanvasProfile,
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
import { AllNewsContainer, NEWS_ARTICLE_CLASS_OPTIONS, NewsDetailContainer, TickerNewsContainer } from "../app/components/NewsContainers";
import { AllSecContainer, SecDetailContainer, TickerSecContainer } from "../app/components/SecContainers";
import { MarketTime } from "../app/components/MarketTime";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";
import { ChartsQuotesMarketLayout, QuotesTapeContainer, type ChartsQuotesLayoutSettings } from "../app/components/MarketMicrostructureContainers";
import { MarketScannerContainer, SCANNER_TIMEFRAMES, SignalStreamContainer, StrategyActivityContainer, WatchUniverseContainer, type StrategyActivitySettings } from "../app/components/MarketScreenerContainers";
import { StockFactsContainer } from "../app/components/StockFactsContainer";
import { XbrlAnalysisContainer, type XbrlAnalysisSettings } from "../app/components/XbrlAnalysisContainer";
import { TickerIdentity, TickerIdentityWithChange, useTickerPresentations } from "../app/components/TickerIdentity";
import { PresentedValue, SecurityIdentityCell, tableCellClass } from "../app/components/TablePresentation";
import { TRADING_WORKSPACE_LAYOUT_VERSION, TradingWorkspace, createFocusLayouts } from "../app/components/TradingWorkspace";
import type { WorkspaceWindowLayout, WorkspaceWindowMeta, WorkspaceWindowStatus } from "../app/components/WorkspaceCanvas";
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
  PnlCandle,
  PnlCandleTimeframe,
  PreviewRow,
} from "../features/canvas/contracts";
import {
  ALL_CONTAINER_IDS,
  HISTORICAL_TIMEFRAMES,
  MANAGER_DEFAULT_CONTAINER_IDS,
  READ_ONLY_BLOCKED_CONTAINERS,
} from "../features/canvas/configuration";
import { marketSessionDate, useCanvasHistoricalChart } from "../features/canvas/chartData";
import { finiteNumber } from "../features/canvas/numbers";
import { formatQuantity, money, nestedValue } from "../features/canvas/presentationFormat";
import { cloneDefaultSettings, instanceSettings, normalizeSettings } from "../features/canvas/settings";
import { useCanvasLiveScannerSnapshot, useCanvasScannerSnapshot } from "../features/canvas/scannerData";
import { dateInTimeZone } from "../features/canvas/time";

type CanvasChartPreviewProps = Parameters<typeof import("../features/canvas/chartPresentation").ChartPreview>[0];
const LazyCanvasChartPreview = lazy(() => import("../features/canvas/chartPresentation").then((module) => ({ default: module.ChartPreview })));

function ChartPreview(props: CanvasChartPreviewProps) {
  return <Suspense fallback={<div aria-live="polite" className="canvas-preview-loading" role="status">Loading chart renderer…</div>}>
    <LazyCanvasChartPreview {...props} />
  </Suspense>;
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
  if (!approved) return <div className="canvas-config-page canvas-focus-page"><div aria-live="polite" className="canvas-empty-state is-loading" role="status"><span className="loading-spinner" aria-hidden="true" /><span><strong>Loading approved Canvas</strong><small>Resolving the published default for this {mode} workspace.</small></span></div></div>;
  return <CanvasWorkspaceSurface accountKeys={accountKeys} approvedCanvas={approved} canvasId={MAIN_CANVAS_ID} manager={false} modeControls={modeControls} runtimeMode={mode} />;
}

export function CanvasFocusPage() {
  const params = new URLSearchParams(window.location.search);
  const replayRunId = params.get("replay_run") || undefined;
  const replayFocusToken = params.get("replay_focus") || undefined;
  if (replayRunId && replayFocusToken) return <ReplayCanvasFocusPage focusToken={replayFocusToken} runId={replayRunId} />;
  const acceptanceKind = params.get("container_preview") as WorkspaceContainerId | null;
  const acceptanceRuntimeMode = params.get("runtime_mode");
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
  if (params.get("canvas_profile") === "draft") return <CanvasWorkspaceSurface canvasId={canvasId} manager={false} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} />;
  return <ApprovedCanvasFocusPage canvasId={canvasId} requestedInstanceId={requestedInstanceId} requestedNewsId={requestedNewsId} requestedSecAccession={requestedSecAccession} requestedSecCik={requestedSecCik} />;
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
  if (!approved) return <div className="canvas-config-page canvas-focus-page"><div aria-live="polite" className="canvas-empty-state is-loading" role="status"><span className="loading-spinner" aria-hidden="true" /><span><strong>Loading approved Canvas</strong><small>Resolving the published default and this workspace's saved overlay.</small></span></div></div>;
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

export function CanvasWorkspaceSurface({ accountKeys, approvedCanvas, canvasId, manager, modeControls, readOnly = false, replayRun, requestedInstanceId, requestedNewsId, requestedSecAccession, requestedSecCik, runtimeMode: requestedRuntimeMode, runtimeWorkspaceId }: { accountKeys?: string[]; approvedCanvas?: ApprovedCanvasProfile; canvasId: string; manager: boolean; modeControls?: ReactNode; readOnly?: boolean; replayRun?: CanvasReplayRun; requestedInstanceId?: string; requestedNewsId?: string; requestedSecAccession?: string; requestedSecCik?: string; runtimeMode?: CanvasRuntimeMode; runtimeWorkspaceId?: string }) {
  const runtimeMode: CanvasRuntimeMode = replayRun?.mode === "backtest" || replayRun?.mode === "backtest_debug" ? replayRun.mode : replayRun ? "replay" : requestedRuntimeMode ?? "canvas";
  const liveMode = runtimeMode === "live" || runtimeMode === "paper";
  const resolvedAccountKeys = readOnly ? [] : accountKeys?.length ? accountKeys : readLiveAccountKeys();
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
  const [liveClockInstant, setLiveClockInstant] = useState(() => Date.now());
  const [preview, setPreview] = useState<CanvasPreview | null>(null);
  const [contextReady, setContextReady] = useState(Boolean(replayRun || liveMode));
  const [contextError, setContextError] = useState("");
  const [workspaceState, setWorkspaceState] = useState<CanvasWorkspaceState | null>(initialCanvasState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [defaultSaved, setDefaultSaved] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);
  const [editableProfileReady, setEditableProfileReady] = useState(Boolean(runtimeBase));
  const [canvasPersistenceEpoch, setCanvasPersistenceEpoch] = useState(0);
  const editableProfileRevisionRef = useRef(0);
  const [linkPopoverContainerId, setLinkPopoverContainerId] = useState<string | null>(null);
  const [settingsContainerId, setSettingsContainerId] = useState<string | null>(null);
  const managementEnabled = manager || Boolean(runtimeBase);
  const workspaceDefinitions = useMemo(() => readOnly
    ? TRADING_WORKSPACE_CONTAINERS.filter((definition) => !READ_ONLY_BLOCKED_CONTAINERS.has(definition.id))
    : TRADING_WORKSPACE_CONTAINERS, [readOnly]);

  const currentCanvas = registry.canvases.find((canvas) => canvas.id === canvasId) ?? { id: canvasId, label: canvasId === MAIN_CANVAS_ID ? "Main" : "Focus canvas" };
  const primaryChartId = (workspaceState?.openIds ?? []).find((id) => workspaceContainerKind(id, workspaceState) === "chart") ?? "chart";
  const primarySettings = instanceSettings(registry, primaryChartId);
  const dedicatedContainers = new Set<WorkspaceContainerId>(["chart", "charts_quotes", "facts", "microstructure", "news", "ticker_news", "news_detail", "sec", "ticker_sec", "sec_detail", "xbrl", "scanner", "signal_stream", "watchlist", "strategy_activity"]);
  const previewContainerKey = (workspaceState?.openIds ?? []).filter((id) => !dedicatedContainers.has(workspaceContainerKind(id, workspaceState))).sort().join(",");
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
  const chartCutoffMs = useMemo(() => dateInTimeZone(previewContext.sessionDate, previewContext.previewTime, "America/New_York").getTime(), [previewContext]);
  const scannerCutoffMs = replayRun ? Math.floor(chartCutoffMs / 15_000) * 15_000 : chartCutoffMs;
  const historicalScanner = useCanvasScannerSnapshot({
    cutoffMs: scannerCutoffMs,
    enabled: Boolean(scannerContainerKey) && contextReady && !liveMode,
    materializeDiscovery: scannerNeedsDiscoveryRuntime,
    technicalWindows: scannerTechnicalWindows,
  });
  const liveScanner = useCanvasLiveScannerSnapshot(Boolean(scannerContainerKey) && contextReady && liveMode);
  const { error: scannerError, loading: scannerLoading, snapshot: scannerSnapshot } = liveMode ? liveScanner : historicalScanner;
  const previewClocks = useMemo(() => previewClockReadings(previewContext, liveMode ? new Date(liveClockInstant) : undefined), [liveClockInstant, liveMode, previewContext]);
  const clockIcons = [Clock3, MapPin];
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
    const localProfile = snapshotCanvasProfile();
    api<EditableCanvasProfile>("/api/trading/canvas-profile", { timeoutMs: 20_000 })
      .then(async (payload) => {
        if (cancelled) return;
        if (payload.available && payload.profile) {
          const restored = hydrateCanvasProfile(mergeCanvasProfiles(payload.profile, localProfile, canvasId));
          editableProfileRevisionRef.current = payload.revision;
          setRegistry(restored);
          setWorkspaceState(focusCanvasState(canvasId, requestedInstanceId));
          return;
        }
        const saved = await api<EditableCanvasProfile>("/api/trading/canvas-profile", {
          body: JSON.stringify({ expected_revision: payload.revision, profile: localProfile }),
          method: "PUT",
          timeoutMs: 20_000,
        });
        if (!cancelled) editableProfileRevisionRef.current = saved.revision;
      })
      .catch((reason) => {
        if (!cancelled) setError(`Canvas persistence is unavailable: ${reason instanceof Error ? reason.message : String(reason)}`);
      })
      .finally(() => { if (!cancelled) setEditableProfileReady(true); });
    return () => { cancelled = true; };
  }, [canvasId, requestedInstanceId, runtimeBase]);

  useEffect(() => {
    if (runtimeBase) return;
    const noteWorkspaceChange = () => setCanvasPersistenceEpoch((value) => value + 1);
    window.addEventListener(CANVAS_WORKSPACE_UPDATED_EVENT, noteWorkspaceChange);
    return () => window.removeEventListener(CANVAS_WORKSPACE_UPDATED_EVENT, noteWorkspaceChange);
  }, [runtimeBase]);

  useEffect(() => {
    if (runtimeBase || !editableProfileReady) return;
    const timer = window.setTimeout(async () => {
      const profile = snapshotCanvasProfile(registry);
      if (workspaceState) {
        profile.workspaceStates = {
          ...(profile.workspaceStates ?? {}),
          [canvasId]: snapshotCanvasWorkspaceState(workspaceState),
        };
      }
      const save = (expectedRevision: number, candidate = profile) => api<EditableCanvasProfile>("/api/trading/canvas-profile", {
        body: JSON.stringify({ expected_revision: expectedRevision, profile: candidate }),
        method: "PUT",
        timeoutMs: 20_000,
      });
      try {
        const saved = await save(editableProfileRevisionRef.current);
        editableProfileRevisionRef.current = saved.revision;
        setError((current) => current.startsWith("Canvas persistence is unavailable:") ? "" : current);
      } catch (reason) {
        const status = typeof reason === "object" && reason && "status" in reason ? Number((reason as { status?: number }).status) : 0;
        try {
          if (status !== 409) throw reason;
          const latest = await api<EditableCanvasProfile>("/api/trading/canvas-profile", { timeoutMs: 20_000 });
          const merged = latest.profile ? mergeCanvasProfiles(latest.profile, profile, canvasId) : profile;
          const saved = await save(latest.revision, merged);
          editableProfileRevisionRef.current = saved.revision;
        } catch (retryReason) {
          setError(`Canvas persistence is unavailable: ${retryReason instanceof Error ? retryReason.message : String(retryReason)}`);
        }
      }
    }, 400);
    return () => window.clearTimeout(timer);
  }, [canvasId, canvasPersistenceEpoch, editableProfileReady, registry, runtimeBase, workspaceState]);

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
    if (!liveMode) return undefined;
    const update = () => setLiveClockInstant(Date.now());
    update();
    const timer = window.setInterval(update, 1_000);
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
    window.open(focusCanvasUrl(created.canvas.id, instanceId, runtimeBase ? "approved" : "draft"), "_blank", "noopener,noreferrer");
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
    window.open(focusCanvasUrl(created.canvas.id, instanceId, runtimeBase ? "approved" : "draft"), "_blank", "noopener,noreferrer");
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
    window.open(focusCanvasUrl(created.canvas.id, undefined, runtimeBase ? "approved" : "draft"), "_blank", "noopener,noreferrer");
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
        <div className="canvas-mode-context-slot">{modeControls}{readOnly ? null : <CanvasPerformanceStrip state={performanceState} />}</div>
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
        definitionsOverride={workspaceDefinitions}
        excludedContainerIds={readOnly ? [...READ_ONLY_BLOCKED_CONTAINERS] : undefined}
        historicalSourceReady={!error}
        initialStateOverride={manager ? null : initialCanvasState}
        layoutPreset={managementEnabled ? "global" : "focus"}
        managementContent={manager
          ? <CanvasManager registry={registry} onCreate={() => openNewCanvas()} onOpen={(id) => window.open(focusCanvasUrl(id, undefined, "draft"), "_blank", "noopener,noreferrer")} onRemove={removeCanvas} />
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

function ContainerPreview({ canvasId, chartCutoffMs, definition, instanceId, linkContext, linkGroup, linkedContainers, linkOpen, liveMode, loading, onLinkChange, onLinkContextChange, onTickerWorkspaceOpen, preview, previewContext, readOnly, requestedNewsId, requestedSecAccession, requestedSecCik, scannerError, scannerLoading, scannerSnapshot, settings, settingsOpen, signalStreamLive, signalStreamRunId, symbolEditable, updateSettings }: {
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
        ? <ChartContainerPreview canvasId={canvasId} cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} linkGroup={linkGroup} liveMode={liveMode} onLinkContextChange={onLinkContextChange} previewContext={previewContext} readOnly={readOnly} settings={settings} strategy={preview?.strategy} symbolEditable={symbolEditable} trading={preview?.trading} updateSettings={updateSettings} />
      : definition.id === "charts_quotes"
        ? <ChartsQuotesContainerPreview canvasId={canvasId} cutoffMs={chartCutoffMs} instanceId={instanceId} linkContext={linkContext} liveMode={liveMode} onLinkContextChange={onLinkContextChange} previewContext={previewContext} readOnly={readOnly} settings={settings} strategy={preview?.strategy} symbolEditable={symbolEditable} trading={preview?.trading} updateSettings={updateSettings} />
      : definition.id === "microstructure"
        ? <QuotesTapeContainer end={liveMode ? undefined : new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.microstructure} start={liveMode ? undefined : dateInTimeZone(previewContext.sessionDate, "04:00", "America/New_York").toISOString()} symbol={linkContext.symbol} />
      : definition.id === "facts"
        ? <StockFactsContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} symbol={linkContext.symbol} />
      : definition.id === "news"
        ? <AllNewsContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, news: { ...state.news, ...patch } }))} settings={settings.news} />
      : definition.id === "ticker_news"
        ? <TickerNewsContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.ticker_news} symbol={linkContext.symbol} />
      : definition.id === "news_detail"
        ? <NewsDetailContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} requestedNewsId={requestedNewsId} />
      : definition.id === "sec"
        ? <AllSecContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, sec: { ...state.sec, ...patch } }))} settings={settings.sec} />
      : definition.id === "ticker_sec"
        ? <TickerSecContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.ticker_sec} symbol={linkContext.symbol} />
      : definition.id === "sec_detail"
        ? <SecDetailContainer asOf={new Date(chartCutoffMs).toISOString()} canvasId={canvasId} requestedAccession={requestedSecAccession} requestedCik={requestedSecCik} />
      : definition.id === "xbrl"
        ? <XbrlAnalysisContainer asOf={new Date(chartCutoffMs).toISOString()} onSymbolChange={symbolEditable ? (symbol) => onLinkContextChange({ symbol }) : undefined} settings={settings.xbrl} symbol={linkContext.symbol} />
      : definition.id === "scanner"
        ? (scannerLoading || scannerSnapshot?.meta.status === "building") && !scannerSnapshot?.rows.length
          ? <div className="canvas-preview-loading">Building the complete {liveMode ? "live" : "historical"} scanner snapshot…</div>
          : scannerError && !scannerSnapshot
            ? <div className="canvas-inline-error">{liveMode ? "Live" : "Historical"} scanner unavailable: {scannerError}</div>
            : <MarketScannerContainer asOf={scannerSnapshot?.as_of ?? new Date(chartCutoffMs).toISOString()} live={liveMode} meta={scannerSnapshot?.meta ?? preview?.scanner_meta} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, scanner: { ...state.scanner, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} rows={scannerSnapshot?.rows ?? preview?.scanner ?? []} settings={settings.scanner} />
      : definition.id === "signal_stream"
        ? <SignalStreamContainer asOf={new Date(chartCutoffMs).toISOString()} live={signalStreamLive} onSettingsChange={(patch) => updateSettings((state) => ({ ...state, signal_stream: { ...state.signal_stream, ...patch } }))} onTickerSelect={onTickerWorkspaceOpen} runId={signalStreamRunId} settings={settings.signal_stream} />
      : definition.id === "watchlist"
        ? (scannerLoading || scannerSnapshot?.meta.status === "building") && !scannerSnapshot?.rows.length
          ? <div className="canvas-preview-loading">Loading the {liveMode ? "live" : "historical"} watchlist snapshot…</div>
          : scannerError && !scannerSnapshot
            ? <div className="canvas-inline-error">{liveMode ? "Live" : "Historical"} watchlist unavailable: {scannerError}</div>
            : <WatchUniverseContainer asOf={new Date(chartCutoffMs).toISOString()} live={liveMode} onSettingsChange={(change) => updateSettings((state) => ({ ...state, watchlist: { ...state.watchlist, ...(typeof change === "function" ? change(state.watchlist) : change) } }))} onTickerSelect={onTickerWorkspaceOpen} runtime={scannerSnapshot?.watchlist_runtime ?? null} scannerRows={scannerSnapshot?.rows ?? preview?.scanner ?? []} settings={settings.watchlist} />
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
  canvasId: string;
  cutoffMs: number;
  instanceId: string;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  liveMode: boolean;
  readOnly: boolean;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  previewContext: CanvasPreviewContext;
  settings: ContainerSettings;
  strategy?: CanvasPreview["strategy"];
  symbolEditable: boolean;
  trading?: CanonicalTradingPreview;
  updateSettings: SettingsUpdater;
};

const ChartContainerPreview = memo(function ChartContainerPreview({ canvasId, cutoffMs, instanceId, linkContext, liveMode, onLinkContextChange, previewContext, settings, strategy, symbolEditable, trading, updateSettings }: ChartContainerPreviewProps) {
  const liveChart = useCanvasHistoricalChart(linkContext.symbol, settings.chart.timeframe, cutoffMs, previewContext.sessionDate, settings.chart.visibleIndicators, liveMode);
  const presentations = useTickerPresentations([linkContext.symbol]);
  const strategyDecisions = useMemo(() => strategyDecisionEvents(strategy), [strategy]);
  const strategyPresentation = useMemo(() => resolvedStrategyPresentation(strategy), [strategy]);
  return <ChartPreview canvasId={canvasId} changeAsOf={new Date(cutoffMs).toISOString()} chartSettings={settings.chart} instanceId={instanceId} linkContext={linkContext} liveChart={liveChart} logoUrl={presentations[linkContext.symbol]?.logo_url} onChartSettingsChange={(next) => updateSettings((current) => ({ ...current, chart: next }))} onLinkContextChange={onLinkContextChange} strategyDecisions={strategyDecisions} strategyPresentation={strategyPresentation} symbolEditable={symbolEditable} trading={trading} />;
}, chartContainerPreviewPropsEqual);

function ChartsQuotesContainerPreview({ canvasId, cutoffMs, instanceId, linkContext, liveMode, onLinkContextChange, previewContext, readOnly, settings, strategy, symbolEditable, trading, updateSettings }: Omit<ChartContainerPreviewProps, "linkGroup">) {
  const main = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.main.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.main.visibleIndicators, liveMode);
  const macroChartsEnabled = main.ready;
  const month = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.month.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.month.visibleIndicators, liveMode, macroChartsEnabled);
  const daily = useCanvasHistoricalChart(linkContext.symbol, settings.charts_quotes.daily.timeframe, cutoffMs, previewContext.sessionDate, settings.charts_quotes.daily.visibleIndicators, liveMode, macroChartsEnabled);
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
    dailyChart={<ChartPreview {...chartProps} baseHeight={255} canvasId={canvasId} chartSettings={settings.charts_quotes.daily} fillHeight instanceId={`${instanceId}.daily`} liveChart={daily} onChartSettingsChange={(next) => updateSlot("daily", { ...next, timeframe: "1d" })} timeframes={["1d"]} />}
    end={liveMode ? undefined : changeAsOf}
    layout={settings.charts_quotes.layout}
    mainChart={<ChartPreview {...chartProps} baseHeight={460} canvasId={canvasId} chartSettings={settings.charts_quotes.main} fillHeight instanceId={`${instanceId}.main`} liveChart={main} onChartSettingsChange={(next) => updateSlot("main", next)} timeframes={HISTORICAL_TIMEFRAMES} />}
    monthChart={<ChartPreview {...chartProps} baseHeight={255} canvasId={canvasId} chartSettings={settings.charts_quotes.month} fillHeight instanceId={`${instanceId}.month`} liveChart={month} onChartSettingsChange={(next) => updateSlot("month", { ...next, timeframe: "1mo" })} timeframes={["1mo"]} />}
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


function PreviewTable({ columns, onSymbolSelect, rows }: { columns: string[]; onSymbolSelect?: (symbol: string) => void; rows: PreviewRow[] }) {
  const tickerColumns = columns.filter(isPreviewTickerColumn);
  const presentations = useTickerPresentations(rows.flatMap((row) => tickerColumns.map((column) => String(row[column] || ""))));
  if (!rows.length) return <EmptyState label="No point-in-time rows" />;
  const visibleColumns = columns.filter((column) => column !== "logo" && column !== "company_name");
  return <div className="canvas-preview-table-wrap"><table className="canvas-preview-table"><thead><tr>{visibleColumns.map((column) => <th key={column}>{labelFor(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={previewRowKey(row, visibleColumns, index)}>{visibleColumns.map((column) => <td className={`${tableCellClass(column)} preview-cell-${column.replace(/[^a-z0-9_-]/gi, "-")}`} data-tone={cellTone(row[column], column)} key={column}><PreviewCell column={column} onSymbolSelect={onSymbolSelect} presentations={presentations} row={row} /></td>)}</tr>)}</tbody></table></div>;
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
  const visibleColumns = useMemo(() => columns.filter((column) => column !== "logo" && column !== "company_name"), [columns]);
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
      return visibleColumns.some((column) => searchableValue(row[column]).includes(queryValue));
    });
    return [...filtered].sort((left, right) => compareTradingValues(left[sortColumn], right[sortColumn]) * (sortDirection === "asc" ? 1 : -1));
  }, [filterColumn, filterValue, queryText, rows, sortColumn, sortDirection, visibleColumns]);
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
    {!visibleRows.length ? <EmptyState label={rows.length ? "No rows match the active search and filter" : "No point-in-time rows"} /> : <div className="canvas-preview-table-wrap"><table className="canvas-preview-table trading-data-table"><thead><tr>{renderExpanded ? <th aria-label="Expand row" className="trading-expand-column" /> : null}{visibleColumns.map((column) => <th aria-sort={sortColumn === column ? (sortDirection === "asc" ? "ascending" : "descending") : "none"} key={column}><button onClick={() => changeSort(column)} type="button"><span>{labelFor(column)}</span>{sortColumn === column ? sortDirection === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} /> : <ArrowUpDown size={11} />}</button></th>)}</tr></thead><tbody>{visibleRows.map((row, index) => {
      const key = previewRowKey(row, visibleColumns, index);
      const expanded = expandedKey === key;
      return <FragmentRow columns={visibleColumns} expanded={expanded} key={key} onExpand={renderExpanded ? () => setExpandedKey(expanded ? "" : key) : undefined} onSymbolSelect={onSymbolSelect} presentations={presentations} renderExpanded={renderExpanded} row={row} />;
    })}</tbody></table></div>}
  </div>;
}

function FragmentRow({ columns, expanded, onExpand, onSymbolSelect, presentations, renderExpanded, row }: { columns: string[]; expanded: boolean; onExpand?: () => void; onSymbolSelect?: (symbol: string) => void; presentations: ReturnType<typeof useTickerPresentations>; renderExpanded?: (row: PreviewRow) => ReactNode; row: PreviewRow }) {
  return <>{<tr className={expanded ? "is-expanded" : undefined}>{renderExpanded ? <td className="trading-expand-column"><button aria-label={expanded ? "Collapse row" : "Expand row"} aria-expanded={expanded} onClick={onExpand} type="button">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button></td> : null}{columns.map((column) => <td className={`${tableCellClass(column)} preview-cell-${column.replace(/[^a-z0-9_-]/gi, "-")}`} data-tone={cellTone(row[column], column)} key={column}><PreviewCell column={column} onSymbolSelect={onSymbolSelect} presentations={presentations} row={row} /></td>)}</tr>}{expanded && renderExpanded ? <tr className="trading-expanded-row"><td colSpan={columns.length + 1}>{renderExpanded(row)}</td></tr> : null}</>;
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
    const identity = <SecurityIdentityCell companyName={String(row.company_name ?? row.issuer_name ?? presentations[ticker]?.issuer_name ?? "")} country={String(row.country ?? row.company_country_code ?? presentations[ticker]?.country ?? "")} halted={row.market_is_halted ?? row.is_halted ?? row.trading_status} logoUrl={String(row.logo_url ?? presentations[ticker]?.logo_url ?? "")} newsRecency={row.live_news_recency} secRecency={row.sec_recency} ticker={ticker} />;
    return column === "symbol" && onSymbolSelect ? <button className="canvas-symbol-link" onClick={() => onSymbolSelect(ticker)} type="button">{identity}</button> : identity;
  }
  if (isPreviewTimeColumn(column)) return <MarketTime includeSeconds value={String(row[column] || "")} />;
  return <PresentedValue column={column} value={row[column]} />;
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
  const [proposalAction, setProposalAction] = useState("position.enter_long");
  const [proposalStop, setProposalStop] = useState("");
  const [proposalTarget, setProposalTarget] = useState("");
  const actionDefinitions = strategy?.action_definitions ?? [];
  const configuredPolicies = strategy?.action_policies ?? [];
  const intentActions = actionDefinitions.filter((action) => action.kind === "intent");
  const campaignActions = actionDefinitions.filter((action) => action.kind === "campaign_command");
  const readOnlyBacktest = strategy?.runtime_mode === "backtest" || strategy?.runtime_mode === "backtest_debug";
  const runId = strategy?.run_id || "";
  const interactiveReplay = Boolean(runId && !readOnlyBacktest);
  const interactiveLive = runtimeMode === "live" || runtimeMode === "paper";

  useEffect(() => {
    setAssignment(strategy?.assignment ?? null);
  }, [strategy?.assignment]);

  useEffect(() => {
    if (intentActions.some((action) => action.action_id === proposalAction)) return;
    setProposalAction(intentActions.find((action) => action.category === "enter")?.action_id ?? intentActions[0]?.action_id ?? "position.enter_long");
  }, [intentActions, proposalAction]);

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
          action: actionDefinitions.find((action) => action.action_id === proposalAction)?.runtime_action ?? proposalAction.replace("position.", ""),
          action_id: proposalAction,
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
    {configuredPolicies.length ? <div className="strategy-order-capabilities">
      <span>Action policies</span>
      {configuredPolicies.map((policy) => <div key={policy.policy_id}><strong>{policy.name}</strong><small>{policy.authority.replaceAll("_", " ")} · {actionDefinitions.find((action) => action.action_id === policy.action_id)?.name ?? policy.action_id}</small></div>)}
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
        {campaignActions.filter((action) => action.runtime_action !== "resume" || status === "paused").map((action) => <button className={action.runtime_action === "disable_after_exit" ? "danger" : undefined} disabled={busy || readOnlyBacktest || (status === "paused" && !["resume", "disable_after_exit"].includes(action.runtime_action))} key={action.action_id} onClick={() => command(action.runtime_action)} title={action.description} type="button">{action.name}</button>)}
      </div>
      <div className="strategy-order-proposal">
        <span><strong>Chart trade proposal</strong><small>Snapshot is revalidated by the run, then Portfolio and OMS retain exclusive authority.</small></span>
        <label><span>Trading Action</span><select onChange={(event) => setProposalAction(event.target.value)} value={proposalAction}>{intentActions.map((action) => <option key={action.action_id} value={action.action_id}>{action.name}</option>)}</select></label>
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
  if (id === "signal_stream") return <><NumberField label="Maximum events" max={5000} onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><div className="canvas-settings-note">Configured Market Discovery occurrences are append-only and preserve their trigger-time values. Strategy Activity remains a separate durable runtime surface.</div></>;
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

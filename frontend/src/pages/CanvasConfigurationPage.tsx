import { ExternalLink, Link2, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import {
  CANVAS_PREVIEW_CONTEXT_STORAGE_KEY,
  CANVAS_REGISTRY_STORAGE_KEY,
  CANVAS_SETTINGS_STORAGE_KEY,
  MAIN_CANVAS_ID,
  canvasWorkspaceStorageKey,
  configurationCanvasUrl,
  createCanvasRecord,
  focusCanvasUrl,
  readCanvasRegistry,
  readCanvasWorkspaceState,
  removeCanvasRecord,
  writeCanvasRegistry,
  writeCanvasWorkspaceState,
  type CanvasLinkContext,
  type CanvasLinkGroupId,
  type CanvasRegistry,
  type CanvasWorkspaceState,
} from "../app/canvasWorkspace";
import { ChartPanel, type ChartPayload } from "../app/components/ChartPanel";
import { TRADING_WORKSPACE_LAYOUT_VERSION, TradingWorkspace, createFocusLayouts } from "../app/components/TradingWorkspace";
import type { WorkspaceWindowLayout, WorkspaceWindowMeta } from "../app/components/WorkspaceCanvas";
import { TRADING_WORKSPACE_CONTAINERS, type WorkspaceContainerDefinition, type WorkspaceContainerId } from "../app/tradingWorkspace";

type HistoricalBar = { bar_start: string; close: number; high: number; low: number; open: number; volume: number };
type PreviewRow = Record<string, unknown>;
type CanvasPreview = {
  as_of: string;
  chart: { bars: HistoricalBar[]; symbol: string; timeframe: string };
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

type ContainerSettings = {
  chart: { showVolume: boolean; symbol: string; timeframe: "1m" | "5m" };
  fills: { limit: number; showCommission: boolean };
  journal: { limit: number };
  news: { limit: number; showTeaser: boolean };
  orders: { limit: number; showOrderIds: boolean };
  portfolio: { showPositions: boolean; showPnl: boolean };
  scanner: { limit: number; showActivity: boolean };
  sec: { limit: number; form: string };
  strategy: { showSignals: boolean };
  xbrl: { limit: number; showPeriod: boolean };
};

type CanvasPreviewContext = { previewTime: string; sessionDate: string };

const ALL_CONTAINER_IDS = TRADING_WORKSPACE_CONTAINERS.map((definition) => definition.id);
const DEFAULT_SETTINGS: ContainerSettings = {
  chart: { showVolume: true, symbol: "AAPL", timeframe: "1m" },
  fills: { limit: 5, showCommission: true },
  journal: { limit: 6 },
  news: { limit: 5, showTeaser: true },
  orders: { limit: 6, showOrderIds: true },
  portfolio: { showPositions: true, showPnl: true },
  scanner: { limit: 6, showActivity: true },
  sec: { limit: 5, form: "All" },
  strategy: { showSignals: true },
  xbrl: { limit: 6, showPeriod: true },
};

const LINK_OPTIONS: Array<{ label: string; value: CanvasLinkGroupId }> = [
  { label: "None · independent", value: "none" },
  { label: "Group A · Market context", value: "A" },
  { label: "Group B · Execution context", value: "B" },
  { label: "Group C · Custom context", value: "C" },
];

export function CanvasConfigurationPage() {
  return <CanvasWorkspaceSurface canvasId={MAIN_CANVAS_ID} manager />;
}

export function CanvasFocusPage() {
  const canvasId = new URLSearchParams(window.location.search).get("canvas") || MAIN_CANVAS_ID;
  return <CanvasWorkspaceSurface canvasId={canvasId} manager={false} />;
}

function CanvasWorkspaceSurface({ canvasId, manager }: { canvasId: string; manager: boolean }) {
  const [registry, setRegistry] = useState<CanvasRegistry>(readCanvasRegistry);
  const [settings, setSettings] = useState<ContainerSettings>(readSettings);
  const [previewContext, setPreviewContext] = useState<CanvasPreviewContext>(readPreviewContext);
  const [preview, setPreview] = useState<CanvasPreview | null>(null);
  const [workspaceState, setWorkspaceState] = useState<CanvasWorkspaceState | null>(() => readCanvasWorkspaceState(canvasId));
  const [initialCanvasState] = useState<CanvasWorkspaceState | null>(() => readCanvasWorkspaceState(canvasId));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [defaultSaved, setDefaultSaved] = useState(false);

  const currentCanvas = registry.canvases.find((canvas) => canvas.id === canvasId) ?? { id: canvasId, label: canvasId === MAIN_CANVAS_ID ? "Main" : "Focus canvas" };
  const activeContainerId = workspaceState?.openIds.includes("chart") ? "chart" : workspaceState?.openIds[0] ?? "chart";
  const activeLinkGroup = registry.linkAssignments[activeContainerId] ?? "none";
  const activeLinkContext = activeLinkGroup === "none"
    ? settings.chart
    : registry.linkContexts[activeLinkGroup];

  useEffect(() => {
    writeCanvasRegistry(registry);
  }, [registry]);

  useEffect(() => {
    window.localStorage.setItem(CANVAS_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    window.localStorage.setItem(CANVAS_PREVIEW_CONTEXT_STORAGE_KEY, JSON.stringify(previewContext));
  }, [previewContext]);

  useEffect(() => {
    const syncSharedCanvasState = (event: StorageEvent) => {
      if (event.key === CANVAS_REGISTRY_STORAGE_KEY) setRegistry(readCanvasRegistry());
      if (event.key === CANVAS_SETTINGS_STORAGE_KEY) setSettings(readSettings());
      if (event.key === CANVAS_PREVIEW_CONTEXT_STORAGE_KEY) setPreviewContext(readPreviewContext());
    };
    window.addEventListener("storage", syncSharedCanvasState);
    return () => window.removeEventListener("storage", syncSharedCanvasState);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api<CanvasPreview>("/api/trading/canvas-preview", {
      body: JSON.stringify({
        chart_symbol: activeLinkContext.symbol,
        chart_timeframe: activeLinkContext.timeframe,
        preview_time: previewContext.previewTime,
        session_date: previewContext.sessionDate,
      }),
      method: "POST",
      timeoutMs: 60000,
    }).then((payload) => { if (!cancelled) setPreview(payload); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [activeLinkContext.symbol, activeLinkContext.timeframe, previewContext.previewTime, previewContext.sessionDate, refreshKey]);

  const metaForContainer = useMemo(() => (definition: WorkspaceContainerDefinition): WorkspaceWindowMeta => {
    const sourceError = preview?.errors[definition.id] ?? preview?.errors[definition.id === "news" ? "news" : definition.id === "sec" ? "sec" : definition.id === "xbrl" ? "xbrl" : ""];
    return {
      detail: `${definition.title} rendered at the shared configuration clock.`,
      freshness: previewContext.previewTime,
      sourceLabel: sourceError ? "Unavailable" : definition.id === "chart" || definition.id === "scanner" ? "QMD History" : ["news", "sec", "xbrl"].includes(definition.id) ? "Point-in-time DB" : "IBKR preview",
      status: sourceError ? "error" : preview ? "ready" : "idle",
    };
  }, [preview, previewContext.previewTime]);

  const canvasTargets = registry.canvases.map((canvas, index) => ({
    color: ["var(--primary)", "var(--info)", "var(--success)", "var(--warning)"][index % 4],
    id: canvas.id,
    isCurrent: canvas.id === canvasId,
    label: canvas.label,
  }));

  function updateRegistry(update: (current: CanvasRegistry) => CanvasRegistry) {
    setRegistry((current) => update(current));
  }

  function updateLinkContext(group: Exclude<CanvasLinkGroupId, "none">, patch: Partial<CanvasLinkContext>) {
    updateRegistry((current) => ({
      ...current,
      linkContexts: { ...current.linkContexts, [group]: { ...current.linkContexts[group], ...patch } },
    }));
  }

  function setContainerLink(containerId: WorkspaceContainerId, group: CanvasLinkGroupId) {
    updateRegistry((current) => ({ ...current, linkAssignments: { ...current.linkAssignments, [containerId]: group } }));
  }

  function openNewCanvas(containerId?: WorkspaceContainerId, sourceLayout?: WorkspaceWindowLayout) {
    const created = createCanvasRecord(registry, containerId ? `${containerTitle(containerId)} focus` : undefined);
    const sourceState = registry.defaultState ?? workspaceState;
    const inheritedIds = sourceState?.openIds.length ? sourceState.openIds : ALL_CONTAINER_IDS;
    const state: CanvasWorkspaceState = containerId
      ? {
          layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
          layouts: { [containerId]: focusLayout(sourceLayout) },
          openIds: [containerId],
        }
      : {
          layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
          layouts: sourceState
            ? normalizeInheritedLayouts(sourceState.layouts, inheritedIds)
            : createFocusLayouts(inheritedIds),
          openIds: [...inheritedIds],
        };
    writeCanvasWorkspaceState(created.canvas.id, state);
    setRegistry(created.registry);
    window.open(focusCanvasUrl(created.canvas.id), "_blank", "noopener,noreferrer");
  }

  function moveContainer(containerId: WorkspaceContainerId, targetCanvasId: string, sourceLayout: WorkspaceWindowLayout) {
    const target = readCanvasWorkspaceState(targetCanvasId) ?? { layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts: {}, openIds: [] };
    const openIds = target.openIds.includes(containerId) ? target.openIds : [...target.openIds, containerId];
    const targetContainsFullscreenWindow = target.openIds.some((id) => target.layouts[id]?.fullscreen);
    const layouts = target.openIds.length === 0
      ? { ...target.layouts, [containerId]: focusLayout(sourceLayout) }
      : targetContainsFullscreenWindow
        ? createFocusLayouts(openIds)
        : { ...target.layouts, [containerId]: offsetLayout(sourceLayout, target.openIds.length) };
    writeCanvasWorkspaceState(targetCanvasId, {
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts,
      openIds,
    });
  }

  function saveDefaultLayout() {
    if (!workspaceState) return;
    const defaultState = {
      ...workspaceState,
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
        {manager ? <h1>Canvas</h1> : <a className="canvas-back-link" href={configurationCanvasUrl()}>All canvases</a>}
        {!manager ? <strong className="canvas-focus-title">{currentCanvas.label}</strong> : null}
        <label><span>Date</span><input aria-label="Preview date" onChange={(event) => setPreviewContext((current) => ({ ...current, sessionDate: event.target.value }))} type="date" value={previewContext.sessionDate} /></label>
        <label><span>Time</span><input aria-label="Preview time" onChange={(event) => setPreviewContext((current) => ({ ...current, previewTime: event.target.value }))} type="time" value={previewContext.previewTime} /></label>
        <button aria-label="Refresh preview" className="toolbar-button compact" onClick={() => setRefreshKey((value) => value + 1)} title="Refresh preview" type="button"><RefreshCw size={13} /></button>
        {manager ? <button className="button secondary compact" disabled={!workspaceState} onClick={saveDefaultLayout} type="button"><Save size={13} /> {defaultSaved ? "Default saved" : "Set default"}</button> : null}
        <span className="canvas-preview-state" data-state={error ? "error" : loading ? "loading" : "ready"}>{error ? "Unavailable" : loading ? "Loading" : `${activeLinkContext.symbol} · ${previewContext.previewTime}`}</span>
      </header>

      {manager ? <CanvasManager registry={registry} onCreate={() => openNewCanvas()} onOpen={(id) => window.open(focusCanvasUrl(id), "_blank", "noopener,noreferrer")} onRemove={removeCanvas} /> : null}
      {error ? <div className="canvas-inline-error">{error}</div> : null}

      <TradingWorkspace
        canPopOut
        canvasTargets={canvasTargets}
        clockLabel={`${previewContext.sessionDate} · ${previewContext.previewTime} New York`}
        compact
        defaultOpenIds={manager ? ALL_CONTAINER_IDS : initialCanvasState?.openIds ?? []}
        defaultStateOverride={manager ? registry.defaultState ?? null : initialCanvasState}
        definitionsOverride={TRADING_WORKSPACE_CONTAINERS}
        historicalSourceReady={!error}
        layoutPreset={manager ? "global" : "focus"}
        metaForContainer={metaForContainer}
        mode="replay"
        onMoveContainerToCanvas={moveContainer}
        onPopOutContainer={openNewCanvas}
        onStateChange={setWorkspaceState}
        renderContainer={(definition) => {
          const group = registry.linkAssignments[definition.id] ?? "none";
          const linkContext = group === "none" ? settings.chart : registry.linkContexts[group];
          const linkedContainers = group === "none" ? [] : TRADING_WORKSPACE_CONTAINERS
            .filter((candidate) => candidate.id !== definition.id && registry.linkAssignments[candidate.id] === group)
            .map((candidate) => candidate.title);
          return <ContainerPreview
            definition={definition}
            linkContext={linkContext}
            linkGroup={group}
            linkedContainers={linkedContainers}
            loading={loading}
            onLinkChange={(nextGroup) => setContainerLink(definition.id, nextGroup)}
            onLinkContextChange={(patch) => { if (group !== "none") updateLinkContext(group, patch); }}
            preview={preview}
            settings={settings}
            setSettings={setSettings}
          />;
        }}
        runLabel={currentCanvas.label}
        runStatus={preview ? "running" : "idle"}
        showHealth={false}
        storageKeyOverride={canvasWorkspaceStorageKey(canvasId)}
        workspaceBadge={manager ? "Main" : "Focus"}
      />
    </div>
  );
}

function CanvasManager({ onCreate, onOpen, onRemove, registry }: { onCreate: () => void; onOpen: (id: string) => void; onRemove: (id: string) => void; registry: CanvasRegistry }) {
  return <section aria-label="Canvas manager" className="canvas-manager-strip"><strong>Canvases</strong><div className="canvas-manager-items">{registry.canvases.map((canvas) => <article key={canvas.id} data-main={canvas.id === MAIN_CANVAS_ID ? "true" : "false"}>{canvas.id === MAIN_CANVAS_ID ? <><span>{canvas.label}</span><small>default authority</small></> : <><button aria-label={`Open ${canvas.label}`} className="canvas-manager-open" onClick={() => onOpen(canvas.id)} title="Open canvas in a new page" type="button"><span>{canvas.label}</span><ExternalLink size={11} /></button><button aria-label={`Remove ${canvas.label}`} className="toolbar-button compact" onClick={() => onRemove(canvas.id)} title="Remove canvas" type="button"><Trash2 size={12} /></button></>}</article>)}</div><button className="button secondary compact" onClick={onCreate} type="button"><Plus size={13} /> New canvas</button></section>;
}

function ContainerPreview({ definition, linkContext, linkGroup, linkedContainers, loading, onLinkChange, onLinkContextChange, preview, settings, setSettings }: {
  definition: WorkspaceContainerDefinition;
  linkContext: CanvasLinkContext;
  linkGroup: CanvasLinkGroupId;
  linkedContainers: string[];
  loading: boolean;
  onLinkChange: (group: CanvasLinkGroupId) => void;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  preview: CanvasPreview | null;
  settings: ContainerSettings;
  setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>>;
}) {
  const [configOpen, setConfigOpen] = useState(false);
  return <div className="canvas-container-preview">
    <button aria-expanded={configOpen} aria-label={`Configure and link ${definition.title}`} className="canvas-container-configure" onClick={() => setConfigOpen((value) => !value)} title={linkGroup === "none" ? "Configure container and choose a link group" : `Linked to Group ${linkGroup}; configure or change group`} type="button">{configOpen ? <X size={12} /> : <Link2 size={12} />}<span>{configOpen ? "Close" : linkGroup === "none" ? "Link" : `Link ${linkGroup}`}</span></button>
    {configOpen ? <div className="canvas-container-settings" aria-label={`${definition.title} configuration`}><div className="canvas-link-guide"><strong>Link containers</strong><p>Choose the same group in each container to share symbol and interval across canvases.</p></div><label><span>Linked group</span><select aria-label={`${definition.title} link group`} onChange={(event) => onLinkChange(event.target.value as CanvasLinkGroupId)} value={linkGroup}>{LINK_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>{linkGroup !== "none" ? <div className="canvas-link-context"><Link2 size={12} /><span>Group {linkGroup} · {linkContext.symbol} · {linkContext.timeframe}</span><small>{linkedContainers.length ? `With ${linkedContainers.join(", ")}` : "No other container uses this group yet"}</small></div> : null}{containerFields(definition.id, settings, linkContext, setSettings, onLinkContextChange)}</div> : null}
    <div className={configOpen ? "canvas-container-content configuration-open" : "canvas-container-content"}>{loading && !preview ? <div className="canvas-preview-loading">Loading {definition.title.toLowerCase()}…</div> : renderPreview(definition.id, preview, settings, setSettings, linkGroup, onLinkContextChange, linkContext)}</div>
  </div>;
}

function renderPreview(id: WorkspaceContainerId, preview: CanvasPreview | null, settings: ContainerSettings, setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>>, linkGroup: CanvasLinkGroupId, onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void, linkContext: CanvasLinkContext) {
  if (!preview) return <EmptyState label="No preview data" />;
  if (id === "chart") return <ChartPreview linkContext={linkContext} onLinkContextChange={onLinkContextChange} preview={preview} settings={settings} setSettings={setSettings} />;
  if (id === "scanner") return <PreviewTable columns={settings.scanner.showActivity ? ["symbol", "last", "change_pct", "volume", "trade_count"] : ["symbol", "last", "change_pct"]} onSymbolSelect={linkGroup === "none" ? undefined : (symbol) => onLinkContextChange({ symbol })} rows={preview.scanner.slice(0, settings.scanner.limit)} />;
  if (id === "portfolio") return <PortfolioPreview data={preview.portfolio} settings={settings.portfolio} />;
  if (id === "orders") return <PreviewTable columns={settings.orders.showOrderIds ? ["orderId", "ticker", "side", "orderType", "quantity", "status"] : ["ticker", "side", "orderType", "quantity", "status"]} rows={preview.orders.slice(0, settings.orders.limit)} />;
  if (id === "fills") return <PreviewTable columns={settings.fills.showCommission ? ["time", "ticker", "side", "shares", "price", "commission"] : ["time", "ticker", "side", "shares", "price"]} rows={preview.fills.slice(0, settings.fills.limit)} />;
  if (id === "strategy") return <StrategyPreview data={preview.strategy} showSignals={settings.strategy.showSignals} />;
  if (id === "news") return <NewsPreview rows={preview.news.slice(0, settings.news.limit)} showTeaser={settings.news.showTeaser} />;
  if (id === "sec") {
    const rows = settings.sec.form === "All" ? preview.sec : preview.sec.filter((row) => row.form_type === settings.sec.form);
    return <PreviewTable columns={["accepted_at_utc", "form_type", "company_name", "accession_number"]} rows={rows.slice(0, settings.sec.limit)} />;
  }
  if (id === "xbrl") return <PreviewTable columns={settings.xbrl.showPeriod ? ["filed_at_utc", "tag", "value", "unit_code", "fiscal_period"] : ["filed_at_utc", "tag", "value", "unit_code"]} rows={preview.xbrl.slice(0, settings.xbrl.limit)} />;
  return <PreviewTable columns={["time", "category", "event", "detail"]} rows={preview.journal.slice(0, settings.journal.limit)} />;
}

function ChartPreview({ linkContext, onLinkContextChange, preview, settings, setSettings }: { linkContext: CanvasLinkContext; onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void; preview: CanvasPreview; settings: ContainerSettings; setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>> }) {
  const payload = useMemo<ChartPayload>(() => ({
    candles: preview.chart.bars.map((bar) => ({ close: bar.close, high: bar.high, low: bar.low, open: bar.open, time: Date.parse(bar.bar_start) / 1000 })),
    markers: [], oscillator_series: [], overlay_series: [], regions: [],
    volume: settings.chart.showVolume ? preview.chart.bars.map((bar) => ({ color: bar.close >= bar.open ? "var(--success)" : "var(--danger)", time: Date.parse(bar.bar_start) / 1000, value: bar.volume })) : [],
  }), [preview.chart.bars, settings.chart.showVolume]);
  function updateChart(symbol: string, timeframe: "1m" | "5m") {
    setSettings((current) => ({ ...current, chart: { ...current.chart, symbol, timeframe } }));
    onLinkContextChange({ symbol, timeframe });
  }
  return <ChartPanel emptyMessage="No bars at this clock." enableFullscreen={false} featureOptions={[]} indicatorOptions={[]} initialFitMode="recent" onTickerChange={(symbol) => updateChart(symbol.toUpperCase(), linkContext.timeframe)} onTimeframeChange={(timeframe) => updateChart(linkContext.symbol, timeframe as "1m" | "5m")} onVisibleColumnsChange={() => undefined} payload={payload} periodEnd={preview.as_of.slice(0, 10)} periodStart={preview.as_of.slice(0, 10)} showIndicatorControls={false} ticker={linkContext.symbol} timeframe={linkContext.timeframe} timeframes={["1m", "5m"]} visibleColumns={[]} />;
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

function NewsPreview({ rows, showTeaser }: { rows: PreviewRow[]; showTeaser: boolean }) {
  if (!rows.length) return <EmptyState label="No news before this clock" />;
  return <div className="canvas-news-preview">{rows.map((row, index) => <article key={String(row.canonical_news_id ?? index)}><time>{formatCell(row.published_at_utc, "published_at_utc")}</time><strong>{String(row.title ?? "Untitled")}</strong>{showTeaser && row.teaser ? <p>{String(row.teaser)}</p> : null}</article>)}</div>;
}

function containerFields(id: WorkspaceContainerId, settings: ContainerSettings, linkContext: CanvasLinkContext, setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>>, onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void) {
  const current = settings[id] as Record<string, unknown>;
  function patch(value: Record<string, unknown>) { setSettings((state) => ({ ...state, [id]: { ...state[id], ...value } })); }
  if (id === "chart") return <><TextField label="Symbol" onChange={(value) => { patch({ symbol: value.toUpperCase() }); onLinkContextChange({ symbol: value.toUpperCase() }); }} value={linkContext.symbol} /><SelectField label="Bar interval" onChange={(value) => { patch({ timeframe: value }); onLinkContextChange({ timeframe: value as "1m" | "5m" }); }} options={["1m", "5m"]} value={linkContext.timeframe} /><CheckField checked={Boolean(current.showVolume)} label="Show volume" onChange={(value) => patch({ showVolume: value })} /></>;
  if (id === "portfolio") return <><CheckField checked={Boolean(current.showPositions)} label="Show positions" onChange={(value) => patch({ showPositions: value })} /><CheckField checked={Boolean(current.showPnl)} label="Show P&L" onChange={(value) => patch({ showPnl: value })} /></>;
  if (id === "strategy") return <CheckField checked={Boolean(current.showSignals)} label="Show recent signals" onChange={(value) => patch({ showSignals: value })} />;
  if (id === "scanner") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showActivity)} label="Show market activity" onChange={(value) => patch({ showActivity: value })} /></>;
  if (id === "orders") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showOrderIds)} label="Show order IDs" onChange={(value) => patch({ showOrderIds: value })} /></>;
  if (id === "fills") return <><NumberField label="Rows" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showCommission)} label="Show commission" onChange={(value) => patch({ showCommission: value })} /></>;
  if (id === "news") return <><NumberField label="Last N articles" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showTeaser)} label="Show teaser" onChange={(value) => patch({ showTeaser: value })} /></>;
  if (id === "sec") return <><NumberField label="Last N filings" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><SelectField label="Form" onChange={(value) => patch({ form: value })} options={["All", "10-K", "10-Q", "8-K"]} value={String(current.form)} /></>;
  if (id === "xbrl") return <><NumberField label="Last N facts" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} /><CheckField checked={Boolean(current.showPeriod)} label="Show fiscal period" onChange={(value) => patch({ showPeriod: value })} /></>;
  return <NumberField label="Last N events" onChange={(value) => patch({ limit: value })} value={Number(current.limit)} />;
}

function TextField({ label, onChange, value }: { label: string; onChange: (value: string) => void; value: string }) { return <label><span>{label}</span><input onChange={(event) => onChange(event.target.value)} value={value} /></label>; }
function NumberField({ label, onChange, value }: { label: string; onChange: (value: number) => void; value: number }) { return <label><span>{label}</span><input max={20} min={1} onChange={(event) => onChange(Number(event.target.value))} type="number" value={value} /></label>; }
function SelectField({ label, onChange, options, value }: { label: string; onChange: (value: string) => void; options: string[]; value: string }) { return <label><span>{label}</span><select onChange={(event) => onChange(event.target.value)} value={value}>{options.map((option) => <option key={option}>{option}</option>)}</select></label>; }
function CheckField({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) { return <label className="canvas-check-field"><input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" /><span>{label}</span></label>; }
function Metric({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong>{value}</strong></div>; }
function EmptyState({ label }: { label: string }) { return <div className="canvas-preview-empty">{label}</div>; }

function readSettings(): ContainerSettings { try { const raw = window.localStorage.getItem(CANVAS_SETTINGS_STORAGE_KEY); return raw ? { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } : DEFAULT_SETTINGS; } catch { return DEFAULT_SETTINGS; } }
function readPreviewContext(): CanvasPreviewContext { try { const parsed = JSON.parse(window.localStorage.getItem(CANVAS_PREVIEW_CONTEXT_STORAGE_KEY) || "null") as CanvasPreviewContext | null; return parsed?.sessionDate && parsed?.previewTime ? parsed : { previewTime: "09:45", sessionDate: previousWeekdayIsoDate() }; } catch { return { previewTime: "09:45", sessionDate: previousWeekdayIsoDate() }; } }
function previousWeekdayIsoDate() { const value = new Date(); value.setDate(value.getDate() - 1); while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1); const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000); return local.toISOString().slice(0, 10); }
function labelFor(value: string) { return value.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2"); }
function previewRowKey(row: PreviewRow, columns: string[], index: number) { return `${columns.map((column) => String(row[column] ?? "")).join("|")}|${index}`; }
function money(value: unknown) { return typeof value === "number" ? new Intl.NumberFormat("en-US", { currency: "USD", style: "currency" }).format(value) : "—"; }
function formatCell(value: unknown, column: string) { if (value === null || value === undefined || value === "") return "—"; if (column.includes("time") || column.includes("at_utc")) { const date = new Date(String(value)); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }).format(date); } if (typeof value === "number") return new Intl.NumberFormat("en-US", { maximumFractionDigits: column.includes("pct") ? 2 : 4 }).format(value); if (Array.isArray(value)) return value.join(", "); return String(value); }
function containerTitle(id: WorkspaceContainerId) { return TRADING_WORKSPACE_CONTAINERS.find((definition) => definition.id === id)?.title ?? id; }
function normalizeInheritedLayouts(layouts: Record<string, WorkspaceWindowLayout>, ids: WorkspaceContainerId[]) {
  const fallback = createFocusLayouts(ids);
  return Object.fromEntries(ids.map((id) => [id, { ...(layouts[id] ?? fallback[id]), fullscreen: false, minimized: false }]));
}
function focusLayout(source?: WorkspaceWindowLayout): WorkspaceWindowLayout { const scale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale")) || 1; return { fullscreen: true, h: Math.max(320, Math.floor(window.innerHeight / scale) - 62), minimized: false, w: Math.max(680, Math.floor(window.innerWidth / scale)), x: 0, y: 0, z: Math.max(1, source?.z ?? 1) }; }
function offsetLayout(source: WorkspaceWindowLayout, index: number): WorkspaceWindowLayout { const offset = (index % 6) * 18; return { ...source, fullscreen: false, minimized: false, x: offset, y: offset, z: index + 1 }; }

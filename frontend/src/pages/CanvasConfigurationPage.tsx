import { RefreshCw, Settings2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { ChartPanel, type ChartPayload } from "../app/components/ChartPanel";
import { TradingWorkspace } from "../app/components/TradingWorkspace";
import { TRADING_WORKSPACE_CONTAINERS, type WorkspaceContainerDefinition, type WorkspaceContainerId } from "../app/tradingWorkspace";
import type { WorkspaceWindowMeta } from "../app/components/WorkspaceCanvas";

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

const LAYOUT_STORAGE_KEY = "quant-research-workbench.trading-workspace.global.v1";
const SETTINGS_STORAGE_KEY = "quant-research-workbench.canvas.container-settings.v1";
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

export function CanvasConfigurationPage() {
  const [sessionDate, setSessionDate] = useState(previousWeekdayIsoDate);
  const [previewTime, setPreviewTime] = useState("09:45");
  const [settings, setSettings] = useState<ContainerSettings>(readSettings);
  const [selectedContainer, setSelectedContainer] = useState<WorkspaceContainerId | null>(null);
  const [preview, setPreview] = useState<CanvasPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings)), [settings]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api<CanvasPreview>("/api/trading/canvas-preview", {
      body: JSON.stringify({ chart_symbol: settings.chart.symbol, chart_timeframe: settings.chart.timeframe, preview_time: previewTime, session_date: sessionDate }),
      method: "POST",
      timeoutMs: 60000,
    }).then((payload) => { if (!cancelled) setPreview(payload); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [previewTime, refreshKey, sessionDate, settings.chart.symbol, settings.chart.timeframe]);

  const metaForContainer = useMemo(() => (definition: WorkspaceContainerDefinition): WorkspaceWindowMeta => {
    const sourceError = preview?.errors[definition.id] ?? (definition.id === "news" ? preview?.errors.news : definition.id === "sec" ? preview?.errors.sec : definition.id === "xbrl" ? preview?.errors.xbrl : undefined);
    return {
      detail: `${definition.title} rendered at the global configuration clock.`,
      freshness: previewTime,
      sourceLabel: sourceError ? "Source unavailable" : definition.id === "chart" || definition.id === "scanner" ? "QMD History" : ["news", "sec", "xbrl"].includes(definition.id) ? "Point-in-time database" : "IBKR-shaped preview",
      status: sourceError ? "error" : preview ? "ready" : "idle",
    };
  }, [preview, previewTime]);

  return (
    <div className="canvas-config-page">
      <header className="canvas-config-toolbar">
        <h1>Canvas configuration</h1>
        <label><span>Date</span><input aria-label="Preview date" onChange={(event) => setSessionDate(event.target.value)} type="date" value={sessionDate} /></label>
        <label><span>Time</span><input aria-label="Preview time" onChange={(event) => setPreviewTime(event.target.value)} type="time" value={previewTime} /></label>
        <button className="button secondary compact" onClick={() => setRefreshKey((value) => value + 1)} type="button"><RefreshCw size={14} /> Refresh</button>
        <span className="canvas-preview-state" data-state={error ? "error" : loading ? "loading" : "ready"}>{error ? "Preview unavailable" : loading ? "Loading 09:45 state" : "Point-in-time preview"}</span>
      </header>
      {error ? <div className="canvas-inline-error">{error}</div> : null}

      <TradingWorkspace
        clockLabel={`${sessionDate} · ${previewTime} New York`}
        defaultOpenIds={ALL_CONTAINER_IDS}
        definitionsOverride={TRADING_WORKSPACE_CONTAINERS}
        historicalSourceReady={!error}
        layoutPreset="global"
        metaForContainer={metaForContainer}
        mode="replay"
        renderContainer={(definition) => (
          <ContainerPreview definition={definition} loading={loading} onConfigure={() => setSelectedContainer(definition.id)} preview={preview} settings={settings} setSettings={setSettings} />
        )}
        runLabel="Global layout"
        runStatus={preview ? "running" : "idle"}
        showHealth={false}
        sourceLabel="09:45 preview"
        statusLabel="Configuration"
        storageKeyOverride={LAYOUT_STORAGE_KEY}
        workspaceBadge="Global"
      />

      {selectedContainer ? <ConfigurationDrawer containerId={selectedContainer} onClose={() => setSelectedContainer(null)} settings={settings} setSettings={setSettings} /> : null}
    </div>
  );
}

function ContainerPreview({ definition, loading, onConfigure, preview, settings, setSettings }: { definition: WorkspaceContainerDefinition; loading: boolean; onConfigure: () => void; preview: CanvasPreview | null; settings: ContainerSettings; setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>> }) {
  return (
    <div className="canvas-container-preview">
      <button className="canvas-container-configure" onClick={onConfigure} type="button"><Settings2 size={13} /> Configure</button>
      {loading && !preview ? <div className="canvas-preview-loading">Loading {definition.title.toLowerCase()}…</div> : renderPreview(definition.id, preview, settings, setSettings)}
    </div>
  );
}

function renderPreview(id: WorkspaceContainerId, preview: CanvasPreview | null, settings: ContainerSettings, setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>>) {
  if (!preview) return <EmptyState label="No preview data" />;
  if (id === "chart") return <ChartPreview preview={preview} settings={settings} setSettings={setSettings} />;
  if (id === "scanner") return <PreviewTable columns={settings.scanner.showActivity ? ["symbol", "last", "change_pct", "volume", "trade_count"] : ["symbol", "last", "change_pct"]} rows={preview.scanner.slice(0, settings.scanner.limit)} />;
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

function ChartPreview({ preview, settings, setSettings }: { preview: CanvasPreview; settings: ContainerSettings; setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>> }) {
  const payload = useMemo<ChartPayload>(() => ({
    candles: preview.chart.bars.map((bar) => ({ close: bar.close, high: bar.high, low: bar.low, open: bar.open, time: Date.parse(bar.bar_start) / 1000 })),
    markers: [], oscillator_series: [], overlay_series: [], regions: [],
    volume: settings.chart.showVolume ? preview.chart.bars.map((bar) => ({ color: bar.close >= bar.open ? "var(--success)" : "var(--danger)", time: Date.parse(bar.bar_start) / 1000, value: bar.volume })) : [],
  }), [preview.chart.bars, settings.chart.showVolume]);
  return <ChartPanel emptyMessage="No bars at this clock." enableFullscreen={false} featureOptions={[]} indicatorOptions={[]} initialFitMode="recent" onTickerChange={(symbol) => setSettings((current) => ({ ...current, chart: { ...current.chart, symbol: symbol.toUpperCase() } }))} onTimeframeChange={(timeframe) => setSettings((current) => ({ ...current, chart: { ...current.chart, timeframe: timeframe as "1m" | "5m" } }))} onVisibleColumnsChange={() => undefined} payload={payload} periodEnd={preview.as_of.slice(0, 10)} periodStart={preview.as_of.slice(0, 10)} showIndicatorControls={false} ticker={settings.chart.symbol} timeframe={settings.chart.timeframe} timeframes={["1m", "5m"]} visibleColumns={[]} />;
}

function PreviewTable({ columns, rows }: { columns: string[]; rows: PreviewRow[] }) {
  if (!rows.length) return <EmptyState label="No point-in-time rows" />;
  return <div className="canvas-preview-table-wrap"><table className="canvas-preview-table"><thead><tr>{columns.map((column) => <th key={column}>{labelFor(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.orderId ?? row.executionId ?? row.accession_number ?? row.canonical_news_id ?? index)}>{columns.map((column) => <td key={column}>{formatCell(row[column], column)}</td>)}</tr>)}</tbody></table></div>;
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

function ConfigurationDrawer({ containerId, onClose, settings, setSettings }: { containerId: WorkspaceContainerId; onClose: () => void; settings: ContainerSettings; setSettings: React.Dispatch<React.SetStateAction<ContainerSettings>> }) {
  const definition = TRADING_WORKSPACE_CONTAINERS.find((item) => item.id === containerId)!;
  function patch(value: Record<string, unknown>) { setSettings((current) => ({ ...current, [containerId]: { ...current[containerId], ...value } })); }
  return <aside className="canvas-config-drawer" aria-label={`${definition.title} configuration`}><header><div><span>Container</span><strong>{definition.title}</strong></div><button aria-label="Close configuration" onClick={onClose} type="button"><X size={18} /></button></header><div className="canvas-config-fields">{containerFields(containerId, settings, patch)}</div></aside>;
}

function containerFields(id: WorkspaceContainerId, settings: ContainerSettings, patch: (value: Record<string, unknown>) => void) {
  const current = settings[id] as Record<string, unknown>;
  if (id === "chart") return <><TextField label="Symbol" onChange={(value) => patch({ symbol: value.toUpperCase() })} value={String(current.symbol)} /><SelectField label="Bar interval" onChange={(value) => patch({ timeframe: value })} options={["1m", "5m"]} value={String(current.timeframe)} /><CheckField checked={Boolean(current.showVolume)} label="Show volume" onChange={(value) => patch({ showVolume: value })} /></>;
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

function readSettings(): ContainerSettings { try { const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY); return raw ? { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } : DEFAULT_SETTINGS; } catch { return DEFAULT_SETTINGS; } }
function previousWeekdayIsoDate() { const value = new Date(); value.setDate(value.getDate() - 1); while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1); const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000); return local.toISOString().slice(0, 10); }
function labelFor(value: string) { return value.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2"); }
function money(value: unknown) { return typeof value === "number" ? new Intl.NumberFormat("en-US", { currency: "USD", style: "currency" }).format(value) : "—"; }
function formatCell(value: unknown, column: string) { if (value === null || value === undefined || value === "") return "—"; if (column.includes("time") || column.includes("at_utc")) { const date = new Date(String(value)); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }).format(date); } if (typeof value === "number") return new Intl.NumberFormat("en-US", { maximumFractionDigits: column.includes("pct") ? 2 : 4 }).format(value); if (Array.isArray(value)) return value.join(", "); return String(value); }

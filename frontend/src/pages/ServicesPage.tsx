import { createChart, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { Activity, AlertTriangle, CalendarDays, CheckCircle2, Clock3, Loader2, MapPin, RadioTower, RefreshCcw, Settings2, WifiOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api } from "../api/client";
import { Button } from "../app/components/Button";
import { DataTable } from "../app/components/DataTable";
import { Modal } from "../app/components/Modal";
import { displayName, formatCell, formatCompactNumber } from "../app/format";

export type ServicePageMode = "dashboard" | ServiceId;
export type ServiceId = "ibkr" | "news" | "qmd" | "reference" | "sec" | "text-embed";

type ServiceRegistry = {
  base_url: string;
  description: string;
  id: ServiceId;
  kind: string;
  label: string;
};

type ServiceStatusPayload = {
  checked_at_utc: string;
  current_operation: Record<string, unknown>;
  database_tables?: ServiceDatabaseTablePayload;
  errors: Record<string, unknown>;
  header: Record<string, unknown>;
  health: Record<string, unknown>;
  logs?: ServiceLogPayload;
  metrics: Record<string, unknown>;
  online: boolean;
  recent: unknown;
  registry: ServiceRegistry;
  snapshot: Record<string, unknown>;
  status: string;
};

type ServicesStatusPayload = {
  checked_at_utc: string;
  services: ServiceStatusPayload[];
};

type ServiceDatabaseTablePayload = {
  error?: string;
  rows?: ServiceDatabaseTableRow[];
};

type ServiceDatabaseTableRow = {
  bytes?: string;
  database?: string;
  detail?: string;
  engine?: string;
  latest_update?: string;
  role?: string;
  rows?: string;
  rows_last_month?: string;
  rows_last_week?: string;
  rows_today?: string;
  status?: string;
  table?: string;
  time_column?: string;
  [key: string]: string | undefined;
};

type ServiceTablePreviewPayload = {
  database: string;
  limit: number;
  order_by?: string;
  rows: Record<string, unknown>[];
  table: string;
};

type ServiceLogPayload = {
  error?: string;
  path?: string;
  rows?: ServiceRuntimeLogRow[];
};

type ServiceRuntimeLogRow = {
  detail?: string;
  event?: string;
  fields?: Record<string, unknown>;
  level?: string;
  line?: number;
  source?: string;
  title?: string;
  ts_utc?: string;
};

const SERVICE_IDS: ServiceId[] = ["qmd", "news", "sec", "text-embed", "reference", "ibkr"];
const EXCHANGE_TIME_ZONE = "America/New_York";
const VANCOUVER_TIME_ZONE = "America/Vancouver";

export function ServicesPage({ mode, onNavigate }: { mode: ServicePageMode; onNavigate: (mode: ServicePageMode) => void }) {
  const [payload, setPayload] = useState<ServicesStatusPayload | null>(null);
  const [selectedPayload, setSelectedPayload] = useState<ServiceStatusPayload | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => new Date());
  const payloadRef = useRef<ServicesStatusPayload | null>(null);
  const serviceId = mode === "dashboard" ? null : mode;

  useEffect(() => {
    payloadRef.current = payload;
  }, [payload]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setError("");
        const next = await api<ServicesStatusPayload>("/api/services/status");
        if (cancelled) return;
        setPayload(next);
        setLoading(false);
      } catch (exc) {
        if (cancelled) return;
        setError(exc instanceof Error ? exc.message : String(exc));
        setLoading(false);
      }
    }
    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!serviceId) {
      setSelectedPayload(null);
      setDetailLoading(false);
      return;
    }
    let cancelled = false;
    let fastInFlight = false;
    let fullInFlight = false;
    async function loadDetail(options: { full: boolean; showLoading?: boolean }) {
      if (options.full ? fullInFlight : fastInFlight) return;
      if (options.full) fullInFlight = true;
      else fastInFlight = true;
      if (options.showLoading) setDetailLoading(true);
      try {
        const query = options.full
          ? "include_database_tables=true&include_recent=true&include_logs=true"
          : "include_database_tables=false&include_recent=false&include_logs=false";
        const next = await api<ServiceStatusPayload>(`/api/services/${serviceId}/status?${query}`);
        if (!cancelled) {
          setSelectedPayload((current) => mergeServiceDetailPayload(next, current, options.full));
        }
      } catch (exc) {
        if (!cancelled) {
          const fallback = payloadRef.current?.services.find((service) => service.registry.id === serviceId) ?? null;
          setSelectedPayload(fallback ? { ...fallback, errors: { ...fallback.errors, detail: exc instanceof Error ? exc.message : String(exc) } } : null);
        }
      } finally {
        if (options.full) fullInFlight = false;
        else fastInFlight = false;
        if (!cancelled && options.showLoading) setDetailLoading(false);
      }
    }
    void loadDetail({ full: true, showLoading: true });
    const fastTimer = window.setInterval(() => void loadDetail({ full: false }), 5000);
    const fullTimer = window.setInterval(() => void loadDetail({ full: true }), 30000);
    return () => {
      cancelled = true;
      window.clearInterval(fastTimer);
      window.clearInterval(fullTimer);
    };
  }, [serviceId]);

  const services = useMemo(() => sortServices(payload?.services ?? []), [payload]);
  const selectedPayloadForMode = selectedPayload?.registry.id === serviceId ? selectedPayload : null;
  const selected = serviceId ? selectedPayloadForMode ?? services.find((service) => service.registry.id === serviceId) ?? null : null;

  return (
    <div className={`services-page ${loading || detailLoading ? "is-page-loading" : ""}`}>
      <section className="services-header">
        <div>
          <span className="page-kicker">Services</span>
          <h1>{selected ? selected.registry.label : "Service Dashboard"}</h1>
          <p>{selected ? selected.registry.description : "Live status, current focus, coverage, and processing state across the running gateway services."}</p>
        </div>
        <div className="services-header-actions">
          <span className="services-refresh-note">Updated {payload?.checked_at_utc ? formatTime(payload.checked_at_utc) : "-"}</span>
          <Button onClick={() => window.location.reload()} variant="secondary"><RefreshCcw size={15} /> Refresh</Button>
        </div>
        <ServicesTopSummary now={now} services={services} />
      </section>
      {selected ? (
        <div className="service-detail-shell">
          <ServiceDetail pageError={error} service={selected} />
        </div>
      ) : (
        <ServicesDashboard services={services} onNavigate={onNavigate} />
      )}
      {loading || detailLoading ? (
        <div className="services-page-loading-overlay" aria-label="Loading service data">
          <Loader2 size={22} />
          <span>{loading ? "Loading service status..." : "Loading service details..."}</span>
        </div>
      ) : null}
    </div>
  );
}

function mergeServiceDetailPayload(next: ServiceStatusPayload, current: ServiceStatusPayload | null, full: boolean): ServiceStatusPayload {
  if (full || current?.registry.id !== next.registry.id) return next;
  return {
    ...next,
    database_tables: current.database_tables ?? next.database_tables,
    logs: current.logs ?? next.logs,
    recent: current.recent ?? next.recent,
  };
}

function ServicesTopSummary({ now, services }: { now: Date; services: ServiceStatusPayload[] }) {
  const counts = countStatuses(services);
  const market = fleetMarketStatus(services);
  const tiles = [
    { label: "ET", value: formatZoneTime(now, "America/New_York"), sub: formatZoneDate(now, "America/New_York"), icon: Clock3, className: "market-time" },
    { label: "Vancouver", value: formatZoneTime(now, "America/Vancouver"), sub: formatZoneDate(now, "America/Vancouver"), icon: MapPin },
    { label: "UTC", value: formatZoneTime(now, "UTC"), sub: formatZoneDate(now, "UTC"), icon: CalendarDays },
    { label: "Market", value: market.status, sub: market.detail, icon: Activity, className: marketTileClass(market.status, market.detail) },
    { label: "Fleet", value: `${counts.online}/${services.length || 0} online`, sub: `${counts.active} active, ${counts.degraded} degraded, ${counts.offline} not started`, icon: RadioTower },
  ];
  return (
    <div className="services-top-summary" aria-label="Service fleet summary">
      {tiles.map((tile) => {
        const Icon = tile.icon;
        return (
          <div className={`services-top-tile ${tile.className ?? ""}`} key={tile.label}>
            <Icon size={16} />
            <span>{tile.label}</span>
            <strong>{tile.value}</strong>
            <small>{tile.sub || "-"}</small>
          </div>
        );
      })}
    </div>
  );
}

function ServicesDashboard({ onNavigate, services }: { onNavigate: (mode: ServicePageMode) => void; services: ServiceStatusPayload[] }) {
  return (
    <>
      <section className="services-card-grid">
        {services.map((service) => (
          <button className={`service-card ${statusInfo(service).className}`} key={service.registry.id} onClick={() => onNavigate(service.registry.id)} type="button">
            <div className="service-card-topline">
              <div className="service-card-title-lockup">
                <ServiceIcon service={service} />
                <span>{displayName(service.registry.kind)}</span>
              </div>
              <ServiceStatusBadge status={service.status} online={service.online} />
            </div>
            <h2>{service.registry.label}</h2>
            <p className="service-card-message">{cardMessage(service)}</p>
            <div className="service-card-facts">
              <ServiceFact label="Endpoint" value={service.registry.base_url} />
              <ServiceFact label="Phase" value={phaseText(service)} />
              <ServiceFact label="Runtime" value={runtimeText(service)} />
              <ServiceFact label="Coverage" value={coverageText(service)} />
            </div>
          </button>
        ))}
      </section>
    </>
  );
}

function ServiceFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong title={value}>{value || "-"}</strong>
    </div>
  );
}

function ServiceDetail({ pageError, service }: { pageError: string; service: ServiceStatusPayload }) {
  const [configOpen, setConfigOpen] = useState(false);
  const snapshot = service.snapshot ?? {};
  const metrics = service.metrics ?? {};
  const runtimeRows = objectRows(snapshot.runtime, metrics);
  const dailyRows = objectRows(snapshot.daily_summary);
  const coverageRows = objectRows(snapshot.coverage);
  const dependencyRows = arrayRows(snapshot.dependencies);
  const sourceRows = arrayRows(snapshot.sources_sinks);
  const taskRows = arrayRows(snapshot.tasks);
  const progressRows = arrayRows(snapshot.task_table_progress);
  const queueRows = arrayRows(snapshot.queues);
  const configuredTableRows = arrayRows(snapshot.configured_tables);
  const recentRows = recentRowsFromPayload(service.recent);
  const focusStatus = statusInfo(service);
  return (
    <>
      <section className="service-primary-grid">
        <Panel className="service-focus-panel" title="">
          <div className={`service-focus ${focusStatus.className}`}>
            <div className="service-focus-top">
              <ServiceStatusBadge status={service.status} online={service.online} />
              <span>{service.checked_at_utc ? `Checked ${formatTime(service.checked_at_utc)}` : "Not checked yet"}</span>
            </div>
            <div className="service-focus-content">
              <strong className="service-focus-phase">{phaseText(service)}</strong>
            </div>
            <div className="service-focus-meta">
              <span className="service-focus-runtime">{runtimeText(service)}</span>
              <button className="service-focus-config-button" onClick={() => setConfigOpen(true)} type="button">
                <Settings2 size={14} />
                Configuration
              </button>
            </div>
            <p className="service-focus-message">{currentMessage(service) || "No current operation message reported."}</p>
          </div>
        </Panel>
        <Panel className="service-database-state-panel" title="Database Table State">
          <ServiceDatabaseTableState service={service} />
        </Panel>
      </section>
      {configOpen ? (
        <Modal className="service-config-modal-panel" onClose={() => setConfigOpen(false)} title={`${service.registry.label} Run Configuration`}>
          <ServiceConfigurationPanel service={service} />
        </Modal>
      ) : null}
      <ServiceWorkPlanPanel service={service} />
      <ServiceErrorLogPanel pageError={pageError} service={service} />
      <Panel title="Coverage">
        <KeyValueList rows={coverageRows.length ? coverageRows : [{ key: "status", value: "not reported" }]} />
      </Panel>
      <section className="service-two-column">
        <Panel title="Runtime Counters"><DataTable rows={runtimeRows} columns={["key", "value"]} empty="No runtime counters reported." /></Panel>
        <Panel title="Daily Summary"><DataTable rows={dailyRows} columns={["key", "value"]} empty="No daily summary reported." /></Panel>
      </section>
      <Panel title="Tasks And Table Progress">
        <DataTable rows={[...taskRows, ...progressRows]} empty="No tasks reported." />
      </Panel>
      <section className="service-two-column">
        <Panel title="Dependencies"><DataTable rows={dependencyRows} empty="No dependencies reported." /></Panel>
        <Panel title="Queues"><DataTable rows={queueRows} empty="No queues reported." /></Panel>
      </section>
      <section className="service-two-column">
        <Panel title="Sources And Sinks"><DataTable rows={sourceRows} empty="No source coverage reported." /></Panel>
        <Panel title="Configured Tables"><DataTable rows={configuredTableRows} empty="No configured tables reported." /></Panel>
      </section>
      <Panel title="Recent Items">
        <DataTable rows={recentRows} empty="No recent items reported." />
      </Panel>
    </>
  );
}

type ServiceWorkRow = {
  detail: string;
  kind: string;
  lastAt: string;
  lastAtMs?: number;
  name: string;
  progress: string;
  reportKind: "live" | "setup";
  rows: string;
  schedule: string;
  status: string;
};

type ServiceWorkGroup = {
  activeCount: number;
  completedCount: number;
  description: string;
  id: string;
  lastAt: string;
  rows: ServiceWorkRow[];
  status: string;
  title: string;
  warningCount: number;
};

type NewsPollHistoryRow = {
  checkedAt: string;
  duplicateRows: number;
  failedRows: number;
  pollAt: string;
  pollRun: number;
  processedRows: number;
  providerRows: number;
  signature: string;
  skippedExisting: number;
  status: string;
  uniqueRows: number;
  wallSeconds: number;
  writtenRows: number;
};

type NewsDailyHistogramDatum = {
  broadOrNoneRows: number;
  bucketUtc: string;
  singleTickerRows: number;
  totalRows: number;
};

type NewsDailyHistogramState = {
  binSeconds: number;
  error: string;
  rows: NewsDailyHistogramDatum[];
  windowEndUtc: string;
  windowStartUtc: string;
};

type NewsHistogramPayload = {
  bin_seconds: number;
  error?: string;
  market_timezone?: string;
  rows: Array<{
    broad_or_none_rows?: number;
    bucket_utc?: string;
    single_ticker_rows?: number;
    total_rows?: number;
  }>;
  source?: string;
  window_end_et?: string;
  window_end_utc?: string;
  window_start_et?: string;
  window_start_utc?: string;
};

function ServiceWorkPlanPanel({ service }: { service: ServiceStatusPayload }) {
  const groups = serviceWorkGroups(service);
  const newsPollHistory = useNewsPollHistory(service);
  const setupRows = serviceSetupRows(service);
  const dependencyRows = setupRows.filter((row) => isPreflightSetupRow(row));
  const contractRows = setupRows.filter((row) => !isPreflightSetupRow(row));
  const liveCounts = groups.reduce(
    (summary, row) => {
      const status = workStatusClass(row.status);
      summary.total += 1;
      if (status === "running") summary.running += 1;
      else if (status === "ok") summary.healthy += 1;
      else if (status === "warn" || status === "error") summary.needsAttention += 1;
      return summary;
    },
    { healthy: 0, needsAttention: 0, running: 0, total: 0 },
  );
  const latestLiveAt = latestWorkTimestamp(groups.flatMap((group) => group.rows));
  const setupProblems = setupRows.filter((row) => ["error", "warn"].includes(workStatusClass(row.status))).length;
  return (
    <Panel className="service-work-plan-panel" title="Service Work Plan">
      <div className="service-work-plan-summary">
        <WorkPlanSummaryItem label="Live Areas" value={String(liveCounts.total)} />
        <WorkPlanSummaryItem label="Active Now" value={String(liveCounts.running)} />
        <WorkPlanSummaryItem label="Last Live Report" value={latestLiveAt || "-"} />
        <WorkPlanSummaryItem label="Setup Issues" value={String(setupProblems)} tone={setupProblems ? "warn" : "ok"} />
      </div>
      <div className="service-work-plan-layout">
        <section className="service-work-live-section">
          <ServiceWorkResponsibilityGrid groups={groups} newsPollHistory={newsPollHistory} service={service} />
        </section>
        <aside className="service-work-static-panel">
          <ServiceCollapsedWorkSection
            description="Provider reachability, auth, storage, ClickHouse, and environment checks. These are setup checks, not active data work."
            rows={dependencyRows}
            title="Preflight"
          />
          <ServiceCollapsedWorkSection
            description="Configured tables and static contracts this dashboard expects the service to maintain or read."
            rows={contractRows}
            title="Setup / Contracts"
          />
        </aside>
      </div>
    </Panel>
  );
}

function ServiceWorkResponsibilityGrid({ groups, newsPollHistory, service }: { groups: ServiceWorkGroup[]; newsPollHistory: NewsPollHistoryRow[]; service: ServiceStatusPayload }) {
  const visibleGroups = groups.filter((group) => group.id !== "other" || group.rows.length);
  return (
    <div className="service-work-responsibility-grid">
      {visibleGroups.map((group) => group.id === "live" && service.registry.id === "news" ? (
        <NewsBenzingaLiveCard group={group} history={newsPollHistory} key={group.id} service={service} />
      ) : (
        <ServiceWorkResponsibilityCard group={group} key={group.id} />
      ))}
    </div>
  );
}

function NewsBenzingaLiveCard({ group, history, service }: { group: ServiceWorkGroup; history: NewsPollHistoryRow[]; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const histogram = useNewsDailyHistogram(service.registry.id === "news");
  const histogramData = histogram.rows;
  const latestPoll = history[0];
  const backgroundPending = numericMetric(metrics, ["background_pending_articles", "publish_pending_rows", "background_queue_size"]);
  return (
    <section className={`service-work-responsibility-card news-live-card ${workStatusClass(group.status)}`}>
      <div className="news-live-card-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${workStatusClass(group.status)}`}>{displayName(group.status || "waiting")}</span>
      </div>
      <NewsDailyHistogram
        binSeconds={histogram.binSeconds}
        data={histogramData}
        error={histogram.error}
        windowEndUtc={histogram.windowEndUtc}
        windowStartUtc={histogram.windowStartUtc}
      />
      <div className="news-live-summary">
        <span><small>Polls</small><strong>{formatCompactNumber(numericMetric(metrics, ["poll_runs"]))}</strong></span>
        <span><small>Last Provider</small><strong>{formatCompactNumber(latestPoll?.providerRows ?? numericMetric(metrics, ["last_cycle_provider_rows"]))}</strong></span>
        <span><small>Last Unique</small><strong>{formatCompactNumber(latestPoll?.uniqueRows ?? numericMetric(metrics, ["last_cycle_unique_news_rows"]))}</strong></span>
        <span><small>Written</small><strong>{formatCompactNumber(latestPoll?.writtenRows ?? numericMetric(metrics, ["last_cycle_written_rows"]))}</strong></span>
        <span><small>Pending</small><strong>{formatCompactNumber(backgroundPending)}</strong></span>
        <span><small>Status</small><strong>{displayName(String(latestPoll?.status || metrics.last_cycle_status || service.status || "-"))}</strong></span>
      </div>
      <NewsPollHistoryTable rows={history} />
    </section>
  );
}

function NewsDailyHistogram({
  binSeconds,
  data,
  error,
  windowEndUtc,
  windowStartUtc,
}: {
  binSeconds: number;
  data: NewsDailyHistogramDatum[];
  error: string;
  windowEndUtc: string;
  windowStartUtc: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const defaultWindow = useMemo(() => defaultNewsHistogramWindow(binSeconds), [binSeconds]);
  const effectiveWindowStartUtc = windowStartUtc || defaultWindow.windowStartUtc;
  const effectiveWindowEndUtc = windowEndUtc || defaultWindow.windowEndUtc;
  const effectiveData = useMemo(
    () => data.length ? elapsedNewsHistogramRows(data, effectiveWindowStartUtc, effectiveWindowEndUtc) : defaultWindow.rows,
    [data, defaultWindow.rows, effectiveWindowEndUtc, effectiveWindowStartUtc],
  );
  const dataRef = useRef<NewsDailyHistogramDatum[]>(effectiveData);
  const binSecondsRef = useRef(binSeconds);
  const windowRef = useRef({ end: effectiveWindowEndUtc, start: effectiveWindowStartUtc });
  const singleSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const broadSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const [hover, setHover] = useState<{ broad: number; et: string; single: number; utc: string; van: string } | null>(null);
  dataRef.current = effectiveData;
  binSecondsRef.current = binSeconds;
  windowRef.current = { end: effectiveWindowEndUtc, start: effectiveWindowStartUtc };

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const chart = createChart(element, {
      autoSize: false,
      height: 144,
      layout: { background: { color: "transparent" }, textColor: "#667085" },
      rightPriceScale: { borderVisible: false, scaleMargins: { bottom: 0.08, top: 0.18 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, timeVisible: false, visible: false },
      grid: { horzLines: { color: "rgba(16,24,40,0.06)" }, vertLines: { color: "rgba(16,24,40,0.04)" } },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScale: false,
      handleScroll: false,
      width: Math.max(280, element.clientWidth),
    });
    const singleSeries = chart.addHistogramSeries({
      color: "#17b26a",
      lastValueVisible: false,
      priceLineVisible: false,
      priceFormat: { type: "volume" },
    });
    const broadSeries = chart.addHistogramSeries({
      color: "#f79009",
      lastValueVisible: false,
      priceLineVisible: false,
      priceFormat: { type: "volume" },
    });
    chartRef.current = chart;
    singleSeriesRef.current = singleSeries;
    broadSeriesRef.current = broadSeries;
    chart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        setHover(null);
        return;
      }
      const timestampSeconds =
        typeof param.time === "number"
          ? param.time
          : typeof param.time === "string"
            ? Math.floor(Date.parse(param.time) / 1000)
            : Date.UTC(param.time.year, param.time.month - 1, param.time.day) / 1000;
      const bucket = newsHistogramBucketForTime(dataRef.current, timestampSeconds, binSecondsRef.current);
      if (!bucket) {
        setHover(null);
        return;
      }
      setHover({
        broad: bucket.broadOrNoneRows,
        et: formatZoneDateTime(new Date(Date.parse(bucket.bucketUtc)), EXCHANGE_TIME_ZONE),
        single: bucket.singleTickerRows,
        utc: formatUtcDateTime(bucket.bucketUtc),
        van: formatZoneDateTime(new Date(Date.parse(bucket.bucketUtc)), VANCOUVER_TIME_ZONE),
      });
    });
    const resizeObserver = new ResizeObserver((entries) => {
      const width = Math.floor(entries[0]?.contentRect.width ?? element.clientWidth);
      chart.applyOptions({ width: Math.max(280, width) });
      setNewsHistogramVisibleRange(chart, windowRef.current.start, windowRef.current.end);
    });
    resizeObserver.observe(element);
    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      singleSeriesRef.current = null;
      broadSeriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const singleSeries = singleSeriesRef.current;
    const broadSeries = broadSeriesRef.current;
    const chart = chartRef.current;
    if (!singleSeries || !broadSeries || !chart) return;
    const offset = Math.max(1, Math.floor(binSeconds / 3));
    singleSeries.setData(effectiveData.map((row) => ({ time: newsBucketChartTime(row.bucketUtc, offset), value: row.singleTickerRows })));
    broadSeries.setData(effectiveData.map((row) => ({ time: newsBucketChartTime(row.bucketUtc, offset * 2), value: row.broadOrNoneRows })));
    setNewsHistogramVisibleRange(chart, effectiveWindowStartUtc, effectiveWindowEndUtc);
  }, [binSeconds, effectiveData, effectiveWindowEndUtc, effectiveWindowStartUtc]);

  const singleTotal = effectiveData.reduce((sum, row) => sum + row.singleTickerRows, 0);
  const broadTotal = effectiveData.reduce((sum, row) => sum + row.broadOrNoneRows, 0);
  const total = singleTotal + broadTotal;
  return (
    <div className="news-live-histogram">
      <div className="news-live-histogram-label">
        <span>Today from DB / 24h fixed axis / {binSeconds}s bins</span>
        <div className="news-live-histogram-legend">
          <span className="single">1 ticker <strong>{formatCompactNumber(singleTotal)}</strong></span>
          <span className="broad">0 or 2+ tickers <strong>{formatCompactNumber(broadTotal)}</strong></span>
          <span>total <strong>{formatCompactNumber(total)}</strong></span>
        </div>
      </div>
      {hover ? (
        <div className="news-live-histogram-hover">
          <strong>{hover.et}</strong>
          <span>VAN {hover.van}</span>
          <span>UTC {hover.utc}</span>
          <span>1 ticker {formatCompactNumber(hover.single)}</span>
          <span>0 or 2+ {formatCompactNumber(hover.broad)}</span>
        </div>
      ) : null}
      {error ? <div className="news-live-histogram-error">{error}</div> : null}
      <div className="news-live-histogram-chart" ref={containerRef} />
    </div>
  );
}

function NewsPollHistoryTable({ rows }: { rows: NewsPollHistoryRow[] }) {
  return (
    <div className="news-poll-history-table-wrap">
      <table className="news-poll-history-table">
        <thead>
          <tr>
            <th>Poll</th>
            <th>Time</th>
            <th>Status</th>
            <th>Provider</th>
            <th>Unique</th>
            <th>Duplicate</th>
            <th>Written</th>
            <th>Skipped</th>
            <th>Failed</th>
            <th>Sec</th>
          </tr>
        </thead>
        <tbody>
          {(rows.length ? rows : [null]).map((row, index) => row ? (
            <tr className={workStatusClass(row.status)} key={row.signature}>
              <td>{formatCompactNumber(row.pollRun)}</td>
              <td title={row.pollAt}>{formatLogTime(row.pollAt)}</td>
              <td><span className={`service-work-mini-status ${workStatusClass(row.status)}`}>{displayName(row.status)}</span></td>
              <td>{formatCompactNumber(row.providerRows)}</td>
              <td>{formatCompactNumber(row.uniqueRows)}</td>
              <td>{formatCompactNumber(row.duplicateRows)}</td>
              <td>{formatCompactNumber(row.writtenRows)}</td>
              <td>{formatCompactNumber(row.skippedExisting)}</td>
              <td>{formatCompactNumber(row.failedRows)}</td>
              <td>{formatCompactNumber(row.wallSeconds)}</td>
            </tr>
          ) : (
            <tr key={`empty-${index}`}>
              <td colSpan={10}>No poll has been observed by this dashboard yet.</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ServiceWorkResponsibilityCard({ group }: { group: ServiceWorkGroup }) {
  const latestRow = groupPrimaryRow(group);
  return (
    <section className={`service-work-responsibility-card ${workStatusClass(group.status)}`}>
      <div className="service-work-responsibility-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${workStatusClass(group.status)}`}>{displayName(group.status || "waiting")}</span>
      </div>
      <div className="service-work-responsibility-metrics">
        <span><small>Last</small><strong>{group.lastAt || "-"}</strong></span>
        <span><small>Active</small><strong>{group.activeCount}</strong></span>
        <span><small>Done</small><strong>{group.completedCount}</strong></span>
        <span><small>Warn</small><strong>{group.warningCount}</strong></span>
        <span className="wide" title={latestRow.detail}><small>Current</small><strong>{latestRow.name}</strong></span>
      </div>
      <ServiceWorkSubtaskTable rows={group.rows} title={group.title} />
    </section>
  );
}

function ServiceWorkSubtaskTable({ rows, title }: { rows: ServiceWorkRow[]; title: string }) {
  const tableRows = rows.length ? rows : [{ detail: "No subtask report has been received in the current service snapshot.", kind: "service", lastAt: "-", name: title, progress: "-", reportKind: "live" as const, rows: "-", schedule: "-", status: "not reported" }];
  return (
    <div className="service-work-subtask-table-wrap">
      <table className="service-work-subtask-table">
        <thead>
          <tr>
            <th>Subtask</th>
            <th>Status</th>
            <th>Last</th>
            <th>Progress</th>
            <th>Rows</th>
            <th>Readable Detail</th>
          </tr>
        </thead>
        <tbody>
          {tableRows.map((row, index) => (
            <tr className={workStatusClass(row.status)} key={`${row.kind}-${row.name}-${index}`}>
              <td>
                <strong title={row.name}>{row.name}</strong>
                <span>{displayName(row.kind)}</span>
              </td>
              <td><span className={`service-work-mini-status ${workStatusClass(row.status)}`}>{displayName(row.status || "waiting")}</span></td>
              <td>{row.lastAt}</td>
              <td>{row.progress}</td>
              <td>{row.rows}</td>
              <td title={row.detail}>{row.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ServiceCollapsedWorkSection({ description, rows, title }: { description: string; rows: ServiceWorkRow[]; title: string }) {
  const issueCount = rows.filter((row) => ["error", "warn"].includes(workStatusClass(row.status))).length;
  const visibleRows = rows.slice(0, 10);
  const hiddenCount = Math.max(0, rows.length - visibleRows.length);
  return (
    <details className={`service-work-collapsed ${issueCount ? "has-issues" : ""}`}>
      <summary>
        <span>
          <strong>{title}</strong>
          <small>{description}</small>
        </span>
        <em>{rows.length} rows / {issueCount} issues</em>
      </summary>
      <div className="service-work-static-list">
        {(visibleRows.length ? visibleRows : [{ detail: "No rows reported for this setup area.", kind: "setup", lastAt: "-", name: title, progress: "-", reportKind: "setup" as const, rows: "-", schedule: "-", status: "not reported" }]).map((row, index) => (
          <div className={`service-work-static-row ${workStatusClass(row.status)}`} key={`${row.kind}-${row.name}-${index}`}>
            <div>
              <strong title={row.name}>{row.name}</strong>
              <span>{displayName(row.kind)}</span>
              <p title={row.detail}>{row.detail}</p>
            </div>
            <span className={`service-work-mini-status ${workStatusClass(row.status)}`}>{displayName(row.status)}</span>
          </div>
        ))}
        {hiddenCount ? <div className="service-work-more">+ {hiddenCount} more row{hiddenCount === 1 ? "" : "s"}</div> : null}
      </div>
    </details>
  );
}

function groupPrimaryRow(group: ServiceWorkGroup): ServiceWorkRow {
  const sortedRows = [...group.rows].sort((a, b) => workStatusRank(a.status) - workStatusRank(b.status) || (b.lastAtMs ?? 0) - (a.lastAtMs ?? 0));
  return sortedRows[0] ?? { detail: "No subtask report received in the current snapshot.", kind: "service", lastAt: "-", name: "No live report", progress: "-", reportKind: "live", rows: "-", schedule: "-", status: "not reported" };
}

function useNewsPollHistory(service: ServiceStatusPayload) {
  const [history, setHistory] = useState<NewsPollHistoryRow[]>([]);
  useEffect(() => {
    if (service.registry.id !== "news") {
      setHistory([]);
      return;
    }
    const logRows = newsPollHistoryRowsFromLogs(service);
    const row = newsPollHistoryRow(service);
    const incoming = row ? [row, ...logRows] : logRows;
    if (!incoming.length) return;
    setHistory((current) => {
      const merged = mergeNewsPollHistory(incoming, current);
      return historiesEqual(merged, current) ? current : merged;
    });
  }, [service]);
  return history;
}

function newsPollHistoryRowsFromLogs(service: ServiceStatusPayload): NewsPollHistoryRow[] {
  return (service.logs?.rows ?? [])
    .filter((row) => row.event === "poll_completed" && isRecord(row.fields))
    .map((row) => newsPollHistoryRowFromLog(row, service.checked_at_utc))
    .filter((row): row is NewsPollHistoryRow => Boolean(row));
}

function newsPollHistoryRowFromLog(row: ServiceRuntimeLogRow, checkedAt: string): NewsPollHistoryRow | null {
  const fields = row.fields;
  if (!isRecord(fields)) return null;
  const pollId = stringMetric(fields, ["poll_id"]);
  const pollRunMatch = pollId.match(/(\d+)$/);
  const pollRun = pollRunMatch ? Number(pollRunMatch[1]) : 0;
  const pollAt = row.ts_utc || stringMetric(fields, ["start_utc"]) || checkedAt;
  const providerRows = numericMetric(fields, ["provider_rows"]);
  const processedRows = numericMetric(fields, ["processed_rows"]);
  const uniqueRows = numericMetric(fields, ["unique_news_rows"]);
  const duplicateRows = numericMetric(fields, ["duplicate_news_rows", "input_duplicate_ids_total"]);
  const writtenRows = numericMetric(fields, ["normalized_rows_inserted"]);
  const skippedExisting = numericMetric(fields, ["skipped_existing"]);
  const failedRows = numericMetric(fields, ["failed_rows"]);
  const wallSeconds = numericMetric(fields, ["wall_seconds"]);
  const status = stringMetric(fields, ["status"]) || row.level || "observed";
  const signature = [
    pollId || pollRun,
    pollAt,
    providerRows,
    processedRows,
    uniqueRows,
    writtenRows,
    skippedExisting,
    failedRows,
    status,
  ].join("|");
  return {
    checkedAt,
    duplicateRows,
    failedRows,
    pollAt,
    pollRun,
    processedRows,
    providerRows,
    signature,
    skippedExisting,
    status,
    uniqueRows,
    wallSeconds,
    writtenRows,
  };
}

function mergeNewsPollHistory(...sets: NewsPollHistoryRow[][]) {
  const bySignature = new Map<string, NewsPollHistoryRow>();
  for (const rows of sets) {
    for (const row of rows) bySignature.set(row.signature, row);
  }
  return Array.from(bySignature.values())
    .sort((a, b) => (Date.parse(b.pollAt) || 0) - (Date.parse(a.pollAt) || 0))
    .slice(0, 50);
}

function historiesEqual(left: NewsPollHistoryRow[], right: NewsPollHistoryRow[]) {
  if (left.length !== right.length) return false;
  return left.every((row, index) => row.signature === right[index]?.signature);
}

function useNewsDailyHistogram(enabled: boolean) {
  const [payload, setPayload] = useState<NewsDailyHistogramState>(() => defaultNewsHistogramWindow(1500));
  useEffect(() => {
    if (!enabled) {
      setPayload(defaultNewsHistogramWindow(1500));
      return undefined;
    }
    let cancelled = false;
    async function load() {
      try {
        const response = await api<NewsHistogramPayload>("/api/services/news/histogram");
        if (cancelled) return;
        const binSeconds = Number(response.bin_seconds || 1500);
        const defaultWindow = defaultNewsHistogramWindow(binSeconds);
        const windowStartUtc = response.window_start_utc || defaultWindow.windowStartUtc;
        const windowEndUtc = response.window_end_utc || defaultWindow.windowEndUtc;
        setPayload({
          binSeconds,
          error: response.error || "",
          rows: elapsedNewsHistogramRows(
            (response.rows || [])
              .map((row) => ({
                broadOrNoneRows: Number(row.broad_or_none_rows || 0),
                bucketUtc: String(row.bucket_utc || ""),
                singleTickerRows: Number(row.single_ticker_rows || 0),
                totalRows: Number(row.total_rows || 0),
              }))
              .filter((row) => row.bucketUtc),
            windowStartUtc,
            windowEndUtc,
          ),
          windowEndUtc,
          windowStartUtc,
        });
      } catch (exc) {
        if (cancelled) return;
        setPayload({ ...defaultNewsHistogramWindow(1500), error: exc instanceof Error ? exc.message : String(exc) });
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled]);
  return payload;
}

function newsPollHistoryRow(service: ServiceStatusPayload): NewsPollHistoryRow | null {
  const metrics = serviceMetricsRecord(service);
  const pollRun = numericMetric(metrics, ["poll_runs"]);
  if (!pollRun) return null;
  const pollAt = stringMetric(metrics, ["last_poll_at_utc"]) || service.checked_at_utc;
  const providerRows = numericMetric(metrics, ["last_cycle_provider_rows"]);
  const processedRows = numericMetric(metrics, ["last_cycle_processed_rows"]);
  const uniqueRows = numericMetric(metrics, ["last_cycle_unique_news_rows"]);
  const duplicateRows = numericMetric(metrics, ["last_cycle_duplicate_news_rows"]);
  const writtenRows = numericMetric(metrics, ["last_cycle_written_rows"]);
  const skippedExisting = numericMetric(metrics, ["last_cycle_skipped_existing"]);
  const failedRows = numericMetric(metrics, ["last_cycle_failed_rows"]);
  const wallSeconds = numericMetric(metrics, ["last_cycle_wall_seconds"]);
  const status = stringMetric(metrics, ["last_cycle_status"]) || "observed";
  const signature = [
    pollRun,
    pollAt,
    providerRows,
    processedRows,
    uniqueRows,
    writtenRows,
    skippedExisting,
    failedRows,
    status,
  ].join("|");
  return {
    checkedAt: service.checked_at_utc,
    duplicateRows,
    failedRows,
    pollAt,
    pollRun,
    processedRows,
    providerRows,
    signature,
    skippedExisting,
    status,
    uniqueRows,
    wallSeconds,
    writtenRows,
  };
}

function serviceMetricsRecord(service: ServiceStatusPayload) {
  const serviceSpecific = service.snapshot?.service_specific;
  const runtime = service.snapshot?.runtime;
  return {
    ...(isRecord(runtime) ? runtime : {}),
    ...(isRecord(service.metrics) ? service.metrics : {}),
    ...(isRecord(serviceSpecific) ? serviceSpecific : {}),
  };
}

function numericMetric(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
}

function stringMetric(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value);
  }
  return "";
}

function newsBucketChartTime(bucketUtc: string, offsetSeconds: number): Time {
  const parsed = Date.parse(bucketUtc);
  const seconds = Number.isFinite(parsed) ? Math.floor(parsed / 1000) + offsetSeconds : Math.floor(Date.now() / 1000);
  return seconds as Time;
}

function setNewsHistogramVisibleRange(chart: IChartApi, windowStartUtc: string, windowEndUtc: string) {
  const first = Date.parse(windowStartUtc);
  const last = Date.parse(windowEndUtc);
  if (!Number.isFinite(first) || !Number.isFinite(last) || last <= first) return;
  chart.timeScale().setVisibleRange({
    from: Math.floor(first / 1000) as Time,
    to: Math.floor(last / 1000) as Time,
  });
}

function defaultNewsHistogramWindow(binSeconds: number): NewsDailyHistogramState {
  const { day, month, year } = exchangeDateParts(new Date());
  const start = zonedDateTimeToUtc(year, month, day, 0, 0, EXCHANGE_TIME_ZONE);
  const nextDay = nextCalendarDate(year, month, day);
  const end = zonedDateTimeToUtc(nextDay.year, nextDay.month, nextDay.day, 0, 0, EXCHANGE_TIME_ZONE);
  const totalBins = Math.max(0, Math.ceil((end.getTime() - start.getTime()) / (binSeconds * 1000)));
  const elapsedBins = Math.max(0, Math.min(totalBins, Math.ceil((Date.now() - start.getTime()) / (binSeconds * 1000))));
  const rows = Array.from({ length: elapsedBins }, (_, index) => {
    const bucketUtc = new Date(start.getTime() + index * binSeconds * 1000).toISOString();
    return { broadOrNoneRows: 0, bucketUtc, singleTickerRows: 0, totalRows: 0 };
  });
  return {
    binSeconds,
    error: "",
    rows,
    windowEndUtc: end.toISOString(),
    windowStartUtc: start.toISOString(),
  };
}

function elapsedNewsHistogramRows(rows: NewsDailyHistogramDatum[], windowStartUtc: string, windowEndUtc: string) {
  const start = Date.parse(windowStartUtc);
  const end = Date.parse(windowEndUtc);
  const cutoff = Math.min(Number.isFinite(end) ? end : Date.now(), Date.now());
  return rows.filter((row) => {
    const bucket = Date.parse(row.bucketUtc);
    if (!Number.isFinite(bucket)) return false;
    if (Number.isFinite(start) && bucket < start) return false;
    if (Number.isFinite(end) && bucket >= end) return false;
    if (bucket >= cutoff) return false;
    return row.totalRows > 0 || row.singleTickerRows > 0 || row.broadOrNoneRows > 0;
  });
}

function nextCalendarDate(year: number, month: number, day: number) {
  const value = new Date(Date.UTC(year, month - 1, day + 1));
  return { day: value.getUTCDate(), month: value.getUTCMonth() + 1, year: value.getUTCFullYear() };
}

function exchangeDateParts(value: Date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    month: "2-digit",
    timeZone: EXCHANGE_TIME_ZONE,
    year: "numeric",
  }).formatToParts(value);
  const part = (type: string) => Number(parts.find((item) => item.type === type)?.value || "0");
  return { day: part("day"), month: part("month"), year: part("year") };
}

function zonedDateTimeToUtc(year: number, month: number, day: number, hour: number, minute: number, timeZone: string) {
  const target = Date.UTC(year, month - 1, day, hour, minute, 0, 0);
  let utc = target;
  for (let index = 0; index < 3; index += 1) {
    const parts = new Intl.DateTimeFormat("en-US", {
      day: "2-digit",
      hour: "2-digit",
      hourCycle: "h23",
      minute: "2-digit",
      month: "2-digit",
      second: "2-digit",
      timeZone,
      year: "numeric",
    }).formatToParts(new Date(utc));
    const part = (type: string) => Number(parts.find((item) => item.type === type)?.value || "0");
    const asUtc = Date.UTC(part("year"), part("month") - 1, part("day"), part("hour"), part("minute"), part("second"), 0);
    utc += target - asUtc;
  }
  return new Date(utc);
}

function newsHistogramBucketForTime(rows: NewsDailyHistogramDatum[], timestampSeconds: number, binSeconds: number) {
  if (!rows.length) return null;
  for (const row of rows) {
    const parsed = Date.parse(row.bucketUtc);
    if (!Number.isFinite(parsed)) continue;
    const bucketStart = Math.floor(parsed / 1000);
    if (timestampSeconds >= bucketStart && timestampSeconds < bucketStart + binSeconds) return row;
  }
  return null;
}

function WorkPlanSummaryItem({ label, tone = "", value }: { label: string; tone?: string; value: string }) {
  return (
    <div className={tone ? `service-work-plan-summary-item ${tone}` : "service-work-plan-summary-item"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ServiceDatabaseTableState({ service }: { service: ServiceStatusPayload }) {
  const rows = service.database_tables?.rows ?? [];
  const error = service.database_tables?.error || "";
  const [preview, setPreview] = useState<ServiceTablePreviewPayload | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const years = serviceTableStateYears();
  async function openPreview(row: ServiceDatabaseTableRow) {
    if (!row.database || !row.table || row.database === "-" || row.table === "-") return;
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(true);
    try {
      const payload = await api<ServiceTablePreviewPayload>(`/api/services/${service.registry.id}/tables/${encodeURIComponent(row.database)}/${encodeURIComponent(row.table)}/preview?limit=20`);
      setPreview(payload);
    } catch (exc) {
      setPreviewError(exc instanceof Error ? exc.message : String(exc));
      setPreview({ database: row.database, limit: 20, rows: [], table: row.table });
    } finally {
      setPreviewLoading(false);
    }
  }
  if (error && !rows.length) {
    return (
      <div className="service-db-state-empty error">
        <strong>Database table state unavailable</strong>
        <span>{error}</span>
      </div>
    );
  }
  if (!rows.length) {
    return (
      <div className="service-db-state-empty">
        <strong>No direct database table contract reported.</strong>
        <span>This service has no table state configured for the dashboard.</span>
      </div>
    );
  }
  return (
    <>
      <div className="service-db-state-wrap">
        <table className="service-db-state-table">
          <colgroup>
            <col className="service-db-state-col-status" />
            <col className="service-db-state-col-role" />
            <col className="service-db-state-col-table" />
            <col className="service-db-state-col-latest" />
            <col className="service-db-state-col-count" />
            <col className="service-db-state-col-count" />
            <col className="service-db-state-col-count" />
            <col className="service-db-state-col-count" />
            {years.map((year) => <col className="service-db-state-col-year" key={year} />)}
          </colgroup>
          <thead>
            <tr>
              <th>Status</th>
              <th>Role</th>
              <th>Table</th>
              <th>Latest</th>
              <th>Total</th>
              <th>Today</th>
              <th>7d</th>
              <th>30d</th>
              {years.map((year) => <th key={year}>{year}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr className={`service-db-state-row ${tableStateClass(row.status)}`} key={`${row.database}.${row.table}.${index}`} onClick={() => void openPreview(row)}>
                <td><span className="service-db-state-status">{displayName(row.status || "unknown")}</span></td>
                <td title={row.role || ""}>{row.role || "-"}</td>
                <td title={`${row.database || "-"}.${row.table || "-"}${row.time_column && row.time_column !== "-" ? ` by ${row.time_column}` : ""}`}>
                  <span className={`service-db-name ${databaseClass(row.database)}`}>{row.database || "-"}</span>
                  <span className="service-db-dot">.</span>
                  <span className="service-db-table-name">{row.table || "-"}</span>
                </td>
                <td title={row.latest_update || ""}>{shortTableTimestamp(row.latest_update)}</td>
                <td className="service-db-total-cell">{row.rows || "-"}</td>
                <td className="service-db-today-cell">{row.rows_today || "-"}</td>
                <td className="service-db-muted-count-cell">{row.rows_last_week || "-"}</td>
                <td className="service-db-muted-count-cell">{row.rows_last_month || "-"}</td>
                {years.map((year) => <td className="service-db-muted-count-cell" key={year}>{row[`rows_${year}`] || "-"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {error ? <p className="service-db-state-error">{error}</p> : null}
      </div>
      {preview || previewLoading ? (
        <ServiceTablePreviewModal error={previewError} loading={previewLoading} onClose={() => { setPreview(null); setPreviewError(""); }} payload={preview} service={service} />
      ) : null}
    </>
  );
}

function ServiceTablePreviewModal({ error, loading, onClose, payload, service }: { error: string; loading: boolean; onClose: () => void; payload: ServiceTablePreviewPayload | null; service: ServiceStatusPayload }) {
  const title = payload ? `${payload.database}.${payload.table}` : "Table Preview";
  const subtitle = payload?.order_by ? `Latest ${payload.limit} rows ordered by ${payload.order_by}` : `Latest ${payload?.limit ?? 20} rows`;
  return (
    <Modal className="service-table-preview-modal-panel" onClose={onClose} title={`${service.registry.label} Table Preview`}>
      <div className="service-table-preview">
        <div className="service-table-preview-header">
          <div>
            <span className="service-table-preview-kicker">Direct ClickHouse Preview</span>
            <h3>{title}</h3>
            <p>{subtitle}</p>
          </div>
          {loading ? <span className="service-table-preview-loading">Loading...</span> : null}
        </div>
        {error ? <div className="service-table-preview-error">{error}</div> : null}
        <DataTable empty={loading ? "Loading table rows..." : "No preview rows returned."} fitToContent rows={payload?.rows ?? []} title="Last 20 Rows" />
      </div>
    </Modal>
  );
}

function ServiceErrorLogPanel({ pageError, service }: { pageError: string; service: ServiceStatusPayload }) {
  const items = collectErrorLogItems(pageError, service);
  const [statusFilter, setStatusFilter] = useState<ServiceLogStatusFilter>("all");
  const [selectedLog, setSelectedLog] = useState<ServiceLogItem | null>(null);
  const filteredItems = statusFilter === "all" ? items : items.filter((item) => item.status === statusFilter);
  const activeItems = items.filter((item) => item.status === "active" || item.status === "retrying");
  const logPath = service.logs?.path || "";
  const logError = service.logs?.error || "";
  const tableRows = filteredItems.length ? filteredItems : [{ detail: "No log rows match the selected status filter.", key: "service", status: "clear" as const, title: "No matching rows" }];
  return (
    <Panel title="Errors And Logs">
      <div className={`service-log-panel ${activeItems.length ? "has-active" : ""}`}>
        <div className="service-log-summary">
          <ServiceStatusBadge online={service.online} status={activeItems.length ? "degraded" : "running"} />
          <div>
            <strong>{items.length ? `${items.length} log row${items.length === 1 ? "" : "s"} loaded` : "No service log rows reported"}</strong>
            <p>
              {logPath ? `Source: ${logPath}` : "No saved runtime log path was reported by this service."}
              {logError ? ` (${logError})` : ""}
            </p>
          </div>
        </div>
        <div className="service-log-filter" aria-label="Filter service logs by status">
          {logStatusFilterOptions(items).map((option) => (
            <button
              className={statusFilter === option.status ? "active" : ""}
              key={option.status}
              onClick={() => setStatusFilter(option.status)}
              type="button"
            >
              <span>{displayName(option.status)}</span>
              <strong>{option.count}</strong>
            </button>
          ))}
        </div>
        <div className="service-log-table-wrap">
          <table className="service-log-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Status</th>
                <th>Source</th>
                <th>Event</th>
                <th>Message</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map((item, index) => (
                <tr
                  className={`service-log-row ${item.status}`}
                  key={`${item.key}-${index}`}
                  onClick={() => setSelectedLog(item)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedLog(item);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <td className="service-log-time" title={item.time || item.meta || ""}>{item.time || "-"}</td>
                  <td><span className={`service-log-status ${item.status}`}>{displayName(item.status)}</span></td>
                  <td title={item.source || item.meta || ""}>{item.source || "-"}</td>
                  <td title={displayName(item.event || item.key)}>{displayName(item.event || item.key)}</td>
                  <td title={item.title}>{item.title}</td>
                  <td title={item.detail}>{item.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {selectedLog ? (
        <Modal className="service-log-detail-modal-panel" onClose={() => setSelectedLog(null)} title="Service Log Row">
          <ServiceLogDetailModal item={selectedLog} />
        </Modal>
      ) : null}
    </Panel>
  );
}

function ServiceLogDetailModal({ item }: { item: ServiceLogItem }) {
  const detailRows = unpackLogDetail(item.detail);
  const rows = [
    { key: "time", value: item.time || "-" },
    { key: "status", value: displayName(item.status) },
    { key: "source", value: item.source || "-" },
    { key: "event", value: displayName(item.event || item.key) },
    { key: "message", value: item.title || "-" },
    { key: "metadata", value: item.meta || "-" },
    { key: "row_key", value: item.key || "-" },
  ];
  return (
    <div className="service-log-detail">
      <div className={`service-log-detail-status ${item.status}`}>
        <span>{displayName(item.status)}</span>
        <strong>{item.title || displayName(item.event || item.key)}</strong>
      </div>
      <dl className="service-log-detail-grid">
        {rows.map((row) => (
          <div className={row.key === "detail" || row.key === "message" ? "wide" : ""} key={row.key}>
            <dt>{displayName(row.key)}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
      <section className="service-log-detail-fields">
        <div className="service-log-detail-section-title">
          <span>Detail Fields</span>
          <strong>{detailRows.length}</strong>
        </div>
        <dl className="service-log-detail-grid">
          {(detailRows.length ? detailRows : [{ key: "detail", value: item.detail || "-" }]).map((row) => (
            <div className="wide" key={row.key}>
              <dt>{displayName(row.key)}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

function ServiceConfigurationPanel({ service }: { service: ServiceStatusPayload }) {
  const groups = configurationGroups(service);
  const totalSettings = groups.reduce((total, group) => total + group.rows.length, 0);
  const findValue = (patterns: RegExp[]) => {
    for (const group of groups) {
      for (const row of group.rows) {
        if (patterns.some((pattern) => pattern.test(row.key.toLowerCase()))) return formatValue(row.key, row.value);
      }
    }
    return "-";
  };
  return (
    <div className="service-config-panel">
      <div className="service-config-summary">
        <ConfigSummaryItem label="Service" value={service.registry.label} />
        <ConfigSummaryItem label="Mode" value={findValue([/mode/, /profile/, /execute/, /daemon/])} />
        <ConfigSummaryItem label="Database" value={findValue([/database/, /clickhouse/])} />
        <ConfigSummaryItem label="Settings" value={String(totalSettings)} />
      </div>
      <div className="service-config-sections">
        {groups.map((group) => (
          <ConfigGroupView group={group} key={group.title} />
        ))}
      </div>
    </div>
  );
}

function ConfigSummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong title={value}>{value || "-"}</strong>
    </div>
  );
}

function ConfigGroupView({ group }: { group: ConfigGroup }) {
  return (
    <section className="service-config-group">
      <div className="service-config-group-header">
        <h3>{group.title}</h3>
        <span>{group.rows.length} setting{group.rows.length === 1 ? "" : "s"}</span>
      </div>
      <div className="service-config-items">
        {group.rows.map((item) => (
          <ConfigItemView item={item} key={item.key} />
        ))}
      </div>
    </section>
  );
}

function ConfigItemView({ item }: { item: ConfigItem }) {
  const value = formatValue(item.key, item.value);
  const valueType = typeof item.value === "boolean" ? "boolean" : typeof item.value === "number" ? "number" : value.length > 48 ? "long" : "text";
  return (
    <div className={`service-config-item ${valueType}`}>
      <span>{displayName(item.key)}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function Panel({ children, className = "", title }: { children: ReactNode; className?: string; title: string }) {
  return (
    <section className={`service-panel ${className}`}>
      {title ? (
        <div className="service-panel-header">
          <h2>{title}</h2>
        </div>
      ) : null}
      {children}
    </section>
  );
}

function KeyValueList({ rows }: { rows: Array<{ key: string; value: unknown }> }) {
  return (
    <dl className="service-key-values">
      {rows.slice(0, 8).map((row) => (
        <div key={row.key}>
          <dt>{displayName(row.key)}</dt>
          <dd>{formatValue(row.key, row.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

type ConfigItem = {
  key: string;
  value: unknown;
};

type ConfigGroup = {
  rows: ConfigItem[];
  title: string;
};

type ServiceLogItem = {
  detail: string;
  event?: string;
  key: string;
  meta?: string;
  occurredAtMs?: number;
  source?: string;
  status: "active" | "clear" | "log" | "resolved" | "retrying";
  time?: string;
  title: string;
};

type ServiceLogStatusFilter = ServiceLogItem["status"] | "all";

function collectErrorLogItems(pageError: string, service: ServiceStatusPayload) {
  const items: ServiceLogItem[] = [];
  if (pageError) items.push({ detail: pageError, key: "dashboard_api", status: "active", title: "Dashboard API error" });
  for (const record of runtimeLogRows(service.logs)) items.push(record);
  if (service.logs?.error) items.push({ detail: service.logs.error, key: "runtime_log", status: "retrying", title: "Runtime log read error" });

  for (const [key, value] of Object.entries(service.errors ?? {})) {
    if (isEmptyErrorValue(value)) continue;
    items.push({ detail: formatValue(key, value), key, status: "active", title: "Service endpoint error" });
  }

  const errorState = isRecord(service.snapshot?.error_state) ? service.snapshot.error_state : {};
  const hasCanonicalErrorState = Object.keys(errorState).length > 0;
  for (const record of errorRecordRows(errorState.latest_active_errors, "active")) items.push(record);
  for (const record of errorRecordRows(errorState.latest_resolved_errors, "resolved")) items.push(record);

  const serviceSpecific = isRecord(service.snapshot?.service_specific) ? service.snapshot.service_specific : {};
  const lastErrorStatus = String(serviceSpecific.last_error_status ?? service.metrics?.last_error_status ?? "").toLowerCase();
  const warningState = isRecord(service.snapshot?.warnings_errors) ? service.snapshot.warnings_errors : {};
  for (const [key, value] of Object.entries(warningState)) {
    if (isEmptyErrorValue(value)) continue;
    const status = key === "last_error" && lastErrorStatus === "resolved" ? "resolved" : "active";
    items.push({ detail: formatValue(key, value), key, status, title: displayName(key) });
  }

  if (!hasCanonicalErrorState) {
    for (const row of errorLikePayloadRows(service.snapshot?.service_specific, "service_specific")) items.push(row);
    for (const row of errorLikePayloadRows(service.metrics, "metrics")) items.push(row);
  }

  for (const row of nonZeroErrorCounters(errorState)) items.push(row);
  const deduped = sortLogItems(dedupeLogItems(items));
  if (!deduped.length) deduped.push({ detail: "No errors or warnings reported.", key: "service", status: "clear", title: "Clear" });
  return deduped;
}

function errorRecordRows(value: unknown, fallbackStatus: "active" | "resolved"): ServiceLogItem[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((record) => {
    const rawStatus = String(record.status || fallbackStatus).toLowerCase();
    const status = rawStatus.includes("retry") ? "retrying" : rawStatus.includes("resolved") ? "resolved" : fallbackStatus;
    const severity = String(record.severity || record.category || "error");
    const title = String(record.message || record.safe_detail || record.error_id || "Service error");
    const rawTime = String(record.last_seen_utc || record.resolved_at_utc || record.created_at_utc || record.ts_utc || "");
    const metaParts = [
      record.phase ? `phase=${record.phase}` : "",
      record.task ? `task=${record.task}` : "",
      record.provider ? `provider=${record.provider}` : "",
      record.table ? `table=${record.table}` : "",
      record.item_id ? `item=${record.item_id}` : "",
      record.last_seen_utc ? `last=${record.last_seen_utc}` : "",
      record.resolved_at_utc ? `resolved=${record.resolved_at_utc}` : "",
    ].filter(Boolean);
    return {
      detail: String(record.safe_detail || record.message || record.error_id || "-"),
      key: severity,
      meta: metaParts.join("  "),
      occurredAtMs: parseLogTime(rawTime),
      source: String(record.provider || record.phase || "service"),
      status,
      time: rawTime ? formatLogTime(rawTime) : "",
      title,
    };
  });
}

function nonZeroErrorCounters(errorState: Record<string, unknown>): ServiceLogItem[] {
  const counterKeys = ["active_critical_count", "active_error_count", "active_warning_count", "retrying_count", "retry_exhausted_count", "manual_action_count"];
  return counterKeys.flatMap((key) => {
    const value = Number(errorState[key] ?? 0);
    if (!Number.isFinite(value) || value <= 0) return [];
    return [{
      detail: `${displayName(key)} = ${formatCompactNumber(value)}`,
      key,
      status: key === "retrying_count" ? "retrying" as const : "active" as const,
      title: "Non-zero error counter without detailed records",
    }];
  });
}

function errorLikePayloadRows(value: unknown, source: string): ServiceLogItem[] {
  if (!isRecord(value)) return [];
  const rows: ServiceLogItem[] = [];
  for (const [key, item] of Object.entries(value)) {
    const normalized = key.toLowerCase();
    if (!/error|warning|failure|fail/.test(normalized) || isEmptyErrorValue(item)) continue;
    if (Array.isArray(item)) {
      for (const entry of item) {
        if (isEmptyErrorValue(entry)) continue;
        if (isRecord(entry)) {
          rows.push(...errorRecordRows([entry], normalized.includes("resolved") ? "resolved" : "active"));
        } else {
          rows.push({ detail: formatValue(key, entry), key, meta: `source=${source}`, source, status: "active", title: displayName(key) });
        }
      }
    } else {
      rows.push({ detail: formatValue(key, item), key, meta: `source=${source}`, source, status: "active", title: displayName(key) });
    }
  }
  return rows;
}

function runtimeLogRows(logs: ServiceLogPayload | undefined): ServiceLogItem[] {
  if (!logs?.rows?.length) return [];
  return logs.rows.map((row) => {
    const severityStatus = logLevelToStatus(row.level || row.event || row.title || "");
    const status = severityStatus === "resolved" ? "resolved" : "log";
    const event = row.event || row.level || "log";
    const ts = row.ts_utc ? formatLogTime(row.ts_utc) : "";
    const line = typeof row.line === "number" ? `line ${row.line}` : "";
    const meta = [ts, row.source, line].filter(Boolean).join(" | ");
    return {
      detail: row.detail || "-",
      event,
      key: [event, row.source, row.line].filter(Boolean).join(":"),
      meta,
      occurredAtMs: parseLogTime(row.ts_utc || ""),
      source: row.source || "",
      status,
      time: ts,
      title: row.title || event,
    };
  });
}

function logLevelToStatus(value: string): ServiceLogItem["status"] {
  const text = value.toLowerCase();
  if (/(critical|exception|fail|error|traceback)/.test(text)) return "active";
  if (/(warn|retry|timeout|degraded)/.test(text)) return "retrying";
  if (/(resolved|complete|success|succeeded|ok)/.test(text)) return "resolved";
  return "log";
}

function dedupeLogItems(items: ServiceLogItem[]) {
  const seen = new Set<string>();
  const rows: ServiceLogItem[] = [];
  for (const item of items) {
    const key = `${item.status}|${item.key}|${item.title}|${item.detail}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(item);
  }
  return rows;
}

function sortLogItems(items: ServiceLogItem[]) {
  return [...items].sort((a, b) => {
    const aTime = a.occurredAtMs ?? -1;
    const bTime = b.occurredAtMs ?? -1;
    if (aTime !== bTime) return bTime - aTime;
    return statusPriority(a.status) - statusPriority(b.status);
  });
}

function statusPriority(status: ServiceLogItem["status"]) {
  if (status === "active") return 0;
  if (status === "retrying") return 1;
  if (status === "resolved") return 2;
  if (status === "log") return 3;
  return 4;
}

function tableStateClass(status: string | undefined) {
  const normalized = String(status || "unknown").toLowerCase();
  if (normalized === "ok") return "ok";
  if (normalized === "empty") return "empty";
  if (normalized === "missing" || normalized === "error") return "error";
  return "unknown";
}

function databaseClass(database: string | undefined) {
  const normalized = String(database || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  if (normalized === "q-live") return "q-live";
  if (normalized === "market-sip-compact") return "market-sip-compact";
  if (normalized === "sec-core") return "sec-core";
  return "default";
}

function shortTableTimestamp(value: string | undefined) {
  if (!value || value === "-") return "-";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(parsed));
}

function serviceTableStateYears() {
  const currentYear = new Date().getFullYear();
  const years: number[] = [];
  for (let year = currentYear; year >= 2019; year -= 1) {
    years.push(year);
  }
  return years;
}

function logStatusFilterOptions(items: ServiceLogItem[]): Array<{ count: number; status: ServiceLogStatusFilter }> {
  const statuses: ServiceLogItem["status"][] = ["active", "retrying", "resolved", "log", "clear"];
  const counts = new Map<ServiceLogStatusFilter, number>([["all", items.length]]);
  for (const status of statuses) counts.set(status, 0);
  for (const item of items) counts.set(item.status, (counts.get(item.status) ?? 0) + 1);
  return [
    { status: "all", count: counts.get("all") ?? 0 },
    ...statuses.filter((status) => (counts.get(status) ?? 0) > 0).map((status) => ({ status, count: counts.get(status) ?? 0 })),
  ];
}

function unpackLogDetail(value: string): Array<{ key: string; value: string }> {
  const text = value.trim();
  if (!text || text === "-") return [];
  const parsed = parseMaybeJson(text);
  if (isRecord(parsed)) return objectLogRows(parsed);
  if (Array.isArray(parsed)) return parsed.map((item, index) => ({ key: `item_${index + 1}`, value: formatLogDetailValue(item) }));

  const segments = text.split(/;\s+(?=[A-Za-z0-9_. -]+=)/).map((segment) => segment.trim()).filter(Boolean);
  const rows: Array<{ key: string; value: string }> = [];
  for (const segment of segments) {
    const match = segment.match(/^([^=]{1,80})=(.*)$/s);
    if (!match) continue;
    const key = match[1].trim();
    const rawValue = match[2].trim();
    const parsedValue = parseMaybeJson(rawValue);
    if (isRecord(parsedValue)) {
      for (const nested of objectLogRows(parsedValue, key)) rows.push(nested);
    } else if (Array.isArray(parsedValue)) {
      rows.push({ key, value: formatLogDetailValue(parsedValue) });
    } else {
      rows.push({ key, value: rawValue || "-" });
    }
  }
  return rows;
}

function objectLogRows(record: Record<string, unknown>, prefix = ""): Array<{ key: string; value: string }> {
  return Object.entries(record).map(([key, value]) => ({
    key: prefix ? `${prefix}.${key}` : key,
    value: formatLogDetailValue(value),
  }));
}

function parseMaybeJson(value: string): unknown {
  const text = value.trim();
  if (!/^[{[]/.test(text)) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function formatLogDetailValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function isEmptyErrorValue(value: unknown) {
  if (value === undefined || value === null || value === "" || value === false) return true;
  if (typeof value === "number") return value === 0;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return !normalized || normalized === "-" || normalized === "ok" || normalized === "none" || normalized === "false" || normalized === "[]" || normalized.includes("no active errors");
  }
  if (isRecord(value)) return Object.keys(value).length === 0;
  return false;
}

function configurationGroups(service: ServiceStatusPayload): ConfigGroup[] {
  const rawItems = new Map<string, ConfigItem>();
  const add = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    if (!rawItems.has(key)) rawItems.set(key, { key, value });
  };

  add("service", service.registry.label);
  add("kind", service.registry.kind);
  add("endpoint", service.registry.base_url);

  const snapshot = service.snapshot ?? {};
  if (isRecord(snapshot.configuration)) {
    for (const [key, value] of Object.entries(snapshot.configuration)) add(key, value);
  }

  const grouped = new Map<string, ConfigItem[]>();
  for (const item of rawItems.values()) {
    const title = configGroupTitle(item.key);
    grouped.set(title, [...(grouped.get(title) ?? []), item]);
  }

  const order = ["Service", "Run Mode", "Connection", "Schedule And Market", "Database", "Storage", "Other Parameters"];
  return order
    .map((title) => ({ title, rows: grouped.get(title) ?? [] }))
    .filter((group) => group.rows.length > 0);
}

function configGroupTitle(key: string) {
  const normalized = key.toLowerCase();
  if (/^service$|^kind$/.test(normalized)) return "Service";
  if (/mode|execute|daemon|profile|env|policy/.test(normalized)) return "Run Mode";
  if (/endpoint|bind|host|port|url|client|server/.test(normalized)) return "Connection";
  if (/poll|interval|lookback|schedule|market|session|window|cadence|timezone|holiday/.test(normalized)) return "Schedule And Market";
  if (/database|table|clickhouse|schema/.test(normalized)) return "Database";
  if (/root|path|artifact|storage|log|report|folder|directory/.test(normalized)) return "Storage";
  return "Other Parameters";
}

function ServiceIcon({ service }: { service: ServiceStatusPayload }) {
  const info = statusInfo(service);
  const Icon = info.className === "not-started" ? WifiOff : info.className === "degraded" || info.className === "failed" || info.className === "blocked" ? AlertTriangle : CheckCircle2;
  return <Icon className="service-card-icon" size={20} />;
}

function ServiceStatusBadge({ online, status }: { online: boolean; status: string }) {
  const info = statusInfo({ online, status } as ServiceStatusPayload);
  return <span className={`service-status-badge ${info.className}`} title={info.description}>{info.label}</span>;
}

function sortServices(services: ServiceStatusPayload[]) {
  return [...services].sort((left, right) => SERVICE_IDS.indexOf(left.registry.id) - SERVICE_IDS.indexOf(right.registry.id));
}

function countStatuses(services: ServiceStatusPayload[]) {
  return services.reduce(
    (counts, service) => {
      const status = statusInfo(service).className;
      if (!service.online) counts.offline += 1;
      else counts.online += 1;
      if (status === "running" || status === "working" || status === "catching-up" || status === "preflight" || status === "starting") counts.active += 1;
      if (status === "degraded" || status === "failed" || status === "blocked") counts.degraded += 1;
      return counts;
    },
    { active: 0, degraded: 0, offline: 0, online: 0 },
  );
}

function phaseText(service: ServiceStatusPayload) {
  return String(service.current_operation?.phase || service.current_operation?.status || service.header?.market_status || "-");
}

function currentMessage(service: ServiceStatusPayload) {
  return String(service.current_operation?.message || service.current_operation?.next_action || service.errors?.snapshot || "");
}

function cardMessage(service: ServiceStatusPayload) {
  if (!service.online) return offlineReason(service) || "Service endpoint is not responding.";
  return currentMessage(service) || service.registry.description;
}

function offlineReason(service: ServiceStatusPayload) {
  return String(service.errors?.snapshot || service.errors?.health || service.errors?.metrics || "");
}

function coverageText(service: ServiceStatusPayload) {
  const coverage = service.snapshot?.coverage;
  if (!coverage || typeof coverage !== "object") return "-";
  const record = coverage as Record<string, unknown>;
  return String(record.message || record.status || record.active_window_utc || "-");
}

function runtimeText(service: ServiceStatusPayload) {
  const runtime = service.snapshot?.runtime;
  if (!runtime || typeof runtime !== "object") return "-";
  const record = runtime as Record<string, unknown>;
  const keys = ["poll_runs", "processed_rows", "written_rows", "feed_items", "ingest_events", "embedding_rows_written", "cycles"];
  const found = keys.find((key) => record[key] !== undefined && record[key] !== null && record[key] !== "");
  return found ? `${displayName(found)} ${formatCompactNumber(record[found])}` : "-";
}

function serviceWorkRows(service: ServiceStatusPayload): ServiceWorkRow[] {
  const snapshot = service.snapshot ?? {};
  const rows: ServiceWorkRow[] = [];
  if (isRecord(snapshot.coverage)) rows.push(serviceWorkRow({ ...snapshot.coverage, name: "coverage manifest" }, "coverage", "live"));
  rows.push(...arrayRows(snapshot.tasks).map((row) => serviceWorkRow(row, "task", "live")));
  rows.push(...arrayRows(snapshot.task_table_progress).map((row) => serviceWorkRow(row, "table", "live")));
  rows.push(...arrayRows(snapshot.queues).map((row) => serviceWorkRow(row, "queue", "live")));
  rows.push(...arrayRows(snapshot.sources_sinks).map((row) => serviceWorkRow(row, "source", "live")));
  return dedupeWorkRows(rows)
    .filter((row) => !isSetupLikeWorkRow(row))
    .sort((a, b) => workStatusRank(a.status) - workStatusRank(b.status) || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
}

function serviceSetupRows(service: ServiceStatusPayload): ServiceWorkRow[] {
  const snapshot = service.snapshot ?? {};
  const rows: ServiceWorkRow[] = [];
  rows.push(...arrayRows(snapshot.dependencies).map((row) => serviceWorkRow(row, "dependency", "setup")));
  rows.push(...arrayRows(snapshot.configured_tables).map((row) => serviceWorkRow(row, "configured table", "setup")));
  rows.push(...arrayRows(snapshot.tasks).map((row) => serviceWorkRow(row, "task", "setup")).filter(isSetupLikeWorkRow));
  return dedupeWorkRows(rows).sort((a, b) => workStatusRank(a.status) - workStatusRank(b.status) || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
}

function isSetupLikeWorkRow(row: ServiceWorkRow) {
  const text = workRowSearchText(row);
  return /preflight|dependenc|configured table|config contract|startup check|schema check|credential|auth|artifact storage/.test(text);
}

function isPreflightSetupRow(row: ServiceWorkRow) {
  const text = workRowSearchText(row);
  return /preflight|dependenc|clickhouse|artifact|provider|credential|auth|storage|market status|calendar|health/.test(text);
}

function serviceWorkGroups(service: ServiceStatusPayload): ServiceWorkGroup[] {
  const rows = serviceWorkRows(service);
  const specs = serviceResponsibilitySpecs(service.registry.id);
  const groups = specs.map((spec) => ({ ...spec, rows: [] as ServiceWorkRow[], status: "waiting" }));
  const fallback = groups[groups.length - 1];
  for (const row of rows) {
    const text = workRowSearchText(row);
    const group = groups.find((candidate) => candidate.match.some((pattern) => pattern.test(text))) ?? fallback;
    group.rows.push(row);
  }
  return groups.map((group) => ({
    activeCount: countRowsByStatus(group.rows, "running"),
    completedCount: countRowsByStatus(group.rows, "ok"),
    description: group.description,
    id: group.id,
    lastAt: latestWorkTimestamp(group.rows),
    rows: group.rows,
    status: groupStatus(group.rows),
    title: group.title,
    warningCount: group.rows.filter((row) => ["warn", "error"].includes(workStatusClass(row.status))).length,
  }));
}

type ServiceResponsibilitySpec = {
  description: string;
  id: string;
  match: RegExp[];
  title: string;
};

function serviceResponsibilitySpecs(serviceId: ServiceId): ServiceResponsibilitySpec[] {
  const common = {
    other: {
      description: "Additional reported work that does not map cleanly to a primary responsibility.",
      id: "other",
      match: [/./],
      title: "Other Reported Work",
    },
  } satisfies Record<string, ServiceResponsibilitySpec>;

  const specs: Record<ServiceId, ServiceResponsibilitySpec[]> = {
    news: [
      {
        description: "Benzinga polling cadence, raw item intake, duplicate handling, and live news memory updates.",
        id: "live",
        match: [/poll|benzinga|provider|news|raw|duplicate|skip|live|latest/],
        title: "Live Benzinga Update",
      },
      {
        description: "Coverage bootstrap, gap detection, gap fill, and historical catch-up for Benzinga news.",
        id: "coverage",
        match: [/coverage|manifest|gap|backfill|catch.?up|initial|bootstrap|historical/],
        title: "Coverage, Gap Fill, Backfill",
      },
      {
        description: "URL handling, external text/PDF enrichment, canonicalization, ticker links, and quality flags.",
        id: "processing",
        match: [/enrich|canonical|normaliz|url|pdf|extract|text|ticker|quality|process|article/],
        title: "Enrichment And Canonical Rows",
      },
      {
        description: "Database publishing for normalized rows, ticker links, coverage rows, and runtime logs.",
        id: "publish",
        match: [/publish|insert|write|database|table|sink|clickhouse|persist/],
        title: "Database Publishing",
      },
      common.other,
    ],
    sec: [
      {
        description: "SEC coverage manifest, current-day gaps, historical archive backfill, and bulk catch-up state.",
        id: "coverage",
        match: [/coverage|manifest|gap|backfill|catch.?up|archive|bulk|submissions|companyfacts|initial|historical/],
        title: "Coverage, Gap Fill, Backfill",
      },
      {
        description: "SEC current feed polling, rate-limit aware retries, filing discovery, and duplicate suppression.",
        id: "live",
        match: [/poll|feed|rss|current|live|filing|accession|duplicate|skip|sec/],
        title: "Live SEC Feed Update",
      },
      {
        description: "Filing text extraction, document parsing, XBRL companyfacts/frames, and canonical filing rows.",
        id: "processing",
        match: [/xbrl|companyfact|frame|document|filing text|parse|extract|text|normaliz|canonical|process/],
        title: "Filing Text And XBRL Processing",
      },
      {
        description: "Database writes, audit checks, integrity warnings, and repair status for SEC tables.",
        id: "publish",
        match: [/publish|insert|write|database|table|audit|integrity|repair|orphan|persist/],
        title: "Database Publishing And Audit",
      },
      common.other,
    ],
    qmd: [
      {
        description: "Massive websocket subscriptions, trade/quote event intake, connection health, and live stream state.",
        id: "live",
        match: [/websocket|subscription|ingest|trade|quote|event|connection|disconnect|massive|live|luld/],
        title: "Live Market Event Ingest",
      },
      {
        description: "Recent q_live coverage, REST repair, current-session head/tail fill, and three-market-day gap repair.",
        id: "gap_fill",
        match: [/coverage|manifest|gap|repair|backfill|rest|recent|q_live|head|tail|maintenance/],
        title: "Recent Live Gap Repair",
      },
      {
        description: "Streaming bars, scanner state, market condition state, and downstream event publication.",
        id: "processing",
        match: [/bar|scanner|condition|halt|resume|state|publish|fanout|broadcast|compact/],
        title: "Bars, State, And Broadcast",
      },
      {
        description: "ClickHouse persistence for live market events and live bars, including writer queues and flush state.",
        id: "persist",
        match: [/clickhouse|persist|insert|write|database|table|writer|flush|sink/],
        title: "Database Persistence",
      },
      common.other,
    ],
    reference: [
      {
        description: "Low-frequency provider sync for Massive, IBKR, FINRA, SEC-derived mappings, presentation assets, and publications.",
        id: "source_sync",
        match: [/source|sync|massive|ibkr|finra|sec|ticker|listing|issuer|exchange|asset|borrow|short|split|dividend|ipo/],
        title: "Reference Source Sync",
      },
      {
        description: "Integrity audit, issue detection, deterministic resolution, tradability blocking, and human-review queues.",
        id: "integrity",
        match: [/audit|issue|resolve|resolution|tradable|block|guard|integrity|warning|error|review/],
        title: "Integrity And Issue Resolution",
      },
      {
        description: "Derived scanner/tradability publications, alerts, and reference facts maintained from canonical source tables.",
        id: "publication",
        match: [/publication|publish|fact|alert|scanner|snapshot|view|bridge|sec_market_bridge/],
        title: "Publications, Facts, Alerts",
      },
      {
        description: "After-hours maintenance, schema checks, rebuilds, historical gap fill, and source-specific repair work.",
        id: "maintenance",
        match: [/maintenance|gap|backfill|historical|rebuild|schema|policy|after.?hours|repair/],
        title: "Maintenance And Gap Fill",
      },
      common.other,
    ],
    "text-embed": [
      {
        description: "Source coverage checks, lookback windows, pending text discovery, and historical gap scan.",
        id: "coverage",
        match: [/coverage|gap|lookback|source|scan|pending|historical|backfill|manifest/],
        title: "Source Coverage And Gap Scan",
      },
      {
        description: "Text extraction, chunking, tokenization, queue depth, batching, and model input preparation.",
        id: "processing",
        match: [/extract|chunk|token|queue|batch|pending|text|prepare|process/],
        title: "Extraction And Tokenization",
      },
      {
        description: "Embedding inference, vector writes, publication state, and downstream table persistence.",
        id: "embedding",
        match: [/embed|embedding|vector|model|gpu|vllm|inference|write|publish|insert|database|table/],
        title: "Embedding Inference And Writes",
      },
      {
        description: "Retry handling, stale work recovery, audit state, and failed-row repair.",
        id: "recovery",
        match: [/retry|error|failure|failed|repair|audit|warning|stale|recover/],
        title: "Recovery And Audit",
      },
      common.other,
    ],
    ibkr: [
      {
        description: "Client Portal authentication, brokerage session health, account discovery, and API reachability.",
        id: "session",
        match: [/auth|session|client portal|iserver|account|portfolio|broker|gateway|login|connected/],
        title: "Broker Session And Accounts",
      },
      {
        description: "Keepalive tickles, websocket or endpoint health, reconnect handling, and active failure recovery.",
        id: "connectivity",
        match: [/keepalive|tickle|connection|connect|disconnect|health|recover|retry|heartbeat/],
        title: "Connectivity And Recovery",
      },
      {
        description: "Contract lookup, conid validation, account routing readiness, and order-path guardrails.",
        id: "routing",
        match: [/contract|conid|route|routing|order|account|security|stock|secdef/],
        title: "Contract And Routing Readiness",
      },
      common.other,
    ],
  };
  return specs[serviceId];
}

function workRowSearchText(row: ServiceWorkRow) {
  return `${row.name} ${row.kind} ${row.status} ${row.progress} ${row.rows} ${row.schedule} ${row.detail}`.toLowerCase();
}

function groupStatus(rows: ServiceWorkRow[]) {
  if (!rows.length) return "waiting";
  const statuses = rows.map((row) => workStatusClass(row.status));
  if (statuses.includes("error")) return "error";
  if (statuses.includes("warn")) return "warning";
  if (statuses.includes("running")) return "running";
  if (statuses.includes("waiting")) return "waiting";
  return "ok";
}

function serviceWorkRow(row: Record<string, unknown>, fallbackKind: string, reportKind: ServiceWorkRow["reportKind"]): ServiceWorkRow {
  const name = firstString(row, ["name", "task", "work", "item", "source", "sink", "table", "database", "label", "area"]) || fallbackKind;
  const kind = firstString(row, ["kind", "type", "category", "role"]) || fallbackKind;
  const status = firstString(row, ["status", "state", "phase", "result"]) || "waiting";
  const progress = workProgressText(row);
  const rows = firstString(row, ["rows", "row_count", "processed_rows", "written_rows", "done", "completed", "count"]) || "-";
  const schedule = firstString(row, ["schedule", "cadence", "frequency", "interval", "next", "next_run", "next_poll", "window"]) || "-";
  const lastTimestamp = firstTimestamp(row);
  const detail = humanizeWorkDetail(firstString(row, ["detail", "details", "message", "description", "notes", "last", "latest"]) || compactWorkDetail(row));
  return {
    detail,
    kind,
    lastAt: lastTimestamp.label,
    lastAtMs: lastTimestamp.value,
    name,
    progress,
    reportKind,
    rows: rows === "" ? "-" : rows,
    schedule,
    status,
  };
}

function countRowsByStatus(rows: ServiceWorkRow[], className: ReturnType<typeof workStatusClass>) {
  return rows.filter((row) => workStatusClass(row.status) === className).length;
}

function latestWorkTimestamp(rows: ServiceWorkRow[]) {
  const latest = rows
    .map((row) => ({ label: row.lastAt, value: row.lastAtMs }))
    .filter((item) => item.label && item.label !== "-" && item.value !== undefined)
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))[0];
  return latest?.label ?? "";
}

function firstTimestamp(row: Record<string, unknown>) {
  const raw = firstString(row, ["updated_at_utc", "last_seen_at_utc", "last_run_at_utc", "completed_at_utc", "started_at_utc", "last_poll_at_utc", "checked_at_utc", "ts_utc", "time_utc", "updated_at", "last_seen", "last_run", "completed_at", "started_at", "last_poll_at", "checked_at", "time", "since"]);
  if (!raw || raw === "-") return { label: "-", value: undefined };
  const parsed = Date.parse(raw);
  if (!Number.isFinite(parsed)) return { label: raw.length > 28 ? `${raw.slice(0, 25)}...` : raw, value: undefined };
  return { label: formatLogTime(raw), value: parsed };
}

function firstString(row: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value === undefined || value === null || value === "") continue;
    return formatValue(key, value);
  }
  return "";
}

function workProgressText(row: Record<string, unknown>) {
  const progress = row.progress ?? row.percent ?? row.progress_pct ?? row.completion_pct;
  if (progress !== undefined && progress !== null && progress !== "") {
    const value = typeof progress === "number" && progress <= 1 ? `${Math.round(progress * 100)}%` : formatValue("progress", progress);
    return value;
  }
  const done = row.done ?? row.completed ?? row.processed ?? row.finished;
  const total = row.total ?? row.expected ?? row.target ?? row.targets;
  if (done !== undefined && total !== undefined && done !== "" && total !== "") return `${formatValue("done", done)} / ${formatValue("total", total)}`;
  return "-";
}

function compactWorkDetail(row: Record<string, unknown>) {
  const omitted = new Set(["area", "category", "completed", "completion_pct", "count", "database", "done", "expected", "finished", "interval", "item", "kind", "label", "name", "next", "next_poll", "next_run", "percent", "phase", "processed", "processed_rows", "progress", "progress_pct", "result", "role", "row_count", "rows", "schedule", "sink", "source", "state", "status", "table", "target", "targets", "task", "total", "type", "window", "work", "written_rows"]);
  const parts = Object.entries(row)
    .filter(([key, value]) => !omitted.has(key) && value !== undefined && value !== null && value !== "")
    .slice(0, 4)
    .map(([key, value]) => `${displayName(key)} ${formatValue(key, value)}`);
  return parts.length ? parts.join("; ") : "-";
}

function humanizeWorkDetail(value: string) {
  if (!value || value === "-") return "-";
  const normalized = value
    .replace(/\\\\DESKTOP-SAAI85T\\Workstation-D\\market-data/gi, "Workstation-D:/market-data")
    .replace(/D:\\TradingCodes\\quant-research-workbench/gi, "repo:")
    .replace(/\s+/g, " ")
    .trim();
  const segments = normalized.split(/;\s*/).filter(Boolean);
  const readable = segments.length > 1
    ? segments.slice(0, 4).map((segment) => {
        const match = segment.match(/^([^=]{1,40})=(.*)$/);
        if (!match) return segment;
        return `${displayName(match[1].trim())}: ${shortenWorkValue(match[2].trim())}`;
      }).join(" / ")
    : shortenWorkValue(normalized);
  return readable.length > 220 ? `${readable.slice(0, 217)}...` : readable;
}

function shortenWorkValue(value: string) {
  if (!value) return "-";
  if (value.length <= 120) return value;
  const slashParts = value.split(/[\\/]/).filter(Boolean);
  if (slashParts.length >= 3) {
    const tail = slashParts.slice(-3).join("/");
    return `.../${tail}`;
  }
  return `${value.slice(0, 117)}...`;
}

function dedupeWorkRows(rows: ServiceWorkRow[]) {
  const seen = new Set<string>();
  const output: ServiceWorkRow[] = [];
  for (const row of rows) {
    const key = `${row.kind}|${row.name}|${row.status}|${row.detail}`;
    if (seen.has(key)) continue;
    seen.add(key);
    output.push(row);
  }
  return output;
}

function normalizedStatus(status: string) {
  return String(status || "").toLowerCase().replace(/[^a-z0-9]+/g, "_");
}

function workStatusClass(status: string) {
  const normalized = normalizedStatus(status);
  if (/failed|error|blocked|critical/.test(normalized)) return "error";
  if (/warn|degraded|retry/.test(normalized)) return "warn";
  if (/running|working|active|queued|pending|loading|polling/.test(normalized)) return "running";
  if (/complete|completed|ok|ready|success|idle/.test(normalized)) return "ok";
  return "waiting";
}

function workStatusRank(status: string) {
  const className = workStatusClass(status);
  if (className === "error") return 0;
  if (className === "warn") return 1;
  if (className === "running") return 2;
  if (className === "waiting") return 3;
  return 4;
}

function objectRows(...values: unknown[]) {
  const rows: Array<{ key: string; value: unknown }> = [];
  for (const value of values) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (item === undefined || item === null || item === "") continue;
      if (typeof item === "object") rows.push({ key, value: compactJson(item) });
      else rows.push({ key, value: item });
    }
  }
  return rows;
}

function arrayRows(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)).map(normalizeRow);
}

function recentRowsFromPayload(value: unknown) {
  if (Array.isArray(value)) return value.filter(isRecord).map(normalizeRow);
  if (!value || typeof value !== "object") return [];
  const record = value as Record<string, unknown>;
  for (const key of ["rows", "items", "recent", "events", "filings", "news", "snapshots"]) {
    const rows = record[key];
    if (Array.isArray(rows)) return rows.filter(isRecord).map(normalizeRow);
  }
  return objectRows(record);
}

function normalizeRow(row: Record<string, unknown>) {
  const normalized: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(row)) {
    normalized[key] = typeof value === "object" && value !== null ? compactJson(value) : value;
  }
  return normalized;
}

function compactJson(value: unknown) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function formatValue(key: string, value: unknown) {
  if (typeof value === "number") return formatCell(key, value);
  if (typeof value === "string") return value || "-";
  return compactJson(value);
}

function formatTime(value: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(parsed));
}

function formatLogTime(value: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(parsed));
}

function parseLogTime(value: string) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function formatZoneTime(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone }).format(value);
}

function formatZoneDate(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "2-digit", year: "numeric", timeZone }).format(value);
}

function formatZoneDateTime(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone }).format(value);
}

function formatUtcDateTime(value: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value || "-";
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }).format(new Date(parsed));
}

function fleetMarketStatus(services: ServiceStatusPayload[]) {
  for (const service of services) {
    const candidates = [
      service.header?.market_status,
      service.header?.market_session,
      service.metrics?.market_status,
      service.metrics?.current_market_session,
      service.snapshot?.runtime && isRecord(service.snapshot.runtime) ? service.snapshot.runtime.market_status : "",
      service.snapshot?.runtime && isRecord(service.snapshot.runtime) ? service.snapshot.runtime.current_market_session : "",
    ];
    const value = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
    if (value) {
      const source = String(service.metrics?.market_status_source || service.header?.market_status_source || service.registry.label);
      return { status: String(value), detail: marketSourceLabel(source || service.registry.label) };
    }
  }
  return { status: "not reported", detail: "No gateway has reported market state yet" };
}

function marketSourceLabel(source: string) {
  const normalized = source.toLowerCase();
  if (normalized === "massive_market_calendar") return "Massive status + calendar";
  if (normalized === "massive_status") return "Massive status";
  if (normalized === "local_clock") return "Local clock";
  if (normalized === "disabled") return "Market status disabled";
  return displayName(source);
}

function marketTileClass(status: string, detail: string) {
  const statusText = status.toLowerCase().replaceAll("_", "-");
  const detailText = detail.toLowerCase().replaceAll("_", "-");
  if (!statusText.trim() || statusText.includes("not reported") || statusText.includes("unknown")) return "market-unknown";
  if (statusText.includes("error") || statusText.includes("degraded") || statusText.includes("blocked") || detailText.includes("error")) return "market-warning";
  if (statusText.includes("pre-market") || statusText.includes("premarket") || statusText.includes("after-hours") || statusText.includes("after hours") || statusText.includes("extended")) return "market-extended";
  if (statusText.includes("open") || statusText.includes("regular")) return "market-open";
  if (statusText.includes("holiday")) return "market-holiday";
  if (statusText.includes("closed") || statusText.includes("close")) return "market-closed";
  return "market-unknown";
}

type StatusInfo = {
  className: string;
  description: string;
  label: string;
};

function statusInfo(service: Pick<ServiceStatusPayload, "online" | "status">): StatusInfo {
  if (!service.online) {
    return { className: "not-started", description: "The service API endpoint is not reachable or timed out.", label: "NOT STARTED" };
  }
  const text = String(service.status || "").toLowerCase().replaceAll("_", "-");
  if (text.includes("start")) return { className: "starting", description: "The service is starting and has not completed initialization.", label: "STARTING" };
  if (text.includes("preflight")) return { className: "preflight", description: "The service is checking dependencies before operational work.", label: "PREFLIGHT" };
  if (text.includes("catch") || text.includes("gap") || text.includes("repair")) return { className: "catching-up", description: "The service is filling coverage gaps or repairing recent data.", label: "CATCHING UP" };
  if (text.includes("work") || text.includes("queue") || text.includes("processing")) return { className: "working", description: "The service is actively processing background work.", label: "WORKING" };
  if (text.includes("degraded") || text.includes("warn")) return { className: "degraded", description: "The service is reachable but has warnings or reduced capability.", label: "DEGRADED" };
  if (text.includes("block")) return { className: "blocked", description: "The service is blocked by policy, dependency, or required manual action.", label: "BLOCKED" };
  if (text.includes("stop")) return { className: "stopping", description: "The service is shutting down.", label: "STOPPING" };
  if (text.includes("fail") || text.includes("error") || text.includes("critical")) return { className: "failed", description: "The service reports an active critical failure.", label: "FAILED" };
  if (text.includes("idle") || text.includes("waiting")) return { className: "idle", description: "The service is healthy and waiting for the next scheduled task.", label: "IDLE" };
  if (text.includes("run") || text.includes("ok") || text.includes("healthy") || text.includes("online")) return { className: "running", description: "The service is healthy and running.", label: "RUNNING" };
  return { className: "unknown", description: "The service is reachable but did not report a standard status.", label: service.status ? String(service.status).toUpperCase() : "UNKNOWN" };
}

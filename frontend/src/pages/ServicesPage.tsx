import { Activity, AlertTriangle, CalendarDays, CheckCircle2, Clock3, Loader2, MapPin, RadioTower, RefreshCcw, Search, Settings2, WifiOff, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { api } from "../api/client";
import { Button } from "../app/components/Button";
import { DataTable } from "../app/components/DataTable";
import { Modal } from "../app/components/Modal";
import { displayName, formatCell, formatCompactNumber, formatDuration } from "../app/format";

export type ServicePageMode = "dashboard" | ServiceId;
export type ServiceId = "ibkr" | "news" | "qmd" | "reference" | "sec" | "text-embed";

type ServiceRegistry = {
  base_url: string;
  description: string;
  id: ServiceId;
  kind: string;
  label: string;
};

type ServiceStatusTone = "active" | "error" | "idle" | "ok" | "waiting" | "warn";

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
        const next = await api<ServicesStatusPayload>("/api/services/status", { timeoutMs: 15000 });
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
        const next = await api<ServiceStatusPayload>(`/api/services/${serviceId}/status?${query}`, { timeoutMs: options.full ? 30000 : 10000 });
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
  const showBlockingLoader = !selected && (loading || detailLoading);

  return (
    <div className={`services-page ${showBlockingLoader ? "is-page-loading" : ""}`}>
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
      ) : error && !services.length ? (
        <ServicePageApiFailure message={error} />
      ) : (
        <ServicesDashboard services={services} onNavigate={onNavigate} />
      )}
      {showBlockingLoader ? (
        <div className="services-page-loading-overlay" aria-label="Loading service data">
          <Loader2 size={22} />
          <span>{loading ? "Loading service status..." : "Loading service details..."}</span>
        </div>
      ) : null}
    </div>
  );
}

function ServicePageApiFailure({ message }: { message: string }) {
  return (
    <section className="service-page-api-failure">
      <div className="service-page-api-failure-icon">
        <AlertTriangle size={18} />
      </div>
      <div>
        <h2>Service status could not be loaded</h2>
        <p>{message}</p>
        <span>The dashboard will keep retrying in the background. Confirm the backend is running on port 8000 and refresh once it is healthy.</span>
      </div>
    </section>
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
  const [dependenciesOpen, setDependenciesOpen] = useState(false);
  const focusStatus = statusInfo(service);
  const runTiming = serviceRunTiming(service);
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
              <div className="service-focus-run">
                <span className="service-focus-runtime">{runtimeText(service)}</span>
                <span>Started {runTiming.started}</span>
                <span>Duration {runTiming.duration}</span>
              </div>
              <div className="service-focus-actions">
                <button className="service-focus-config-button" onClick={() => setConfigOpen(true)} type="button">
                  <Settings2 size={14} />
                  Configuration
                </button>
                <button className="service-focus-config-button" onClick={() => setDependenciesOpen(true)} type="button">
                  <CheckCircle2 size={14} />
                  Dependencies
                </button>
              </div>
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
      {dependenciesOpen ? (
        <Modal className="service-dependencies-modal-panel" onClose={() => setDependenciesOpen(false)} title={`${service.registry.label} Dependencies`}>
          <ServiceDependenciesPanel service={service} />
        </Modal>
      ) : null}
      {service.registry.id === "news" ? <NewsServiceWorkAndRows service={service} /> : <ServiceWorkAndActivity service={service} />}
      <ServiceErrorLogPanel pageError={pageError} service={service} />
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

type ServiceActivityRow = {
  detail: string;
  kind: string;
  raw: Record<string, unknown>;
  rows: string;
  status: string;
  subject: string;
  time: string;
  timeMs?: number;
};

type ServiceActivitySummaryItem = {
  label: string;
  tone?: "bad" | "good" | "warn";
  value: string;
};

type ServiceActivitySpec = {
  description: string;
  status: string;
  summary: ServiceActivitySummaryItem[];
  title: string;
};

type NewsPublishHistoryRow = {
  activeJobs: number;
  canonicalNewsId: string;
  coverageMode: string;
  enrichment: string;
  event: string;
  insertedRows: number;
  pendingRows: number;
  pollId: string;
  providerArticleId: string;
  publishedAt: string;
  processedRows: number;
  qualityFlags: string[];
  skippedRows: number;
  status: string;
  tickerRows: number;
  tickers: string;
  title: string;
  time: string;
  wallSeconds?: number;
};

type NewsEnrichmentArticleRow = {
  canonicalNewsId: string;
  domainSample: string[];
  externalFetchStatus: string;
  hasPdf: boolean;
  preEnrichedRow: Record<string, unknown>;
  providerArticleId: string;
  providerPayload: Record<string, unknown>;
  publishedAt: string;
  requiresEnrichment: boolean;
  tickers: string;
  title: string;
  urlCount: number;
  urlResolution: Record<string, unknown>;
  urlSample: string[];
};

type NewsEnrichmentHistoryRow = {
  articleCount: number;
  detail: string;
  domainSample: string[];
  enrichedUrls: number;
  event: string;
  failedArticles: number;
  fetchTasks: number;
  mode: string;
  pollId: string;
  providerArticleId: string;
  queueSize: number;
  status: string;
  time: string;
  title: string;
  titleSample: string[];
  items: NewsEnrichmentArticleRow[];
  urlSample: string[];
  wallSeconds: number;
  worker: string;
};

type NewsCoverageHistoryRow = {
  chunkCount: number;
  coverageId: string;
  detail: string;
  endUtc: string;
  event: string;
  gapCount: number;
  inFlight: number;
  progress: string;
  rows: number;
  script: string;
  stage: string;
  startUtc: string;
  status: string;
  time: string;
  totalChunks: number;
  window: string;
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

type NewsTodayRow = {
  articleUrl: string;
  author: string;
  bodyChars: number;
  canonicalNewsId: string;
  channels: string[];
  contentQualityFlags: string[];
  downloadedAtUtc: string;
  externalChars: number;
  externalFetchStatus: string;
  fullTextChars: number;
  hasBody: boolean;
  hasExternalText: boolean;
  hasPdf: boolean;
  isTitleOnly: boolean;
  normalizedTitle: string;
  pdfChars: number;
  pdfExtractStatus: string;
  providerArticleId: string;
  providerTags: string[];
  publishedAtUtc: string;
  textPreview: string;
  tickerLinkCount: number;
  tickerLinkSample: string[];
  tickers: string[];
  title: string;
  urlDomain: string;
};

type NewsTodayRowsPayload = {
  database?: string;
  error?: string;
  limit?: number;
  normalized_table?: string;
  rows?: Array<Record<string, unknown>>;
  sort?: string;
  summary?: Record<string, unknown>;
  ticker_table?: string;
  window_end_utc?: string;
  window_start_utc?: string;
};

type NewsDetailPayload = {
  canonical_news_id?: string;
  database?: string;
  normalized_table?: string;
  row?: Record<string, unknown>;
  ticker_rows?: Array<Record<string, unknown>>;
  ticker_table?: string;
};

type NewsTodayRowsState = {
  error: string;
  loading: boolean;
  rows: NewsTodayRow[];
  sort: NewsTodaySort;
  summary: NewsTodaySummary;
  windowEndUtc: string;
  windowStartUtc: string;
};

type NewsTodaySort = "asc" | "desc";

type NewsTodaySummary = {
  externalText: number;
  latest: string;
  loadedRows: number;
  multiTickerRows: number;
  noTickerRows: number;
  oneTickerRows: number;
  pdfRows: number;
  totalRows: number;
  withTicker: number;
};

function NewsServiceWorkAndRows({ service }: { service: ServiceStatusPayload }) {
  const [todaySort, setTodaySort] = useState<NewsTodaySort>("desc");
  const todayNews = useNewsTodayRows(service.registry.id === "news", todaySort);
  return (
    <section className="news-service-work-and-rows-grid">
      <ServiceWorkPlanPanel service={service} />
      <NewsTodayRowsPanel onSortChange={setTodaySort} state={todayNews} />
    </section>
  );
}

function ServiceWorkAndActivity({ service }: { service: ServiceStatusPayload }) {
  return (
    <section className={`service-work-and-activity-grid service-work-and-activity-${service.registry.id}`}>
      <ServiceWorkPlanPanel service={service} />
      <ServiceActivityPanel service={service} />
    </section>
  );
}

function ServiceActivityPanel({ service }: { service: ServiceStatusPayload }) {
  const [selectedRow, setSelectedRow] = useState<ServiceActivityRow | null>(null);
  const spec = serviceActivitySpec(service);
  const rows = serviceActivityRows(service);
  const visibleRows = rows.length ? rows : [{
    detail: `No recent ${service.registry.label.toLowerCase()} activity rows have been reported by the service endpoint yet.`,
    kind: "service",
    raw: { service: service.registry.id, recent: service.recent || null },
    rows: "-",
    status: service.online ? "waiting" : "not started",
    subject: "No recent activity",
    time: service.checked_at_utc ? formatLogTime(service.checked_at_utc) : "-",
    timeMs: service.checked_at_utc ? Date.parse(service.checked_at_utc) : undefined,
  }];
  return (
    <Panel className={`service-activity-panel service-activity-panel-${service.registry.id}`} title={spec.title}>
      <div className="service-activity-header">
        <p>{spec.description}</p>
        <span className={`service-work-status ${workStatusClass(spec.status)}`}>{displayName(spec.status)}</span>
      </div>
      <div className="service-activity-summary">
        {spec.summary.map((item) => (
          <span className={item.tone ? `metric-${item.tone}` : ""} key={item.label}>
            <small>{item.label}</small>
            <strong>{item.value}</strong>
          </span>
        ))}
      </div>
      <div className="service-activity-table-wrap">
        <table className="service-activity-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Status</th>
              <th>Subject</th>
              <th>Rows</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row, index) => (
              <tr
                className={workStatusClass(row.status)}
                key={`${row.kind}-${row.subject}-${row.time}-${index}`}
                onClick={() => setSelectedRow(row)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedRow(row);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <td title={row.time}>{row.time || "-"}</td>
                <td><span className={`service-work-status ${workStatusClass(row.status)}`}>{displayName(row.status || "waiting")}</span></td>
                <td title={row.subject}><strong>{row.subject}</strong><span>{displayName(row.kind)}</span></td>
                <td>{row.rows || "-"}</td>
                <td title={row.detail}>{row.detail || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedRow ? (
        <Modal className="service-activity-detail-modal-panel" onClose={() => setSelectedRow(null)} title={`${service.registry.label} Activity Detail`}>
          <ServiceActivityDetailModal row={selectedRow} service={service} />
        </Modal>
      ) : null}
    </Panel>
  );
}

function ServiceActivityDetailModal({ row, service }: { row: ServiceActivityRow; service: ServiceStatusPayload }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="service-activity-detail">
      <div className={`service-activity-detail-status ${statusClass}`}>
        <div>
          <span>{displayName(service.registry.kind)}</span>
          <strong>{row.subject}</strong>
        </div>
        <span className={`service-work-status ${statusClass}`}>{displayName(row.status)}</span>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Time</dt>
          <dd>{row.time || "-"}</dd>
        </div>
        <div>
          <dt>Kind</dt>
          <dd>{displayName(row.kind)}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{displayName(row.status)}</dd>
        </div>
        <div>
          <dt>Rows</dt>
          <dd>{row.rows || "-"}</dd>
        </div>
        <div className="wide">
          <dt>Detail</dt>
          <dd>{row.detail || "-"}</dd>
        </div>
      </dl>
      <DebugObjectBlock title="Raw Service Activity Row" value={row.raw} />
    </div>
  );
}

function NewsTodayRowsPanel({ onSortChange, state }: { onSortChange: (sort: NewsTodaySort) => void; state: NewsTodayRowsState }) {
  const [detail, setDetail] = useState<NewsDetailPayload | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRow, setSelectedRow] = useState<NewsTodayRow | null>(null);
  const rows = state.rows;
  const summary = state.summary;
  const filteredRows = useMemo(() => newsTodayFilteredRows(rows, searchQuery), [rows, searchQuery]);
  const showingLabel = summary.totalRows > summary.loadedRows
    ? `Showing ${formatCompactNumber(summary.loadedRows)} of ${formatCompactNumber(summary.totalRows)} rows`
    : `${formatCompactNumber(summary.totalRows)} rows loaded`;
  const searchLabel = searchQuery.trim()
    ? `Filtered ${formatCompactNumber(filteredRows.length)} of ${formatCompactNumber(summary.loadedRows)} loaded rows`
    : showingLabel;

  async function openNews(row: NewsTodayRow) {
    setSelectedRow(row);
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    try {
      const payload = await api<NewsDetailPayload>(`/api/services/news/detail/${encodeURIComponent(row.canonicalNewsId)}`);
      setDetail(payload);
    } catch (exc) {
      setDetailError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <Panel className="news-today-panel" title="Today's Inserted News">
      <div className="news-today-searchbar">
        <label className="news-today-search-field">
          <Search size={14} />
          <input
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search ticker, title, source, flag, author, URL, or article id"
            type="search"
            value={searchQuery}
          />
          {searchQuery ? (
            <button aria-label="Clear inserted news search" onClick={() => setSearchQuery("")} type="button">
              <X size={14} />
            </button>
          ) : null}
        </label>
        <div className="news-today-compact-stats">
          <span><small>Today</small><strong>{formatCompactNumber(summary.totalRows)}</strong></span>
          <span><small>Loaded</small><strong>{formatCompactNumber(summary.loadedRows)}</strong></span>
          <span><small>1 ticker</small><strong>{formatCompactNumber(summary.oneTickerRows)}</strong></span>
          <span><small>Latest</small><strong>{summary.latest ? formatLogTime(summary.latest) : "-"}</strong></span>
        </div>
      </div>
      <div className="news-today-meta">
        <span>{state.windowStartUtc ? `Window ${formatLogTime(state.windowStartUtc)} -> ${formatLogTime(state.windowEndUtc)}` : "Today, market timezone"}</span>
        {state.error ? <strong>{state.error}</strong> : <strong>{state.loading ? "Loading rows..." : searchLabel}</strong>}
      </div>
      <div className="news-today-table-wrap">
        <table className="news-today-table">
          <thead>
            <tr>
              <th aria-sort={state.sort === "desc" ? "descending" : "ascending"}>
                <button className="news-today-sort-button" onClick={() => onSortChange(state.sort === "desc" ? "asc" : "desc")} type="button">
                  <span>Time</span>
                  <strong>{state.sort === "desc" ? "Newest" : "Oldest"}</strong>
                </button>
              </th>
              <th>Tickers</th>
              <th>Title</th>
              <th>Text</th>
              <th>Flags</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {(filteredRows.length ? filteredRows : [null]).map((row, index) => row ? (
              <tr
                className={newsTodayRowTone(row)}
                key={`${row.canonicalNewsId}-${index}`}
                onClick={() => void openNews(row)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    void openNews(row);
                  }
                }}
                tabIndex={0}
              >
                <td className="news-today-time-cell" title={row.publishedAtUtc}>
                  <div className="news-today-cell-stack">
                    <strong className="news-today-time-main">{formatTime(row.publishedAtUtc)}</strong>
                    <span className="news-today-date-muted">{formatNewsTableDate(row.publishedAtUtc)}</span>
                    <span>UTC {formatUtcDateTime(row.publishedAtUtc)}</span>
                  </div>
                </td>
                <td className="news-today-ticker-cell" title={newsTodayTickerLabel(row)}>
                  <div className="news-today-chip-row">
                    {newsTodayTickerChips(row).map((ticker) => <span key={ticker}>{ticker}</span>)}
                  </div>
                </td>
                <td className="news-today-title-cell" title={row.title}>
                  <div className="news-today-cell-stack">
                    <strong>{row.title || row.normalizedTitle || "-"}</strong>
                    <span>{row.textPreview || row.normalizedTitle || "No text preview reported."}</span>
                  </div>
                </td>
                <td className="news-today-text-cell" title={newsTodayTextLabel(row)}>{newsTodayTextLabel(row)}</td>
                <td className="news-today-flag-cell" title={row.contentQualityFlags.join(", ")}>
                  <div className="news-today-chip-row muted">
                    {newsTodayFlagChips(row).map((flag) => <span key={flag}>{flag}</span>)}
                  </div>
                </td>
                <td className="news-today-source-cell" title={row.articleUrl || row.urlDomain}>
                  <div className="news-today-cell-stack">
                    <strong>{row.urlDomain || "-"}</strong>
                    <span>{row.author || row.channels.slice(0, 2).join(", ") || "Benzinga"}</span>
                  </div>
                </td>
              </tr>
            ) : (
              <tr key={`empty-${index}`}>
                <td colSpan={6}>
                  {state.loading
                    ? "Loading today's inserted news rows..."
                    : searchQuery.trim()
                      ? "No loaded news rows match this search."
                      : "No inserted news rows found for today's market date."}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedRow ? (
        <Modal className="news-full-detail-modal-panel" onClose={() => { setSelectedRow(null); setDetail(null); setDetailError(""); }} title="Inserted News Detail">
          <NewsTodayDetailModal detail={detail} error={detailError} loading={detailLoading} row={selectedRow} />
        </Modal>
      ) : null}
    </Panel>
  );
}

function NewsTodayDetailModal({ detail, error, loading, row }: { detail: NewsDetailPayload | null; error: string; loading: boolean; row: NewsTodayRow }) {
  const dbRow = isRecord(detail?.row) ? detail.row : {};
  const tickerRows = Array.isArray(detail?.ticker_rows) ? detail.ticker_rows.filter(isRecord) : [];
  const title = stringMetric(dbRow, ["title", "normalized_title"]) || row.title || row.normalizedTitle || "Untitled news row";
  const publishedAt = stringMetric(dbRow, ["published_at_utc"]) || row.publishedAtUtc;
  const downloadedAt = stringMetric(dbRow, ["downloaded_at_utc"]) || row.downloadedAtUtc;
  const articleUrl = stringMetric(dbRow, ["article_url"]) || row.articleUrl;
  const domain = stringMetric(dbRow, ["url_domain"]) || row.urlDomain || "benzinga";
  const author = stringMetric(dbRow, ["author"]) || row.author || "Benzinga";
  const canonicalId = stringMetric(dbRow, ["canonical_news_id"]) || row.canonicalNewsId;
  const providerId = stringMetric(dbRow, ["provider_article_id"]) || row.providerArticleId;
  const tickers = newsDetailTickers(dbRow, tickerRows, row);
  const channels = stringArrayMetric(dbRow, ["channels"]).length ? stringArrayMetric(dbRow, ["channels"]) : row.channels;
  const providerTags = stringArrayMetric(dbRow, ["provider_tags"]).length ? stringArrayMetric(dbRow, ["provider_tags"]) : row.providerTags;
  const qualityFlags = stringArrayMetric(dbRow, ["content_quality_flags"]).length ? stringArrayMetric(dbRow, ["content_quality_flags"]) : row.contentQualityFlags;
  const textCandidates = newsDetailTextCandidates(dbRow, row);
  const primaryText = textCandidates[0] ?? { label: "No Body Text", value: row.textPreview || "No readable body text was returned for this news row." };
  const articleBlocks = newsArticleBlocks(primaryText.value, title, stringMetric(dbRow, ["teaser"]) || row.textPreview);
  const statRows = [
    { label: "Full text", value: numericMetric(dbRow, ["full_text_chars"]) || row.fullTextChars },
    { label: "Body", value: numericMetric(dbRow, ["body_chars"]) || row.bodyChars },
    { label: "External", value: numericMetric(dbRow, ["external_chars"]) || row.externalChars },
    { label: "PDF", value: numericMetric(dbRow, ["pdf_chars"]) || row.pdfChars },
  ].filter((item) => item.value).map((item) => ({ ...item, value: `${formatCompactNumber(item.value)} chars` }));
  const readableFacts = [
    { label: "Provider article", value: providerId || "-" },
    { label: "Canonical row", value: canonicalId || "-" },
    { label: "Downloaded", value: downloadedAt ? formatReadableDateTime(downloadedAt, "UTC") : "-" },
    { label: "Source domain", value: domain || "-" },
    { label: "Author", value: author || "-" },
    { label: "Channels", value: channels.length ? channels.join(", ") : "-" },
    { label: "Provider tags", value: providerTags.length ? providerTags.join(", ") : "-" },
    { label: "Text source", value: primaryText.label },
  ];
  const processingFacts = [
    { label: "External fetch", value: displayName(stringMetric(dbRow, ["external_fetch_status", "external_fetch_error"]) || row.externalFetchStatus || "not reported") },
    { label: "PDF extraction", value: displayName(stringMetric(dbRow, ["pdf_extract_status", "pdf_extract_error"]) || row.pdfExtractStatus || "not reported") },
    { label: "Normalizer", value: stringMetric(dbRow, ["normalizer_version"]) || "-" },
    { label: "Raw artifact", value: stringMetric(dbRow, ["raw_artifact_path"]) || "-" },
  ];
  const remainingRows = Object.entries(dbRow)
    .map(([key, value]) => ({ key, value: formatValue(key, value) }));
  return (
    <div className="news-full-detail">
      <article className="news-full-article-card">
        <header className="news-full-article-header">
          <div className="news-full-article-meta-line">
            <span className="news-full-provider-pill">Benzinga</span>
            <span>{domain}</span>
            <span>{tickers.length ? `${tickers.length} ticker${tickers.length === 1 ? "" : "s"}` : "Market-wide"}</span>
            <span>{qualityFlags.length ? qualityFlags.slice(0, 3).map(displayName).join(" / ") : "No quality flags"}</span>
          </div>
          <h3>{title}</h3>
          <p>{stringMetric(dbRow, ["teaser"]) || row.textPreview || "No summary text was returned for this news row."}</p>
          <div className="news-full-ticker-row">
            {(tickers.length ? tickers : ["No ticker linked"]).slice(0, 18).map((ticker) => (
              <span className={tickers.length ? "news-full-ticker-chip" : "news-full-muted-chip"} key={ticker}>{ticker}</span>
            ))}
            {tickers.length > 18 ? <span className="news-full-muted-chip">+{tickers.length - 18} more</span> : null}
          </div>
        </header>
        <div className="news-full-time-grid">
          <NewsTimeCard label="Market time" timeZone={EXCHANGE_TIME_ZONE} value={publishedAt} />
          <NewsTimeCard label="Vancouver" timeZone={VANCOUVER_TIME_ZONE} value={publishedAt} />
          <NewsTimeCard label="UTC" timeZone="UTC" value={publishedAt} />
        </div>
        <div className="news-full-readable-grid">
          <section className="news-full-readable-main">
            <div className="news-full-section-heading">
              <span>Readable body</span>
              <strong>{primaryText.label}</strong>
            </div>
            <div className="news-full-readable-body">
              {articleBlocks.map((block, index) => (
                block.kind === "list" ? (
                  <ul className="news-full-readable-list" key={`${primaryText.label}-${index}`}>
                    {block.items.map((item, itemIndex) => <li key={`${item}-${itemIndex}`}>{item}</li>)}
                  </ul>
                ) : (
                  <p className={`news-full-readable-${block.kind}`} key={`${primaryText.label}-${index}`}>{block.text}</p>
                )
              ))}
            </div>
          </section>
          <aside className="news-full-readable-side">
            <section>
              <h4>Article Context</h4>
              <dl>
                {readableFacts.map((item) => (
                  <div key={item.label}>
                    <dt>{item.label}</dt>
                    <dd>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </section>
            <section>
              <h4>Processing</h4>
              <dl>
                {processingFacts.map((item) => (
                  <div className={item.label === "Raw artifact" ? "wide" : ""} key={item.label}>
                    <dt>{item.label}</dt>
                    <dd>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </aside>
        </div>
        {articleUrl ? (
          <a className="news-full-source-link" href={articleUrl} rel="noreferrer" target="_blank">
            Open source article
          </a>
        ) : null}
      </article>
      {loading ? <div className="news-full-detail-notice">Loading complete row from ClickHouse...</div> : null}
      {error ? <div className="news-full-detail-notice error">{error}</div> : null}
      <details className="news-full-technical-section">
        <summary>
          <span>Technical details</span>
          <strong>Raw fields, alternate text, ticker links</strong>
        </summary>
        <div className="news-full-technical-content">
          <section className="news-full-text-metrics">
            {(statRows.length ? statRows : [{ label: "Reported text", value: "No text length metadata reported." }]).map((item) => (
              <div key={item.label}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </div>
            ))}
          </section>
          {textCandidates.slice(1).map((section) => (
            <details className="news-full-text-section" key={section.label}>
              <summary>{section.label}</summary>
              <pre>{section.value}</pre>
            </details>
          ))}
          {tickerRows.length ? (
            <section className="news-full-table-section">
              <h4>Ticker Relations</h4>
              <DataTable fitToContent rows={tickerRows.map(normalizeRow)} />
            </section>
          ) : null}
          <section className="news-full-table-section">
            <h4>Actual Database Values</h4>
            <NewsMetadataTable rows={remainingRows} />
          </section>
        </div>
      </details>
    </div>
  );
}

function NewsTimeCard({ label, timeZone, value }: { label: string; timeZone: string; value: string }) {
  return (
    <div className="news-full-time-card">
      <span>{label}</span>
      <strong>{value ? formatReadableDateTime(value, timeZone) : "-"}</strong>
      <small>{timeZone === "UTC" ? "UTC" : timeZone.replace("America/", "")}</small>
    </div>
  );
}

function newsDetailTickers(dbRow: Record<string, unknown>, tickerRows: Record<string, unknown>[], row: NewsTodayRow) {
  const relationTickers = tickerRows
    .map((item) => stringMetric(item, ["ticker", "symbol", "primary_ticker", "normalized_ticker"]))
    .filter(Boolean);
  return uniqueStringSample([...stringArrayMetric(dbRow, ["tickers"]), ...row.tickers, ...row.tickerLinkSample, ...relationTickers], 48);
}

function newsDetailTextCandidates(dbRow: Record<string, unknown>, row: NewsTodayRow) {
  const candidates = [
    { label: "Provider body", value: stringMetric(dbRow, ["body_text"]) },
    { label: "External source text", value: stringMetric(dbRow, ["external_text"]) },
    { label: "PDF extracted text", value: stringMetric(dbRow, ["pdf_text"]) },
    { label: "Normalized full text", value: stringMetric(dbRow, ["normalized_full_text"]) },
    { label: "Teaser", value: stringMetric(dbRow, ["teaser"]) },
    { label: "List preview", value: row.textPreview },
  ];
  return candidates
    .map((candidate) => ({ ...candidate, value: cleanNewsArticleText(candidate.value) }))
    .filter((candidate, index, all) => candidate.value && all.findIndex((item) => item.value === candidate.value) === index);
}

function cleanNewsArticleText(value: string) {
  const normalizedMarkup = value
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<\/\s*p\s*>/gi, "\n\n")
    .replace(/<\s*li\s*>/gi, "\n- ")
    .replace(/<\/\s*li\s*>/gi, "\n")
    .replace(/<[^>]+>/g, " ");
  return decodeNewsHtmlEntities(normalizedMarkup)
    .replace(/\r\n/g, "\n")
    .replace(/\t/g, " ")
    .replace(/[ \u00a0]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

type NewsArticleBlock = { items: string[]; kind: "list"; text?: never } | { items?: never; kind: "lead" | "paragraph" | "subhead"; text: string };

function newsArticleBlocks(value: string, title = "", teaser = ""): NewsArticleBlock[] {
  const cleaned = dedupeNewsBodySentences(stripNewsBodyLeadNoise(cleanNewsArticleText(value), title, teaser));
  if (!cleaned) return [{ kind: "paragraph", text: "No readable body text was returned for this news row." }];
  const paragraphBlocks = cleaned.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);
  const blocks = paragraphBlocks.length > 1 ? paragraphBlocks : splitLongNewsParagraph(cleaned);
  return blocks.slice(0, 48).map((block, index) => {
    const listItems = newsListItems(block);
    if (listItems.length >= 2) return { items: listItems, kind: "list" };
    if (index === 0 && block.length > 80) return { kind: "lead", text: block };
    if (isNewsSubhead(block)) return { kind: "subhead", text: block.replace(/:$/, "") };
    return { kind: "paragraph", text: block };
  });
}

function stripNewsBodyLeadNoise(value: string, title: string, teaser: string) {
  let stripped = value.trim();
  for (const candidate of [title, teaser].map(cleanNewsArticleText).filter((item) => item.length > 8).sort((a, b) => b.length - a.length)) {
    const escaped = escapeRegExp(candidate);
    stripped = stripped.replace(new RegExp(`^${escaped}[\\s:.-]*`, "i"), "").trim();
  }
  return stripped;
}

function dedupeNewsBodySentences(value: string) {
  return value
    .split(/\n{2,}/)
    .map((paragraph) => {
      const seen = new Set<string>();
      const sentences = paragraph.split(/(?<=[.!?])\s+(?=[A-Z0-9"'])/).map((item) => item.trim()).filter(Boolean);
      const deduped = sentences.filter((sentence) => {
        const key = sentence.toLowerCase().replace(/[^a-z0-9]+/g, "");
        if (key.length < 48) return true;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      return deduped.join(" ");
    })
    .join("\n\n")
    .trim();
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function splitLongNewsParagraph(value: string) {
  const sentences = value.split(/(?<=[.!?])\s+(?=[A-Z0-9"'])/).map((item) => item.trim()).filter(Boolean);
  if (sentences.length <= 1) return [value];
  const chunks: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if (current && `${current} ${sentence}`.length > 720) {
      chunks.push(current);
      current = sentence;
    } else {
      current = current ? `${current} ${sentence}` : sentence;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

function newsListItems(value: string) {
  const lines = value.split("\n").map((line) => line.trim()).filter(Boolean);
  const items = lines
    .map((line) => line.match(/^[-*]\s+(.+)$/)?.[1]?.trim() ?? "")
    .filter(Boolean);
  return items.length === lines.length ? items : [];
}

function isNewsSubhead(value: string) {
  const trimmed = value.trim();
  if (trimmed.length > 96) return false;
  if (trimmed.endsWith(":")) return true;
  const letters = trimmed.replace(/[^A-Za-z]/g, "");
  if (letters.length < 6) return false;
  const uppercase = letters.replace(/[^A-Z]/g, "").length;
  return uppercase / letters.length > 0.72;
}

function decodeNewsHtmlEntities(value: string) {
  if (!value.includes("&")) return value;
  const named: Record<string, string> = {
    amp: "&",
    apos: "'",
    gt: ">",
    ldquo: "\"",
    lsquo: "'",
    lt: "<",
    mdash: "-",
    nbsp: " ",
    ndash: "-",
    quot: "\"",
    rdquo: "\"",
    rsquo: "'",
  };
  return value
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCharCode(Number.parseInt(code, 16)))
    .replace(/&([a-z]+);/gi, (match, name) => named[String(name).toLowerCase()] ?? match);
}

function NewsMetadataTable({ rows }: { rows: Array<{ key: string; value: string }> }) {
  const visibleRows = rows.length ? rows : [{ key: "metadata", value: "No complete database row has been loaded yet." }];
  return (
    <div className="news-full-metadata-wrap">
      <table className="news-full-metadata-table">
        <thead>
          <tr>
            <th>Field</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row) => (
            <tr key={row.key}>
              <td><code>{row.key}</code></td>
              <td><pre>{row.value}</pre></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ServiceWorkPlanPanel({ service }: { service: ServiceStatusPayload }) {
  const groups = serviceWorkGroups(service);
  const visibleGroups = visibleServiceWorkGroups(groups, service.registry.id);
  const newsPollHistory = useNewsPollHistory(service);
  const summaryItems = service.registry.id === "news"
    ? newsWorkPlanSummaryItems(service)
    : serviceWorkPlanSummaryItems(visibleGroups);
  return (
    <Panel className="service-work-plan-panel" title="Service Work Plan">
      <div className="service-work-plan-summary">
        {summaryItems.map((item) => (
          <WorkPlanSummaryItem key={item.label} label={item.label} title={item.title} tone={item.tone} value={item.value} />
        ))}
      </div>
      <div className="service-work-plan-layout">
        <section className="service-work-live-section">
          <ServiceWorkResponsibilityGrid groups={visibleGroups} newsPollHistory={newsPollHistory} service={service} />
        </section>
      </div>
    </Panel>
  );
}

function ServiceWorkResponsibilityGrid({ groups, newsPollHistory, service }: { groups: ServiceWorkGroup[]; newsPollHistory: NewsPollHistoryRow[]; service: ServiceStatusPayload }) {
  const visibleGroups = visibleServiceWorkGroups(groups, service.registry.id);
  return (
    <div className="service-work-responsibility-grid">
      {visibleGroups.map((group) => group.id === "live" && service.registry.id === "news" ? (
        <NewsBenzingaLiveCard group={group} history={newsPollHistory} key={group.id} service={service} />
      ) : group.id === "publish" && service.registry.id === "news" ? (
        <NewsDatabasePublishingCard group={group} key={group.id} service={service} />
      ) : group.id === "processing" && service.registry.id === "news" ? (
        <NewsEnrichmentCanonicalCard group={group} key={group.id} service={service} />
      ) : group.id === "coverage" && service.registry.id === "news" ? (
        <NewsCoverageGapCard group={group} key={group.id} service={service} />
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
  const summary = newsPollHistorySummary(history);
  const backgroundPending = numericMetric(metrics, ["background_pending_articles", "publish_pending_rows", "background_queue_size"]);
  const liveBadge = newsLiveBadge(service, history);
  return (
    <section className={`service-work-responsibility-card news-live-card ${workStatusClass(group.status)}`}>
      <div className="service-work-responsibility-header news-live-card-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${liveBadge.className}`}>{liveBadge.label}</span>
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
        <span><small>Avg Fetched</small><strong>{formatCompactNumber(summary.avgProviderRows)}</strong></span>
        <span><small>Avg Unique</small><strong>{formatCompactNumber(summary.avgUniqueRows)}</strong></span>
        <span><small>Avg Duplicate</small><strong>{formatCompactNumber(summary.avgDuplicateRows)}</strong></span>
        <span><small>Avg Runtime</small><strong>{formatSeconds(summary.avgWallSeconds)}</strong></span>
        <span><small>Pending</small><strong>{formatCompactNumber(backgroundPending)}</strong></span>
      </div>
      <NewsPollHistoryTable rows={history} />
    </section>
  );
}

function NewsDatabasePublishingCard({ group, service }: { group: ServiceWorkGroup; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const history = newsPublishHistoryRows(service);
  const status = String(metrics.publish_status || group.status || "idle");
  const insertedRows = numericMetric(metrics, ["written_rows"]);
  const tickerRows = numericMetric(metrics, ["ticker_rows_written"]);
  const skippedRows = numericMetric(metrics, ["skipped_existing"]);
  const failedJobs = numericMetric(metrics, ["publish_failed_jobs"]);
  return (
    <section className={`service-work-responsibility-card news-publish-card ${workStatusClass(status)}`}>
      <div className="service-work-responsibility-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${workStatusClass(status)}`}>{displayName(status)}</span>
      </div>
      <div className="news-live-summary news-publish-summary">
        <span><small>Active</small><strong>{formatCompactNumber(numericMetric(metrics, ["publish_active_jobs"]))}</strong></span>
        <span><small>Pending Rows</small><strong>{formatCompactNumber(numericMetric(metrics, ["publish_pending_rows"]))}</strong></span>
        <span className={insertedRows > 0 ? "metric-good" : ""}><small>Inserted</small><strong>{formatCompactNumber(insertedRows)}</strong></span>
        <span><small>Ticker Links</small><strong>{formatCompactNumber(tickerRows)}</strong></span>
        <span className={skippedRows > 0 ? "metric-warn" : ""}><small>Skipped</small><strong>{formatCompactNumber(skippedRows)}</strong></span>
        <span className={failedJobs > 0 ? "metric-bad" : ""}><small>Failed Jobs</small><strong>{formatCompactNumber(failedJobs)}</strong></span>
      </div>
      <NewsPublishHistoryTable rows={history} />
    </section>
  );
}

function NewsEnrichmentCanonicalCard({ group, service }: { group: ServiceWorkGroup; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const history = newsEnrichmentHistoryRows(service);
  const pendingArticles = numericMetric(metrics, ["background_pending_articles"]);
  const activeBatches = numericMetric(metrics, ["background_active_batches"]);
  const completedArticles = numericMetric(metrics, ["background_completed_articles"]);
  const enrichedUrls = numericMetric(metrics, ["background_enriched_urls"]);
  const failedArticles = numericMetric(metrics, ["background_failed_articles"]);
  const fetchTasks = numericMetric(metrics, ["background_fetch_tasks"]);
  const status = failedArticles > 0 ? "warning" : activeBatches > 0 || pendingArticles > 0 ? "running" : completedArticles > 0 ? "complete" : group.status;
  return (
    <section className={`service-work-responsibility-card news-publish-card news-enrichment-card ${workStatusClass(status)}`}>
      <div className="service-work-responsibility-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${workStatusClass(status)}`}>{displayName(status || "idle")}</span>
      </div>
      <div className="news-live-summary news-publish-summary">
        <span className={pendingArticles > 0 ? "metric-warn" : ""}><small>Pending</small><strong>{formatCompactNumber(pendingArticles)}</strong></span>
        <span><small>Active</small><strong>{formatCompactNumber(activeBatches)}</strong></span>
        <span className={completedArticles > 0 ? "metric-good" : ""}><small>Done</small><strong>{formatCompactNumber(completedArticles)}</strong></span>
        <span className={enrichedUrls > 0 ? "metric-good" : ""}><small>URL Text</small><strong>{formatCompactNumber(enrichedUrls)}</strong></span>
        <span><small>Fetch Tasks</small><strong>{formatCompactNumber(fetchTasks)}</strong></span>
        <span className={failedArticles > 0 ? "metric-bad" : ""}><small>Failed</small><strong>{formatCompactNumber(failedArticles)}</strong></span>
      </div>
      <NewsEnrichmentHistoryTable rows={history} />
    </section>
  );
}

function NewsCoverageGapCard({ group, service }: { group: ServiceWorkGroup; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const history = newsCoverageHistoryRows(service);
  const gapStatus = stringMetric(metrics, ["gap_status"]) || group.status || "idle";
  const totalChunks = numericMetric(metrics, ["gap_fill_total_chunks"]);
  const flushedChunks = numericMetric(metrics, ["gap_fill_flushed_chunks"]);
  const submittedChunks = numericMetric(metrics, ["gap_fill_submitted_chunks"]);
  const inFlightChunks = numericMetric(metrics, ["gap_fill_in_flight_chunks"]);
  const probeCompleted = numericMetric(metrics, ["bootstrap_probe_completed"]);
  const probeTotal = numericMetric(metrics, ["bootstrap_probe_total"]);
  const manualScript = stringMetric(metrics, ["manual_gap_fill_script_win"]);
  const statusClass = coverageStatusClass(gapStatus, { inFlightChunks, totalChunks });
  const latestGapCount = history.find((row) => row.gapCount > 0)?.gapCount ?? 0;
  const latestScript = manualScript || history.find((row) => row.script)?.script || "";
  const gapCount = numericMetric(metrics, ["gap_count", "gaps"]) || latestGapCount;
  return (
    <section className={`service-work-responsibility-card news-publish-card news-coverage-card ${statusClass}`}>
      <div className="service-work-responsibility-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${statusClass}`}>{coverageStatusLabel(gapStatus)}</span>
      </div>
      <div className="news-live-summary news-publish-summary news-coverage-summary">
        <span className={statusClass === "ok" ? "metric-good" : statusClass === "error" ? "metric-bad" : statusClass === "warn" ? "metric-warn" : ""}>
          <small>Status</small><strong>{coverageStatusLabel(gapStatus)}</strong>
        </span>
        <span><small>Gaps</small><strong>{formatCompactNumber(gapCount)}</strong></span>
        <span className={totalChunks > 0 && flushedChunks >= totalChunks ? "metric-good" : totalChunks > 0 ? "metric-warn" : ""}>
          <small>Chunks</small><strong>{totalChunks ? `${formatCompactNumber(flushedChunks)}/${formatCompactNumber(totalChunks)}` : "-"}</strong>
        </span>
        <span><small>In Flight</small><strong>{formatCompactNumber(inFlightChunks)}</strong></span>
        <span><small>Probes</small><strong>{probeTotal ? `${formatCompactNumber(probeCompleted)}/${formatCompactNumber(probeTotal)}` : "-"}</strong></span>
        <span className={latestScript ? "metric-warn" : ""}><small>Manual</small><strong>{latestScript ? "Ready" : "-"}</strong></span>
      </div>
      <NewsCoverageHistoryTable rows={history} />
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
  const defaultWindow = useMemo(() => defaultNewsHistogramWindow(binSeconds), [binSeconds]);
  const effectiveWindowStartUtc = windowStartUtc || defaultWindow.windowStartUtc;
  const effectiveWindowEndUtc = windowEndUtc || defaultWindow.windowEndUtc;
  const effectiveData = useMemo(
    () => data.length ? elapsedNewsHistogramRows(data, effectiveWindowStartUtc, effectiveWindowEndUtc, binSeconds) : defaultWindow.rows,
    [binSeconds, data, defaultWindow.rows, effectiveWindowEndUtc, effectiveWindowStartUtc],
  );
  const displayData = useMemo(
    () => newsHistogramFullWindowRows(effectiveData, effectiveWindowStartUtc, effectiveWindowEndUtc, binSeconds),
    [binSeconds, effectiveData, effectiveWindowEndUtc, effectiveWindowStartUtc],
  );
  const [hover, setHover] = useState<{ broad: number; et: string; single: number; utc: string; van: string } | null>(null);
  const maxTotal = useMemo(() => Math.max(1, ...displayData.map((row) => row.totalRows)), [displayData]);

  const singleTotal = effectiveData.reduce((sum, row) => sum + row.singleTickerRows, 0);
  const broadTotal = effectiveData.reduce((sum, row) => sum + row.broadOrNoneRows, 0);
  const total = singleTotal + broadTotal;
  return (
    <div className="news-live-histogram">
      <div className="news-live-histogram-label">
        <span>Today from DB / {formatNewsBinDuration(binSeconds)} bins</span>
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
      <div
        className="news-live-histogram-chart"
        onMouseLeave={() => setHover(null)}
        style={{ "--histogram-bin-count": displayData.length } as CSSProperties}
      >
        {displayData.map((row) => (
          <div
            aria-label={`${formatZoneDateTime(new Date(Date.parse(row.bucketUtc)), EXCHANGE_TIME_ZONE)}: ${row.singleTickerRows} one-ticker, ${row.broadOrNoneRows} broad`}
            className={row.totalRows > 0 ? "news-live-histogram-bin has-data" : "news-live-histogram-bin"}
            key={row.bucketUtc}
            onMouseEnter={() => setHover(newsHistogramHover(row))}
            style={{ "--bar-height": `${newsHistogramBarHeight(row.totalRows, maxTotal)}%` } as CSSProperties}
          >
            {row.totalRows > 0 ? (
              <span className="news-live-histogram-stack">
                <span className="news-live-histogram-segment broad" style={{ height: `${(row.broadOrNoneRows / row.totalRows) * 100}%` }} />
                <span className="news-live-histogram-segment single" style={{ height: `${(row.singleTickerRows / row.totalRows) * 100}%` }} />
              </span>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function NewsPollHistoryTable({ rows }: { rows: NewsPollHistoryRow[] }) {
  return (
    <div className="news-poll-history-table-wrap">
      <table className="news-poll-history-table">
        <thead>
          <tr>
            <th title="Gateway poll run number. Higher values are newer polls.">Poll</th>
            <th title="When this poll completed, shown in your local browser timezone.">Time</th>
            <th title="Rows returned by the Benzinga provider before duplicate filtering.">Fetched</th>
            <th title="Provider rows that were new within this poll batch.">Unique</th>
            <th title="Rows repeated inside the provider response or already represented in the batch.">Duplicate</th>
            <th title="Rows skipped because they already existed in the database.">Skipped</th>
            <th title="Rows that failed processing or persistence in this poll.">Failed</th>
            <th title="Total wall-clock runtime for this poll in seconds.">Sec</th>
          </tr>
        </thead>
        <tbody>
          {(rows.length ? rows : [null]).map((row, index) => row ? (
            <tr className={workStatusClass(row.status)} key={row.signature}>
              <td>{formatCompactNumber(row.pollRun)}</td>
              <td title={row.pollAt}>{formatLogTime(row.pollAt)}</td>
              <td>{formatCompactNumber(row.providerRows)}</td>
              <td>{formatCompactNumber(row.uniqueRows)}</td>
              <td>{formatCompactNumber(row.duplicateRows)}</td>
              <td>{formatCompactNumber(row.skippedExisting)}</td>
              <td>{formatCompactNumber(row.failedRows)}</td>
              <td>{formatSeconds(row.wallSeconds)}</td>
            </tr>
          ) : (
            <tr key={`empty-${index}`}>
              <td colSpan={8}>No poll has been observed by this dashboard yet.</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NewsPublishHistoryTable({ rows }: { rows: NewsPublishHistoryRow[] }) {
  const [selectedRow, setSelectedRow] = useState<NewsPublishHistoryRow | null>(null);
  return (
    <>
      <div className="news-publish-history-table-wrap">
        <table className="news-publish-history-table">
          <thead>
            <tr>
              <th title="When the publish event was logged, shown in your local browser timezone.">Time</th>
              <th title="Per-news-row publish status reported by the news gateway.">Status</th>
              <th title="Live, live-background, gap-fill, or coverage mode for this publish.">Mode</th>
              <th title="Ticker symbols linked to this news item.">Ticker</th>
              <th title="Whether this item needed URL/PDF enrichment and its enrichment state.">Enrichment</th>
              <th title="One when this news row was inserted into ClickHouse, otherwise zero.">Inserted</th>
              <th title="One when this news row was skipped because it was already present or duplicated in the input batch.">Skipped</th>
            </tr>
          </thead>
          <tbody>
            {(rows.length ? rows : [null]).map((row, index) => row ? (
              <tr
                className={workStatusClass(row.status)}
                key={`${row.event}-${row.pollId}-${row.time}-${index}`}
                onClick={() => setSelectedRow(row)}
                tabIndex={0}
                title={row.title || "Open publish detail"}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedRow(row);
                  }
                }}
              >
                <td title={row.time}>{formatLogTime(row.time)}</td>
                <td><span className={`service-work-mini-status ${workStatusClass(row.status)}`}>{displayName(row.event)}</span></td>
                <td>{displayName(row.coverageMode)}</td>
                <td title={row.tickers}>{row.tickers}</td>
                <td title={row.enrichment}>{row.enrichment}</td>
                <td>{formatCompactNumber(row.insertedRows)}</td>
                <td>{formatCompactNumber(row.skippedRows)}</td>
              </tr>
            ) : (
              <tr key={`empty-${index}`}>
                <td colSpan={7}>No non-empty publish event has been observed by this dashboard yet.</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedRow ? (
        <Modal className="news-publish-detail-modal-panel" onClose={() => setSelectedRow(null)} title="News Publish Detail">
          <NewsPublishDetailModal row={selectedRow} />
        </Modal>
      ) : null}
    </>
  );
}

function NewsEnrichmentHistoryTable({ rows }: { rows: NewsEnrichmentHistoryRow[] }) {
  const [selectedRow, setSelectedRow] = useState<NewsEnrichmentHistoryRow | null>(null);
  return (
    <>
      <div className="news-publish-history-table-wrap">
        <table className="news-publish-history-table news-enrichment-history-table">
          <thead>
            <tr>
              <th title="When the enrichment event was logged, shown in your local browser timezone.">Time</th>
              <th title="Background enrichment status for this batch or article.">Status</th>
              <th title="Queue, active worker, completed batch, or failed article stage.">Stage</th>
              <th title="First news title included in this enrichment batch.">Title</th>
              <th title="External domains or URLs being enriched.">URLs</th>
              <th title="External URLs that produced extracted text.">Text</th>
              <th title="Articles that failed enrichment and were published with fallback flags.">Failed</th>
            </tr>
          </thead>
          <tbody>
            {(rows.length ? rows : [null]).map((row, index) => row ? (
              <tr
                className={workStatusClass(row.status)}
                key={`${row.event}-${row.pollId}-${row.time}-${index}`}
                onClick={() => setSelectedRow(row)}
                tabIndex={0}
                title={row.detail || "Open enrichment detail"}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedRow(row);
                  }
                }}
              >
                <td title={row.time}>{formatLogTime(row.time)}</td>
                <td><span className={`service-work-mini-status ${workStatusClass(row.status)}`}>{displayName(row.status)}</span></td>
                <td title={row.title}>{row.title}</td>
                <td title={row.titleSample.join(" | ")}>{row.titleSample[0] || "-"}</td>
                <td title={row.urlSample.join(" | ")}>{enrichmentUrlLabel(row)}</td>
                <td>{formatCompactNumber(row.enrichedUrls)}</td>
                <td>{formatCompactNumber(row.failedArticles)}</td>
              </tr>
            ) : (
              <tr key={`empty-${index}`}>
                <td colSpan={7}>No background enrichment event has been observed by this dashboard yet.</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedRow ? (
        <Modal className="news-publish-detail-modal-panel" onClose={() => setSelectedRow(null)} title="News Enrichment Detail">
          <NewsEnrichmentDetailModal row={selectedRow} />
        </Modal>
      ) : null}
    </>
  );
}

function NewsCoverageHistoryTable({ rows }: { rows: NewsCoverageHistoryRow[] }) {
  const [selectedRow, setSelectedRow] = useState<NewsCoverageHistoryRow | null>(null);
  return (
    <>
      <div className="news-publish-history-table-wrap">
        <table className="news-publish-history-table news-coverage-history-table">
          <thead>
            <tr>
              <th title="When this coverage, gap-fill, or backfill event was logged.">Time</th>
              <th title="Lifecycle status derived from the coverage event.">Status</th>
              <th title="Coverage work stage, such as bootstrap, provider probe, or gap-fill.">Stage</th>
              <th title="UTC window covered or inspected by this event.">Window</th>
              <th title="Progress through chunks or provider probes.">Progress</th>
              <th title="Rows observed, processed, or written by this coverage event.">Rows</th>
              <th title="Readable summary of the coverage action.">Detail</th>
            </tr>
          </thead>
          <tbody>
            {(rows.length ? rows : [null]).map((row, index) => row ? (
              <tr
                className={workStatusClass(row.status)}
                key={`${row.event}-${row.time}-${index}`}
                onClick={() => setSelectedRow(row)}
                tabIndex={0}
                title={row.detail || "Open coverage detail"}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedRow(row);
                  }
                }}
              >
                <td title={row.time}>{formatLogTime(row.time)}</td>
                <td><span className={`service-work-mini-status ${workStatusClass(row.status)}`}>{displayName(row.status)}</span></td>
                <td title={row.stage}>{row.stage}</td>
                <td title={row.window}>{row.window}</td>
                <td>{row.progress}</td>
                <td>{formatCompactNumber(row.rows)}</td>
                <td title={row.detail}>{row.detail}</td>
              </tr>
            ) : (
              <tr key={`empty-${index}`}>
                <td colSpan={7}>No coverage, gap-fill, or backfill event has been observed by this dashboard yet.</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedRow ? (
        <Modal className="news-publish-detail-modal-panel" onClose={() => setSelectedRow(null)} title="Coverage / Gap Fill Detail">
          <NewsCoverageDetailModal row={selectedRow} />
        </Modal>
      ) : null}
    </>
  );
}

function NewsPublishDetailModal({ row }: { row: NewsPublishHistoryRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="news-publish-detail">
      <div className={`news-publish-detail-status ${statusClass}`}>
        <span>{displayName(row.event)}</span>
        <strong>{row.title || "Untitled news row"}</strong>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Logged At</dt>
          <dd>{row.time ? formatLogTime(row.time) : "-"}</dd>
        </div>
        <div>
          <dt>Published At</dt>
          <dd>{row.publishedAt ? formatLogTime(row.publishedAt) : "-"}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{displayName(row.coverageMode)}</dd>
        </div>
        <div>
          <dt>Tickers</dt>
          <dd>{row.tickers || "-"}</dd>
        </div>
        <div>
          <dt>Inserted</dt>
          <dd>{formatCompactNumber(row.insertedRows)}</dd>
        </div>
        <div>
          <dt>Skipped</dt>
          <dd>{formatCompactNumber(row.skippedRows)}</dd>
        </div>
        <div>
          <dt>Ticker Links</dt>
          <dd>{formatCompactNumber(row.tickerRows)}</dd>
        </div>
        <div>
          <dt>Poll ID</dt>
          <dd>{row.pollId || "-"}</dd>
        </div>
        <div>
          <dt>Provider Article ID</dt>
          <dd>{row.providerArticleId || "-"}</dd>
        </div>
        <div>
          <dt>Canonical News ID</dt>
          <dd>{row.canonicalNewsId || "-"}</dd>
        </div>
        <div className="wide">
          <dt>Enrichment</dt>
          <dd>{row.enrichment || "-"}</dd>
        </div>
        <div className="wide">
          <dt>Quality Flags</dt>
          <dd>{row.qualityFlags.length ? row.qualityFlags.join(", ") : "-"}</dd>
        </div>
      </dl>
    </div>
  );
}

function NewsEnrichmentDetailModal({ row }: { row: NewsEnrichmentHistoryRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="news-publish-detail">
      <div className={`news-publish-detail-status ${statusClass}`}>
        <span>{displayName(row.status)}</span>
        <strong>{row.title || "Background enrichment event"}</strong>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Logged At</dt>
          <dd>{row.time ? formatLogTime(row.time) : "-"}</dd>
        </div>
        <div>
          <dt>Event</dt>
          <dd>{displayName(row.event)}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{displayName(row.mode)}</dd>
        </div>
        <div>
          <dt>Poll ID</dt>
          <dd>{row.pollId || "-"}</dd>
        </div>
        <div>
          <dt>Worker</dt>
          <dd>{row.worker || "-"}</dd>
        </div>
        <div>
          <dt>Queue Size</dt>
          <dd>{formatCompactNumber(row.queueSize)}</dd>
        </div>
        <div>
          <dt>Articles</dt>
          <dd>{formatCompactNumber(row.articleCount)}</dd>
        </div>
        <div>
          <dt>Fetch Tasks</dt>
          <dd>{formatCompactNumber(row.fetchTasks)}</dd>
        </div>
        <div>
          <dt>Extracted URL Text</dt>
          <dd>{formatCompactNumber(row.enrichedUrls)}</dd>
        </div>
        <div>
          <dt>Failed Articles</dt>
          <dd>{formatCompactNumber(row.failedArticles)}</dd>
        </div>
        <div>
          <dt>Runtime</dt>
          <dd>{row.wallSeconds ? formatSeconds(row.wallSeconds) : "-"}</dd>
        </div>
        <div>
          <dt>Provider Article ID</dt>
          <dd>{row.providerArticleId || "-"}</dd>
        </div>
        <div className="wide">
          <dt>News Titles</dt>
          <dd>{row.titleSample.length ? row.titleSample.join(" | ") : "-"}</dd>
        </div>
        <div className="wide">
          <dt>Enrichment URLs</dt>
          <dd>{row.urlSample.length ? row.urlSample.join(" | ") : "-"}</dd>
        </div>
        <div className="wide">
          <dt>Domains</dt>
          <dd>{row.domainSample.length ? row.domainSample.join(", ") : "-"}</dd>
        </div>
        <div className="wide">
          <dt>Detail</dt>
          <dd>{row.detail || "-"}</dd>
        </div>
      </dl>
      {row.items.length ? (
        <section className="news-enrichment-relation-section">
          <div className="news-enrichment-relation-heading">
            <span>Article Relation</span>
            <strong>{formatCompactNumber(row.items.length)} item{row.items.length === 1 ? "" : "s"}</strong>
          </div>
          <div className="news-enrichment-relation-table-wrap">
            <table className="news-enrichment-relation-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>URLs</th>
                  <th>Tickers</th>
                  <th>Provider ID</th>
                  <th>Fetch</th>
                </tr>
              </thead>
              <tbody>
                {row.items.map((item, index) => (
                  <tr key={`${item.canonicalNewsId || item.providerArticleId || item.title}-${index}`}>
                    <td title={item.title}>{item.title || "-"}</td>
                    <td title={item.urlSample.join(" | ") || item.domainSample.join(", ")}>
                      {newsEnrichmentArticleUrlLabel(item)}
                    </td>
                    <td>{item.tickers || "-"}</td>
                    <td title={item.providerArticleId || item.canonicalNewsId}>
                      {item.providerArticleId || shortPollId(item.canonicalNewsId) || "-"}
                    </td>
                    <td>
                      <span className={`service-work-mini-status ${item.requiresEnrichment ? "active" : "idle"}`}>
                        {item.externalFetchStatus ? displayName(item.externalFetchStatus) : item.requiresEnrichment ? "needed" : "not needed"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="news-enrichment-debug-list">
            {row.items.map((item, index) => (
              <NewsEnrichmentArticleDebugCard item={item} key={`${item.canonicalNewsId || item.providerArticleId || item.title}-debug-${index}`} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function NewsEnrichmentArticleDebugCard({ item }: { item: NewsEnrichmentArticleRow }) {
  return (
    <article className="news-enrichment-debug-card">
      <header>
        <span>{item.tickers || "No ticker"}</span>
        <strong>{item.title || "Untitled enrichment item"}</strong>
      </header>
      <dl className="news-enrichment-debug-meta">
        <div><dt>Provider ID</dt><dd>{item.providerArticleId || "-"}</dd></div>
        <div><dt>Canonical ID</dt><dd>{item.canonicalNewsId || "-"}</dd></div>
        <div><dt>Published</dt><dd>{item.publishedAt ? formatLogTime(item.publishedAt) : "-"}</dd></div>
        <div><dt>URL Count</dt><dd>{formatCompactNumber(item.urlCount)}</dd></div>
        <div><dt>Fetch Status</dt><dd>{item.externalFetchStatus ? displayName(item.externalFetchStatus) : item.requiresEnrichment ? "needed" : "not needed"}</dd></div>
        <div><dt>PDF</dt><dd>{item.hasPdf ? "yes" : "no"}</dd></div>
      </dl>
      <DebugObjectBlock title="URLs And Resolution" value={item.urlResolution} />
      <DebugObjectBlock title="Pre-Enriched Normalized Row" value={item.preEnrichedRow} />
      <DebugObjectBlock title="Raw Provider Payload" value={item.providerPayload} />
    </article>
  );
}

function DebugObjectBlock({ title, value }: { title: string; value: Record<string, unknown> }) {
  const rows = Object.entries(value || {});
  if (!rows.length) return null;
  return (
    <section className="debug-object-block">
      <h4>{title}</h4>
      <dl className="debug-object-grid">
        {rows.map(([key, item]) => (
          <div className={debugObjectValueWide(item) ? "wide" : ""} key={key}>
            <dt>{displayName(key)}</dt>
            <dd>{debugObjectValue(item)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function NewsCoverageDetailModal({ row }: { row: NewsCoverageHistoryRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="news-publish-detail">
      <div className={`news-publish-detail-status ${statusClass}`}>
        <span>{displayName(row.status)}</span>
        <strong>{row.stage || "Coverage event"}</strong>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Logged At</dt>
          <dd>{row.time ? formatLogTime(row.time) : "-"}</dd>
        </div>
        <div>
          <dt>Event</dt>
          <dd>{displayName(row.event)}</dd>
        </div>
        <div>
          <dt>Coverage Id</dt>
          <dd>{row.coverageId || "-"}</dd>
        </div>
        <div>
          <dt>Stage</dt>
          <dd>{row.stage || "-"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{displayName(row.status)}</dd>
        </div>
        <div>
          <dt>Window Start</dt>
          <dd>{row.startUtc ? formatLogTime(row.startUtc) : "-"}</dd>
        </div>
        <div>
          <dt>Window End</dt>
          <dd>{row.endUtc ? formatLogTime(row.endUtc) : "-"}</dd>
        </div>
        <div>
          <dt>Gaps</dt>
          <dd>{formatCompactNumber(row.gapCount)}</dd>
        </div>
        <div>
          <dt>Chunks</dt>
          <dd>{row.totalChunks ? `${formatCompactNumber(row.chunkCount)}/${formatCompactNumber(row.totalChunks)}` : formatCompactNumber(row.chunkCount)}</dd>
        </div>
        <div>
          <dt>In Flight</dt>
          <dd>{formatCompactNumber(row.inFlight)}</dd>
        </div>
        <div>
          <dt>Rows</dt>
          <dd>{formatCompactNumber(row.rows)}</dd>
        </div>
        <div className="wide">
          <dt>Script</dt>
          <dd>{row.script || "-"}</dd>
        </div>
        <div className="wide">
          <dt>Detail</dt>
          <dd>{row.detail || "-"}</dd>
        </div>
      </dl>
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

function groupPrimaryRow(group: ServiceWorkGroup): ServiceWorkRow {
  const sortedRows = [...group.rows].sort((a, b) => workStatusRank(a.status) - workStatusRank(b.status) || (b.lastAtMs ?? 0) - (a.lastAtMs ?? 0));
  return sortedRows[0] ?? { detail: "No subtask report received in the current snapshot.", kind: "service", lastAt: "-", name: "No live report", progress: "-", reportKind: "live", rows: "-", schedule: "-", status: "not reported" };
}

function newsPollHistorySummary(rows: NewsPollHistoryRow[]) {
  const count = Math.max(1, rows.length);
  const sum = rows.reduce(
    (totals, row) => ({
      providerRows: totals.providerRows + row.providerRows,
      uniqueRows: totals.uniqueRows + row.uniqueRows,
      duplicateRows: totals.duplicateRows + row.duplicateRows,
      wallSeconds: totals.wallSeconds + row.wallSeconds,
    }),
    { duplicateRows: 0, providerRows: 0, uniqueRows: 0, wallSeconds: 0 },
  );
  return {
    avgDuplicateRows: sum.duplicateRows / count,
    avgProviderRows: sum.providerRows / count,
    avgUniqueRows: sum.uniqueRows / count,
    avgWallSeconds: sum.wallSeconds / count,
  };
}

function newsPublishHistoryRows(service: ServiceStatusPayload): NewsPublishHistoryRow[] {
  const rows: NewsPublishHistoryRow[] = [];
  for (const logRow of service.logs?.rows ?? []) {
    if (!isNewsPublishLogEvent(logRow.event || "")) continue;
    const fields = isRecord(logRow.fields) ? logRow.fields : {};
    const items = Array.isArray(fields.items) ? fields.items.filter(isRecord) : [];
    if (items.length) {
      items.forEach((item, index) => rows.push(newsPublishItemHistoryRow(logRow, fields, item, index)));
      continue;
    }
    const fallback = newsPublishBatchFallbackRow(logRow, fields);
    if (fallback) rows.push(fallback);
  }
  return rows
    .sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0))
    .slice(0, 50);
}

function newsEnrichmentHistoryRows(service: ServiceStatusPayload): NewsEnrichmentHistoryRow[] {
  return (service.logs?.rows ?? [])
    .filter((row) => isNewsEnrichmentLogEvent(row.event || ""))
    .map(newsEnrichmentHistoryRow)
    .sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0))
    .slice(0, 50);
}

function newsCoverageHistoryRows(service: ServiceStatusPayload): NewsCoverageHistoryRow[] {
  const rows = (service.logs?.rows ?? [])
    .filter((row) => isNewsCoverageLogEvent(row.event || ""))
    .map(newsCoverageHistoryRow)
    .sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0));
  if (rows.length) return compactNewsCoverageHistoryRows(rows).slice(0, 50);
  const metrics = serviceMetricsRecord(service);
  const gapStatus = stringMetric(metrics, ["gap_status"]);
  const gapMessage = stringMetric(metrics, ["gap_message"]);
  if (!gapStatus && !gapMessage) return [];
  return [{
    chunkCount: numericMetric(metrics, ["gap_fill_flushed_chunks"]),
    coverageId: "gap_status_snapshot",
    detail: gapMessage || coverageStatusLabel(gapStatus),
    endUtc: "",
    event: "gap_status_snapshot",
    gapCount: numericMetric(metrics, ["gap_count", "gaps"]),
    inFlight: numericMetric(metrics, ["gap_fill_in_flight_chunks"]),
    progress: coverageProgressLabel(
      numericMetric(metrics, ["gap_fill_flushed_chunks"]),
      numericMetric(metrics, ["gap_fill_total_chunks"]),
      numericMetric(metrics, ["gap_fill_submitted_chunks"]),
      numericMetric(metrics, ["gap_fill_in_flight_chunks"]),
    ),
    rows: 0,
    script: stringMetric(metrics, ["manual_gap_fill_script_win"]),
    stage: "current status",
    startUtc: "",
    status: gapStatus || "observed",
    time: service.checked_at_utc || "",
    totalChunks: numericMetric(metrics, ["gap_fill_total_chunks"]),
    window: "-",
  }];
}

function compactNewsCoverageHistoryRows(rows: NewsCoverageHistoryRow[]) {
  const seen = new Set<string>();
  const compactRows: NewsCoverageHistoryRow[] = [];
  for (const row of rows) {
    const key = newsCoverageHistoryJobKey(row);
    if (seen.has(key)) continue;
    seen.add(key);
    compactRows.push(row);
  }
  return compactRows;
}

function newsCoverageHistoryJobKey(row: NewsCoverageHistoryRow) {
  if (row.coverageId) return `coverage:${row.event}:${row.coverageId}`;
  if (row.event === "coverage_live_snapshot_written" || row.event === "coverage_gap_snapshot_written") {
    return `coverage:${row.event}:${row.startUtc || row.stage || "unknown"}`;
  }
  if (row.event.startsWith("gap_fill_")) {
    return [
      "gap-fill",
      row.startUtc || "-",
      row.endUtc || "-",
      row.script || "",
    ].join("|");
  }
  return [
    row.event,
    row.stage,
    row.status,
    row.startUtc || "-",
    row.endUtc || "-",
    row.window || "-",
    row.script || "",
  ].join("|");
}

function isNewsPublishLogEvent(event: string) {
  return event === "publish_completed"
    || event === "publish_failed";
}

function isNewsEnrichmentLogEvent(event: string) {
  return event === "background_batch_queued"
    || event === "background_batch_started"
    || event === "background_batch_completed"
    || event === "background_article_enrichment_failed"
    || event === "background_batch_failed_uncaught"
    || event === "live_url_download_not_downloaded"
    || event === "shutdown_waiting_for_background_news"
    || event === "shutdown_background_drained"
    || event === "shutdown_background_timeout";
}

function isNewsCoverageLogEvent(event: string) {
  return event === "startup_gap_plan"
    || event === "gap_fill_started"
    || event === "gap_fill_progress"
    || event === "gap_fill_finished"
    || event === "coverage_bootstrap_completed"
    || event === "coverage_bootstrap_skipped"
    || event === "coverage_manifest_compacted"
    || event === "coverage_gap_provider_probe_plan"
    || event === "coverage_gap_provider_probe_started"
    || event === "coverage_gap_provider_probe_failed"
    || event === "coverage_gap_provider_probe"
    || event === "coverage_live_snapshot_written"
    || event === "coverage_gap_snapshot_written";
}

function newsCoverageHistoryRow(logRow: ServiceRuntimeLogRow): NewsCoverageHistoryRow {
  const fields = isRecord(logRow.fields) ? logRow.fields : {};
  const event = logRow.event || "coverage";
  const summary = isRecord(fields.summary) ? fields.summary : {};
  const status = coverageEventVisualStatus(event, fields, logRow.level || "");
  const startUtc = stringMetric(fields, ["start_utc", "first_start_utc"]) || stringMetric(summary, ["start_utc", "coverage_start_utc"]);
  const endUtc = stringMetric(fields, ["end_utc", "last_end_utc"]) || stringMetric(summary, ["end_utc", "coverage_end_utc"]);
  const chunkCount = numericMetric(fields, ["flushed", "chunks", "chunk_count", "poll_runs"]);
  const totalChunks = numericMetric(fields, ["total_chunks", "chunks"]);
  return {
    chunkCount,
    coverageId: stringMetric(fields, ["coverage_id", "gap_fill_id", "job_id", "run_id", "task_id"]),
    detail: coverageEventDetail(event, fields, summary, logRow.detail || ""),
    endUtc,
    event,
    gapCount: numericMetric(fields, ["gaps", "gap_count"]) || numericMetric(summary, ["discovered_gap_intervals", "gap_count"]),
    inFlight: numericMetric(fields, ["in_flight"]),
    progress: coverageProgressLabel(
      chunkCount,
      totalChunks,
      numericMetric(fields, ["submitted"]),
      numericMetric(fields, ["in_flight"]),
    ),
    rows: coverageRowsCount(fields, summary),
    script: stringMetric(fields, ["script"]),
    stage: coverageEventStage(event, fields),
    startUtc,
    status,
    time: logRow.ts_utc || "",
    totalChunks,
    window: coverageWindowLabel(startUtc, endUtc),
  };
}

function coverageStatusClass(status: string, progress: { inFlightChunks: number; totalChunks: number }) {
  const normalized = normalizedStatus(status);
  if (/failed|error|manual_required|deferred|no_watermark/.test(normalized)) return "warn";
  if (/auto_running|auto_started|workstation_auto|running|gap_fill|probe|bootstrap/.test(normalized)) return "active";
  if (/auto_completed|covered|bootstrapped|complete|completed|skipped/.test(normalized)) return "ok";
  if (progress.inFlightChunks > 0 || progress.totalChunks > 0) return "active";
  return workStatusClass(status);
}

function coverageStatusLabel(status: string) {
  if (!status) return "idle";
  const normalized = normalizedStatus(status);
  if (normalized === "covered_by_live_lookback") return "covered";
  if (normalized === "manual_required_large_gap") return "manual required";
  if (normalized === "workstation_deferred_large_gap_market_window") return "deferred";
  if (normalized === "workstation_auto_started_large_gap") return "workstation running";
  if (normalized === "coverage_bootstrapped") return "bootstrapped";
  return displayName(status);
}

function coverageEventVisualStatus(event: string, fields: Record<string, unknown>, level: string) {
  const explicit = stringMetric(fields, ["status"]);
  const text = normalizedStatus(`${event} ${explicit} ${level}`);
  if (/failed|error/.test(text)) return "failed";
  if (/manual_required|deferred|positive|gap_requires_fill/.test(text)) return "warning";
  if (/started|progress|running|probe/.test(text)) return "running";
  if (/finished|completed|skipped|compacted|written|covered_empty|covered|bootstrapped/.test(text)) return "complete";
  return explicit || "observed";
}

function coverageEventStage(event: string, fields: Record<string, unknown>) {
  if (event === "startup_gap_plan") return "startup plan";
  if (event === "gap_fill_started") return "gap-fill start";
  if (event === "gap_fill_progress") return "gap-fill progress";
  if (event === "gap_fill_finished") return "gap-fill finished";
  if (event === "coverage_bootstrap_completed") return "bootstrap completed";
  if (event === "coverage_bootstrap_skipped") return "bootstrap skipped";
  if (event === "coverage_manifest_compacted") return "manifest compacted";
  if (event === "coverage_gap_provider_probe_plan") return "probe plan";
  if (event === "coverage_gap_provider_probe_started") return `probe ${formatCompactNumber(numericMetric(fields, ["probe_index"]))}`;
  if (event === "coverage_gap_provider_probe_failed") return "probe failed";
  if (event === "coverage_gap_provider_probe") return stringMetric(fields, ["decision"]) || "probe result";
  if (event === "coverage_live_snapshot_written") return "live coverage";
  if (event === "coverage_gap_snapshot_written") return "gap coverage";
  return displayName(event);
}

function coverageEventDetail(event: string, fields: Record<string, unknown>, summary: Record<string, unknown>, fallback: string) {
  if (event === "coverage_bootstrap_completed") {
    return [
      `chunk=${formatCompactNumber(numericMetric(summary, ["chunk_seconds"]))}s`,
      `covered=${formatCompactNumber(numericMetric(summary, ["covered_intervals"]))}`,
      `gaps=${formatCompactNumber(numericMetric(summary, ["discovered_gap_intervals"]))}`,
      `unique_days=${formatCompactNumber(numericMetric(summary, ["discovered_gap_unique_days"]))}`,
    ].join("; ");
  }
  if (event === "coverage_bootstrap_skipped") {
    return `status=${stringMetric(summary, ["status"]) || stringMetric(fields, ["status"]) || "skipped"}; chunk=${formatCompactNumber(numericMetric(summary, ["chunk_seconds"]))}s`;
  }
  if (event === "startup_gap_plan") {
    return [
      `status=${coverageStatusLabel(stringMetric(fields, ["status"]))}`,
      `gaps=${formatCompactNumber(numericMetric(fields, ["gaps", "gap_count"]))}`,
      `days=${formatCompactNumber(numericMetric(fields, ["unique_gap_days"]))}`,
      coverageDurationLabel(numericMetric(fields, ["total_gap_seconds"])),
      stringMetric(fields, ["script"]) ? "script ready" : "",
    ].filter(Boolean).join("; ");
  }
  if (event === "gap_fill_progress") {
    return [
      `flushed=${formatCompactNumber(numericMetric(fields, ["flushed"]))}/${formatCompactNumber(numericMetric(fields, ["total_chunks"]))}`,
      `submitted=${formatCompactNumber(numericMetric(fields, ["submitted"]))}`,
      `in_flight=${formatCompactNumber(numericMetric(fields, ["in_flight"]))}`,
    ].join("; ");
  }
  if (event === "gap_fill_started") {
    return [
      `${formatCompactNumber(numericMetric(fields, ["chunks"]))} chunks`,
      `${formatCompactNumber(numericMetric(fields, ["workers"]))} workers`,
      `chunk=${formatCompactNumber(numericMetric(fields, ["chunk_minutes"]))}m`,
    ].join("; ");
  }
  if (event === "coverage_gap_provider_probe" || event === "coverage_gap_provider_probe_started") {
    return [
      coverageProgressLabel(numericMetric(fields, ["probe_index"]), numericMetric(fields, ["probe_total"]), 0, 0),
      `decision=${stringMetric(fields, ["decision"]) || "-"}`,
      `rows=${formatCompactNumber(numericMetric(fields, ["rows_seen"]))}`,
      `pages=${formatCompactNumber(numericMetric(fields, ["pages"]))}`,
    ].join("; ");
  }
  if (event === "coverage_live_snapshot_written" || event === "coverage_gap_snapshot_written") {
    return [
      `status=${displayName(stringMetric(fields, ["status"]))}`,
      `polls=${formatCompactNumber(numericMetric(fields, ["poll_runs"]))}`,
      `provider=${formatCompactNumber(numericMetric(fields, ["provider_rows"]))}`,
      `processed=${formatCompactNumber(numericMetric(fields, ["processed_rows"]))}`,
      `written=${formatCompactNumber(numericMetric(fields, ["written_rows"]))}`,
    ].join("; ");
  }
  if (event === "coverage_manifest_compacted") {
    return [
      `status=${stringMetric(summary, ["status"]) || "reported"}`,
      `active=${formatCompactNumber(numericMetric(summary, ["active_intervals"]))}`,
      `merged=${formatCompactNumber(numericMetric(summary, ["merged_intervals"]))}`,
      `inserted=${formatCompactNumber(numericMetric(summary, ["inserted_rows"]))}`,
    ].join("; ");
  }
  return fallback || Object.entries(fields)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 5)
    .map(([key, value]) => `${displayName(key)}=${formatCell(key, value)}`)
    .join("; ");
}

function coverageProgressLabel(done: number, total: number, submitted: number, inFlight: number) {
  if (total > 0) return `${formatCompactNumber(done)}/${formatCompactNumber(total)}`;
  if (submitted > 0 || inFlight > 0) return `${formatCompactNumber(submitted)} submitted`;
  if (done > 0) return formatCompactNumber(done);
  return "-";
}

function coverageRowsCount(fields: Record<string, unknown>, summary: Record<string, unknown>) {
  return numericMetric(fields, ["written_rows", "processed_rows", "provider_rows", "rows_seen"])
    || numericMetric(summary, ["non_empty_buckets", "covered_intervals", "rows"]);
}

function coverageWindowLabel(startUtc: string, endUtc: string) {
  if (!startUtc && !endUtc) return "-";
  const start = startUtc ? formatShortUtcWindowTime(startUtc) : "-";
  const end = endUtc ? formatShortUtcWindowTime(endUtc) : "-";
  return `${start} -> ${end}`;
}

function formatShortUtcWindowTime(value: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(parsed));
}

function coverageDurationLabel(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86_400).toFixed(1)}d`;
}

function newsEnrichmentHistoryRow(logRow: ServiceRuntimeLogRow): NewsEnrichmentHistoryRow {
  const fields = isRecord(logRow.fields) ? logRow.fields : {};
  const event = logRow.event || "background";
  const status = enrichmentEventVisualStatus(event);
  const articleCount = numericMetric(fields, ["article_count", "processed_rows", "pending_articles"]);
  const failedArticles = numericMetric(fields, ["article_failures", "failed_articles"]);
  const enrichedUrls = numericMetric(fields, ["enriched_urls"]);
  const fetchTasks = numericMetric(fields, ["fetch_task_count", "url_tasks"]);
  const queueSize = numericMetric(fields, ["queue_size", "pending_batches"]);
  const pollId = stringMetric(fields, ["poll_id"]);
  const items = newsEnrichmentArticleRows(fields);
  const itemTitles = items.map((item) => item.title).filter(Boolean);
  const itemUrls = items.flatMap((item) => item.urlSample).filter(Boolean);
  const itemDomains = items.flatMap((item) => item.domainSample).filter(Boolean);
  const titleSample = uniqueStringSample([
    ...stringArrayMetric(fields, ["enrichment_title_sample", "title_sample"]),
    ...itemTitles,
  ], 8);
  const urlSample = uniqueStringSample([
    ...stringArrayMetric(fields, ["enrichment_url_sample", "url_sample"]),
    ...itemUrls,
  ], 12);
  const domainSample = uniqueStringSample([
    ...stringArrayMetric(fields, ["enrichment_domain_sample", "domain_sample"]),
    ...itemDomains,
  ], 8);
  return {
    articleCount,
    detail: enrichmentEventDetail(event, fields),
    domainSample,
    enrichedUrls,
    event,
    failedArticles,
    fetchTasks,
    mode: stringMetric(fields, ["coverage_mode"]),
    pollId,
    providerArticleId: stringMetric(fields, ["provider_article_id"]),
    queueSize,
    status,
    time: logRow.ts_utc || "",
    title: enrichmentEventTitle(event, fields),
    titleSample,
    items,
    urlSample,
    wallSeconds: numericMetric(fields, ["wall_seconds"]),
    worker: stringMetric(fields, ["worker_index"]),
  };
}

function enrichmentUrlLabel(row: NewsEnrichmentHistoryRow) {
  const itemWithUrl = row.items.find((item) => item.domainSample.length || item.urlSample.length);
  if (itemWithUrl) return newsEnrichmentArticleUrlLabel(itemWithUrl);
  if (row.domainSample.length) {
    const label = row.domainSample.slice(0, 2).join(", ");
    const extra = Math.max(0, row.domainSample.length - 2);
    return extra ? `${label} +${extra}` : label;
  }
  if (row.urlSample.length) {
    const label = row.urlSample[0].replace(/^https?:\/\//i, "").replace(/^www\./i, "");
    return label.length > 34 ? `${label.slice(0, 31)}...` : label;
  }
  return row.fetchTasks ? `${formatCompactNumber(row.fetchTasks)} tasks` : "-";
}

function newsEnrichmentArticleRows(fields: Record<string, unknown>): NewsEnrichmentArticleRow[] {
  const rawItems = Array.isArray(fields.items) ? fields.items.filter(isRecord) : [];
  return rawItems
    .map(newsEnrichmentArticleRow)
    .filter((item) => item.title || item.urlSample.length || item.domainSample.length || item.providerArticleId || item.canonicalNewsId);
}

function newsEnrichmentArticleRow(item: Record<string, unknown>): NewsEnrichmentArticleRow {
  const urlSample = uniqueStringSample(stringArrayMetric(item, ["url_sample", "enrichment_url_sample", "source_url", "url"]), 8);
  const domainSample = uniqueStringSample(stringArrayMetric(item, ["domain_sample", "enrichment_domain_sample"]), 8);
  return {
    canonicalNewsId: stringMetric(item, ["canonical_news_id"]),
    domainSample,
    externalFetchStatus: stringMetric(item, ["external_fetch_status", "source_text_status"]),
    hasPdf: Boolean(item.has_pdf),
    preEnrichedRow: isRecord(item.pre_enriched_row) ? item.pre_enriched_row : {},
    providerArticleId: stringMetric(item, ["provider_article_id"]),
    providerPayload: isRecord(item.provider_payload) ? item.provider_payload : {},
    publishedAt: stringMetric(item, ["published_at_utc", "published_utc", "published"]),
    requiresEnrichment: Boolean(item.requires_enrichment),
    tickers: publishTickerLabel({}, item),
    title: stringMetric(item, ["title", "headline"]),
    urlCount: numericMetric(item, ["url_count"]) || urlSample.length,
    urlResolution: isRecord(item.url_resolution) ? item.url_resolution : {},
    urlSample,
  };
}

function newsEnrichmentArticleUrlLabel(item: NewsEnrichmentArticleRow) {
  if (item.domainSample.length) {
    const label = item.domainSample.slice(0, 2).join(", ");
    const extra = Math.max(0, item.domainSample.length - 2);
    return extra ? `${label} +${extra}` : label;
  }
  if (item.urlSample.length) {
    const label = item.urlSample[0].replace(/^https?:\/\//i, "").replace(/^www\./i, "");
    return label.length > 42 ? `${label.slice(0, 39)}...` : label;
  }
  return item.urlCount ? `${formatCompactNumber(item.urlCount)} URLs` : "-";
}

function enrichmentEventVisualStatus(event: string) {
  if (event.includes("failed") || event.includes("timeout") || event.includes("not_downloaded")) return "failed";
  if (event.includes("started") || event.includes("waiting")) return "running";
  if (event.includes("queued")) return "queued";
  if (event.includes("completed") || event.includes("drained")) return "complete";
  return "observed";
}

function enrichmentEventTitle(event: string, fields: Record<string, unknown>) {
  if (event === "background_batch_queued") return "queued batch";
  if (event === "background_batch_started") return `worker ${stringMetric(fields, ["worker_index"]) || "-"} active`;
  if (event === "background_batch_completed") return "completed batch";
  if (event === "background_article_enrichment_failed") return "article failed";
  if (event === "background_batch_failed_uncaught") return "batch failed";
  if (event === "live_url_download_not_downloaded") return "url not downloaded";
  if (event === "shutdown_waiting_for_background_news") return "shutdown drain";
  if (event === "shutdown_background_drained") return "queue drained";
  if (event === "shutdown_background_timeout") return "drain timeout";
  return displayName(event);
}

function enrichmentEventDetail(event: string, fields: Record<string, unknown>) {
  if (event === "background_batch_completed") {
    return [
      `articles=${formatCompactNumber(numericMetric(fields, ["article_count"]))}`,
      `inserted=${formatCompactNumber(numericMetric(fields, ["normalized_rows_inserted"]))}`,
      `skipped=${formatCompactNumber(numericMetric(fields, ["skipped_existing"]))}`,
      `ticker_links=${formatCompactNumber(numericMetric(fields, ["ticker_rows_inserted"]))}`,
      `text_urls=${formatCompactNumber(numericMetric(fields, ["enriched_urls"]))}`,
    ].join("; ");
  }
  if (event === "background_batch_started") {
    return [
      `poll=${shortPollId(stringMetric(fields, ["poll_id"]))}`,
      `articles=${formatCompactNumber(numericMetric(fields, ["article_count"]))}`,
      `queue=${formatCompactNumber(numericMetric(fields, ["queue_size"]))}`,
    ].join("; ");
  }
  if (event === "background_batch_queued") {
    return [
      `poll=${shortPollId(stringMetric(fields, ["poll_id"]))}`,
      `articles=${formatCompactNumber(numericMetric(fields, ["article_count"]))}`,
      `url_tasks=${formatCompactNumber(numericMetric(fields, ["fetch_task_count"]))}`,
      `queue=${formatCompactNumber(numericMetric(fields, ["queue_size"]))}`,
    ].join("; ");
  }
  if (event === "background_article_enrichment_failed") {
    return [
      `poll=${shortPollId(stringMetric(fields, ["poll_id"]))}`,
      `provider_article_id=${stringMetric(fields, ["provider_article_id"]) || "-"}`,
      `canonical=${shortPollId(stringMetric(fields, ["canonical_news_id"]))}`,
    ].join("; ");
  }
  return Object.entries(fields)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 5)
    .map(([key, value]) => `${displayName(key)}=${formatCell(key, value)}`)
    .join("; ");
}

function newsPublishItemHistoryRow(logRow: ServiceRuntimeLogRow, fields: Record<string, unknown>, item: Record<string, unknown>, index: number): NewsPublishHistoryRow {
  const event = logRow.event || "publish";
  const publishStatus = publishItemStatus(event, item);
  return {
    activeJobs: numericMetric(fields, ["active_jobs"]),
    canonicalNewsId: stringMetric(item, ["canonical_news_id"]),
    coverageMode: stringMetric(fields, ["coverage_mode"]),
    enrichment: publishEnrichmentLabel(fields, item),
    event: publishStatus,
    insertedRows: numericMetric(item, ["inserted_rows"]),
    pendingRows: publishStatus === "pending" ? 1 : 0,
    pollId: `${stringMetric(fields, ["poll_id"])}:${index}`,
    providerArticleId: stringMetric(item, ["provider_article_id"]),
    processedRows: 1,
    publishedAt: stringMetric(item, ["published_at_utc"]) || stringMetric(fields, ["published_at_start_utc"]),
    qualityFlags: Array.isArray(item.quality_flags) ? item.quality_flags.map(String).filter(Boolean) : [],
    skippedRows: numericMetric(item, ["skipped_rows"]),
    status: publishItemVisualStatus(publishStatus),
    tickerRows: Array.isArray(item.tickers) ? item.tickers.length : 0,
    tickers: publishTickerLabel(fields, item),
    title: stringMetric(item, ["title"]) || publishTitleLabel(event, fields, item),
    time: logRow.ts_utc || "",
  };
}

function newsPublishBatchFallbackRow(logRow: ServiceRuntimeLogRow, fields: Record<string, unknown>): NewsPublishHistoryRow | null {
  const processedRows = numericMetric(fields, ["processed_rows", "article_count"]);
  const insertedRows = numericMetric(fields, ["normalized_rows_inserted"]);
  const tickerRows = numericMetric(fields, ["ticker_rows_inserted", "ticker_count"]);
  const skippedRows = numericMetric(fields, ["skipped_existing"]);
  const providerRows = numericMetric(fields, ["provider_rows"]);
  const hasUsefulPublishWork = providerRows > 0 || processedRows > 0 || insertedRows > 0 || tickerRows > 0 || skippedRows > 0;
  if (!hasUsefulPublishWork) return null;
  const event = logRow.event || "publish";
  const publishStatus = event.includes("failed") ? "failed" : event.includes("started") ? "pending" : "batch_summary";
  return {
    activeJobs: numericMetric(fields, ["active_jobs"]),
    canonicalNewsId: "",
    coverageMode: stringMetric(fields, ["coverage_mode"]),
    enrichment: publishEnrichmentLabel(fields, {}),
    event: publishStatus,
    insertedRows,
    pendingRows: numericMetric(fields, ["pending_rows"]),
    pollId: stringMetric(fields, ["poll_id"]),
    providerArticleId: "",
    processedRows,
    publishedAt: stringMetric(fields, ["published_at_start_utc"]),
    qualityFlags: [],
    skippedRows,
    status: publishItemVisualStatus(publishStatus),
    tickerRows,
    tickers: publishTickerLabel(fields, {}),
    title: `${formatCompactNumber(processedRows)} row batch; restart News Gateway for per-row publish status.`,
    time: logRow.ts_utc || "",
  };
}

function publishItemStatus(event: string, item: Record<string, unknown>) {
  const explicit = stringMetric(item, ["publish_status"]);
  if (explicit) return explicit;
  if (event.includes("failed")) return "failed";
  if (event.includes("started")) return "pending";
  if (event.includes("completed")) return "unknown";
  return event || "unknown";
}

function publishItemVisualStatus(status: string) {
  const normalized = status.toLowerCase();
  if (normalized.includes("failed")) return "failed";
  if (normalized.includes("pending")) return "running";
  if (normalized.includes("inserted") || normalized.includes("dry_run")) return "complete";
  if (normalized.includes("skipped") || normalized.includes("duplicate") || normalized.includes("summary")) return "idle";
  return "waiting";
}

function newsLiveBadge(service: ServiceStatusPayload, history: NewsPollHistoryRow[]) {
  if (!service.online) return { className: "error", label: "offline" };
  const metrics = serviceMetricsRecord(service);
  const latest = history[0];
  const failed = latest?.failedRows ?? numericMetric(metrics, ["last_cycle_failed_rows"]);
  if (failed > 0) return { className: "warn", label: "poll issues" };
  const fetched = latest?.providerRows ?? numericMetric(metrics, ["last_cycle_provider_rows"]);
  if (fetched > 0) return { className: "active", label: "polling" };
  return { className: "idle", label: "idle" };
}

function publishTickerLabel(fields: Record<string, unknown>, firstItem: Record<string, unknown>) {
  const candidate = firstItem.tickers ?? fields.ticker_sample;
  if (Array.isArray(candidate)) {
    const labels = candidate.map((item) => String(item || "").trim()).filter(Boolean);
    return labels.length ? labels.slice(0, 5).join(", ") : "-";
  }
  return stringMetric(firstItem, ["ticker", "symbol"]) || "-";
}

function publishEnrichmentLabel(fields: Record<string, unknown>, firstItem: Record<string, unknown>) {
  const status = stringMetric(firstItem, ["external_fetch_status", "enrichment_status"]) || stringMetric(fields, ["external_fetch_status"]);
  const needs = Boolean(firstItem.requires_enrichment ?? fields.requires_enrichment_count);
  const hasPdf = Boolean(firstItem.has_pdf ?? fields.pdf_count);
  const flags = Array.isArray(firstItem.quality_flags) ? firstItem.quality_flags.map(String).filter(Boolean).slice(0, 2) : [];
  const enrichedUrls = numericMetric(fields, ["enriched_urls"]);
  const parts = [needs ? "needs" : "inline", status || "", hasPdf ? "pdf" : "", enrichedUrls ? `${formatCompactNumber(enrichedUrls)} urls` : "", ...flags].filter(Boolean);
  return parts.length ? parts.join(" / ") : "-";
}

function publishTitleLabel(event: string, fields: Record<string, unknown>, firstItem: Record<string, unknown>) {
  const title = stringMetric(firstItem, ["title"]) || stringMetric(fields, ["title_sample"]);
  if (title) return title;
  if (event === "poll_completed") return `poll ${shortPollId(stringMetric(fields, ["poll_id"]))}`;
  if (event === "background_batch_completed") return `${formatCompactNumber(numericMetric(fields, ["article_count"]))} enriched article rows`;
  return shortPollId(stringMetric(fields, ["poll_id"]));
}

function shortPollId(value: string) {
  if (!value) return "-";
  return value.length > 18 ? `${value.slice(0, 8)}...${value.slice(-6)}` : value;
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
  const [payload, setPayload] = useState<NewsDailyHistogramState>(() => defaultNewsHistogramWindow(900));
  useEffect(() => {
    if (!enabled) {
      setPayload(defaultNewsHistogramWindow(900));
      return undefined;
    }
    let cancelled = false;
    async function load() {
      try {
        const response = await api<NewsHistogramPayload>("/api/services/news/histogram");
        if (cancelled) return;
        const binSeconds = Number(response.bin_seconds || 900);
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
            binSeconds,
          ),
          windowEndUtc,
          windowStartUtc,
        });
      } catch (exc) {
        if (cancelled) return;
        setPayload({ ...defaultNewsHistogramWindow(900), error: exc instanceof Error ? exc.message : String(exc) });
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

function useNewsTodayRows(enabled: boolean, sort: NewsTodaySort): NewsTodayRowsState {
  const [payload, setPayload] = useState<NewsTodayRowsState>(() => defaultNewsTodayRowsState(sort));
  useEffect(() => {
    if (!enabled) {
      setPayload(defaultNewsTodayRowsState(sort));
      return undefined;
    }
    let cancelled = false;
    async function load() {
      setPayload((current) => ({ ...current, loading: true }));
      try {
        const response = await api<NewsTodayRowsPayload>(`/api/services/news/today?limit=5000&sort=${sort}`);
        if (cancelled) return;
        const rows = (response.rows || []).filter(isRecord).map(newsTodayRowFromPayload);
        setPayload({
          error: response.error || "",
          loading: false,
          rows,
          sort: (response.sort === "asc" ? "asc" : "desc"),
          summary: newsTodaySummaryFromPayload(response.summary, rows),
          windowEndUtc: response.window_end_utc || "",
          windowStartUtc: response.window_start_utc || "",
        });
      } catch (exc) {
        if (cancelled) return;
        setPayload((current) => ({ ...current, error: exc instanceof Error ? exc.message : String(exc), loading: false }));
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled, sort]);
  return payload;
}

function defaultNewsTodayRowsState(sort: NewsTodaySort): NewsTodayRowsState {
  return {
    error: "",
    loading: false,
    rows: [],
    sort,
    summary: {
      externalText: 0,
      latest: "",
      loadedRows: 0,
      multiTickerRows: 0,
      noTickerRows: 0,
      oneTickerRows: 0,
      pdfRows: 0,
      totalRows: 0,
      withTicker: 0,
    },
    windowEndUtc: "",
    windowStartUtc: "",
  };
}

function newsTodayRowFromPayload(row: Record<string, unknown>): NewsTodayRow {
  return {
    articleUrl: stringMetric(row, ["article_url"]),
    author: stringMetric(row, ["author"]),
    bodyChars: numericMetric(row, ["body_chars"]),
    canonicalNewsId: stringMetric(row, ["canonical_news_id"]),
    channels: stringArrayMetric(row, ["channels"]),
    contentQualityFlags: stringArrayMetric(row, ["content_quality_flags"]),
    downloadedAtUtc: stringMetric(row, ["downloaded_at_utc"]),
    externalChars: numericMetric(row, ["external_chars"]),
    externalFetchStatus: stringMetric(row, ["external_fetch_status"]),
    fullTextChars: numericMetric(row, ["full_text_chars"]),
    hasBody: Boolean(Number(row.has_body || 0)),
    hasExternalText: Boolean(Number(row.has_external_text || 0)),
    hasPdf: Boolean(Number(row.has_pdf || 0)),
    isTitleOnly: Boolean(Number(row.is_title_only || 0)),
    normalizedTitle: stringMetric(row, ["normalized_title"]),
    pdfChars: numericMetric(row, ["pdf_chars"]),
    pdfExtractStatus: stringMetric(row, ["pdf_extract_status"]),
    providerArticleId: stringMetric(row, ["provider_article_id"]),
    providerTags: stringArrayMetric(row, ["provider_tags"]),
    publishedAtUtc: stringMetric(row, ["published_at_utc"]),
    textPreview: stringMetric(row, ["text_preview"]),
    tickerLinkCount: numericMetric(row, ["ticker_link_count"]),
    tickerLinkSample: stringArrayMetric(row, ["ticker_link_sample"]),
    tickers: stringArrayMetric(row, ["tickers"]),
    title: stringMetric(row, ["title"]),
    urlDomain: stringMetric(row, ["url_domain"]),
  };
}

function newsTodayFilteredRows(rows: NewsTodayRow[], query: string) {
  const terms = query.toLowerCase().split(/\s+/).map((term) => term.trim()).filter(Boolean);
  if (!terms.length) return rows;
  return rows.filter((row) => {
    const haystack = newsTodaySearchText(row);
    return terms.every((term) => haystack.includes(term));
  });
}

function newsTodaySearchText(row: NewsTodayRow) {
  return [
    row.articleUrl,
    row.author,
    row.canonicalNewsId,
    row.downloadedAtUtc,
    row.externalFetchStatus,
    row.normalizedTitle,
    row.pdfExtractStatus,
    row.providerArticleId,
    row.publishedAtUtc,
    row.textPreview,
    row.title,
    row.urlDomain,
    formatLogTime(row.publishedAtUtc),
    newsTodayTickerLabel(row),
    newsTodayTextLabel(row),
    newsTodayFlagLabel(row),
    row.channels.join(" "),
    row.contentQualityFlags.join(" "),
    row.providerTags.join(" "),
    row.tickerLinkSample.join(" "),
    row.tickers.join(" "),
  ].join(" ").toLowerCase();
}

function newsTodaySummaryFromPayload(summaryPayload: unknown, rows: NewsTodayRow[]): NewsTodaySummary {
  const fallback = rows.reduce(
    (summary, row) => {
      const tickerCount = row.tickerLinkCount || row.tickers.length;
      return {
        externalText: summary.externalText + (row.hasExternalText ? 1 : 0),
        latest: !summary.latest || Date.parse(row.publishedAtUtc) > Date.parse(summary.latest) ? row.publishedAtUtc : summary.latest,
        loadedRows: rows.length,
        multiTickerRows: summary.multiTickerRows + (tickerCount > 1 ? 1 : 0),
        noTickerRows: summary.noTickerRows + (tickerCount <= 0 ? 1 : 0),
        oneTickerRows: summary.oneTickerRows + (tickerCount === 1 ? 1 : 0),
        pdfRows: summary.pdfRows + (row.hasPdf ? 1 : 0),
        totalRows: rows.length,
        withTicker: summary.withTicker + (tickerCount > 0 ? 1 : 0),
      };
    },
    {
      externalText: 0,
      latest: "",
      loadedRows: rows.length,
      multiTickerRows: 0,
      noTickerRows: 0,
      oneTickerRows: 0,
      pdfRows: 0,
      totalRows: rows.length,
      withTicker: 0,
    },
  );
  if (!isRecord(summaryPayload)) return fallback;
  return {
    externalText: numericMetric(summaryPayload, ["external_text_rows"]) || fallback.externalText,
    latest: stringMetric(summaryPayload, ["latest_published_at_utc"]) || fallback.latest,
    loadedRows: numericMetric(summaryPayload, ["loaded_rows"]) || rows.length,
    multiTickerRows: numericMetric(summaryPayload, ["multi_ticker_rows"]) || fallback.multiTickerRows,
    noTickerRows: numericMetric(summaryPayload, ["no_ticker_rows"]) || fallback.noTickerRows,
    oneTickerRows: numericMetric(summaryPayload, ["one_ticker_rows"]) || fallback.oneTickerRows,
    pdfRows: numericMetric(summaryPayload, ["pdf_rows"]) || fallback.pdfRows,
    totalRows: numericMetric(summaryPayload, ["total_rows"]) || fallback.totalRows,
    withTicker: numericMetric(summaryPayload, ["with_ticker_rows"]) || fallback.withTicker,
  };
}

function newsTodayTickerLabel(row: NewsTodayRow) {
  const tickers = row.tickers.length ? row.tickers : row.tickerLinkSample;
  if (!tickers.length) return "-";
  const label = tickers.slice(0, 4).join(", ");
  const extra = Math.max(0, tickers.length - 4);
  return extra ? `${label} +${extra}` : label;
}

function newsTodayTickerChips(row: NewsTodayRow) {
  const tickers = row.tickers.length ? row.tickers : row.tickerLinkSample;
  if (!tickers.length) return ["-"];
  const labels = tickers.slice(0, 3);
  const extra = Math.max(0, tickers.length - labels.length);
  return extra ? [...labels, `+${extra}`] : labels;
}

function newsTodayTextLabel(row: NewsTodayRow) {
  const parts = [
    row.bodyChars ? `body ${formatCompactNumber(row.bodyChars)}` : "",
    row.externalChars ? `ext ${formatCompactNumber(row.externalChars)}` : "",
    row.pdfChars ? `pdf ${formatCompactNumber(row.pdfChars)}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" / ") : row.isTitleOnly ? "title only" : "-";
}

function newsTodayFlagLabel(row: NewsTodayRow) {
  const flags = row.contentQualityFlags;
  if (!flags.length) return "-";
  const label = flags.slice(0, 2).join(", ");
  const extra = Math.max(0, flags.length - 2);
  return extra ? `${label} +${extra}` : label;
}

function newsTodayFlagChips(row: NewsTodayRow) {
  const flags = row.contentQualityFlags;
  if (!flags.length) return ["-"];
  const labels = flags.slice(0, 2);
  const extra = Math.max(0, flags.length - labels.length);
  return extra ? [...labels, `+${extra}`] : labels;
}

function newsTodayRowTone(row: NewsTodayRow) {
  const tickerCount = row.tickerLinkCount || row.tickers.length;
  const baseTone = row.hasPdf
    ? "has-pdf"
    : row.hasExternalText
      ? "has-external-text"
      : tickerCount > 1
        ? "multi-ticker"
        : tickerCount === 1
          ? "one-ticker"
          : row.isTitleOnly
            ? "title-only"
            : "broad-news";
  return newsTodayIsRecent(row.publishedAtUtc) ? `${baseTone} recent-news` : baseTone;
}

function newsTodayIsRecent(publishedAtUtc: string) {
  const publishedAtMs = Date.parse(publishedAtUtc);
  if (!Number.isFinite(publishedAtMs)) return false;
  const ageMs = Date.now() - publishedAtMs;
  return ageMs >= 0 && ageMs <= 60 * 60 * 1000;
}

function orderedServiceWorkGroups(groups: ServiceWorkGroup[], serviceId: ServiceId) {
  if (serviceId !== "news") return groups;
  const order = new Map([
    ["live", 0],
    ["processing", 1],
    ["publish", 2],
    ["coverage", 3],
    ["other", 4],
  ]);
  return [...groups].sort((left, right) => (order.get(left.id) ?? 50) - (order.get(right.id) ?? 50));
}

function visibleServiceWorkGroups(groups: ServiceWorkGroup[], serviceId: ServiceId) {
  return orderedServiceWorkGroups(groups, serviceId).filter((group) => group.id !== "other" || group.rows.length);
}

type WorkPlanSummaryMetric = {
  label: string;
  title?: string;
  tone?: string;
  value: string;
};

function serviceWorkPlanSummaryItems(groups: ServiceWorkGroup[]): WorkPlanSummaryMetric[] {
  const liveCounts = serviceWorkPlanSummary(groups);
  return [
    { label: "Areas", value: String(liveCounts.areas) },
    { label: "Active Tasks", tone: liveCounts.activeTasks ? "active" : undefined, value: formatCompactNumber(liveCounts.activeTasks) },
    { label: "Completed Tasks", tone: liveCounts.completedTasks ? "ok" : undefined, value: formatCompactNumber(liveCounts.completedTasks) },
    { label: "Warnings / Errors", tone: liveCounts.warningTasks ? "warn" : "ok", value: formatCompactNumber(liveCounts.warningTasks) },
  ];
}

function newsWorkPlanSummaryItems(service: ServiceStatusPayload): WorkPlanSummaryMetric[] {
  const metrics = serviceMetricsRecord(service);
  const polledRows = numericMetric(metrics, ["provider_rows", "processed_rows", "raw_saved"]);
  const processedRows = numericMetric(metrics, ["processed_rows", "provider_rows", "raw_saved"]);
  const duplicateRows = numericMetric(metrics, ["duplicate_news_rows"]);
  const uniqueNews = numericMetric(metrics, ["unique_news_rows"]) || Math.max(0, processedRows - duplicateRows);
  const enrichedUrls = numericMetric(metrics, ["background_enriched_urls"]);
  const requiredDownloads = numericMetric(metrics, ["background_fetch_tasks"]);
  const insertedRows = numericMetric(metrics, ["written_rows"]);
  const gapFilled = numericMetric(metrics, ["gap_fill_flushed_chunks"]);
  const gapTotal = numericMetric(metrics, ["gap_fill_total_chunks"]);
  const coverageRows = newsCoverageHistoryRows(service).filter((row) => row.coverageId || row.event.includes("coverage") || row.event.includes("gap_fill"));
  const coverageJobs = coverageRows.length;
  return [
    {
      label: "Unique / Polled",
      title: "Distinct Benzinga news items received by the live path divided by all rows returned by polling lookbacks.",
      tone: uniqueNews > 0 ? "active" : undefined,
      value: `${formatCompactNumber(uniqueNews)} / ${formatCompactNumber(polledRows)}`,
    },
    {
      label: "Enriched / Required",
      title: "External URL/PDF downloads that produced text compared with total required fetch tasks.",
      tone: requiredDownloads > 0 && enrichedUrls >= requiredDownloads ? "ok" : requiredDownloads > 0 ? "warn" : undefined,
      value: `${formatCompactNumber(enrichedUrls)} / ${formatCompactNumber(requiredDownloads)}`,
    },
    {
      label: "Inserted",
      title: "Total normalized news rows inserted into ClickHouse by this service run.",
      tone: insertedRows > 0 ? "ok" : undefined,
      value: formatCompactNumber(insertedRows),
    },
    {
      label: "Coverage Filled",
      title: "Coverage or gap-fill work completed in this service run. Shows chunks when a chunked fill ran; otherwise coverage jobs.",
      tone: gapTotal > 0 && gapFilled >= gapTotal ? "ok" : gapTotal > 0 ? "active" : coverageJobs > 0 ? "ok" : undefined,
      value: gapTotal > 0 ? `${formatCompactNumber(gapFilled)} / ${formatCompactNumber(gapTotal)}` : formatCompactNumber(coverageJobs),
    },
  ];
}

function serviceWorkPlanSummary(groups: ServiceWorkGroup[]) {
  return groups.reduce(
    (summary, group) => {
      summary.areas += 1;
      summary.activeTasks += group.activeCount;
      summary.completedTasks += group.completedCount;
      summary.warningTasks += group.warningCount;
      return summary;
    },
    { activeTasks: 0, areas: 0, completedTasks: 0, warningTasks: 0 },
  );
}

function serviceActivitySpec(service: ServiceStatusPayload): ServiceActivitySpec {
  const metrics = serviceMetricsRecord(service);
  const status = stringMetric(metrics, ["activity_status", "run_status", "status"]) || service.status || "unknown";
  if (service.registry.id === "qmd") {
    return {
      description: "Recent scanner primitives, market-state signals, live event throughput, and persistence activity.",
      status,
      summary: [
        metricSummary(metrics, "Events", ["total_events", "ingest_events", "events"]),
        metricSummary(metrics, "Trades/sec", ["trades_per_sec", "trades/sec", "trade_rate"]),
        metricSummary(metrics, "Quotes/sec", ["quotes_per_sec", "quotes/sec", "quote_rate"]),
        metricSummary(metrics, "Bars", ["bar_events", "bars_written", "bars"]),
        metricSummary(metrics, "Gaps", ["gap_count", "gaps", "coverage_gaps"], "warn"),
      ],
      title: "Scanner And Market Event Activity",
    };
  }
  if (service.registry.id === "sec") {
    return {
      description: "Recent SEC feed filings, duplicate skips, filing text/XBRL extraction, and write status.",
      status,
      summary: [
        metricSummary(metrics, "Polls", ["poll_runs"]),
        metricSummary(metrics, "Feed Items", ["feed_items", "provider_rows"]),
        metricSummary(metrics, "Written", ["written_filings", "written_rows"], "good"),
        metricSummary(metrics, "Skipped", ["skipped_existing", "skips"], "warn"),
        metricSummary(metrics, "XBRL Facts", ["xbrl_facts", "facts_written"]),
      ],
      title: "Latest SEC Filing Activity",
    };
  }
  if (service.registry.id === "text-embed") {
    return {
      description: "Recent source discovery, tokenization, embedding inference, write batches, and failed work.",
      status,
      summary: [
        metricSummary(metrics, "Pending", ["pending_rows", "pending_items", "queue_depth"], "warn"),
        metricSummary(metrics, "Tokens", ["token_rows_written", "tokens_written", "tokens"]),
        metricSummary(metrics, "Embeddings", ["embedding_rows_written", "embeddings_written", "vectors_written"], "good"),
        metricSummary(metrics, "Batches", ["completed_batches", "batches", "batch_count"]),
        metricSummary(metrics, "Failed", ["failed_rows", "failed_batches", "failures"], "bad"),
      ],
      title: "Embedding Work Queue",
    };
  }
  if (service.registry.id === "reference") {
    return {
      description: "Recent provider source sync, issue resolution, publication maintenance, and tradability guardrails.",
      status,
      summary: [
        metricSummary(metrics, "Sources", ["source_candidates", "sources_synced", "source_rows"]),
        metricSummary(metrics, "Issues", ["issue_writes", "open_issues", "issues"], "warn"),
        metricSummary(metrics, "Alerts", ["alert_writes", "alerts"]),
        metricSummary(metrics, "Blocks", ["tradability_blocks", "blocked_rows"], "bad"),
        metricSummary(metrics, "Audit", ["audit_failures", "audit_warning_count"], "warn"),
      ],
      title: "Reference Sync Activity",
    };
  }
  return {
    description: "Client Portal health, authentication, account checks, keepalive, contract lookup, and routing readiness.",
    status,
    summary: [
      metricSummary(metrics, "Gateway", ["gateway_status", "client_portal_status", "run_status"]),
      metricSummary(metrics, "Auth", ["authenticated", "auth_status"]),
      metricSummary(metrics, "Keepalive", ["keepalive_count", "tickle_count", "tickles"]),
      metricSummary(metrics, "Accounts", ["account_count", "accounts"]),
      metricSummary(metrics, "Failures", ["failure_count", "failures", "errors"], "bad"),
    ],
    title: "Broker Session Activity",
  };
}

function serviceActivityRows(service: ServiceStatusPayload): ServiceActivityRow[] {
  const sourceRows = serviceActivitySourceRows(service);
  const logRows = runtimeLogRows(service.logs).slice(0, 12).map((row) => ({
    detail: row.detail,
    event: row.event,
    level: row.status,
    source: row.source,
    status: row.status === "active" ? "failed" : row.status === "retrying" ? "warning" : row.status,
    title: row.title,
    ts_utc: row.time,
  }));
  const rows = [...sourceRows, ...logRows]
    .map((row, index) => serviceActivityRow(service, row, index))
    .sort((left, right) => (right.timeMs ?? 0) - (left.timeMs ?? 0))
    .slice(0, 36);
  return rows;
}

function serviceActivitySourceRows(service: ServiceStatusPayload): Record<string, unknown>[] {
  const snapshot = service.snapshot ?? {};
  const rows: Record<string, unknown>[] = [];
  rows.push(...rowsFromPayload(service.recent));
  rows.push(...rowsFromPayload(snapshot.recent_items));
  rows.push(...rowsFromPayload(snapshot.recent));
  rows.push(...rowsFromPayload(snapshot.feed_items));
  rows.push(...rowsFromPayload(snapshot.scanner));
  rows.push(...rowsFromPayload(snapshot.source_reports));
  rows.push(...rowsFromPayload(snapshot.sources_sinks));
  rows.push(...rowsFromPayload(snapshot.task_table_progress));
  rows.push(...rowsFromPayload(snapshot.queues));
  return dedupeActivityRows(rows).slice(0, 40);
}

function rowsFromPayload(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter(isRecord);
  if (!isRecord(value)) return [];
  const rowKeys = ["rows", "items", "events", "recent", "recent_items", "feed_items", "primitives", "data"];
  for (const key of rowKeys) {
    const rows = value[key];
    if (Array.isArray(rows)) return rows.filter(isRecord);
  }
  return Object.keys(value).length ? [value] : [];
}

function dedupeActivityRows(rows: Record<string, unknown>[]) {
  const seen = new Set<string>();
  const output: Record<string, unknown>[] = [];
  for (const row of rows) {
    const key = [
      firstString(row, ["accession_number", "canonical_news_id", "ticker", "symbol", "event", "title", "source"]),
      firstString(row, ["updated_at_utc", "ts_utc", "time_utc", "time", "poll_at_utc"]),
      firstString(row, ["status", "state", "stage", "phase"]),
    ].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    output.push(row);
  }
  return output;
}

function serviceActivityRow(service: ServiceStatusPayload, row: Record<string, unknown>, index: number): ServiceActivityRow {
  const timestamp = firstTimestamp(row);
  const status = serviceActivityStatus(service, row);
  return {
    detail: serviceActivityDetail(service, row),
    kind: serviceActivityKind(service, row),
    raw: row,
    rows: serviceActivityRowsValue(service, row),
    status,
    subject: serviceActivitySubject(service, row, index),
    time: timestamp.label,
    timeMs: timestamp.value,
  };
}

function serviceActivitySubject(service: ServiceStatusPayload, row: Record<string, unknown>, index: number) {
  if (service.registry.id === "qmd") {
    const ticker = firstString(row, ["ticker", "symbol", "primary_symbol"]);
    const primitive = firstString(row, ["primitive_key", "condition", "state", "event_type", "type"]);
    return [ticker, primitive].filter(Boolean).join(" / ") || `Market activity ${index + 1}`;
  }
  if (service.registry.id === "sec") {
    const form = firstString(row, ["form_type", "form", "type"]);
    const accession = firstString(row, ["accession_number", "accession"]);
    const title = firstString(row, ["title", "company_name", "issuer_name"]);
    return [form, accession || title].filter(Boolean).join(" / ") || `SEC filing ${index + 1}`;
  }
  if (service.registry.id === "text-embed") {
    const source = firstString(row, ["source", "source_table", "source_kind"]);
    const stage = firstString(row, ["stage", "mode", "task", "event"]);
    return [source, stage].filter(Boolean).join(" / ") || `Embedding work ${index + 1}`;
  }
  if (service.registry.id === "reference") {
    const source = firstString(row, ["source", "provider", "endpoint", "event"]);
    const item = firstString(row, ["ticker", "symbol", "table", "title", "task", "issue_type"]);
    return [source, item].filter(Boolean).join(" / ") || `Reference activity ${index + 1}`;
  }
  const event = firstString(row, ["event", "title", "task", "name"]);
  const account = firstString(row, ["account", "account_id", "acctId", "endpoint"]);
  return [event, account].filter(Boolean).join(" / ") || `IBKR activity ${index + 1}`;
}

function serviceActivityKind(service: ServiceStatusPayload, row: Record<string, unknown>) {
  const explicit = firstString(row, ["kind", "type", "category", "source", "event"]);
  if (explicit) return explicit;
  if (service.registry.id === "qmd") return "scanner primitive";
  if (service.registry.id === "sec") return "filing feed";
  if (service.registry.id === "text-embed") return "embedding work";
  if (service.registry.id === "reference") return "reference sync";
  return "broker event";
}

function serviceActivityStatus(service: ServiceStatusPayload, row: Record<string, unknown>) {
  const explicit = firstString(row, ["status", "state", "phase", "result", "level"]);
  if (explicit) return explicit;
  if (firstString(row, ["error", "failure", "exception"])) return "failed";
  if (service.registry.id === "qmd" && firstString(row, ["reject_reason"])) return "rejected";
  if (service.registry.id === "qmd") return "active";
  return "observed";
}

function serviceActivityRowsValue(service: ServiceStatusPayload, row: Record<string, unknown>) {
  const direct = firstString(row, ["rows", "row_count", "processed_rows", "written_rows", "inserted_rows", "feed_items", "documents", "texts", "xbrl_facts", "embedding_rows_written", "tokens_written", "done", "completed", "count"]);
  if (direct) return direct;
  if (service.registry.id === "qmd") {
    const score = firstString(row, ["score"]);
    if (score) return `score ${score}`;
    const volume = firstString(row, ["volume", "dollar_volume"]);
    if (volume) return volume;
  }
  return "-";
}

function serviceActivityDetail(service: ServiceStatusPayload, row: Record<string, unknown>) {
  const detail = firstString(row, ["detail", "details", "message", "description", "notes", "trigger_reason", "reject_reason", "title"]);
  const extras: string[] = [];
  if (service.registry.id === "qmd") {
    extras.push(compactPair(row, "side_bias", "Side"));
    extras.push(compactPair(row, "close", "Close"));
    extras.push(compactPair(row, "vwap", "VWAP"));
    extras.push(compactPair(row, "spread_bps", "Spread bps"));
    extras.push(compactPair(row, "liquidity_score", "Liquidity"));
  } else if (service.registry.id === "sec") {
    extras.push(compactPair(row, "documents", "Docs"));
    extras.push(compactPair(row, "texts", "Texts"));
    extras.push(compactPair(row, "xbrl_facts", "XBRL"));
    extras.push(compactPair(row, "skips", "Skips"));
  } else if (service.registry.id === "text-embed") {
    extras.push(compactPair(row, "mode", "Mode"));
    extras.push(compactPair(row, "stage", "Stage"));
    extras.push(compactPair(row, "seconds", "Seconds"));
  } else if (service.registry.id === "reference") {
    extras.push(compactPair(row, "provider", "Provider"));
    extras.push(compactPair(row, "issue_type", "Issue"));
    extras.push(compactPair(row, "action", "Action"));
  } else {
    extras.push(compactPair(row, "endpoint", "Endpoint"));
    extras.push(compactPair(row, "authenticated", "Auth"));
    extras.push(compactPair(row, "connected", "Connected"));
  }
  const readable = [detail, ...extras.filter(Boolean)].filter(Boolean).join("; ");
  return humanizeWorkDetail(readable || compactWorkDetail(row));
}

function compactPair(row: Record<string, unknown>, key: string, label: string) {
  const value = row[key];
  if (value === undefined || value === null || value === "") return "";
  return `${label}=${formatValue(key, value)}`;
}

function metricSummary(record: Record<string, unknown>, label: string, keys: string[], tone?: ServiceActivitySummaryItem["tone"]): ServiceActivitySummaryItem {
  const { value, numeric } = metricDisplayValue(record, keys);
  const resolvedTone = tone && value !== "-" && (numeric === undefined || numeric > 0) ? tone : undefined;
  return { label, tone: resolvedTone, value };
}

function metricDisplayValue(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (value === undefined || value === null || value === "") continue;
    const numeric = typeof value === "number" ? value : Number(value);
    return { numeric: Number.isFinite(numeric) ? numeric : undefined, value: formatValue(key, value) };
  }
  return { numeric: undefined, value: "-" };
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

function stringArrayMetric(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean);
    if (value !== undefined && value !== null && String(value).trim()) return [String(value).trim()];
  }
  return [];
}

function arrayValueLabel(value: unknown) {
  if (!Array.isArray(value)) return "";
  return value.map((item) => String(item || "").trim()).filter(Boolean).join(", ");
}

function uniqueStringSample(values: string[], limit: number) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean))).slice(0, limit);
}

function newsHistogramBarHeight(totalRows: number, maxRows: number) {
  if (totalRows <= 0 || maxRows <= 0) return 0;
  return Math.max(4, (totalRows / maxRows) * 100);
}

function newsHistogramHover(row: NewsDailyHistogramDatum) {
  const bucketDate = new Date(Date.parse(row.bucketUtc));
  return {
    broad: row.broadOrNoneRows,
    et: formatZoneDateTime(bucketDate, EXCHANGE_TIME_ZONE),
    single: row.singleTickerRows,
    utc: formatUtcDateTime(row.bucketUtc),
    van: formatZoneDateTime(bucketDate, VANCOUVER_TIME_ZONE),
  };
}

function formatNewsBinDuration(binSeconds: number) {
  if (binSeconds > 0 && binSeconds % 60 === 0) {
    const minutes = binSeconds / 60;
    return `${formatCompactNumber(minutes)} minute${minutes === 1 ? "" : "s"}`;
  }
  return `${formatCompactNumber(binSeconds)} second${binSeconds === 1 ? "" : "s"}`;
}

function formatSeconds(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "-";
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}

function defaultNewsHistogramWindow(binSeconds: number): NewsDailyHistogramState {
  const { day, month, year } = exchangeDateParts(new Date());
  const start = zonedDateTimeToUtc(year, month, day, 0, 0, EXCHANGE_TIME_ZONE);
  const nextDay = nextCalendarDate(year, month, day);
  const end = zonedDateTimeToUtc(nextDay.year, nextDay.month, nextDay.day, 0, 0, EXCHANGE_TIME_ZONE);
  const totalBins = Math.max(0, Math.ceil((end.getTime() - start.getTime()) / (binSeconds * 1000)) + 1);
  const elapsedBins = Math.max(0, Math.min(totalBins, Math.ceil((Date.now() - start.getTime()) / (binSeconds * 1000)) + 1));
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

function elapsedNewsHistogramRows(rows: NewsDailyHistogramDatum[], windowStartUtc: string, windowEndUtc: string, binSeconds: number) {
  const start = Date.parse(windowStartUtc);
  const end = Date.parse(windowEndUtc);
  const cutoff = Math.min(Number.isFinite(end) ? end : Date.now(), Date.now());
  const halfBinMs = Math.max(0, binSeconds * 500);
  return rows.filter((row) => {
    const bucket = Date.parse(row.bucketUtc);
    if (!Number.isFinite(bucket)) return false;
    if (Number.isFinite(start) && bucket < start) return false;
    if (Number.isFinite(end) && bucket >= end) return false;
    if (bucket - halfBinMs >= cutoff) return false;
    return row.totalRows > 0 || row.singleTickerRows > 0 || row.broadOrNoneRows > 0;
  });
}

function newsHistogramFullWindowRows(rows: NewsDailyHistogramDatum[], windowStartUtc: string, windowEndUtc: string, binSeconds: number) {
  const start = Date.parse(windowStartUtc);
  const end = Date.parse(windowEndUtc);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || binSeconds <= 0) return rows;
  const byTime = new Map<number, NewsDailyHistogramDatum>();
  for (const row of rows) {
    const timestamp = Date.parse(row.bucketUtc);
    if (Number.isFinite(timestamp)) byTime.set(timestamp, row);
  }
  const totalBins = Math.max(1, Math.ceil((end - start) / (binSeconds * 1000)) + 1);
  return Array.from({ length: totalBins }, (_, index) => {
    const timestamp = start + index * binSeconds * 1000;
    return byTime.get(timestamp) ?? { broadOrNoneRows: 0, bucketUtc: new Date(timestamp).toISOString(), singleTickerRows: 0, totalRows: 0 };
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

function WorkPlanSummaryItem({ label, title = "", tone = "", value }: { label: string; title?: string; tone?: string; value: string }) {
  return (
    <div className={tone ? `service-work-plan-summary-item ${tone}` : "service-work-plan-summary-item"} title={title || label}>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
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
            <col className="service-db-state-col-recent" />
            <col className="service-db-state-col-recent" />
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

function ServiceDependenciesPanel({ service }: { service: ServiceStatusPayload }) {
  const [selectedRow, setSelectedRow] = useState<ServiceDependencyDisplayRow | null>(null);
  const snapshot = service.snapshot ?? {};
  const dependencyRows = arrayRows(snapshot.dependencies);
  const queueRows = arrayRows(snapshot.queues);
  const sourceRows = arrayRows(snapshot.sources_sinks);
  const configuredTableRows = arrayRows(snapshot.configured_tables);
  const setupRows = serviceSetupRows(service).map((row) => ({
    detail: row.detail,
    last: row.lastAt,
    name: row.name,
    progress: row.progress,
    rows: row.rows,
    status: displayName(row.status),
    type: displayName(row.kind),
  }));
  const sections: ServiceDependencySectionPayload[] = [
    {
      description: "Provider credentials, storage paths, ClickHouse access, market calendar, and other startup checks.",
      empty: "No dependency checks reported.",
      id: "dependency",
      rows: dependencyRows.map((row) => dependencyDisplayRow(row, "dependency")),
      title: "Dependency Checks",
    },
    {
      description: "Configured tables and contracts the service expects before live or background work starts.",
      empty: "No setup or contract rows reported.",
      id: "setup",
      rows: setupRows.map((row) => dependencyDisplayRow(row, "setup")),
      title: "Setup Contracts",
    },
    {
      description: "Internal queue depth, active workers, pending work, and drain state.",
      empty: "No queues reported.",
      id: "queue",
      rows: queueRows.map((row) => dependencyDisplayRow(row, "queue")),
      title: "Queues",
    },
    {
      description: "External providers, input sources, output sinks, and their last reported state.",
      empty: "No sources or sinks reported.",
      id: "source",
      rows: sourceRows.map((row) => dependencyDisplayRow(row, "source")),
      title: "Sources And Sinks",
    },
    {
      description: "Database tables this service reads, writes, validates, or publishes.",
      empty: "No configured tables reported.",
      id: "table",
      rows: configuredTableRows.map((row) => dependencyDisplayRow(row, "table")),
      title: "Configured Tables",
    },
  ];
  const issueCount = sections.reduce((total, section) => total + section.rows.filter((row) => ["error", "warn"].includes(workStatusClass(row.status))).length, 0);
  const healthyCount = sections.reduce((total, section) => total + section.rows.filter((row) => ["ok", "active"].includes(workStatusClass(row.status))).length, 0);
  const rowCount = sections.reduce((total, section) => total + section.rows.length, 0);
  return (
    <div className="service-dependencies-panel">
      <div className="service-dependencies-hero">
        <div>
          <span className="service-dependencies-kicker">Dependency Readiness</span>
          <h3>{service.registry.label}</h3>
          <p>Operational checks that determine whether this gateway can safely reach providers, storage, and database tables.</p>
        </div>
        <ServiceStatusBadge online={service.online} status={issueCount ? "degraded" : "running"} />
      </div>
      <div className="service-dependencies-summary">
        <DependencySummaryItem label="Sections" value={String(sections.length)} />
        <DependencySummaryItem label="Rows" value={formatCompactNumber(rowCount)} />
        <DependencySummaryItem label="Healthy" tone="ok" value={formatCompactNumber(healthyCount)} />
        <DependencySummaryItem label="Issues" tone={issueCount ? "warn" : "ok"} value={formatCompactNumber(issueCount)} />
      </div>
      <div className="service-dependencies-sections">
        {sections.map((section) => (
          <ServiceDependencySection key={section.id} onSelect={setSelectedRow} section={section} />
        ))}
      </div>
      {selectedRow ? (
        <Modal className="service-dependency-detail-modal-panel" onClose={() => setSelectedRow(null)} title="Dependency Row Detail">
          <ServiceDependencyDetail row={selectedRow} />
        </Modal>
      ) : null}
    </div>
  );
}

type ServiceDependencyDisplayRow = {
  detail: string;
  kind: string;
  last: string;
  metric: string;
  name: string;
  raw: Record<string, unknown>;
  status: string;
};

type ServiceDependencySectionPayload = {
  description: string;
  empty: string;
  id: string;
  rows: ServiceDependencyDisplayRow[];
  title: string;
};

function ServiceDependencySection({ onSelect, section }: { onSelect: (row: ServiceDependencyDisplayRow) => void; section: ServiceDependencySectionPayload }) {
  const issueCount = section.rows.filter((row) => ["error", "warn"].includes(workStatusClass(row.status))).length;
  const status = issueCount ? "warning" : section.rows.length ? "ok" : "not reported";
  return (
    <section className={`service-dependencies-section ${workStatusClass(status)}`}>
      <div className="service-dependencies-section-header">
        <div>
          <h3>{section.title}</h3>
          <p>{section.description}</p>
        </div>
        <div className="service-dependencies-section-badges">
          <span className={`service-work-status ${workStatusClass(status)}`}>{displayName(status)}</span>
          <span>{section.rows.length} row{section.rows.length === 1 ? "" : "s"}</span>
        </div>
      </div>
      <div className="service-dependency-row-list">
        {section.rows.length ? section.rows.map((row, index) => (
          <button className={`service-dependency-row ${workStatusClass(row.status)}`} key={`${section.id}-${row.name}-${index}`} onClick={() => onSelect(row)} type="button">
            <div>
              <strong title={row.name}>{row.name}</strong>
              <span>{displayName(row.kind)}</span>
            </div>
            <span className={`service-work-status ${workStatusClass(row.status)}`}>{displayName(row.status)}</span>
            <span title={row.metric}>{row.metric}</span>
            <span title={row.last}>{row.last}</span>
            <p title={row.detail}>{row.detail}</p>
          </button>
        )) : (
          <div className="service-dependency-empty">{section.empty}</div>
        )}
      </div>
    </section>
  );
}

function ServiceDependencyDetail({ row }: { row: ServiceDependencyDisplayRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="service-dependency-detail">
      <div className={`service-dependency-detail-heading ${statusClass}`}>
        <div>
          <span>{displayName(row.kind)}</span>
          <strong>{row.name}</strong>
        </div>
        <span className={`service-work-status ${statusClass}`}>{displayName(row.status)}</span>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Status</dt>
          <dd>{displayName(row.status)}</dd>
        </div>
        <div>
          <dt>Metric</dt>
          <dd>{row.metric}</dd>
        </div>
        <div>
          <dt>Last</dt>
          <dd>{row.last}</dd>
        </div>
        <div className="wide">
          <dt>Detail</dt>
          <dd>{row.detail}</dd>
        </div>
      </dl>
      <DebugObjectBlock title="Raw Dependency Payload" value={row.raw} />
    </div>
  );
}

function DependencySummaryItem({ label, tone = "", value }: { label: string; tone?: string; value: string }) {
  return (
    <div className={tone ? `service-dependencies-summary-item ${tone}` : "service-dependencies-summary-item"}>
      <span>{label}</span>
      <strong title={value}>{value || "-"}</strong>
    </div>
  );
}

function dependencyDisplayRow(row: Record<string, unknown>, fallbackKind: string): ServiceDependencyDisplayRow {
  const status = firstString(row, ["status", "state", "result", "level"]) || (dependencyModalRowHasIssue(row) ? "warning" : "ok");
  const timestamp = firstTimestamp(row);
  return {
    detail: humanizeWorkDetail(firstString(row, ["message", "detail", "details", "description", "notes", "last", "latest"]) || compactWorkDetail(row)),
    kind: firstString(row, ["kind", "type", "category", "role"]) || fallbackKind,
    last: timestamp.label,
    metric: dependencyMetric(row),
    name: firstString(row, ["name", "task", "work", "item", "source", "sink", "table", "database", "label", "area", "queue_worker"]) || fallbackKind,
    raw: row,
    status,
  };
}

function dependencyMetric(row: Record<string, unknown>) {
  const metric = firstString(row, ["wall_seconds", "seconds", "depth", "active", "pending", "progress", "rows", "row_count", "count"]);
  return metric || "-";
}

function dependencyModalRowHasIssue(row: Record<string, unknown>) {
  return ["status", "state", "result", "level"].some((key) => {
    const value = normalizedStatus(String(row[key] || ""));
    return /failed|error|warn|degraded|blocked|unreachable/.test(value);
  });
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
  const Icon = !service.online ? WifiOff : info.tone === "error" || info.tone === "warn" ? AlertTriangle : CheckCircle2;
  return <Icon className="service-card-icon" size={20} />;
}

function ServiceStatusBadge({ online, status }: { online: boolean; status: string }) {
  const info = statusInfo({ online, status } as ServiceStatusPayload);
  return <span className={`service-status-badge ${info.className} ${info.tone}`} title={info.description}>{info.label}</span>;
}

function sortServices(services: ServiceStatusPayload[]) {
  return [...services].sort((left, right) => SERVICE_IDS.indexOf(left.registry.id) - SERVICE_IDS.indexOf(right.registry.id));
}

function countStatuses(services: ServiceStatusPayload[]) {
  return services.reduce(
    (counts, service) => {
      const info = statusInfo(service);
      if (!service.online) counts.offline += 1;
      else counts.online += 1;
      if (info.tone === "active") counts.active += 1;
      if (info.tone === "warn" || info.tone === "error") counts.degraded += 1;
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

function serviceRunTiming(service: ServiceStatusPayload) {
  const metrics = serviceMetricsRecord(service);
  const startedAt = stringMetric(metrics, ["started_at_utc", "service_started_at_utc", "run_started_at_utc", "gateway_started_at_utc"])
    || stringMetric(service.current_operation ?? {}, ["started_at", "started_at_utc", "since"]);
  const elapsedSeconds = numericMetric(metrics, ["elapsed_seconds", "uptime_seconds", "process_uptime_seconds", "runtime_seconds"]);
  const elapsedMs = numericMetric(metrics, ["process_uptime_ms", "uptime_ms", "elapsed_ms"]);
  const parsedStart = Date.parse(startedAt);
  const parsedNow = Date.parse(service.checked_at_utc);
  const derivedSeconds = Number.isFinite(parsedStart)
    ? Math.max(0, ((Number.isFinite(parsedNow) ? parsedNow : Date.now()) - parsedStart) / 1000)
    : 0;
  const durationSeconds = elapsedSeconds || (elapsedMs ? elapsedMs / 1000 : 0) || derivedSeconds;
  return {
    duration: durationSeconds ? formatDuration(durationSeconds) : "-",
    started: startedAt ? formatLogTime(startedAt) : "-",
  };
}

function serviceWorkRows(service: ServiceStatusPayload): ServiceWorkRow[] {
  const snapshot = service.snapshot ?? {};
  const rows: ServiceWorkRow[] = [];
  if (isRecord(snapshot.coverage)) rows.push(serviceWorkRow({ ...snapshot.coverage, name: "coverage manifest" }, "coverage", "live"));
  rows.push(...arrayRows(snapshot.tasks).map((row) => serviceWorkRow(row, "task", "live")));
  rows.push(...arrayRows(snapshot.task_table_progress).map((row) => serviceWorkRow(row, "table", "live")));
  rows.push(...arrayRows(snapshot.queues).map((row) => serviceWorkRow(row, "queue", "live")));
  rows.push(...arrayRows(snapshot.sources_sinks).map((row) => serviceWorkRow(row, "source", "live")));
  if (service.registry.id === "news") rows.push(...newsSyntheticWorkRows(service));
  return dedupeWorkRows(rows)
    .filter((row) => !isSetupLikeWorkRow(row))
    .sort((a, b) => workStatusRank(a.status) - workStatusRank(b.status) || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
}

function newsSyntheticWorkRows(service: ServiceStatusPayload): ServiceWorkRow[] {
  const metrics = serviceMetricsRecord(service);
  const pendingArticles = numericMetric(metrics, ["background_pending_articles"]);
  const activeBatches = numericMetric(metrics, ["background_active_batches"]);
  const completedBatches = numericMetric(metrics, ["background_completed_batches"]);
  const failedBatches = numericMetric(metrics, ["background_failed_batches"]);
  const urlTasks = numericMetric(metrics, ["background_fetch_tasks"]);
  const enrichedUrls = numericMetric(metrics, ["background_enriched_urls"]);
  const pendingPublishRows = numericMetric(metrics, ["publish_pending_rows"]);
  const activePublishJobs = numericMetric(metrics, ["publish_active_jobs"]);
  const completedPublishJobs = numericMetric(metrics, ["publish_completed_jobs"]);
  const failedPublishJobs = numericMetric(metrics, ["publish_failed_jobs"]);
  const publishStatus = stringMetric(metrics, ["publish_status"]) || "idle";
  return [
    syntheticWorkRow({
      detail: `pending_articles=${formatCompactNumber(pendingArticles)} active_batches=${formatCompactNumber(activeBatches)} completed_batches=${formatCompactNumber(completedBatches)} failed_batches=${formatCompactNumber(failedBatches)}`,
      kind: "background",
      name: "Background enrichment queue",
      rows: pendingArticles,
      status: failedBatches > 0 ? "warning" : activeBatches > 0 || pendingArticles > 0 ? "running" : "complete",
    }),
    syntheticWorkRow({
      detail: `url_tasks=${formatCompactNumber(urlTasks)} enriched_urls=${formatCompactNumber(enrichedUrls)}`,
      kind: "enrichment",
      name: "URL and external text enrichment",
      rows: enrichedUrls,
      status: failedBatches > 0 ? "warning" : activeBatches > 0 || pendingArticles > 0 ? "running" : "complete",
    }),
    syntheticWorkRow({
      detail: `status=${publishStatus} pending_rows=${formatCompactNumber(pendingPublishRows)} active_jobs=${formatCompactNumber(activePublishJobs)} completed_jobs=${formatCompactNumber(completedPublishJobs)} failed_jobs=${formatCompactNumber(failedPublishJobs)}`,
      kind: "publisher",
      name: "Async database publisher",
      rows: pendingPublishRows,
      status: failedPublishJobs > 0 ? "warning" : activePublishJobs > 0 || pendingPublishRows > 0 ? "running" : publishStatus,
    }),
  ];
}

function syntheticWorkRow({ detail, kind, name, rows, status }: { detail: string; kind: string; name: string; rows: number; status: string }): ServiceWorkRow {
  return {
    detail,
    kind,
    lastAt: "-",
    name,
    progress: "-",
    reportKind: "live",
    rows: formatCompactNumber(rows),
    schedule: "-",
    status,
  };
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
    activeCount: countRowsByStatus(group.rows, "active"),
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
        match: [/poll|benzinga provider|provider rows|raw|duplicate|skip|live|latest/],
        title: "Live Benzinga Update",
      },
      {
        description: "Database publishing for normalized rows, ticker links, coverage rows, and runtime logs.",
        id: "publish",
        match: [/publish|publisher|insert|write|database|table|sink|clickhouse|persist/],
        title: "Database Publishing",
      },
      {
        description: "URL handling, external text/PDF enrichment, canonicalization, ticker links, and quality flags.",
        id: "processing",
        match: [/background|enrich|canonical|normaliz|url|pdf|extract|text|ticker|quality|process|article/],
        title: "Enrichment And Canonical Rows",
      },
      {
        description: "Coverage bootstrap, gap detection, gap fill, and historical catch-up for Benzinga news.",
        id: "coverage",
        match: [/coverage|manifest|gap|backfill|catch.?up|initial|bootstrap|historical/],
        title: "Coverage, Gap Fill, Backfill",
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
  if (statuses.includes("active")) return "running";
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

function workStatusClass(status: string): ServiceStatusTone {
  const normalized = normalizedStatus(status);
  if (/failed|error|blocked|critical|offline|not_started|unreachable/.test(normalized)) return "error";
  if (/warn|degraded|retry|queued|pending|waiting|attention/.test(normalized)) return "warn";
  if (/running|working|active|loading|polling|publishing|processing|ingesting|syncing|repairing|catching_up|preflight|starting/.test(normalized)) return "active";
  if (/complete|completed|ok|ready|success|healthy|observed/.test(normalized)) return "ok";
  if (/idle|noop|no_op|not_reported/.test(normalized)) return "idle";
  return "waiting";
}

function workStatusRank(status: string) {
  const className = workStatusClass(status);
  if (className === "error") return 0;
  if (className === "warn") return 1;
  if (className === "active") return 2;
  if (className === "waiting") return 3;
  if (className === "idle") return 4;
  return 4;
}

function arrayRows(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)).map(normalizeRow);
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

function debugObjectValue(value: unknown) {
  if (Array.isArray(value)) {
    if (!value.length) return "-";
    if (value.every((item) => typeof item !== "object" || item === null)) return value.map(String).join(", ");
    return JSON.stringify(value, null, 2);
  }
  if (isRecord(value)) return JSON.stringify(value, null, 2);
  if (value === undefined || value === null || value === "") return "-";
  return String(value);
}

function debugObjectValueWide(value: unknown) {
  if (Array.isArray(value) || isRecord(value)) return true;
  return String(value ?? "").length > 100;
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

function formatNewsTableDate(value: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value || "-";
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", year: "numeric" }).format(new Date(parsed));
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

function formatReadableDateTime(value: string, timeZone: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value || "-";
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    second: "2-digit",
    timeZone,
    timeZoneName: "short",
    weekday: "short",
    year: "numeric",
  }).format(new Date(parsed));
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
  tone: ServiceStatusTone;
};

function statusInfo(service: Pick<ServiceStatusPayload, "online" | "status">): StatusInfo {
  if (!service.online) {
    return { className: "not-started", description: "The service API endpoint is not reachable or timed out.", label: "NOT STARTED", tone: "error" };
  }
  const text = String(service.status || "").toLowerCase().replaceAll("_", "-");
  if (text.includes("not-start") || text.includes("offline") || text.includes("unreachable")) return { className: "not-started", description: "The service API endpoint is not reachable or timed out.", label: "NOT STARTED", tone: "error" };
  if (text.includes("start")) return { className: "starting", description: "The service is starting and has not completed initialization.", label: "STARTING", tone: "active" };
  if (text.includes("preflight")) return { className: "preflight", description: "The service is checking dependencies before operational work.", label: "PREFLIGHT", tone: "active" };
  if (text.includes("catch") || text.includes("gap") || text.includes("repair")) return { className: "catching-up", description: "The service is filling coverage gaps or repairing recent data.", label: "CATCHING UP", tone: "active" };
  if (text.includes("work") || text.includes("queue") || text.includes("processing")) return { className: "working", description: "The service is actively processing background work.", label: "WORKING", tone: "active" };
  if (text.includes("degraded") || text.includes("warn")) return { className: "degraded", description: "The service is reachable but has warnings or reduced capability.", label: "DEGRADED", tone: "warn" };
  if (text.includes("block")) return { className: "blocked", description: "The service is blocked by policy, dependency, or required manual action.", label: "BLOCKED", tone: "error" };
  if (text.includes("stop")) return { className: "stopping", description: "The service is shutting down.", label: "STOPPING", tone: "warn" };
  if (text.includes("fail") || text.includes("error") || text.includes("critical")) return { className: "failed", description: "The service reports an active critical failure.", label: "FAILED", tone: "error" };
  if (text.includes("idle") || text.includes("waiting")) return { className: "idle", description: "The service is healthy and waiting for the next scheduled task.", label: "IDLE", tone: "idle" };
  if (text.includes("run") || text.includes("ok") || text.includes("healthy") || text.includes("online")) return { className: "running", description: "The service is healthy and running.", label: "RUNNING", tone: "active" };
  return { className: "unknown", description: "The service is reachable but did not report a standard status.", label: service.status ? String(service.status).toUpperCase() : "UNKNOWN", tone: "waiting" };
}

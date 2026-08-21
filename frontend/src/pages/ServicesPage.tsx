import { Activity, CheckCircle2, Clock3, Layers3, Loader2, RadioTower, RefreshCcw, Search, Settings2, X } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { Button } from "../app/components/Button";
import { DataTable } from "../app/components/DataTable";
import { MetricRatio } from "../app/components/MetricRatio";
import { Modal } from "../app/components/Modal";
import { useWallClock } from "../app/components/useWallClock";
import { displayName, formatBytes, formatCell, formatCompactNumber, formatDuration } from "../app/format";
import { usePollingTask } from "../app/hooks/usePollingTask";
import type { ServicePageMode } from "../app/routes";
import type {
  ServiceRuntimeLogRow,
  ServiceStatusPayload,
  WorkloadBudgetPayload,
} from "../features/services/contracts";
import { ServiceConfigurationPanel } from "../features/services/ServiceConfigurationPanel";
import { ServiceDatabaseTableState } from "../features/services/ServiceDatabaseTableState";
import { ServicePageApiFailure, WorkloadBudgetPanel } from "../features/services/ServiceDashboardStates";
import { DebugObjectBlock } from "../features/services/DebugObjectBlock";
import { ServiceDependenciesPanel } from "../features/services/ServiceDependenciesPanel";
import { ServiceErrorLogPanel } from "../features/services/ServiceErrorLogPanel";
import { ServiceActivityPanel, serviceRecentSourceRows } from "../features/services/ServiceActivityPanel";
import {
  fleetDatabaseSummary,
} from "../features/services/fleetPresentation";
import { ServiceFleetCard } from "../features/services/ServiceFleetCard";
import { ServiceMetadataTable } from "../features/services/ServiceMetadataTable";
import type { NewsTodayRowsState, NewsTodaySort } from "../features/services/newsContracts";
import { NewsDailyHistogram } from "../features/services/NewsDailyHistogram";
import { NewsTodayRowsPanel } from "../features/services/NewsTodayRowsPanel";
import { SecDailyHistogram } from "../features/services/SecDailyHistogram";
import { SecTodayRowsPanel } from "../features/services/SecTodayRowsPanel";
import { ServiceOperationalAuthorityPanel } from "../features/services/ServiceOperationalAuthorityPanel";
import type {
  SecDailyHistogramState,
  SecLiveFeedRow,
  SecTodayRowsState,
  SecTodaySort,
} from "../features/services/secContracts";
import { defaultSecHistogramWindow, useSecTodayRows } from "../features/services/useSecTodayRows";
import { secHistogramSummary, secLiveFeedRows } from "../features/services/secHistogramPresentation";
import {
  arrayRecords,
  arrayValueLabel,
  differenceLabel,
  hasRemaining,
  metricStatus,
  numericMetric,
  optionalNumber,
  optionalNumberOrNull,
  remainingLabel,
  serviceMetricsRecord,
  statusIsHealthy,
  stringArrayMetric,
  stringMetric,
  sumTableCounts,
  tableTimestamp,
  textEmbedCoverageTotals,
  uniqueStringSample,
} from "../features/services/metrics";
import {
  runtimeLogRows,
} from "../features/services/diagnostics";
import { ServicePanel as Panel } from "../features/services/ServicePanel";
import { ServiceStatusBadge } from "../features/services/ServiceStatusIndicators";
import { ServiceTableTimeCell } from "../features/services/ServiceTableTimeCell";
import { ServiceTimeCard } from "../features/services/ServiceTimeCard";
import type { ServiceWorkGroup, ServiceWorkRow, WorkPlanSummaryMetric } from "../features/services/serviceWorkContracts";
import {
  fleetWorkSummary,
  serviceSetupRows,
  serviceWorkGroups,
  serviceWorkPlanSummaryItems,
  visibleServiceWorkGroups,
} from "../features/services/serviceWorkPresentation";
import {
  countStatuses,
  currentMessage,
  fleetMarketStatus,
  marketTileClass,
  phaseText,
  relativeServiceAge,
  runtimeText,
  serviceFreshness,
  serviceRunTiming,
  sortServices,
  statusInfo,
} from "../features/services/statusPresentation";
import { useServicesStatus } from "../features/services/useServicesStatus";
import { useNewsDailyHistogram } from "../features/services/useNewsDailyHistogram";
import { useNewsTodayRows } from "../features/services/useNewsTodayRows";
import {
  compactJson,
  isRecord,
  normalizeRow,
  normalizedStatus,
  workStatusClass,
  workStatusRank,
} from "../features/services/workPresentation";
import {
  EXCHANGE_TIME_ZONE,
  formatLogTime,
  formatNewsTableDate,
  formatReadableDateTime,
  formatServiceTime as formatTime,
  formatTableZoneDate,
  formatTableZoneTime,
  formatZoneDate,
  formatZoneTime,
  parseServiceTimestamp,
  tableTimeTitle,
  tableTimestampMs,
} from "../features/services/time";
import "./ServicesOverview.css";

export type { ServiceId, ServicePageMode } from "../app/routes";

const LazyBarGptOperationalConfigurationPanel = lazy(() => import("../features/services/BarGptOperationalConfigurationPanel").then((module) => ({ default: module.BarGptOperationalConfigurationPanel })));

function BarGptOperationalConfigurationPanel() {
  return <Suspense fallback={<Panel title="BarGPT Operational Configuration"><div className="bar-gpt-config-state"><Loader2 size={18} /><span>Loading service-owned configuration…</span></div></Panel>}>
    <LazyBarGptOperationalConfigurationPanel />
  </Suspense>;
}

export function ServicesPage({ mode, onNavigate }: { mode: ServicePageMode; onNavigate: (mode: ServicePageMode) => void }) {
  const serviceId = mode === "dashboard" ? null : mode;
  const { detailLoading, error, loading, payload, selectedPayload, workloadBudgetError, workloadBudgets } = useServicesStatus(serviceId);
  const wallClockMs = useWallClock(1_000);
  const now = useMemo(() => new Date(wallClockMs), [wallClockMs]);

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
          <p>{selected ? selected.registry.description : "Live gateway health, objective-specific work, database state, and today's durable output."}</p>
        </div>
        <div className="services-header-actions">
          <span className="services-refresh-note">Updated {payload?.checked_at_utc ? formatTime(payload.checked_at_utc) : "-"}</span>
          <Button onClick={() => window.location.reload()} variant="secondary"><RefreshCcw size={15} /> Refresh</Button>
        </div>
        <ServicesTopSummary checkedAt={payload?.checked_at_utc ?? ""} now={now} services={services} />
      </section>
      {selected ? (
        <div className="service-detail-shell">
          <ServiceDetail pageError={error} service={selected} />
        </div>
      ) : error && !services.length ? (
        <ServicePageApiFailure message={error} />
      ) : (
        <ServicesDashboard budgets={workloadBudgets} budgetError={workloadBudgetError} now={now} services={services} onNavigate={onNavigate} />
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

function ServicesTopSummary({ checkedAt, now, services }: { checkedAt: string; now: Date; services: ServiceStatusPayload[] }) {
  const counts = countStatuses(services);
  const market = fleetMarketStatus(services);
  const work = fleetWorkSummary(services);
  const databases = fleetDatabaseSummary(services);
  const stale = services.filter((service) => serviceFreshness(service, now).tone === "stale").length;
  const summaries: Array<{
    detail: string;
    icon: typeof RadioTower;
    label: string;
    ratio?: { accent: 1 | 2 | 3 | 4; current: number; suffix: string; total: number };
    tone: string;
    value?: string;
  }> = [
    { label: "Fleet", ratio: { accent: 1, current: counts.online, suffix: "online", total: services.length || 0 }, detail: `${counts.degraded} need attention`, icon: RadioTower, tone: counts.degraded ? "neutral" : "ok" },
    { label: "Databases", value: databases.total ? undefined : "No contracts", ratio: databases.total ? { accent: 2, current: databases.healthy, suffix: "healthy", total: databases.total } : undefined, detail: databases.total ? (databases.missing ? `${databases.missing} missing or empty` : "Configured tables available") : "Waiting for database contracts", icon: Layers3, tone: databases.total && !databases.missing ? "ok" : "neutral" },
    { label: "Responsibilities", value: `${work.active} active`, detail: `${work.warning} warning · ${work.completed} recent cycles`, icon: Layers3, tone: work.warning ? "warn" : work.active ? "active" : "ok" },
    { label: "Market", value: displayName(market.status), detail: market.detail, icon: Activity, tone: marketTileClass(market.status, market.detail).replace("market-", "") },
    { label: "Freshness", value: stale ? `${stale} stale` : "Live", detail: checkedAt ? `Updated ${relativeServiceAge(checkedAt, now)}` : "Waiting for first fleet check", icon: RefreshCcw, tone: stale ? "warn" : checkedAt ? "ok" : "idle" },
    { label: "Clock", value: `${formatZoneTime(now, EXCHANGE_TIME_ZONE)} ET`, detail: `${formatZoneTime(now, "UTC")} UTC · ${formatZoneDate(now, EXCHANGE_TIME_ZONE)}`, icon: Clock3, tone: "neutral" },
  ];
  return (
    <div className="service-fleet-summary" aria-label="Service fleet summary">
      {summaries.map((summary) => {
        const Icon = summary.icon;
        return (
          <div className={`service-fleet-summary-item tone-${summary.tone}`} key={summary.label}>
            <Icon aria-hidden="true" size={15} />
            <span>{summary.label}</span>
            <strong>{summary.ratio ? <MetricRatio {...summary.ratio} /> : summary.value}</strong>
            <small title={summary.detail}>{summary.detail}</small>
          </div>
        );
      })}
    </div>
  );
}

function ServicesDashboard({ budgets, budgetError, now, onNavigate, services }: { budgets: WorkloadBudgetPayload | null; budgetError: string; now: Date; onNavigate: (mode: ServicePageMode) => void; services: ServiceStatusPayload[] }) {
  return (
    <div className="services-dashboard-stack">
      <WorkloadBudgetPanel error={budgetError} payload={budgets} />
      <section className="service-fleet-grid" aria-label="Gateway live responsibility status">
        {services.map((service) => (
          <ServiceFleetCard key={service.registry.id} now={now} onOpen={() => onNavigate(service.registry.id)} service={service} />
        ))}
      </section>
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
      <ServiceOperationalAuthorityPanel service={service} />
      {service.registry.id === "bar-gpt" ? <BarGptOperationalConfigurationPanel /> : null}
      {configOpen ? (
        <Modal className="service-config-modal-panel" onClose={() => setConfigOpen(false)} title={`${service.registry.label} Run Configuration`}>
          <ServiceConfigurationPanel service={service} />
        </Modal>
      ) : null}
      {dependenciesOpen ? (
        <Modal className="service-dependencies-modal-panel" onClose={() => setDependenciesOpen(false)} title={`${service.registry.label} Dependencies`}>
          <ServiceDependenciesPanel service={service} setupRows={serviceSetupRows(service)} />
        </Modal>
      ) : null}
      {service.registry.id === "news" ? (
        <NewsServiceWorkAndRows service={service} />
      ) : service.registry.id === "sec" ? (
        <SecServiceWorkAndRows service={service} />
      ) : (
        <ServiceWorkAndActivity service={service} />
      )}
      <ServiceErrorLogPanel pageError={pageError} service={service} />
    </>
  );
}


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

function SecServiceWorkAndRows({ service }: { service: ServiceStatusPayload }) {
  const [todaySort, setTodaySort] = useState<SecTodaySort>("desc");
  const todaySec = useSecTodayRows(service.registry.id === "sec", todaySort);
  return (
    <section className="service-work-and-activity-grid service-work-and-activity-sec">
      <ServiceWorkPlanPanel secToday={todaySec} service={service} />
      <SecTodayRowsPanel onSortChange={setTodaySort} state={todaySec} />
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

function ServiceWorkPlanPanel({ secToday, service }: { secToday?: SecTodayRowsState; service: ServiceStatusPayload }) {
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
          <ServiceWorkResponsibilityGrid groups={visibleGroups} newsPollHistory={newsPollHistory} secToday={secToday} service={service} />
        </section>
      </div>
    </Panel>
  );
}

function ServiceWorkResponsibilityGrid({
  groups,
  newsPollHistory,
  secToday,
  service,
}: {
  groups: ServiceWorkGroup[];
  newsPollHistory: NewsPollHistoryRow[];
  secToday?: SecTodayRowsState;
  service: ServiceStatusPayload;
}) {
  const visibleGroups = visibleServiceWorkGroups(groups, service.registry.id);
  return (
    <div className="service-work-responsibility-grid">
      {visibleGroups.map((group) => group.id === "live" && service.registry.id === "news" ? (
        <NewsBenzingaLiveCard group={group} history={newsPollHistory} key={group.id} service={service} />
      ) : group.id === "live" && service.registry.id === "sec" ? (
        <SecLiveFeedCard group={group} histogram={secToday?.histogram ?? defaultSecHistogramWindow(900)} key={group.id} service={service} />
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

function SecLiveFeedCard({ group, histogram, service }: { group: ServiceWorkGroup; histogram: SecDailyHistogramState; service: ServiceStatusPayload }) {
  const metrics = serviceMetricsRecord(service);
  const rows = secLiveFeedRows(service);
  const status = stringMetric(metrics, ["last_error_status", "run_status", "status"]) || group.status || "idle";
  const summary = secHistogramSummary(histogram.rows);
  const queueDepth = numericMetric(metrics, ["queue_depth", "feed_queue_depth", "pending_filings"]);
  return (
    <section className={`service-work-responsibility-card sec-live-card ${workStatusClass(status)}`}>
      <div className="service-work-responsibility-header news-live-card-header">
        <div>
          <h3>{group.title}</h3>
          <p>{group.description}</p>
        </div>
        <span className={`service-work-status ${workStatusClass(status)}`}>{displayName(status)}</span>
      </div>
      <SecDailyHistogram
        binSeconds={histogram.binSeconds}
        data={histogram.rows}
        error={histogram.error}
        windowEndUtc={histogram.windowEndUtc}
        windowStartUtc={histogram.windowStartUtc}
      />
      <div className="news-live-summary sec-live-summary">
        <span><small>Submissions</small><strong>{formatCompactNumber(summary.total)}</strong></span>
        <span><small>Filing Only</small><strong>{formatCompactNumber(summary.filingOnly)}</strong></span>
        <span><small>Docs</small><strong>{formatCompactNumber(summary.documents)}</strong></span>
        <span className={summary.text > 0 ? "metric-good" : ""}><small>Text</small><strong>{formatCompactNumber(summary.text)}</strong></span>
        <span className={summary.xbrl > 0 ? "metric-good" : ""}><small>XBRL</small><strong>{formatCompactNumber(summary.xbrl)}</strong></span>
        <span><small>Queue</small><strong>{formatCompactNumber(queueDepth)}</strong></span>
      </div>
      <SecLiveFeedTable rows={rows} />
    </section>
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
              <ServiceTableTimeCell compact value={row.pollAt} />
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

function SecLiveFeedTable({ rows }: { rows: SecLiveFeedRow[] }) {
  return (
    <div className="news-poll-history-table-wrap sec-live-feed-table-wrap">
      <table className="news-poll-history-table sec-live-feed-table">
        <thead>
          <tr>
            <th title="When the SEC feed item was observed by the gateway, shown in your local browser timezone.">Time</th>
            <th title="Central Index Key reported by SEC.">CIK</th>
            <th title="SEC filing form type, such as 10-K, 8-K, 4, 424B2, or FWP.">Form</th>
            <th title="SEC accession number for the filing.">Accession</th>
            <th title="Gateway status for this live feed item.">Status</th>
            <th title="Company or filing title from the live feed item.">Filing</th>
            <th title="Document and extracted text counts reported with this item.">Docs / Text</th>
            <th title="XBRL facts or frame observations reported with this item.">XBRL</th>
          </tr>
        </thead>
        <tbody>
          {(rows.length ? rows : [null]).map((row, index) => row ? (
            <tr className={workStatusClass(row.status)} key={`${row.accession}-${row.time}-${index}`}>
              <ServiceTableTimeCell compact timeMs={row.timeMs} value={row.time} />
              <td title={row.cik}>{row.cik || "-"}</td>
              <td>{row.form || "-"}</td>
              <td title={row.accession}>{row.accession || "-"}</td>
              <td><span className={`service-work-mini-status ${workStatusClass(row.status)}`}>{displayName(row.status)}</span></td>
              <td title={row.title || row.company}>{row.title || row.company || "-"}</td>
              <td title={row.documents}>{row.documents || "-"}</td>
              <td title={row.xbrl}>{row.xbrl || "-"}</td>
            </tr>
          ) : (
            <tr key={`empty-${index}`}>
              <td colSpan={8}>No live SEC feed item has been observed by this dashboard yet.</td>
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
                <ServiceTableTimeCell compact value={row.time} />
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
                <ServiceTableTimeCell compact value={row.time} />
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
                <ServiceTableTimeCell compact value={row.time} />
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
  const parsed = parseServiceTimestamp(value);
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

function formatSeconds(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "-";
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}

function WorkPlanSummaryItem({ label, title = "", tone = "", value }: { label: string; title?: string; tone?: string; value: string }) {
  return (
    <div className={tone ? `service-work-plan-summary-item ${tone}` : "service-work-plan-summary-item"} title={title || label}>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

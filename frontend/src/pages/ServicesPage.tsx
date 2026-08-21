import { Activity, CheckCircle2, Clock3, Layers3, Loader2, RadioTower, RefreshCcw, Search, Settings2, X } from "lucide-react";
import { lazy, Suspense, useMemo, useState } from "react";

import { api } from "../api/client";
import { Button } from "../app/components/Button";
import { DataTable } from "../app/components/DataTable";
import { MetricRatio } from "../app/components/MetricRatio";
import { Modal } from "../app/components/Modal";
import { useWallClock } from "../app/components/useWallClock";
import { displayName, formatBytes, formatCompactNumber, formatDuration } from "../app/format";
import { usePollingTask } from "../app/hooks/usePollingTask";
import type { ServicePageMode } from "../app/routes";
import type { ServiceStatusPayload, WorkloadBudgetPayload } from "../features/services/contracts";
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
import type {
  NewsCoverageHistoryRow,
  NewsEnrichmentArticleRow,
  NewsEnrichmentHistoryRow,
  NewsPollHistoryRow,
  NewsPublishHistoryRow,
} from "../features/services/newsWorkContracts";
import {
  coverageStatusClass,
  coverageStatusLabel,
  enrichmentUrlLabel,
  formatSeconds,
  newsCoverageHistoryRows,
  newsEnrichmentArticleUrlLabel,
  newsEnrichmentHistoryRows,
  newsLiveBadge,
  newsPollHistorySummary,
  newsPublishHistoryRows,
  newsWorkPlanSummaryItems,
  shortPollId,
} from "../features/services/newsWorkPresentation";
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
  stringMetric,
  sumTableCounts,
  tableTimestamp,
  textEmbedCoverageTotals,
} from "../features/services/metrics";
import {
  runtimeLogRows,
} from "../features/services/diagnostics";
import { ServicePanel as Panel } from "../features/services/ServicePanel";
import { ServiceStatusBadge } from "../features/services/ServiceStatusIndicators";
import { ServiceTableTimeCell } from "../features/services/ServiceTableTimeCell";
import { ServiceTimeCard } from "../features/services/ServiceTimeCard";
import type { ServiceWorkGroup, ServiceWorkRow } from "../features/services/serviceWorkContracts";
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
import { useNewsPollHistory } from "../features/services/useNewsPollHistory";
import { useNewsTodayRows } from "../features/services/useNewsTodayRows";
import {
  compactJson,
  normalizeRow,
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

function WorkPlanSummaryItem({ label, title = "", tone = "", value }: { label: string; title?: string; tone?: string; value: string }) {
  return (
    <div className={tone ? `service-work-plan-summary-item ${tone}` : "service-work-plan-summary-item"} title={title || label}>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

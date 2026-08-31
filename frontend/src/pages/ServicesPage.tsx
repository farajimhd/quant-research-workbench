import { Activity, CheckCircle2, Clock3, Layers3, RadioTower, RefreshCcw, Search, Settings2, X } from "lucide-react";
import { lazy, Suspense, useMemo, useState } from "react";

import { api } from "../api/client";
import { Button } from "../app/components/Button";
import { DataTable } from "../app/components/DataTable";
import { LoadingState } from "../app/components/LoadingState";
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
import { ServiceDependenciesPanel } from "../features/services/ServiceDependenciesPanel";
import { ServiceErrorLogPanel } from "../features/services/ServiceErrorLogPanel";
import { ServiceActivityPanel, serviceRecentSourceRows } from "../features/services/ServiceActivityPanel";
import {
  fleetDatabaseSummary,
} from "../features/services/fleetPresentation";
import { ServiceFleetCard } from "../features/services/ServiceFleetCard";
import { ServiceMetadataTable } from "../features/services/ServiceMetadataTable";
import type { NewsTodayRowsState, NewsTodaySort } from "../features/services/newsContracts";
import type { NewsPollHistoryRow } from "../features/services/newsWorkContracts";
import { newsWorkPlanSummaryItems } from "../features/services/newsWorkPresentation";
import {
  NewsBenzingaLiveCard,
  NewsCoverageGapCard,
  NewsDatabasePublishingCard,
  NewsEnrichmentCanonicalCard,
} from "../features/services/NewsWorkPlanCards";
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
  return <Suspense fallback={<Panel title="BarGPT Operational Configuration"><LoadingState label="Loading configuration" /></Panel>}>
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
        <LoadingState className="services-page-loading-overlay" label={loading ? "Loading service status" : "Loading service details"} />
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

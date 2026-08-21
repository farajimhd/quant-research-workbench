import { formatCompactNumber } from "../../app/format";
import type { ServiceId } from "../../app/routes";
import type { ServiceStatusPayload } from "./contracts";
import { numericMetric, serviceMetricsRecord, stringMetric } from "./metrics";
import { serviceResponsibilitySpecs } from "./serviceResponsibilitySpecs";
import type { ServiceWorkGroup, ServiceWorkRow, WorkPlanSummaryMetric } from "./serviceWorkContracts";
import { humanizeWorkDetail } from "./statusPresentation";
import {
  arrayRows,
  compactWorkDetail,
  firstString,
  firstTimestamp,
  formatValue,
  isRecord,
  workStatusClass,
  workStatusRank,
} from "./workPresentation";

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

export function visibleServiceWorkGroups(groups: ServiceWorkGroup[], serviceId: ServiceId) {
  return orderedServiceWorkGroups(groups, serviceId).filter((group) => group.id !== "other" || group.rows.length);
}

export function serviceWorkPlanSummaryItems(groups: ServiceWorkGroup[]): WorkPlanSummaryMetric[] {
  const liveCounts = serviceWorkPlanSummary(groups);
  return [
    { label: "Areas", value: String(liveCounts.areas) },
    { label: "Active Tasks", tone: liveCounts.activeTasks ? "active" : undefined, value: formatCompactNumber(liveCounts.activeTasks) },
    { label: "Completed Tasks", tone: liveCounts.completedTasks ? "ok" : undefined, value: formatCompactNumber(liveCounts.completedTasks) },
    { label: "Warnings / Errors", tone: liveCounts.warningTasks ? "warn" : "ok", value: formatCompactNumber(liveCounts.warningTasks) },
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

function serviceWorkRows(service: ServiceStatusPayload): ServiceWorkRow[] {
  const snapshot = service.snapshot ?? {};
  const rows: ServiceWorkRow[] = [];
  if (isRecord(service.current_operation) && Object.keys(service.current_operation).length) {
    rows.push(serviceWorkRow(
      {
        ...service.current_operation,
        name: service.current_operation.phase || service.current_operation.status || "current operation",
      },
      "current operation",
      "live",
    ));
  }
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

export function serviceSetupRows(service: ServiceStatusPayload): ServiceWorkRow[] {
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

export function serviceWorkGroups(service: ServiceStatusPayload): ServiceWorkGroup[] {
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

export function fleetWorkSummary(services: ServiceStatusPayload[]) {
  return services.reduce(
    (summary, service) => {
      for (const group of visibleServiceWorkGroups(serviceWorkGroups(service), service.registry.id)) {
        summary.active += group.activeCount;
        summary.completed += group.completedCount;
        summary.warning += group.warningCount;
      }
      return summary;
    },
    { active: 0, completed: 0, warning: 0 },
  );
}

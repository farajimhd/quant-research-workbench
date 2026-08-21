import { displayName, formatCompactNumber } from "../../app/format";
import type { ServiceDatabaseTableRow, ServiceStatusPayload } from "./contracts";
import { isRecord } from "./workPresentation";

export function textEmbedCoverageTotals(metrics: Record<string, unknown>) {
  const reports = isRecord(metrics.source_reports) ? metrics.source_reports : {};
  let detected = 0;
  let completed = 0;
  let remaining = 0;
  let found = false;
  for (const mode of Object.values(reports)) {
    if (!isRecord(mode)) continue;
    for (const report of Object.values(mode)) {
      if (!isRecord(report)) continue;
      found = true;
      detected += optionalNumber(report.source_detected) + optionalNumber(report.embedding_detected) + optionalNumber(report.context_detected);
      completed += optionalNumber(report.source_completed) + optionalNumber(report.embedding_completed) + optionalNumber(report.context_completed);
      remaining += optionalNumber(report.source_remaining) + optionalNumber(report.embedding_remaining) + optionalNumber(report.context_remaining) + optionalNumber(report.context_blocked);
    }
  }
  return { completed: found ? completed : null, detected: found ? detected : null, remaining: found ? remaining : null };
}

export function arrayRecords(value: unknown) {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

export function metricStatus(record: Record<string, unknown>, keys: string[]) {
  const value = stringMetric(record, keys);
  return value ? displayName(value) : "—";
}

export function hasRemaining(completed: number | null, total: number | null) {
  return completed !== null && total !== null && completed < total;
}

export function remainingLabel(completed: number | null, total: number | null, unit: string) {
  if (completed === null || total === null) return "not reported";
  const remaining = Math.max(0, total - completed);
  return remaining ? `${formatCompactNumber(remaining)} ${unit} remaining` : "caught up";
}

export function differenceLabel(completed: number | null, total: number | null, label: string) {
  if (completed === null || total === null) return "not reported";
  return `${formatCompactNumber(Math.max(0, total - completed))} ${label}`;
}

export function numericMetricOptional(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    if (record[key] === undefined || record[key] === null || record[key] === "") continue;
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

export function optionalNumber(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function optionalNumberOrNull(value: unknown) {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function sumTableCounts(rows: ServiceDatabaseTableRow[], key: "rows" | "rows_today") {
  let found = false;
  const total = rows.reduce((sum, row) => {
    const raw = row[key];
    if (!raw || raw === "-") return sum;
    const value = Number(raw.replaceAll(",", ""));
    if (!Number.isFinite(value)) return sum;
    found = true;
    return sum + value;
  }, 0);
  return found ? total : null;
}

export function tableTimestamp(value: string | undefined) {
  if (!value || value === "-") return 0;
  const normalized = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  const parsed = new Date(normalized).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

export function statusIsHealthy(value: string) {
  return /^(ok|ready|healthy|completed|success|running|allowed)$/i.test(value.trim());
}

export function serviceMetricsRecord(service: ServiceStatusPayload) {
  const serviceSpecific = service.snapshot?.service_specific;
  const runtime = service.snapshot?.runtime;
  return {
    ...(isRecord(runtime) ? runtime : {}),
    ...(isRecord(service.metrics) ? service.metrics : {}),
    ...(isRecord(serviceSpecific) ? serviceSpecific : {}),
  };
}

export function numericMetric(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
}

export function stringMetric(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value);
  }
  return "";
}

export function stringArrayMetric(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean);
    if (value !== undefined && value !== null && String(value).trim()) return [String(value).trim()];
  }
  return [];
}

export function arrayValueLabel(value: unknown) {
  if (!Array.isArray(value)) return "";
  return value.map((item) => String(item || "").trim()).filter(Boolean).join(", ");
}

export function uniqueStringSample(values: string[], limit: number) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean))).slice(0, limit);
}

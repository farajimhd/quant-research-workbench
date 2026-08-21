import { displayName, formatCell } from "../../app/format";
import type { ServiceStatusTone } from "./contracts";
import { formatLogTime, parseServiceTimestamp } from "./time";

export function firstTimestamp(row: Record<string, unknown>) {
  const raw = firstString(row, ["updated_at_utc", "last_seen_at_utc", "last_run_at_utc", "completed_at_utc", "started_at_utc", "last_poll_at_utc", "checked_at_utc", "ts_utc", "time_utc", "updated_at", "last_seen", "last_run", "completed_at", "started_at", "last_poll_at", "checked_at", "time", "since"]);
  if (!raw || raw === "-") return { label: "-", value: undefined };
  const parsed = parseServiceTimestamp(raw);
  if (!Number.isFinite(parsed)) return { label: raw.length > 28 ? `${raw.slice(0, 25)}...` : raw, value: undefined };
  return { label: formatLogTime(raw), value: parsed };
}

export function firstString(row: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value === undefined || value === null || value === "") continue;
    return formatValue(key, value);
  }
  return "";
}

export function compactWorkDetail(row: Record<string, unknown>) {
  const omitted = new Set(["area", "category", "completed", "completion_pct", "count", "database", "done", "expected", "finished", "interval", "item", "kind", "label", "name", "next", "next_poll", "next_run", "percent", "phase", "processed", "processed_rows", "progress", "progress_pct", "result", "role", "row_count", "rows", "schedule", "sink", "source", "state", "status", "table", "target", "targets", "task", "total", "type", "window", "work", "written_rows"]);
  const parts = Object.entries(row)
    .filter(([key, value]) => !omitted.has(key) && value !== undefined && value !== null && value !== "")
    .slice(0, 4)
    .map(([key, value]) => `${displayName(key)} ${formatValue(key, value)}`);
  return parts.length ? parts.join("; ") : "-";
}

export function normalizedStatus(status: string) {
  return String(status || "").toLowerCase().replace(/[^a-z0-9]+/g, "_");
}

export function workStatusClass(status: string): ServiceStatusTone {
  const normalized = normalizedStatus(status);
  if (/failed|error|blocked|critical|offline|not_started|unreachable/.test(normalized)) return "error";
  if (/warn|degraded|retry|queued|pending|waiting|attention/.test(normalized)) return "warn";
  if (/running|working|active|loading|polling|publishing|processing|ingesting|syncing|repairing|catching_up|preflight|starting/.test(normalized)) return "active";
  if (/complete|completed|ok|ready|success|healthy|observed/.test(normalized)) return "ok";
  if (/idle|noop|no_op|not_reported/.test(normalized)) return "idle";
  return "waiting";
}

export function workStatusRank(status: string) {
  const className = workStatusClass(status);
  if (className === "error") return 0;
  if (className === "warn") return 1;
  if (className === "active") return 2;
  if (className === "waiting") return 3;
  return 4;
}

export function arrayRows(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map(normalizeRow);
}

export function normalizeRow(row: Record<string, unknown>) {
  const normalized: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(row)) normalized[key] = typeof value === "object" && value !== null ? compactJson(value) : value;
  return normalized;
}

export function compactJson(value: unknown) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function formatValue(key: string, value: unknown) {
  if (typeof value === "number") return formatCell(key, value);
  if (typeof value === "string") return value || "-";
  return compactJson(value);
}

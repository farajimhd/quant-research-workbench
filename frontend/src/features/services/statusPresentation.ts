import { displayName, formatCompactNumber, formatDuration } from "../../app/format";
import { SERVICE_IDS } from "../../app/routes";
import type { ServiceStatusPayload, ServiceStatusTone } from "./contracts";
import { formatLogTime, parseServiceTimestamp } from "./time";

export type StatusInfo = {
  className: string;
  description: string;
  label: string;
  tone: ServiceStatusTone;
};

export function statusInfo(service: Pick<ServiceStatusPayload, "online" | "status">): StatusInfo {
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

export function serviceFreshness(service: ServiceStatusPayload, now: Date): { label: string; tone: "error" | "fresh" | "idle" | "stale" } {
  if (!service.online) return { label: "Endpoint offline", tone: "error" };
  const snapshotAt = firstStringMetric(service.header, ["snapshot_utc", "checked_at_utc", "updated_at_utc"]) || service.checked_at_utc;
  const parsed = parseServiceTimestamp(snapshotAt);
  if (!Number.isFinite(parsed)) return { label: "Freshness unknown", tone: "idle" };
  const ageSeconds = Math.max(0, (now.getTime() - parsed) / 1000);
  if (ageSeconds <= 15) return { label: "Live now", tone: "fresh" };
  if (ageSeconds <= 60) return { label: `${Math.floor(ageSeconds)}s old`, tone: "idle" };
  return { label: `Stale · ${relativeServiceAge(snapshotAt, now)}`, tone: "stale" };
}

export function relativeServiceAge(value: string, now: Date): string {
  const parsed = parseServiceTimestamp(value);
  if (!Number.isFinite(parsed)) return "unknown";
  const seconds = Math.max(0, Math.floor((now.getTime() - parsed) / 1000));
  if (seconds < 5) return "now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

export function fleetMarketStatus(services: ServiceStatusPayload[]): { detail: string; status: string } {
  for (const service of services) {
    const runtime = isRecord(service.snapshot?.runtime) ? service.snapshot.runtime : {};
    const candidates = [
      service.header?.market_status,
      service.header?.market_session,
      service.metrics?.market_status,
      service.metrics?.current_market_session,
      runtime.market_status,
      runtime.current_market_session,
    ];
    const value = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
    if (value) {
      const source = String(service.metrics?.market_status_source || service.header?.market_status_source || service.registry.label);
      return { status: String(value), detail: marketSourceLabel(source || service.registry.label) };
    }
  }
  return { status: "not reported", detail: "No gateway has reported market state yet" };
}

export function marketTileClass(status: string, detail: string): string {
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

export function sortServices(services: ServiceStatusPayload[]) {
  return [...services].sort((left, right) => SERVICE_IDS.indexOf(left.registry.id) - SERVICE_IDS.indexOf(right.registry.id));
}

export function countStatuses(services: ServiceStatusPayload[]) {
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

export function phaseText(service: ServiceStatusPayload) {
  return String(service.current_operation?.phase || service.current_operation?.status || service.header?.market_status || "-");
}

export function currentMessage(service: ServiceStatusPayload) {
  return String(service.current_operation?.message || service.current_operation?.next_action || service.errors?.snapshot || "");
}

export function offlineReason(service: ServiceStatusPayload) {
  return String(service.errors?.snapshot || service.errors?.health || service.errors?.metrics || "");
}

export function cardMessage(service: ServiceStatusPayload) {
  if (!service.online) return friendlyServiceError(offlineReason(service)) || "Service endpoint is not responding.";
  return currentMessage(service) || service.registry.description;
}

export function friendlyServiceError(value: string) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/timed?\s*out/i.test(text)) return "Endpoint timed out. Confirm the gateway process and bind address.";
  if (/refused|actively refused|connection reset/i.test(text)) return "Endpoint refused the connection. Confirm the gateway process is running.";
  return humanizeWorkDetail(text);
}

export function runtimeText(service: ServiceStatusPayload) {
  const runtime = service.snapshot?.runtime;
  if (!runtime || typeof runtime !== "object") return "-";
  const record = runtime as Record<string, unknown>;
  const keys = ["poll_runs", "processed_rows", "written_rows", "feed_items", "ingest_events", "embedding_rows_written", "cycles"];
  const found = keys.find((key) => record[key] !== undefined && record[key] !== null && record[key] !== "");
  return found ? `${displayName(found)} ${formatCompactNumber(record[found])}` : "-";
}

export function serviceRunTiming(service: ServiceStatusPayload) {
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

export function humanizeWorkDetail(value: string) {
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

function marketSourceLabel(source: string): string {
  const normalized = source.toLowerCase();
  if (normalized === "massive_market_calendar") return "Massive status + calendar";
  if (normalized === "massive_status") return "Massive status";
  if (normalized === "local_clock") return "Local clock";
  if (normalized === "disabled") return "Market status disabled";
  return displayName(source);
}

function firstStringMetric(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
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

function shortenWorkValue(value: string) {
  if (!value) return "-";
  if (value.length <= 120) return value;
  const slashParts = value.split(/[\\/]/).filter(Boolean);
  if (slashParts.length >= 3) return `.../${slashParts.slice(-3).join("/")}`;
  return `${value.slice(0, 117)}...`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

import { displayName } from "../../app/format";
import type { ServiceStatusPayload, ServiceStatusTone } from "./contracts";
import { parseServiceTimestamp } from "./time";

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

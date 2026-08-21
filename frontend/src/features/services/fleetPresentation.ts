import { formatBytes, formatCompactNumber, formatDuration } from "../../app/format";
import type { ServiceId } from "../../app/routes";
import type { ServiceStatusPayload } from "./contracts";
import {
  arrayRecords,
  differenceLabel,
  hasRemaining,
  metricStatus,
  numericMetricOptional,
  optionalNumber,
  optionalNumberOrNull,
  remainingLabel,
  serviceMetricsRecord,
  statusIsHealthy,
  stringMetric,
  sumTableCounts,
  tableTimestamp,
  textEmbedCoverageTotals,
} from "./metrics";
import { formatServiceTime } from "./time";
import { isRecord } from "./workPresentation";

export type ServiceFleetMetric = {
  detail: string;
  label: string;
  tone?: "good" | "neutral" | "warn";
  value: string;
  valueParts?: {
    current: string;
    total: string;
  };
};

export type ServiceFleetDatabaseSummary = {
  latest: string;
  overall: string;
  product: string;
  status: string;
  statusParts?: {
    current: string;
    suffix: string;
    total: string;
  };
  today: string;
  tone: "good" | "neutral";
};

const SERVICE_PRIMARY_DATABASE_ROLES: Record<ServiceId, string[]> = {
  ibkr: ["supervisor events"],
  news: ["normalized news"],
  qmd: ["live events"],
  "qmd-history": [],
  "news-hypothesis": ["contextual hypotheses"],
  "bar-gpt": [],
  "model-gateway": [],
  "text-intelligence": ["semantic labels"],
  reference: ["tradable universe"],
  sec: ["filings"],
  "text-embed": ["news embeddings", "sec embeddings"],
};

export function serviceFleetMetrics(service: ServiceStatusPayload): ServiceFleetMetric[] {
  const metrics = serviceMetricsRecord(service);
  const number = (keys: string[]) => numericMetricOptional(metrics, keys);
  const compact = (value: number | null) => value === null ? "—" : formatCompactNumber(value);
  const ratio = (left: number | null, right: number | null) => {
    if (left === null && right === null) return { value: "—" };
    const current = compact(left);
    const total = compact(right);
    return { value: `${current} / ${total}`, valueParts: { current, total } };
  };
  const detail = (text: string, fallback: string) => text.trim() || fallback;

  if (service.registry.id === "text-intelligence") {
    return [
      { label: "Processed", value: compact(number(["processed"])), detail: "validated semantic labels" },
      { label: "Queued", value: compact(number(["queued", "queue_size"])), detail: "bounded live work" },
      { label: "Filtered", value: compact(number(["filtered"])), detail: "scope, session, hours, or price gate" },
      { label: "Failed", value: compact(number(["failed"])), detail: "retryable semantic work", tone: (number(["failed"]) ?? 0) > 0 ? "warn" : "neutral" },
    ];
  }
  if (service.registry.id === "model-gateway") {
    return [
      { label: "Routes", value: compact(number(["route_count"])), detail: "named inference contracts" },
      { label: "Concurrency", value: compact(number(["max_concurrency"])), detail: "bounded provider calls" },
      { label: "Providers", value: compact(number(["provider_count"])), detail: "local and remote profiles" },
      { label: "Status", value: service.online ? "Ready" : "Unavailable", detail: "schema, budget, and idempotency authority", tone: service.online ? "good" : "warn" },
    ];
  }
  if (service.registry.id === "news-hypothesis") {
    return [
      { label: "Completed", value: compact(number(["completed"])), detail: "persisted hypotheses" },
      { label: "Queued", value: compact(number(["queued", "queue_size"])), detail: "deep contextual work" },
      { label: "Failed", value: compact(number(["failed"])), detail: "reconciled while unexpired", tone: (number(["failed"]) ?? 0) > 0 ? "warn" : "neutral" },
      { label: "Authority", value: "Advisory", detail: "no order or sizing control" },
    ];
  }
  if (service.registry.id === "bar-gpt") {
    return [
      { label: "Predictions", value: compact(number(["predictions"])), detail: "published causal forecasts" },
      { label: "Batches", value: compact(number(["inference_batches"])), detail: "full-prefix dynamic batches" },
      { label: "Warm", value: compact(number(["warm_completed"])), detail: "ticker contexts ready" },
      { label: "Failures", value: compact(number(["failed_batches", "warm_failed"])), detail: "visible serving failures", tone: (number(["failed_batches", "warm_failed"]) ?? 0) > 0 ? "warn" : "neutral" },
    ];
  }
  if (service.registry.id === "news") {
    const polled = number(["provider_rows", "processed_rows", "raw_saved"]);
    const processed = number(["processed_rows"]);
    const duplicates = number(["duplicate_news_rows"]);
    const unique = number(["unique_news_rows"]) ?? (processed !== null && duplicates !== null ? Math.max(0, processed - duplicates) : null);
    const enriched = number(["background_enriched_urls"]);
    const required = number(["background_fetch_tasks"]);
    const written = number(["written_rows"]);
    const coverageDone = number(["gap_fill_flushed_chunks"]);
    const coverageTotal = number(["gap_fill_total_chunks"]);
    return [
      { label: "Unique / Polled", ...ratio(unique, polled), detail: `${compact(duplicates)} repeat rows` },
      { label: "Enriched / Required", ...ratio(enriched, required), detail: remainingLabel(enriched, required, "URLs"), tone: hasRemaining(enriched, required) ? "warn" : "neutral" },
      { label: "Inserted", value: compact(written), detail: differenceLabel(written, unique, "unique not written"), tone: hasRemaining(written, unique) ? "warn" : "neutral" },
      { label: "Coverage Filled", ...ratio(coverageDone, coverageTotal), detail: remainingLabel(coverageDone, coverageTotal, "chunks"), tone: hasRemaining(coverageDone, coverageTotal) ? "warn" : "neutral" },
    ];
  }
  if (service.registry.id === "sec") {
    const processed = number(["processed_filings"]);
    const feed = number(["feed_items"]);
    const written = number(["written_filings"]);
    const workerFailures = number(["live_worker_failures"]);
    const contextPending = number(["xbrl_context_pending_rows"]);
    const contextFailures = number(["xbrl_context_sync_failures"]);
    return [
      { label: "Processed / Feed", ...ratio(processed, feed), detail: remainingLabel(processed, feed, "filings"), tone: hasRemaining(processed, feed) ? "warn" : "neutral" },
      { label: "Written / Processed", ...ratio(written, processed), detail: differenceLabel(written, processed, "not written"), tone: hasRemaining(written, processed) ? "warn" : "neutral" },
      { label: "Workers Active", ...ratio(number(["live_active_workers"]), number(["live_workers"])), detail: `${compact(number(["live_queue_size"]))} queued · ${compact(workerFailures)} failed`, tone: (workerFailures ?? 0) > 0 ? "warn" : "neutral" },
      { label: "XBRL Facts", value: compact(number(["xbrl_company_fact_rows"])), detail: `${compact(contextPending)} pending · ${compact(contextFailures)} failed`, tone: (contextPending ?? 0) + (contextFailures ?? 0) > 0 ? "warn" : "neutral" },
    ];
  }
  if (service.registry.id === "text-embed") {
    const coverage = textEmbedCoverageTotals(metrics);
    return [
      { label: "Completed / Detected", ...ratio(coverage.completed, coverage.detected), detail: `${compact(coverage.remaining)} gaps remaining`, tone: coverage.remaining ? "warn" : "neutral" },
      { label: "Embeddings Written", value: compact(number(["embedding_rows_written"])), detail: `${compact(number(["coverage_rows_written"]))} coverage rows` },
      { label: "Last Batch", value: compact(number(["last_embedding_sequences"])), detail: detail(stringMetric(metrics, ["last_embedding_stage", "last_embedding_source"]), "not reported") },
      { label: "Last Throughput", value: number(["last_embedding_sequences_per_second"]) === null ? "—" : `${number(["last_embedding_sequences_per_second"])!.toFixed(1)}/s`, detail: detail(stringMetric(metrics, ["last_embedding_source", "active_source"]), "not reported") },
    ];
  }
  if (service.registry.id === "reference") {
    const sources = arrayRecords(metrics.source_statuses);
    const tables = arrayRecords(metrics.table_progress);
    const healthySources = sources.filter((row) => statusIsHealthy(String(row.status ?? ""))).length;
    const present = tables.reduce((sum, row) => sum + optionalNumber(row.tables_present), 0);
    const total = tables.reduce((sum, row) => sum + optionalNumber(row.tables_total), 0);
    return [
      { label: "Sources Healthy", ...ratio(sources.length ? healthySources : null, sources.length || null), detail: `${Math.max(0, sources.length - healthySources)} need attention`, tone: sources.length > healthySources ? "warn" : "neutral" },
      { label: "Rows Fetched", value: compact(number(["source_rows_fetched"])), detail: "latest sync" },
      { label: "Tables Present", ...ratio(tables.length ? present : null, tables.length ? total : null), detail: `${Math.max(0, total - present)} missing`, tone: total > present ? "warn" : "neutral" },
      { label: "Audit Failures", value: compact(number(["audit_failures"])), detail: detail(stringMetric(metrics, ["audit_status"]), "not reported"), tone: (number(["audit_failures"]) ?? 0) > 0 ? "warn" : "neutral" },
    ];
  }
  if (service.registry.id === "ibkr") {
    return [
      { label: "Gateway Session", value: metricStatus(metrics, ["gateway_status"]), detail: "process + listener" },
      { label: "Authentication", value: metricStatus(metrics, ["auth_status"]), detail: "session readiness" },
      { label: "Account", value: metricStatus(metrics, ["account_status"]), detail: "routing readiness" },
      { label: "Keepalive", value: metricStatus(metrics, ["keepalive_status"]), detail: `${compact(number(["poll_runs"]))} tickles · ${compact(number(["poll_failures"]))} failures since supervisor start` },
    ];
  }
  if (service.registry.id === "qmd-history") {
    const config = isRecord(service.health.config) ? service.health.config : {};
    const operations = isRecord(service.operations) ? service.operations : {};
    const coverage = isRecord(operations.coverage) ? operations.coverage : {};
    const cache = isRecord(operations.cache) ? operations.cache : {};
    const queues = isRecord(operations.queues) ? operations.queues : {};
    const hitRate = numericMetricOptional(cache, ["hit_rate"]);
    return [
      { label: "Archive Through", value: stringMetric(coverage, ["archive_session_date"]) || "Unknown", detail: stringMetric(coverage, ["message"]) || "No archive watermark reported", tone: stringMetric(coverage, ["status"]) === "ready" ? "good" : "neutral" },
      { label: "Cache Hit Rate", value: hitRate === null ? "—" : `${(hitRate * 100).toFixed(1)}%`, detail: `${compact(numericMetricOptional(cache, ["hits"]))} hits · ${compact(numericMetricOptional(cache, ["misses"]))} misses` },
      { label: "Cache Footprint", value: compact(numericMetricOptional(cache, ["entries"])), detail: `${formatBytes(numericMetricOptional(cache, ["estimated_bytes"]))} allocated` },
      { label: "Active Builds", value: compact(numericMetricOptional(queues, ["active_builds"])), detail: `${compact(numericMetricOptional(queues, ["build_capacity"]))} capacity · ${compact(numericMetricOptional(config, ["batch_size"]))} row batches` },
    ];
  }

  const coverage = isRecord(service.snapshot.coverage) ? service.snapshot.coverage : {};
  const queueKeys = ["events_broadcast_dropped", "bar_events_dropped", "indicator_events_dropped", "compact_event_queue_dropped", "clickhouse_events_dropped"];
  const queueDropParts = queueKeys.map((key) => number([key])).filter((value): value is number => value !== null);
  const queueDrops = number(["queue_drop_total"]) ?? (queueDropParts.length ? queueDropParts.reduce((sum, value) => sum + value, 0) : null);
  const eventLagMs = number(["last_event_lag_ms"]);
  const liveInputPending = number(["compact_live_events_pending"]);
  const repairInputPending = number(["compact_repair_events_pending"]);
  const repairWaits = number(["gap_fill_queue_waits"]);
  return [
    { label: "Events Ingested", value: compact(number(["ingest_events"])), detail: `${compact(number(["ingest_quotes"]))} quotes · ${compact(number(["ingest_trades"]))} trades · ${eventLagMs === null ? "unknown" : formatDuration(eventLagMs / 1000)} lag` },
    { label: "Events Persisted", value: compact(number(["compact_events_persisted"])), detail: `${compact(number(["compact_events_reorder_pending"]))} reorder pending` },
    { label: "Bars Persisted", value: compact(number(["intraday_bar_rows_persisted"])), detail: `${compact(number(["intraday_bar_repairs_completed"]))} late repairs` },
    { label: "Repair / Queue", ...ratio(optionalNumberOrNull(coverage.completed_jobs), optionalNumberOrNull(coverage.total_jobs)), detail: `${compact(liveInputPending)} live · ${compact(repairInputPending)} repair pending · ${compact(repairWaits)} waits · ${compact(queueDrops)} drops`, tone: (queueDrops ?? 0) > 0 ? "warn" : "neutral" },
  ];
}

export function fleetDatabaseSummary(services: ServiceStatusPayload[]) {
  const rows = services.flatMap((service) => service.database_tables?.rows ?? []);
  const healthy = rows.filter((row) => String(row.status || "").toLowerCase() === "ok").length;
  return { healthy, missing: rows.length - healthy, total: rows.length };
}

export function serviceFleetDatabaseSummary(service: ServiceStatusPayload): ServiceFleetDatabaseSummary {
  if (service.registry.id === "qmd-history") {
    return { latest: "On request", overall: "Read only", product: "Historical source", status: service.online ? "Source ready" : "Unavailable", today: "—", tone: service.online ? "good" : "neutral" };
  }
  const rows = service.database_tables?.rows ?? [];
  if (!rows.length) {
    const error = service.database_tables?.error?.trim();
    return { latest: "—", overall: "—", product: "Primary product", status: error ? "Check failed" : "Checking", today: "—", tone: "neutral" };
  }
  const roles = SERVICE_PRIMARY_DATABASE_ROLES[service.registry.id];
  const primary = rows.filter((row) => roles.includes(String(row.role || "").toLowerCase()));
  const selected = primary.length ? primary : rows.slice(0, 1);
  const healthy = rows.filter((row) => String(row.status || "").toLowerCase() === "ok").length;
  const today = sumTableCounts(selected, "rows_today");
  const overall = sumTableCounts(selected, "rows");
  const latestRow = [...selected].sort((left, right) => tableTimestamp(right.latest_update) - tableTimestamp(left.latest_update))[0];
  const product = selected.map((row) => row.role).filter(Boolean).join(" + ") || "Primary product";
  return {
    latest: latestRow?.latest_update && latestRow.latest_update !== "-" ? formatServiceTime(latestRow.latest_update) : "—",
    overall: overall === null ? "—" : formatCompactNumber(overall),
    product,
    status: `${healthy}/${rows.length} healthy`,
    statusParts: { current: String(healthy), suffix: "healthy", total: String(rows.length) },
    today: today === null ? "—" : formatCompactNumber(today),
    tone: healthy === rows.length ? "good" : "neutral",
  };
}

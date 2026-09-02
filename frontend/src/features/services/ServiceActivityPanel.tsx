import { useState } from "react";

import { Modal } from "../../app/components/Modal";
import { displayName, formatBasisPointsWithDollar } from "../../app/format";
import type { ServiceStatusPayload } from "./contracts";
import { DebugObjectBlock } from "./DebugObjectBlock";
import { runtimeLogRows } from "./diagnostics";
import { serviceMetricsRecord, stringMetric } from "./metrics";
import { ServicePanel as Panel } from "./ServicePanel";
import { ServiceTableTimeCell } from "./ServiceTableTimeCell";
import { humanizeWorkDetail } from "./statusPresentation";
import { formatLogTime, tableRowRecencyClass } from "./time";
import {
  compactWorkDetail,
  firstString,
  firstTimestamp,
  formatValue,
  isRecord,
  workStatusClass,
} from "./workPresentation";

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

export function ServiceActivityPanel({ service }: { service: ServiceStatusPayload }) {
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
          <thead><tr><th>Time</th><th>Status</th><th>Subject</th><th>Rows</th><th>Detail</th></tr></thead>
          <tbody>
            {visibleRows.map((row, index) => (
              <tr
                className={`${workStatusClass(row.status)} ${serviceActivityRecencyClass(service, row)}`.trim()}
                key={`${row.kind}-${row.subject}-${row.time}-${index}`}
              >
                <ServiceTableTimeCell timeMs={row.timeMs} value={row.time} />
                <td><span className={`service-work-status ${workStatusClass(row.status)}`}>{displayName(row.status || "waiting")}</span></td>
                <td title={row.subject}><button className="table-primary-link" onClick={() => setSelectedRow(row)} type="button"><strong>{row.subject}</strong><span>{displayName(row.kind)}</span></button></td>
                <td>{row.rows || "-"}</td>
                <td title={row.detail}>{row.detail || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedRow ? (
        <Modal className="service-activity-detail-modal-panel" onClose={() => setSelectedRow(null)} title={`${service.registry.label} Activity Detail`}>
          <ServiceActivityDetail row={selectedRow} service={service} />
        </Modal>
      ) : null}
    </Panel>
  );
}

function ServiceActivityDetail({ row, service }: { row: ServiceActivityRow; service: ServiceStatusPayload }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="service-activity-detail">
      <div className={`service-activity-detail-status ${statusClass}`}>
        <div><span>{displayName(service.registry.kind)}</span><strong>{row.subject}</strong></div>
        <span className={`service-work-status ${statusClass}`}>{displayName(row.status)}</span>
      </div>
      <dl className="service-log-detail-grid">
        <div><dt>Time</dt><dd>{row.time || "-"}</dd></div>
        <div><dt>Kind</dt><dd>{displayName(row.kind)}</dd></div>
        <div><dt>Status</dt><dd>{displayName(row.status)}</dd></div>
        <div><dt>Rows</dt><dd>{row.rows || "-"}</dd></div>
        <div className="wide"><dt>Detail</dt><dd>{row.detail || "-"}</dd></div>
      </dl>
      <DebugObjectBlock title="Raw Service Activity Row" value={row.raw} />
    </div>
  );
}

function serviceActivitySpec(service: ServiceStatusPayload): ServiceActivitySpec {
  const metrics = serviceMetricsRecord(service);
  const status = stringMetric(metrics, ["activity_status", "run_status", "status"]) || service.status || "unknown";
  if (service.registry.id === "text-intelligence") return activitySpec("Live semantic eligibility, validation, canonical reconciliation, and label persistence.", status, "Semantic Label Activity", [
    metricSummary(metrics, "Processed", ["processed"]), metricSummary(metrics, "Queued", ["queued", "queue_size"]), metricSummary(metrics, "Filtered", ["filtered"]), metricSummary(metrics, "Failed", ["failed"], "bad"),
  ]);
  if (service.registry.id === "model-gateway") return activitySpec("Named model routes, bounded provider execution, schema validation, and cost controls.", status, "Inference Routing Activity", [
    metricSummary(metrics, "Routes", ["route_count"]), metricSummary(metrics, "Providers", ["provider_count"]), metricSummary(metrics, "Concurrency", ["max_concurrency"]),
  ]);
  if (service.registry.id === "news-hypothesis") return activitySpec("Frozen point-in-time context, deep model work, expiring hypotheses, and recovery.", status, "Contextual Hypothesis Activity", [
    metricSummary(metrics, "Completed", ["completed"]), metricSummary(metrics, "Queued", ["queued", "queue_size"]), metricSummary(metrics, "Failed", ["failed"], "bad"),
  ]);
  if (service.registry.id === "bar-gpt") return activitySpec("Mode-scoped causal context, dynamic GPU batches, raw prediction heads, and decoded forecast fields.", status, "BarGPT Serving Activity", [
    metricSummary(metrics, "Predictions", ["predictions"]), metricSummary(metrics, "Batches", ["inference_batches"]), metricSummary(metrics, "Warm", ["warm_completed"]), metricSummary(metrics, "Failed", ["failed_batches", "warm_failed"], "bad"),
  ]);
  if (service.registry.id === "qmd") return activitySpec("Recent reusable market signals, market-state events, live throughput, and persistence activity.", status, "Scanner And Market Event Activity", [
    metricSummary(metrics, "Events", ["total_events", "ingest_events", "events"]), metricSummary(metrics, "Trades/sec", ["trades_per_sec", "trades/sec", "trade_rate"]), metricSummary(metrics, "Quotes/sec", ["quotes_per_sec", "quotes/sec", "quote_rate"]), metricSummary(metrics, "Bars", ["bar_events", "bars_written", "bars"]), metricSummary(metrics, "Gaps", ["gap_count", "gaps", "coverage_gaps"], "warn"),
  ]);
  if (service.registry.id === "qmd-history") return activitySpec("Historical gateway readiness, source identity, deterministic event-window serving, and request limits.", status, "Historical Query Activity", [
    { label: "Source", value: firstString(service.health, ["source"]) || "events_YYYY" },
    { label: "Role", value: firstString(service.health, ["host_role"]) || "historical" },
    { label: "Running", value: service.health.running === true ? "yes" : "no", tone: service.health.running === true ? "good" : "bad" },
    metricSummary(metrics, "Failures", ["failure_count", "failures", "errors"], "bad"),
  ]);
  if (service.registry.id === "sec") return activitySpec("Recent SEC feed filings, duplicate skips, filing text/XBRL extraction, and write status.", status, "Latest SEC Filing Activity", [
    metricSummary(metrics, "Polls", ["poll_runs"]), metricSummary(metrics, "Feed Items", ["feed_items", "provider_rows"]), metricSummary(metrics, "Written", ["written_filings", "written_rows"], "good"), metricSummary(metrics, "Skipped", ["skipped_existing", "skips"], "warn"), metricSummary(metrics, "XBRL Facts", ["xbrl_facts", "facts_written"]),
  ]);
  if (service.registry.id === "text-embed") return activitySpec("Recent source discovery, tokenization, embedding inference, write batches, and failed work.", status, "Embedding Work Queue", [
    metricSummary(metrics, "Pending", ["pending_rows", "pending_items", "queue_depth"], "warn"), metricSummary(metrics, "Tokens", ["token_rows_written", "tokens_written", "tokens"]), metricSummary(metrics, "Embeddings", ["embedding_rows_written", "embeddings_written", "vectors_written"], "good"), metricSummary(metrics, "Batches", ["completed_batches", "batches", "batch_count"]), metricSummary(metrics, "Failed", ["failed_rows", "failed_batches", "failures"], "bad"),
  ]);
  if (service.registry.id === "reference") return activitySpec("Recent provider source sync, issue resolution, publication maintenance, and tradability guardrails.", status, "Reference Sync Activity", [
    metricSummary(metrics, "Sources", ["source_candidates", "sources_synced", "source_rows"]), metricSummary(metrics, "Issues", ["issue_writes", "open_issues", "issues"], "warn"), metricSummary(metrics, "Alerts", ["alert_writes", "alerts"]), metricSummary(metrics, "Blocks", ["tradability_blocks", "blocked_rows"], "bad"), metricSummary(metrics, "Audit", ["audit_failures", "audit_warning_count"], "warn"),
  ]);
  return activitySpec("Client Portal health, authentication, account checks, keepalive, contract lookup, and routing readiness.", status, "Broker Session Activity", [
    metricSummary(metrics, "Gateway", ["gateway_status", "client_portal_status", "run_status"]), metricSummary(metrics, "Auth", ["authenticated", "auth_status"]), metricSummary(metrics, "Keepalive", ["keepalive_count", "tickle_count", "tickles"]), metricSummary(metrics, "Accounts", ["account_count", "accounts"]), metricSummary(metrics, "Failures", ["failure_count", "failures", "errors"], "bad"),
  ]);
}

function activitySpec(description: string, status: string, title: string, summary: ServiceActivitySummaryItem[]): ServiceActivitySpec {
  return { description, status, summary, title };
}

function serviceActivityRecencyClass(service: ServiceStatusPayload, row: ServiceActivityRow) {
  return service.registry.id === "text-embed" || service.registry.id === "reference" ? tableRowRecencyClass(row.timeMs) : "";
}

function serviceActivityRows(service: ServiceStatusPayload): ServiceActivityRow[] {
  const logRows = runtimeLogRows(service.logs).slice(0, 12).map((row) => ({
    detail: row.detail, event: row.event, level: row.status, source: row.source,
    status: row.status === "active" ? "failed" : row.status === "retrying" ? "warning" : row.status,
    title: row.title, ts_utc: row.time,
  }));
  return [...serviceRecentSourceRows(service), ...logRows]
    .map((row, index) => serviceActivityRow(service, row, index))
    .sort((left, right) => (right.timeMs ?? 0) - (left.timeMs ?? 0))
    .slice(0, 36);
}

export function serviceRecentSourceRows(service: ServiceStatusPayload): Record<string, unknown>[] {
  const snapshot = service.snapshot ?? {};
  return dedupeActivityRows([
    ...rowsFromPayload(service.recent), ...rowsFromPayload(snapshot.recent_items), ...rowsFromPayload(snapshot.recent),
    ...rowsFromPayload(snapshot.feed_items), ...rowsFromPayload(snapshot.scanner), ...rowsFromPayload(snapshot.source_reports),
    ...rowsFromPayload(snapshot.sources_sinks), ...rowsFromPayload(snapshot.task_table_progress), ...rowsFromPayload(snapshot.queues),
  ]).slice(0, 40);
}

function rowsFromPayload(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter(isRecord);
  if (!isRecord(value)) return [];
  for (const key of ["rows", "items", "events", "recent", "recent_items", "feed_items", "primitives", "data"]) {
    const rows = value[key];
    if (Array.isArray(rows)) return rows.filter(isRecord);
  }
  return Object.keys(value).length ? [value] : [];
}

function dedupeActivityRows(rows: Record<string, unknown>[]) {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = [
      firstString(row, ["accession_number", "canonical_news_id", "ticker", "symbol", "event", "title", "source"]),
      firstString(row, ["updated_at_utc", "ts_utc", "time_utc", "time", "poll_at_utc"]),
      firstString(row, ["status", "state", "stage", "phase"]),
    ].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function serviceActivityRow(service: ServiceStatusPayload, row: Record<string, unknown>, index: number): ServiceActivityRow {
  const timestamp = firstTimestamp(row);
  return {
    detail: serviceActivityDetail(service, row), kind: serviceActivityKind(service, row), raw: row,
    rows: serviceActivityRowsValue(service, row), status: serviceActivityStatus(service, row),
    subject: serviceActivitySubject(service, row, index), time: timestamp.label, timeMs: timestamp.value,
  };
}

function serviceActivitySubject(service: ServiceStatusPayload, row: Record<string, unknown>, index: number) {
  if (service.registry.id === "qmd") return [firstString(row, ["ticker", "symbol", "primary_symbol"]), firstString(row, ["primitive_key", "condition", "state", "event_type", "type"])].filter(Boolean).join(" / ") || `Market activity ${index + 1}`;
  if (service.registry.id === "qmd-history") return [firstString(row, ["source", "service"]), firstString(row, ["host_role", "status"])].filter(Boolean).join(" / ") || `Historical request ${index + 1}`;
  if (service.registry.id === "sec") return [firstString(row, ["form_type", "form", "type"]), firstString(row, ["accession_number", "accession"]) || firstString(row, ["title", "company_name", "issuer_name"])].filter(Boolean).join(" / ") || `SEC filing ${index + 1}`;
  if (service.registry.id === "text-embed") return [firstString(row, ["source", "source_table", "source_kind"]), firstString(row, ["stage", "mode", "task", "event"])].filter(Boolean).join(" / ") || `Embedding work ${index + 1}`;
  if (service.registry.id === "reference") return [firstString(row, ["source", "provider", "endpoint", "event"]), firstString(row, ["ticker", "symbol", "table", "title", "task", "issue_type"])].filter(Boolean).join(" / ") || `Reference activity ${index + 1}`;
  return [firstString(row, ["event", "title", "task", "name"]), firstString(row, ["account", "account_id", "acctId", "endpoint"])].filter(Boolean).join(" / ") || `IBKR activity ${index + 1}`;
}

function serviceActivityKind(service: ServiceStatusPayload, row: Record<string, unknown>) {
  const explicit = firstString(row, ["kind", "type", "category", "source", "event"]);
  if (explicit) return explicit;
  if (service.registry.id === "qmd") return "market signal";
  if (service.registry.id === "qmd-history") return "historical gateway";
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
  return service.registry.id === "qmd" ? "active" : "observed";
}

function serviceActivityRowsValue(service: ServiceStatusPayload, row: Record<string, unknown>) {
  const direct = firstString(row, ["rows", "row_count", "processed_rows", "written_rows", "inserted_rows", "feed_items", "documents", "texts", "xbrl_facts", "embedding_rows_written", "tokens_written", "done", "completed", "count"]);
  if (direct) return direct;
  if (service.registry.id === "qmd") return firstString(row, ["score"]) ? `score ${firstString(row, ["score"])}` : firstString(row, ["volume", "dollar_volume"]) || "-";
  return "-";
}

function serviceActivityDetail(service: ServiceStatusPayload, row: Record<string, unknown>) {
  const detail = firstString(row, ["detail", "details", "message", "description", "notes", "trigger_reason", "reject_reason", "title"]);
  const extras: string[] = [];
  if (service.registry.id === "qmd") [["side_bias", "Side"], ["close", "Close"], ["vwap", "VWAP"], ["spread_bps", "Spread bps"], ["liquidity_score", "Liquidity"]].forEach(([key, label]) => extras.push(compactPair(row, key, label)));
  else if (service.registry.id === "sec") [["documents", "Docs"], ["texts", "Texts"], ["xbrl_facts", "XBRL"], ["skips", "Skips"]].forEach(([key, label]) => extras.push(compactPair(row, key, label)));
  else if (service.registry.id === "text-embed") [["mode", "Mode"], ["stage", "Stage"], ["seconds", "Seconds"]].forEach(([key, label]) => extras.push(compactPair(row, key, label)));
  else if (service.registry.id === "reference") [["provider", "Provider"], ["issue_type", "Issue"], ["action", "Action"]].forEach(([key, label]) => extras.push(compactPair(row, key, label)));
  else [["endpoint", "Endpoint"], ["authenticated", "Auth"], ["connected", "Connected"]].forEach(([key, label]) => extras.push(compactPair(row, key, label)));
  return humanizeWorkDetail([detail, ...extras.filter(Boolean)].filter(Boolean).join("; ") || compactWorkDetail(row));
}

function compactPair(row: Record<string, unknown>, key: string, label: string) {
  const value = row[key];
  if (value === undefined || value === null || value === "") return "";
  if (key.endsWith("_bps")) {
    const referencePrice = row.midpoint ?? row.close ?? row.last_price ?? row.vwap;
    return `${label}=${formatBasisPointsWithDollar(value, referencePrice)}`;
  }
  return `${label}=${formatValue(key, value)}`;
}

function metricSummary(record: Record<string, unknown>, label: string, keys: string[], tone?: ServiceActivitySummaryItem["tone"]): ServiceActivitySummaryItem {
  const { value, numeric } = metricDisplayValue(record, keys);
  return { label, tone: tone && value !== "-" && (numeric === undefined || numeric > 0) ? tone : undefined, value };
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

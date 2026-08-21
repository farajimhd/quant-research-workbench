import { useState } from "react";

import { Modal } from "../../app/components/Modal";
import { displayName, formatCompactNumber } from "../../app/format";
import type { ServiceStatusPayload } from "./contracts";
import { DebugObjectBlock } from "./DebugObjectBlock";
import { humanizeWorkDetail } from "./statusPresentation";
import { ServiceStatusBadge } from "./ServiceStatusIndicators";
import {
  arrayRows,
  compactWorkDetail,
  firstString,
  firstTimestamp,
  normalizedStatus,
  workStatusClass,
} from "./workPresentation";

export type ServiceDependencySetupRow = {
  detail: string;
  kind: string;
  lastAt: string;
  name: string;
  progress: string;
  rows: string;
  status: string;
};

type ServiceDependencyDisplayRow = {
  detail: string;
  kind: string;
  last: string;
  metric: string;
  name: string;
  raw: Record<string, unknown>;
  status: string;
};

type ServiceDependencySectionPayload = {
  description: string;
  empty: string;
  id: string;
  rows: ServiceDependencyDisplayRow[];
  title: string;
};

export function ServiceDependenciesPanel({ service, setupRows }: { service: ServiceStatusPayload; setupRows: ServiceDependencySetupRow[] }) {
  const [selectedRow, setSelectedRow] = useState<ServiceDependencyDisplayRow | null>(null);
  const snapshot = service.snapshot ?? {};
  const normalizedSetupRows = setupRows.map((row) => ({
    detail: row.detail,
    last: row.lastAt,
    name: row.name,
    progress: row.progress,
    rows: row.rows,
    status: displayName(row.status),
    type: displayName(row.kind),
  }));
  const sections: ServiceDependencySectionPayload[] = [
    section("dependency", "Dependency Checks", "Provider credentials, storage paths, ClickHouse access, market calendar, and other startup checks.", "No dependency checks reported.", arrayRows(snapshot.dependencies)),
    section("setup", "Setup Contracts", "Configured tables and contracts the service expects before live or background work starts.", "No setup or contract rows reported.", normalizedSetupRows),
    section("queue", "Queues", "Internal queue depth, active workers, pending work, and drain state.", "No queues reported.", arrayRows(snapshot.queues)),
    section("source", "Sources And Sinks", "External providers, input sources, output sinks, and their last reported state.", "No sources or sinks reported.", arrayRows(snapshot.sources_sinks)),
    section("table", "Configured Tables", "Database tables this service reads, writes, validates, or publishes.", "No configured tables reported.", arrayRows(snapshot.configured_tables)),
  ];
  const issueCount = sections.reduce((total, item) => total + item.rows.filter((row) => ["error", "warn"].includes(workStatusClass(row.status))).length, 0);
  const healthyCount = sections.reduce((total, item) => total + item.rows.filter((row) => ["ok", "active"].includes(workStatusClass(row.status))).length, 0);
  const rowCount = sections.reduce((total, item) => total + item.rows.length, 0);
  return (
    <div className="service-dependencies-panel">
      <div className="service-dependencies-hero">
        <div><span className="service-dependencies-kicker">Dependency Readiness</span><h3>{service.registry.label}</h3><p>Operational checks that determine whether this gateway can safely reach providers, storage, and database tables.</p></div>
        <ServiceStatusBadge online={service.online} status={issueCount ? "degraded" : "running"} />
      </div>
      <div className="service-dependencies-summary">
        <DependencySummaryItem label="Sections" value={String(sections.length)} />
        <DependencySummaryItem label="Rows" value={formatCompactNumber(rowCount)} />
        <DependencySummaryItem label="Healthy" tone="ok" value={formatCompactNumber(healthyCount)} />
        <DependencySummaryItem label="Issues" tone={issueCount ? "warn" : "ok"} value={formatCompactNumber(issueCount)} />
      </div>
      <div className="service-dependencies-sections">
        {sections.map((item) => <ServiceDependencySection key={item.id} onSelect={setSelectedRow} section={item} />)}
      </div>
      {selectedRow ? <Modal className="service-dependency-detail-modal-panel" onClose={() => setSelectedRow(null)} title="Dependency Row Detail"><ServiceDependencyDetail row={selectedRow} /></Modal> : null}
    </div>
  );
}

function section(id: string, title: string, description: string, empty: string, rows: Record<string, unknown>[]): ServiceDependencySectionPayload {
  return { description, empty, id, rows: rows.map((row) => dependencyDisplayRow(row, id)), title };
}

function ServiceDependencySection({ onSelect, section: item }: { onSelect: (row: ServiceDependencyDisplayRow) => void; section: ServiceDependencySectionPayload }) {
  const issueCount = item.rows.filter((row) => ["error", "warn"].includes(workStatusClass(row.status))).length;
  const status = issueCount ? "warning" : item.rows.length ? "ok" : "not reported";
  return (
    <section className={`service-dependencies-section ${workStatusClass(status)}`}>
      <div className="service-dependencies-section-header">
        <div><h3>{item.title}</h3><p>{item.description}</p></div>
        <div className="service-dependencies-section-badges"><span className={`service-work-status ${workStatusClass(status)}`}>{displayName(status)}</span><span>{item.rows.length} row{item.rows.length === 1 ? "" : "s"}</span></div>
      </div>
      <div className="service-dependency-row-list">
        {item.rows.length ? item.rows.map((row, index) => (
          <button className={`service-dependency-row ${workStatusClass(row.status)}`} key={`${item.id}-${row.name}-${index}`} onClick={() => onSelect(row)} type="button">
            <div><strong title={row.name}>{row.name}</strong><span>{displayName(row.kind)}</span></div>
            <span className={`service-work-status ${workStatusClass(row.status)}`}>{displayName(row.status)}</span>
            <span title={row.metric}>{row.metric}</span><span title={row.last}>{row.last}</span><p title={row.detail}>{row.detail}</p>
          </button>
        )) : <div className="service-dependency-empty">{item.empty}</div>}
      </div>
    </section>
  );
}

function ServiceDependencyDetail({ row }: { row: ServiceDependencyDisplayRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="service-dependency-detail">
      <div className={`service-dependency-detail-heading ${statusClass}`}><div><span>{displayName(row.kind)}</span><strong>{row.name}</strong></div><span className={`service-work-status ${statusClass}`}>{displayName(row.status)}</span></div>
      <dl className="service-log-detail-grid">
        <div><dt>Status</dt><dd>{displayName(row.status)}</dd></div><div><dt>Metric</dt><dd>{row.metric}</dd></div><div><dt>Last</dt><dd>{row.last}</dd></div><div className="wide"><dt>Detail</dt><dd>{row.detail}</dd></div>
      </dl>
      <DebugObjectBlock title="Raw Dependency Payload" value={row.raw} />
    </div>
  );
}

function DependencySummaryItem({ label, tone = "", value }: { label: string; tone?: string; value: string }) {
  return <div className={tone ? `service-dependencies-summary-item ${tone}` : "service-dependencies-summary-item"}><span>{label}</span><strong title={value}>{value || "-"}</strong></div>;
}

function dependencyDisplayRow(row: Record<string, unknown>, fallbackKind: string): ServiceDependencyDisplayRow {
  const status = firstString(row, ["status", "state", "result", "level"]) || (dependencyModalRowHasIssue(row) ? "warning" : "ok");
  const timestamp = firstTimestamp(row);
  return {
    detail: humanizeWorkDetail(firstString(row, ["message", "detail", "details", "description", "notes", "last", "latest"]) || compactWorkDetail(row)),
    kind: firstString(row, ["kind", "type", "category", "role"]) || fallbackKind,
    last: timestamp.label,
    metric: firstString(row, ["wall_seconds", "seconds", "depth", "active", "pending", "progress", "rows", "row_count", "count"]) || "-",
    name: firstString(row, ["name", "task", "work", "item", "source", "sink", "table", "database", "label", "area", "queue_worker"]) || fallbackKind,
    raw: row,
    status,
  };
}

function dependencyModalRowHasIssue(row: Record<string, unknown>) {
  return ["status", "state", "result", "level"].some((key) => /failed|error|warn|degraded|blocked|unreachable/.test(normalizedStatus(String(row[key] || ""))));
}

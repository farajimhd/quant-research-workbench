import { AlertTriangle, CheckCircle2, Loader2, RadioTower, RefreshCcw, ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "../api/client";
import { Button } from "../app/components/Button";
import { DataTable } from "../app/components/DataTable";
import { MetricStrip, type MetricItem } from "../app/components/MetricStrip";
import { displayName, formatCell, formatCompactNumber } from "../app/format";

export type ServicePageMode = "dashboard" | ServiceId;
export type ServiceId = "ibkr" | "news" | "qmd" | "reference" | "sec" | "text-embed";

type ServiceRegistry = {
  base_url: string;
  description: string;
  id: ServiceId;
  kind: string;
  label: string;
};

type ServiceStatusPayload = {
  checked_at_utc: string;
  current_operation: Record<string, unknown>;
  errors: Record<string, unknown>;
  header: Record<string, unknown>;
  health: Record<string, unknown>;
  metrics: Record<string, unknown>;
  online: boolean;
  recent: unknown;
  registry: ServiceRegistry;
  snapshot: Record<string, unknown>;
  status: string;
};

type ServicesStatusPayload = {
  checked_at_utc: string;
  services: ServiceStatusPayload[];
};

const SERVICE_IDS: ServiceId[] = ["qmd", "news", "sec", "text-embed", "reference", "ibkr"];

export function ServicesPage({ mode, onNavigate }: { mode: ServicePageMode; onNavigate: (mode: ServicePageMode) => void }) {
  const [payload, setPayload] = useState<ServicesStatusPayload | null>(null);
  const [selectedPayload, setSelectedPayload] = useState<ServiceStatusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const serviceId = mode === "dashboard" ? null : mode;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setError("");
        const next = await api<ServicesStatusPayload>("/api/services/status");
        if (cancelled) return;
        setPayload(next);
        setLoading(false);
      } catch (exc) {
        if (cancelled) return;
        setError(exc instanceof Error ? exc.message : String(exc));
        setLoading(false);
      }
    }
    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!serviceId) {
      setSelectedPayload(null);
      return;
    }
    let cancelled = false;
    async function loadDetail() {
      try {
        const next = await api<ServiceStatusPayload>(`/api/services/${serviceId}/status`);
        if (!cancelled) setSelectedPayload(next);
      } catch (exc) {
        if (!cancelled) {
          const fallback = payload?.services.find((service) => service.registry.id === serviceId) ?? null;
          setSelectedPayload(fallback ? { ...fallback, errors: { ...fallback.errors, detail: exc instanceof Error ? exc.message : String(exc) } } : null);
        }
      }
    }
    void loadDetail();
    const timer = window.setInterval(loadDetail, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [payload, serviceId]);

  const services = useMemo(() => sortServices(payload?.services ?? []), [payload]);
  const selected = serviceId ? selectedPayload ?? services.find((service) => service.registry.id === serviceId) ?? null : null;

  return (
    <div className="services-page">
      <div className="services-header">
        <div>
          <span className="page-kicker">Services</span>
          <h1>{selected ? selected.registry.label : "Service Dashboard"}</h1>
          <p>{selected ? selected.registry.description : "Live status, current focus, coverage, and processing state across the running gateway services."}</p>
        </div>
        <div className="services-header-actions">
          <span className="services-refresh-note">Updated {payload?.checked_at_utc ? formatTime(payload.checked_at_utc) : "-"}</span>
          <Button onClick={() => window.location.reload()} variant="secondary"><RefreshCcw size={15} /> Refresh</Button>
        </div>
      </div>
      {error ? <div className="services-alert"><ShieldAlert size={16} />{error}</div> : null}
      {loading && !payload ? <div className="services-loading"><Loader2 size={18} /> Loading service status.</div> : null}
      {selected ? <ServiceDetail service={selected} /> : <ServicesDashboard services={services} onNavigate={onNavigate} />}
    </div>
  );
}

function ServicesDashboard({ onNavigate, services }: { onNavigate: (mode: ServicePageMode) => void; services: ServiceStatusPayload[] }) {
  const counts = countStatuses(services);
  const metrics: MetricItem[] = [
    { label: "Services", value: services.length, kind: "number" },
    { label: "Online", value: counts.online, kind: "number" },
    { label: "Active", value: counts.active, kind: "number" },
    { label: "Degraded", value: counts.degraded, kind: "number" },
    { label: "Offline", value: counts.offline, kind: "number" },
  ];
  return (
    <>
      <MetricStrip items={metrics} />
      <section className="services-card-grid">
        {services.map((service) => (
          <button className="service-card" key={service.registry.id} onClick={() => onNavigate(service.registry.id)} type="button">
            <div className="service-card-topline">
              <ServiceIcon service={service} />
              <ServiceStatusBadge status={service.status} online={service.online} />
            </div>
            <h2>{service.registry.label}</h2>
            <p>{currentMessage(service) || service.registry.description}</p>
            <div className="service-card-meta">
              <span>{displayName(service.registry.kind)}</span>
              <span>{phaseText(service)}</span>
              <span>{coverageText(service)}</span>
            </div>
          </button>
        ))}
      </section>
      <Panel title="Fleet Focus">
        <DataTable rows={services.map(serviceSummaryRow)} columns={["service", "status", "phase", "message", "coverage", "runtime", "endpoint"]} empty="No services configured." />
      </Panel>
    </>
  );
}

function ServiceDetail({ service }: { service: ServiceStatusPayload }) {
  const snapshot = service.snapshot ?? {};
  const metrics = service.metrics ?? {};
  const runtimeRows = objectRows(snapshot.runtime, metrics);
  const dailyRows = objectRows(snapshot.daily_summary);
  const coverageRows = objectRows(snapshot.coverage);
  const configRows = objectRows(snapshot.configuration);
  const dependencyRows = arrayRows(snapshot.dependencies);
  const sourceRows = arrayRows(snapshot.sources_sinks);
  const taskRows = arrayRows(snapshot.tasks);
  const progressRows = arrayRows(snapshot.task_table_progress);
  const queueRows = arrayRows(snapshot.queues);
  const tableRows = arrayRows(snapshot.configured_tables);
  const errorRows = objectRows(snapshot.error_state, service.errors);
  const recentRows = recentRowsFromPayload(service.recent);
  const detailMetrics: MetricItem[] = [
    { label: "Status", value: service.online ? service.status : "OFFLINE", kind: "status" },
    { label: "Runtime Rows", value: runtimeRows.length, kind: "number" },
    { label: "Task Rows", value: taskRows.length + progressRows.length, kind: "number" },
    { label: "Recent Rows", value: recentRows.length, kind: "number" },
    { label: "Endpoint", value: service.registry.base_url },
  ];
  return (
    <>
      <MetricStrip items={detailMetrics} />
      <section className="service-focus-grid">
        <Panel title="Current Focus">
          <div className="service-focus">
            <ServiceStatusBadge status={service.status} online={service.online} />
            <div>
              <strong>{phaseText(service)}</strong>
              <p>{currentMessage(service) || "No current operation message reported."}</p>
            </div>
          </div>
        </Panel>
        <Panel title="Coverage">
          <KeyValueList rows={coverageRows.length ? coverageRows : [{ key: "status", value: "not reported" }]} />
        </Panel>
      </section>
      <section className="service-two-column">
        <Panel title="Runtime Counters"><DataTable rows={runtimeRows} columns={["key", "value"]} empty="No runtime counters reported." /></Panel>
        <Panel title="Daily Summary"><DataTable rows={dailyRows} columns={["key", "value"]} empty="No daily summary reported." /></Panel>
      </section>
      <Panel title="Tasks And Table Progress">
        <DataTable rows={[...taskRows, ...progressRows]} empty="No tasks reported." />
      </Panel>
      <section className="service-two-column">
        <Panel title="Dependencies"><DataTable rows={dependencyRows} empty="No dependencies reported." /></Panel>
        <Panel title="Queues"><DataTable rows={queueRows} empty="No queues reported." /></Panel>
      </section>
      <section className="service-two-column">
        <Panel title="Sources And Sinks"><DataTable rows={sourceRows} empty="No source coverage reported." /></Panel>
        <Panel title="Configured Tables"><DataTable rows={tableRows} empty="No configured tables reported." /></Panel>
      </section>
      <Panel title="Errors And Warnings">
        <DataTable rows={errorRows} columns={["key", "value"]} empty="No errors or warnings reported." />
      </Panel>
      <Panel title="Recent Items">
        <DataTable rows={recentRows} empty="No recent items reported." />
      </Panel>
      <Panel title="Configuration">
        <DataTable rows={configRows} columns={["key", "value"]} empty="No public configuration reported." />
      </Panel>
    </>
  );
}

function Panel({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="service-panel">
      <div className="service-panel-header">
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function KeyValueList({ rows }: { rows: Array<{ key: string; value: unknown }> }) {
  return (
    <dl className="service-key-values">
      {rows.slice(0, 8).map((row) => (
        <div key={row.key}>
          <dt>{displayName(row.key)}</dt>
          <dd>{formatValue(row.key, row.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ServiceIcon({ service }: { service: ServiceStatusPayload }) {
  const Icon = service.online ? (String(service.status).toLowerCase().includes("degraded") ? AlertTriangle : CheckCircle2) : RadioTower;
  return <Icon className="service-card-icon" size={20} />;
}

function ServiceStatusBadge({ online, status }: { online: boolean; status: string }) {
  const normalized = online ? statusClass(status) : "offline";
  return <span className={`service-status-badge ${normalized}`}>{online ? statusLabel(status) : "Offline"}</span>;
}

function sortServices(services: ServiceStatusPayload[]) {
  return [...services].sort((left, right) => SERVICE_IDS.indexOf(left.registry.id) - SERVICE_IDS.indexOf(right.registry.id));
}

function countStatuses(services: ServiceStatusPayload[]) {
  return services.reduce(
    (counts, service) => {
      const status = statusClass(service.status);
      if (!service.online) counts.offline += 1;
      else counts.online += 1;
      if (status === "running" || status === "catching-up" || status === "preflight") counts.active += 1;
      if (status === "degraded" || status === "failed") counts.degraded += 1;
      return counts;
    },
    { active: 0, degraded: 0, offline: 0, online: 0 },
  );
}

function serviceSummaryRow(service: ServiceStatusPayload) {
  return {
    service: service.registry.label,
    status: service.online ? statusLabel(service.status) : "Offline",
    phase: phaseText(service),
    message: currentMessage(service) || "-",
    coverage: coverageText(service),
    runtime: runtimeText(service),
    endpoint: service.registry.base_url,
  };
}

function phaseText(service: ServiceStatusPayload) {
  return String(service.current_operation?.phase || service.current_operation?.status || service.header?.market_status || "-");
}

function currentMessage(service: ServiceStatusPayload) {
  return String(service.current_operation?.message || service.current_operation?.next_action || service.errors?.snapshot || "");
}

function coverageText(service: ServiceStatusPayload) {
  const coverage = service.snapshot?.coverage;
  if (!coverage || typeof coverage !== "object") return "-";
  const record = coverage as Record<string, unknown>;
  return String(record.message || record.status || record.active_window_utc || "-");
}

function runtimeText(service: ServiceStatusPayload) {
  const runtime = service.snapshot?.runtime;
  if (!runtime || typeof runtime !== "object") return "-";
  const record = runtime as Record<string, unknown>;
  const keys = ["poll_runs", "processed_rows", "written_rows", "feed_items", "ingest_events", "embedding_rows_written", "cycles"];
  const found = keys.find((key) => record[key] !== undefined && record[key] !== null && record[key] !== "");
  return found ? `${displayName(found)} ${formatCompactNumber(record[found])}` : "-";
}

function objectRows(...values: unknown[]) {
  const rows: Array<{ key: string; value: unknown }> = [];
  for (const value of values) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (item === undefined || item === null || item === "") continue;
      if (typeof item === "object") rows.push({ key, value: compactJson(item) });
      else rows.push({ key, value: item });
    }
  }
  return rows;
}

function arrayRows(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)).map(normalizeRow);
}

function recentRowsFromPayload(value: unknown) {
  if (Array.isArray(value)) return value.filter(isRecord).map(normalizeRow);
  if (!value || typeof value !== "object") return [];
  const record = value as Record<string, unknown>;
  for (const key of ["rows", "items", "recent", "events", "filings", "news", "snapshots"]) {
    const rows = record[key];
    if (Array.isArray(rows)) return rows.filter(isRecord).map(normalizeRow);
  }
  return objectRows(record);
}

function normalizeRow(row: Record<string, unknown>) {
  const normalized: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(row)) {
    normalized[key] = typeof value === "object" && value !== null ? compactJson(value) : value;
  }
  return normalized;
}

function compactJson(value: unknown) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function formatValue(key: string, value: unknown) {
  if (typeof value === "number") return formatCell(key, value);
  if (typeof value === "string") return value || "-";
  return compactJson(value);
}

function formatTime(value: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(parsed));
}

function statusClass(status: string) {
  const text = String(status || "").toLowerCase().replaceAll("_", "-");
  if (text.includes("fail") || text.includes("blocked")) return "failed";
  if (text.includes("degraded") || text.includes("warning")) return "degraded";
  if (text.includes("catch") || text.includes("gap") || text.includes("repair")) return "catching-up";
  if (text.includes("preflight") || text.includes("starting")) return "preflight";
  if (text.includes("idle") || text.includes("waiting")) return "idle";
  if (text.includes("running") || text.includes("ok") || text.includes("healthy") || text.includes("online")) return "running";
  return "unknown";
}

function statusLabel(status: string) {
  return status ? String(status).replaceAll("_", " ").replaceAll("-", " ").toUpperCase() : "UNKNOWN";
}

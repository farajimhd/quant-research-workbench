import { Activity, AlertTriangle, CalendarDays, CheckCircle2, Clock3, Loader2, MapPin, RadioTower, RefreshCcw, Settings2, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "../api/client";
import { Button } from "../app/components/Button";
import { DataTable } from "../app/components/DataTable";
import { Modal } from "../app/components/Modal";
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
  logs?: ServiceLogPayload;
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

type ServiceLogPayload = {
  error?: string;
  path?: string;
  rows?: ServiceRuntimeLogRow[];
};

type ServiceRuntimeLogRow = {
  detail?: string;
  event?: string;
  level?: string;
  line?: number;
  source?: string;
  title?: string;
  ts_utc?: string;
};

const SERVICE_IDS: ServiceId[] = ["qmd", "news", "sec", "text-embed", "reference", "ibkr"];

export function ServicesPage({ mode, onNavigate }: { mode: ServicePageMode; onNavigate: (mode: ServicePageMode) => void }) {
  const [payload, setPayload] = useState<ServicesStatusPayload | null>(null);
  const [selectedPayload, setSelectedPayload] = useState<ServiceStatusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => new Date());
  const serviceId = mode === "dashboard" ? null : mode;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

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
      <section className="services-header">
        <div>
          <span className="page-kicker">Services</span>
          <h1>{selected ? selected.registry.label : "Service Dashboard"}</h1>
          <p>{selected ? selected.registry.description : "Live status, current focus, coverage, and processing state across the running gateway services."}</p>
        </div>
        <div className="services-header-actions">
          <span className="services-refresh-note">Updated {payload?.checked_at_utc ? formatTime(payload.checked_at_utc) : "-"}</span>
          <Button onClick={() => window.location.reload()} variant="secondary"><RefreshCcw size={15} /> Refresh</Button>
        </div>
        <ServicesTopSummary now={now} services={services} />
      </section>
      {loading && !payload ? <div className="services-loading"><Loader2 size={18} /> Loading service status.</div> : null}
      {selected ? <ServiceDetail pageError={error} service={selected} /> : <ServicesDashboard services={services} onNavigate={onNavigate} />}
    </div>
  );
}

function ServicesTopSummary({ now, services }: { now: Date; services: ServiceStatusPayload[] }) {
  const counts = countStatuses(services);
  const market = fleetMarketStatus(services);
  const tiles = [
    { label: "ET", value: formatZoneTime(now, "America/New_York"), sub: formatZoneDate(now, "America/New_York"), icon: Clock3, className: "market-time" },
    { label: "Vancouver", value: formatZoneTime(now, "America/Vancouver"), sub: formatZoneDate(now, "America/Vancouver"), icon: MapPin },
    { label: "UTC", value: formatZoneTime(now, "UTC"), sub: formatZoneDate(now, "UTC"), icon: CalendarDays },
    { label: "Market", value: market.status, sub: market.detail, icon: Activity, className: marketTileClass(market.status, market.detail) },
    { label: "Fleet", value: `${counts.online}/${services.length || 0} online`, sub: `${counts.active} active, ${counts.degraded} degraded, ${counts.offline} not started`, icon: RadioTower },
  ];
  return (
    <div className="services-top-summary" aria-label="Service fleet summary">
      {tiles.map((tile) => {
        const Icon = tile.icon;
        return (
          <div className={`services-top-tile ${tile.className ?? ""}`} key={tile.label}>
            <Icon size={16} />
            <span>{tile.label}</span>
            <strong>{tile.value}</strong>
            <small>{tile.sub || "-"}</small>
          </div>
        );
      })}
    </div>
  );
}

function ServicesDashboard({ onNavigate, services }: { onNavigate: (mode: ServicePageMode) => void; services: ServiceStatusPayload[] }) {
  return (
    <>
      <section className="services-card-grid">
        {services.map((service) => (
          <button className={`service-card ${statusInfo(service).className}`} key={service.registry.id} onClick={() => onNavigate(service.registry.id)} type="button">
            <div className="service-card-topline">
              <div className="service-card-title-lockup">
                <ServiceIcon service={service} />
                <span>{displayName(service.registry.kind)}</span>
              </div>
              <ServiceStatusBadge status={service.status} online={service.online} />
            </div>
            <h2>{service.registry.label}</h2>
            <p className="service-card-message">{cardMessage(service)}</p>
            <div className="service-card-facts">
              <ServiceFact label="Endpoint" value={service.registry.base_url} />
              <ServiceFact label="Phase" value={phaseText(service)} />
              <ServiceFact label="Runtime" value={runtimeText(service)} />
              <ServiceFact label="Coverage" value={coverageText(service)} />
            </div>
          </button>
        ))}
      </section>
    </>
  );
}

function ServiceFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong title={value}>{value || "-"}</strong>
    </div>
  );
}

function ServiceDetail({ pageError, service }: { pageError: string; service: ServiceStatusPayload }) {
  const [configOpen, setConfigOpen] = useState(false);
  const snapshot = service.snapshot ?? {};
  const metrics = service.metrics ?? {};
  const runtimeRows = objectRows(snapshot.runtime, metrics);
  const dailyRows = objectRows(snapshot.daily_summary);
  const coverageRows = objectRows(snapshot.coverage);
  const dependencyRows = arrayRows(snapshot.dependencies);
  const sourceRows = arrayRows(snapshot.sources_sinks);
  const taskRows = arrayRows(snapshot.tasks);
  const progressRows = arrayRows(snapshot.task_table_progress);
  const queueRows = arrayRows(snapshot.queues);
  const tableRows = arrayRows(snapshot.configured_tables);
  const recentRows = recentRowsFromPayload(service.recent);
  const focusStatus = statusInfo(service);
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
              <span className="service-focus-runtime">{runtimeText(service)}</span>
              <button className="service-focus-config-button" onClick={() => setConfigOpen(true)} type="button">
                <Settings2 size={14} />
                Configuration
              </button>
            </div>
            <p className="service-focus-message">{currentMessage(service) || "No current operation message reported."}</p>
          </div>
        </Panel>
      </section>
      {configOpen ? (
        <Modal className="service-config-modal-panel" onClose={() => setConfigOpen(false)} title={`${service.registry.label} Run Configuration`}>
          <ServiceConfigurationPanel service={service} />
        </Modal>
      ) : null}
      <ServiceErrorLogPanel pageError={pageError} service={service} />
      <Panel title="Coverage">
        <KeyValueList rows={coverageRows.length ? coverageRows : [{ key: "status", value: "not reported" }]} />
      </Panel>
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
      <Panel title="Recent Items">
        <DataTable rows={recentRows} empty="No recent items reported." />
      </Panel>
    </>
  );
}

function ServiceErrorLogPanel({ pageError, service }: { pageError: string; service: ServiceStatusPayload }) {
  const items = collectErrorLogItems(pageError, service);
  const activeItems = items.filter((item) => item.status === "active" || item.status === "retrying");
  const logPath = service.logs?.path || "";
  const logError = service.logs?.error || "";
  return (
    <Panel title="Errors And Logs">
      <div className={`service-log-panel ${activeItems.length ? "has-active" : ""}`}>
        <div className="service-log-summary">
          <ServiceStatusBadge online={service.online} status={activeItems.length ? "degraded" : "running"} />
          <div>
            <strong>{items.length ? `${items.length} log row${items.length === 1 ? "" : "s"} loaded` : "No service log rows reported"}</strong>
            <p>
              {logPath ? `Source: ${logPath}` : "No saved runtime log path was reported by this service."}
              {logError ? ` (${logError})` : ""}
            </p>
          </div>
        </div>
        <div className="service-log-table-wrap">
          <table className="service-log-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Status</th>
                <th>Source</th>
                <th>Event</th>
                <th>Message</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {(items.length ? items : [{ detail: "No errors or warnings reported.", key: "service", status: "clear" as const, title: "Clear" }]).map((item, index) => (
                <tr className={`service-log-row ${item.status}`} key={`${item.key}-${index}`}>
                  <td className="service-log-time" title={item.time || item.meta || ""}>{item.time || "-"}</td>
                  <td><span className={`service-log-status ${item.status}`}>{displayName(item.status)}</span></td>
                  <td title={item.source || item.meta || ""}>{item.source || "-"}</td>
                  <td title={displayName(item.event || item.key)}>{displayName(item.event || item.key)}</td>
                  <td title={item.title}>{item.title}</td>
                  <td title={item.detail}>{item.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Panel>
  );
}

function ServiceConfigurationPanel({ service }: { service: ServiceStatusPayload }) {
  const groups = configurationGroups(service);
  const totalSettings = groups.reduce((total, group) => total + group.rows.length, 0);
  const findValue = (patterns: RegExp[]) => {
    for (const group of groups) {
      for (const row of group.rows) {
        if (patterns.some((pattern) => pattern.test(row.key.toLowerCase()))) return formatValue(row.key, row.value);
      }
    }
    return "-";
  };
  return (
    <div className="service-config-panel">
      <div className="service-config-summary">
        <ConfigSummaryItem label="Service" value={service.registry.label} />
        <ConfigSummaryItem label="Mode" value={findValue([/mode/, /profile/, /execute/, /daemon/])} />
        <ConfigSummaryItem label="Database" value={findValue([/database/, /clickhouse/])} />
        <ConfigSummaryItem label="Settings" value={String(totalSettings)} />
      </div>
      <div className="service-config-sections">
        {groups.map((group) => (
          <ConfigGroupView group={group} key={group.title} />
        ))}
      </div>
    </div>
  );
}

function ConfigSummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong title={value}>{value || "-"}</strong>
    </div>
  );
}

function ConfigGroupView({ group }: { group: ConfigGroup }) {
  return (
    <section className="service-config-group">
      <div className="service-config-group-header">
        <h3>{group.title}</h3>
        <span>{group.rows.length} setting{group.rows.length === 1 ? "" : "s"}</span>
      </div>
      <div className="service-config-items">
        {group.rows.map((item) => (
          <ConfigItemView item={item} key={item.key} />
        ))}
      </div>
    </section>
  );
}

function ConfigItemView({ item }: { item: ConfigItem }) {
  const value = formatValue(item.key, item.value);
  const valueType = typeof item.value === "boolean" ? "boolean" : typeof item.value === "number" ? "number" : value.length > 48 ? "long" : "text";
  return (
    <div className={`service-config-item ${valueType}`}>
      <span>{displayName(item.key)}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function Panel({ children, className = "", title }: { children: ReactNode; className?: string; title: string }) {
  return (
    <section className={`service-panel ${className}`}>
      {title ? (
        <div className="service-panel-header">
          <h2>{title}</h2>
        </div>
      ) : null}
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

type ConfigItem = {
  key: string;
  value: unknown;
};

type ConfigGroup = {
  rows: ConfigItem[];
  title: string;
};

type ServiceLogItem = {
  detail: string;
  event?: string;
  key: string;
  meta?: string;
  occurredAtMs?: number;
  source?: string;
  status: "active" | "clear" | "log" | "resolved" | "retrying";
  time?: string;
  title: string;
};

function collectErrorLogItems(pageError: string, service: ServiceStatusPayload) {
  const items: ServiceLogItem[] = [];
  if (pageError) items.push({ detail: pageError, key: "dashboard_api", status: "active", title: "Dashboard API error" });
  for (const record of runtimeLogRows(service.logs)) items.push(record);
  if (service.logs?.error) items.push({ detail: service.logs.error, key: "runtime_log", status: "retrying", title: "Runtime log read error" });

  for (const [key, value] of Object.entries(service.errors ?? {})) {
    if (isEmptyErrorValue(value)) continue;
    items.push({ detail: formatValue(key, value), key, status: "active", title: "Service endpoint error" });
  }

  const errorState = isRecord(service.snapshot?.error_state) ? service.snapshot.error_state : {};
  for (const record of errorRecordRows(errorState.latest_active_errors, "active")) items.push(record);
  for (const record of errorRecordRows(errorState.latest_resolved_errors, "resolved")) items.push(record);

  const warningState = isRecord(service.snapshot?.warnings_errors) ? service.snapshot.warnings_errors : {};
  for (const [key, value] of Object.entries(warningState)) {
    if (isEmptyErrorValue(value)) continue;
    items.push({ detail: formatValue(key, value), key, status: "active", title: displayName(key) });
  }

  for (const row of errorLikePayloadRows(service.snapshot?.service_specific, "service_specific")) items.push(row);
  for (const row of errorLikePayloadRows(service.metrics, "metrics")) items.push(row);

  for (const row of nonZeroErrorCounters(errorState)) items.push(row);
  const deduped = sortLogItems(dedupeLogItems(items));
  if (!deduped.length) deduped.push({ detail: "No errors or warnings reported.", key: "service", status: "clear", title: "Clear" });
  return deduped;
}

function errorRecordRows(value: unknown, fallbackStatus: "active" | "resolved"): ServiceLogItem[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((record) => {
    const rawStatus = String(record.status || fallbackStatus).toLowerCase();
    const status = rawStatus.includes("retry") ? "retrying" : rawStatus.includes("resolved") ? "resolved" : fallbackStatus;
    const severity = String(record.severity || record.category || "error");
    const title = String(record.message || record.safe_detail || record.error_id || "Service error");
    const rawTime = String(record.last_seen_utc || record.resolved_at_utc || record.created_at_utc || record.ts_utc || "");
    const metaParts = [
      record.phase ? `phase=${record.phase}` : "",
      record.task ? `task=${record.task}` : "",
      record.provider ? `provider=${record.provider}` : "",
      record.table ? `table=${record.table}` : "",
      record.item_id ? `item=${record.item_id}` : "",
      record.last_seen_utc ? `last=${record.last_seen_utc}` : "",
      record.resolved_at_utc ? `resolved=${record.resolved_at_utc}` : "",
    ].filter(Boolean);
    return {
      detail: String(record.safe_detail || record.message || record.error_id || "-"),
      key: severity,
      meta: metaParts.join("  "),
      occurredAtMs: parseLogTime(rawTime),
      source: String(record.provider || record.phase || "service"),
      status,
      time: rawTime ? formatLogTime(rawTime) : "",
      title,
    };
  });
}

function nonZeroErrorCounters(errorState: Record<string, unknown>): ServiceLogItem[] {
  const counterKeys = ["active_critical_count", "active_error_count", "active_warning_count", "retrying_count", "retry_exhausted_count", "manual_action_count"];
  return counterKeys.flatMap((key) => {
    const value = Number(errorState[key] ?? 0);
    if (!Number.isFinite(value) || value <= 0) return [];
    return [{
      detail: `${displayName(key)} = ${formatCompactNumber(value)}`,
      key,
      status: key === "retrying_count" ? "retrying" as const : "active" as const,
      title: "Non-zero error counter without detailed records",
    }];
  });
}

function errorLikePayloadRows(value: unknown, source: string): ServiceLogItem[] {
  if (!isRecord(value)) return [];
  const rows: ServiceLogItem[] = [];
  for (const [key, item] of Object.entries(value)) {
    const normalized = key.toLowerCase();
    if (!/error|warning|failure|fail/.test(normalized) || isEmptyErrorValue(item)) continue;
    if (Array.isArray(item)) {
      for (const entry of item) {
        if (isEmptyErrorValue(entry)) continue;
        if (isRecord(entry)) {
          rows.push(...errorRecordRows([entry], normalized.includes("resolved") ? "resolved" : "active"));
        } else {
          rows.push({ detail: formatValue(key, entry), key, meta: `source=${source}`, source, status: "active", title: displayName(key) });
        }
      }
    } else {
      rows.push({ detail: formatValue(key, item), key, meta: `source=${source}`, source, status: "active", title: displayName(key) });
    }
  }
  return rows;
}

function runtimeLogRows(logs: ServiceLogPayload | undefined): ServiceLogItem[] {
  if (!logs?.rows?.length) return [];
  return logs.rows.map((row) => {
    const status = logLevelToStatus(row.level || row.event || row.title || "");
    const event = row.event || row.level || "log";
    const ts = row.ts_utc ? formatLogTime(row.ts_utc) : "";
    const line = typeof row.line === "number" ? `line ${row.line}` : "";
    const meta = [ts, row.source, line].filter(Boolean).join(" | ");
    return {
      detail: row.detail || "-",
      event,
      key: [event, row.source, row.line].filter(Boolean).join(":"),
      meta,
      occurredAtMs: parseLogTime(row.ts_utc || ""),
      source: row.source || "",
      status,
      time: ts,
      title: row.title || event,
    };
  });
}

function logLevelToStatus(value: string): ServiceLogItem["status"] {
  const text = value.toLowerCase();
  if (/(critical|exception|fail|error|traceback)/.test(text)) return "active";
  if (/(warn|retry|timeout|degraded)/.test(text)) return "retrying";
  if (/(resolved|complete|success|succeeded|ok)/.test(text)) return "resolved";
  return "log";
}

function dedupeLogItems(items: ServiceLogItem[]) {
  const seen = new Set<string>();
  const rows: ServiceLogItem[] = [];
  for (const item of items) {
    const key = `${item.status}|${item.key}|${item.title}|${item.detail}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(item);
  }
  return rows;
}

function sortLogItems(items: ServiceLogItem[]) {
  return [...items].sort((a, b) => {
    const aTime = a.occurredAtMs ?? -1;
    const bTime = b.occurredAtMs ?? -1;
    if (aTime !== bTime) return bTime - aTime;
    return statusPriority(a.status) - statusPriority(b.status);
  });
}

function statusPriority(status: ServiceLogItem["status"]) {
  if (status === "active") return 0;
  if (status === "retrying") return 1;
  if (status === "resolved") return 2;
  if (status === "log") return 3;
  return 4;
}

function isEmptyErrorValue(value: unknown) {
  if (value === undefined || value === null || value === "" || value === false) return true;
  if (typeof value === "number") return value === 0;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return !normalized || normalized === "-" || normalized === "ok" || normalized === "none" || normalized === "false" || normalized === "[]" || normalized.includes("no active errors");
  }
  if (isRecord(value)) return Object.keys(value).length === 0;
  return false;
}

function configurationGroups(service: ServiceStatusPayload): ConfigGroup[] {
  const rawItems = new Map<string, ConfigItem>();
  const add = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    if (!rawItems.has(key)) rawItems.set(key, { key, value });
  };

  add("service", service.registry.label);
  add("kind", service.registry.kind);
  add("endpoint", service.registry.base_url);

  const snapshot = service.snapshot ?? {};
  if (isRecord(snapshot.configuration)) {
    for (const [key, value] of Object.entries(snapshot.configuration)) add(key, value);
  }

  const grouped = new Map<string, ConfigItem[]>();
  for (const item of rawItems.values()) {
    const title = configGroupTitle(item.key);
    grouped.set(title, [...(grouped.get(title) ?? []), item]);
  }

  const order = ["Service", "Run Mode", "Connection", "Schedule And Market", "Database", "Storage", "Other Parameters"];
  return order
    .map((title) => ({ title, rows: grouped.get(title) ?? [] }))
    .filter((group) => group.rows.length > 0);
}

function configGroupTitle(key: string) {
  const normalized = key.toLowerCase();
  if (/^service$|^kind$/.test(normalized)) return "Service";
  if (/mode|execute|daemon|profile|env|policy/.test(normalized)) return "Run Mode";
  if (/endpoint|bind|host|port|url|client|server/.test(normalized)) return "Connection";
  if (/poll|interval|lookback|schedule|market|session|window|cadence|timezone|holiday/.test(normalized)) return "Schedule And Market";
  if (/database|table|clickhouse|schema/.test(normalized)) return "Database";
  if (/root|path|artifact|storage|log|report|folder|directory/.test(normalized)) return "Storage";
  return "Other Parameters";
}

function ServiceIcon({ service }: { service: ServiceStatusPayload }) {
  const info = statusInfo(service);
  const Icon = info.className === "not-started" ? WifiOff : info.className === "degraded" || info.className === "failed" || info.className === "blocked" ? AlertTriangle : CheckCircle2;
  return <Icon className="service-card-icon" size={20} />;
}

function ServiceStatusBadge({ online, status }: { online: boolean; status: string }) {
  const info = statusInfo({ online, status } as ServiceStatusPayload);
  return <span className={`service-status-badge ${info.className}`} title={info.description}>{info.label}</span>;
}

function sortServices(services: ServiceStatusPayload[]) {
  return [...services].sort((left, right) => SERVICE_IDS.indexOf(left.registry.id) - SERVICE_IDS.indexOf(right.registry.id));
}

function countStatuses(services: ServiceStatusPayload[]) {
  return services.reduce(
    (counts, service) => {
      const status = statusInfo(service).className;
      if (!service.online) counts.offline += 1;
      else counts.online += 1;
      if (status === "running" || status === "working" || status === "catching-up" || status === "preflight" || status === "starting") counts.active += 1;
      if (status === "degraded" || status === "failed" || status === "blocked") counts.degraded += 1;
      return counts;
    },
    { active: 0, degraded: 0, offline: 0, online: 0 },
  );
}

function phaseText(service: ServiceStatusPayload) {
  return String(service.current_operation?.phase || service.current_operation?.status || service.header?.market_status || "-");
}

function currentMessage(service: ServiceStatusPayload) {
  return String(service.current_operation?.message || service.current_operation?.next_action || service.errors?.snapshot || "");
}

function cardMessage(service: ServiceStatusPayload) {
  if (!service.online) return offlineReason(service) || "Service endpoint is not responding.";
  return currentMessage(service) || service.registry.description;
}

function offlineReason(service: ServiceStatusPayload) {
  return String(service.errors?.snapshot || service.errors?.health || service.errors?.metrics || "");
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

function formatLogTime(value: string) {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(parsed));
}

function parseLogTime(value: string) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function formatZoneTime(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone }).format(value);
}

function formatZoneDate(value: Date, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "2-digit", year: "numeric", timeZone }).format(value);
}

function fleetMarketStatus(services: ServiceStatusPayload[]) {
  for (const service of services) {
    const candidates = [
      service.header?.market_status,
      service.header?.market_session,
      service.metrics?.market_status,
      service.metrics?.current_market_session,
      service.snapshot?.runtime && isRecord(service.snapshot.runtime) ? service.snapshot.runtime.market_status : "",
      service.snapshot?.runtime && isRecord(service.snapshot.runtime) ? service.snapshot.runtime.current_market_session : "",
    ];
    const value = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
    if (value) {
      const source = String(service.metrics?.market_status_source || service.header?.market_status_source || service.registry.label);
      return { status: String(value), detail: marketSourceLabel(source || service.registry.label) };
    }
  }
  return { status: "not reported", detail: "No gateway has reported market state yet" };
}

function marketSourceLabel(source: string) {
  const normalized = source.toLowerCase();
  if (normalized === "massive_market_calendar") return "Massive status + calendar";
  if (normalized === "massive_status") return "Massive status";
  if (normalized === "local_clock") return "Local clock";
  if (normalized === "disabled") return "Market status disabled";
  return displayName(source);
}

function marketTileClass(status: string, detail: string) {
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

type StatusInfo = {
  className: string;
  description: string;
  label: string;
};

function statusInfo(service: Pick<ServiceStatusPayload, "online" | "status">): StatusInfo {
  if (!service.online) {
    return { className: "not-started", description: "The service API endpoint is not reachable or timed out.", label: "NOT STARTED" };
  }
  const text = String(service.status || "").toLowerCase().replaceAll("_", "-");
  if (text.includes("start")) return { className: "starting", description: "The service is starting and has not completed initialization.", label: "STARTING" };
  if (text.includes("preflight")) return { className: "preflight", description: "The service is checking dependencies before operational work.", label: "PREFLIGHT" };
  if (text.includes("catch") || text.includes("gap") || text.includes("repair")) return { className: "catching-up", description: "The service is filling coverage gaps or repairing recent data.", label: "CATCHING UP" };
  if (text.includes("work") || text.includes("queue") || text.includes("processing")) return { className: "working", description: "The service is actively processing background work.", label: "WORKING" };
  if (text.includes("degraded") || text.includes("warn")) return { className: "degraded", description: "The service is reachable but has warnings or reduced capability.", label: "DEGRADED" };
  if (text.includes("block")) return { className: "blocked", description: "The service is blocked by policy, dependency, or required manual action.", label: "BLOCKED" };
  if (text.includes("stop")) return { className: "stopping", description: "The service is shutting down.", label: "STOPPING" };
  if (text.includes("fail") || text.includes("error") || text.includes("critical")) return { className: "failed", description: "The service reports an active critical failure.", label: "FAILED" };
  if (text.includes("idle") || text.includes("waiting")) return { className: "idle", description: "The service is healthy and waiting for the next scheduled task.", label: "IDLE" };
  if (text.includes("run") || text.includes("ok") || text.includes("healthy") || text.includes("online")) return { className: "running", description: "The service is healthy and running.", label: "RUNNING" };
  return { className: "unknown", description: "The service is reachable but did not report a standard status.", label: service.status ? String(service.status).toUpperCase() : "UNKNOWN" };
}

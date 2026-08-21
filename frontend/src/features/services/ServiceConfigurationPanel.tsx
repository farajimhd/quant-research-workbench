import { displayName, formatCell } from "../../app/format";
import type { ServiceStatusPayload } from "./contracts";

type ConfigItem = {
  key: string;
  value: unknown;
};

type ConfigGroup = {
  rows: ConfigItem[];
  title: string;
};

export function ServiceConfigurationPanel({ service }: { service: ServiceStatusPayload }) {
  const groups = configurationGroups(service);
  const totalSettings = groups.reduce((total, group) => total + group.rows.length, 0);
  const findValue = (patterns: RegExp[]) => {
    for (const group of groups) {
      for (const row of group.rows) {
        if (patterns.some((pattern) => pattern.test(row.key.toLowerCase()))) return formatConfigValue(row.key, row.value);
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
        {groups.map((group) => <ConfigGroupView group={group} key={group.title} />)}
      </div>
    </div>
  );
}

function ConfigSummaryItem({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong title={value}>{value || "-"}</strong></div>;
}

function ConfigGroupView({ group }: { group: ConfigGroup }) {
  return (
    <section className="service-config-group">
      <div className="service-config-group-header">
        <h3>{group.title}</h3>
        <span>{group.rows.length} setting{group.rows.length === 1 ? "" : "s"}</span>
      </div>
      <div className="service-config-items">
        {group.rows.map((item) => <ConfigItemView item={item} key={item.key} />)}
      </div>
    </section>
  );
}

function ConfigItemView({ item }: { item: ConfigItem }) {
  const value = formatConfigValue(item.key, item.value);
  const valueType = typeof item.value === "boolean" ? "boolean" : typeof item.value === "number" ? "number" : value.length > 48 ? "long" : "text";
  return <div className={`service-config-item ${valueType}`}><span>{displayName(item.key)}</span><strong title={value}>{value}</strong></div>;
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
  const configuration = service.snapshot?.configuration;
  if (isRecord(configuration)) {
    for (const [key, value] of Object.entries(configuration)) add(key, value);
  }

  const grouped = new Map<string, ConfigItem[]>();
  for (const item of rawItems.values()) {
    const title = configGroupTitle(item.key);
    grouped.set(title, [...(grouped.get(title) ?? []), item]);
  }

  const order = ["Service", "Run Mode", "Connection", "Schedule And Market", "Database", "Storage", "Other Parameters"];
  return order.map((title) => ({ title, rows: grouped.get(title) ?? [] })).filter((group) => group.rows.length > 0);
}

function configGroupTitle(key: string): string {
  const normalized = key.toLowerCase();
  if (/^service$|^kind$/.test(normalized)) return "Service";
  if (/mode|execute|daemon|profile|env|policy/.test(normalized)) return "Run Mode";
  if (/endpoint|bind|host|port|url|client|server/.test(normalized)) return "Connection";
  if (/poll|interval|lookback|schedule|market|session|window|cadence|timezone|holiday/.test(normalized)) return "Schedule And Market";
  if (/database|table|clickhouse|schema/.test(normalized)) return "Database";
  if (/root|path|artifact|storage|log|report|folder|directory/.test(normalized)) return "Storage";
  return "Other Parameters";
}

function formatConfigValue(key: string, value: unknown): string {
  if (typeof value === "number") return formatCell(key, value);
  if (typeof value === "string") return value || "-";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

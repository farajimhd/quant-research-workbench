import { formatBytes, formatDuration, formatNumber } from "../format";

export type MetricItem = {
  label: string;
  value: string | number;
  kind?: "bytes" | "duration" | "number" | "status";
};

export function MetricStrip({ items }: { items: MetricItem[] }) {
  return (
    <div className="metric-strip">
      {items.map((item) => (
        <div className="metric-item" key={item.label}>
          <div className="metric-label">{item.label}</div>
          <div className={item.kind === "status" ? `metric-value status-${String(item.value).toLowerCase()}` : "metric-value"}>
            {formatMetric(item)}
          </div>
        </div>
      ))}
    </div>
  );
}

function formatMetric(item: MetricItem): string {
  if (item.kind === "bytes") return formatBytes(item.value);
  if (item.kind === "duration") return formatDuration(item.value);
  if (item.kind === "number") return formatNumber(item.value);
  return String(item.value);
}


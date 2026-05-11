import { formatBytes, formatCompactNumber, formatDuration, formatNumber } from "../format";

export type MetricItem = {
  label: string;
  value: string | number;
  kind?: "bytes" | "duration" | "number" | "status";
};

export function MetricStrip({ items }: { items: MetricItem[] }) {
  return (
    <div className="metric-strip">
      {items.map((item) => (
        <article className="metric-card" key={item.label}>
          <span className="metric-label">{item.label}</span>
          <strong className={item.kind === "status" ? `metric-value status-${String(item.value).toLowerCase()}` : "metric-value"}>
            {formatMetric(item)}
          </strong>
        </article>
      ))}
    </div>
  );
}

function formatMetric(item: MetricItem): string {
  if (item.kind === "bytes") return formatBytes(item.value);
  if (item.kind === "duration") return formatDuration(item.value);
  if (item.kind === "number") {
    const numeric = Number(item.value ?? 0);
    return Number.isFinite(numeric) && Math.abs(numeric) >= 1_000_000 ? formatCompactNumber(numeric) : formatNumber(numeric);
  }
  return String(item.value);
}

import type { ReactNode } from "react";

export type LiveMetricItem = { icon: ReactNode; label: string; tone: string; value: string };

export function MetricsDock({ metrics }: { metrics: { items: LiveMetricItem[] } }) {
  return (
    <section className="live-metrics-dock" aria-label="Portfolio metrics">
      <div className="live-debug-metric-strip" style={{ gridTemplateColumns: `repeat(${Math.max(metrics.items.length, 1)}, minmax(106px, 1fr))` }}>
        {metrics.items.map((item) => (
          <article className="live-debug-metric-card" data-tone={item.tone} key={item.label}>
            <span className="live-debug-metric-icon">{item.icon}</span>
            <span className="live-debug-metric-label">{item.label}</span>
            <strong>{item.value}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}

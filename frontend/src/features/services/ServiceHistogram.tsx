import { useMemo, useState, type CSSProperties, type ReactNode } from "react";

export type ServiceHistogramSegment = {
  className: string;
  value: number;
};

export function ServiceHistogram<Row, Hover>({
  ariaLabel,
  className = "",
  error,
  getHover,
  getKey,
  getSegments,
  getTotal,
  hoverClassName = "",
  label,
  legend,
  legendClassName = "",
  renderHover,
  rows,
}: {
  ariaLabel: (row: Row) => string;
  className?: string;
  error: string;
  getHover: (row: Row) => Hover;
  getKey: (row: Row) => string;
  getSegments: (row: Row) => ServiceHistogramSegment[];
  getTotal: (row: Row) => number;
  hoverClassName?: string;
  label: string;
  legend: ReactNode;
  legendClassName?: string;
  renderHover: (hover: Hover) => ReactNode;
  rows: Row[];
}) {
  const [hover, setHover] = useState<Hover | null>(null);
  const maxTotal = useMemo(() => Math.max(1, ...rows.map(getTotal)), [getTotal, rows]);
  return (
    <div className={`service-histogram news-live-histogram ${className}`.trim()}>
      <div className="service-histogram-label news-live-histogram-label">
        <span>{label}</span>
        <div className={`service-histogram-legend news-live-histogram-legend ${legendClassName}`.trim()}>{legend}</div>
      </div>
      {hover ? (
        <div className={`service-histogram-hover news-live-histogram-hover ${hoverClassName}`.trim()}>{renderHover(hover)}</div>
      ) : null}
      {error ? <div className="service-histogram-error news-live-histogram-error">{error}</div> : null}
      <div
        className="service-histogram-chart news-live-histogram-chart"
        onMouseLeave={() => setHover(null)}
        style={{ "--histogram-bin-count": rows.length } as CSSProperties}
      >
        {rows.map((row) => {
          const total = getTotal(row);
          return (
            <div
              aria-label={ariaLabel(row)}
              className={total > 0 ? "service-histogram-bin news-live-histogram-bin has-data" : "service-histogram-bin news-live-histogram-bin"}
              key={getKey(row)}
              onBlur={() => setHover(null)}
              onFocus={() => setHover(getHover(row))}
              onMouseEnter={() => setHover(getHover(row))}
              role="img"
              style={{ "--bar-height": `${histogramBarHeight(total, maxTotal)}%` } as CSSProperties}
              tabIndex={total > 0 ? 0 : -1}
            >
              {total > 0 ? (
                <span className="service-histogram-stack news-live-histogram-stack">
                  {getSegments(row).map((segment) => (
                    <span
                      className={`service-histogram-segment news-live-histogram-segment ${segment.className}`}
                      key={segment.className}
                      style={{ height: `${(segment.value / total) * 100}%` }}
                    />
                  ))}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function histogramBarHeight(totalRows: number, maxRows: number) {
  if (totalRows <= 0 || maxRows <= 0) return 0;
  return Math.max(4, (totalRows / maxRows) * 100);
}

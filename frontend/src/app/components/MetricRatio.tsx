type MetricRatioAccent = 1 | 2 | 3 | 4;

export function MetricRatio({
  accent = 1,
  current,
  suffix = "",
  total,
}: {
  accent?: MetricRatioAccent;
  current: number | string;
  suffix?: string;
  total: number | string;
}) {
  return (
    <span className="metric-ratio" data-accent={accent}>
      <span className="metric-ratio-current">{current}</span>
      <span className="metric-ratio-separator">/</span>
      <span className="metric-ratio-total">{total}</span>
      {suffix ? <span className="metric-ratio-suffix">{suffix}</span> : null}
    </span>
  );
}

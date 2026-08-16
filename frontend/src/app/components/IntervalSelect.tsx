type IntervalSelectProps = {
  ariaLabel: string;
  className?: string;
  intervals: string[];
  label?: string;
  onChange: (value: string) => void;
  value: string;
};

const INTERVAL_GROUPS = [
  { label: "Intraday", units: new Set(["ms", "s", "m", "h"]) },
  { label: "Higher timeframe", units: new Set(["d", "w", "mo", "y"]) },
];

export function IntervalSelect({ ariaLabel, className = "", intervals, label = "Interval", onChange, value }: IntervalSelectProps) {
  return <label className={`interval-select ${className}`.trim()}>
    <span>{label}</span>
    <select aria-label={ariaLabel} onChange={(event) => onChange(event.target.value)} value={value || preferredInterval(intervals)}>
      {INTERVAL_GROUPS.map((group) => {
        const options = intervals.filter((interval) => group.units.has(intervalUnit(interval)));
        return options.length ? <optgroup key={group.label} label={group.label}>{options.map((interval) => <option key={interval} value={interval}>{intervalLabel(interval)}</option>)}</optgroup> : null;
      })}
    </select>
  </label>;
}

export function preferredInterval(intervals: string[]) { return intervals.includes("1m") ? "1m" : intervals.includes("1s") ? "1s" : intervals[0] ?? ""; }
export function intervalLabel(value: string) { const match = /^(\d+)(ms|s|m|h|d|w|mo|y)$/.exec(value); if (!match) return readable(value); const count = Number(match[1]); const unit = ({ ms: "millisecond", s: "second", m: "minute", h: "hour", d: "day", w: "week", mo: "month", y: "year" } as Record<string, string>)[match[2]]; return `${count} ${unit}${count === 1 ? "" : "s"}`; }
function intervalUnit(value: string) { return /^(?:\d+)(ms|s|m|h|d|w|mo|y)$/.exec(value)?.[1] ?? ""; }
function readable(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }

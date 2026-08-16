export type IntervalUnit = "milliseconds" | "seconds" | "minutes" | "hours" | "days" | "weeks" | "months";
export type IntervalSpec = { value: number; unit: IntervalUnit };
export type IntervalValue = IntervalSpec | string | null | undefined;

type IntervalSelectProps = {
  ariaLabel: string;
  className?: string;
  intervals?: string[];
  label?: string;
  onChange: (value: IntervalSpec) => void;
  value: IntervalValue;
};

const UNITS: Array<{ label: string; suffix: string; value: IntervalUnit }> = [
  { label: "Milliseconds", suffix: "ms", value: "milliseconds" },
  { label: "Seconds", suffix: "s", value: "seconds" },
  { label: "Minutes", suffix: "m", value: "minutes" },
  { label: "Hours", suffix: "h", value: "hours" },
  { label: "Days", suffix: "d", value: "days" },
  { label: "Weeks", suffix: "w", value: "weeks" },
  { label: "Months", suffix: "mo", value: "months" },
];

export function IntervalSelect({ ariaLabel, className = "", intervals = [], label = "Interval", onChange, value }: IntervalSelectProps) {
  const current = normalizeInterval(value) ?? preferredInterval(intervals);
  const allowedUnits = new Set(intervals.map((interval) => normalizeInterval(interval)?.unit).filter((unit): unit is IntervalUnit => Boolean(unit)));
  const units = allowedUnits.size ? UNITS.filter((unit) => allowedUnits.has(unit.value)) : UNITS;
  return <label className={`interval-select ${className}`.trim()}>
    <span>{label}</span>
    <div className="interval-parts">
      <input aria-label={`${ariaLabel} value`} min="1" onChange={(event) => onChange({ ...current, value: Math.max(1, Math.trunc(Number(event.target.value) || 1)) })} step="1" type="number" value={current.value} />
      <select aria-label={`${ariaLabel} unit`} onChange={(event) => onChange({ ...current, unit: event.target.value as IntervalUnit })} value={current.unit}>
        {units.map((unit) => <option key={unit.value} value={unit.value}>{unit.label}</option>)}
      </select>
      <code aria-label={`${ariaLabel} expression`}>{intervalExpression(current)}</code>
    </div>
  </label>;
}

export function normalizeInterval(value: IntervalValue): IntervalSpec | null {
  if (value && typeof value === "object") {
    const unit = UNITS.find((candidate) => candidate.value === value.unit)?.value;
    const count = Math.trunc(Number(value.value));
    return unit && count > 0 ? { unit, value: count } : null;
  }
  const match = /^(\d+)(ms|s|m|h|d|w|mo)$/.exec(String(value ?? "").trim().toLowerCase());
  if (!match) return null;
  const unit = UNITS.find((candidate) => candidate.suffix === match[2])?.value;
  return unit ? { unit, value: Number(match[1]) } : null;
}

export function preferredInterval(intervals: string[] = []): IntervalSpec {
  return normalizeInterval(intervals.includes("1m") ? "1m" : intervals.includes("1s") ? "1s" : intervals[0]) ?? { unit: "minutes", value: 1 };
}
export function intervalExpression(value: IntervalValue): string {
  const interval = normalizeInterval(value);
  if (!interval) return "";
  return `${interval.value}${UNITS.find((candidate) => candidate.value === interval.unit)?.suffix ?? ""}`;
}
export function intervalLabel(value: IntervalValue) {
  const interval = normalizeInterval(value);
  if (!interval) return readable(String(value ?? ""));
  const singular = interval.unit.endsWith("s") ? interval.unit.slice(0, -1) : interval.unit;
  return `${interval.value} ${interval.value === 1 ? singular : interval.unit}`;
}
function readable(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }

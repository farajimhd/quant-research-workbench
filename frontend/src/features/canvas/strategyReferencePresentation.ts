type Row = Record<string, unknown>;
export type StrategyReferenceSegment = { start: number; end: number; hod?: number; resistance?: number };

/** Recorded strategy selections only; never rebuild the book or HOD in the UI. */
export function strategyReferencePresentation(rows: Row[], ticker: string, asOf: string): StrategyReferenceSegment[] {
  const cutoff = Date.parse(asOf) / 1000;
  const result: StrategyReferenceSegment[] = [];
  const positive = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v > 0 ? v : undefined;
  for (const row of [...rows].sort((a,b) => Date.parse(String(a.event_time))-Date.parse(String(b.event_time)) || Number(a.sequence)-Number(b.sequence))) {
    if (row.ticker !== ticker) continue;
    const plan = (row.chart_plan || row.gate_snapshot) as Row | undefined;
    const reference = plan?.historical_hod_reference as Row | undefined;
    if (!reference || !reference.changed) continue;
    const time = Number(reference.at);
    const published = Date.parse(String(row.event_time))/1000;
    if (!Number.isFinite(time) || !Number.isFinite(published) || !Number.isFinite(cutoff)
        || time > cutoff || published > cutoff || time > published) continue;
    const last = result.at(-1);
    const hod = positive(reference.hod), resistance = positive(reference.resistance_upper);
    if (last && last.hod === hod && last.resistance === resistance) continue;
    if (last) last.end = time;
    result.push({ start: time, end: cutoff, hod, resistance });
  }
  return result.filter(row => row.end > row.start);
}

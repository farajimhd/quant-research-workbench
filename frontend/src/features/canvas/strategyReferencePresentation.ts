type Row = Record<string, unknown>;
export type StrategyReferenceSegment = { start: number; end: number; hod?: number; resistance?: number; zoneLower?: number; resistanceLabel?: "R1" };

export function strategyReferenceLabel(kind: "hod" | "resistance" | "zoneLower", reference: Pick<StrategyReferenceSegment, "resistanceLabel">): string {
  return kind === "hod" ? "HOD" : kind === "zoneLower" ? "Entry zone floor" : reference.resistanceLabel ?? "Entry R";
}

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
    const ladder = reference.contract === "r1-hod-resistance-ladder-v1";
    const resistanceLabel = ladder ? "R1" as const : undefined;
    // The ladder's continuation threshold may advance independently of R1.
    const hod = positive(reference.hod), resistance = positive(ladder ? reference.resistance_upper : reference.resistance_center ?? reference.resistance_upper), zoneLower=positive(reference.zone_lower);
    if (last && last.hod === hod && last.resistance === resistance && last.zoneLower===zoneLower && last.resistanceLabel===resistanceLabel) continue;
    if (last) last.end = time;
    result.push({ start: time, end: cutoff, hod, resistance, ...(zoneLower===undefined?{}:{zoneLower}), ...(resistanceLabel ? {resistanceLabel} : {}) });
  }
  return result.filter(row => row.end > row.start);
}

type Evidence = Record<string, unknown>;

/** Render the recorded entry selection; never infer candidates from targets or chart bars. */
export function entryStructurePresentation(trigger: Evidence, entryTime: number) {
  const current = trigger.current_snapshot as Evidence | undefined;
  const hasCurrent = current && Array.isArray(current.levels);
  const snapshot = hasCurrent ? current : {
    levels: trigger.prior_snapshot_levels,
    session_high: trigger.prior_snapshot_session_high,
    selected_at: trigger.prior_snapshot_selected_at,
  };
  const selectedAt = Date.parse(String(snapshot.selected_at ?? "")) / 1000;
  const highOfDayPrice = Number(snapshot.session_high);
  if (!Number.isFinite(highOfDayPrice) || highOfDayPrice <= 0
      || (Number.isFinite(selectedAt) && selectedAt > entryTime)) {
    return { highOfDayPrice: undefined, resistancePrices: [] as number[] };
  }
  const levels = Array.isArray(snapshot.levels) ? snapshot.levels as Evidence[] : [];
  // The producer has already selected and qualified this set. Descending prices
  // name the nearest selected boundary below HOD R1, then R2 and R3.
  const resistancePrices = [...new Set(levels.map((row) => Number(row.entry_boundary ?? row.price))
    .filter((price) => Number.isFinite(price) && price > 0 && price <= highOfDayPrice))]
    .sort((left, right) => right - left).slice(0, 3);
  return { highOfDayPrice, resistancePrices };
}

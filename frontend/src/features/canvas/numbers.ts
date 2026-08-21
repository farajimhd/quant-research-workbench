export function finiteNumber(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function boundedUnit(value: unknown) {
  return Math.max(0, Math.min(1, finiteNumber(value)));
}

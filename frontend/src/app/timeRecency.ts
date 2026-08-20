export type TimeRecency = "cold" | "hot" | "old";

const HOT_MINUTES = 4 * 60;
const COLD_MINUTES = 24 * 60;

export function timeRecency(timestamp: string, asOfMs: number): TimeRecency {
  const timestampMs = Date.parse(timestamp);
  const ageMinutes = Number.isFinite(timestampMs) && Number.isFinite(asOfMs)
    ? Math.max(0, (asOfMs - timestampMs) / 60_000)
    : Number.POSITIVE_INFINITY;
  return ageMinutes <= HOT_MINUTES ? "hot" : ageMinutes <= COLD_MINUTES ? "cold" : "old";
}

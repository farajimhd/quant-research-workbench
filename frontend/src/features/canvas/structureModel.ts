import type { QmdStructureEvent, QmdStructureLevelCandidate } from "./contracts";

export const QMD_STRUCTURE_TIMEFRAMES = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"] as const;
const QMD_STRUCTURE_HISTORY_LIMIT = 1_000;

export function isQmdStructureLevelCandidate(value: unknown): value is QmdStructureLevelCandidate {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<QmdStructureLevelCandidate>;
  return Number.isFinite(candidate.price)
    && Number(candidate.price) > 0
    && Number.isFinite(candidate.lower)
    && Number.isFinite(candidate.upper)
    && Number.isFinite(candidate.confidence)
    && Number.isFinite(candidate.strength)
    && Number.isFinite(candidate.distance)
    && Number.isFinite(candidate.evidence_score)
    && typeof candidate.footprint_session_date === "string"
    && candidate.footprint_session_date.length > 0
    && Number.isFinite(candidate.footprint_as_of_ms)
    && Number(candidate.footprint_as_of_ms) > 0
    && Array.isArray(candidate.promotions)
    && Array.isArray(candidate.footprint)
    && (candidate.side === 1 || candidate.side === -1);
}

export function qmdStructureSwingLayerId(timeframe: string) {
  return `indicator.qmd_generic_structure.v10.${timeframe}.swings`;
}

export function qmdStructureBreakLayerId(timeframe: string) {
  return `indicator.qmd_generic_structure.v10.${timeframe}.breaks`;
}

export function qmdStructureTimeframeSeconds(timeframe: string) {
  const values: Record<string, number> = {
    "100ms": 0.1,
    "1s": 1,
    "5s": 5,
    "10s": 10,
    "30s": 30,
    "1m": 60,
    "5m": 300,
    "1h": 3_600,
  };
  return values[timeframe] ?? 0;
}

export function retainStructureEventsPerTimeframe(
  events: QmdStructureEvent[],
  predicate: (event: QmdStructureEvent) => boolean,
) {
  const retainedIds = new Set(
    QMD_STRUCTURE_TIMEFRAMES.flatMap((timeframe) =>
      events
        .filter((event) => event.timeframe === timeframe && predicate(event))
        .slice(-QMD_STRUCTURE_HISTORY_LIMIT)
        .map((event) => event.event_id),
    ),
  );
  return events.filter((event) => retainedIds.has(event.event_id));
}

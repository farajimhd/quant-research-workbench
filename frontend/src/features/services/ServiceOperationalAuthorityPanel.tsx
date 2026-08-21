import { displayName, formatCompactNumber } from "../../app/format";
import type { ServiceStatusPayload } from "./contracts";
import { numericMetricOptional, stringMetric } from "./metrics";
import { ServicePanel as Panel } from "./ServicePanel";
import { formatServiceTime } from "./time";
import { isRecord } from "./workPresentation";

export function ServiceOperationalAuthorityPanel({ service }: { service: ServiceStatusPayload }) {
  const operations = isRecord(service.operations) ? service.operations : {};
  const authority = isRecord(operations.authority) ? operations.authority : {};
  const coverage = isRecord(operations.coverage) ? operations.coverage : {};
  const freshness = isRecord(operations.freshness) ? operations.freshness : {};
  const queues = isRecord(operations.queues) ? operations.queues : {};
  const checkpoint = isRecord(operations.checkpoint) ? operations.checkpoint : {};
  const degradation = isRecord(operations.degradation) ? operations.degradation : {};
  const coverageStatus = stringMetric(coverage, ["status", "state"]) || (Object.keys(coverage).length ? "Reported" : "Unknown");
  const lastEvent = stringMetric(freshness, ["last_event_utc", "observed_at_utc"]);
  const queueDepth = numericMetricOptional(queues, ["depth", "pending_rows"]);
  const queueDrops = numericMetricOptional(queues, ["drop_total"]);
  const checkpointIdentity = stringMetric(checkpoint, ["cursor", "checkpoint_id", "watermark", "event_clock"]);
  const degraded = degradation.degraded === true;
  const degradationKnown = degradation.evidence_present === true;
  const evidencePresent = authority.evidence_present === true;

  return (
    <Panel className="service-operational-authority-panel" title="Operational Authority">
      <div className="service-operational-authority-grid">
        <OperationalAuthorityMetric detail={stringMetric(coverage, ["message", "through", "archive_session_date"]) || "No producer coverage evidence"} label="Coverage" tone={coverageStatus.toLowerCase() === "ready" ? "good" : "neutral"} value={displayName(coverageStatus)} />
        <OperationalAuthorityMetric detail={lastEvent || "No event or observation clock"} label="Freshness" value={lastEvent ? formatServiceTime(lastEvent) : "Unknown"} />
        <OperationalAuthorityMetric detail={`${queueDrops === null ? "Unknown" : formatCompactNumber(queueDrops)} drops`} label="Queue" tone={(queueDrops ?? 0) > 0 ? "warn" : "neutral"} value={queueDepth === null ? "Unknown" : formatCompactNumber(queueDepth)} />
        <OperationalAuthorityMetric detail={Object.keys(checkpoint).length ? "Producer-declared checkpoint evidence" : "No checkpoint contract declared"} label="Checkpoint" value={checkpointIdentity || (Object.keys(checkpoint).length ? "Reported" : "Unknown")} />
        <OperationalAuthorityMetric detail={degraded ? "Attention or error evidence is active" : degradationKnown ? "Producer contract reports no active degradation" : "No degradation contract declared"} label="Degradation" tone={degraded ? "warn" : degradationKnown ? "good" : "neutral"} value={degraded ? "Degraded" : degradationKnown ? "Clear" : "Unknown"} />
        <OperationalAuthorityMetric detail={stringMetric(authority, ["source"]) || "Backend composition contract"} label="Authority" tone={evidencePresent ? "good" : "neutral"} value={evidencePresent ? "Declared" : "Unknown"} />
      </div>
    </Panel>
  );
}

function OperationalAuthorityMetric({ detail, label, tone = "neutral", value }: { detail: string; label: string; tone?: "good" | "neutral" | "warn"; value: string }) {
  return (
    <div className={`service-operational-authority-metric tone-${tone}`}>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
      <small title={detail}>{detail}</small>
    </div>
  );
}

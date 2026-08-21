import {
  EXCHANGE_TIME_ZONE,
  VANCOUVER_TIME_ZONE,
  formatTableZoneDate,
  formatTableZoneTime,
  tableTimeTitle,
  tableTimestampMs,
} from "./time";

export function ServiceTableTimeCell({ className = "", compact = false, timeMs, value }: { className?: string; compact?: boolean; timeMs?: number; value: string }) {
  const resolvedMs = tableTimestampMs(value, timeMs);
  const title = tableTimeTitle(value, resolvedMs);
  const timeClassName = `service-table-time-cell ${compact ? "is-compact" : ""} ${className}`.trim();
  return (
    <td className={timeClassName} title={title}>
      {Number.isFinite(resolvedMs) ? (
        <div className="service-table-time-stack">
          <strong>{formatTableZoneTime(resolvedMs, EXCHANGE_TIME_ZONE)}</strong>
          <span>VAN {formatTableZoneTime(resolvedMs, VANCOUVER_TIME_ZONE)}</span>
          {!compact ? <span>ET {formatTableZoneDate(resolvedMs, EXCHANGE_TIME_ZONE)}</span> : null}
        </div>
      ) : (
        <div className="service-table-time-stack">
          <strong>{value || "-"}</strong>
          <span>VAN -</span>
          {!compact ? <span>ET -</span> : null}
        </div>
      )}
    </td>
  );
}

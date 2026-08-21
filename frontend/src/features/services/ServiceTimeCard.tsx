import { formatReadableDateTime } from "./time";

export function ServiceTimeCard({ label, timeZone, value }: { label: string; timeZone: string; value: string }) {
  return (
    <div className="news-full-time-card">
      <span>{label}</span>
      <strong>{value ? formatReadableDateTime(value, timeZone) : "-"}</strong>
      <small>{timeZone === "UTC" ? "UTC" : timeZone.replace("America/", "")}</small>
    </div>
  );
}

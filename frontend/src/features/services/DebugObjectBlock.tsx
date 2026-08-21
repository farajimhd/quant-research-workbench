import { displayName } from "../../app/format";
import { isRecord } from "./workPresentation";

export function DebugObjectBlock({ title, value }: { title: string; value: Record<string, unknown> }) {
  const rows = Object.entries(value || {});
  if (!rows.length) return null;
  return (
    <section className="debug-object-block">
      <h4>{title}</h4>
      <dl className="debug-object-grid">
        {rows.map(([key, item]) => (
          <div className={debugObjectValueWide(item) ? "wide" : ""} key={key}>
            <dt>{displayName(key)}</dt>
            <dd>{debugObjectValue(item)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function debugObjectValue(value: unknown) {
  if (Array.isArray(value)) {
    if (!value.length) return "-";
    if (value.every((item) => typeof item !== "object" || item === null)) return value.map(String).join(", ");
    return JSON.stringify(value, null, 2);
  }
  if (isRecord(value)) return JSON.stringify(value, null, 2);
  if (value === undefined || value === null || value === "") return "-";
  return String(value);
}

function debugObjectValueWide(value: unknown) {
  if (Array.isArray(value) || isRecord(value)) return true;
  return String(value ?? "").length > 100;
}

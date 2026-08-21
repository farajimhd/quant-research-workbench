export function parseServiceTimestamp(value: string) {
  const trimmed = String(value || "").trim();
  if (!trimmed) return Number.NaN;
  const clickHouseUtc = trimmed.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?$/);
  if (clickHouseUtc) {
    const fraction = clickHouseUtc[3] ? clickHouseUtc[3].slice(0, 4).padEnd(4, "0") : "";
    return Date.parse(`${clickHouseUtc[1]}T${clickHouseUtc[2]}${fraction}Z`);
  }
  return Date.parse(trimmed);
}

export function formatServiceTime(value: string) {
  const parsed = parseServiceTimestamp(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(parsed));
}

export function stringValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  return value === null || value === undefined ? "" : String(value);
}

export function numberValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function money(value: number) {
  if (!Number.isFinite(value)) return "-";
  return `${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(Math.abs(value) >= 10 ? 2 : 4)}`;
}

export function percent(value: number) {
  if (!Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(Math.abs(value) >= 0.1 ? 1 : 2)}%`;
}

export function integer(value: number) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString() : "-";
}

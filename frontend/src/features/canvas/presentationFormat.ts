import type { PreviewRow } from "./contracts";

export function nestedValue(row: PreviewRow, container: string, ...keys: string[]) {
  const nested = row[container];
  if (!nested || typeof nested !== "object") return "";
  const record = nested as PreviewRow;
  for (const key of keys) if (record[key] !== undefined && record[key] !== null) return record[key];
  return "";
}

export function money(value: unknown) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: 2, style: "currency" }).format(number) : "—";
}

export function formatQuantity(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(number) : "—";
}

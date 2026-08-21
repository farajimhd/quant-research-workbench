import type { PnlCandleTimeframe, PreviewRow } from "./contracts";

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

export function labelFor(value: string) { return value.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2"); }
export function previewRowKey(row: PreviewRow, columns: string[], index: number) { return `${columns.map((column) => String(row[column] ?? "")).join("|")}|${index}`; }
export function ratioPct(value: unknown) { const number = Number(value); return Number.isFinite(number) ? `${(number * 100).toFixed(number * 100 >= 10 ? 1 : 2)}%` : "—"; }
export function ratioNumber(value: unknown) { const number = Number(value); return Number.isFinite(number) ? `${number.toFixed(2)}×` : "—"; }
export function metricThresholdTone(value: unknown, threshold: number): "negative" | "neutral" | "positive" { const number = Number(value); return !Number.isFinite(number) ? "neutral" : number > threshold ? "positive" : number < threshold ? "negative" : "neutral"; }
export function compactDuration(seconds: number) { if (!Number.isFinite(seconds) || seconds < 0) return "—"; if (seconds < 60) return `${Math.round(seconds)}s`; if (seconds < 3600) return `${Math.round(seconds / 60)}m`; return `${(seconds / 3600).toFixed(seconds < 36_000 ? 1 : 0)}h`; }
export function formatJournalDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("en-US", { day: "numeric", hour: "numeric", minute: "2-digit", month: "short", timeZone: "America/New_York" }).format(date); }
export function formatMoneyAxis(value: number) {
  if (!Number.isFinite(value)) return "";
  const absolute = Math.abs(value);
  const divisor = absolute >= 1_000_000 ? 1_000_000 : absolute >= 1_000 ? 1_000 : 1;
  const suffix = divisor === 1_000_000 ? "M" : divisor === 1_000 ? "K" : "";
  const precision = divisor === 1 || absolute / divisor >= 100 ? 0 : absolute / divisor >= 10 ? 1 : 2;
  return `${value < 0 ? "-" : ""}$${(absolute / divisor).toFixed(precision)}${suffix}`;
}
export function formatPnlCandleTime(value: string, timeframe: PnlCandleTimeframe) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const shared = { timeZone: "America/New_York" } as const;
  if (timeframe === "30m" || timeframe === "1h") return new Intl.DateTimeFormat("en-US", { ...shared, day: "numeric", hour: "numeric", minute: "2-digit", month: "short" }).format(date);
  if (timeframe === "1d") return new Intl.DateTimeFormat("en-US", { ...shared, day: "numeric", month: "short", year: "2-digit" }).format(date);
  return new Intl.DateTimeFormat("en-US", { ...shared, month: "short", year: "numeric" }).format(date);
}
export function basisPoints(value: unknown) { const number = Number(value); return Number.isFinite(number) ? `${number > 0 ? "+" : ""}${number.toFixed(2)} bp` : "—"; }
export function slippageTone(value: unknown): "negative" | "neutral" | "positive" { const number = Number(value); return !Number.isFinite(number) || number === 0 ? "neutral" : number > 0 ? "negative" : "positive"; }
export function formatCell(value: unknown, column: string) { if (value === null || value === undefined || value === "") return "—"; if (column.includes("time") || column.includes("at_utc")) { const date = new Date(String(value)); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(date); } const numeric = typeof value === "number" ? value : /^-?\d+(?:\.\d+)?$/.test(String(value)) ? Number(value) : Number.NaN; if (Number.isFinite(numeric)) { if (isMoneyColumn(column)) return new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: 4, minimumFractionDigits: column.includes("price") || column === "mark" || column === "limit" || column === "stop" ? 2 : 0, style: "currency" }).format(numeric); return new Intl.NumberFormat("en-US", { maximumFractionDigits: column.includes("pct") ? 2 : 4 }).format(numeric); } if (Array.isArray(value)) return value.join(", "); return String(value); }
function isMoneyColumn(column: string) { return ["price", "mark", "limit", "stop", "market_value", "average_price", "unrealized_pnl", "realized_pnl", "gross_pnl", "net_pnl", "fees", "commission", "net_amount", "cash", "settled", "net_liquidation", "entry_price", "exit_price", "expectancy", "max_drawdown", "notional"].some((key) => column === key || column.endsWith(`_${key}`)); }
export function cellTone(value: unknown, column: string) {
  if (["unrealized_pnl", "realized_pnl", "gross_pnl", "net_pnl", "return_pct", "expectancy", "risk_multiple"].includes(column)) { const number = Number(value); return number > 0 ? "positive" : number < 0 ? "negative" : "neutral"; }
  const normalized = String(value || "").toLowerCase();
  if (column === "side") return ["buy", "long"].includes(normalized) ? "positive" : ["sell", "short"].includes(normalized) ? "negative" : "neutral";
  if (column === "status") return ["filled"].includes(normalized) ? "positive" : ["rejected", "cancelled", "expired", "inactive"].includes(normalized) ? "negative" : ["working", "partially_filled", "pending_submission", "trigger_pending"].includes(normalized) ? "primary" : "neutral";
  if (column === "fee_state" && normalized === "pending") return "warning";
  return "neutral";
}

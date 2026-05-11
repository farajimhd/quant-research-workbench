export function formatNumber(value: unknown, decimals = 0): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toLocaleString(undefined, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals
  });
}

export function formatCompactNumber(value: unknown, decimals = 1): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toLocaleString(undefined, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: 0,
    notation: "compact"
  });
}

export function formatBytes(value: unknown): string {
  let size = Number(value ?? 0);
  if (!Number.isFinite(size)) return "-";
  for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
    if (size < 1024 || unit === "TB") {
      return unit === "B" ? `${formatNumber(size)} ${unit}` : `${formatNumber(size, 1)} ${unit}`;
    }
    size /= 1024;
  }
  return `${formatNumber(size, 1)} TB`;
}

export function formatDuration(value: unknown): string {
  const seconds = Number(value ?? 0);
  if (!Number.isFinite(seconds)) return "-";
  if (seconds > 0 && seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(2)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  if (minutes < 60) return `${minutes}m ${remainder}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function formatPct(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "-";
  return `${(numeric * 100).toFixed(2)}%`;
}

export function formatMoney(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

export function displayName(value: string): string {
  const overrides: Record<string, string> = { macd: "MACD", orb: "ORB", vwap: "VWAP", rsi: "RSI", atr: "ATR" };
  return value
    .replaceAll("-", "_")
    .split("_")
    .filter(Boolean)
    .map((part) => overrides[part.toLowerCase()] ?? (part.length <= 3 ? part.toUpperCase() : part[0].toUpperCase() + part.slice(1)))
    .join(" ");
}

export function formatCell(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  const lower = key.toLowerCase();
  if (lower.includes("bytes")) return formatBytes(value);
  if (lower.includes("pct") || lower.includes("rate") || lower.includes("return")) return formatPct(value);
  if (lower.includes("pnl") || lower.includes("cash") || lower.includes("equity") || lower.includes("price")) return formatMoney(value);
  if (typeof value === "number" && Math.abs(value) >= 10000) return formatNumber(value);
  if (typeof value === "number" && !Number.isInteger(value)) return formatNumber(value, 3);
  return String(value);
}

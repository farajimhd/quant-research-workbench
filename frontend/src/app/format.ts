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

export function formatSignedPct(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "-";
  const prefix = numeric > 0 ? "+" : "";
  return `${prefix}${(numeric * 100).toFixed(2)}%`;
}

export function formatMoney(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

const titleAcronyms: Record<string, string> = {
  atr: "ATR",
  bb: "BB",
  bp: "bp",
  bps: "bps",
  cci: "CCI",
  cmf: "CMF",
  ema: "EMA",
  fvg: "FVG",
  fwd: "FWD",
  hvn: "HVN",
  id: "ID",
  lvn: "LVN",
  macd: "MACD",
  mae: "MAE",
  mfe: "MFE",
  mfi: "MFI",
  obv: "OBV",
  orb: "ORB",
  pct: "pct",
  roc: "ROC",
  rsi: "RSI",
  sma: "SMA",
  tema: "TEMA",
  utc: "UTC",
  vwap: "VWAP",
};
const titleLowercaseWords = new Set(["a", "an", "and", "as", "at", "before", "by", "for", "from", "in", "into", "of", "on", "or", "per", "the", "to", "vs", "with", "without"]);

export function displayName(value: string): string {
  const parts = value
    .replaceAll("-", "_")
    .split("_")
    .filter(Boolean);
  const lastIndex = parts.length - 1;
  return parts.map((part, index) => displayNamePart(part, index, lastIndex)).join(" ");
}

function displayNamePart(part: string, index: number, lastIndex: number): string {
  const lower = part.toLowerCase();
  const numericUnit = lower.match(/^(\d+)([a-z]+)$/);
  if (numericUnit && titleAcronyms[numericUnit[2]]) return `${numericUnit[1]} ${titleAcronyms[numericUnit[2]]}`;
  const trailingNumber = lower.match(/^([a-z]+)(\d+)$/);
  if (trailingNumber && titleAcronyms[trailingNumber[1]]) return `${titleAcronyms[trailingNumber[1]]}${trailingNumber[2]}`;
  if (titleAcronyms[lower]) return titleAcronyms[lower];
  if (index > 0 && index < lastIndex && titleLowercaseWords.has(lower)) return lower;
  return lower.slice(0, 1).toUpperCase() + lower.slice(1);
}

export function formatCell(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  const lower = key.toLowerCase();
  if (lower.includes("bytes")) return formatBytes(value);
  if ((lower === "gap_pct" || lower === "last_gap_pct") && isNumericLike(value)) return formatSignedPct(value);
  if (lower.includes("pct") || lower.includes("rate") || lower.includes("return")) return formatPct(value);
  if (isMoneyColumn(lower) && isNumericLike(value)) return formatMoney(value);
  if (typeof value === "number" && Math.abs(value) >= 10000) return formatNumber(value);
  if (typeof value === "number" && !Number.isInteger(value)) return formatNumber(value, 3);
  return String(value);
}

function isMoneyColumn(lowerKey: string) {
  if (lowerKey.includes("pnl") || lowerKey.includes("cash") || lowerKey.includes("equity") || lowerKey.includes("price")) return true;
  if (lowerKey.includes("pct") || lowerKey.includes("volume") || lowerKey.includes("transaction") || lowerKey.includes("location")) return false;
  const parts = lowerKey.split(/[_-]+/).filter(Boolean);
  return parts.some((part) => MONEY_FIELD_PARTS.has(part));
}

function isNumericLike(value: unknown) {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "string") return false;
  return value.trim() !== "" && Number.isFinite(Number(value));
}

const MONEY_FIELD_PARTS = new Set(["ask", "bid", "close", "entry", "exit", "high", "low", "mark", "midpoint", "open", "stop", "vwap"]);

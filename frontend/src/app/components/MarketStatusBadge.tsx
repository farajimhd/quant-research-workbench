import { Clock3, Moon, Sunrise, Sunset, TriangleAlert } from "lucide-react";

export type MarketSessionStatus = "after-hours" | "closed" | "open" | "pre-market" | "unavailable";
export type MarketStatus = { asOfEt: string; label: string; source: "et-clock" | "qmd-service-core"; status: MarketSessionStatus };

const STATUS_ICON = { "after-hours": Sunset, closed: Moon, open: Clock3, "pre-market": Sunrise, unavailable: TriangleAlert } as const;

export function MarketStatusBadge({ value }: { value: MarketStatus }) {
  const Icon = STATUS_ICON[value.status];
  return <div className="market-status-badge" data-market-status={value.status} title={`${value.source === "qmd-service-core" ? "QMD Service Core" : "ET session clock"} · ${value.asOfEt}`}>
    <Icon aria-hidden="true" size={14} /><span>Market</span><strong>{value.label}</strong>
  </div>;
}

export function historicalMarketStatus(dateIso: string, timeText = "09:45:00"): MarketStatus {
  const normalizedTime = normalizeTime(timeText);
  const [year, month, day] = dateIso.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, Math.max(0, month - 1), day)).getUTCDay();
  const seconds = timeToSeconds(normalizedTime);
  if (weekday === 0 || weekday === 6) return makeStatus("closed", "Closed", `${dateIso} ${normalizedTime}`, "et-clock");
  if (seconds >= 4 * 3600 && seconds < 9 * 3600 + 30 * 60) return makeStatus("pre-market", "Pre-market", `${dateIso} ${normalizedTime}`, "et-clock");
  if (seconds >= 9 * 3600 + 30 * 60 && seconds < 16 * 3600) return makeStatus("open", "Open", `${dateIso} ${normalizedTime}`, "et-clock");
  if (seconds >= 16 * 3600 && seconds < 20 * 3600) return makeStatus("after-hours", "After-hours", `${dateIso} ${normalizedTime}`, "et-clock");
  return makeStatus("closed", "Closed", `${dateIso} ${normalizedTime}`, "et-clock");
}

export function liveMarketStatus(payload: Record<string, unknown> | null): MarketStatus {
  const header = asRecord(payload?.header);
  const raw = String(header.market_status ?? "").trim().toLowerCase();
  const reason = String(header.market_calendar_reason ?? "").trim().toLowerCase();
  const asOf = String(header.snapshot_utc ?? "");
  if (reason.includes("early") || raw.includes("early")) return makeStatus("pre-market", "Pre-market", asOf, "qmd-service-core");
  if (reason.includes("after") || raw.includes("after")) return makeStatus("after-hours", "After-hours", asOf, "qmd-service-core");
  if (["active", "open"].includes(raw)) return makeStatus("open", "Open", asOf, "qmd-service-core");
  if (raw === "closed") return makeStatus("closed", "Closed", asOf, "qmd-service-core");
  return makeStatus("unavailable", "Unavailable", asOf, "qmd-service-core");
}

function asRecord(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function makeStatus(status: MarketSessionStatus, label: string, asOfEt: string, source: MarketStatus["source"]): MarketStatus { return { asOfEt, label, source, status }; }
function normalizeTime(value: string) { const parts = value.split(":"); return `${parts[0] || "00"}:${parts[1] || "00"}:${parts[2] || "00"}`; }
function timeToSeconds(value: string) { const [hours = 0, minutes = 0, seconds = 0] = value.split(":").map(Number); return hours * 3600 + minutes * 60 + seconds; }

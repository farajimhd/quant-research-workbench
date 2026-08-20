import { FileCheck2, Flame, ShieldAlert, Snowflake } from "lucide-react";
import type { ReactNode } from "react";

import { MarketTime } from "./MarketTime";
import { TickerLogo } from "./TickerIdentity";

export type PresentationValueType =
  | "basis_points" | "boolean" | "category" | "date" | "datetime" | "identifier"
  | "integer" | "money" | "percent" | "price" | "quantity" | "ratio" | "score" | "text" | "time";

export type TableColumnPresentation = {
  importance?: "normal" | "strong";
  label?: string;
  presentationValueType?: PresentationValueType;
  semanticTone?: "directional" | "inverse-directional" | "neutral";
};

export function presentationForColumn(column: string, override?: TableColumnPresentation): Required<TableColumnPresentation> {
  const key = column.toLowerCase();
  const importance = override?.importance ?? (isImportantColumn(key) ? "strong" : "normal");
  const categorical = isCategoricalColumn(key);
  if (override?.presentationValueType && !categorical) return { importance, label: override.label ?? labelForColumn(column), presentationValueType: override.presentationValueType, semanticTone: override.semanticTone ?? "neutral" };
  const directional = /(^|_)(change|return|pnl|gain|growth|margin|momentum|imbalance|net_flow)(_|$)/.test(key);
  const inverse = /(^|_)(dilution|drawdown|spread|debt)(_|$)/.test(key);
  let presentationValueType: PresentationValueType = "text";
  if (categorical) presentationValueType = "category";
  else if (key === "time" || key.endsWith("_time") || key.endsWith("_at") || key.endsWith("_at_utc") || key.includes("timestamp")) presentationValueType = "datetime";
  else if (key.endsWith("_date") || key === "date") presentationValueType = "date";
  else if (key.includes("pct") || key.includes("percent") || key.includes("return")) presentationValueType = "percent";
  else if (key.includes("bps")) presentationValueType = "basis_points";
  else if (/(^|_)(price|bid|ask|open|high|low|close|vwap|stop)(_|$)/.test(key)) presentationValueType = "price";
  else if (/(^|_)(pnl|cash|value|cost|proceeds|notional|market_cap|dollar_volume)(_|$)/.test(key)) presentationValueType = "money";
  else if (/(^|_)(volume|shares|size|count|quantity|qty)(_|$)/.test(key)) presentationValueType = "quantity";
  else if (/(^|_)(score|confidence)(_|$)/.test(key)) presentationValueType = "score";
  else if (/(^|_)(ratio|multiple|rate)(_|$)/.test(key)) presentationValueType = "ratio";
  else if (/(^|_)(id|symbol|ticker|account|cik|accession)(_|$)/.test(key)) presentationValueType = "identifier";
  return { importance, label: override?.label ?? labelForColumn(column), presentationValueType, semanticTone: override?.semanticTone ?? (inverse ? "inverse-directional" : directional ? "directional" : "neutral") };
}

export function PresentedValue({ column, presentation, value }: { column: string; presentation?: TableColumnPresentation; value: unknown }) {
  const resolved = presentationForColumn(column, presentation);
  if (value === null || value === undefined || value === "") return <span className="table-value-unavailable">—</span>;
  if (resolved.presentationValueType === "datetime") return <MarketTime includeSeconds value={String(value)} />;
  if (resolved.presentationValueType === "date") return <DateOnlyValue value={String(value)} />;
  if (resolved.presentationValueType === "category" || resolved.presentationValueType === "boolean") return <CategoryBadge column={column} value={value} />;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && isNumericPresentation(resolved.presentationValueType)) {
    const tone = numericTone(numeric, resolved.semanticTone);
    return <span className="table-number" data-importance={resolved.importance} data-tone={tone} title={new Intl.NumberFormat("en-US", { maximumFractionDigits: 10 }).format(numeric)}>{formatNumber(numeric, resolved.presentationValueType, resolved.semanticTone)}</span>;
  }
  return <span className={resolved.presentationValueType === "identifier" ? "table-identifier" : "table-text"}>{String(value)}</span>;
}

export function CategoryBadge({ column = "", value }: { column?: string; value: unknown }) {
  const label = String(value).replaceAll("_", " ").trim();
  if (!label) return <span className="table-value-unavailable">—</span>;
  return <span className="table-category-badge" data-tone={categoryTone(column, label)}>{label}</span>;
}

export function SecurityIdentityCell({ companyName = "", country = "", halted, logoUrl = "", newsRecency, secRecency, ticker, trailing }: { companyName?: string; country?: string; halted?: unknown; logoUrl?: string; newsRecency?: unknown; secRecency?: unknown; ticker: string; trailing?: ReactNode }) {
  const symbol = ticker.trim().toUpperCase();
  const countryName = formatCountry(country);
  const newsState = normalizedRecency(newsRecency);
  const secState = normalizedRecency(secRecency);
  const isHalted = normalizedBoolean(halted);
  const hasTrailing = isHalted || isRecentRecency(newsState) || isRecentRecency(secState) || Boolean(trailing);
  return <span className="table-security-card" data-has-trailing={hasTrailing} title={[symbol, companyName, countryName].filter(Boolean).join(" · ")}>
    <TickerLogo logoUrl={logoUrl} showLogoPlaceholder ticker={symbol} />
    <span className="table-security-copy"><strong>{symbol || "—"}</strong>{companyName ? <small>{companyName}</small> : null}</span>
    {countryName ? <span className="table-security-country">{countryName}</span> : null}
    {hasTrailing ? <span className="table-security-trailing">
      {isHalted ? <span aria-label="Trading halted" className="table-security-status-icon" data-status="halted" title="Trading halted"><ShieldAlert aria-hidden="true" size={17} strokeWidth={1.8} /></span> : null}
      <SecurityRecencyIcon kind="news" state={newsState} />
      <SecurityRecencyIcon kind="sec" state={secState} />
      {trailing}
    </span> : null}
  </span>;
}

type RecencyState = "hot" | "cold" | "old" | "none" | "unavailable";

function SecurityRecencyIcon({ kind, state }: { kind: "news" | "sec"; state: RecencyState }) {
  if (!isRecentRecency(state)) return null;
  const source = kind === "news" ? "News" : "SEC filing";
  const Icon = kind === "news" ? state === "hot" ? Flame : Snowflake : FileCheck2;
  const description = `${state} ${source.toLowerCase()}`;
  const hot = state === "hot";
  return <span aria-label={description} className="table-security-recency-icon" data-source={kind} data-state={state} title={description}><Icon aria-hidden="true" size={hot ? 16 : 15} strokeWidth={hot ? 1.5 : 1.8} /></span>;
}

function normalizedRecency(value: unknown): RecencyState {
  if (value === null || value === undefined || value === "") return "unavailable";
  const state = String(value).trim().toLowerCase();
  if (state === "warm" || state === "recent") return "cold";
  return state === "hot" || state === "cold" || state === "old" || state === "none" ? state : "unavailable";
}

function isRecentRecency(state: RecencyState) { return state === "hot" || state === "cold"; }
function normalizedBoolean(value: unknown) {
  if (value === true || value === 1) return true;
  if (typeof value !== "string") return false;
  return ["true", "1", "yes", "halted"].includes(value.trim().toLowerCase());
}

export function tableCellClass(column: string, presentation?: TableColumnPresentation) {
  const resolved = presentationForColumn(column, presentation);
  return `table-presented-cell table-presented-${resolved.presentationValueType}`;
}

export function labelForColumn(column: string) { return column.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }

function DateOnlyValue({ value }: { value: string }) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return <span className="table-text">{value}</span>;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12));
  const label = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(date);
  return <time className="market-time market-time-inline table-date-only" dateTime={`${match[1]}-${match[2]}-${match[3]}`}><span className="market-time-primary"><strong>{label}</strong></span></time>;
}

function isNumericPresentation(type: PresentationValueType) { return ["basis_points", "integer", "money", "percent", "price", "quantity", "ratio", "score"].includes(type); }
function numericTone(value: number, semantic: Required<TableColumnPresentation>["semanticTone"]) { if (!value || semantic === "neutral") return "neutral"; const positive = semantic === "inverse-directional" ? value < 0 : value > 0; return positive ? "positive" : "negative"; }
function categoryTone(column: string, value: string) {
  const field = column.toLowerCase();
  const key = value.toLowerCase();
  if (/(^|_)(float_category|float_profile)$/.test(field)) {
    if (/tiny|micro|extra small|small|low/.test(key)) return "positive";
    if (/medium\+?|mid/.test(key)) return "info";
    if (/extra large|large/.test(key)) return "warning";
    if (/broad/.test(key)) return "negative";
  }
  if (/(phase|session|exchange|sector|industry|country|currency|source|origin|role|type|category|class)/.test(field)) return "neutral";
  if (/(direction|side|action|sentiment|bias|outlook)/.test(field)) {
    if (/bull|buy|long|positive/.test(key)) return "positive";
    if (/bear|sell|short|negative/.test(key)) return "negative";
    if (/mixed|uncertain/.test(key)) return "warning";
    return "neutral";
  }
  if (/(status|state|quality|health|eligib|valid)/.test(field)) {
    if (/ready|active|filled|approved|success|healthy|valid|eligible/.test(key)) return "positive";
    if (/error|failed|rejected|danger|halt|invalid|ineligible/.test(key)) return "negative";
    if (/pending|warning|partial|stale|degraded/.test(key)) return "warning";
  }
  return "neutral";
}
function isCategoricalColumn(column: string) {
  if (/(^|_)(id|identifier|accession|cik)(_|$)/.test(column)) return false;
  return /(^|_)(status|state|phase|direction|side|type|category|class|role|origin|source|provider|exchange|sector|industry|country|currency|quality|coverage|sentiment|bias|outlook)(_|$)/.test(column);
}
function formatNumber(value: number, type: PresentationValueType, semantic: Required<TableColumnPresentation>["semanticTone"]) {
  if (type === "percent") return `${semantic !== "neutral" && value > 0 ? "+" : ""}${value.toFixed(Math.abs(value) < 1 ? 2 : 1)}%`;
  if (type === "basis_points") return `${value.toFixed(Math.abs(value) < 10 ? 1 : 0)} bps`;
  if (type === "price") return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: Math.abs(value) < 1 ? 4 : Math.abs(value) < 100 ? 3 : 2 }).format(value);
  if (type === "money") return compact(value, "$", "");
  if (type === "quantity" || type === "integer") return compact(value, "", "");
  if (type === "ratio") return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)}×`;
  if (type === "score") return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
  return String(value);
}
function compact(value: number, prefix: string, suffix: string) { const absolute = Math.abs(value); const scales: Array<[number, string]> = [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]; const scale = scales.find(([threshold]) => absolute >= threshold); const formatted = scale ? `${(value / scale[0]).toFixed(Math.abs(value / scale[0]) < 10 ? 2 : 1)}${scale[1]}` : new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value); return `${prefix}${formatted}${suffix}`; }
function isImportantColumn(column: string) { return ["last_price", "current_price", "net_liquidation", "realized_pnl", "unrealized_pnl", "signal_score"].includes(column); }
function formatCountry(value: string) {
  const code = value.trim().toUpperCase();
  if (!code) return "";
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code; } catch { return code; }
}

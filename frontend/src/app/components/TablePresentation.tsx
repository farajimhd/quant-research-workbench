import { Building2, CheckCircle2, CircleAlert, Clock3, Flame, Gauge, Info, Minus, ShieldAlert, Snowflake, TrendingDown, TrendingUp, Zap } from "lucide-react";
import type { ReactNode } from "react";

import { MarketTime } from "./MarketTime";
import { normalizeSecIconPrediction, SecIntelligenceIcon, secIconKindFor, secIconKindLabel } from "./SecIntelligenceIcon";
import { TickerLogo } from "./TickerIdentity";
import { dispatchTickerSecPopover } from "./TickerSecPopoverContext";

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
  if (override?.presentationValueType === "boolean") {
    return { importance, label: override.label ?? labelForColumn(column), presentationValueType: "boolean", semanticTone: override.semanticTone ?? "neutral" };
  }
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
  else if (/(^|_)(pnl|cash|value|cost|fee|fees|commission|proceeds|notional|market_cap|dollar_volume)(_|$)/.test(key)) presentationValueType = "money";
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
  if (resolved.presentationValueType === "boolean") return <CategoryBadge booleanMode column={column} value={value} />;
  if (resolved.presentationValueType === "category") return <CategoryBadge column={column} value={value} />;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && isNumericPresentation(resolved.presentationValueType)) {
    const tone = numericTone(numeric, resolved.semanticTone);
    return <span className="table-number" data-importance={resolved.importance} data-tone={tone} title={new Intl.NumberFormat("en-US", { maximumFractionDigits: 10 }).format(numeric)}>{formatNumber(numeric, resolved.presentationValueType, resolved.semanticTone)}</span>;
  }
  return <span className={resolved.presentationValueType === "identifier" ? "table-identifier" : "table-text"}>{String(value)}</span>;
}

export function CategoryBadge({ booleanMode = false, column = "", value }: { booleanMode?: boolean; column?: string; value: unknown }) {
  const boolean = booleanMode ? booleanBadge(value) : null;
  const label = boolean?.label ?? String(value).replaceAll("_", " ").trim();
  if (!label) return <span className="table-value-unavailable">—</span>;
  const tone = boolean?.tone ?? categoryTone(column, label);
  const Icon = boolean ? boolean.icon : categoryIcon(column, tone);
  return <span className="table-category-badge" data-emphasis={boolean ? "strong" : categoryEmphasis(column)} data-tone={tone} title={label}>
    {Icon ? <Icon aria-hidden="true" className="table-category-badge-icon" size={11} strokeWidth={2} /> : null}
    <span>{label}</span>
  </span>;
}

function booleanBadge(value: unknown): { icon: typeof CheckCircle2; label: string; tone: "neutral" | "positive" } | null {
  if (value === true || value === 1 || (typeof value === "string" && ["true", "1", "yes", "on"].includes(value.trim().toLowerCase()))) {
    return { icon: CheckCircle2, label: "On", tone: "positive" };
  }
  if (value === false || value === 0 || (typeof value === "string" && ["false", "0", "no", "off"].includes(value.trim().toLowerCase()))) {
    return { icon: Minus, label: "Off", tone: "neutral" };
  }
  return null;
}

export function SecurityIdentityCell({ companyName = "", country = "", halted, logoUrl = "", newsRecency, onTickerSelect, secCount, secLabels, secRecency, secReviewDirection, secReviewStatus, secSynthesisCount, secSynthesisDirection, ticker, trailing }: { companyName?: string; country?: string; halted?: unknown; logoUrl?: string; newsRecency?: unknown; onTickerSelect?: (ticker: string) => void; secCount?: unknown; secLabels?: unknown; secRecency?: unknown; secReviewDirection?: unknown; secReviewStatus?: unknown; secSynthesisCount?: unknown; secSynthesisDirection?: unknown; ticker: string; trailing?: ReactNode }) {
  const symbol = ticker.trim().toUpperCase();
  const countryName = formatCountry(country);
  const newsState = normalizedRecency(newsRecency);
  const secState = normalizedRecency(secRecency);
  const isHalted = normalizedBoolean(halted);
  const hasTrailing = isHalted || isRecentRecency(newsState) || isRecentRecency(secState) || Boolean(trailing);
  return <span className="table-security-card" data-has-trailing={hasTrailing}>
    <TickerLogo logoUrl={logoUrl} showLogoPlaceholder ticker={symbol} />
    <span className="table-security-copy">{symbol && onTickerSelect ? <button aria-label={`Open ${symbol} Charts & Quotes in a new tab`} className="ticker-charts-quotes-link" onClick={() => onTickerSelect(symbol)} type="button"><strong>{symbol}</strong></button> : <strong>{symbol || "—"}</strong>}{companyName ? <small>{companyName}</small> : null}</span>
    {countryName ? <span className="table-security-country">{countryName}</span> : null}
    {hasTrailing ? <span className="table-security-trailing">
      {isHalted ? <span aria-label="Trading halted" className="table-security-status-icon" data-status="halted"><ShieldAlert aria-hidden="true" size={17} strokeWidth={1.8} /></span> : null}
      <SecurityRecencyIcon kind="news" state={newsState} ticker={symbol} />
      <SecurityRecencyIcon count={secCount} kind="sec" labels={secLabels} reviewDirection={secReviewDirection} reviewStatus={secReviewStatus} state={secState} synthesisCount={secSynthesisCount} synthesisDirection={secSynthesisDirection} ticker={symbol} />
      {trailing}
    </span> : null}
  </span>;
}

type RecencyState = "hot" | "cold" | "old" | "none" | "unavailable";

function SecurityRecencyIcon({ count, kind, labels, reviewDirection, reviewStatus, state, synthesisCount, synthesisDirection, ticker }: { count?: unknown; kind: "news" | "sec"; labels?: unknown; reviewDirection?: unknown; reviewStatus?: unknown; state: RecencyState; synthesisCount?: unknown; synthesisDirection?: unknown; ticker: string }) {
  if (!isRecentRecency(state)) return null;
  const source = kind === "news" ? "News" : "SEC filing";
  const Icon = state === "hot" ? Flame : Snowflake;
  const description = `${state} ${source.toLowerCase()}`;
  const hot = state === "hot";
  if (kind === "sec") {
    const filingCount = Math.max(0, Math.round(Number(count) || 0));
    const synthesized = Math.max(0, Math.round(Number(synthesisCount) || 0)) > 0;
    const reviewed = ["complete", "completed"].includes(String(reviewStatus || "").toLowerCase());
    const iconKind = secIconKindFor(labels);
    const prediction = normalizeSecIconPrediction(reviewed && reviewDirection ? reviewDirection : synthesisDirection);
    const predictionSource = reviewed && reviewDirection ? "AI review" : synthesized && prediction !== "unavailable" ? "SEC Synthesis" : "";
    const predictionState = predictionSource ? `${predictionSource} ${prediction}` : "direction unavailable";
    const intelligenceState = [secIconKindLabel(iconKind), predictionState, synthesized ? "synthesis available" : "synthesis pending", reviewed ? "manual AI reviewed" : ""].filter(Boolean).join(", ");
    return <button aria-label={`Open ${ticker} SEC filing timeline. ${description}; ${intelligenceState}.`} className="table-security-recency-icon" data-open-sec-ticker={ticker} data-prediction={prediction} data-source={kind} data-state={state} onClick={(event) => { event.stopPropagation(); dispatchTickerSecPopover(event.currentTarget, ticker); }} onPointerDown={(event) => event.stopPropagation()} type="button"><SecIntelligenceIcon count={filingCount} kind={iconKind} prediction={prediction} recency={state} reviewed={reviewed} synthesized={synthesized} /></button>;
  }
  return <span aria-label={description} className="table-security-recency-icon" data-source={kind} data-state={state}><Icon aria-hidden="true" size={hot ? 16 : 15} strokeWidth={hot ? 1.5 : 1.8} /></span>;
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
  if (/(^|_)halt_direction$/.test(field)) {
    if (/^up$/.test(key)) return "positive";
    if (/^down$/.test(key)) return "negative";
    return "neutral";
  }
  if (/(^|_)halt_category$/.test(field)) {
    if (/regulatory|suspension|noncompliance|sec/.test(key)) return "negative";
    if (/luld|volatility|pause/.test(key)) return "warning";
    if (/news|information/.test(key)) return "info";
    if (/corporate action|order imbalance/.test(key)) return "highlight";
    return "neutral";
  }
  if (/(^|_)(float_category|float_profile)$/.test(field)) {
    if (/tiny|micro|extra small|small|low/.test(key)) return "positive";
    if (/medium\+?|mid/.test(key)) return "info";
    if (/extra large|large/.test(key)) return "warning";
    if (/broad/.test(key)) return "negative";
  }
  if (/(^|_)(cap_category|market_cap_category)$/.test(field)) {
    if (/small/.test(key)) return "highlight";
    if (/mid|medium/.test(key)) return "info";
    return "neutral";
  }
  if (/(^|_)(session_phase|market_phase)$/.test(field)) {
    if (/regular|open/.test(key)) return "positive";
    if (/pre/.test(key)) return "info";
    if (/after/.test(key)) return "highlight";
    if (/maintenance|closed/.test(key)) return "warning";
  }
  if (/(^|_)short_pressure$/.test(field)) {
    if (/crowded/.test(key)) return "negative";
    if (/elevated/.test(key)) return "warning";
    if (/normal/.test(key)) return "positive";
    return "neutral";
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
function categoryEmphasis(column: string) {
  const field = column.toLowerCase();
  if (/(float_category|float_profile|short_pressure|status|state|quality|health|eligib|valid|direction|side|action|sentiment|bias|outlook|signal)/.test(field)) return "strong";
  if (/(cap_category|market_cap_category|phase|type|class|coverage)/.test(field)) return "medium";
  return "subtle";
}
function categoryIcon(column: string, tone: ReturnType<typeof categoryTone>) {
  const field = column.toLowerCase();
  if (/(^|_)halt_category$/.test(field)) return ShieldAlert;
  if (/(float_category|float_profile)/.test(field)) return Gauge;
  if (/(cap_category|market_cap_category)/.test(field)) return Building2;
  if (/(session_phase|market_phase)/.test(field)) return Clock3;
  if (/(^|_)short_pressure$/.test(field)) return Gauge;
  if (/(direction|side|sentiment|bias|outlook)/.test(field)) return tone === "positive" ? TrendingUp : tone === "negative" ? TrendingDown : Minus;
  if (/(signal|action)/.test(field)) return Zap;
  if (/(status|state|quality|health|eligib|valid)/.test(field)) return tone === "positive" ? CheckCircle2 : tone === "negative" || tone === "warning" ? CircleAlert : Info;
  return null;
}
function isCategoricalColumn(column: string) {
  if (/(^|_)(id|identifier|accession|cik)(_|$)/.test(column)) return false;
  return /(^|_)(status|state|phase|direction|side|type|category|class|role|origin|source|provider|exchange|sector|industry|country|currency|quality|coverage|sentiment|bias|outlook|pressure)(_|$)/.test(column);
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

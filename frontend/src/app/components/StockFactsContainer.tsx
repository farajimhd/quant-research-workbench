import { AlertTriangle, BookOpen, Building2, CalendarDays, Database, Landmark, Scale, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api, query } from "../../api/client";
import { Modal } from "./Modal";
import { TickerIdentityWithChange, useTickerPresentations } from "./TickerIdentity";

type FactRecord = Record<string, unknown>;
type FundamentalFact = FactRecord & { label?: string };
type SourceFact = { available: boolean; label: string; table: string };
type TickerFactsPayload = {
  as_of: string;
  errors: Record<string, string>;
  facts: {
    borrow?: FactRecord;
    classifications?: FactRecord[];
    corporate?: FactRecord;
    float?: FactRecord;
    identity?: FactRecord;
    market?: FactRecord;
    short_interest?: FactRecord;
    short_volume?: FactRecord;
    volume?: FactRecord;
  };
  fundamentals: FundamentalFact[];
  identifiers: FactRecord[];
  sources: SourceFact[];
  status: "not_found" | "partial" | "ready";
  symbol: string;
  warnings: string[];
};

type StockFactsContainerProps = {
  asOf: string;
  onSymbolChange?: (symbol: string) => void;
  symbol: string;
};

export function StockFactsContainer({ asOf, onSymbolChange, symbol }: StockFactsContainerProps) {
  const [payload, setPayload] = useState<TickerFactsPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [guideOpen, setGuideOpen] = useState(false);
  const presentations = useTickerPresentations([symbol]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<TickerFactsPayload>(`/api/trading/ticker-facts/${encodeURIComponent(symbol)}${query({ as_of: asOf })}`, {
      signal: controller.signal,
      timeoutMs: 30000,
    }).then((next) => { if (!controller.signal.aborted) setPayload(next); })
      .catch((reason) => { if (!controller.signal.aborted) { setPayload(null); setError(reason instanceof Error ? reason.message : String(reason)); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [asOf, symbol]);

  const facts = payload?.facts ?? {};
  const identity = facts.identity ?? {};
  const market = facts.market ?? {};
  const float = facts.float ?? {};
  const shortInterest = facts.short_interest ?? {};
  const shortVolume = facts.short_volume ?? {};
  const volume = facts.volume ?? {};
  const borrow = facts.borrow ?? {};
  const classifications = facts.classifications ?? [];
  const companyName = text(identity.branding_name, identity.issuer_name, identity.legal_name, identity.security_name) || presentations[symbol]?.issuer_name || symbol;
  const sharesOutstanding = number(float.shares_outstanding, market.share_class_shares_outstanding, market.weighted_shares_outstanding);
  const freeFloat = number(float.free_float);
  const shortPercent = number(shortInterest.percent_of_float) ?? number(shortInterest.percent_of_outstanding);
  const shortPercentBasis = number(shortInterest.percent_of_float) != null ? "of float" : number(shortInterest.percent_of_outstanding) != null ? "of shares" : "unavailable";
  const compactFacts = useMemo(() => [
    { detail: dateLabel(market.observed_at_utc), label: "Market cap", value: formatMoney(number(market.market_cap)) },
    { detail: freeFloat != null ? percentDetail(number(float.free_float_percent)) : "Not published", label: "Free float", value: formatCount(freeFloat) },
    { detail: dateLabel(float.effective_date ?? market.observed_at_utc), label: "Shares out", value: formatCount(sharesOutstanding) },
    { detail: `${formatCount(number(volume.average_volume_20d))} 20D avg`, label: "Latest volume", value: formatCount(number(volume.latest_volume)) },
    { detail: "vs 20 daily sessions", label: "Relative volume", tone: toneActivity(number(volume.relative_volume_20d)), value: formatMultiple(number(volume.relative_volume_20d)) },
    { detail: shortPercentBasis, label: "Short interest", tone: toneShort(shortPercent), value: formatPercent(shortPercent) },
  ], [float, freeFloat, market, sharesOutstanding, shortPercent, shortPercentBasis, volume]);

  return <section className="stock-facts" data-status={payload?.status ?? (loading ? "loading" : "error")}>
    <header className="facts-header">
      <div className="facts-header-identity">
        <TickerIdentityWithChange asOf={asOf} inputAriaLabel="Facts ticker" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} />
        <span><strong>{companyName}</strong><small>{identityDescription(identity)}</small></span>
      </div>
      <div className="facts-header-actions">
        <span className="facts-clock"><CalendarDays size={12} />{formatAsOf(asOf)}</span>
        <button className="facts-guide-button" onClick={() => setGuideOpen(true)} type="button"><BookOpen size={13} /> Guide</button>
      </div>
    </header>
    {loading && !payload ? <FactsState label="Loading canonical issuer, market, SEC, FINRA, and IBKR facts…" />
      : error ? <FactsState error label={error} />
        : payload?.status === "not_found" ? <FactsState error label={payload.warnings[0] || `No facts found for ${symbol}.`} />
          : <div className="facts-scroll">
            <section aria-label="Primary stock facts" className="facts-primary-grid">
              {compactFacts.map((fact) => <FactMetric {...fact} key={fact.label} />)}
            </section>
            {(payload?.warnings.length || Object.keys(payload?.errors ?? {}).length) ? <FactsNotice errors={payload?.errors ?? {}} warnings={payload?.warnings ?? []} /> : null}
            <div className="facts-evidence-grid">
              <FactSection icon={Scale} subtitle="Positioning, locate evidence, and dated short activity" title="Short & borrow">
                <div className="facts-detail-grid">
                  <FactDatum label="Short shares" meta={dateLabel(shortInterest.settlement_date, "settled")} value={formatCount(number(shortInterest.short_interest))} />
                  <FactDatum label="Change" meta={previousLabel(shortInterest.previous_settlement_date)} tone={toneSigned(number(shortInterest.change_from_previous), true)} value={formatSignedCount(number(shortInterest.change_from_previous))} />
                  <FactDatum label="Days to cover" meta="SI ÷ average daily volume" value={formatNumber(number(shortInterest.days_to_cover), 2)} />
                  <FactDatum label="FINRA short volume" meta={dateLabel(shortVolume.latest_trade_date)} tone={toneShort(ratioToPercent(number(shortVolume.latest_short_volume_ratio)))} value={formatPercent(ratioToPercent(number(shortVolume.latest_short_volume_ratio)))} />
                  <FactDatum label="20D short-volume ratio" meta={`${integer(shortVolume.sessions)} sessions`} tone={toneShort(ratioToPercent(number(shortVolume.ratio_20d)))} value={formatPercent(ratioToPercent(number(shortVolume.ratio_20d)))} />
                  <FactDatum label="IBKR borrow" meta={dateLabel(borrow.observed_at_utc)} tone={borrowTone(text(borrow.borrow_status))} value={borrowValue(borrow)} />
                  <FactDatum label="Shortable shares" meta="Latest IBKR snapshot" value={formatCount(number(borrow.shortable_shares))} />
                  <FactDatum label="Borrow / fee rate" meta="Indicative when provided" value={formatBorrowRates(borrow)} />
                </div>
              </FactSection>
              <FactSection icon={Landmark} subtitle="Latest SEC-reported observations available at this clock" title="Fundamentals">
                {payload?.fundamentals.length ? <div className="fundamental-list">{payload.fundamentals.map((fact) => <article key={String(fact.label)}>
                  <span>{fact.label}</span><strong>{formatFundamental(fact)}</strong><small>{fundamentalMeta(fact)}</small>
                </article>)}</div> : <FactsInlineEmpty label="No selected SEC-reported facts available." />}
              </FactSection>
              <FactSection icon={Building2} subtitle="Issuer, security, listing, and corporate-action context" title="Company & listing">
                <div className="facts-detail-grid company-fact-grid">
                  <FactDatum label="Issuer" value={text(identity.legal_name, identity.issuer_name)} />
                  <FactDatum label="Security" value={text(identity.security_name, identity.security_type)} />
                  <FactDatum label="Classification" value={text(identity.sic_description, identity.industry, identity.sector)} />
                  <FactDatum label="SIC" value={text(identity.sic_code)} />
                  <FactDatum label="Entity / incorporation" value={[text(identity.entity_type), text(identity.state_of_incorporation)].filter(Boolean).join(" · ") || "—"} />
                  <FactDatum label="Product taxonomy" meta="Provider classifications" value={classificationSummary(classifications)} />
                  <FactDatum label="Exchange / currency" value={[text(identity.exchange_code), text(identity.currency_code)].filter(Boolean).join(" · ") || "—"} />
                  <FactDatum label="Listed" value={text(identity.list_date) || "—"} />
                  <FactDatum label="IBKR conid" value={text(identity.ibkr_conid)} />
                  <FactDatum label="Tradability" tone={Number(identity.is_tradable) === 1 ? "positive" : "negative"} value={Number(identity.is_tradable) === 1 ? "Tradable" : text(identity.exclusion_reason) || "Blocked"} />
                  <FactLink label="Company website" value={text(identity.website_url)} />
                  <FactLink label="Investor relations" value={text(identity.investor_website_url)} />
                  <FactDatum label="Last split" meta={dateLabel(facts.corporate?.last_split_date)} value={splitValue(facts.corporate ?? {})} />
                  <FactDatum label="Last dividend" meta={dateLabel(facts.corporate?.last_ex_dividend_date, "ex-date")} value={dividendValue(facts.corporate ?? {})} />
                </div>
              </FactSection>
              <FactSection icon={ShieldCheck} subtitle="Canonical cross-provider keys for reconciliation" title="Identifiers">
                {payload?.identifiers.length ? <div className="identifier-list">{payload.identifiers.map((item, index) => <article key={`${text(item.entity)}-${text(item.identifier_kind)}-${index}`}><span>{friendlyIdentifier(item.identifier_kind)}</span><strong>{text(item.identifier_value)}</strong><small>{text(item.source_system)}</small></article>)}</div> : <FactsInlineEmpty label="No canonical identifiers available." />}
              </FactSection>
              <FactSection className="facts-source-section" icon={Database} subtitle="Availability reflects this ticker and selected clock" title="Data provenance">
                <div className="facts-source-list">{payload?.sources.map((source) => <article data-available={source.available ? "true" : "false"} key={source.label}><i aria-hidden="true" /><span><strong>{source.label}</strong><small>{source.table}</small></span><em>{source.available ? "Available" : "No row"}</em></article>)}</div>
              </FactSection>
            </div>
          </div>}
    {guideOpen ? <FactsGuide onClose={() => setGuideOpen(false)} /> : null}
  </section>;
}

function FactMetric({ detail, label, tone = "neutral", value }: { detail: string; label: string; tone?: string; value: string }) {
  return <article className="facts-primary-metric" data-tone={tone}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function FactSection({ children, className = "", icon: Icon, subtitle, title }: { children: ReactNode; className?: string; icon: typeof Building2; subtitle: string; title: string }) {
  return <section className={`facts-section${className ? ` ${className}` : ""}`}><header><Icon size={14} /><span><strong>{title}</strong><small>{subtitle}</small></span></header>{children}</section>;
}

function FactDatum({ label, meta, tone = "neutral", value }: { label: string; meta?: string; tone?: string; value: string }) {
  return <article className="facts-datum" data-tone={tone}><span>{label}</span><strong title={value}>{value || "—"}</strong>{meta ? <small>{meta}</small> : null}</article>;
}

function FactLink({ label, value }: { label: string; value: string }) {
  const href = safeExternalUrl(value);
  return <article className="facts-datum"><span>{label}</span>{href ? <a href={href} rel="noreferrer" target="_blank" title={href}>{displayHost(href)}</a> : <strong>—</strong>}<small>{href ? "Provider-supplied link" : "Not published"}</small></article>;
}

function FactsNotice({ errors, warnings }: { errors: Record<string, string>; warnings: string[] }) {
  const issueCount = warnings.length + Object.keys(errors).length;
  return <details className="facts-notice"><summary><AlertTriangle size={12} /><strong>{issueCount} data note{issueCount === 1 ? "" : "s"}</strong><span>Missing values are not estimated</span></summary><div>{warnings.map((warning) => <p key={warning}>{warning}</p>)}{Object.entries(errors).map(([source, message]) => <p key={source}><b>{source}:</b> {message}</p>)}</div></details>;
}

function FactsGuide({ onClose }: { onClose: () => void }) {
  return <Modal className="facts-guide-modal" onClose={onClose} title="How to read Stock Facts"><div className="facts-guide-content">
    <div className="facts-guide-intro"><strong>Use facts as context, not a directional signal.</strong><p>Every value keeps its source date. Compare market activity, share supply, short positioning, and reported company results without treating differently dated publications as if they arrived together.</p></div>
    <div className="facts-guide-grid">
      <GuideItem title="Market cap and shares" text="Massive's dated market snapshot. Market cap measures company scale; shares outstanding is issued equity. Free float is the subset generally available to public trading and remains blank when the float table has no row." />
      <GuideItem title="Volume and relative volume" text="Latest completed QMD daily trade volume compared with the mean of the latest 20 completed daily sessions. Above 1× means activity is elevated; it says nothing about direction without price and flow." />
      <GuideItem title="Short interest" text="A settlement-date stock of shares sold short. Days to cover divides short interest by the publication's average daily volume. A high value can create covering pressure, but can also reflect persistent bearish positioning." />
      <GuideItem title="FINRA short volume" text="Daily off-exchange and exchange short-sale marking volume for the available FINRA venue file. It is transaction flow, not the outstanding short-interest stock, and should never be used as a substitute for short interest." />
      <GuideItem title="IBKR borrow" text="The latest broker snapshot for locates, shortable shares, and indicative rates. Unknown means IBKR returned no usable borrow fields; it does not mean easy-to-borrow or hard-to-borrow." />
      <GuideItem title="SEC fundamentals" text="Latest selected XBRL observations filed and recorded by the selected clock. Fiscal period, report date, unit, form, and exact tag remain visible because duration facts can represent quarterly, year-to-date, or annual values." />
      <GuideItem title="Corporate actions" text="Most recent known split and cash-dividend ex-date available by the selected clock. These describe capital history and should be checked against the event date before comparing unadjusted price or share quantities." />
      <GuideItem title="Identifiers and provenance" text="CIK, CUSIP, FIGI, ISIN, IBKR conid, canonical entity keys, and table-level availability make cross-source mapping auditable. 'No row' is intentionally different from a numeric zero." />
    </div>
  </div></Modal>;
}

function GuideItem({ text: body, title }: { text: string; title: string }) { return <article><strong>{title}</strong><p>{body}</p></article>; }
function FactsState({ error = false, label }: { error?: boolean; label: string }) { return <div className="facts-state" data-error={error ? "true" : "false"}>{error ? <AlertTriangle size={18} /> : <Database size={18} />}<span>{label}</span></div>; }
function FactsInlineEmpty({ label }: { label: string }) { return <div className="facts-inline-empty">{label}</div>; }

function number(...values: unknown[]): number | null { for (const value of values) { const parsed = Number(value); if (value != null && value !== "" && Number.isFinite(parsed)) return parsed; } return null; }
function text(...values: unknown[]): string { for (const value of values) { const result = String(value ?? "").trim(); if (result) return result; } return ""; }
function integer(value: unknown) { const parsed = number(value); return parsed == null ? "—" : Math.round(parsed).toLocaleString("en-US"); }
function formatCount(value: number | null) { if (value == null) return "—"; return new Intl.NumberFormat("en-US", { maximumFractionDigits: value >= 1_000 ? 1 : 0, notation: value >= 1_000 ? "compact" : "standard" }).format(value); }
function formatMoney(value: number | null) { if (value == null) return "—"; return new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: value >= 1_000 ? 2 : 0, notation: value >= 1_000 ? "compact" : "standard", style: "currency" }).format(value); }
function formatNumber(value: number | null, digits = 1) { return value == null ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: digits }); }
function formatPercent(value: number | null) { return value == null ? "—" : `${value.toFixed(value < 10 ? 2 : 1)}%`; }
function formatMultiple(value: number | null) { return value == null ? "—" : `${value.toFixed(2)}×`; }
function formatSignedCount(value: number | null) { if (value == null) return "—"; return `${value > 0 ? "+" : value < 0 ? "−" : ""}${formatCount(Math.abs(value))}`; }
function ratioToPercent(value: number | null) { return value == null ? null : value * 100; }
function percentDetail(value: number | null) { return value == null ? "Provider dated" : `${formatPercent(value)} of shares`; }
function dateLabel(value: unknown, prefix = "as of") { const raw = text(value); if (!raw || raw.startsWith("1970-01-01")) return "Date unavailable"; return `${prefix} ${raw.slice(0, 10)}`; }
function previousLabel(value: unknown) { const date = text(value); return date ? `vs ${date.slice(0, 10)}` : "Prior publication unavailable"; }
function formatAsOf(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "Point in time" : new Intl.DateTimeFormat("en-US", { day: "2-digit", hour: "2-digit", hour12: false, minute: "2-digit", month: "short", timeZone: "America/New_York" }).format(date) + " ET"; }
function toneActivity(value: number | null) { return value == null ? "neutral" : value >= 1.5 ? "positive" : value <= .65 ? "muted" : "neutral"; }
function toneShort(value: number | null) { return value == null ? "neutral" : value >= 10 ? "warning" : "neutral"; }
function toneSigned(value: number | null, inverse = false) { if (value == null || value === 0) return "neutral"; const positive = inverse ? value < 0 : value > 0; return positive ? "positive" : "negative"; }
function borrowTone(status: string) { const normalized = status.toLowerCase(); return normalized.includes("available") || normalized.includes("easy") ? "positive" : normalized.includes("hard") || normalized.includes("unavailable") ? "negative" : "neutral"; }
function borrowValue(row: FactRecord) { const status = text(row.borrow_status); return status ? status.replaceAll("_", " ") : "No snapshot"; }
function formatBorrowRates(row: FactRecord) { const indicative = number(row.indicative_borrow_rate); const fee = number(row.fee_rate); return indicative == null && fee == null ? "—" : [indicative == null ? "" : `${formatPercent(indicative)} indicative`, fee == null ? "" : `${formatPercent(fee)} fee`].filter(Boolean).join(" · "); }
function identityDescription(row: FactRecord) { return [text(row.exchange_code), text(row.security_type, row.instrument_type), text(row.currency_code)].filter(Boolean).join(" · ") || "Canonical security identity"; }
function splitValue(row: FactRecord) { const from = number(row.last_split_from); const to = number(row.last_split_to); return from == null || to == null || from <= 0 || to <= 0 ? "—" : `${formatNumber(to)}-for-${formatNumber(from)}`; }
function dividendValue(row: FactRecord) { const amount = number(row.last_dividend_amount); if (amount == null) return "—"; const currency = text(row.dividend_currency); const prefix = currency === "USD" ? "$" : currency ? `${currency} ` : "$"; return `${prefix}${amount.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`; }
function classificationSummary(rows: FactRecord[]) { const values = [...new Set(rows.map((row) => text(row.classification_value).replaceAll("_", " ")).filter(Boolean))]; return values.slice(0, 3).join(" · ") || "—"; }
function safeExternalUrl(value: string) { if (!value) return ""; try { const url = new URL(value.startsWith("www.") ? `https://${value}` : value); return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : ""; } catch { return ""; } }
function displayHost(value: string) { try { return new URL(value).hostname.replace(/^www\./, ""); } catch { return value; } }
function friendlyIdentifier(value: unknown) { const label = text(value).replaceAll("_", " "); return label ? label.toUpperCase() : "Identifier"; }
function fundamentalMeta(row: FactRecord) { return [text(row.fiscal_period), text(row.form_type), dateLabel(row.period_end_date, "period")].filter(Boolean).join(" · "); }
function formatFundamental(row: FactRecord) { const value = number(row.value); const unit = text(row.unit_code); if (value == null) return "—"; if (unit === "USD") return formatMoney(value); if (unit.toLowerCase().includes("share")) return `$${formatNumber(value, 3)}`; return `${formatCount(value)}${unit ? ` ${unit}` : ""}`; }

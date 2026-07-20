import { AlertTriangle, ArrowDown, ArrowRight, ArrowUp, BookOpen, Building2, CalendarDays, ChartNoAxesColumnIncreasing, Database, Landmark, Scale, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api, query } from "../../api/client";
import { Modal } from "./Modal";
import { TickerIdentityWithChange, useTickerPresentations } from "./TickerIdentity";

type FactRecord = Record<string, unknown>;
type FundamentalFact = FactRecord & { label?: string };
type SourceFact = { available: boolean; label: string; table: string };
type MetricChange = { current?: number; current_at?: string; delta?: number; direction: "down" | "flat" | "unavailable" | "up"; previous?: number; previous_at?: string };
type MetricHistoryPoint = { at: string; value: number; [key: string]: unknown };
type MetricHistoryPayload = { as_of: string; label: string; metric: string; points: MetricHistoryPoint[]; row_count: number; status: "not_found" | "ready"; symbol: string; truncated: boolean; unit: string };
type MetricDescriptor = { label: string; metric: string };
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
  metric_changes: Record<string, MetricChange>;
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
  const [historyMetric, setHistoryMetric] = useState<MetricDescriptor | null>(null);
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
  const metricChanges = payload?.metric_changes ?? {};
  const classifications = facts.classifications ?? [];
  const companyName = text(identity.branding_name, identity.issuer_name, identity.legal_name, identity.security_name) || presentations[symbol]?.issuer_name || symbol;
  const sharesOutstanding = number(float.shares_outstanding, market.share_class_shares_outstanding, market.weighted_shares_outstanding);
  const freeFloat = number(float.free_float);
  const shortPercent = number(shortInterest.percent_of_float) ?? number(shortInterest.percent_of_outstanding);
  const shortPercentBasis = number(shortInterest.percent_of_float) != null ? "of float" : number(shortInterest.percent_of_outstanding) != null ? "of shares" : "unavailable";
  const compactFacts = useMemo(() => [
    { detail: dateLabel(market.observed_at_utc), label: "Market cap", metric: "market_cap", value: formatMoney(number(market.market_cap)) },
    { detail: freeFloat != null ? percentDetail(number(float.free_float_percent)) : "Not published", label: "Free float", metric: "free_float", value: formatCount(freeFloat) },
    { detail: dateLabel(float.effective_date ?? market.observed_at_utc), label: "Shares out", metric: "shares_outstanding", value: formatCount(sharesOutstanding) },
    { detail: `${formatCount(number(volume.average_volume_20d))} 20D avg`, label: "Latest volume", metric: "daily_volume", value: formatCount(number(volume.latest_volume)) },
    { detail: "vs 20 daily sessions", label: "Relative volume", metric: "relative_volume_20d", tone: toneActivity(number(volume.relative_volume_20d)), value: formatMultiple(number(volume.relative_volume_20d)) },
    { detail: shortPercentBasis, label: "Short interest", metric: "short_interest", tone: toneShort(shortPercent), value: formatPercent(shortPercent) },
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
              {compactFacts.map((fact) => <FactMetric {...fact} change={metricChanges[fact.metric]} key={fact.label} onHistory={() => setHistoryMetric({ label: fact.label, metric: fact.metric })} />)}
            </section>
            {(payload?.warnings.length || Object.keys(payload?.errors ?? {}).length) ? <FactsNotice errors={payload?.errors ?? {}} warnings={payload?.warnings ?? []} /> : null}
            <div className="facts-evidence-grid">
              <FactSection icon={Scale} subtitle="Positioning, locate evidence, and dated short activity" title="Short & borrow">
                <div className="facts-detail-grid">
                  <FactDatum change={metricChanges.short_interest} label="Short shares" meta={dateLabel(shortInterest.settlement_date, "settled")} onHistory={() => setHistoryMetric({ label: "Short interest", metric: "short_interest" })} value={formatCount(number(shortInterest.short_interest))} />
                  <FactDatum label="Change" meta={previousLabel(shortInterest.previous_settlement_date)} tone={toneSigned(number(shortInterest.change_from_previous), true)} value={formatSignedCount(number(shortInterest.change_from_previous))} />
                  <FactDatum change={metricChanges.days_to_cover} label="Days to cover" meta="SI ÷ average daily volume" onHistory={() => setHistoryMetric({ label: "Days to cover", metric: "days_to_cover" })} value={formatNumber(number(shortInterest.days_to_cover), 2)} />
                  <FactDatum change={metricChanges.short_volume_ratio} label="FINRA short volume" meta={dateLabel(shortVolume.latest_trade_date)} onHistory={() => setHistoryMetric({ label: "FINRA short-volume ratio", metric: "short_volume_ratio" })} tone={toneShort(ratioToPercent(number(shortVolume.latest_short_volume_ratio)))} value={formatPercent(ratioToPercent(number(shortVolume.latest_short_volume_ratio)))} />
                  <FactDatum change={metricChanges.short_volume_ratio_20d} label="20D short-volume ratio" meta={`${integer(shortVolume.sessions)} sessions`} onHistory={() => setHistoryMetric({ label: "20D short-volume ratio", metric: "short_volume_ratio_20d" })} tone={toneShort(ratioToPercent(number(shortVolume.ratio_20d)))} value={formatPercent(ratioToPercent(number(shortVolume.ratio_20d)))} />
                  <FactDatum label="IBKR borrow" meta={dateLabel(borrow.observed_at_utc)} tone={borrowTone(text(borrow.borrow_status))} value={borrowValue(borrow)} />
                  <FactDatum change={metricChanges.shortable_shares} label="Shortable shares" meta="Latest IBKR snapshot" onHistory={() => setHistoryMetric({ label: "IBKR shortable shares", metric: "shortable_shares" })} value={formatCount(number(borrow.shortable_shares))} />
                  <FactDatum change={metricChanges.indicative_borrow_rate} label="Indicative borrow" meta="Latest IBKR snapshot" onHistory={() => setHistoryMetric({ label: "IBKR indicative borrow rate", metric: "indicative_borrow_rate" })} value={formatPercent(number(borrow.indicative_borrow_rate))} />
                  <FactDatum change={metricChanges.fee_rate} label="Fee rate" meta="Latest IBKR snapshot" onHistory={() => setHistoryMetric({ label: "IBKR fee rate", metric: "fee_rate" })} value={formatPercent(number(borrow.fee_rate))} />
                </div>
              </FactSection>
              <FactSection icon={Landmark} subtitle="Latest SEC-reported observations available at this clock" title="Fundamentals">
                {payload?.fundamentals.length ? <div className="fundamental-list">{payload.fundamentals.map((fact) => {
                  const metric = `fundamental:${text(fact.tag).toLowerCase()}`;
                  return <article key={String(fact.label)}><div className="facts-metric-label"><span>{fact.label}</span><MetricActions change={metricChanges[metric]} label={text(fact.label)} onHistory={() => setHistoryMetric({ label: text(fact.label), metric })} /></div><strong>{formatFundamental(fact)}</strong><small>{fundamentalMeta(fact)}</small></article>;
                })}</div> : <FactsInlineEmpty label="No selected SEC-reported facts available." />}
              </FactSection>
              <FactSection icon={Building2} subtitle="Issuer, security, listing, and corporate-action context" title="Company & listing">
                <div className="facts-detail-grid company-fact-grid">
                  <FactDatum label="Issuer" value={text(identity.legal_name, identity.issuer_name)} />
                  <FactDatum label="Security" value={text(identity.security_name, identity.security_type)} />
                  <FactDatum label="Classification" value={text(identity.sic_description, identity.industry, identity.sector)} />
                  <FactDatum label="SIC" value={text(identity.sic_code)} />
                  <FactDatum label="Entity / incorporation" value={[text(identity.entity_type), text(identity.state_of_incorporation)].filter(Boolean).join(" · ") || "—"} />
                  <FactDatum label="Country" meta={text(identity.company_country_source) || "Issuer country not published"} value={countryLabel(identity.company_country_code)} />
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
    {historyMetric ? <FactHistoryModal asOf={asOf} descriptor={historyMetric} onClose={() => setHistoryMetric(null)} symbol={symbol} /> : null}
  </section>;
}

function FactMetric({ change, detail, label, onHistory, tone = "neutral", value }: { change?: MetricChange; detail: string; label: string; onHistory: () => void; tone?: string; value: string }) {
  return <article className="facts-primary-metric" data-tone={tone}><div className="facts-metric-label"><span>{label}</span><MetricActions change={change} label={label} onHistory={onHistory} /></div><strong>{value}</strong><small>{detail}</small></article>;
}

function FactSection({ children, className = "", icon: Icon, subtitle, title }: { children: ReactNode; className?: string; icon: typeof Building2; subtitle: string; title: string }) {
  return <section className={`facts-section${className ? ` ${className}` : ""}`}><header><Icon size={14} /><span><strong>{title}</strong><small>{subtitle}</small></span></header>{children}</section>;
}

function FactDatum({ change, label, meta, onHistory, tone = "neutral", value }: { change?: MetricChange; label: string; meta?: string; onHistory?: () => void; tone?: string; value: string }) {
  return <article className="facts-datum" data-tone={tone}><div className="facts-metric-label"><span>{label}</span>{onHistory ? <MetricActions change={change} label={label} onHistory={onHistory} /> : null}</div><strong title={value}>{value || "—"}</strong>{meta ? <small>{meta}</small> : null}</article>;
}

function MetricActions({ change, label, onHistory }: { change?: MetricChange; label: string; onHistory: () => void }) {
  const direction = change?.direction ?? "unavailable";
  const Arrow = direction === "up" ? ArrowUp : direction === "down" ? ArrowDown : ArrowRight;
  const comparison = change?.previous == null
    ? "No earlier reported value"
    : `${direction === "up" ? "Increased" : direction === "down" ? "Decreased" : "Unchanged"} from ${formatTrendValue(change.previous)}${change.previous_at ? ` on ${String(change.previous_at).slice(0, 10)}` : ""}`;
  return <span className="facts-metric-actions">
    <i aria-label={`${label}: ${comparison}`} data-direction={direction} title={`${comparison}. This is numeric movement, not a bullish or bearish judgment.`}><Arrow size={11} /></i>
    <button aria-label={`Chart history for ${label}`} onClick={onHistory} title={`Plot all available reported values for ${label}`} type="button"><ChartNoAxesColumnIncreasing size={12} /></button>
  </span>;
}

function FactHistoryModal({ asOf, descriptor, onClose, symbol }: { asOf: string; descriptor: MetricDescriptor; onClose: () => void; symbol: string }) {
  const [history, setHistory] = useState<MetricHistoryPayload | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setHistory(null);
    setHistoryError("");
    setHistoryLoading(true);
    api<MetricHistoryPayload>(`/api/trading/ticker-facts/${encodeURIComponent(symbol)}/history/${encodeURIComponent(descriptor.metric)}${query({ as_of: asOf })}`, { signal: controller.signal, timeoutMs: 60000 })
      .then((payload) => { if (!controller.signal.aborted) setHistory(payload); })
      .catch((reason) => { if (!controller.signal.aborted) setHistoryError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false); });
    return () => controller.abort();
  }, [asOf, descriptor.metric, symbol]);
  return <Modal className="facts-history-modal" onClose={onClose} title={`${symbol} · ${history?.label || descriptor.label}`}>
    <div className="facts-history-content">
      {historyLoading ? <FactsState label="Loading reported history…" />
        : historyError ? <FactsState error label={historyError} />
          : history?.points.length ? <FactHistoryChart history={history} />
            : <FactsState label="No historical observations are available for this metric." />}
    </div>
  </Modal>;
}

function FactHistoryChart({ history }: { history: MetricHistoryPayload }) {
  const points = history.points.filter((point) => Number.isFinite(Number(point.value)));
  const values = points.map((point) => Number(point.value));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const range = Math.max(Math.abs(maxValue - minValue), Math.max(Math.abs(maxValue), 1) * 0.02);
  const width = 900;
  const height = 310;
  const left = 72;
  const right = 22;
  const top = 24;
  const bottom = 52;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const x = (index: number) => left + (points.length === 1 ? chartWidth / 2 : index / (points.length - 1) * chartWidth);
  const y = (value: number) => top + (maxValue + range * 0.08 - value) / (range * 1.16) * chartHeight;
  const linePoints = points.map((point, index) => `${x(index).toFixed(2)},${y(Number(point.value)).toFixed(2)}`).join(" ");
  const first = points[0];
  const latest = points[points.length - 1];
  const previous = points.length > 1 ? points[points.length - 2] : null;
  const delta = previous ? Number(latest.value) - Number(previous.value) : null;
  const tickIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  return <>
    <header className="facts-history-summary">
      <span><small>Latest</small><strong>{formatHistoryValue(Number(latest.value), history.unit)}</strong><em>{formatHistoryDate(latest.at)}</em></span>
      <span><small>Prior reported</small><strong>{previous ? formatHistoryValue(Number(previous.value), history.unit) : "—"}</strong><em>{previous ? formatHistoryDate(previous.at) : "No prior value"}</em></span>
      <span data-direction={delta == null ? "unavailable" : delta > 0 ? "up" : delta < 0 ? "down" : "flat"}><small>Change</small><strong>{delta == null ? "—" : `${delta > 0 ? "+" : ""}${formatHistoryValue(delta, history.unit)}`}</strong><em>Numeric movement</em></span>
      <span><small>History</small><strong>{history.row_count.toLocaleString()}</strong><em>{history.truncated ? "Latest bounded history" : "All available values"}</em></span>
    </header>
    <div className="facts-history-chart" role="img" aria-label={`${history.label} history from ${formatHistoryDate(first.at)} to ${formatHistoryDate(latest.at)}`}>
      <svg preserveAspectRatio="none" viewBox={`0 0 ${width} ${height}`}>
        {[0, 0.5, 1].map((ratio) => {
          const lineY = top + ratio * chartHeight;
          const value = maxValue + range * 0.08 - ratio * range * 1.16;
          return <g key={ratio}><line className="facts-history-grid" x1={left} x2={width - right} y1={lineY} y2={lineY} /><text className="facts-history-y-label" x={left - 9} y={lineY + 4}>{formatHistoryValue(value, history.unit)}</text></g>;
        })}
        <polyline className="facts-history-line" fill="none" points={linePoints} />
        {points.length <= 120 ? points.map((point, index) => <circle className="facts-history-point" cx={x(index)} cy={y(Number(point.value))} key={`${point.at}-${index}`} r="2.5"><title>{`${formatHistoryDate(point.at)} · ${formatHistoryValue(Number(point.value), history.unit)}`}</title></circle>) : null}
        {tickIndexes.map((index) => <text className="facts-history-x-label" key={index} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"} x={x(index)} y={height - 14}>{formatHistoryDate(points[index].at)}</text>)}
      </svg>
    </div>
    <p className="facts-history-note">Time is on the x-axis. Values use the source’s report, settlement, observation, or completed-session date. Arrows describe change from the immediately prior reported value; they are not trade signals.</p>
  </>;
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
      <GuideItem title="Arrows and history charts" text="The arrow compares a metric with its immediately prior reported observation: up means numerically higher, down lower, and a right arrow means unchanged or no prior value. It is not a bullish/bearish rating. Select the chart icon to plot every available point-in-time observation with report time on the x-axis." />
      <GuideItem title="Volume and relative volume" text="Latest completed QMD daily trade volume compared with the mean of the latest 20 completed daily sessions. Above 1× means activity is elevated; it says nothing about direction without price and flow." />
      <GuideItem title="Short interest" text="A settlement-date stock of shares sold short. Days to cover divides short interest by the publication's average daily volume. A high value can create covering pressure, but can also reflect persistent bearish positioning." />
      <GuideItem title="FINRA short volume" text="Daily off-exchange and exchange short-sale marking volume for the available FINRA venue file. It is transaction flow, not the outstanding short-interest stock, and should never be used as a substitute for short interest." />
      <GuideItem title="IBKR borrow" text="The latest broker snapshot for locates, shortable shares, and indicative rates. Unknown means IBKR returned no usable borrow fields; it does not mean easy-to-borrow or hard-to-borrow." />
      <GuideItem title="SEC fundamentals" text="Latest selected XBRL observations filed and recorded by the selected clock. Fiscal period, report date, unit, form, and exact tag remain visible because duration facts can represent quarterly, year-to-date, or annual values." />
      <GuideItem title="Corporate actions" text="Most recent known split and cash-dividend ex-date available by the selected clock. These describe capital history and should be checked against the event date before comparing unadjusted price or share quantities." />
      <GuideItem title="Company country and provenance" text="Country is the canonical issuer domicile when published, distinct from listing exchange country. CIK, CUSIP, FIGI, ISIN, IBKR conid, canonical entity keys, and table-level availability make cross-source mapping auditable. 'No row' is intentionally different from a numeric zero." />
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
function formatTrendValue(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 3, notation: Math.abs(value) >= 10_000 ? "compact" : "standard" }).format(value); }
function formatHistoryDate(value: string) { const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value); return Number.isNaN(date.getTime()) ? value.slice(0, 10) : new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "short", year: "2-digit", timeZone: "UTC" }).format(date); }
function formatHistoryValue(value: number, unit: string) { if (!Number.isFinite(value)) return "—"; const normalized = unit.toLowerCase(); if (normalized === "usd") return formatMoney(value); if (normalized === "shares") return formatCount(value); if (normalized === "percent") return `${value.toFixed(Math.abs(value) < 10 ? 2 : 1)}%`; if (normalized === "multiple") return `${value.toFixed(2)}×`; if (normalized === "days") return `${value.toFixed(2)} d`; if (normalized.includes("share")) return `$${formatNumber(value, 3)}`; return formatCount(value); }
function countryLabel(value: unknown) { const code = text(value).toUpperCase(); if (!code) return "—"; try { const display = new Intl.DisplayNames(["en"], { type: "region" }).of(code); return display && display !== code ? `${display} · ${code}` : code; } catch { return code; } }
function identityDescription(row: FactRecord) { return [text(row.exchange_code), text(row.security_type, row.instrument_type), text(row.currency_code)].filter(Boolean).join(" · ") || "Canonical security identity"; }
function splitValue(row: FactRecord) { const from = number(row.last_split_from); const to = number(row.last_split_to); return from == null || to == null || from <= 0 || to <= 0 ? "—" : `${formatNumber(to)}-for-${formatNumber(from)}`; }
function dividendValue(row: FactRecord) { const amount = number(row.last_dividend_amount); if (amount == null) return "—"; const currency = text(row.dividend_currency); const prefix = currency === "USD" ? "$" : currency ? `${currency} ` : "$"; return `${prefix}${amount.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`; }
function classificationSummary(rows: FactRecord[]) { const values = [...new Set(rows.map((row) => text(row.classification_value).replaceAll("_", " ")).filter(Boolean))]; return values.slice(0, 3).join(" · ") || "—"; }
function safeExternalUrl(value: string) { if (!value) return ""; try { const url = new URL(value.startsWith("www.") ? `https://${value}` : value); return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : ""; } catch { return ""; } }
function displayHost(value: string) { try { return new URL(value).hostname.replace(/^www\./, ""); } catch { return value; } }
function friendlyIdentifier(value: unknown) { const label = text(value).replaceAll("_", " "); return label ? label.toUpperCase() : "Identifier"; }
function fundamentalMeta(row: FactRecord) { return [text(row.fiscal_period), text(row.form_type), dateLabel(row.period_end_date, "period")].filter(Boolean).join(" · "); }
function formatFundamental(row: FactRecord) { const value = number(row.value); const unit = text(row.unit_code); if (value == null) return "—"; if (unit === "USD") return formatMoney(value); if (unit.toLowerCase().includes("share")) return `$${formatNumber(value, 3)}`; return `${formatCount(value)}${unit ? ` ${unit}` : ""}`; }

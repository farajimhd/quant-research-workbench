import { Activity, AlertTriangle, ArrowDown, ArrowRight, ArrowUp, BookOpen, Building2, CalendarDays, ChartNoAxesColumnIncreasing, CircleDollarSign, Clock3, Database, Droplets, Gauge, HelpCircle, Landmark, Layers3, List, Scale, ShieldCheck, Sparkles, TrendingDown, TrendingUp } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { api, query } from "../../api/client";
import { Modal } from "./Modal";
import { TickerIdentityWithChange, useTickerPresentations } from "./TickerIdentity";

type FactRecord = Record<string, unknown>;
type Freshness = { available_at: string; status: "new" | "recent" };
type FundamentalFact = FactRecord & { description?: string; freshness?: Freshness | null; label?: string; tag?: string };
type SourceFact = { available: boolean; label: string; table: string };
type MetricChange = { current?: number; current_at?: string; delta?: number; direction: "down" | "flat" | "unavailable" | "up"; previous?: number; previous_at?: string };
type MetricHistoryPoint = { at: string; value: number; [key: string]: unknown };
type HealthComparison = { at?: string; label?: string; period: string; score?: number; tone?: string };
type MetricHistoryPayload = { as_of: string; comparisons?: HealthComparison[]; label: string; metric: string; points: MetricHistoryPoint[]; row_count: number; status: "not_found" | "ready"; symbol: string; truncated: boolean; unit: string };
type MetricDescriptor = { label: string; metric: string };
type FactSectionGuideId = "company" | "fundamentals" | "identifiers" | "provenance" | "short_borrow";
type SynthesisEvidence = { explanation: string; label: string; observed_at?: string; type: "derived" | "estimated" | "reported" | string; unit: string; value: number };
type SynthesisCard = { confidence: string; decision_inputs?: { label: string; score?: number; weight: number }[]; evidence: SynthesisEvidence[]; id: string; label: string; method: string; risk_score?: number; title: string; tone: string; unit: string; value?: number; [key: string]: unknown };
type HealthComponent = { label: string; score?: number; weight: number };
type HealthSummary = { as_of: string; components: HealthComponent[]; confidence: string; coverage_percent: number; label: string; score?: number; tone: string };
type FundamentalFacet = { coverage_percent: number; id: string; label: string; score?: number; strength: string; tone: string };
type DerivedFundamental = { available_at?: string; formula: string; id: string; label: string; period_end_date?: string; unit: string; value: number };
type FundamentalAnalysis = { coverage_percent: number; facets: FundamentalFacet[]; label: string; metrics: DerivedFundamental[]; score?: number; tone: string; version: string };
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
  fundamental_analysis?: FundamentalAnalysis;
  freshness: Record<string, Freshness>;
  identifiers: FactRecord[];
  metric_changes: Record<string, MetricChange>;
  synthesis?: { cards: SynthesisCard[]; health: HealthSummary; profile_summary: string; version: string };
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
  const [metricGuide, setMetricGuide] = useState<SynthesisCard | "health" | null>(null);
  const [sectionGuide, setSectionGuide] = useState<FactSectionGuideId | null>(null);
  const [healthHistory, setHealthHistory] = useState<MetricHistoryPayload | null>(null);
  const [fundamentalsOpen, setFundamentalsOpen] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [inViewport, setInViewport] = useState(true);
  const containerRef = useRef<HTMLElement | null>(null);
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
  }, [asOf, refreshTick, symbol]);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setInViewport(entry.isIntersecting), { threshold: 0 });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (inViewport && document.visibilityState === "visible") setRefreshTick((current) => current + 1);
    }, 300_000);
    return () => window.clearInterval(interval);
  }, [inViewport]);

  useEffect(() => {
    if (!payload?.synthesis?.health) return;
    const controller = new AbortController();
    setHealthHistory(null);
    api<MetricHistoryPayload>(`/api/trading/ticker-facts/${encodeURIComponent(symbol)}/history/health_score${query({ as_of: asOf })}`, { signal: controller.signal, timeoutMs: 60000 })
      .then((next) => { if (!controller.signal.aborted) setHealthHistory(next); })
      .catch(() => { if (!controller.signal.aborted) setHealthHistory(null); });
    return () => controller.abort();
  }, [asOf, payload?.synthesis?.health?.as_of, symbol]);

  const facts = payload?.facts ?? {};
  const identity = facts.identity ?? {};
  const market = facts.market ?? {};
  const float = facts.float ?? {};
  const shortInterest = facts.short_interest ?? {};
  const shortVolume = facts.short_volume ?? {};
  const volume = facts.volume ?? {};
  const borrow = facts.borrow ?? {};
  const metricChanges = payload?.metric_changes ?? {};
  const freshness = payload?.freshness ?? {};
  const classifications = facts.classifications ?? [];
  const synthesisCards = payload?.synthesis?.cards ?? [];
  const health = payload?.synthesis?.health;
  const fundamentalAnalysis = payload?.fundamental_analysis;
  const primaryDerivedFundamentals = selectPrimaryDerivedFundamentals(fundamentalAnalysis?.metrics ?? []);
  const companyName = text(identity.branding_name, identity.issuer_name, identity.legal_name, identity.security_name) || presentations[symbol]?.issuer_name || symbol;
  const companyCountry = countryName(identity.company_country_code);
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

  return <section className="stock-facts" data-status={payload?.status ?? (loading ? "loading" : "error")} ref={containerRef}>
    <header className="facts-header">
      <div className="facts-header-identity">
        <TickerIdentityWithChange asOf={asOf} inputAriaLabel="Facts ticker" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} />
        <span><strong title={[companyName, companyCountry].filter(Boolean).join(" · ")}><span>{companyName}</span>{companyCountry ? <em>· {companyCountry}</em> : null}</strong><small>{identityDescription(identity)}</small></span>
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
            {health ? <HealthOverview health={health} history={healthHistory} onGuide={() => setMetricGuide("health")} onHistory={() => setHistoryMetric({ label: "Stock health", metric: "health_score" })} profile={payload?.synthesis?.profile_summary ?? ""} /> : null}
            {synthesisCards.length ? <section aria-label="Synthesized stock profile" className="facts-synthesis-grid">
              {synthesisCards.map((card) => <SynthesisMetricCard card={card} key={card.id} onGuide={() => setMetricGuide(card)} />)}
            </section> : <section aria-label="Primary stock facts" className="facts-primary-grid">
              {compactFacts.map((fact) => <FactMetric {...fact} change={metricChanges[fact.metric]} freshness={freshness[fact.metric]} key={fact.label} onHistory={() => setHistoryMetric({ label: fact.label, metric: fact.metric })} />)}
            </section>}
            {(payload?.warnings.length || Object.keys(payload?.errors ?? {}).length) ? <FactsNotice errors={payload?.errors ?? {}} warnings={payload?.warnings ?? []} /> : null}
            <div className="facts-evidence-grid">
              <FactSection icon={Scale} onGuide={() => setSectionGuide("short_borrow")} subtitle="Positioning, locate evidence, and dated short activity" title="Short & borrow">
                <div className="facts-detail-grid">
                  <FactDatum change={metricChanges.short_interest} freshness={freshness.short_interest} label="Short shares" meta={dateLabel(shortInterest.settlement_date, "settled")} onHistory={() => setHistoryMetric({ label: "Short interest", metric: "short_interest" })} value={formatCount(number(shortInterest.short_interest))} />
                  <FactDatum label="Change" meta={previousLabel(shortInterest.previous_settlement_date)} tone={toneSigned(number(shortInterest.change_from_previous), true)} value={formatSignedCount(number(shortInterest.change_from_previous))} />
                  <FactDatum change={metricChanges.days_to_cover} freshness={freshness.days_to_cover} label="Days to cover" meta="SI ÷ average daily volume" onHistory={() => setHistoryMetric({ label: "Days to cover", metric: "days_to_cover" })} value={formatNumber(number(shortInterest.days_to_cover), 2)} />
                  <FactDatum change={metricChanges.short_volume_ratio} freshness={freshness.short_volume_ratio} label="FINRA short volume" meta={dateLabel(shortVolume.latest_trade_date)} onHistory={() => setHistoryMetric({ label: "FINRA short-volume ratio", metric: "short_volume_ratio" })} tone={toneShort(ratioToPercent(number(shortVolume.latest_short_volume_ratio)))} value={formatPercent(ratioToPercent(number(shortVolume.latest_short_volume_ratio)))} />
                  <FactDatum change={metricChanges.short_volume_ratio_20d} freshness={freshness.short_volume_ratio_20d} label="20D short-volume ratio" meta={`${integer(shortVolume.sessions)} sessions`} onHistory={() => setHistoryMetric({ label: "20D short-volume ratio", metric: "short_volume_ratio_20d" })} tone={toneShort(ratioToPercent(number(shortVolume.ratio_20d)))} value={formatPercent(ratioToPercent(number(shortVolume.ratio_20d)))} />
                  <FactDatum freshness={freshness.borrow} label="IBKR borrow" meta={dateLabel(borrow.observed_at_utc)} tone={borrowTone(text(borrow.borrow_status))} value={borrowValue(borrow)} />
                  <FactDatum change={metricChanges.shortable_shares} freshness={freshness.shortable_shares} label="Shortable shares" meta="Latest IBKR snapshot" onHistory={() => setHistoryMetric({ label: "IBKR shortable shares", metric: "shortable_shares" })} value={formatCount(number(borrow.shortable_shares))} />
                  <FactDatum change={metricChanges.indicative_borrow_rate} freshness={freshness.indicative_borrow_rate} label="Indicative borrow" meta="Latest IBKR snapshot" onHistory={() => setHistoryMetric({ label: "IBKR indicative borrow rate", metric: "indicative_borrow_rate" })} value={formatPercent(number(borrow.indicative_borrow_rate))} />
                  <FactDatum change={metricChanges.fee_rate} freshness={freshness.fee_rate} label="Fee rate" meta="Latest IBKR snapshot" onHistory={() => setHistoryMetric({ label: "IBKR fee rate", metric: "fee_rate" })} value={formatPercent(number(borrow.fee_rate))} />
                </div>
              </FactSection>
              <FactSection className="facts-fundamentals-section" icon={Landmark} onGuide={() => setSectionGuide("fundamentals")} subtitle="SEC-reported evidence and aligned strength measures" title="Fundamentals">
                {fundamentalAnalysis ? <FundamentalStrength analysis={fundamentalAnalysis} /> : null}
                {primaryDerivedFundamentals.length ? <section className="fundamental-decision-strip"><header><span><Sparkles size={12} /><strong>Decision metrics</strong></span><small>Derived from aligned SEC observations</small></header><div>{primaryDerivedFundamentals.map((metric) => <DerivedFundamentalCard compact key={metric.id} metric={metric} />)}</div></section> : null}
                {(payload?.fundamentals.length ?? 0) || fundamentalAnalysis?.metrics.length ? <button className="facts-show-all" onClick={() => setFundamentalsOpen(true)} type="button"><List size={13} /> Show all fundamentals <span>{(payload?.fundamentals.length ?? 0) + (fundamentalAnalysis?.metrics.length ?? 0)}</span></button> : <FactsInlineEmpty label="No SEC-reported or derived fundamentals available." />}
              </FactSection>
              <FactSection className="facts-company-section" icon={Building2} onGuide={() => setSectionGuide("company")} subtitle="Issuer, security, listing, and corporate-action context" title="Company & listing">
                <div className="facts-detail-grid company-fact-grid">
                  <FactDatum className="company-primary-datum" label="Issuer" value={text(identity.legal_name, identity.issuer_name)} />
                  <FactDatum className="company-primary-datum" label="Security" value={text(identity.security_name, identity.security_type)} />
                  <FactDatum className="company-wide-datum" label="Classification" value={text(identity.sic_description, identity.industry, identity.sector)} />
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
              <FactSection className="facts-identifiers-section" icon={ShieldCheck} onGuide={() => setSectionGuide("identifiers")} subtitle="Canonical cross-provider keys grouped by entity" title="Identifiers">
                {payload?.identifiers.length ? <IdentifierGroups identifiers={payload.identifiers} /> : <FactsInlineEmpty label="No canonical identifiers available." />}
              </FactSection>
              <FactSection className="facts-source-section" icon={Database} onGuide={() => setSectionGuide("provenance")} subtitle="Availability reflects this ticker and selected clock" title="Data provenance">
                <div className="facts-source-list">{payload?.sources.map((source) => <article data-available={source.available ? "true" : "false"} key={source.label}><i aria-hidden="true" /><span><strong>{source.label}</strong><small>{source.table}</small></span><em>{source.available ? "Available" : "No row"}</em></article>)}</div>
              </FactSection>
            </div>
          </div>}
    {guideOpen ? <FactsGuide onClose={() => setGuideOpen(false)} /> : null}
    {metricGuide ? <SynthesisGuide card={metricGuide === "health" ? null : metricGuide} health={metricGuide === "health" ? health : undefined} onClose={() => setMetricGuide(null)} /> : null}
    {sectionGuide ? <FactSectionGuide fundamentals={payload?.fundamentals ?? []} onClose={() => setSectionGuide(null)} section={sectionGuide} /> : null}
    {fundamentalsOpen && payload ? <FundamentalsModal analysis={fundamentalAnalysis} facts={payload.fundamentals} metricChanges={metricChanges} onClose={() => setFundamentalsOpen(false)} onHistory={setHistoryMetric} /> : null}
    {historyMetric ? <FactHistoryModal asOf={asOf} descriptor={historyMetric} onClose={() => setHistoryMetric(null)} symbol={symbol} /> : null}
  </section>;
}

const SYNTHESIS_ICONS = {
  financial_trajectory: Activity,
  share_base: Layers3,
  short_crowding: Gauge,
  tradable_supply: Droplets,
  trading_liquidity: Scale,
  valuation: CircleDollarSign,
} as const;

const SYNTHESIS_GUIDES: Record<string, { read: string; calculation: string; caution: string }> = {
  tradable_supply: {
    read: "The best point-in-time estimate of shares that can realistically trade. Reported float remains the headline when available; the independent SEC-derived estimate is still shown so disagreement is visible.",
    calculation: "Priority: reported provider float; otherwise SEC public-float market value divided by the close on the SEC measurement date and adjusted for later splits. Shares outstanding is only an upper bound. The market-cap-implied count is a cross-check for outstanding shares, not float.",
    caution: "SEC public float is a market value measured on a filing-specific date. The share estimate can be distorted by price choice, dual classes, restricted ownership, stale filings, corporate actions, or provider definitions. Estimated values therefore include a range and never replace a reported value silently.",
  },
  short_crowding: {
    read: "Crowding estimates how difficult and potentially unstable the outstanding short position is. The colored badge is the final risk label: low, normal, elevated, high, or extreme.",
    calculation: "The risk score combines short interest as a percentage of tradable supply (40%), days to cover (20%), borrow cost (15%), borrow availability (5%), fails to deliver relative to supply (10%), and Reg SHO threshold status (10%). Missing inputs are excluded and reduce coverage. FINRA short-sale volume remains separate flow evidence and is not added to short interest.",
    caution: "High crowding is not automatically bearish. It can represent persistent negative positioning and also create squeeze risk. Settlement dates, borrow snapshots, and daily flow arrive on different schedules, so always inspect freshness and coverage.",
  },
  trading_liquidity: {
    read: "Trading liquidity describes how readily shares normally change hands. The headline is 20-session average share turnover relative to the tradable-share estimate.",
    calculation: "The liquidity score combines log-scaled 20-session average dollar volume (60%) and average daily shares divided by tradable supply (40%). If float is unavailable, shares outstanding is the clearly disclosed denominator fallback.",
    caution: "High historical liquidity does not guarantee low impact during a halt, gap, news shock, or outside regular hours. This is completed-session context, not a replacement for live spread and depth.",
  },
  share_base: {
    read: "Share-base pressure shows whether ownership is being diluted or concentrated through issuance, compensation, conversions, and repurchases.",
    calculation: "The latest SEC or provider shares-outstanding observation is compared with the nearest comparable observation at least 300 days earlier. More than +5% is rapid expansion; +1% to +5% expanding; within ±1% stable; below −1% contracting.",
    caution: "Splits change the nominal share count without economic dilution. The evidence card therefore retains dates and source values; future versions should also reconcile every historical point to a split-adjusted basis where required.",
  },
  financial_trajectory: {
    read: "Financial trajectory summarizes reported operating quality rather than displaying unrelated filing values with equal weight.",
    calculation: "The score combines profitability (45%), cash generation (30%), and balance-sheet resilience (25%). Those components use comparable revenue and income growth, positive operating and net income, operating cash flow, free cash flow, cash, debt, and liabilities. Only available evidence is scored; missing evidence lowers coverage.",
    caution: "SEC facts may be annual, quarterly, or year-to-date. The service prioritizes comparable annual observations for growth, keeps filing dates, and does not use information published after the selected clock.",
  },
  valuation: {
    read: "Valuation regime puts the current completed price in relation to the latest comparable reported earnings.",
    calculation: "The preferred proxy is latest completed close divided by latest comparable annual diluted EPS. Market capitalization divided by comparable annual net income is the fallback. The result is explicitly historical/FY based, not forward P/E.",
    caution: "A low multiple can reflect risk and a high multiple can reflect expected growth. Negative earnings make P/E economically meaningless. Relative sector and own-history percentiles require reliable peer history and are not invented when unavailable.",
  },
};

type DetailGuideItem = { label: string; text: string };

const SHORT_BORROW_GUIDE: DetailGuideItem[] = [
  { label: "Short shares", text: "Exchange-reported open short positions on the settlement date. This is a stock of outstanding positions, not daily short-sale activity." },
  { label: "Change", text: "Short shares minus the previous settlement's short shares. Positive means the reported short position grew; it is numeric movement, not a price forecast." },
  { label: "Days to cover", text: "Short shares divided by the publication's average daily volume. Higher values imply more normal trading days would be needed to cover, assuming volume and behavior stay comparable." },
  { label: "FINRA short volume", text: "The latest session's short-sale-marked FINRA volume divided by total FINRA volume. It measures transaction flow and must not be added to open short interest." },
  { label: "20D short-volume ratio", text: "Short-sale-marked FINRA volume divided by total FINRA volume across up to 20 completed sessions. Aggregating volumes before dividing prevents a low-volume day from receiving equal weight." },
  { label: "IBKR borrow", text: "The latest broker-specific locate or borrow status. It can change intraday and does not represent every lender or prime broker." },
  { label: "Shortable shares", text: "Shares IBKR reported as available to short at the latest snapshot. This is volatile broker inventory, not total market-wide lendable supply." },
  { label: "Indicative borrow", text: "IBKR's indicative annualized borrow rate at the latest snapshot. It is an estimate, not a guaranteed execution rate." },
  { label: "Fee rate", text: "The latest annualized borrow fee field returned by IBKR. The actual client rate can differ by account, inventory, timing, and broker terms." },
];

const FUNDAMENTAL_GUIDE: DetailGuideItem[] = [
  { label: "Revenue", text: "Top-line sales recognized for the reported fiscal period. Compare only equivalent quarterly, year-to-date, or annual durations." },
  { label: "Gross profit", text: "Revenue less direct cost of goods or services. It helps assess product economics, but reporting definitions vary by industry." },
  { label: "Net income", text: "Profit attributable after operating costs, interest, taxes, and other reported items for the fiscal period." },
  { label: "Diluted EPS", text: "Net earnings per diluted weighted-average share. Dilution from options and convertibles is included when economically applicable." },
  { label: "Operating income", text: "Income from core operations before financing and most non-operating items. It is not the same as cash generated." },
  { label: "Operating cash flow", text: "Cash generated or consumed by operating activities. Compare with net income to assess cash conversion." },
  { label: "Capital expenditure", text: "Cash paid for property, plant, and equipment. It is an investment outflow; subtracting it from operating cash flow approximates free cash flow." },
  { label: "Cash", text: "Reported cash and cash equivalents at the balance-sheet date. Restricted cash may be included only when the selected SEC tag says so." },
  { label: "Assets", text: "Total reported resources on the balance sheet. Scale alone is not quality; composition and returns matter." },
  { label: "Liabilities", text: "Reported obligations at the balance-sheet date. When only current liabilities are available, the exact SEC tag remains visible to avoid implying total liabilities." },
  { label: "Stockholders' equity", text: "Assets minus liabilities attributable to shareholders, subject to the selected SEC taxonomy tag and noncontrolling-interest treatment." },
  { label: "Long-term debt", text: "Noncurrent interest-bearing debt due beyond the current reporting horizon." },
  { label: "Current debt", text: "Long-term borrowings due within the current reporting horizon; it helps identify near-term refinancing needs." },
  { label: "Common shares outstanding", text: "Common shares legally outstanding at the reported instant. It is not free float and differs from period-average share counts." },
  { label: "Weighted average basic shares", text: "Time-weighted basic shares used in basic EPS for a reporting period." },
  { label: "Weighted average diluted shares", text: "Time-weighted shares used in diluted EPS after including dilutive instruments when applicable." },
  { label: "SEC public float value", text: "Market value of voting and non-voting common equity held by non-affiliates on the issuer's disclosed measurement date. It is dollars, not shares." },
  { label: "Dividends per share", text: "Cash dividends declared per common share for the reported period. Confirm period duration before annualizing." },
  { label: "Share repurchases", text: "Cash spent repurchasing common stock during the reported period. It does not directly state the number of shares retired." },
  { label: "Repurchased shares", text: "Shares repurchased and retired during the reported period under the selected SEC tag." },
];

const SECTION_GUIDES: Record<Exclude<FactSectionGuideId, "fundamentals" | "short_borrow">, { intro: string; items: DetailGuideItem[]; title: string }> = {
  company: { title: "Company & listing metrics", intro: "Issuer identity, listing, classification, and corporate-action fields describe what is being traded and where it belongs. Missing means not published by the current source, not a negative value.", items: [
    { label: "Issuer", text: "Canonical legal or provider issuer name." }, { label: "Security", text: "Security name and instrument type for the ticker." }, { label: "Classification", text: "Industry or sector description, prioritizing the canonical issuer classification." }, { label: "SIC", text: "SEC Standard Industrial Classification code." }, { label: "Entity / incorporation", text: "Legal entity type and jurisdiction of incorporation." }, { label: "Country", text: "Canonical issuer domicile when published; it is distinct from exchange country." }, { label: "Product taxonomy", text: "Provider product or thematic classifications; multiple values may apply." }, { label: "Exchange / currency", text: "Primary listing venue code and quoted currency." }, { label: "Listed", text: "Known listing start date for this security." }, { label: "IBKR conid", text: "Interactive Brokers contract identifier used for broker reconciliation." }, { label: "Tradability", text: "Whether the canonical reference currently permits trading, with an exclusion reason when blocked." }, { label: "Company website", text: "Provider-supplied issuer website." }, { label: "Investor relations", text: "Provider-supplied investor-relations website." }, { label: "Last split", text: "Most recent known split ratio and effective date available at the selected clock." }, { label: "Last dividend", text: "Most recent known cash-dividend amount and ex-date available at the selected clock." },
  ] },
  identifiers: { title: "Identifier metrics", intro: "Identifiers reconcile the same issuer and security across SEC, market-data, and broker sources. They are keys, not trading signals.", items: [
    { label: "CIK", text: "SEC Central Index Key for the filing entity." }, { label: "FIGI", text: "Financial Instrument Global Identifier for an instrument or share class." }, { label: "CUSIP", text: "North American security identifier; licensing and point-in-time coverage may vary." }, { label: "ISIN", text: "International Securities Identification Number." }, { label: "Canonical entity/security keys", text: "Internal stable keys used to preserve identity across ticker changes and providers." }, { label: "Source system", text: "System that supplied or asserted the identifier, retained for audit and conflict resolution." },
  ] },
  provenance: { title: "Data provenance metrics", intro: "This section is an availability audit for the selected ticker and point-in-time clock. It separates a missing source row from a reported numeric zero.", items: [
    { label: "Available", text: "At least one usable source row was resolved for this ticker by the selected clock." }, { label: "No row", text: "No usable row was resolved. It does not mean the underlying economic value is zero." }, { label: "Table name", text: "The authoritative database relation queried for that source domain." }, { label: "Selected clock", text: "Point-in-time boundary that prevents later reports or observations from leaking into the displayed snapshot." },
  ] },
};

function HealthOverview({ health, history, onGuide, onHistory, profile }: { health: HealthSummary; history: MetricHistoryPayload | null; onGuide: () => void; onHistory: () => void; profile: string }) {
  return <section className="facts-health" data-tone={health.tone}>
    <div className="facts-health-summary">
      <span className="facts-eyebrow">Evidence-weighted stock health</span>
      <div className="facts-health-value"><strong>{health.score == null ? "—" : Math.round(health.score)}</strong>{health.score != null ? <small>/100</small> : null}<HealthIcon tone={health.tone} /></div>
      <div className="facts-health-badges"><ToneBadge label={health.label} tone={health.tone} /><ConfidenceBadge confidence={health.confidence} /><span>{Math.round(health.coverage_percent)}% coverage</span></div>
      <p>{profile || "The summary will appear when sufficient point-in-time evidence is available."}</p>
      <div className="facts-health-actions"><button onClick={onGuide} type="button"><HelpCircle size={13} /> How calculated</button><button onClick={onHistory} type="button"><ChartNoAxesColumnIncreasing size={13} /> Full history</button></div>
    </div>
    <HealthSparkline history={history} />
    <div className="facts-health-components">
      <header><span>Health score composition</span><small>Six weighted inputs · score /100</small></header>
      <div>{[...health.components].sort((left, right) => right.weight - left.weight).map((component) => {
        const tone = componentScoreTone(component.score);
        return <article aria-label={`${component.label}: ${component.score == null ? "unavailable" : `${Math.round(component.score)} out of 100`}; ${component.weight}% weight`} data-tone={tone} key={component.label}>
          <span>{component.label}<small>{component.weight}% weight</small></span><strong>{component.score == null ? "—" : `${Math.round(component.score)}/100`}</strong><i aria-hidden="true"><b style={{ width: `${Math.max(0, Math.min(100, component.score ?? 0))}%` }} /></i>
        </article>;
      })}</div>
    </div>
  </section>;
}

function HealthSparkline({ history }: { history: MetricHistoryPayload | null }) {
  const gradientId = `facts-health-gradient-${useId().replace(/:/g, "")}`;
  const points = (history?.points ?? []).filter((point) => Number.isFinite(Number(point.value)));
  const width = 360; const height = 82; const pad = 8;
  const timestamps = points.map((point) => Date.parse(point.at));
  const firstTimestamp = timestamps[0] ?? 0; const lastTimestamp = timestamps[timestamps.length - 1] ?? firstTimestamp;
  const x = (index: number) => pad + (points.length <= 1 || firstTimestamp === lastTimestamp ? (width - pad * 2) / 2 : (timestamps[index] - firstTimestamp) / (lastTimestamp - firstTimestamp) * (width - pad * 2));
  const line = points.map((point, index) => {
    const y = height - pad - Number(point.value) / 100 * (height - pad * 2);
    return `${x(index).toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const area = line ? `${pad},${height - pad} ${line} ${width - pad},${height - pad}` : "";
  const axisPoints = points.length ? [points[0], points[Math.floor((points.length - 1) / 2)], points[points.length - 1]] : [];
  const currentScore = points.length ? Number(points[points.length - 1].value) : null;
  return <div className="facts-health-history">
    <header><span>Historical health trajectory</span><small>{points.length ? `${formatHistoryDate(points[0].at)} → ${formatHistoryDate(points[points.length - 1].at)}` : "Loading history…"}</small></header>
    <div className="facts-health-sparkline">{points.length ? <svg aria-label="Historical stock health trajectory" preserveAspectRatio="none" role="img" viewBox={`0 0 ${width} ${height}`}><defs><linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1"><stop className="facts-area-stop-strong" offset="0%" /><stop className="facts-area-stop-mid" offset="62%" /><stop className="facts-area-stop-clear" offset="100%" /></linearGradient></defs><line x1="0" x2={width} y1={height / 2} y2={height / 2} /><polygon className="facts-health-area" points={area} style={{ fill: `url(#${gradientId})` }} /><polyline fill="none" points={line} /></svg> : <span>Historical score is loaded separately so the current facts are not blocked.</span>}</div>
    {axisPoints.length ? <div className="facts-health-axis" aria-hidden="true">{axisPoints.map((point, index) => <span key={`${point.at}-${index}`}>{formatHealthAxisDate(point.at)}</span>)}</div> : null}
    <div className="facts-health-comparisons">{(history?.comparisons ?? []).map((item) => <HealthComparisonCard currentScore={currentScore} item={item} key={item.period} />)}</div>
  </div>;
}

function HealthComparisonCard({ currentScore, item }: { currentScore: number | null; item: HealthComparison }) {
  const score = item.score == null ? null : Number(item.score);
  const delta = currentScore == null || score == null ? null : currentScore - score;
  const DirectionIcon = delta == null || Math.abs(delta) < .5 ? ArrowRight : delta > 0 ? ArrowUp : ArrowDown;
  const direction = delta == null ? "unavailable" : Math.abs(delta) < .5 ? "flat" : delta > 0 ? "up" : "down";
  return <article data-tone={item.tone ?? "muted"}>
    <header><span>{item.period} ago</span><time dateTime={item.at || undefined}>{item.at ? formatHealthAxisDate(item.at) : "No date"}</time></header>
    <div><strong>{score == null ? "—" : Math.round(score)}{score == null ? null : <small>/100</small>}</strong><ToneBadge label={item.label || "Unavailable"} tone={item.tone || "muted"} /></div>
    <footer data-direction={direction}><DirectionIcon size={10} /><span>{delta == null ? "No comparison" : Math.abs(delta) < .5 ? "Unchanged now" : `${delta > 0 ? "+" : ""}${Math.round(delta)} to current`}</span></footer>
  </article>;
}

function SynthesisMetricCard({ card, onGuide }: { card: SynthesisCard; onGuide: () => void }) {
  const Icon = SYNTHESIS_ICONS[card.id as keyof typeof SYNTHESIS_ICONS] ?? Activity;
  const decisionInputs = card.decision_inputs ?? [];
  return <article className="facts-synthesis-card" data-card={card.id} data-tone={card.tone}>
    <header><span><Icon size={15} />{card.title}</span><button aria-label={`Guide for ${card.title}`} onClick={onGuide} type="button"><HelpCircle size={14} /></button></header>
    <div className="facts-synthesis-value"><strong>{formatSynthesisValue(card.value, card.unit)}</strong><Icon aria-hidden="true" size={20} /></div>
    <div className="facts-synthesis-badges"><ToneBadge label={card.label} tone={card.tone} /><ConfidenceBadge confidence={card.confidence} /><span>{friendlyMethod(card.method)}</span></div>
    <p>{cardSubtitle(card)}</p>
    <details className="facts-card-evidence"><summary>How this decision was reached</summary>
      {decisionInputs.length ? <div className="facts-decision-list">{decisionInputs.map((input) => <article key={input.label}><span>{input.label}<small>{input.weight}% weight</small></span><strong>{input.score == null ? "Missing" : Math.round(input.score)}</strong><i><b style={{ width: `${Math.max(0, Math.min(100, input.score ?? 0))}%` }} /></i></article>)}</div> : null}
      <div className="facts-evidence-list">{card.evidence.slice(0, 5).map((item) => <article key={`${card.id}-${item.label}`}><span>{item.label}<small>{item.type} · {dateLabel(item.observed_at)}</small></span><strong>{formatEvidence(item.value, item.unit)}</strong></article>)}</div>
    </details>
  </article>;
}

function ToneBadge({ label, tone }: { label: string; tone: string }) { return <em className="facts-tone-badge" data-tone={tone}>{label}</em>; }
function ConfidenceBadge({ confidence }: { confidence: string }) { return <em className="facts-confidence-badge" data-confidence={confidence}>{confidence} confidence</em>; }
function HealthIcon({ tone }: { tone: string }) { return tone === "positive" ? <TrendingUp size={20} /> : tone === "negative" ? <TrendingDown size={20} /> : <Activity size={20} />; }

function SynthesisGuide({ card, health, onClose }: { card: SynthesisCard | null; health?: HealthSummary; onClose: () => void }) {
  if (!card && health) return <Modal className="facts-guide-modal facts-metric-guide" onClose={onClose} title="How to read: Stock health"><div className="facts-guide-content">
    <div className="facts-guide-intro"><strong>{health.label}{health.score == null ? "" : ` · ${Math.round(health.score)}/100`}</strong><p>Stock health is an evidence-weighted context score, not a buy/sell signal. It asks whether the company and its trading conditions appear robust using only information available at each historical clock.</p></div>
    <div className="facts-guide-grid"><GuideItem title="Calculation" text="Profitability contributes 25%, cash generation 20%, balance-sheet resilience 20%, share-base discipline 15%, trading liquidity 10%, and short/settlement resilience 10%. Missing components are excluded from the numeric average and reduce evidence coverage." /><GuideItem title="Labels" text="Robust is 80–100, Healthy 65–79, Mixed 45–64, Fragile 25–44, and Stressed below 25. A label is withheld when coverage is below 70%." /><GuideItem title="Historical chart" text="Every monthly point is recalculated using only facts that had been published or observed by that date. Later filings do not repaint earlier health. The one-month, three-month, and one-year labels use the nearest available historical point." /><GuideItem title="Do not overread" text="Health measures operating, balance-sheet, capital-structure, liquidity, and crowding resilience. It does not predict the next price move and intentionally excludes valuation from the health score." /></div>
    <HealthComponentGuide components={health.components} />
  </div></Modal>;
  if (!card) return null;
  const guide = SYNTHESIS_GUIDES[card.id];
  return <Modal className="facts-guide-modal facts-metric-guide" onClose={onClose} title={`How to read: ${card.title}`}><div className="facts-guide-content">
    <div className="facts-guide-intro"><strong><ToneBadge label={card.label} tone={card.tone} /> {formatSynthesisValue(card.value, card.unit)}</strong><p>{guide?.read}</p></div>
    <div className="facts-guide-grid"><GuideItem title="Calculation" text={guide?.calculation || "The value is deterministically derived from the evidence shown below."} /><GuideItem title="Interpretation limits" text={guide?.caution || "Retain the source date and confidence when using this metric."} /><GuideItem title="Evidence type" text="Reported means provider-published. Derived means an exact formula over aligned reported inputs. Estimated means a proxy with uncertainty. Upper bounds are never displayed as exact estimates." /><GuideItem title="Confidence" text="Confidence reflects evidence coverage, reconciliation, and whether the headline is reported or estimated. It is separate from whether the metric is favorable or risky." /></div>
    <div className="facts-guide-evidence">{card.evidence.map((item) => <article key={item.label}><span><strong>{item.label}</strong><small>{item.type} · {dateLabel(item.observed_at)}</small></span><b>{formatEvidence(item.value, item.unit)}</b><p>{item.explanation}</p></article>)}</div>
  </div></Modal>;
}

const HEALTH_COMPONENT_EXPLANATIONS: Record<string, string> = {
  "Balance-sheet resilience": "Scores cash relative to debt and liabilities, plus positive equity when available. Higher means more balance-sheet capacity; missing line items reduce coverage.",
  "Cash generation": "Scores positive operating cash flow and free cash flow, where free cash flow is operating cash flow minus capital expenditure.",
  Profitability: "Scores positive net and operating income plus comparable revenue and income growth. It measures reported operating quality, not valuation.",
  "Share-base discipline": "Scores long-horizon change in comparable shares outstanding. Expansion lowers the score, stability is neutral-to-strong, and contraction raises it after split effects are considered.",
  "Short / settlement resilience": "Inverts deterministic short-crowding risk. Lower crowding, borrow pressure, fails-to-deliver pressure, and Reg SHO risk produce a higher resilience score.",
  "Trading liquidity": "Combines 20-session average dollar volume and daily turnover relative to tradable supply. Higher means the stock has historically absorbed more trading activity.",
};

function HealthComponentGuide({ components }: { components: HealthComponent[] }) { return <div className="facts-guide-evidence">{components.map((component) => <article key={component.label}><span><strong>{component.label}</strong><small>{component.weight}% of final score</small></span><b>{component.score == null ? "Unavailable" : `${Math.round(component.score)}/100`}</b><p>{HEALTH_COMPONENT_EXPLANATIONS[component.label] || "Missing evidence lowers coverage and is not silently treated as a neutral score."}</p></article>)}</div>; }

const PRIMARY_DERIVED_FUNDAMENTAL_IDS = ["free_cash_flow", "operating_margin", "revenue_growth", "current_ratio", "interest_coverage", "debt_to_equity", "net_margin", "return_on_equity"];

function selectPrimaryDerivedFundamentals(metrics: DerivedFundamental[]) {
  const byId = new Map(metrics.map((metric) => [metric.id, metric]));
  return PRIMARY_DERIVED_FUNDAMENTAL_IDS.map((id) => byId.get(id)).filter((metric): metric is DerivedFundamental => Boolean(metric)).slice(0, 4);
}

function derivedFundamentalSignal(metric: DerivedFundamental): { label: string; tone: "negative" | "neutral" | "positive" | "warning" } {
  const value = metric.value;
  if (["free_cash_flow", "operating_margin", "net_margin", "gross_margin", "return_on_assets", "return_on_equity", "revenue_growth", "earnings_growth", "working_capital"].includes(metric.id)) {
    return value > 0 ? { label: "Supportive", tone: "positive" } : value < 0 ? { label: "Pressure", tone: "negative" } : { label: "Neutral", tone: "neutral" };
  }
  if (["share_count_growth", "diluted_share_growth"].includes(metric.id)) {
    return value > 0 ? { label: "Dilutive", tone: "negative" } : value < 0 ? { label: "Accretive", tone: "positive" } : { label: "Stable", tone: "neutral" };
  }
  if (metric.id === "current_ratio") return value >= 1.5 ? { label: "Liquid", tone: "positive" } : value >= 1 ? { label: "Adequate", tone: "neutral" } : { label: "Caution", tone: "warning" };
  if (metric.id === "debt_to_equity") return value <= 1 ? { label: "Contained", tone: "positive" } : value <= 2 ? { label: "Elevated", tone: "warning" } : { label: "High leverage", tone: "negative" };
  if (metric.id === "interest_coverage") return value >= 3 ? { label: "Covered", tone: "positive" } : value >= 1 ? { label: "Thin cover", tone: "warning" } : { label: "Uncovered", tone: "negative" };
  if (metric.id === "cash_conversion") return value >= 1 ? { label: "Strong conversion", tone: "positive" } : value >= 0 ? { label: "Partial conversion", tone: "neutral" } : { label: "Cash pressure", tone: "negative" };
  return { label: "Context", tone: "neutral" };
}

function DerivedFundamentalCard({ compact = false, metric }: { compact?: boolean; metric: DerivedFundamental }) {
  const signal = derivedFundamentalSignal(metric);
  return <article className="derived-fundamental-card" data-compact={compact ? "true" : "false"} data-tone={signal.tone}>
    <header><span>{metric.label}</span><em>{signal.label}</em></header>
    <strong>{formatDerivedFundamental(metric)}</strong>
    <small>{metric.formula}</small>
    {!compact ? <footer>{dateLabel(metric.period_end_date, "period")}</footer> : null}
  </article>;
}

function FundamentalStrength({ analysis }: { analysis: FundamentalAnalysis }) {
  return <section className="fundamental-strength" data-tone={analysis.tone}>
    <header><span><Sparkles size={14} /><small>Financial strength</small><strong>{analysis.label}</strong></span><b>{analysis.score == null ? "—" : `${Math.round(analysis.score)}/100`}</b><em>{Math.round(analysis.coverage_percent)}% evidence</em></header>
    <div className="fundamental-facet-grid">{analysis.facets.map((facet) => {
      const score = facet.score == null ? null : Math.round(facet.score);
      const angle = Math.max(0, Math.min(100, facet.score ?? 0)) * 3.6;
      return <article aria-label={`${facet.label}: ${score == null ? "unavailable" : `${score} out of 100`}, ${facet.strength}`} data-tone={facet.tone} key={facet.id}>
        <i aria-hidden="true" className="fundamental-facet-gauge" style={{ "--facet-angle": `${angle}deg` } as CSSProperties}><b>{score ?? "—"}</b></i>
        <span><strong>{facet.label}</strong><small>{facet.strength}</small></span>
      </article>;
    })}</div>
  </section>;
}

function FundamentalsModal({ analysis, facts, metricChanges, onClose, onHistory }: { analysis?: FundamentalAnalysis; facts: FundamentalFact[]; metricChanges: Record<string, MetricChange>; onClose: () => void; onHistory: (descriptor: MetricDescriptor) => void }) {
  return <Modal className="facts-guide-modal facts-fundamentals-modal" onClose={onClose} title="Complete SEC fundamentals">
    <div className="facts-guide-content">
      {analysis ? <><section className="fundamental-modal-hero" data-tone={analysis.tone}><div><small>SEC-derived operating profile</small><span><strong>{analysis.label}</strong><b>{analysis.score == null ? "—" : `${Math.round(analysis.score)}/100`}</b></span><p>The strength label combines aligned profitability, growth, cash quality, balance-sheet, and capital-discipline evidence. It is operating context, not a price forecast.</p></div><em>{Math.round(analysis.coverage_percent)}% evidence coverage</em></section><FundamentalStrength analysis={analysis} /></> : null}
      {analysis?.metrics.length ? <section className="fundamental-modal-section derived-decision-section"><header><span><Sparkles size={16} /><strong>Derived decision metrics</strong></span><small>Primary interpretation layer · exact formulas over aligned SEC observations</small></header><div className="derived-fundamental-grid">{analysis.metrics.map((metric) => <DerivedFundamentalCard key={metric.id} metric={metric} />)}</div></section> : null}
      <section className="fundamental-modal-section reported-fundamental-section"><header><span><Landmark size={14} /><strong>Reported SEC observations</strong></span><small>{facts.length} supporting standardized concepts</small></header><div className="fundamental-modal-list">{facts.map((fact) => {
        const label = text(fact.label); const metric = `fundamental:${text(fact.tag).toLowerCase()}`;
        return <article key={`${label}-${text(fact.tag)}`}><MetricLabel freshness={fact.freshness ?? undefined} label={label} onHistory={() => onHistory({ label, metric })} /><MetricValue change={metricChanges[metric]} label={label} value={formatFundamental(fact)} /><p>{text(fact.description)}</p><small>{fundamentalMeta(fact)} · {text(fact.taxonomy)}:{text(fact.tag)}</small></article>;
      })}</div></section>
      <p className="facts-history-note">Segment and geographic breakdowns are not fabricated from company-level totals. They require dimensional XBRL contexts, which are not present in the current company-fact contract, and will appear only after that separate canonical relationship is available.</p>
    </div>
  </Modal>;
}

function IdentifierGroups({ identifiers }: { identifiers: FactRecord[] }) {
  const groups = ["issuer", "security"].map((entity) => ({ entity, rows: identifiers.filter((item) => text(item.entity).toLowerCase() === entity) })).filter((group) => group.rows.length);
  const ungrouped = identifiers.filter((item) => !["issuer", "security"].includes(text(item.entity).toLowerCase()));
  if (ungrouped.length) groups.push({ entity: "other", rows: ungrouped });
  return <div className="identifier-groups">{groups.map((group) => <section key={group.entity}><header>{group.entity === "issuer" ? "Issuer keys" : group.entity === "security" ? "Security keys" : "Other keys"}<small>{group.rows.length}</small></header><div>{group.rows.map((item, index) => <article key={`${text(item.entity)}-${text(item.identifier_kind)}-${index}`}><span>{friendlyIdentifier(item.identifier_kind)}<FreshnessBadge freshness={item.freshness as Freshness | undefined} /></span><strong title={text(item.identifier_value)}>{text(item.identifier_value)}</strong><small>{text(item.source_system)}</small></article>)}</div></section>)}</div>;
}

function FreshnessBadge({ freshness }: { freshness?: Freshness | null }) {
  if (!freshness) return null;
  const label = freshness.status === "new" ? "New" : "Recent";
  return <em className="facts-freshness" data-status={freshness.status} title={`${label} as of ${formatHistoryDate(freshness.available_at)}`}><Clock3 size={9} />{label}</em>;
}

function FactSectionGuide({ fundamentals, onClose, section }: { fundamentals: FundamentalFact[]; onClose: () => void; section: FactSectionGuideId }) {
  const visibleFundamentals = new Set(fundamentals.map((fact) => text(fact.label)));
  const definition = section === "short_borrow"
    ? { intro: "Short-position stock, short-sale flow, and broker inventory are different measurements with different clocks. Read their dates and never add them together.", items: SHORT_BORROW_GUIDE, title: "Short & borrow metrics" }
    : section === "fundamentals"
      ? { intro: "Each value is the latest selected SEC XBRL observation known at the chosen clock. The fiscal period, form, period end, unit, and exact tag determine whether two values are comparable.", items: fundamentals.length ? fundamentals.map((fact) => ({ label: text(fact.label), text: text(fact.description) || "SEC-reported XBRL observation; inspect the exact tag, unit, and fiscal period before comparison." })) : FUNDAMENTAL_GUIDE, title: "Fundamental metrics" }
      : SECTION_GUIDES[section];
  return <Modal className="facts-guide-modal facts-section-guide" onClose={onClose} title={`How to read: ${definition.title}`}><div className="facts-guide-content">
    <div className="facts-guide-intro"><strong>Metric-by-metric reference</strong><p>{definition.intro}</p></div>
    <div className="facts-guide-evidence facts-detail-guide-list">{definition.items.map((item) => <article data-visible={section !== "fundamentals" || visibleFundamentals.has(item.label) ? "true" : "false"} key={item.label}><span><strong>{item.label}</strong>{section === "fundamentals" ? <small>{visibleFundamentals.has(item.label) ? "Visible at this clock" : "Supported when reported"}</small> : null}</span><p>{item.text}</p></article>)}</div>
    {section === "fundamentals" ? <p className="facts-history-note">Duration facts may be quarterly, year-to-date, or annual; instant facts are balance-sheet snapshots. The card metadata is part of the metric and should remain attached when comparing history.</p> : null}
  </div></Modal>;
}

function FactMetric({ change, detail, freshness, label, onHistory, tone = "neutral", value }: { change?: MetricChange; detail: string; freshness?: Freshness; label: string; onHistory: () => void; tone?: string; value: string }) {
  return <article className="facts-primary-metric" data-tone={tone}><MetricLabel freshness={freshness} label={label} onHistory={onHistory} /><MetricValue change={change} label={label} value={value} /><small>{detail}</small></article>;
}

function FactSection({ children, className = "", icon: Icon, onGuide, subtitle, title }: { children: ReactNode; className?: string; icon: typeof Building2; onGuide?: () => void; subtitle: string; title: string }) {
  return <section className={`facts-section${className ? ` ${className}` : ""}`}><header><Icon size={14} /><span><strong>{title}</strong><small>{subtitle}</small></span>{onGuide ? <button aria-label={`Guide for ${title}`} onClick={onGuide} title={`Explain every ${title} metric`} type="button"><HelpCircle size={14} /> Guide</button> : null}</header>{children}</section>;
}

function FactDatum({ change, className = "", freshness, label, meta, onHistory, tone = "neutral", value }: { change?: MetricChange; className?: string; freshness?: Freshness; label: string; meta?: string; onHistory?: () => void; tone?: string; value: string }) {
  return <article className={`facts-datum${className ? ` ${className}` : ""}`} data-tone={tone}>{onHistory ? <MetricLabel freshness={freshness} label={label} onHistory={onHistory} /> : <span>{label}<FreshnessBadge freshness={freshness} /></span>}{onHistory ? <MetricValue change={change} label={label} title={value} value={value || "—"} /> : <strong title={value}>{value || "—"}</strong>}{meta ? <small>{meta}</small> : null}</article>;
}

function MetricLabel({ freshness, label, onHistory }: { freshness?: Freshness; label: string; onHistory?: () => void }) {
  return <div className="facts-metric-label"><span>{label}<FreshnessBadge freshness={freshness} /></span>{onHistory ? <button aria-label={`Chart history for ${label}`} onClick={onHistory} title={`Plot all available reported values for ${label}`} type="button"><ChartNoAxesColumnIncreasing size={12} /></button> : null}</div>;
}

function MetricValue({ change, label, title, value }: { change?: MetricChange; label: string; title?: string; value: string }) {
  const direction = change?.direction ?? "unavailable";
  const Arrow = direction === "up" ? ArrowUp : direction === "down" ? ArrowDown : ArrowRight;
  const comparison = change?.previous == null
    ? "No earlier reported value"
    : `${direction === "up" ? "Increased" : direction === "down" ? "Decreased" : "Unchanged"} from ${formatTrendValue(change.previous)}${change.previous_at ? ` on ${String(change.previous_at).slice(0, 10)}` : ""}`;
  return <div className="facts-metric-value" data-direction={direction}><i aria-label={`${label}: ${comparison}`} data-direction={direction} title={`${comparison}. This is numeric movement, not a bullish or bearish judgment.`}><Arrow size={12} /></i><strong title={title}>{value}</strong></div>;
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
  const gradientId = `facts-history-gradient-${useId().replace(/:/g, "")}`;
  const points = history.points.filter((point) => Number.isFinite(Number(point.value)));
  const values = points.map((point) => Number(point.value));
  const isScore = history.unit.toLowerCase() === "score";
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const rawRange = Math.max(Math.abs(rawMax - rawMin), Math.max(Math.abs(rawMax), 1) * 0.02);
  const minValue = isScore ? 0 : rawMin - rawRange * 0.08;
  const maxValue = isScore ? 100 : rawMax + rawRange * 0.08;
  const range = maxValue - minValue;
  const width = 900;
  const height = 310;
  const left = 72;
  const right = 22;
  const top = 24;
  const bottom = 52;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const x = (index: number) => left + (points.length === 1 ? chartWidth / 2 : index / (points.length - 1) * chartWidth);
  const y = (value: number) => top + (maxValue - value) / range * chartHeight;
  const linePoints = points.map((point, index) => `${x(index).toFixed(2)},${y(Number(point.value)).toFixed(2)}`).join(" ");
  const areaPoints = `${x(0).toFixed(2)},${(top + chartHeight).toFixed(2)} ${linePoints} ${x(points.length - 1).toFixed(2)},${(top + chartHeight).toFixed(2)}`;
  const first = points[0];
  const latest = points[points.length - 1];
  const previous = points.length > 1 ? points[points.length - 2] : null;
  const delta = previous ? Number(latest.value) - Number(previous.value) : null;
  const tickIndexes = [...new Set([0, Math.round((points.length - 1) * .25), Math.round((points.length - 1) * .5), Math.round((points.length - 1) * .75), points.length - 1])];
  return <>
    <header className="facts-history-summary">
      <span><small>Latest</small><strong>{formatHistoryValue(Number(latest.value), history.unit)}</strong><em>{formatHistoryDate(latest.at)}</em></span>
      <span><small>Prior reported</small><strong>{previous ? formatHistoryValue(Number(previous.value), history.unit) : "—"}</strong><em>{previous ? formatHistoryDate(previous.at) : "No prior value"}</em></span>
      <span data-direction={delta == null ? "unavailable" : delta > 0 ? "up" : delta < 0 ? "down" : "flat"}><small>Change</small><strong>{delta == null ? "—" : `${delta > 0 ? "+" : ""}${formatHistoryValue(delta, history.unit)}`}</strong><em>Numeric movement</em></span>
      <span><small>History</small><strong>{history.row_count.toLocaleString()}</strong><em>{history.truncated ? "Latest bounded history" : "All available values"}</em></span>
    </header>
    <div className="facts-history-chart" role="img" aria-label={`${history.label} history from ${formatHistoryDate(first.at)} to ${formatHistoryDate(latest.at)}`}>
      <svg preserveAspectRatio="none" viewBox={`0 0 ${width} ${height}`}>
        <defs><linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1"><stop className="facts-area-stop-strong" offset="0%" /><stop className="facts-area-stop-mid" offset="58%" /><stop className="facts-area-stop-clear" offset="100%" /></linearGradient></defs>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const lineY = top + ratio * chartHeight;
          const value = maxValue - ratio * range;
          return <g key={ratio}><line className="facts-history-grid" x1={left} x2={width - right} y1={lineY} y2={lineY} /><text className="facts-history-y-label" x={left - 9} y={lineY + 4}>{formatHistoryValue(value, history.unit)}</text></g>;
        })}
        {tickIndexes.map((index) => <line className="facts-history-grid facts-history-grid-vertical" key={`grid-${index}`} x1={x(index)} x2={x(index)} y1={top} y2={top + chartHeight} />)}
        <polygon className="facts-history-area" points={areaPoints} style={{ fill: `url(#${gradientId})` }} />
        <polyline className="facts-history-line" fill="none" points={linePoints} />
        {points.length <= 120 ? points.map((point, index) => <circle className="facts-history-point" cx={x(index)} cy={y(Number(point.value))} key={`${point.at}-${index}`} r="2.5"><title>{`${formatHistoryDate(point.at)} · ${formatHistoryValue(Number(point.value), history.unit)}`}</title></circle>) : null}
        {tickIndexes.map((index) => <text className="facts-history-x-label" key={index} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"} x={x(index)} y={height - 14}>{formatHistoryDate(points[index].at)}</text>)}
      </svg>
    </div>
    {history.comparisons?.length ? <div className="facts-history-comparisons">{history.comparisons.map((item) => <article key={item.period}><small>{item.period}</small><strong>{item.score == null ? "—" : `${Math.round(item.score)}/100`}</strong><ToneBadge label={item.label || "Unavailable"} tone={item.tone || "muted"} /></article>)}</div> : null}
    <p className="facts-history-note">Time is on the x-axis. Values use the source’s report, settlement, observation, or completed-session date. Health history uses a fixed 0–100 scale and recalculates each monthly point only from evidence available at that date; it does not repaint the past.</p>
  </>;
}

function FactLink({ label, value }: { label: string; value: string }) {
  const href = safeExternalUrl(value);
  return <article className="facts-datum"><span>{label}</span>{href ? <a href={href} rel="noreferrer" target="_blank" title={href}>{displayHost(href)}</a> : <strong>—</strong>}<small>{href ? "Provider-supplied link" : "Not published"}</small></article>;
}

function FactsNotice({ errors, warnings }: { errors: Record<string, string>; warnings: string[] }) {
  const issueCount = warnings.length + Object.keys(errors).length;
  return <details className="facts-notice"><summary><AlertTriangle size={12} /><strong>{issueCount} data note{issueCount === 1 ? "" : "s"}</strong><span>Estimates and bounds are explicitly labeled</span></summary><div>{warnings.map((warning) => <p key={warning}>{warning}</p>)}{Object.entries(errors).map(([source, message]) => <p key={source}><b>{source}:</b> {message}</p>)}</div></details>;
}

function FactsGuide({ onClose }: { onClose: () => void }) {
  return <Modal className="facts-guide-modal" onClose={onClose} title="How to read Stock Facts"><div className="facts-guide-content">
    <div className="facts-guide-intro"><strong>Start with the synthesized profile, then inspect its evidence.</strong><p>Each large metric answers one trading-context question and keeps its source dates, calculation, confidence, and missing inputs. The page never presents an estimate as a reported fact or treats differently dated publications as simultaneous.</p></div>
    <div className="facts-guide-grid">
      <GuideItem title="Stock health" text="A direction-neutral resilience score built from profitability, cash generation, balance sheet, share-base discipline, trading liquidity, and short/settlement resilience. It is withheld below 70% evidence coverage and is not a buy/sell forecast." />
      <GuideItem title="Tradable supply reconciliation" text="Reported float remains authoritative when present, while the independent SEC-derived estimate remains visible as a comparison. If reported float is absent, the estimate is labeled and ranged. Market cap divided by price checks shares outstanding only; it does not calculate float." />
      <GuideItem title="Colored decision badges" text="Green, neutral, amber, and red badges communicate the interpretation defined for that metric. Confidence has a separate outlined badge so favorable color is never confused with evidence quality. Select the help icon on any card for its exact formula and limitations." />
      <GuideItem title="Arrows, value color, and history charts" text="The arrow and value color compare a metric with its immediately prior reported observation: green means numerically higher, red lower, and neutral means unchanged or no prior value. This is not a bullish/bearish rating because a numeric increase can have different implications for different metrics. Select the chart icon to plot every available point-in-time observation with report time on the x-axis." />
      <GuideItem title="Volume and relative volume" text="Latest completed QMD daily trade volume compared with the mean of the latest 20 completed daily sessions. Above 1× means activity is elevated; it says nothing about direction without price and flow." />
      <GuideItem title="Short interest" text="A settlement-date stock of shares sold short. Days to cover divides short interest by the publication's average daily volume. A high value can create covering pressure, but can also reflect persistent bearish positioning." />
      <GuideItem title="FINRA short volume" text="Daily off-exchange and exchange short-sale marking volume for the available FINRA venue file. It is transaction flow, not the outstanding short-interest stock, and should never be used as a substitute for short interest." />
      <GuideItem title="IBKR borrow" text="The latest broker snapshot for locates, shortable shares, and indicative rates. Unknown means IBKR returned no usable borrow fields; it does not mean easy-to-borrow or hard-to-borrow." />
      <GuideItem title="SEC fundamentals" text="Latest selected US-GAAP or IFRS XBRL observations filed and recorded by the selected clock. Fiscal period, report date, unit, form, and exact tag remain visible because duration facts can represent quarterly, year-to-date, or annual values. Open Show all fundamentals for current assets and liabilities, receivables and payables, inventory, cash flow and capex, R&D, SG&A, stock compensation, financing, tax, goodwill, intangibles, share issuance, and every other available curated concept." />
      <GuideItem title="Financial strength" text="A separate evidence-weighted label summarizes five aligned SEC facets: profitability, growth, cash quality, balance sheet, and capital discipline. Its detail view shows free cash flow, margins, returns, working capital, current ratio, leverage, net debt, interest coverage, growth, dilution, cash conversion, and expense intensity with the exact formula. Missing inputs lower coverage; they are not treated as zero." />
      <GuideItem title="Recent-data badges and refresh" text="New marks a field published within 24 hours of the selected clock; Recent covers the following six days. The page refreshes its database snapshot every five minutes only while visible, preserving responsiveness and the selected point-in-time boundary. Fields outside the recent window stay visually quiet." />
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
function componentScoreTone(value: number | undefined) { return value == null ? "muted" : value >= 65 ? "positive" : value >= 45 ? "neutral" : value >= 25 ? "warning" : "negative"; }
function borrowTone(status: string) { const normalized = status.toLowerCase(); return normalized.includes("available") || normalized.includes("easy") ? "positive" : normalized.includes("hard") || normalized.includes("unavailable") ? "negative" : "neutral"; }
function borrowValue(row: FactRecord) { const status = text(row.borrow_status); return status ? status.replaceAll("_", " ") : "No snapshot"; }
function formatBorrowRates(row: FactRecord) { const indicative = number(row.indicative_borrow_rate); const fee = number(row.fee_rate); return indicative == null && fee == null ? "—" : [indicative == null ? "" : `${formatPercent(indicative)} indicative`, fee == null ? "" : `${formatPercent(fee)} fee`].filter(Boolean).join(" · "); }
function formatTrendValue(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 3, notation: Math.abs(value) >= 10_000 ? "compact" : "standard" }).format(value); }
function formatHistoryDate(value: string) { const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value); return Number.isNaN(date.getTime()) ? value.slice(0, 10) : new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "short", year: "2-digit", timeZone: "UTC" }).format(date); }
function formatHealthAxisDate(value: string) { const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value); return Number.isNaN(date.getTime()) ? value.slice(0, 7) : new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(date); }
function formatHistoryValue(value: number, unit: string) { if (!Number.isFinite(value)) return "—"; const normalized = unit.toLowerCase(); if (normalized === "usd") return formatMoney(value); if (normalized === "shares") return formatCount(value); if (normalized === "percent") return `${value.toFixed(Math.abs(value) < 10 ? 2 : 1)}%`; if (normalized === "multiple") return `${value.toFixed(2)}×`; if (normalized === "days") return `${value.toFixed(2)} d`; if (normalized === "score") return `${Math.round(value)}/100`; if (normalized.includes("share")) return `$${formatNumber(value, 3)}`; return formatCount(value); }
function countryName(value: unknown) { const code = text(value).toUpperCase(); if (!code) return ""; try { return new Intl.DisplayNames(["en"], { type: "region" }).of(code) || code; } catch { return code; } }
function countryLabel(value: unknown) { const code = text(value).toUpperCase(); if (!code) return "—"; const display = countryName(code); return display && display !== code ? `${display} · ${code}` : code; }
function identityDescription(row: FactRecord) { return [text(row.exchange_code), text(row.security_type, row.instrument_type), text(row.currency_code)].filter(Boolean).join(" · ") || "Canonical security identity"; }
function splitValue(row: FactRecord) { const from = number(row.last_split_from); const to = number(row.last_split_to); return from == null || to == null || from <= 0 || to <= 0 ? "—" : `${formatNumber(to)}-for-${formatNumber(from)}`; }
function dividendValue(row: FactRecord) { const amount = number(row.last_dividend_amount); if (amount == null) return "—"; const currency = text(row.dividend_currency); const prefix = currency === "USD" ? "$" : currency ? `${currency} ` : "$"; return `${prefix}${amount.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`; }
function classificationSummary(rows: FactRecord[]) { const values = [...new Set(rows.map((row) => text(row.classification_value).replaceAll("_", " ")).filter(Boolean))]; return values.slice(0, 3).join(" · ") || "—"; }
function safeExternalUrl(value: string) { if (!value) return ""; try { const url = new URL(value.startsWith("www.") ? `https://${value}` : value); return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : ""; } catch { return ""; } }
function displayHost(value: string) { try { return new URL(value).hostname.replace(/^www\./, ""); } catch { return value; } }
function friendlyIdentifier(value: unknown) { const label = text(value).replaceAll("_", " "); return label ? label.toUpperCase() : "Identifier"; }
function fundamentalMeta(row: FactRecord) { return [text(row.fiscal_period), text(row.form_type), dateLabel(row.period_end_date, "period")].filter(Boolean).join(" · "); }
function formatFundamental(row: FactRecord) { const value = number(row.value); const unit = text(row.unit_code); if (value == null) return "—"; if (unit === "USD") return formatMoney(value); if (unit.toLowerCase().includes("share")) return `$${formatNumber(value, 3)}`; return `${formatCount(value)}${unit ? ` ${unit}` : ""}`; }
function formatDerivedFundamental(row: DerivedFundamental) { const value = number(row.value); if (value == null) return "—"; if (row.unit === "USD") return formatMoney(value); if (row.unit === "percent") return formatPercent(value); if (row.unit === "multiple") return `${formatNumber(value, 2)}×`; return formatNumber(value, 2); }
function formatSynthesisValue(value: unknown, unit: string) { const parsed = number(value); if (parsed == null) return "—"; if (unit === "shares") return formatCount(parsed); if (unit === "percent") return formatPercent(parsed); if (unit === "multiple") return `${formatNumber(parsed, 1)}×`; if (unit === "score") return `${Math.round(parsed)}/100`; if (unit === "USD") return formatMoney(parsed); return formatNumber(parsed, 2); }
function formatEvidence(value: unknown, unit: string) { const parsed = number(value); if (parsed == null) return "—"; const normalized = unit.toLowerCase(); if (normalized === "shares" || normalized === "shares/day") return formatCount(parsed); if (normalized === "percent") return formatPercent(parsed); if (normalized === "multiple") return `${formatNumber(parsed, 2)}×`; if (normalized === "days") return `${formatNumber(parsed, 2)} d`; if (normalized === "usd" || normalized === "usd/day") return formatMoney(parsed); if (normalized === "usd/share") return `$${formatNumber(parsed, 3)}`; return formatNumber(parsed, 2); }
function friendlyMethod(value: string) { return ({ derived: "Derived", estimated: "Estimated", reported: "Reported", upper_bound: "Upper bound" } as Record<string, string>)[value] || value.replaceAll("_", " "); }
function cardSubtitle(card: SynthesisCard) { if (card.id === "tradable_supply") { const reported = number(card.reported_value); const estimated = number(card.estimated_value); if (reported != null && estimated != null) return `${formatCount(reported)} reported vs ${formatCount(estimated)} independently estimated.`; if (estimated != null) return `${formatCount(number(card.lower_bound))}–${formatCount(number(card.upper_bound))} uncertainty range.`; return "Shares outstanding is retained only as an upper bound."; } if (card.id === "short_crowding") return `${formatCount(number(card.short_shares))} short shares · ${formatNumber(number(card.risk_score), 0)}/100 crowding risk.`; if (card.id === "trading_liquidity") return `${formatMoney(number(card.dollar_volume))} average dollar volume.`; if (card.id === "share_base") return "Change versus the nearest comparable observation at least 300 days earlier."; if (card.id === "financial_trajectory") return `${formatNumber(number(card.coverage_percent), 0)}% of financial evidence available.`; if (card.id === "valuation") return "Historical/FY earnings basis; not an analyst forward estimate."; return "Open the evidence to inspect the decision."; }

import { BookOpen, CalendarDays, ChevronDown, Database, HelpCircle, Sparkles, TrendingDown, TrendingUp } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";

import { api, query } from "../../api/client";
import { Modal } from "./Modal";
import { TickerIdentityWithChange, useTickerPresentations } from "./TickerIdentity";

type Point = { filed_at_utc?: string; fiscal_period?: string; period_end_date?: string; value?: number };
type Fact = Point & { accession_number?: string; change_percent?: number; change_tone?: string; description?: string; direction?: string; freshness?: { status?: string }; history?: Point[]; label?: string; tag?: string; taxonomy?: string; unit_code?: string };
type Component = { direction: string; formula: string; id: string; label: string; lower_bound: number; normalized_score?: number; unit: string; upper_bound: number; value?: number; weight: number; weighted_points?: number };
type Facet = { components: Component[]; contribution_points?: number; coverage_percent: number; effective_weight?: number; formula: string; id: string; label: string; overall_weight: number; score?: number; strength: string; tone: string };
type Metric = { available_at?: string; formula: string; id: string; label: string; period_end_date?: string; unit: string; value: number };
type Analysis = { coverage_percent: number; facets: Facet[]; formula: string; label: string; metrics: Metric[]; score?: number; tone: string };
type TimelinePoint = { accession_numbers: string[]; available_at: string; coverage_percent: number; facets: Facet[]; label: string; score?: number; tone: string };
type XbrlAnalysis = { classes: Array<{ facts: Fact[]; id: string; label: string }>; current: Analysis; decision: { delta_from_previous?: number; label: string; scope: string; tone: string }; latest_filing_at?: string; timeline: TimelinePoint[]; version: string };
type Payload = { status: string; symbol: string; warnings: string[]; xbrl_analysis?: XbrlAnalysis };

export type XbrlAnalysisSettings = { metricLimit: number; showRawTags: boolean };

export function XbrlAnalysisContainer({ asOf, onSymbolChange, settings, symbol }: { asOf: string; onSymbolChange?: (symbol: string) => void; settings: XbrlAnalysisSettings; symbol: string }) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [guideOpen, setGuideOpen] = useState(false);
  const [activeClass, setActiveClass] = useState("");
  const [activeFacet, setActiveFacet] = useState("overall");
  const [expandedFact, setExpandedFact] = useState("");
  const presentations = useTickerPresentations([symbol]);
  useEffect(() => {
    const controller = new AbortController();
    setError(""); setPayload(null);
    api<Payload>(`/api/trading/ticker-facts/${encodeURIComponent(symbol)}${query({ as_of: asOf })}`, { signal: controller.signal, timeoutMs: 45000 })
      .then((next) => { if (!controller.signal.aborted) setPayload(next); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [asOf, symbol]);
  const analysis = payload?.xbrl_analysis;
  const currentClass = analysis?.classes.find((item) => item.id === activeClass) ?? analysis?.classes[0];
  const selectedFacet = analysis?.current.facets.find((item) => item.id === activeFacet);
  useEffect(() => { if (analysis?.classes.length && !analysis.classes.some((item) => item.id === activeClass)) setActiveClass(analysis.classes[0].id); }, [activeClass, analysis]);
  const metrics = useMemo(() => analysis?.current.metrics.slice(0, Math.max(3, settings.metricLimit)) ?? [], [analysis, settings.metricLimit]);

  return <section className="xbrl-analysis" data-status={payload?.status ?? (error ? "error" : "loading")}>
    <header className="xbrl-analysis-header">
      <TickerIdentityWithChange asOf={asOf} inputAriaLabel="XBRL ticker" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} />
      <span><strong>XBRL financial quality</strong><small>Auditable operating strength from public SEC filings</small></span>
      <button onClick={() => setGuideOpen(true)} type="button"><BookOpen size={15} /> Guide</button>
    </header>
    {error ? <div className="xbrl-state" data-error="true">{error}</div> : !analysis ? <div className="xbrl-state">Loading causal filing evidence…</div> : <div className="xbrl-analysis-scroll">
      <section className="xbrl-hero" data-tone={analysis.decision.tone}>
        <div className="xbrl-score"><span>Overall financial quality</span><strong>{score(analysis.current.score)}<small>/100</small></strong><em data-tone={analysis.current.tone}>{analysis.current.label}</em><small>{Math.round(analysis.current.coverage_percent)}% of weighted evidence available</small></div>
        <div className="xbrl-decision"><span><Sparkles size={15} /> Latest filing decision</span><strong>{analysis.decision.label}</strong><b data-tone={analysis.decision.tone}>{signed(analysis.decision.delta_from_previous)} pts</b><small>Change versus the previous scored filing state</small></div>
        <div className="xbrl-freshness"><span><CalendarDays size={15} /> Evidence clock</span><strong>{dateLabel(analysis.latest_filing_at)}</strong><small>Only filings public by the selected clock are included</small></div>
      </section>

      <section className="xbrl-trajectory">
        <header><span><strong>Financial quality through filings</strong><small>Select the composite or a category; history never uses future filings.</small></span><b>{analysis.timeline.length} causal states</b></header>
        <nav aria-label="Trajectory series"><button aria-pressed={activeFacet === "overall"} onClick={() => setActiveFacet("overall")} type="button">Overall</button>{analysis.current.facets.map((facet) => <button aria-pressed={activeFacet === facet.id} key={facet.id} onClick={() => setActiveFacet(facet.id)} type="button">{facet.label}</button>)}</nav>
        <ScoreAreaChart facetId={activeFacet} points={analysis.timeline} />
      </section>

      <section className="xbrl-facets" aria-label="XBRL category scores">
        {analysis.current.facets.map((facet) => <button aria-pressed={activeFacet === facet.id} data-tone={facet.tone} key={facet.id} onClick={() => setActiveFacet(facet.id)} type="button"><span>{facet.label}<small>{facet.overall_weight}% composite weight</small></span><strong>{score(facet.score)}<small>/100</small></strong><em>{facet.strength}</em><i><b style={{ width: `${clamp(facet.coverage_percent)}%` }} /></i><small>{Math.round(facet.coverage_percent)}% evidence · {facet.contribution_points == null ? "—" : `${facet.contribution_points.toFixed(1)} pts contributed`}</small></button>)}
      </section>

      {selectedFacet ? <section className="xbrl-calculation">
        <header><span><strong>{selectedFacet.label}: closed-form calculation</strong><small>Every available input is normalized to 0–100, then combined by the visible weights.</small></span><b>{score(selectedFacet.score)}/100</b></header>
        <div>{selectedFacet.components.map((component) => <article data-tone={componentTone(component)} key={component.id}><span>{component.label}<small>{component.weight}% category weight</small></span><strong>{component.value == null ? "—" : formatMetric(component.value, component.unit)}</strong><div><b>{score(component.normalized_score)}</b><small>/100 normalized</small></div><p>{rangeText(component)}</p><footer><span>{component.direction === "lower_is_stronger" ? <TrendingDown size={13} /> : <TrendingUp size={13} />}{component.direction === "lower_is_stronger" ? "Lower scores stronger" : "Higher scores stronger"}</span><b>{component.weighted_points == null ? "—" : `${component.weighted_points.toFixed(1)} weighted pts`}</b></footer></article>)}</div>
      </section> : null}

      <section className="xbrl-metrics">
        <header><span><strong>Derived financial signals</strong><small>Aligned ratios and changes calculated from reported facts</small></span></header>
        <div>{metrics.map((metric) => <article data-tone={metricTone(metric)} key={metric.id}><span>{metric.label}</span><strong>{formatMetric(metric.value, metric.unit)}</strong><small>{metric.formula}</small><time>{dateLabel(metric.period_end_date)}</time></article>)}</div>
      </section>

      <section className="xbrl-evidence">
        <header><span><Database size={16} /><strong>Reported evidence and history</strong></span><small>Latest comparable value, change, history, and source filing</small></header>
        <nav>{analysis.classes.map((item) => <button aria-pressed={item.id === currentClass?.id} key={item.id} onClick={() => setActiveClass(item.id)} type="button">{item.label}<b>{item.facts.length}</b></button>)}</nav>
        {currentClass ? <div className="xbrl-evidence-grid">{currentClass.facts.map((fact) => { const key = `${fact.tag}-${fact.period_end_date}`; const expanded = expandedFact === key; return <article data-tone={fact.change_tone || "neutral"} key={key}><header><span>{fact.label}<small>{fact.description}</small></span>{fact.freshness ? <em data-recency={fact.freshness.status}>{fact.freshness.status === "new" ? "New" : "Recent"}</em> : null}</header><div className="xbrl-fact-value"><strong>{formatFact(fact)}</strong><span data-tone={fact.change_tone}>{changeLabel(fact.change_percent)}</span></div><MiniHistory points={fact.history ?? []} tone={fact.change_tone || "neutral"} /><footer><span>{fact.fiscal_period || "Reported"} · {dateLabel(fact.period_end_date)}</span><button aria-expanded={expanded} onClick={() => setExpandedFact(expanded ? "" : key)} type="button">Audit <ChevronDown size={13} /></button></footer>{expanded ? <dl><div><dt>Filed</dt><dd>{dateLabel(fact.filed_at_utc)}</dd></div><div><dt>Direction</dt><dd>{directionLabel(fact.direction)}</dd></div>{settings.showRawTags ? <div><dt>Taxonomy tag</dt><dd><code>{fact.taxonomy ? `${fact.taxonomy}:` : ""}{fact.tag}</code></dd></div> : null}<div><dt>Accession</dt><dd>{fact.accession_number || "—"}</dd></div></dl> : null}</article>; })}</div> : null}
      </section>
    </div>}
    {guideOpen ? <GuideModal onClose={() => setGuideOpen(false)} /> : null}
  </section>;
}

function ScoreAreaChart({ facetId, points }: { facetId: string; points: TimelinePoint[] }) {
  const id = useId().replace(/:/g, "");
  const scored = points.map((point) => ({ ...point, plotted: facetId === "overall" ? point.score : point.facets.find((facet) => facet.id === facetId)?.score })).filter((point) => point.plotted != null);
  if (scored.length < 2) return <div className="xbrl-chart-empty">At least two scored filings are required for this trajectory.</div>;
  const width = 720, height = 150, left = 34, right = 10, top = 10, bottom = 24;
  const x = (index: number) => left + index * ((width - left - right) / Math.max(1, scored.length - 1));
  const y = (value: number) => top + (100 - value) * ((height - top - bottom) / 100);
  const line = scored.map((point, index) => `${x(index)},${y(point.plotted ?? 0)}`).join(" ");
  const area = `${left},${height - bottom} ${line} ${x(scored.length - 1)},${height - bottom}`;
  return <svg aria-label="Financial quality through filing time" preserveAspectRatio="none" role="img" viewBox={`0 0 ${width} ${height}`}><defs><linearGradient id={id} x1="0" x2="0" y1="0" y2="1"><stop className="xbrl-gradient-start" offset="0%" /><stop className="xbrl-gradient-end" offset="100%" /></linearGradient></defs>{[0, 25, 50, 75, 100].map((value) => <g key={value}><line className="xbrl-chart-grid" x1={left} x2={width - right} y1={y(value)} y2={y(value)} /><text x={left - 6} y={y(value) + 3}>{value}</text></g>)}<polygon fill={`url(#${id})`} points={area} /><polyline className="xbrl-chart-line" fill="none" points={line} />{scored.map((point, index) => <circle className="xbrl-chart-point" cx={x(index)} cy={y(point.plotted ?? 0)} key={point.available_at} r="3"><title>{`${dateLabel(point.available_at)} · ${score(point.plotted)}/100`}</title></circle>)}<text className="xbrl-chart-date" x={left} y={height - 4}>{dateLabel(scored[0].available_at)}</text><text className="xbrl-chart-date" textAnchor="end" x={width - right} y={height - 4}>{dateLabel(scored[scored.length - 1].available_at)}</text></svg>;
}

function MiniHistory({ points, tone }: { points: Point[]; tone: string }) {
  const id = useId().replace(/:/g, "");
  const valid = points.filter((point) => Number.isFinite(point.value));
  if (valid.length < 2) return <div className="xbrl-mini-empty">History appears after two comparable reports.</div>;
  const values = valid.map((point) => Number(point.value)); const min = Math.min(...values); const max = Math.max(...values); const span = max - min || Math.max(Math.abs(max), 1);
  const coords = values.map((value, index) => `${index * (100 / Math.max(1, values.length - 1))},${34 - ((value - min) / span) * 28}`).join(" ");
  return <svg className="xbrl-mini-chart" data-tone={tone} preserveAspectRatio="none" viewBox="0 0 100 38"><defs><linearGradient id={id} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".34" /><stop offset="100%" stopColor="currentColor" stopOpacity=".03" /></linearGradient></defs><polygon fill={`url(#${id})`} points={`0,36 ${coords} 100,36`} /><polyline fill="none" points={coords} /><title>{`${valid.length} comparable reported observations`}</title></svg>;
}

function GuideModal({ onClose }: { onClose: () => void }) { return <Modal className="xbrl-guide-modal" onClose={onClose} title="How to read XBRL financial quality"><div className="xbrl-guide-content"><p className="xbrl-guide-intro"><strong>Objective:</strong> turn standardized SEC facts into an auditable, slow-moving view of operating quality. It answers whether reported profitability, growth, cash conversion, balance-sheet resilience, and capital discipline are strong and improving. It is not valuation, an earnings forecast, or a short-term trade signal.</p><div className="xbrl-guide-grid">
  <Guide title="Closed-form scoring" text="Each input is clamped to a documented 0–100 range. A category is the weighted mean of available component scores. The composite is the coverage-adjusted weighted mean of category scores: profitability 30%, growth 20%, cash quality 20%, balance sheet 20%, and capital discipline 10%." />
  <Guide title="Coverage and withholding" text="Coverage is the share of configured weight backed by usable facts. A category is withheld below 40% coverage and the composite below 50%. Missing evidence is not treated as zero; its category receives less effective weight." />
  <Guide title="Profitability · 30%" text="Gross margin (20%, range 10–60%), operating margin (30%, −5–25%), net margin (30%, −5–20%), and return on positive equity (20%, −10–30%). Higher is stronger." />
  <Guide title="Growth · 20%" text="Comparable-period revenue growth (55%, −10–25%) and earnings growth (45%, −25–40%). Growth can score strongly while valuation remains expensive; this category does not measure price." />
  <Guide title="Cash quality · 20%" text="Free-cash-flow margin (60%, −5–20%) and operating-cash-flow conversion of net income (40%, 0.5–1.5×). It tests whether reported earnings are supported by cash." />
  <Guide title="Balance sheet · 20%" text="Current ratio (40%, 0.5–2×), inverse debt-to-positive-equity (35%, 0–2×), and interest coverage (25%, 1–8×). Debt-to-equity is withheld when equity is nonpositive." />
  <Guide title="Capital discipline · 10%" text="Inverse basic-share growth (60%, −2–8%) and inverse diluted-versus-basic share spread (40%, 0–10%). Greater issuance or dilution lowers the category score." />
  <Guide title="Trajectory" text="Every point is recomputed using only filings public at that time. Select Overall or a category to identify persistent improvement, deterioration, or a one-filing discontinuity. Later evidence never repaints an earlier score." />
  <Guide title="Derived financial signals" text="Ratios align numerator and denominator to comparable periods. Semantic color indicates the current numeric implication, while the exact formula and source period remain visible." />
  <Guide title="Reported evidence history" text="Cards group canonical concepts by financial statement. The large value is the latest comparable report, the colored change is versus the previous comparable period, and the gradient area shows up to 12 causal observations." />
  <Guide title="Change colors" text="Green means the change is favorable for concepts with a defensible direction; red means unfavorable. Context-dependent fields such as capex, inventory, receivables, goodwill, and R&D remain neutral because higher or lower is not inherently better." />
  <Guide title="Audit details" text="Open Audit to see the filing date, directional rule, taxonomy namespace and tag, and accession. These fields explain exactly which SEC disclosure supports the displayed value." />
  <Guide title="Limitations" text="Issuer extensions, segment dimensions, restatements, fiscal-calendar changes, and accounting-policy differences can reduce comparability. Scores summarize available standardized evidence; they do not replace filing review." />
</div></div></Modal>; }

function Guide({ text, title }: { text: string; title: string }) { return <article><HelpCircle size={16} /><span><strong>{title}</strong><p>{text}</p></span></article>; }
function clamp(value: number) { return Math.max(0, Math.min(100, value)); }
function score(value?: number) { return value == null ? "—" : Math.round(value).toString(); }
function signed(value?: number) { return value == null ? "No prior" : `${value > 0 ? "+" : ""}${value.toFixed(1)}`; }
function dateLabel(value?: string) { if (!value) return "—"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "2-digit" }); }
function formatMetric(value: number, unit: string) { if (unit === "percent") return `${value.toFixed(1)}%`; if (unit === "multiple") return `${value.toFixed(2)}×`; if (unit === "USD") return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1, style: "currency", currency: "USD" }).format(value); return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(value); }
function formatFact(fact: Fact) { const value = Number(fact.value); if (!Number.isFinite(value)) return "—"; const formatted = Intl.NumberFormat(undefined, { notation: Math.abs(value) >= 100_000 ? "compact" : "standard", maximumFractionDigits: 2 }).format(value); return `${formatted}${fact.unit_code ? ` ${fact.unit_code}` : ""}`; }
function changeLabel(value?: number) { return value == null ? "No comparison" : `${value > 0 ? "+" : ""}${value.toFixed(1)}% vs prior`; }
function directionLabel(value?: string) { return value === "higher_is_stronger" ? "Higher is generally stronger" : value === "lower_is_stronger" ? "Lower is generally stronger" : "Context dependent; no directional color"; }
function rangeText(component: Component) { return `${formatMetric(component.lower_bound, component.unit)} maps to 0 · ${formatMetric(component.upper_bound, component.unit)} maps to 100`; }
function componentTone(component: Component) { const value = component.normalized_score; return value == null ? "muted" : value >= 65 ? "positive" : value < 35 ? "negative" : value < 50 ? "warning" : "neutral"; }
function metricTone(metric: Metric) { if (["debt_to_equity", "net_debt", "share_growth", "dilution", "sga_intensity"].includes(metric.id)) return metric.value > 0 ? "warning" : "positive"; if (["free_cash_flow", "gross_margin", "operating_margin", "net_margin", "revenue_growth", "earnings_growth", "current_ratio", "interest_coverage", "cash_conversion"].includes(metric.id)) return metric.value > 0 ? "positive" : "negative"; return "neutral"; }

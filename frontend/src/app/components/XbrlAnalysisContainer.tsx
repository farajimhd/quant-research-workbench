import { BookOpen, CalendarDays, Database, HelpCircle, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api, query } from "../../api/client";
import { Modal } from "./Modal";
import { TickerIdentityWithChange, useTickerPresentations } from "./TickerIdentity";

type Fact = { accession_number?: string; description?: string; filed_at_utc?: string; fiscal_period?: string; label?: string; period_end_date?: string; tag?: string; taxonomy?: string; unit_code?: string; value?: number };
type Facet = { coverage_percent: number; id: string; label: string; score?: number; strength: string; tone: string };
type Metric = { available_at?: string; formula: string; id: string; label: string; period_end_date?: string; unit: string; value: number };
type Analysis = { coverage_percent: number; facets: Facet[]; label: string; metrics: Metric[]; score?: number; tone: string };
type TimelinePoint = { accession_numbers: string[]; available_at: string; coverage_percent: number; facets: Facet[]; label: string; score?: number; tone: string };
type XbrlAnalysis = {
  classes: Array<{ facts: Fact[]; id: string; label: string }>;
  current: Analysis;
  decision: { delta_from_previous?: number; label: string; scope: string; tone: string };
  latest_filing_at?: string;
  timeline: TimelinePoint[];
  version: string;
};
type Payload = { status: string; symbol: string; warnings: string[]; xbrl_analysis?: XbrlAnalysis };

export type XbrlAnalysisSettings = { metricLimit: number; showRawTags: boolean };

export function XbrlAnalysisContainer({ asOf, onSymbolChange, settings, symbol }: { asOf: string; onSymbolChange?: (symbol: string) => void; settings: XbrlAnalysisSettings; symbol: string }) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [guideOpen, setGuideOpen] = useState(false);
  const [activeClass, setActiveClass] = useState("");
  const presentations = useTickerPresentations([symbol]);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    setPayload(null);
    api<Payload>(`/api/trading/ticker-facts/${encodeURIComponent(symbol)}${query({ as_of: asOf })}`, { signal: controller.signal, timeoutMs: 45000 })
      .then((next) => { if (!controller.signal.aborted) setPayload(next); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [asOf, symbol]);
  const analysis = payload?.xbrl_analysis;
  const currentClass = analysis?.classes.find((item) => item.id === activeClass) ?? analysis?.classes[0];
  useEffect(() => { if (analysis?.classes.length && !analysis.classes.some((item) => item.id === activeClass)) setActiveClass(analysis.classes[0].id); }, [activeClass, analysis]);
  const metrics = useMemo(() => analysis?.current.metrics.slice(0, Math.max(3, settings.metricLimit)) ?? [], [analysis, settings.metricLimit]);
  return <section className="xbrl-analysis" data-status={payload?.status ?? (error ? "error" : "loading")}>
    <header className="xbrl-analysis-header">
      <TickerIdentityWithChange asOf={asOf} inputAriaLabel="XBRL ticker" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} />
      <span><strong>SEC financial evidence</strong><small>Classified XBRL observations available at this clock</small></span>
      <button onClick={() => setGuideOpen(true)} type="button"><BookOpen size={14} /> Guide</button>
    </header>
    {error ? <div className="xbrl-state" data-error="true">{error}</div>
      : !analysis ? <div className="xbrl-state">Loading causal filing evidence…</div>
        : <div className="xbrl-analysis-scroll">
          <section className="xbrl-hero" data-tone={analysis.decision.tone}>
            <div className="xbrl-score"><span>Filing evidence score</span><strong>{score(analysis.current.score)}<small>/100</small></strong><em data-tone={analysis.current.tone}>{analysis.current.label}</em></div>
            <div className="xbrl-decision"><span><Sparkles size={13} /> Change at latest filing</span><strong>{analysis.decision.label}</strong><small>{signed(analysis.decision.delta_from_previous)} points versus the prior scored filing</small></div>
            <div className="xbrl-freshness"><span><CalendarDays size={13} /> Latest evidence</span><strong>{dateLabel(analysis.latest_filing_at)}</strong><small>{Math.round(analysis.current.coverage_percent)}% analytical coverage</small></div>
          </section>
          <section className="xbrl-trajectory">
            <header><span><strong>Financial evidence trajectory</strong><small>Causal score after each filing became public</small></span><b>{analysis.timeline.length} states</b></header>
            <ScoreAreaChart points={analysis.timeline} />
          </section>
          <section className="xbrl-facets" aria-label="XBRL decision facets">
            {analysis.current.facets.map((facet) => <article data-tone={facet.tone} key={facet.id}><span>{facet.label}<small>{Math.round(facet.coverage_percent)}% evidence</small></span><strong>{score(facet.score)}</strong><i><b style={{ width: `${Math.max(0, Math.min(100, facet.score ?? 0))}%` }} /></i><em>{facet.strength}</em></article>)}
          </section>
          <section className="xbrl-metrics">
            <header><span><strong>Actionable financial signals</strong><small>Aligned ratios and changes—not raw tag values</small></span></header>
            <div>{metrics.map((metric) => <article data-tone={metricTone(metric)} key={metric.id}><span>{metric.label}</span><strong>{formatMetric(metric.value, metric.unit)}</strong><small>{metric.formula}</small><time>{dateLabel(metric.period_end_date)}</time></article>)}</div>
          </section>
          <section className="xbrl-evidence">
            <header><span><Database size={14} /><strong>Reported evidence by XBRL class</strong></span><small>Exact taxonomy tags remain visible for audit</small></header>
            <nav>{analysis.classes.map((item) => <button aria-pressed={item.id === currentClass?.id} key={item.id} onClick={() => setActiveClass(item.id)} type="button">{item.label}<b>{item.facts.length}</b></button>)}</nav>
            {currentClass ? <div className="xbrl-fact-table"><table><thead><tr><th>Metric</th><th>Value</th><th>Period</th>{settings.showRawTags ? <th>Taxonomy tag</th> : null}<th>Filed</th></tr></thead><tbody>{currentClass.facts.map((fact) => <tr key={`${fact.tag}-${fact.period_end_date}`}><td><strong>{fact.label}</strong><small>{fact.description}</small></td><td>{formatFact(fact)}</td><td>{fact.fiscal_period || "—"}<small>{dateLabel(fact.period_end_date)}</small></td>{settings.showRawTags ? <td><code>{fact.taxonomy ? `${fact.taxonomy}:` : ""}{fact.tag}</code></td> : null}<td>{dateLabel(fact.filed_at_utc)}<small>{fact.accession_number || ""}</small></td></tr>)}</tbody></table></div> : null}
          </section>
        </div>}
    {guideOpen ? <Modal className="xbrl-guide-modal" onClose={() => setGuideOpen(false)} title="How to read SEC financial evidence"><div className="xbrl-guide-content">
      <p className="xbrl-guide-intro"><strong>This is a slow-moving financial evidence system, not a trade entry signal.</strong> It scores only facts that were publicly available at the selected clock. A higher score means the reported financial evidence is stronger across covered dimensions; it does not guarantee price appreciation.</p>
      <div className="xbrl-guide-grid">
        <Guide title="Filing evidence score" text="A coverage-weighted 0–100 composite of profitability, growth, cash quality, balance-sheet resilience, and capital discipline. A score is withheld when coverage is insufficient." />
        <Guide title="Change at latest filing" text="Compares the current composite with the preceding scored filing state. Strengthening and weakening require a move of at least five points; smaller changes are stable." />
        <Guide title="Trajectory" text="Each point is computed using only filings available at that point. Later filings do not repaint earlier scores. Use it to see whether evidence is persistently improving or deteriorating." />
        <Guide title="Profitability" text="Gross margin, operating margin, net margin, and return on positive equity. Higher sustainable profitability scores better." />
        <Guide title="Growth" text="Revenue and net-income changes between comparable fiscal periods. It distinguishes expansion from contraction but does not assess valuation." />
        <Guide title="Cash quality" text="Free-cash-flow margin and operating-cash conversion. It tests whether accounting earnings are supported by cash generation." />
        <Guide title="Balance sheet" text="Current ratio, debt to positive equity, and interest coverage. Higher liquidity and debt service capacity score better." />
        <Guide title="Capital discipline" text="Basic-share growth and the diluted-versus-basic share spread. Faster dilution lowers the score." />
        <Guide title="Actionable signals" text="Derived ratios align numerator and denominator to comparable periods. Always inspect the formula and period before acting on an unusual value." />
        <Guide title="Reported evidence" text="Facts are grouped into statement-like XBRL classes. The taxonomy tag, filing date, period, unit, and accession preserve the audit trail and explain exactly which disclosure supports a metric." />
      </div>
    </div></Modal> : null}
  </section>;
}

function ScoreAreaChart({ points }: { points: TimelinePoint[] }) {
  const scored = points.filter((point) => point.score != null);
  if (scored.length < 2) return <div className="xbrl-chart-empty">At least two scored filings are required for a trajectory.</div>;
  const width = 640, height = 130, left = 34, right = 10, top = 10, bottom = 22;
  const x = (index: number) => left + index * ((width - left - right) / Math.max(1, scored.length - 1));
  const y = (value: number) => top + (100 - value) * ((height - top - bottom) / 100);
  const line = scored.map((point, index) => `${x(index)},${y(point.score ?? 0)}`).join(" ");
  const area = `${left},${height - bottom} ${line} ${x(scored.length - 1)},${height - bottom}`;
  return <svg aria-label="Financial evidence score through filing time" preserveAspectRatio="none" role="img" viewBox={`0 0 ${width} ${height}`}>
    {[0, 25, 50, 75, 100].map((value) => <g key={value}><line className="xbrl-chart-grid" x1={left} x2={width - right} y1={y(value)} y2={y(value)} /><text x={left - 6} y={y(value) + 3}>{value}</text></g>)}
    <polygon className="xbrl-chart-area" points={area} /><polyline className="xbrl-chart-line" fill="none" points={line} />
    {scored.map((point, index) => <circle className="xbrl-chart-point" cx={x(index)} cy={y(point.score ?? 0)} key={point.available_at} r="3"><title>{`${dateLabel(point.available_at)} · ${score(point.score)}/100 · ${point.label}`}</title></circle>)}
    <text className="xbrl-chart-date" x={left} y={height - 4}>{dateLabel(scored[0].available_at)}</text><text className="xbrl-chart-date" textAnchor="end" x={width - right} y={height - 4}>{dateLabel(scored[scored.length - 1].available_at)}</text>
  </svg>;
}

function Guide({ text, title }: { text: string; title: string }) { return <article><HelpCircle size={15} /><span><strong>{title}</strong><p>{text}</p></span></article>; }
function score(value?: number) { return value == null ? "—" : Math.round(value).toString(); }
function signed(value?: number) { return value == null ? "No prior" : `${value > 0 ? "+" : ""}${value.toFixed(1)}`; }
function dateLabel(value?: string) { if (!value) return "—"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "2-digit" }); }
function formatMetric(value: number, unit: string) { if (unit === "percent") return `${value.toFixed(1)}%`; if (unit === "multiple") return `${value.toFixed(2)}×`; if (unit === "USD") return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1, style: "currency", currency: "USD" }).format(value); return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(value); }
function formatFact(fact: Fact) { const value = Number(fact.value); if (!Number.isFinite(value)) return "—"; const formatted = Intl.NumberFormat(undefined, { notation: Math.abs(value) >= 100_000 ? "compact" : "standard", maximumFractionDigits: 2 }).format(value); return `${formatted}${fact.unit_code ? ` ${fact.unit_code}` : ""}`; }
function metricTone(metric: Metric) { if (["debt_to_equity", "net_debt", "share_growth", "dilution", "sga_intensity"].includes(metric.id)) return metric.value > 0 ? "warning" : "positive"; if (["free_cash_flow", "gross_margin", "operating_margin", "net_margin", "revenue_growth", "earnings_growth", "current_ratio", "interest_coverage", "cash_conversion"].includes(metric.id)) return metric.value > 0 ? "positive" : "negative"; return "neutral"; }

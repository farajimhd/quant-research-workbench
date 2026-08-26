import { Activity, BadgeDollarSign, BriefcaseBusiness, ChartNoAxesColumnIncreasing, Check, Clock3, FileText, Landmark, LoaderCircle, Scale, Sparkles, TriangleAlert, UsersRound } from "lucide-react";
import type { ReactNode } from "react";

export type SecIconRecency = "hot" | "cold" | "older";
export type SecIconKind = "administrative" | "capital" | "event" | "fundamentals" | "governance" | "ownership" | "risk" | "transaction" | "other";
export type SecIconPrediction = "positive" | "negative" | "mixed" | "neutral" | "contextual" | "uncertain" | "unavailable";

const SEC_ICON_KINDS = {
  administrative: { Icon: Clock3, label: "Administrative" },
  capital: { Icon: BadgeDollarSign, label: "Capital & financing" },
  event: { Icon: Activity, label: "Current event" },
  fundamentals: { Icon: ChartNoAxesColumnIncreasing, label: "Results & fundamentals" },
  governance: { Icon: Landmark, label: "Governance" },
  ownership: { Icon: UsersRound, label: "Ownership" },
  risk: { Icon: Scale, label: "Risk & legal" },
  transaction: { Icon: BriefcaseBusiness, label: "Corporate transaction" },
  other: { Icon: FileText, label: "Other filing" },
} satisfies Record<SecIconKind, { Icon: typeof FileText; label: string }>;

export function secIconKindFor(...values: unknown[]): SecIconKind {
  const text = values.flatMap(normalizeLabels).join(" ").toLowerCase();
  if (/earnings|results|fundamental|financial|periodic|quarterly|annual|10-k|10-q/.test(text)) return "fundamentals";
  if (/offering|capital|financ|debt|security|securities|shelf|registration/.test(text)) return "capital";
  if (/merger|acquisition|transaction|disposition|restructur/.test(text)) return "transaction";
  if (/insider|ownership|beneficial|activism|13d|13g|form 4/.test(text)) return "ownership";
  if (/governance|director|officer|board|proxy|voting|compensation/.test(text)) return "governance";
  if (/risk|legal|litigation|bankruptcy|default|investigation/.test(text)) return "risk";
  if (/administrative|notice|fund product|fund dataset|structured finance/.test(text)) return "administrative";
  if (/current event|current_event|8-k/.test(text)) return "event";
  return "other";
}

export function secIconKindLabel(kind: SecIconKind): string { return SEC_ICON_KINDS[kind].label; }

export function normalizeSecIconPrediction(value: unknown): SecIconPrediction {
  const direction = String(value || "").trim().toLowerCase();
  if (["positive", "upside", "bullish", "increase", "increased"].includes(direction)) return "positive";
  if (["negative", "downside", "bearish", "decrease", "decreased"].includes(direction)) return "negative";
  if (direction === "mixed") return "mixed";
  if (direction === "contextual") return "contextual";
  if (direction === "uncertain") return "uncertain";
  if (direction === "neutral") return "neutral";
  return "unavailable";
}

export function SecIntelligenceIcon({ count, failed = false, kind = "other", pending = false, prediction = "unavailable", recency = "older", reviewed = false, synthesized = false }: { count: number; failed?: boolean; kind?: SecIconKind; pending?: boolean; prediction?: SecIconPrediction; recency?: SecIconRecency; reviewed?: boolean; synthesized?: boolean }) {
  const Icon = SEC_ICON_KINDS[kind].Icon;
  return <span aria-hidden="true" className="sec-intelligence-icon" data-kind={kind} data-prediction={prediction} data-recency={recency} data-synthesized={synthesized}>
    <Icon className="sec-intelligence-document" />
    {synthesized ? <Check className="sec-intelligence-synthesis-mark" /> : null}
    {reviewed ? <Sparkles className="sec-intelligence-review-mark" /> : null}
    {pending ? <LoaderCircle className="sec-intelligence-state-mark" data-state="pending" /> : null}
    {failed ? <TriangleAlert className="sec-intelligence-state-mark" data-state="failed" /> : null}
    {count > 1 ? <b className="sec-intelligence-count">{count > 99 ? "99+" : count}</b> : null}
  </span>;
}

export function SecIconGuide() {
  return <section aria-label="SEC marker guide" className="news-icon-guide sec-icon-guide">
    <header><div><strong>SEC marker guide</strong><span>Signal streams, watchlists, and market tables</span></div><SecIntelligenceIcon count={3} kind="fundamentals" prediction="positive" recency="hot" reviewed synthesized /></header>
    <p className="news-guide-intro">The center shape identifies the latest filing family. Its color shows predicted direction; the corner dot preserves recency. Select any SEC marker beside a ticker to open the synthesis-first filing timeline.</p>
    <section className="news-guide-section"><header><strong>Filing meaning</strong><span>Shape identifies the disclosure family</span></header><div className="news-guide-combinations sec-guide-kinds">
      <SecLegend kind="fundamentals" detail="Periodic results and financial changes" />
      <SecLegend kind="event" detail="Material event reported on Form 8-K" />
      <SecLegend kind="capital" detail="Offerings, debt, and financing" />
      <SecLegend kind="transaction" detail="M&A, dispositions, restructuring" />
      <SecLegend kind="ownership" detail="Insiders and beneficial ownership" />
      <SecLegend kind="governance" detail="Board, voting, and compensation" />
      <SecLegend kind="risk" detail="Legal, default, and investigation" />
      <SecLegend kind="administrative" detail="Notices and routine disclosures" />
      <SecLegend kind="other" detail="Unclassified or synthesis pending" />
    </div></section>
    <section className="news-guide-section"><header><strong>Prediction color</strong><span>AI review when available, otherwise SEC Synthesis</span></header><div className="news-guide-examples">
      <GuideState icon={<SecIntelligenceIcon count={1} kind="fundamentals" prediction="positive" recency="cold" synthesized />} label="Positive" detail="Green = favorable implication" />
      <GuideState icon={<SecIntelligenceIcon count={1} kind="risk" prediction="negative" recency="cold" synthesized />} label="Negative" detail="Red = adverse implication" />
      <GuideState icon={<SecIntelligenceIcon count={1} kind="event" prediction="mixed" recency="cold" synthesized />} label="Mixed" detail="Amber = competing implications" />
      <GuideState icon={<SecIntelligenceIcon count={1} kind="transaction" prediction="contextual" recency="cold" reviewed synthesized />} label="Contextual" detail="Blue-gray = insufficient direction" />
    </div></section>
    <section className="news-guide-section"><header><strong>State layers</strong><span>Symbols remain readable without color</span></header><div className="news-guide-examples">
      <GuideState icon={<SecIntelligenceIcon count={1} kind="fundamentals" recency="hot" synthesized />} label="Synthesized" detail="Check = synthesis available" />
      <GuideState icon={<SecIntelligenceIcon count={1} kind="capital" recency="cold" reviewed synthesized />} label="AI reviewed" detail="Violet sparkle = manual review" />
      <GuideState icon={<SecIntelligenceIcon count={1} kind="other" pending recency="cold" />} label="Processing" detail="Spinner = synthesis pending" />
      <GuideState icon={<SecIntelligenceIcon count={4} failed kind="risk" recency="older" />} label="Needs attention" detail="Warning = incomplete processing" />
    </div></section>
    <footer><strong>Recency</strong><span>Corner dot: coral is hot, blue is recent, and gray is older.</span><strong>Counter</strong><span>The blue number is the linked filing count.</span><strong>Authority</strong><span>Prediction color uses completed manual AI review first, then SEC Synthesis sentiment. Forecast eligibility remains a separate deterministic label shown in the timeline.</span></footer>
  </section>;
}

function SecLegend({ detail, kind }: { detail: string; kind: SecIconKind }) {
  return <div className="news-icon-legend-item"><span><SecIntelligenceIcon count={1} kind={kind} recency="cold" synthesized /></span><div><strong>{SEC_ICON_KINDS[kind].label}</strong><small>{detail}</small></div></div>;
}

function GuideState({ detail, icon, label }: { detail: string; icon: ReactNode; label: string }) {
  return <div className="news-guide-result"><span>{icon}</span><div><strong>{label}</strong><small>{detail}</small></div></div>;
}

function normalizeLabels(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(normalizeLabels);
  if (value === null || value === undefined) return [];
  const text = String(value).trim();
  if (!text) return [];
  if (text.startsWith("[") && text.endsWith("]")) {
    try { return normalizeLabels(JSON.parse(text)); } catch { /* Continue with delimited text. */ }
  }
  return text.split(/[,|]/).map((item) => item.trim()).filter(Boolean);
}

import { Flame, LoaderCircle, Minus, Sparkles, TrendingDown, TrendingUp, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

export type NewsIconRecency = "fresh" | "hot" | "older";
export type NewsReactionDirection = "down" | "flat" | "up";

type NewsIntelligenceIconProps = {
  count: number;
  deepFmEligible?: boolean;
  failed?: boolean;
  pending?: boolean;
  reactionDirection?: NewsReactionDirection;
  recency?: NewsIconRecency;
  reviewed?: boolean;
};

export function NewsIntelligenceIcon({ count, deepFmEligible = false, failed = false, pending = false, reactionDirection, recency = "older", reviewed = false }: NewsIntelligenceIconProps) {
  const ReactionIcon = reactionDirection === "up" ? TrendingUp : reactionDirection === "down" ? TrendingDown : Minus;
  return <span aria-hidden="true" className="news-intelligence-icon" data-eligible={deepFmEligible} data-recency={recency}>
    <Flame className="news-intelligence-flame" fill={deepFmEligible ? "currentColor" : "none"} />
    {reviewed ? <Sparkles className="news-intelligence-review-mark" /> : null}
    {reactionDirection ? <ReactionIcon className="news-intelligence-reaction-mark" data-direction={reactionDirection} /> : null}
    {pending ? <LoaderCircle className="news-intelligence-state-mark" data-state="pending" /> : null}
    {failed ? <TriangleAlert className="news-intelligence-state-mark" data-state="failed" /> : null}
    {count > 1 ? <b className="news-intelligence-count">{count > 99 ? "99+" : count}</b> : null}
  </span>;
}

export function NewsIconGuide() {
  return <section aria-label="News marker guide" className="news-icon-guide">
    <header><div><strong>News marker guide</strong><span>Signal streams, watchlists, and market tables</span></div><NewsIntelligenceIcon count={4} deepFmEligible reactionDirection="up" recency="hot" reviewed /></header>
    <p className="news-guide-intro">The marker sits beside the ticker. Layers accumulate as the story moves through the funnel; select it to open today’s timeline, lazily load recent history, and use manual AI actions.</p>
    <section className="news-guide-section"><header><strong>Recency + DeepFM</strong><span>Color is age; fill is eligibility</span></header><div className="news-guide-combinations">
      <Legend icon={<NewsIntelligenceIcon count={1} recency="hot" />} label="Hot · outline" detail="Under 15 min · not eligible" />
      <Legend icon={<NewsIntelligenceIcon count={3} deepFmEligible recency="hot" />} label="Hot · filled" detail="Under 15 min · eligible" />
      <Legend icon={<NewsIntelligenceIcon count={1} recency="fresh" />} label="Fresh · outline" detail="15–60 min · not eligible" />
      <Legend icon={<NewsIntelligenceIcon count={2} deepFmEligible recency="fresh" />} label="Fresh · filled" detail="15–60 min · eligible" />
      <Legend icon={<NewsIntelligenceIcon count={1} recency="older" />} label="Older · outline" detail="Earlier today · not eligible" />
      <Legend icon={<NewsIntelligenceIcon count={7} deepFmEligible recency="older" />} label="Older · filled" detail="Earlier today · eligible" />
    </div></section>
    <section className="news-guide-section"><header><strong>AI review</strong><span>Violet sparkle means completed review</span></header><div className="news-guide-examples">
      <GuideResult icon={<NewsIntelligenceIcon count={2} deepFmEligible recency="hot" reviewed />} label="AI Positive" tone="positive" value="82% relevant" />
      <GuideResult icon={<NewsIntelligenceIcon count={1} deepFmEligible recency="fresh" reviewed />} label="AI Negative" tone="negative" value="74% relevant" />
      <GuideResult icon={<NewsIntelligenceIcon count={4} deepFmEligible recency="older" reviewed />} label="AI Mixed" tone="mixed" value="61% relevant" />
      <GuideResult icon={<NewsIntelligenceIcon count={1} deepFmEligible recency="older" reviewed />} label="AI Neutral" tone="neutral" value="55% relevant" />
    </div></section>
    <section className="news-guide-section"><header><strong>Reaction forecast</strong><span>Arrow is direction; values live in the timeline</span></header><div className="news-guide-examples">
      <GuideResult icon={<NewsIntelligenceIcon count={2} deepFmEligible reactionDirection="up" recency="hot" reviewed />} label="5m ↑ +1.80%" tone="positive" value="76% confidence" />
      <GuideResult icon={<NewsIntelligenceIcon count={1} deepFmEligible reactionDirection="down" recency="fresh" reviewed />} label="5m ↓ −1.20%" tone="negative" value="71% confidence" />
      <GuideResult icon={<NewsIntelligenceIcon count={3} deepFmEligible reactionDirection="flat" recency="older" reviewed />} label="5m → 0.00%" tone="neutral" value="64% confidence" />
    </div></section>
    <section className="news-guide-section news-guide-workflow"><header><strong>Workflow states</strong><span>Non-color symbols remain visible</span></header><div>
      <Legend icon={<NewsIntelligenceIcon count={1} pending recency="fresh" />} label="Processing" detail="Review or reaction running" />
      <Legend icon={<NewsIntelligenceIcon count={4} failed recency="older" />} label="Needs attention" detail="Failed or incomplete processing" />
    </div></section>
    <footer><strong>Counter</strong><span>The violet number is the linked story count. The timeline itself is restricted to the current New York market date.</span><strong>Synthesis</strong><span>News Synthesis remains view-only and does not control fill, filtering, or signals.</span></footer>
  </section>;
}

function Legend({ detail, icon, label }: { detail: string; icon: ReactNode; label: string }) {
  return <div className="news-icon-legend-item"><span>{icon}</span><div><strong>{label}</strong><small>{detail}</small></div></div>;
}

function GuideResult({ icon, label, tone, value }: { icon: ReactNode; label: string; tone: "mixed" | "negative" | "neutral" | "positive"; value: string }) {
  return <div className="news-guide-result"><span>{icon}</span><div><strong data-tone={tone}>{label}</strong><small>{value}</small></div></div>;
}

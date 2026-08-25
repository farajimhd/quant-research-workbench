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

export function NewsIconLegend() {
  return <section aria-label="News icon legend" className="news-icon-legend">
    <div className="news-icon-legend-title"><strong>News icons</strong><span>Signal &amp; watchlist tables</span></div>
    <div className="news-icon-legend-grid">
      <Legend icon={<NewsIntelligenceIcon count={1} recency="hot" />} label="Hot" detail="Under 15 min" />
      <Legend icon={<NewsIntelligenceIcon count={1} recency="fresh" />} label="Fresh" detail="15–60 min" />
      <Legend icon={<NewsIntelligenceIcon count={1} recency="older" />} label="Older" detail="Earlier today" />
      <Legend icon={<NewsIntelligenceIcon count={1} deepFmEligible recency="fresh" />} label="Filled" detail="DeepFM eligible" />
      <Legend icon={<NewsIntelligenceIcon count={1} reviewed recency="older" />} label="Sparkle" detail="AI reviewed" />
      <Legend icon={<NewsIntelligenceIcon count={1} reactionDirection="up" recency="older" />} label="Arrow" detail="Reaction forecast" />
      <Legend icon={<NewsIntelligenceIcon count={1} pending recency="older" />} label="Spinner" detail="Processing" />
      <Legend icon={<NewsIntelligenceIcon count={4} failed recency="older" />} label="Warning" detail="Needs attention" />
    </div>
    <p>Up is green, down is rose, flat is gray. The colored counter is the linked story count; the popover is restricted to today. Table sorting uses the strongest available model confidence.</p>
  </section>;
}

function Legend({ detail, icon, label }: { detail: string; icon: ReactNode; label: string }) {
  return <div className="news-icon-legend-item"><span>{icon}</span><div><strong>{label}</strong><small>{detail}</small></div></div>;
}

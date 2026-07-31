import { ArrowDown, ArrowUp, ArrowUpDown, ChevronsUpDown, CircleDashed, Minus, TrendingDown, TrendingUp } from "lucide-react";

export type SemanticDirection = "mixed" | "negative" | "neutral" | "pending" | "positive";
export type SentimentSortOrder = "ascending" | "descending" | "none";

export function normalizeSemanticDirection(value?: string | null): SemanticDirection {
  if (!value) return "pending";
  const normalized = value.trim().toLowerCase();
  if (["positive", "upside", "bullish", "up"].includes(normalized)) return "positive";
  if (["negative", "downside", "bearish", "down"].includes(normalized)) return "negative";
  if (["mixed", "two_sided", "two-sided"].includes(normalized)) return "mixed";
  if (["neutral", "flat", "none"].includes(normalized)) return "neutral";
  return "pending";
}

export function SemanticDirectionMetric({
  direction: rawDirection,
  prominent = false,
  score,
}: {
  direction?: string | null;
  prominent?: boolean;
  score?: number | null;
}) {
  const direction = normalizeSemanticDirection(rawDirection);
  const hasScore = typeof score === "number" && Number.isFinite(score);
  const Icon = direction === "positive"
    ? TrendingUp
    : direction === "negative"
      ? TrendingDown
      : direction === "mixed"
        ? ArrowUpDown
        : direction === "neutral"
          ? Minus
          : CircleDashed;
  const label = direction === "pending"
    ? "Pending"
    : direction[0].toUpperCase() + direction.slice(1);
  const scoreText = hasScore
    ? `${direction === "mixed" ? "±" : score > 0 ? "+" : ""}${direction === "mixed" ? Math.abs(score).toFixed(2) : score.toFixed(2)} score`
    : "";
  const description = scoreText
    ? `Deterministic text direction: ${scoreText}`
    : "Deterministic text direction";

  return <span
    aria-label={scoreText ? `${label}, ${scoreText}` : label}
    className="semantic-direction-metric"
    data-direction={direction}
    data-prominent={prominent ? "true" : "false"}
    title={description}
  >
    <Icon aria-hidden="true" />
    <span><strong>{label}</strong>{scoreText ? <small>{scoreText}</small> : null}</span>
  </span>;
}

export function SentimentSortButton({ onChange, order }: { onChange: (order: SentimentSortOrder) => void; order: SentimentSortOrder }) {
  const Icon = order === "ascending" ? ArrowUp : order === "descending" ? ArrowDown : ChevronsUpDown;
  const next = order === "none" ? "descending" : order === "descending" ? "ascending" : "none";
  const state = order === "none" ? "not sorted" : `${order} by score`;
  return <button aria-label={`Sentiment, ${state}. Activate to sort ${next === "none" ? "by default order" : next}`} className="sentiment-sort-button" onClick={() => onChange(next)} type="button"><span>Sentiment</span><Icon aria-hidden="true" /></button>;
}

export function sortRowsBySentimentScore<T>(rows: readonly T[], scoreOf: (row: T) => number | null | undefined, order: SentimentSortOrder): T[] {
  if (order === "none") return [...rows];
  return rows.map((row, index) => ({ index, row, score: scoreOf(row) }))
    .sort((left, right) => {
      const leftValid = typeof left.score === "number" && Number.isFinite(left.score);
      const rightValid = typeof right.score === "number" && Number.isFinite(right.score);
      if (leftValid !== rightValid) return leftValid ? -1 : 1;
      if (!leftValid || !rightValid) return left.index - right.index;
      const leftScore = left.score as number;
      const rightScore = right.score as number;
      if (leftScore === rightScore) return left.index - right.index;
      return order === "ascending" ? leftScore - rightScore : rightScore - leftScore;
    })
    .map(({ row }) => row);
}

import { ArrowUpDown, CircleDashed, Minus, TrendingDown, TrendingUp } from "lucide-react";

export type SemanticDirection = "mixed" | "negative" | "neutral" | "pending" | "positive";

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

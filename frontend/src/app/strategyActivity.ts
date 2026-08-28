export const STRATEGY_ACTIVITY_EVENT_OPTIONS = [
  { label: "Signal", value: "signal" },
  { label: "Watchlist", value: "watchlist" },
  { label: "Decision", value: "decision" },
  { label: "Campaign state", value: "campaign_state" },
  { label: "Order", value: "order" },
] as const;

export type StrategyActivityEventType = (typeof STRATEGY_ACTIVITY_EVENT_OPTIONS)[number]["value"];

export const STRATEGY_ACTIVITY_EVENT_LABELS: Record<StrategyActivityEventType, string> = Object.fromEntries(
  STRATEGY_ACTIVITY_EVENT_OPTIONS.map(({ label, value }) => [value, label]),
) as Record<StrategyActivityEventType, string>;

import type { UTCTimestamp } from "lightweight-charts";

import type { ChartPayload } from "./components/ChartPanel";

export type StrategyAction = "enter_long" | "add_long" | "reduce_long" | "take_profit" | "exit" | "hold" | "wait";

export type StrategyDecisionEvent = {
  action: StrategyAction;
  confidence: number;
  direction: "bullish" | "bearish" | "neutral";
  effective_at: string;
  event_id: string;
  invalidation_price?: number | null;
  reference_price?: number | null;
  score: number;
  strategy_id: string;
  strategy_revision: number;
  ticker: string;
};

export type StrategyChartPresentation = {
  label?: string;
  show_confidence: boolean;
  show_adds: boolean;
  show_entries: boolean;
  show_exits: boolean;
  show_holds: boolean;
  show_invalidation: boolean;
  show_reductions: boolean;
  show_waits: boolean;
};

export type StrategyPresentationBar = {
  bar_end?: string;
  bar_start: string;
};

export const DEFAULT_STRATEGY_CHART_PRESENTATION: StrategyChartPresentation = {
  show_confidence: true,
  show_adds: true,
  show_entries: true,
  show_exits: true,
  show_holds: false,
  show_invalidation: true,
  show_reductions: true,
  show_waits: false,
};

export function strategyPresentationMarkers(
  events: StrategyDecisionEvent[],
  bars: StrategyPresentationBar[],
  presentation: StrategyChartPresentation = DEFAULT_STRATEGY_CHART_PRESENTATION,
): NonNullable<ChartPayload["markers"]> {
  if (!events.length || !bars.length) return [];
  const intervals = bars.map((bar) => ({
    end: Date.parse(bar.bar_end || "") || Date.parse(bar.bar_start) + 1,
    start: Date.parse(bar.bar_start),
    time: Date.parse(bar.bar_start) / 1000,
  }));
  return events.flatMap((event) => {
    if (!actionVisible(event.action, presentation)) return [];
    const effectiveAt = Date.parse(event.effective_at);
    if (!Number.isFinite(effectiveAt)) return [];
    const interval = intervals.find((candidate) => effectiveAt >= candidate.start && effectiveAt < candidate.end)
      ?? intervals.find((candidate) => candidate.start >= effectiveAt);
    if (!interval) return [];
    const style = actionStyle(event.action, event.direction);
    const confidence = Math.max(0, Math.min(1, Number(event.confidence) || 0));
    const strategyLabel = presentation.label || event.strategy_id;
    return [{
      color: style.color,
      displayItemId: "strategy.presentation",
      position: style.position,
      shape: style.shape,
      size: style.size,
      text: `${strategyLabel} · ${actionLabel(event.action)}${presentation.show_confidence ? ` · ${Math.round(confidence * 100)}%` : ""}`,
      time: interval.time as UTCTimestamp,
    }];
  });
}

export function strategyInvalidationZones(
  events: StrategyDecisionEvent[],
  bars: StrategyPresentationBar[],
  presentation: StrategyChartPresentation = DEFAULT_STRATEGY_CHART_PRESENTATION,
): NonNullable<ChartPayload["price_zones"]> {
  if (!presentation.show_invalidation || !events.length || !bars.length) return [];
  const chartStart = Date.parse(bars[0].bar_start) / 1000;
  const chartEnd = Date.parse(bars[bars.length - 1].bar_end || bars[bars.length - 1].bar_start) / 1000;
  return events.flatMap((event) => {
    if (event.action !== "enter_long" && event.action !== "add_long") return [];
    const price = Number(event.invalidation_price);
    const eventTime = Date.parse(event.effective_at) / 1000;
    if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(eventTime) || eventTime >= chartEnd) return [];
    return [{
      annotationKind: "level",
      axisLabelDefault: true,
      borderColor: "var(--warning)",
      borderOpacity: 0.8,
      borderStyle: "dashed",
      borderWidth: 1,
      color: "var(--warning)",
      compactLabel: `${presentation.label || event.strategy_id} invalidation`,
      defaultVisible: true,
      displayItemId: "strategy.presentation",
      end: Math.max(chartEnd, eventTime),
      eventTime,
      fillOpacity: 0,
      label: `${presentation.label || event.strategy_id} invalidation`,
      lower: price,
      renderMode: "line",
      start: Math.max(chartStart, eventTime),
      tone: event.direction === "bearish" ? "sell" : "buy",
      upper: price,
    }];
  });
}

function actionVisible(action: StrategyAction, presentation: StrategyChartPresentation) {
  if (action === "enter_long") return presentation.show_entries;
  if (action === "add_long") return presentation.show_adds;
  if (action === "reduce_long" || action === "take_profit") return presentation.show_reductions;
  if (action === "exit") return presentation.show_exits;
  if (action === "hold") return presentation.show_holds;
  return presentation.show_waits;
}

function actionStyle(action: StrategyAction, direction: StrategyDecisionEvent["direction"]) {
  if (action === "enter_long") return { color: "var(--success)", position: "belowBar" as const, shape: "arrowUp" as const, size: 2 };
  if (action === "add_long") return { color: "var(--success)", position: "belowBar" as const, shape: "arrowUp" as const, size: 1 };
  if (action === "reduce_long" || action === "take_profit") return { color: "var(--warning)", position: "aboveBar" as const, shape: "arrowDown" as const, size: 1 };
  if (action === "exit") return {
    color: "var(--warning)",
    position: direction === "bearish" ? "belowBar" as const : "aboveBar" as const,
    shape: "circle" as const,
    size: 1,
  };
  return {
    color: "var(--muted-foreground)",
    position: direction === "bearish" ? "aboveBar" as const : "belowBar" as const,
    shape: "circle" as const,
    size: 1,
  };
}

function actionLabel(action: StrategyAction) {
  return action.replace("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

import { useEffect, useState } from "react";

import { api, query } from "../../api/client";
import type { ChartTimelineEvent } from "./ChartPanel";

export type StockSplitEvent = {
  available_at?: string | null;
  direction: "forward" | "neutral" | "reverse";
  execution_date: string;
  id: string;
  ratio: number;
  source: string;
  split_from: number;
  split_to: number;
};

type StockSplitEventsPayload = { events: StockSplitEvent[] };

export function useStockSplitEvents(symbol: string, cutoffMs: number, enabled = true) {
  const requestCutoffMs = Math.floor(cutoffMs / 60_000) * 60_000;
  const [state, setState] = useState<{ error: string; events: StockSplitEvent[]; loading: boolean }>({ error: "", events: [], loading: false });
  useEffect(() => {
    if (!enabled) {
      setState({ error: "", events: [], loading: false });
      return;
    }
    const ticker = symbol.trim().toUpperCase();
    if (!ticker || !Number.isFinite(requestCutoffMs)) {
      setState({ error: "", events: [], loading: false });
      return;
    }
    const controller = new AbortController();
    setState((current) => ({ ...current, error: "", loading: true }));
    api<StockSplitEventsPayload>(`/api/trading/ticker-facts/${encodeURIComponent(ticker)}/splits${query({ as_of: new Date(requestCutoffMs).toISOString() })}`, {
      signal: controller.signal,
      timeoutMs: 10_000,
    })
      .then((payload) => setState({ error: "", events: Array.isArray(payload.events) ? payload.events : [], loading: false }))
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setState({ error: reason instanceof Error ? reason.message : String(reason), events: [], loading: false });
      });
    return () => controller.abort();
  }, [enabled, requestCutoffMs, symbol]);
  return enabled ? state : { error: "", events: [], loading: false };
}

export function stockSplitTimelineEvents(
  symbol: string,
  events: StockSplitEvent[],
  sessionTimes: Array<{ sessionDate: string; time: number }>,
): ChartTimelineEvent[] {
  const timeBySession = new Map(sessionTimes.map((row) => [row.sessionDate, row.time]));
  return events.map((event) => {
    const direction = event.direction === "reverse" ? "reverse" : event.direction === "forward" ? "forward" : "stock";
    const ratio = `${formatSplitPart(event.split_to)}-for-${formatSplitPart(event.split_from)}`;
    return {
      ariaLabel: `${symbol} ${ratio} ${direction} split executed ${event.execution_date}`,
      id: event.id,
      kind: "split",
      label: "S",
      time: timeBySession.get(event.execution_date) ?? Date.parse(`${event.execution_date}T12:00:00Z`) / 1000,
      title: `${ratio} ${direction} split · executed ${event.execution_date}`,
    };
  });
}

export function stockSplitTimelineEventsForCandles(
  symbol: string,
  events: StockSplitEvent[],
  candles: Array<{ time: number }>,
) {
  return stockSplitTimelineEvents(symbol, events, candles.map((candle) => ({
    sessionDate: marketDate(candle.time),
    time: candle.time,
  })));
}

function formatSplitPart(value: number) {
  return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function marketDate(timestampSeconds: number) {
  return new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "America/New_York",
    year: "numeric",
  }).format(new Date(timestampSeconds * 1000));
}

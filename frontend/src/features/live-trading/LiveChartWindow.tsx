import { useEffect, useMemo, useState } from "react";

import type { ChartPayload } from "../../app/components/ChartPanel";
import { stockSplitTimelineEventsForCandles, useStockSplitEvents } from "../../app/components/chartSplitEvents";
import type { CatalogPayload, Scope } from "./contracts";
import {
  buildLiveEntryLine,
  type OrderRow,
  type PositionRow,
  type StageOrderContext,
} from "./portfolio";
import { latestLiveChartRow, quoteFromRow } from "./scanner";
import {
  clockTimestampSeconds,
  dateOffset,
  previousSessionDate,
  rowTimestampSeconds,
  type TradingSession,
} from "./time";
import {
  castOpenChartPayload,
  dayOpenOnlyChartPayload,
  loadChart,
  openOnlyChartPayload,
} from "./liveChartData";
import { LiveChartsContainer } from "./LiveChartsContainer";
import { numberValue } from "./liveTradingFormat";
import type { ChartWindow } from "./liveWorkspaceContracts";
import { chartOpenAtTime } from "./liveWorkspacePresentation";

type TradeDraft = {
  limit: string;
  quantity: string;
  side: "BUY" | "SELL";
  stop: string;
  type: string;
};

export type LiveChartWindowProps = {
  availableCash: number;
  catalog: CatalogPayload | null;
  chart: ChartWindow;
  compactVisibleColumns: string[];
  draft: TradeDraft;
  mainTimeframe: string;
  mainVisibleColumns: string[];
  marketRows: Record<string, unknown>[];
  orders: OrderRow[];
  positions: PositionRow[];
  preferMarketQuote: boolean;
  scannerRows: Record<string, unknown>[];
  scope: Scope;
  session: TradingSession;
  sessions: string[];
  showDayChart: boolean;
  showFiveMinuteChart: boolean;
  onCompactVisibleColumnsChange: (columns: string[]) => void;
  onDraftChange: (draft: TradeDraft) => void;
  onMainTimeframeChange: (timeframe: string) => void;
  onMainVisibleColumnsChange: (columns: string[]) => void;
  onMarkPosition?: (symbol: string, mark: number) => void;
  onStage: (side?: "BUY" | "SELL", status?: string, context?: Partial<StageOrderContext>) => void;
  onToggleDayChart: () => void;
  onToggleFiveMinuteChart: () => void;
};

export function LiveChartWindow({
  availableCash,
  catalog,
  chart,
  compactVisibleColumns,
  draft,
  mainTimeframe,
  mainVisibleColumns,
  marketRows,
  onCompactVisibleColumnsChange,
  onDraftChange,
  onMainTimeframeChange,
  onMainVisibleColumnsChange,
  onMarkPosition,
  onStage,
  onToggleDayChart,
  onToggleFiveMinuteChart,
  orders,
  positions,
  preferMarketQuote,
  scannerRows,
  scope,
  session,
  sessions,
  showDayChart,
  showFiveMinuteChart,
}: LiveChartWindowProps) {
  const [mainPayload, setMainPayload] = useState<ChartPayload | null>(null);
  const [dayPayload, setDayPayload] = useState<ChartPayload | null>(null);
  const [fiveMinutePayload, setFiveMinutePayload] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [dayChartLoading, setDayChartLoading] = useState(false);
  const [fiveMinuteChartLoading, setFiveMinuteChartLoading] = useState(false);
  const [chartErrors, setChartErrors] = useState({ day: "", fiveMinute: "", main: "" });
  const [splitVisibility, setSplitVisibility] = useState(() => readSplitVisibility(chart.id, mainTimeframe));
  const chartError = [chartErrors.main, showDayChart ? chartErrors.day : "", showFiveMinuteChart ? chartErrors.fiveMinute : ""].filter(Boolean).join(" ");
  const liveRow = latestLiveChartRow(chart, marketRows, scannerRows);
  const selectedTime = clockTimestampSeconds(session.sessionDate, session.barTime) ?? rowTimestampSeconds(chart.row, session.sessionDate, session.barTime);
  const splitEvents = useStockSplitEvents(chart.ticker, selectedTime === null ? Number.NaN : selectedTime * 1000, splitVisibility.main || splitVisibility.day || splitVisibility.fiveMinute);
  const selectedOpen =
    chartOpenAtTime(mainPayload, selectedTime) ||
    numberValue(liveRow, "current_open") ||
    numberValue(liveRow, "open");
  const quote = quoteFromRow(liveRow, selectedOpen, { preferMarketQuote });
  const position = positions.find((row) => row.symbol === chart.ticker);
  const liveEntryLine = buildLiveEntryLine(position, quote.bid);

  useEffect(() => {
    if (onMarkPosition && position && quote.bid > 0) onMarkPosition(chart.ticker, quote.bid);
  }, [chart.ticker, onMarkPosition, position, quote.bid]);

  function closeLivePosition() {
    if (!position || position.quantity <= 0) return;
    onStage("SELL", "STAGED", {
      limit: quote.bid,
      mark: quote.bid,
      quantity: position.quantity,
      row: liveRow,
      side: "SELL",
      status: "STAGED",
      stop: position.stop,
      symbol: chart.ticker,
      type: "LIMIT",
    });
  }

  const mainOpenOnlyPayload = useMemo(() => {
    if (mainTimeframe === "1d") return dayOpenOnlyChartPayload(mainPayload, session.sessionDate, selectedOpen, selectedTime);
    if (mainTimeframe === "5m") return castOpenChartPayload(mainPayload, selectedTime, selectedOpen);
    return openOnlyChartPayload(mainPayload, selectedTime, selectedOpen);
  }, [mainPayload, mainTimeframe, selectedOpen, selectedTime, session.sessionDate]);
  const dayOpenOnlyPayload = useMemo(
    () => dayOpenOnlyChartPayload(dayPayload, session.sessionDate, selectedOpen, selectedTime),
    [dayPayload, selectedOpen, selectedTime, session.sessionDate],
  );
  const fiveMinuteOpenOnlyPayload = useMemo(
    () => castOpenChartPayload(fiveMinutePayload, selectedTime, selectedOpen),
    [fiveMinutePayload, selectedOpen, selectedTime],
  );
  const mainPayloadWithSplits = useMemo(
    () => withSplitEvents(mainOpenOnlyPayload, chart.ticker, splitEvents.events, splitVisibility.main),
    [chart.ticker, mainOpenOnlyPayload, splitEvents.events, splitVisibility.main],
  );
  const dayPayloadWithSplits = useMemo(
    () => withSplitEvents(dayOpenOnlyPayload, chart.ticker, splitEvents.events, splitVisibility.day),
    [chart.ticker, dayOpenOnlyPayload, splitEvents.events, splitVisibility.day],
  );
  const fiveMinutePayloadWithSplits = useMemo(
    () => withSplitEvents(fiveMinuteOpenOnlyPayload, chart.ticker, splitEvents.events, splitVisibility.fiveMinute),
    [chart.ticker, fiveMinuteOpenOnlyPayload, splitEvents.events, splitVisibility.fiveMinute],
  );

  function updateSplitVisibility(slot: keyof SplitVisibility, visible: boolean) {
    setSplitVisibility((current) => {
      const next = { ...current, [slot]: visible };
      writeSplitVisibility(chart.id, next);
      return next;
    });
  }

  function changeMainTimeframe(timeframe: string) {
    if (timeframe !== mainTimeframe) updateSplitVisibility("main", timeframe === "1d");
    onMainTimeframeChange(timeframe);
  }

  useEffect(() => {
    let active = true;
    setChartLoading(true);
    setMainPayload(null);
    setChartErrors((current) => ({ ...current, main: "" }));
    loadChart(scope.processed_root, session.sessionDate, session.sessionDate, mainTimeframe, chart.ticker, mainVisibleColumns)
      .then((payload) => { if (active) setMainPayload(payload); })
      .catch((reason) => { if (active) setChartErrors((current) => ({ ...current, main: reason instanceof Error ? reason.message : "Main chart failed to load." })); })
      .finally(() => { if (active) setChartLoading(false); });
    return () => { active = false; };
  }, [chart.ticker, mainTimeframe, mainVisibleColumns, scope.processed_root, session.sessionDate]);

  useEffect(() => {
    if (!showDayChart) return;
    let active = true;
    setDayChartLoading(true);
    setDayPayload(null);
    setChartErrors((current) => ({ ...current, day: "" }));
    loadChart(scope.processed_root, dateOffset(session.sessionDate, -60), session.sessionDate, "1d", chart.ticker, [])
      .then((payload) => { if (active) setDayPayload(payload); })
      .catch((reason) => { if (active) setChartErrors((current) => ({ ...current, day: reason instanceof Error ? reason.message : "Daily chart failed to load." })); })
      .finally(() => { if (active) setDayChartLoading(false); });
    return () => { active = false; };
  }, [chart.ticker, scope.processed_root, session.sessionDate, showDayChart]);

  useEffect(() => {
    if (!showFiveMinuteChart) return;
    let active = true;
    setFiveMinuteChartLoading(true);
    setFiveMinutePayload(null);
    setChartErrors((current) => ({ ...current, fiveMinute: "" }));
    const start = previousSessionDate(sessions, session.sessionDate, 2);
    loadChart(scope.processed_root, start, session.sessionDate, "5m", chart.ticker, compactVisibleColumns)
      .then((payload) => { if (active) setFiveMinutePayload(payload); })
      .catch((reason) => { if (active) setChartErrors((current) => ({ ...current, fiveMinute: reason instanceof Error ? reason.message : "Five-minute chart failed to load." })); })
      .finally(() => { if (active) setFiveMinuteChartLoading(false); });
    return () => { active = false; };
  }, [chart.ticker, compactVisibleColumns, scope.processed_root, session.sessionDate, sessions, showFiveMinuteChart]);

  return (
    <LiveChartsContainer
      availableCash={availableCash}
      catalog={catalog}
      chartError={chartError}
      chartLoading={chartLoading}
      compactVisibleColumns={compactVisibleColumns}
      dayChartLoading={dayChartLoading}
      dayPayload={dayPayloadWithSplits}
      draft={draft}
      fiveMinuteChartLoading={fiveMinuteChartLoading}
      fiveMinutePayload={fiveMinutePayloadWithSplits}
      liveEntryLine={liveEntryLine}
      mainPayload={mainPayloadWithSplits}
      mainTimeframe={mainTimeframe}
      mainVisibleColumns={mainVisibleColumns}
      orders={orders}
      position={position}
      quote={quote}
      row={liveRow}
      selectedTicker={chart.ticker}
      session={session}
      showDayChart={showDayChart}
      showFiveMinuteChart={showFiveMinuteChart}
      splitEventError={splitEvents.error}
      splitVisibility={splitVisibility}
      onCompactVisibleColumnsChange={onCompactVisibleColumnsChange}
      onDraftChange={onDraftChange}
      onLiveEntryClose={closeLivePosition}
      onMainTimeframeChange={changeMainTimeframe}
      onMainVisibleColumnsChange={onMainVisibleColumnsChange}
      onShowDaySplitEventsChange={(visible) => updateSplitVisibility("day", visible)}
      onShowFiveMinuteSplitEventsChange={(visible) => updateSplitVisibility("fiveMinute", visible)}
      onShowMainSplitEventsChange={(visible) => updateSplitVisibility("main", visible)}
      onStage={onStage}
      onToggleDayChart={onToggleDayChart}
      onToggleFiveMinuteChart={onToggleFiveMinuteChart}
    />
  );
}

type SplitVisibility = { day: boolean; fiveMinute: boolean; main: boolean };

function splitVisibilityStorageKey(chartId: string) {
  return `quant-research-workbench.live-chart.${chartId}.split-events.v1`;
}

function readSplitVisibility(chartId: string, mainTimeframe: string): SplitVisibility {
  try {
    const stored = JSON.parse(window.localStorage.getItem(splitVisibilityStorageKey(chartId)) || "null") as Partial<SplitVisibility> | null;
    return {
      day: typeof stored?.day === "boolean" ? stored.day : true,
      fiveMinute: typeof stored?.fiveMinute === "boolean" ? stored.fiveMinute : false,
      main: typeof stored?.main === "boolean" ? stored.main : mainTimeframe === "1d",
    };
  } catch {
    return { day: true, fiveMinute: false, main: mainTimeframe === "1d" };
  }
}

function writeSplitVisibility(chartId: string, visibility: SplitVisibility) {
  try {
    window.localStorage.setItem(splitVisibilityStorageKey(chartId), JSON.stringify(visibility));
  } catch {
    // Keep the in-memory choice usable when storage is unavailable.
  }
}

function withSplitEvents(payload: ChartPayload | null, symbol: string, events: Parameters<typeof stockSplitTimelineEventsForCandles>[1], visible: boolean) {
  if (!payload) return payload;
  const existing = (payload.timeline_events ?? []).filter((event) => !event.id.startsWith("stock-split:"));
  return {
    ...payload,
    timeline_events: visible ? [...existing, ...stockSplitTimelineEventsForCandles(symbol, events, payload.candles)] : existing,
  };
}

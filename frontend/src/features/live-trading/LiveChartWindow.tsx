import { useEffect, useMemo, useState } from "react";

import type { ChartPayload } from "../../app/components/ChartPanel";
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
  const chartError = [chartErrors.main, showDayChart ? chartErrors.day : "", showFiveMinuteChart ? chartErrors.fiveMinute : ""].filter(Boolean).join(" ");
  const liveRow = latestLiveChartRow(chart, marketRows, scannerRows);
  const selectedTime = clockTimestampSeconds(session.sessionDate, session.barTime) ?? rowTimestampSeconds(chart.row, session.sessionDate, session.barTime);
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
      dayPayload={dayOpenOnlyPayload}
      draft={draft}
      fiveMinuteChartLoading={fiveMinuteChartLoading}
      fiveMinutePayload={fiveMinuteOpenOnlyPayload}
      liveEntryLine={liveEntryLine}
      mainPayload={mainOpenOnlyPayload}
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
      onCompactVisibleColumnsChange={onCompactVisibleColumnsChange}
      onDraftChange={onDraftChange}
      onLiveEntryClose={closeLivePosition}
      onMainTimeframeChange={onMainTimeframeChange}
      onMainVisibleColumnsChange={onMainVisibleColumnsChange}
      onStage={onStage}
      onToggleDayChart={onToggleDayChart}
      onToggleFiveMinuteChart={onToggleFiveMinuteChart}
    />
  );
}

import { X } from "lucide-react";

import { ChartPanel, type ChartPayload, type LiveEntryLine } from "../../app/components/ChartPanel";
import type { CatalogPayload } from "./contracts";
import { ChartTradePanel } from "./LiveChartTradePanel";
import type { OrderRow, PositionRow, StageOrderContext } from "./portfolio";
import { quoteFromRow } from "./scanner";
import type { TradingSession } from "./time";

export const MAIN_DISPLAY_ITEMS = ["indicator.vwap", "indicator.tema_trend", "indicator.macd"];
export const LOWER_DISPLAY_ITEMS = ["indicator.vwap"];

type TradeDraft = {
  limit: string;
  quantity: string;
  side: "BUY" | "SELL";
  stop: string;
  type: string;
};

export type LiveChartsContainerProps = {
  availableCash: number;
  catalog: CatalogPayload | null;
  chartError: string;
  chartLoading: boolean;
  compactVisibleColumns: string[];
  dayChartLoading: boolean;
  dayPayload: ChartPayload | null;
  draft: TradeDraft;
  fiveMinutePayload: ChartPayload | null;
  fiveMinuteChartLoading: boolean;
  liveEntryLine: LiveEntryLine | null;
  mainPayload: ChartPayload | null;
  mainTimeframe: string;
  mainVisibleColumns: string[];
  orders: OrderRow[];
  position?: PositionRow;
  quote: ReturnType<typeof quoteFromRow>;
  row: Record<string, unknown>;
  selectedTicker: string;
  session: TradingSession;
  showDayChart: boolean;
  showFiveMinuteChart: boolean;
  splitEventError: string;
  splitVisibility: { day: boolean; fiveMinute: boolean; main: boolean };
  onCompactVisibleColumnsChange: (columns: string[]) => void;
  onDraftChange: (draft: TradeDraft) => void;
  onLiveEntryClose: () => void;
  onMainTimeframeChange: (timeframe: string) => void;
  onMainVisibleColumnsChange: (columns: string[]) => void;
  onShowDaySplitEventsChange: (visible: boolean) => void;
  onShowFiveMinuteSplitEventsChange: (visible: boolean) => void;
  onShowMainSplitEventsChange: (visible: boolean) => void;
  onStage: (side?: "BUY" | "SELL", status?: string, context?: Partial<StageOrderContext>) => void;
  onToggleDayChart: () => void;
  onToggleFiveMinuteChart: () => void;
};

export function LiveChartsContainer({
  availableCash,
  catalog,
  chartError,
  chartLoading,
  compactVisibleColumns,
  dayChartLoading,
  dayPayload,
  draft,
  fiveMinutePayload,
  fiveMinuteChartLoading,
  liveEntryLine,
  mainPayload,
  mainTimeframe,
  mainVisibleColumns,
  onCompactVisibleColumnsChange,
  onDraftChange,
  onLiveEntryClose,
  onMainTimeframeChange,
  onMainVisibleColumnsChange,
  onShowDaySplitEventsChange,
  onShowFiveMinuteSplitEventsChange,
  onShowMainSplitEventsChange,
  onStage,
  onToggleDayChart,
  onToggleFiveMinuteChart,
  orders,
  position,
  quote,
  row,
  selectedTicker,
  session,
  showDayChart,
  showFiveMinuteChart,
  splitEventError,
  splitVisibility,
}: LiveChartsContainerProps) {
  const mainOptions = mainPayload?.options;
  const compactOptions = fiveMinutePayload?.options ?? dayPayload?.options;
  const lowerChartCount = Number(showDayChart) + Number(showFiveMinuteChart);
  return (
    <div className="live-chart-trade-layout">
      <div className={lowerChartCount ? "live-chart-stack" : "live-chart-stack no-lower"}>
        <div className="live-main-chart-frame">
          <div className="live-chart-view-toggle" aria-label="Lower chart visibility">
            <button className={showDayChart ? "active" : ""} onClick={onToggleDayChart} type="button">
              Daily
            </button>
            <button className={showFiveMinuteChart ? "active" : ""} onClick={onToggleFiveMinuteChart} type="button">
              5m
            </button>
          </div>
          <ChartPanel
            catalogColumns={catalog?.columns ?? []}
            dataStatus={splitVisibility.main && splitEventError ? "Split events unavailable" : undefined}
            displayItemOptions={mainOptions?.display_items ?? catalog?.displayItems ?? []}
            emptyMessage="Select a scanner row to load charts."
            errorMessage={chartError}
            enableFullscreen={false}
            featureOptions={mainOptions?.feature_columns ?? []}
            indicatorOptions={mainOptions?.standard_indicators ?? MAIN_DISPLAY_ITEMS}
            initialFitMode="recent"
            loading={chartLoading}
            liveEntryLine={liveEntryLine}
            onPeriodChange={() => undefined}
            onShowSplitEventsChange={onShowMainSplitEventsChange}
            onTickerChange={() => undefined}
            onTimeframeChange={onMainTimeframeChange}
            onVisibleColumnsChange={onMainVisibleColumnsChange}
            payload={mainPayload}
            periodEnd={session.sessionDate}
            periodStart={session.sessionDate}
            showSplitEvents={splitVisibility.main}
            ticker={selectedTicker}
            tickerInputWidth={130}
            timeframe={mainTimeframe}
            timeframes={["1m", "5m", "1d"]}
            visibleColumns={mainVisibleColumns}
            onLiveEntryClose={onLiveEntryClose}
          />
        </div>
        {lowerChartCount ? (
          <div className={lowerChartCount === 1 ? "live-lower-chart-grid single" : "live-lower-chart-grid"}>
            {showDayChart ? (
              <div className="live-compact-chart">
                <div className="live-compact-chart-header">
                  <span>Daily / 60 days</span>
                  <button className="toolbar-button compact" onClick={onToggleDayChart} title="Hide daily chart" type="button">
                    <X size={12} />
                  </button>
                </div>
                <ChartPanel
                  catalogColumns={catalog?.columns ?? []}
                  dataStatus={splitVisibility.day && splitEventError ? "Split events unavailable" : undefined}
                  displayItemOptions={[]}
                  emptyMessage="No daily chart data."
                  errorMessage={chartError}
                  enableFullscreen={false}
                  featureOptions={[]}
                  indicatorOptions={[]}
                  loading={dayChartLoading}
                  daySeparatorsVisible={false}
                  onTickerChange={() => undefined}
                  onShowSplitEventsChange={onShowDaySplitEventsChange}
                  onTimeframeChange={() => undefined}
                  onVisibleColumnsChange={() => undefined}
                  payload={dayPayload}
                  showIndicatorControls={false}
                  showSplitEvents={splitVisibility.day}
                  ticker={selectedTicker}
                  timeframe="1d"
                  timeframes={["1d"]}
                  visibleColumns={[]}
                />
              </div>
            ) : null}
            {showFiveMinuteChart ? (
              <div className="live-compact-chart">
                <div className="live-compact-chart-header">
                  <span>5m / last day</span>
                  <button className="toolbar-button compact" onClick={onToggleFiveMinuteChart} title="Hide 5m chart" type="button">
                    <X size={12} />
                  </button>
                </div>
                <ChartPanel
                  catalogColumns={catalog?.columns ?? []}
                  dataStatus={splitVisibility.fiveMinute && splitEventError ? "Split events unavailable" : undefined}
                  displayItemOptions={compactOptions?.display_items ?? catalog?.displayItems ?? []}
                  emptyMessage="No 5m chart data."
                  errorMessage={chartError}
                  enableFullscreen={false}
                  featureOptions={compactOptions?.feature_columns ?? []}
                  indicatorOptions={LOWER_DISPLAY_ITEMS}
                  loading={fiveMinuteChartLoading}
                  initialFitMode="last_market_day"
                  onTickerChange={() => undefined}
                  onShowSplitEventsChange={onShowFiveMinuteSplitEventsChange}
                  onTimeframeChange={() => undefined}
                  onVisibleColumnsChange={onCompactVisibleColumnsChange}
                  payload={fiveMinutePayload}
                  showSplitEvents={splitVisibility.fiveMinute}
                  ticker={selectedTicker}
                  timeframe="5m"
                  timeframes={["5m"]}
                  visibleColumns={compactVisibleColumns}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
      <ChartTradePanel
        availableCash={availableCash}
        draft={draft}
        orders={orders}
        position={position}
        quote={quote}
        row={row}
        selectedTicker={selectedTicker}
        session={session}
        onDraftChange={onDraftChange}
        onStage={onStage}
      />
    </div>
  );
}

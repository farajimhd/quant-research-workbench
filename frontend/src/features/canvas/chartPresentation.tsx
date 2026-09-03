import { Activity } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { UTCTimestamp } from "lightweight-charts";

import { api } from "../../api/client";
import { CANVAS_SETTINGS_STORAGE_KEY, type CanvasChartTimeframe, type CanvasLinkContext } from "../../app/canvasWorkspace";
import { ChartPanel, type ChartAppearanceDefaults, type ChartPayload, type LiveEntryLine } from "../../app/components/ChartPanel";
import { stockSplitTimelineEvents, useStockSplitEvents } from "../../app/components/chartSplitEvents";
import {
  DEFAULT_STRATEGY_CHART_PRESENTATION,
  strategyInvalidationZones,
  strategyPresentationMarkers,
  type StrategyChartPresentation,
  type StrategyDecisionEvent,
} from "../../app/strategyPresentation";
import { acquireBarGptScope, canvasBarGptScopeId } from "../charts/barGptScopeLease";
import {
  type BarGptChartVersion,
  type BarGptForecast,
  type BarGptForecastPayload,
  type BarGptScopePayload,
  type CanonicalTradingPreview,
  type CanvasChartSettings,
  type CanvasLiveChartState,
  EMPTY_STRATEGY_DECISIONS,
  type HistoricalBar,
  type HistoricalIndicator,
  type PreviewRow,
  type QmdStructureEvent,
  type QmdStructureLevelCandidate,
  type QmdUnifiedStructureLevel,
} from "./contracts";
import {
  CHART_INDICATORS,
  HISTORICAL_TIMEFRAMES,
  INDICATOR_SERIES,
  MACRO_TIMEFRAMES,
  indicatorGuide,
  movingAverageGuide,
  qmdIndicatorKnowledge,
} from "./configuration";
import {
  extendedSessionRegions,
  qmdMarketSignalChartMarkers,
} from "./chartData";
import { boundedUnit, finiteNumber } from "./numbers";
import { formatQuantity, money, nestedValue } from "./presentationFormat";
import {
  QMD_STRUCTURE_TIMEFRAMES,
  isQmdStructureLevelCandidate,
  qmdStructureBreakLayerId,
  qmdStructureSwingLayerId,
  qmdStructureTimeframeSeconds,
  retainStructureEventsPerTimeframe,
} from "./structureModel";

const BAR_GPT_NEXT_BAR_TIMEFRAMES = new Set<CanvasChartTimeframe>(["1s", "5s", "10s", "30s", "1m", "5m", "1h"]);
const BAR_GPT_ORIGIN_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function ChartPreview({
  appearanceDefaults,
  baseHeight,
  canvasId,
  changeAsOf,
  chartSettings,
  instanceId,
  linkContext,
  liveChart,
  logoUrl,
  onChartSettingsChange,
  onLinkContextChange,
  symbolEditable,
  timeframes = HISTORICAL_TIMEFRAMES,
  toolbarVariant = "full",
  fillHeight = false,
  fullSessionReview = false,
  strategyDecisions = EMPTY_STRATEGY_DECISIONS,
  strategyPresentation = DEFAULT_STRATEGY_CHART_PRESENTATION,
  showTradeAnnotations = true,
  trading,
}: {
  appearanceDefaults?: ChartAppearanceDefaults;
  baseHeight?: number;
  canvasId: string;
  changeAsOf: string;
  chartSettings: CanvasChartSettings;
  instanceId: string;
  linkContext: CanvasLinkContext;
  liveChart: CanvasLiveChartState;
  logoUrl?: string;
  onChartSettingsChange: (next: CanvasChartSettings) => void;
  onLinkContextChange: (patch: Partial<CanvasLinkContext>) => void;
  symbolEditable: boolean;
  timeframes?: CanvasChartTimeframe[];
  toolbarVariant?: "full" | "compact";
  fillHeight?: boolean;
  fullSessionReview?: boolean;
  strategyDecisions?: StrategyDecisionEvent[];
  strategyPresentation?: StrategyChartPresentation;
  showTradeAnnotations?: boolean;
  trading?: CanonicalTradingPreview;
}) {
  const [barGptForecasts, setBarGptForecasts] = useState<BarGptForecast[]>([]);
  const [barGptScope, setBarGptScope] = useState<BarGptScopePayload | null>(null);
  const [barGptError, setBarGptError] = useState("");
  const [barGptInferring, setBarGptInferring] = useState(false);
  const [barGptOriginOverrideUs, setBarGptOriginOverrideUs] = useState<number | null>(null);
  const indicators = liveChart.indicators;
  const visibleIndicators = chartSettings.visibleIndicators;
  const timeframe = chartSettings.timeframe;
  const splitEvents = useStockSplitEvents(linkContext.symbol, Date.parse(changeAsOf), chartSettings.showSplitEvents);
  const latestChartBar = liveChart.bars[liveChart.bars.length - 1];
  const barGptClockUs = latestChartBar ? Math.floor(Date.parse(latestChartBar.bar_end || latestChartBar.bar_start) * 1000) : undefined;
  const barGptOriginOptions = useMemo(() => barGptChartOrigins(liveChart.bars), [liveChart.bars]);
  const showForecastCandles = chartSettings.visibleIndicators.includes("model.bargpt.forecast.candles");
  const forecastLineComponents = (["open", "high", "low", "close"] as const).filter((component) =>
    chartSettings.visibleIndicators.includes(`model.bargpt.forecast.${component}`),
  );
  const showBarGpt = showForecastCandles || forecastLineComponents.length > 0;
  const barGptVersion = chartSettings.barGptVersion;
  const barGptTriggerMode = chartSettings.barGptTriggerMode;
  const barGptView = BAR_GPT_NEXT_BAR_TIMEFRAMES.has(timeframe) ? timeframe : null;
  const barGptOriginUs = barGptOriginOverrideUs && barGptOriginOptions.some((row) => row.originUs === barGptOriginOverrideUs)
    ? barGptOriginOverrideUs
    : barGptClockUs;
  const barGptBaseScopeId = canvasBarGptScopeId(canvasId, instanceId);
  const barGptScopeId = barGptTriggerMode === "manual" && barGptOriginUs
    ? `${barGptBaseScopeId}:origin:${barGptOriginUs}`
    : barGptBaseScopeId;
  const barGptForecastPalette = readBarGptForecastPalette();
  useEffect(() => {
    setBarGptOriginOverrideUs(null);
    setBarGptForecasts([]);
  }, [linkContext.symbol, timeframe]);
  useEffect(() => {
    if (!showBarGpt || !barGptView) return;
    return acquireBarGptScope(barGptScopeId);
  }, [barGptScopeId, barGptView, showBarGpt]);
  useEffect(() => {
    if (!showBarGpt || !barGptView) {
      setBarGptForecasts([]);
      setBarGptScope(null);
      setBarGptError("");
      return;
    }
    let cancelled = false;
    let timer = 0;
    let requestController: AbortController | null = null;
    const refresh = async () => {
      const controller = new AbortController();
      requestController = controller;
      try {
        const scope = await api<BarGptScopePayload>(`/api/bar-gpt/scopes/${encodeURIComponent(barGptScopeId)}`, {
          method: "PUT",
          body: JSON.stringify({
            mode: liveChart.pointInTime || barGptTriggerMode === "manual" ? "replay" : "live",
            trigger_mode: barGptTriggerMode,
            tickers: [linkContext.symbol],
            model_ids: [`bar_gpt_${barGptVersion}`],
            watchlist_ids: [],
            clock_us: liveChart.pointInTime || barGptTriggerMode === "manual" ? barGptOriginUs : null,
            revision: liveChart.pointInTime || barGptTriggerMode === "manual"
              ? Math.max(1, Math.floor((barGptOriginUs ?? Date.now() * 1000) / 1_000_000))
              : 1,
            ttl_ms: 30_000,
            source: "canvas.chart",
          }),
          signal: controller.signal,
          timeoutMs: 10_000,
        });
        if (!cancelled) setBarGptScope(scope);
        const forecasts = await api<BarGptForecastPayload>(`/api/model-features/chart/${encodeURIComponent(linkContext.symbol)}?model_version=${encodeURIComponent(barGptVersion)}&scope_id=${encodeURIComponent(barGptScopeId)}&forecast_kind=next_bar&timeframe=${encodeURIComponent(barGptView)}`, { signal: controller.signal, timeoutMs: 10_000 });
        if (!cancelled) {
          const rows = latestForecastsByHorizon(forecasts.rows);
          setBarGptForecasts(rows);
          setBarGptError(scope.status === "unavailable" ? scope.error || "BarGPT is unavailable." : "");
        }
      } catch (error) {
        if (!cancelled) {
          setBarGptForecasts([]);
          setBarGptError(error instanceof Error ? error.message : "BarGPT forecast refresh failed.");
        }
      } finally {
        if (requestController === controller) requestController = null;
      }
      if (!cancelled) timer = window.setTimeout(refresh, 5_000);
    };
    void refresh();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      requestController?.abort();
    };
  }, [barGptOriginUs, barGptScopeId, barGptTriggerMode, barGptVersion, barGptView, linkContext.symbol, liveChart.pointInTime, showBarGpt]);
  const strategyPresentationAvailable = showTradeAnnotations && supportsPositionPresentation(timeframe);
  const tradeAnnotations = useMemo(
    () => strategyPresentationAvailable ? positionLifecycleAnnotations(trading, linkContext.symbol) : [],
    [linkContext.symbol, strategyPresentationAvailable, trading],
  );
  const payload = useMemo<ChartPayload>(() => {
    const marketSignalMarkers = qmdMarketSignalChartMarkers(
      liveChart.marketSignalEvents,
      liveChart.bars,
      visibleIndicators,
    );
    const strategyMarkers = strategyPresentationMarkers(
      strategyDecisions.filter((event) => event.ticker === linkContext.symbol),
      liveChart.bars,
      strategyPresentation,
    );
    const strategyInvalidations = strategyInvalidationZones(
      strategyDecisions.filter((event) => event.ticker === linkContext.symbol),
      liveChart.bars,
      strategyPresentation,
    );
    const realizedCandles = liveChart.bars.map((bar) => ({ close: bar.close, high: bar.high, low: bar.low, open: bar.open, time: Date.parse(bar.bar_start) / 1000 }));
    const timelineEvents = stockSplitTimelineEvents(linkContext.symbol, splitEvents.events, liveChart.bars.map((bar) => ({
      sessionDate: bar.session_date || bar.bar_start.slice(0, 10),
      time: Date.parse(bar.bar_start) / 1000,
    })));
    const forecastCandles = showForecastCandles ? barGptForecasts
      .filter((row) => row.geometry_valid)
      .map((row) => ({
        close: row.close, high: row.high, low: row.low, open: row.open,
        time: row.target_start_us / 1_000_000,
        color: row.close >= row.open ? barGptForecastPalette.upFill : barGptForecastPalette.downFill,
        borderColor: row.close >= row.open ? barGptForecastPalette.upBorder : barGptForecastPalette.downBorder,
        wickColor: row.close >= row.open ? barGptForecastPalette.upWick : barGptForecastPalette.downWick,
      })) : [];
    const forecastLines = forecastLineComponents.map((component) => ({
      column: `model.bargpt.${barGptVersion}.next_bar.${barGptView ?? "unavailable"}.${component}.value`,
      label: `BarGPT ${barGptVersion} · next ${barGptView ?? timeframe} bar · ${component}`,
      style: "line" as const,
      color: ({ open: "#F59E0B", high: "#22C55E", low: "#EF4444", close: "#A78BFA" })[component],
      lineWidth: component === "close" ? 2 : 1,
      lineStyle: "dashed" as const,
      opacity: component === "close" ? 0.9 : 0.72,
      data: barGptForecasts.map((row) => ({ time: row.target_start_us / 1_000_000, value: row[component] })),
    }));
    const originMarker = barGptForecasts.length ? barGptOriginOptions.find((row) => row.originUs === barGptOriginUs) : undefined;
    return {
      candles: realizedCandles,
      forecast_candles: forecastCandles,
      markers: [
        ...(marketSignalMarkers ?? []),
        ...strategyMarkers,
        ...(originMarker ? [{ color: "var(--primary)", position: "aboveBar" as const, shape: "circle" as const, text: "BarGPT origin", time: originMarker.candleTime as UTCTimestamp }] : []),
      ],
      timeline_events: timelineEvents,
      oscillator_series: historicalIndicatorSeries(indicators, "oscillator", visibleIndicators),
      overlay_series: [...historicalIndicatorSeries(indicators, "price", visibleIndicators), ...forecastLines],
      price_zones: [
        ...historicalMarketLevelZones(indicators, liveChart.bars, liveChart.structureEvents, liveChart.structureLevelHistory, visibleIndicators, timeframe),
        ...strategyInvalidations,
      ],
      regions: MACRO_TIMEFRAMES.has(timeframe) ? [] : extendedSessionRegions(liveChart.bars),
      execution_annotations: [],
      trade_annotations: tradeAnnotations,
      volume: chartSettings.showVolume ? liveChart.bars.map((bar) => ({ color: bar.close >= bar.open ? "var(--success)" : "var(--danger)", time: Date.parse(bar.bar_start) / 1000, value: bar.volume })) : [],
    };
  }, [barGptForecastPalette.downBorder, barGptForecastPalette.downFill, barGptForecastPalette.downWick, barGptForecastPalette.upBorder, barGptForecastPalette.upFill, barGptForecastPalette.upWick, barGptForecasts, barGptOriginOptions, barGptOriginUs, barGptVersion, barGptView, chartSettings.showVolume, forecastLineComponents.join("|"), indicators, linkContext.symbol, liveChart.bars, liveChart.marketSignalEvents, liveChart.structureEvents, liveChart.structureLevelHistory, showForecastCandles, splitEvents.events, strategyDecisions, strategyPresentation, timeframe, tradeAnnotations, visibleIndicators]);
  function updateChart(symbol: string, nextTimeframe: CanvasChartTimeframe) {
    onChartSettingsChange({
      ...chartSettings,
      showSplitEvents: nextTimeframe === timeframe ? chartSettings.showSplitEvents : nextTimeframe === "1d",
      symbol,
      timeframe: nextTimeframe,
    });
    onLinkContextChange({ symbol });
  }
  const latestBar = liveChart.bars[liveChart.bars.length - 1];
  const sessionDate = latestBar?.session_date || latestBar?.bar_start.slice(0, 10);
  const activePosition = trading?.positions.find((row) => nestedValue(row, "instrument", "symbol") === linkContext.symbol && Number(row.quantity || 0) !== 0);
  const quantity = Number(activePosition?.quantity || 0);
  const averagePrice = Number(activePosition?.average_price || 0);
  const activeEntryOrder = trading?.orders.find((row) =>
    nestedValue(row, "instrument", "symbol") === linkContext.symbol
    && String(row.side || "").toUpperCase() === (quantity >= 0 ? "BUY" : "SELL")
    && !Boolean(row.terminal)
    && Number(row.total_quantity || 0) >= Math.abs(quantity),
  );
  const targetQuantity = Number(activeEntryOrder?.total_quantity || 0);
  const positionQuantityLabel = targetQuantity > Math.abs(quantity)
    ? `${formatQuantity(Math.abs(quantity))} filled / ${formatQuantity(targetQuantity)} target`
    : formatQuantity(Math.abs(quantity));
  const activeLifecycleAnnotation = tradeAnnotations.find((annotation) => annotation.status === "open");
  const protectionLabelParts = activeLifecycleAnnotation ? [
    { text: activeLifecycleAnnotation.stopPrice ? `SL ${compactPrice(activeLifecycleAnnotation.stopPrice)}` : "NO SL", tone: activeLifecycleAnnotation.stopPrice ? "price" as const : "pnlLoss" as const },
    { text: activeLifecycleAnnotation.targetPrices?.length ? `TP ${activeLifecycleAnnotation.targetPrices.map(compactPrice).join("/")}` : "NO TP", tone: activeLifecycleAnnotation.targetPrices?.length ? "price" as const : "pnlLoss" as const },
  ] : [{ text: "NO STRATEGY PLAN", tone: "pnlLoss" as const }];
  const positionLine = strategyPresentationAvailable && activePosition && averagePrice > 0 ? {
    color: quantity > 0 ? "var(--success)" : "var(--danger)",
    labelParts: [{ text: quantity > 0 ? "LONG" : "SHORT", tone: "label" as const }, { text: `${positionQuantityLabel} @ ${money(averagePrice)}`, tone: "price" as const }, ...protectionLabelParts],
    pnl: Number(activePosition.unrealized_pnl || 0),
    price: averagePrice,
    quantity,
  } satisfies LiveEntryLine : null;
  const emptyMessage = `No closed ${linkContext.symbol} ${timeframe} bars are available from QMD History at this Canvas clock.`;
  const barGptReady = Boolean(barGptView && barGptScope?.ticker_count && barGptScope.ready_count === barGptScope.ticker_count);
  const barGptWarm = barGptScope?.readiness?.find((row) => row.ticker === linkContext.symbol)?.warm;
  const barGptDetail = barGptError || barGptWarm?.error || "";
  const barGptState = !barGptView
    ? `No ${timeframe} prediction`
    : barGptError
    ? "Unavailable"
    : !barGptReady
      ? barGptWarm?.status === "failed"
        ? "Warm-up failed"
        : barGptWarm?.status === "queued"
          ? "Queued for context"
          : "Warming context"
      : barGptInferring
        ? "Inference running"
        : barGptForecasts.length
          ? `Next ${barGptView} candle ready`
          : barGptTriggerMode === "manual"
            ? "Ready for manual inference"
            : "Waiting for next 1s close";
  async function runManualInference() {
    if (!barGptView || !barGptOriginUs) return;
    setBarGptInferring(true);
    setBarGptError("");
    try {
      const response = await api<{ row_count: number }>("/api/bar-gpt/infer", {
        method: "POST",
        body: JSON.stringify({
          scope_id: barGptScopeId,
          tickers: [linkContext.symbol],
          model_ids: [`bar_gpt_${barGptVersion}`],
          origin_us: barGptOriginUs,
          request_id: `canvas:${instanceId}:${barGptOriginUs}`,
        }),
        timeoutMs: 120_000,
      });
      if (!response.row_count) {
        setBarGptError("Inference completed without a prediction row.");
      } else {
        const forecasts = await api<BarGptForecastPayload>(`/api/model-features/chart/${encodeURIComponent(linkContext.symbol)}?model_version=${encodeURIComponent(barGptVersion)}&scope_id=${encodeURIComponent(barGptScopeId)}&forecast_kind=next_bar&timeframe=${encodeURIComponent(barGptView)}`, { timeoutMs: 10_000 });
        setBarGptForecasts(latestForecastsByHorizon(forecasts.rows));
      }
    } catch (error) {
      setBarGptError(error instanceof Error ? error.message : "Manual inference failed.");
    } finally {
      setBarGptInferring(false);
    }
  }
  return <div className={`canvas-chart-with-model ${fillHeight ? "is-fill-height" : ""}`}>
    {showBarGpt ? <div className="canvas-bar-gpt-controls" data-state={!barGptView ? "unsupported" : barGptDetail ? "error" : barGptReady ? "ready" : "warming"}>
      <div className="canvas-bar-gpt-state"><Activity size={13} /><span>BarGPT</span><strong>{barGptState}</strong>{barGptDetail ? <small title={barGptDetail}>{barGptDetail}</small> : !barGptView ? <small>Next-bar candles exist only for trained model views.</small> : null}</div>
      <label><span>Model</span><select aria-label="BarGPT model version" onChange={(event) => onChartSettingsChange({ ...chartSettings, barGptVersion: event.target.value as BarGptChartVersion })} value={barGptVersion}><option value="v2">V2</option><option value="v3">V3</option></select></label>
      <label><span>Prediction</span><select aria-label="BarGPT prediction timeframe" disabled value={barGptView ?? "unavailable"}><option value={barGptView ?? "unavailable"}>{barGptView ? `Next ${barGptView} bar` : `Unavailable on ${timeframe}`}</option></select></label>
      <label><span>Trigger</span><select aria-label="BarGPT trigger mode" onChange={(event) => onChartSettingsChange({ ...chartSettings, barGptTriggerMode: event.target.value as CanvasChartSettings["barGptTriggerMode"] })} value={barGptTriggerMode}><option value="auto">Auto</option><option value="manual">Manual</option></select></label>
      {barGptTriggerMode === "manual" && barGptView ? <label className="canvas-bar-gpt-origin"><span>Origin (ET)</span><select aria-label="BarGPT inference origin" onChange={(event) => { setBarGptOriginOverrideUs(Number(event.target.value)); setBarGptForecasts([]); setBarGptError(""); }} value={barGptOriginUs ?? ""}>{barGptOriginOptions.map((row, index) => <option key={row.originUs} value={row.originUs}>{index === 0 ? `Latest · ${row.label}` : row.label}</option>)}</select></label> : null}
      {barGptTriggerMode === "manual" ? <button disabled={!barGptView || !barGptOriginUs || !barGptReady || barGptInferring} onClick={() => void runManualInference()} type="button">{barGptInferring ? "Running…" : "Infer now"}</button> : null}
    </div> : null}
    <ChartPanel appearanceDefaults={appearanceDefaults} baseHeight={baseHeight} canLoadEarlier={liveChart.canLoadEarlier} dataStatus={splitEvents.error ? "Split events unavailable" : timeframe === "1d" && liveChart.splitAdjusted ? "Split-adjusted" : undefined} deferInitialFitUntilLoaded={fullSessionReview} displayItemOptions={CHART_INDICATORS} emptyMessage={emptyMessage} enableFullscreen={false} errorMessage={liveChart.error || liveChart.historyError} featureOptions={[]} fillHeight={fillHeight} indicatorOptions={[]} initialFitMode="default" liveEntryLine={positionLine} loading={liveChart.loading} loadingEarlier={liveChart.loadingEarlier} onLoadEarlier={liveChart.loadEarlier} onShowSplitEventsChange={(showSplitEvents) => onChartSettingsChange({ ...chartSettings, showSplitEvents })} onTickerChange={(symbol) => updateChart(symbol.toUpperCase(), timeframe)} onTimeframeChange={(nextTimeframe) => updateChart(linkContext.symbol, nextTimeframe as CanvasChartTimeframe)} onVisibleColumnsChange={(nextVisibleIndicators) => onChartSettingsChange({ ...chartSettings, visibleIndicators: nextVisibleIndicators })} payload={payload} periodEnd={sessionDate} periodStart={sessionDate} settingsStorageKey={`${CANVAS_SETTINGS_STORAGE_KEY}.${instanceId}`} showSplitEvents={chartSettings.showSplitEvents} strategyPresentationEnabled={strategyPresentationAvailable} ticker={linkContext.symbol} tickerChangeAsOf={changeAsOf} tickerEditable={symbolEditable} tickerLogoUrl={logoUrl} timeframe={timeframe} timeframes={timeframes} toolbarVariant={toolbarVariant} visibleColumns={visibleIndicators} />
  </div>;
}

export function supportsPositionPresentation(timeframe: CanvasChartTimeframe): boolean {
  return !MACRO_TIMEFRAMES.has(timeframe);
}

export function positionLifecycleAnnotations(trading: CanonicalTradingPreview | undefined, symbol: string): NonNullable<ChartPayload["trade_annotations"]> {
  const executionsById = new Map((trading?.executions ?? []).map((row) => [String(row.execution_id || ""), row]));
  const normalizedSymbol = symbol.toUpperCase();
  const asOfTime = parsedTime(trading?.as_of) ?? Date.now() / 1_000;
  const activity = (trading?.strategy_chart_activity ?? trading?.strategy_activity ?? [])
    .filter((row) => String(row.ticker || "").toUpperCase() === normalizedSymbol)
    .map((row) => ({ row, time: Date.parse(String(row.event_time || "")) / 1000 }))
    .filter(({ time }) => Number.isFinite(time))
    .sort((left, right) => left.time - right.time);
  const lifecycles = (trading?.position_lifecycles ?? [])
    .filter((row) => String(nestedValue(row, "instrument", "symbol") || "").toUpperCase() === normalizedSymbol)
    .sort((left, right) => Date.parse(String(left.opened_at || "")) - Date.parse(String(right.opened_at || "")));
  return lifecycles.flatMap((row, lifecycleIndex) => {
    const status = String(row.status || "").toLowerCase() === "closed" ? "closed" : "open";
    const side = String(row.side || "LONG").toUpperCase();
    const executionIds = Array.isArray(row.execution_ids) ? row.execution_ids.map(String) : [];
    const actions = positionExecutionActions(executionIds.flatMap((executionId) => {
      const execution = executionsById.get(executionId);
      return execution ? [execution] : [];
    }), side);
    const entryAction = actions[0];
    const exitAction = status === "closed" && actions.length > 1 ? actions.at(-1) : undefined;
    const entryPrice = Number(row.entry_price ?? entryAction?.price ?? 0);
    const exitPrice = status === "closed" ? Number(row.exit_price ?? exitAction?.price ?? 0) : undefined;
    const entryTime = entryAction?.time ?? Date.parse(String(row.opened_at || "")) / 1000;
    const exitTime = status === "closed" ? exitAction?.time ?? parsedTime(row.closed_at) : undefined;
    const endTime = exitTime ?? asOfTime;
    if (!Number.isFinite(entryPrice) || entryPrice <= 0 || !Number.isFinite(entryTime) || !Number.isFinite(endTime)) return [];
    if (status === "closed" && (!Number.isFinite(exitPrice) || Number(exitPrice) <= 0 || exitTime === undefined)) return [];
    const previousCloseTime = lifecycleIndex > 0
      ? Date.parse(String(lifecycles[lifecycleIndex - 1].closed_at || "")) / 1000
      : Number.NEGATIVE_INFINITY;
    const entryDecision = [...activity].reverse().find(({ row: event, time }) =>
      String(event.event_type || "") === "decision"
      &&
      String(event.action || "") === (side === "SHORT" ? "enter_short" : "enter_long")
      && time > previousCloseTime
      && time <= entryTime,
    );
    const gateSnapshot = (entryDecision?.row.chart_plan as PreviewRow | undefined)
      ?? (entryDecision?.row.gate_snapshot as PreviewRow | undefined)
      ?? {};
    const decisionValues = (gateSnapshot.decision_values as PreviewRow | undefined) ?? {};
    const structuralTrigger = (gateSnapshot.unified_structural_trigger as PreviewRow | undefined) ?? {};
    const priorLevels = Array.isArray(structuralTrigger.prior_snapshot_levels)
      ? structuralTrigger.prior_snapshot_levels as PreviewRow[]
      : structuralTrigger.level ? [structuralTrigger.level as PreviewRow] : [];
    const levelPrices = uniquePositivePrices(priorLevels.map((level) =>
      level.entry_boundary
      ?? level.combined_entry_boundary
      ?? level.unified_break_boundary
      ?? level.threshold_price
      ?? level.price
      ?? level.upper,
    )).slice(0, 3);
    const targetSelection = (gateSnapshot.profit_target_selection as PreviewRow | undefined) ?? {};
    const selectedTargets = Array.isArray(targetSelection.selected_target_prices)
      ? targetSelection.selected_target_prices
      : [];
    const qualifiedTargets = Array.isArray(targetSelection.qualified_levels)
      ? (targetSelection.qualified_levels as PreviewRow[]).map((level) => level.target_price ?? level.price)
      : [];
    const plannedTargetPrices = uniquePositivePrices(
      qualifiedTargets.length
        ? qualifiedTargets
        : Array.isArray(decisionValues.profit_targets)
        ? decisionValues.profit_targets
        : selectedTargets,
    ).slice(0, 3);
    const plannedStopPrice = positiveNumber(decisionValues.initial_stop ?? decisionValues.invalidation_price);
    const planStartTime = entryDecision?.time ?? entryTime;
    const quantity = Math.abs(Number(row.quantity || 0));
    const pnl = Number(row.net_pnl || row.gross_pnl || 0);
    const openingSide = side === "SHORT" ? "SELL" : "BUY";
    const lifecycleActions = status === "closed" ? actions.slice(1, -1) : actions.slice(1);
    const fills: NonNullable<NonNullable<ChartPayload["trade_annotations"]>[number]["fills"]> = lifecycleActions.map((action) => {
      const kind = action.side === openingSide
        ? "add" as const
        : normalizedExecutionRole(action.executionRole, action.price, entryPrice, side);
      const label = positionActionLabel(kind, action.quantity, action.price);
      return {
        kind,
        label,
        price: action.price,
        quantity: action.quantity,
        side: action.side,
        time: action.time,
      };
    });
    let activeStop = plannedStopPrice;
    let activeTarget = plannedTargetPrices[0];
    activity.forEach(({ row: event, time }) => {
      if (time <= planStartTime || time >= endTime) return;
      const eventGates = (event.chart_plan as PreviewRow | undefined)
        ?? (event.gate_snapshot as PreviewRow | undefined)
        ?? {};
      const values = (eventGates.decision_values as PreviewRow | undefined) ?? {};
      const nextStop = positiveNumber(values.active_stop ?? values.invalidation_price);
      if (nextStop !== undefined && nextStop !== activeStop) {
        activeStop = nextStop;
        fills.push({ kind: "stop_change", label: `SL@${compactPrice(nextStop)}`, price: nextStop, side: "SELL", time });
      }
      const management = (event.management_event as PreviewRow | undefined) ?? {};
      const operation = String(management.operation ?? event.operation ?? "");
      const nextTarget = positiveNumber(values.profit_target ?? management.target_price);
      if (operation === "profit_target_replaced" && nextTarget !== undefined && nextTarget !== activeTarget) {
        activeTarget = nextTarget;
        fills.push({ kind: "target_change", label: `TP@${compactPrice(nextTarget)}`, price: nextTarget, side: "SELL", time });
      }
      const managementActions = Array.isArray(management.actions) ? management.actions as PreviewRow[] : [];
      managementActions.forEach((managementAction) => {
        if (String(managementAction.action || "") !== "place_missing_oca_protection") return;
        const protectedQuantity = positiveNumber(managementAction.quantity ?? management.required_quantity);
        const repairedStop = positiveNumber(managementAction.stop_price);
        const repairedTarget = positiveNumber(managementAction.target_price);
        if (repairedStop !== undefined) fills.push({
          kind: "protection_repair",
          label: `RECON ${protectedQuantity ? formatQuantity(protectedQuantity) + " · " : ""}SL@${compactPrice(repairedStop)}`,
          price: repairedStop,
          quantity: protectedQuantity,
          side: "SELL",
          time,
        });
        if (repairedTarget !== undefined) fills.push({
          kind: "protection_repair",
          label: `RECON ${protectedQuantity ? formatQuantity(protectedQuantity) + " · " : ""}TP@${compactPrice(repairedTarget)}`,
          price: repairedTarget,
          quantity: protectedQuantity,
          side: "SELL",
          time,
        });
      });
      if (operation === "entry_acquisition_frozen_before_exit") fills.push({
        kind: "entry_freeze",
        label: "ENTRY FROZEN",
        price: activeStop ?? entryPrice,
        side: "SELL",
        time,
      });
    });
    const currentOrders = status === "open" ? lifecycleProtectionOrders(trading?.orders ?? [], row, normalizedSymbol, side) : [];
    const brokerStops = uniquePositivePrices(currentOrders.filter((order) => ["protective_stop", "trailing_stop", "protective_exit"].includes(orderRole(order))).map((order) => order.stop_price));
    const brokerTargets = uniquePositivePrices(currentOrders.filter((order) => orderRole(order) === "profit_target").map((order) => order.limit_price))
      .sort((left, right) => side === "SHORT" ? right - left : left - right);
    if (brokerStops.length) activeStop = side === "SHORT" ? Math.min(...brokerStops) : Math.max(...brokerStops);
    const targetPrices = brokerTargets.length ? brokerTargets : activeTarget !== undefined ? [activeTarget] : plannedTargetPrices;
    fills.sort((left, right) => left.time - right.time);
    const entryQuantity = entryAction?.quantity ?? quantity;
    const exitQuantity = exitAction?.quantity || quantity;
    const exitKind = status === "closed" && exitAction
      ? normalizedExecutionRole(exitAction.executionRole, exitAction.price, entryPrice, side)
      : "position_exit";
    const exitLabel = positionExitLabel(String(row.exit_reason || ""), exitKind);
    return [{
      color: pnl >= 0 ? "var(--success)" : "var(--danger)",
      entryColor: side === "SHORT" ? "#dc2626" : "#16a34a",
      entryLabel: `${side === "SHORT" ? "Short" : "Long"} ${formatQuantity(entryQuantity)} @ ${entryPrice.toFixed(2)}`,
      entryPrice,
      entryTime,
      endTime,
      exitColor: status === "closed" ? side === "SHORT" ? "#16a34a" : "#dc2626" : undefined,
      exitLabel: status === "closed" ? `${exitLabel} ${formatQuantity(exitQuantity)} @ ${Number(exitPrice).toFixed(2)} · ${signedMoneyShort(pnl)}` : undefined,
      exitLabelColor: status === "closed" ? pnl > 0 ? "#16A34A" : pnl < 0 ? "#DC2626" : "#C2410C" : undefined,
      exitPrice,
      exitTime,
      fills,
      guideStartTime: planStartTime,
      id: String(row.lifecycle_id || `${normalizedSymbol}:${entryTime}:${endTime}`),
      levelPrices,
      pnl,
      status,
      stopPrice: activeStop,
      targetPrices,
    }];
  });
}

function parsedTime(value: unknown): number | undefined {
  const time = Date.parse(String(value || "")) / 1_000;
  return Number.isFinite(time) ? time : undefined;
}

function orderRole(order: PreviewRow): string {
  const explicit = String(
    order.execution_role
    ?? nestedValue(order, "raw", "canonical_metadata", "execution_role")
    ?? nestedValue(order, "raw", "raw", "canonical_metadata", "execution_role")
    ?? nestedValue(order, "raw", "execution_role")
    ?? "",
  ).toLowerCase();
  if (explicit) return explicit;
  const orderType = String(order.order_type || "").toUpperCase().replaceAll(" ", "");
  if (orderType.includes("TRAIL")) return "trailing_stop";
  if (orderType.includes("STP") || orderType.includes("STOP")) return "protective_stop";
  if (orderType.includes("LMT") || orderType.includes("LIMIT")) return "profit_target";
  return "";
}

function lifecycleProtectionOrders(orders: PreviewRow[], lifecycle: PreviewRow, symbol: string, side: string): PreviewRow[] {
  const lifecycleOrderIds = new Set(Array.isArray(lifecycle.order_ids) ? lifecycle.order_ids.map(String) : []);
  const closingSide = side === "SHORT" ? "BUY" : "SELL";
  return orders.filter((order) => {
    if (Boolean(order.terminal)) return false;
    if (String(nestedValue(order, "instrument", "symbol") || "").toUpperCase() !== symbol) return false;
    if (String(order.account_id || "") !== String(lifecycle.account_id || "")) return false;
    if (String(order.side || "").toUpperCase() !== closingSide) return false;
    const orderId = String(order.broker_order_id || order.client_order_id || "");
    const sameLifecycle = lifecycleOrderIds.size === 0 || lifecycleOrderIds.has(orderId) || String(order.run_id || "") === String(lifecycle.run_id || "");
    return sameLifecycle && ["profit_target", "protective_stop", "trailing_stop", "protective_exit"].includes(orderRole(order));
  });
}

function positiveNumber(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : undefined;
}

function uniquePositivePrices(values: unknown[]): number[] {
  return [...new Set(values.map(positiveNumber).filter((value): value is number => value !== undefined))];
}

function compactPrice(value: number): string {
  return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function positionExitLabel(exitReason: string, fallbackKind: string): string {
  const reason = exitReason.trim().toLowerCase();
  if (reason.includes("macd")) return "MACD exit";
  if (reason.includes("vwap")) return "VWAP exit";
  if (reason.includes("stop")) return "Stop exit";
  if (reason.includes("target") || fallbackKind === "profit_target") return "Target filled";
  if (reason.includes("breakout") || reason.includes("structure")) return "Structure exit";
  return "Exit";
}

type PositionExecutionRole = "entry" | "managed_exit" | "profit_target" | "protective_stop" | "trailing_stop" | "protective_exit" | "";
type PositionExecutionAction = { executionRole: PositionExecutionRole; orderId: string; price: number; quantity: number; side: "BUY" | "SELL"; time: number };

function positionExecutionActions(executions: PreviewRow[], positionSide: string): PositionExecutionAction[] {
  type Aggregate = PositionExecutionAction & { notional: number };
  const byOrderAndSide = new Map<string, Aggregate>();
  executions.forEach((row, index) => {
    const rawSide = String(row.side || "").toUpperCase();
    const side = rawSide === "BUY" || rawSide === "B" ? "BUY" : rawSide === "SELL" || rawSide === "S" ? "SELL" : null;
    const quantity = Math.abs(Number(row.quantity || 0));
    const price = Number(row.price || 0);
    const time = Date.parse(String(row.source_event_time || "")) / 1000;
    if (!side || !Number.isFinite(quantity) || !Number.isFinite(price) || !Number.isFinite(time) || quantity <= 0 || price <= 0) return;
    const clientOrderId = String(row.client_order_id || nestedValue(row, "raw", "order_ref") || "");
    const persistedRole = String(nestedValue(row, "raw", "canonical_metadata", "execution_role") || "") as PositionExecutionRole;
    const executionRole: PositionExecutionRole = persistedRole || (clientOrderId.includes("-entry") ? "entry" : "");
    const orderId = String(row.broker_order_id || clientOrderId || row.execution_id || `fill:${index}`);
    const key = `${orderId}:${side}`;
    const current = byOrderAndSide.get(key) ?? { executionRole, orderId, time, notional: 0, price: 0, quantity: 0, side };
    current.time = Math.min(current.time, time);
    current.notional += quantity * price;
    current.quantity += quantity;
    current.price = current.notional / current.quantity;
    byOrderAndSide.set(key, current);
  });
  const openingSide = positionSide === "SHORT" ? "SELL" : "BUY";
  const byLevel = new Map<string, Aggregate>();
  [...byOrderAndSide.values()].forEach((action) => {
    const second = Math.floor(action.time);
    const priceTick = action.price.toFixed(2);
    const role = action.executionRole || (action.side === openingSide ? "entry" : "");
    const key = `${role}:${action.side}:${second}:${priceTick}`;
    const current = byLevel.get(key) ?? { ...action, executionRole: role as PositionExecutionRole, notional: 0, quantity: 0 };
    current.time = Math.min(current.time, action.time);
    current.notional += action.notional;
    current.quantity += action.quantity;
    current.price = current.notional / current.quantity;
    byLevel.set(key, current);
  });
  return [...byLevel.values()]
    .sort((left, right) => left.time - right.time || Number(right.side === openingSide) - Number(left.side === openingSide))
    .map(({ notional: _notional, ...action }) => action);
}

function normalizedExecutionRole(
  role: PositionExecutionRole,
  price: number,
  entryPrice: number,
  positionSide: string,
): "profit_target" | "protective_stop" | "trailing_stop" | "position_exit" {
  if (role === "profit_target" || role === "protective_stop" || role === "trailing_stop") return role;
  const favorable = positionSide === "SHORT" ? price < entryPrice : price > entryPrice;
  return favorable ? "profit_target" : "position_exit";
}

function positionActionLabel(
  kind: "add" | "profit_target" | "protective_stop" | "trailing_stop" | "position_exit",
  quantity: number,
  price: number,
): string {
  const name = {
    add: "A",
    profit_target: "TP",
    protective_stop: "SL",
    trailing_stop: "TSL",
    position_exit: "X",
  }[kind];
  return `${name}${formatQuantity(quantity)}@${compactPrice(price)}`;
}

function signedMoneyShort(value: number): string {
  return `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(2)}`;
}

function durationSeconds(value: string): number {
  const match = /^(\d+)(s|m|h)$/.exec(value);
  if (!match) return 0;
  const amount = Number(match[1]);
  return amount * (match[2] === "h" ? 3600 : match[2] === "m" ? 60 : 1);
}

function barGptChartOrigins(bars: HistoricalBar[]) {
  const seen = new Set<number>();
  const origins: Array<{ candleTime: number; label: string; originUs: number }> = [];
  for (let index = bars.length - 1; index >= 0 && origins.length < 1_000; index -= 1) {
    const bar = bars[index];
    if (bar.is_closed === false) continue;
    const originMs = Date.parse(bar.bar_end || bar.bar_start);
    const candleMs = Date.parse(bar.bar_start);
    if (!Number.isFinite(originMs) || !Number.isFinite(candleMs)) continue;
    const originUs = Math.floor(originMs * 1_000);
    if (seen.has(originUs)) continue;
    seen.add(originUs);
    origins.push({ candleTime: candleMs / 1_000, label: BAR_GPT_ORIGIN_FORMATTER.format(originMs), originUs });
  }
  return origins;
}

function readBarGptForecastPalette() {
  const styles = typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
  const color = (name: string, fallback: string) => styles?.getPropertyValue(name).trim() || fallback;
  return {
    upFill: color("--chart-forecast-up-fill", "rgba(51, 228, 42, 0.28)"),
    upBorder: color("--chart-forecast-up-border", "rgba(51, 228, 42, 0.62)"),
    upWick: color("--chart-forecast-up-wick", "rgba(77, 199, 70, 0.70)"),
    downFill: color("--chart-forecast-down-fill", "rgba(253, 14, 80, 0.28)"),
    downBorder: color("--chart-forecast-down-border", "rgba(253, 14, 80, 0.62)"),
    downWick: color("--chart-forecast-down-wick", "rgba(197, 42, 85, 0.70)"),
  };
}

function latestForecastsByHorizon(rows: BarGptForecast[]): BarGptForecast[] {
  const latestOrigin = rows.reduce((value, row) => Math.max(value, row.origin_us), 0);
  return rows
    .filter((row) => row.origin_us === latestOrigin)
    .sort((left, right) => durationSeconds(left.horizon) - durationSeconds(right.horizon));
}

function historicalMarketLevelZones(
  rows: HistoricalIndicator[],
  bars: HistoricalBar[],
  structureEvents: QmdStructureEvent[],
  structureLevelHistory: QmdStructureLevelCandidate[],
  visibleIndicators: string[],
  timeframe: CanvasChartTimeframe,
): NonNullable<ChartPayload["price_zones"]> {
  if (!rows.length || !bars.length) return [];
  const chartEnd = Date.parse(bars[bars.length - 1].bar_end || bars[bars.length - 1].bar_start) / 1000 + 1;
  const zones: NonNullable<ChartPayload["price_zones"]> = [];
  if (visibleIndicators.includes("indicator.qmd_generic_structure")) {
    pushCurrentStructureLevels(zones, rows, chartEnd, timeframe);
    pushEventStructureSwingLevels(
      zones,
      structureEvents.length ? structureEvents : structureEventsFromSampledRows(rows),
      chartEnd,
      timeframe,
    );
    pushStructureEvents(
      zones,
      structureEvents.length ? structureEvents : structureEventsFromSampledRows(rows),
      chartEnd,
      timeframe,
    );
  }
  if (visibleIndicators.includes("indicator.qmd_unified_structure")) {
    pushUnifiedStructureLevels(zones, rows, chartEnd);
  }
  if (visibleIndicators.includes("indicator.qmd_level_footprint")) {
    pushLevelVolumeFootprint(
      zones,
      rows,
      structureLevelHistory,
      structureEvents.length ? structureEvents : structureEventsFromSampledRows(rows),
      chartEnd,
      timeframe,
      String(rows[rows.length - 1]?.session_date ?? ""),
    );
  }
  if (visibleIndicators.includes("indicator.qmd_reference_levels")) {
    pushGenericStructureReferences(zones, rows, chartEnd);
  }
  return zones;
}

function pushUnifiedStructureLevels(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
) {
  const segments = unifiedStructureSegments(rows, chartEnd);
  let latestRank = 0;
  segments.forEach(({ end, latest, level, start }) => {
    if (!Number.isFinite(start) || !(start > 0) || !(chartEnd > start)) return;
    const low = level.side > 0;
    const color = low ? "var(--success)" : "var(--danger)";
    const timeframes = level.timeframes.join(" · ");
    const holdProbability = boundedUnit(level.hold_probability);
    const breakProbability = boundedUnit(level.break_probability ?? (1 - level.hold_probability));
    const pressureBias = Math.max(-1, Math.min(1, Number(level.pressure_bias) || 0));
    zones.push({
      annotationKind: "unified-structure-level",
      axisLabelDefault: latest && latestRank++ < 4,
      borderColor: color,
      borderOpacity: 0,
      borderStyle: "solid",
      borderWidth: 0,
      color,
      compactLabel: `${low ? "S" : "R"} H${Math.round(holdProbability * 100)}%`,
      breakProbability,
      defaultVisible: true,
      displayItemId: "indicator.qmd_unified_structure",
      end,
      extendToRightEdge: latest,
      fillColor: color,
      fillOpacity: 0.045 + holdProbability * 0.085,
      historicalLabelsDefault: false,
      historyBarsDefault: 0,
      holdProbability,
      label: `${low ? "Support" : "Resistance"} · ${String(level.lifecycle || "active").replaceAll("_", " ")} · ${percentLabel(holdProbability)} observed hold · ${percentLabel(breakProbability)} observed break · ${pressureBias >= 0 ? "+" : ""}${Math.round(pressureBias * 100)} executed pressure · ${level.touch_count} tests · ${level.role_flip_count} flips · ${level.independent_pivot_count} pivots (${timeframes})`,
      latest,
      legendLabel: "Unified structural level book",
      lower: level.lower,
      minPixelHeight: 9,
      probabilityLineRatio: holdProbability,
      probabilityLineWidth: Math.min(4, 1.5 + Number(level.independent_pivot_count || 0) * 0.5),
      renderMode: "zone",
      roleFlipCount: Number(level.role_flip_count ?? 0),
      pressureBias,
      settingsId: "indicator.qmd_unified_structure.level-book-v2",
      start,
      tone: low ? "buy" : "sell",
      totalVolume: level.total_volume,
      buyVolume: level.buy_volume,
      sellVolume: level.sell_volume,
      neutralVolume: level.neutral_volume,
      upper: level.upper,
    });
  });
}

type UnifiedStructureSegment = {
  end: number;
  latest: boolean;
  level: QmdUnifiedStructureLevel;
  start: number;
};

function unifiedStructureSegments(rows: HistoricalIndicator[], chartEnd: number): UnifiedStructureSegment[] {
  const ordered = rows
    .map((row) => ({ row, time: Date.parse(String(row.bar_start || "")) / 1000 }))
    .filter(({ time }) => Number.isFinite(time))
    .sort((left, right) => left.time - right.time);
  const active = new Map<string, UnifiedStructureSegment>();
  const completed: UnifiedStructureSegment[] = [];
  const closeSegment = (key: string, time: number) => {
    const segment = active.get(key);
    if (!segment) return;
    completed.push({ ...segment, end: Math.max(segment.start, time), latest: false });
    active.delete(key);
  };
  const upsertLevel = (level: QmdUnifiedStructureLevel, time: number) => {
    const key = `${level.unified_level_id}:${level.side}`;
    const existing = active.get(key);
    if (existing) {
      // Evidence changes reinforce the same causal level episode. Only an
      // explicit removal (accepted break) or a side change closes its box.
      // The backend keeps episode geometry fixed, so updating the evidence
      // cannot rewrite the historical price range.
      existing.level = level;
      existing.end = chartEnd;
      return;
    }
    const confirmed = Number(level.confirmed_at_ms) / 1000;
    const created = Number(level.created_at_ms) / 1000;
    // A full snapshot may carry a level confirmed before the first projected
    // row (for example a prior-day checkpoint). Preserve that causal start;
    // the chart clips it to its loaded boundary. Anchoring to `time` made
    // every historical level falsely begin at 04:00 on each session.
    const causalStart = Number.isFinite(confirmed) && confirmed > 0
      ? confirmed
      : Number.isFinite(created) && created > 0
        ? created
        : time;
    active.set(key, {
      end: chartEnd,
      latest: true,
      level,
      start: causalStart,
    });
  };
  ordered.forEach(({ row, time }) => {
    // QMD History publishes the first level-book snapshot, each material
    // transition, and the terminal snapshot. A missing field carries the
    // previous state; an explicit empty array closes all active segments.
    const snapshot = row.qmd_structure_unified_levels;
    const delta = row.qmd_structure_unified_level_delta;
    if (!Array.isArray(snapshot) && !delta) return;
    // A full snapshot is authoritative if a legacy/mixed cached row contains
    // both forms. Current QMD History responses guarantee exclusivity, but
    // preferring the snapshot here prevents an old delta from fragmenting a
    // continuous episode during an in-place settings redraw.
    if (!Array.isArray(snapshot) && delta) {
      (delta.removed ?? []).forEach((level) => closeSegment(`${level.unified_level_id}:${level.side}`, time));
      (delta.upserts ?? []).filter(isQmdUnifiedStructureLevel).forEach((level) => upsertLevel(level, time));
      return;
    }
    const levels = (snapshot ?? []).filter(isQmdUnifiedStructureLevel);
    const keys = new Set(levels.map((level) => `${level.unified_level_id}:${level.side}`));
    active.forEach((segment, key) => {
      if (keys.has(key)) return;
      closeSegment(key, time);
    });
    levels.forEach((level) => upsertLevel(level, time));
  });
  active.forEach((segment) => completed.push({ ...segment, end: chartEnd, latest: true }));
  return completed.filter((segment) => segment.end > segment.start);
}

function isQmdUnifiedStructureLevel(value: unknown): value is QmdUnifiedStructureLevel {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<QmdUnifiedStructureLevel>;
  return Number.isFinite(Number(row.unified_level_id))
    && (Number(row.side) === 1 || Number(row.side) === -1)
    && Number(row.lower) > 0
    && Number(row.upper) >= Number(row.lower)
    && Number.isFinite(Number(row.hold_probability))
    && Array.isArray(row.timeframes)
    && Array.isArray(row.sources);
}

function pushStructureSwingLevels(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
) {
  ([
    ["micro", "μH", "μL", "Micro"],
    ["tactical", "TH", "TL", "Tactical"],
    ["context", "CH", "CL", "Context"],
  ] as const).forEach(([scope, highTag, lowTag, title]) => {
    ([
      ["high", highTag, "var(--danger)", "swing-high"],
      ["low", lowTag, "var(--success)", "swing-low"],
    ] as const).forEach(([side, compactLabel, color, annotationKind]) => {
      pushTrailingLevelZones(zones, rows, `qmd_structure_${scope}_swing_${side}`, chartEnd, LEVEL_SOURCE_HISTORY_BARS, {
        annotationKind,
        borderStyle: "solid",
        borderWidth: scope === "context" ? 2 : 1,
        color,
        compactLabel,
        defaultVisible: false,
        displayItemId: "indicator.qmd_generic_structure",
        fillOpacity: 0.018,
        historicalLabelsDefault: false,
        label: `${title} swing ${side}`,
        legendLabel: `${title} · Swing references`,
        minPixelHeight: 3,
        renderMode: "line",
        settingsId: `indicator.qmd_generic_structure.${scope}-swings`,
      });
    });
  });
}

type StructureZoneSpec = {
  compactLabel: string;
  label: string;
  prefix: string;
  scope: "micro" | "tactical" | "context";
  side: "support" | "resistance";
};

function pushStructureZoneSegments(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
  spec: StructureZoneSpec,
) {
  const segments: Array<StructureZoneSpec & {
    confidence: number;
    endIndex: number;
    lower: number;
    price: number;
    startIndex: number;
    strength: number;
    upper: number;
  }> = [];
  const firstIndex = Math.max(0, rows.length - LEVEL_SOURCE_HISTORY_BARS);
  let segmentStart = firstIndex;
  let segmentPrice = finiteNumber(rows[firstIndex]?.[`${spec.prefix}_price`]);
  let segmentLower = finiteNumber(rows[firstIndex]?.[`${spec.prefix}_lower`]);
  let segmentUpper = finiteNumber(rows[firstIndex]?.[`${spec.prefix}_upper`]);
  let segmentStrength = boundedUnit(rows[firstIndex]?.[`${spec.prefix}_strength`]);
  let segmentConfidence = boundedUnit(rows[firstIndex]?.[`${spec.prefix}_confidence`]);
  for (let index = firstIndex + 1; index <= rows.length; index += 1) {
    const nextPrice = index < rows.length ? finiteNumber(rows[index]?.[`${spec.prefix}_price`]) : Number.NaN;
    const nextLower = index < rows.length ? finiteNumber(rows[index]?.[`${spec.prefix}_lower`]) : Number.NaN;
    const nextUpper = index < rows.length ? finiteNumber(rows[index]?.[`${spec.prefix}_upper`]) : Number.NaN;
    const nextStrength = index < rows.length ? boundedUnit(rows[index]?.[`${spec.prefix}_strength`]) : Number.NaN;
    const nextConfidence = index < rows.length ? boundedUnit(rows[index]?.[`${spec.prefix}_confidence`]) : Number.NaN;
    if (index < rows.length
      && structureValueMatches(nextPrice, segmentPrice)
      && structureValueMatches(nextLower, segmentLower)
      && structureValueMatches(nextUpper, segmentUpper)
      && evidenceBucket(nextStrength) === evidenceBucket(segmentStrength)
      && evidenceBucket(nextConfidence) === evidenceBucket(segmentConfidence)) continue;
    segments.push({
      ...spec,
      confidence: segmentConfidence,
      endIndex: index,
      lower: segmentLower,
      price: segmentPrice,
      startIndex: segmentStart,
      strength: segmentStrength,
      upper: segmentUpper,
    });
    segmentStart = index;
    segmentPrice = nextPrice;
    segmentLower = nextLower;
    segmentUpper = nextUpper;
    segmentStrength = nextStrength;
    segmentConfidence = nextConfidence;
  }

  const historicalPolicy = {
    context: { maxSegments: 3, minConfidence: 0.5, minDurationSeconds: 300, minStrength: 0.45 },
    micro: { maxSegments: 0, minConfidence: 1, minDurationSeconds: Number.POSITIVE_INFINITY, minStrength: 1 },
    tactical: { maxSegments: 2, minConfidence: 0.5, minDurationSeconds: 120, minStrength: 0.5 },
  }[spec.scope];
  const historical = segments
    .filter((segment) => {
      if (segment.endIndex >= rows.length || !(segment.price > 0)) return false;
      const start = rowTimestamp(rows[segment.startIndex]);
      const end = rowTimestamp(rows[Math.min(rows.length - 1, segment.endIndex)]);
      return Number.isFinite(start)
        && Number.isFinite(end)
        && end - start >= historicalPolicy.minDurationSeconds
        && segment.strength >= historicalPolicy.minStrength
        && segment.confidence >= historicalPolicy.minConfidence;
    })
    .sort((left, right) => {
      const leftStart = rowTimestamp(rows[left.startIndex]);
      const leftEnd = rowTimestamp(rows[Math.min(rows.length - 1, left.endIndex)]);
      const rightStart = rowTimestamp(rows[right.startIndex]);
      const rightEnd = rowTimestamp(rows[Math.min(rows.length - 1, right.endIndex)]);
      const leftRank = left.strength * left.confidence * Math.log1p(Math.max(0, leftEnd - leftStart));
      const rightRank = right.strength * right.confidence * Math.log1p(Math.max(0, rightEnd - rightStart));
      return rightRank - leftRank || rightEnd - leftEnd;
    })
    .slice(0, historicalPolicy.maxSegments)
    .sort((left, right) => left.startIndex - right.startIndex);

  historical.forEach((segment) => {
    pushStructureZoneSegment(zones, rows, segment.startIndex, segment.endIndex, chartEnd, segment);
  });
}

function evidenceBucket(value: number) {
  return Number.isFinite(value) ? Math.floor(Math.max(0, Math.min(1, value)) * 10 + 1e-9) : -1;
}

function pushCurrentStructureLevels(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
  timeframe: CanvasChartTimeframe,
) {
  const latestIndex = rows.length - 1;
  const latest = rows[latestIndex];
  const candidates = Array.isArray(latest?.qmd_structure_active_levels)
    ? latest.qmd_structure_active_levels
      .filter(isQmdStructureLevelCandidate)
      .filter((candidate) => candidate.promotions.some((promotion) => promotion.timeframe === timeframe))
    : [];
  if (!candidates.length) return;
  const startIndex = latestIndex;
  const start = rowTimestamp(rows[startIndex]);
  if (!Number.isFinite(start) || !(chartEnd > start)) return;

  ([
    ["support", 1, "var(--success)", "S"],
    ["resistance", -1, "var(--danger)", "R"],
  ] as const).forEach(([sideName, side, color, shortSide]) => {
    const sideCandidates = candidates
      .filter((candidate) => candidate.side === side)
      .sort((left, right) => left.distance - right.distance || right.evidence_score - left.evidence_score);
    const strongest = sideCandidates.reduce<QmdStructureLevelCandidate | null>(
      (best, candidate) => !best || candidate.evidence_score > best.evidence_score ? candidate : best,
      null,
    );
    sideCandidates.forEach((candidate, index) => {
      const confidence = boundedUnit(candidate.confidence);
      const strength = boundedUnit(candidate.strength);
      const strongestLevel = strongest === candidate;
      zones.push({
        annotationKind: side > 0 ? "liquidity-support" : "liquidity-resistance",
        axisLabelDefault: index === 0,
        borderColor: color,
        borderOpacity: 0,
        borderWidth: 0,
        color,
        compactLabel: `${shortSide}${index + 1}${strongestLevel ? "*" : ""} · ${Math.round(confidence * 100)}%`,
        confidence,
        currentLevelDistanceRank: index + 1,
        currentLevelSide: sideName,
        currentLevelStrongest: strongestLevel,
        defaultVisible: true,
        displayItemId: "indicator.qmd_generic_structure",
        end: chartEnd,
        extendToRightEdge: true,
        fillColor: color,
        fillOpacity: 0.04 + 0.16 * confidence,
        historicalLabelsDefault: false,
        label: `${sideName === "support" ? "Support" : "Resistance"} ${index + 1} · ${timeframe} promoted · ${percentLabel(confidence)} confidence · ${percentLabel(strength)} strength · ${formatQuantity(candidate.total_volume)} traded (${formatQuantity(candidate.buy_volume)} buy / ${formatQuantity(candidate.sell_volume)} sell)`,
        latest: true,
        legendLabel: "Current support & resistance",
        lower: candidate.lower > 0 ? candidate.lower : candidate.price,
        minPixelHeight: 15,
        settingsId: "indicator.qmd_generic_structure.current-levels",
        start,
        strength,
        upper: candidate.upper > 0 ? candidate.upper : candidate.price,
      });
    });
  });
}

function pushLevelVolumeFootprint(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  structureLevelHistory: QmdStructureLevelCandidate[],
  structureEvents: QmdStructureEvent[],
  chartEnd: number,
  selectedTimeframe: CanvasChartTimeframe,
  footprintSessionDate: string,
) {
  if (!footprintSessionDate) return;
  const encounteredById = new Map<string, QmdStructureLevelCandidate>();
  const levelKey = (candidate: QmdStructureLevelCandidate) =>
    `${candidate.footprint_session_date}:${candidate.created_at_ms}:${candidate.side}:${candidate.price.toFixed(8)}`;
  structureLevelHistory.forEach((candidate) => {
    if (
      isQmdStructureLevelCandidate(candidate)
      && candidate.footprint_session_date === footprintSessionDate
    ) {
      encounteredById.set(levelKey(candidate), candidate);
    }
  });
  rows.forEach((row) => {
    if (!Array.isArray(row.qmd_structure_active_levels)) return;
    row.qmd_structure_active_levels
      .filter(isQmdStructureLevelCandidate)
      .filter((candidate) => candidate.footprint_session_date === footprintSessionDate)
      .forEach((candidate) => {
        const key = levelKey(candidate);
        const existing = encounteredById.get(key);
        if (!existing || candidate.footprint_as_of_ms >= existing.footprint_as_of_ms) {
          encounteredById.set(key, candidate);
        }
      });
  });
  const binsByPrice = new Map<string, {
    asOfMs: number;
    buyVolume: number;
    neutralVolume: number;
    price: number;
    sellVolume: number;
    totalVolume: number;
  }>();
  encounteredById.forEach((candidate) => {
    const asOfMs = Number(candidate.footprint_as_of_ms) || 0;
    candidate.footprint.forEach((bin) => {
      if (!(Number(bin.price) > 0) || !(Number(bin.total_volume) > 0)) return;
      const key = Number(bin.price).toFixed(8);
      const current = binsByPrice.get(key);
      if (current && current.asOfMs > asOfMs) return;
      binsByPrice.set(key, {
        asOfMs,
        buyVolume: Math.max(0, Number(bin.buy_volume) || 0),
        neutralVolume: Math.max(0, Number(bin.neutral_volume) || 0),
        price: Number(bin.price),
        sellVolume: Math.max(0, Number(bin.sell_volume) || 0),
        totalVolume: Math.max(0, Number(bin.total_volume) || 0),
      });
    });
  });
  const orderedBins = [...binsByPrice.values()].sort((left, right) => left.price - right.price);
  orderedBins.forEach((bin, index) => {
    const nextPrice = orderedBins[index + 1]?.price;
    const previousPrice = orderedBins[index - 1]?.price;
    const tick = nextPrice && nextPrice > bin.price
      ? nextPrice - bin.price
      : previousPrice && bin.price > previousPrice
        ? bin.price - previousPrice
        : Math.max(bin.price * 0.00002, 0.0001);
    zones.push({
      annotationKind: "level-footprint",
      color: "var(--muted-foreground)",
      defaultVisible: true,
      displayItemId: "indicator.qmd_level_footprint",
      end: chartEnd,
      fillOpacity: 0.5,
      label: `${formatLevelPrice(bin.price)} · ${formatQuantity(bin.totalVolume)} traded (${formatQuantity(bin.buyVolume)} buy / ${formatQuantity(bin.sellVolume)} sell / ${formatQuantity(bin.neutralVolume)} neutral)`,
      latest: true,
      legendLabel: "Level volume footprint",
      lower: bin.price,
      buyVolume: bin.buyVolume,
      neutralVolume: bin.neutralVolume,
      preset: "axis-history",
      presetDefault: "axis-history",
      renderMode: "zone",
      sellVolume: bin.sellVolume,
      settingsId: "indicator.qmd_level_footprint",
      start: chartEnd,
      tone: "neutral",
      totalVolume: bin.totalVolume,
      upper: bin.price + tick,
    });
  });
  structureEvents
    .filter((event) =>
      event.event_kind === "level_promoted"
      && event.timeframe === selectedTimeframe
      && Number(event.price) > 0
      && Number(event.total_volume) > 0)
    .forEach((event) => {
      const totalVolume = Number(event.total_volume) || 0;
      const buyVolume = Math.max(0, Number(event.buy_volume) || 0);
      const sellVolume = Math.max(0, Number(event.sell_volume) || 0);
      const neutralVolume = Math.max(0, Number(event.neutral_volume) || 0);
      const start = Date.parse(event.pivot_at) / 1000;
      if (!Number.isFinite(start)) return;
      zones.push({
        annotationKind: "swing-footprint",
        buyVolume,
        color: "var(--muted-foreground)",
        defaultVisible: true,
        displayItemId: "indicator.qmd_level_footprint",
        end: start,
        label: `${selectedTimeframe} swing footprint · ${formatLevelPrice(Number(event.price))} · ${formatQuantity(totalVolume)} traded · ${percentLabel(buyVolume / totalVolume)} buy / ${percentLabel(sellVolume / totalVolume)} sell / ${percentLabel(neutralVolume / totalVolume)} neutral · ${formatQuantity(Number(event.trade_count) || 0)} trades`,
        latest: false,
        legendLabel: "Level volume footprint",
        lower: Number(event.price),
        neutralVolume,
        preset: "swing-rails",
        presetDefault: "axis-history",
        renderMode: "line",
        sellVolume,
        settingsId: "indicator.qmd_level_footprint",
        start,
        tone: Number(event.direction) < 0 ? "sell" : "buy",
        totalVolume,
        upper: Number(event.price),
      });
    });
}

function pushStructureZoneSegment(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  startIndex: number,
  endIndex: number,
  chartEnd: number,
  spec: StructureZoneSpec & { confidence: number; lower: number; price: number; strength: number; upper: number },
) {
  if (!(spec.price > 0) || startIndex >= rows.length) return;
  const latest = endIndex >= rows.length;
  const activeWindowBars = spec.scope === "micro" ? 10 : spec.scope === "tactical" ? 18 : spec.scope === "context" ? 30 : 16;
  const visualStartIndex = latest ? Math.max(startIndex, rows.length - activeWindowBars) : startIndex;
  const start = rowTimestamp(rows[visualStartIndex]);
  const end = endIndex < rows.length ? rowTimestamp(rows[endIndex]) : chartEnd;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
  const support = spec.side === "support";
  const scopeOpacity = spec.scope === "micro" ? 0.55 : spec.scope === "tactical" ? 0.72 : spec.scope === "context" ? 0.62 : 1;
  const color = support ? "var(--success)" : "var(--danger)";
  zones.push({
    annotationKind: support ? "liquidity-support" : "liquidity-resistance",
    axisLabelDefault: false,
    borderColor: color,
    borderOpacity: (0.22 + 0.48 * spec.confidence) * scopeOpacity,
    borderStyle: spec.scope === "context" ? "dashed" : spec.scope === "micro" ? "dotted" : "solid",
    borderWidth: 0.75 + 0.75 * spec.confidence,
    color,
    compactLabel: spec.compactLabel,
    confidence: spec.confidence,
    defaultVisible: false,
    displayItemId: "indicator.qmd_generic_structure",
    end,
    fillColor: color,
    fillOpacity: 0.01 + spec.strength * 0.04 * scopeOpacity,
    historicalLabelsDefault: false,
    label: `${spec.label} · ${percentLabel(spec.strength)} strength · ${percentLabel(spec.confidence)} confidence`,
    latest,
    legendLabel: `${spec.scope[0].toUpperCase()}${spec.scope.slice(1)} zones`,
    lower: spec.lower > 0 ? spec.lower : spec.price,
    minPixelHeight: 4,
    settingsId: `indicator.qmd_generic_structure.${spec.scope}-zones`,
    start,
    strength: spec.strength,
    upper: spec.upper > 0 ? spec.upper : spec.price,
  });
}

function pushEventStructureSwingLevels(
  zones: NonNullable<ChartPayload["price_zones"]>,
  events: QmdStructureEvent[],
  chartEnd: number,
  selectedTimeframe: CanvasChartTimeframe,
) {
  const ordered = [...events].sort((left, right) =>
    Date.parse(left.confirmed_at) - Date.parse(right.confirmed_at) || left.event_id - right.event_id);
  const lifecycleByLevel = new Map<number, QmdStructureEvent[]>();
  ordered.forEach((event) => {
    if (!Number.isFinite(event.level_id) || Number(event.level_id) <= 0) return;
    const levelId = Number(event.level_id);
    const levelEvents = lifecycleByLevel.get(levelId) ?? [];
    levelEvents.push(event);
    lifecycleByLevel.set(levelId, levelEvents);
  });
  const promoted = retainStructureEventsPerTimeframe(
    ordered,
    (event) => event.event_kind === "level_promoted",
  );
  promoted.forEach((event, eventIndex) => {
      const timeframe = event.timeframe as CanvasChartTimeframe;
      const start = Date.parse(event.pivot_at) / 1000;
      const promotedAt = Date.parse(event.confirmed_at);
      const price = Number(event.price);
      if (!Number.isFinite(start) || !(price > 0)) return;
      const terminal = lifecycleByLevel.get(Number(event.level_id))?.find((candidate) =>
        Date.parse(candidate.confirmed_at) >= promotedAt
        && ["structure_crossed", "bos", "choch", "structure_break"].includes(candidate.event_kind));
      const nextSameSide = promoted.slice(eventIndex + 1).find((candidate) =>
        candidate.timeframe === event.timeframe && Math.sign(Number(candidate.direction)) === Math.sign(Number(event.direction)));
      const end = Math.min(
        chartEnd,
        terminal ? Date.parse(terminal.confirmed_at) / 1000 : chartEnd,
        nextSameSide ? Date.parse(nextSameSide.pivot_at) / 1000 : chartEnd,
      );
      if (!Number.isFinite(end) || end <= start) return;
      const swingHigh = Number(event.direction) < 0;
      const color = swingHigh ? "var(--danger)" : "var(--success)";
      zones.push({
        annotationKind: swingHigh ? "swing-high" : "swing-low",
        axisLabelDefault: false,
        borderColor: color,
        borderOpacity: 0.5,
        borderStyle: "solid",
        borderWidth: 4,
        color,
        compactLabel: swingHigh ? "SH" : "SL",
        confidence: Number(event.confidence || 0),
        defaultVisible: timeframe === selectedTimeframe,
        displayItemId: "indicator.qmd_generic_structure",
        end,
        fillOpacity: 0,
        historicalLabelsDefault: timeframe === selectedTimeframe,
        historyTimeframeSeconds: qmdStructureTimeframeSeconds(timeframe),
        label: `${timeframe} local swing ${swingHigh ? "high" : "low"} · ${formatLevelPrice(price)} · causal confirmation ${event.confirmed_at} · ${percentLabel(Number(event.confidence || 0))} confidence`,
        latest: !terminal,
        legendLabel: `${timeframe} · Swing levels`,
        lower: price,
        minPixelHeight: 1,
        opacityDefault: 0.5,
        renderMode: "line",
        settingsId: qmdStructureSwingLayerId(timeframe),
        start,
        strength: Number(event.strength || 0),
        tone: swingHigh ? "sell" : "buy",
        upper: price,
        zoneHeightMode: "price",
      });
    });
}

function structureEventsFromSampledRows(rows: HistoricalIndicator[]): QmdStructureEvent[] {
  const events: QmdStructureEvent[] = [];
  let previousId = "";
  rows.forEach((row) => {
    const eventId = String(row.qmd_structure_event_id || "");
    const eventKind = String(row.qmd_structure_event_kind || "").toLowerCase();
    if (!eventId || eventId === "0" || eventId === previousId || !["bos", "choch", "structure_break"].includes(eventKind)) {
      previousId = eventId;
      return;
    }
    const confirmedAtMs = finiteNumber(row.qmd_structure_event_at_ms);
    const pivotAtMs = finiteNumber(row.qmd_structure_event_pivot_at_ms);
    const price = finiteNumber(row.qmd_structure_event_price);
    if (!(confirmedAtMs > 0) || !(pivotAtMs > 0) || !(price > 0)) {
      previousId = eventId;
      return;
    }
    events.push({
      algorithm_version: 0,
      confidence: finiteNumber(row.qmd_structure_confidence),
      confirmed_at: new Date(confirmedAtMs).toISOString(),
      direction: finiteNumber(row.qmd_structure_event_direction),
      event_id: Number(eventId),
      event_kind: eventKind,
      lower: price,
      pivot_at: new Date(pivotAtMs).toISOString(),
      price,
      timeframe: String(row.qmd_structure_event_timeframe || "").toLowerCase(),
      strength: finiteNumber(row.qmd_structure_strength),
      sym: "",
      upper: price,
    });
    previousId = eventId;
  });
  return events;
}

function pushStructureEvents(
  zones: NonNullable<ChartPayload["price_zones"]>,
  events: QmdStructureEvent[],
  chartEnd: number,
  selectedTimeframe: CanvasChartTimeframe,
) {
  retainStructureEventsPerTimeframe(
    events,
    (event) => ["bos", "choch", "structure_break"].includes(String(event.event_kind || "").toLowerCase()),
  )
    .forEach((event) => {
    const confirmedAt = Date.parse(event.confirmed_at) / 1000;
    const direction = Number(event.direction || 0);
    const kind = String(event.event_kind || "").toLowerCase();
    const pivotAt = Date.parse(event.pivot_at) / 1000;
    const price = Number(event.price || 0);
    const scale = String(event.timeframe || "").toLowerCase();
    const end = Math.min(chartEnd, confirmedAt);
    if (!(price > 0) || !Number.isFinite(pivotAt) || !Number.isFinite(confirmedAt) || !(end > pivotAt)) return;
    const bullish = direction > 0;
    const label = kind === "choch" ? "CHoCH" : kind === "bos" ? "BoS" : "Break";
    if (!QMD_STRUCTURE_TIMEFRAMES.includes(scale as typeof QMD_STRUCTURE_TIMEFRAMES[number])) return;
    zones.push({
      annotationKind: kind === "structure_break" ? "structure-break" : kind === "choch" ? "choch" : "bos",
      borderColor: bullish ? "var(--success)" : "var(--danger)",
      borderOpacity: 0.82,
      borderStyle: "dashed",
      borderWidth: 1,
      color: bullish ? "var(--success)" : "var(--danger)",
      compactLabel: label,
      displayItemId: "indicator.qmd_generic_structure",
      end,
      eventTime: confirmedAt,
      fillOpacity: 0,
      historicalLabelsDefault: scale === selectedTimeframe,
      historyTimeframeSeconds: qmdStructureTimeframeSeconds(scale),
      label: `${bullish ? "Bullish" : "Bearish"} ${label} · ${scale || "structure"} · ${formatLevelPrice(price)}`,
      latest: false,
      defaultVisible: scale === selectedTimeframe,
      legendLabel: `${scale} · Structure breaks`,
      lower: price,
      minPixelHeight: 1,
      renderMode: "line",
      settingsId: qmdStructureBreakLayerId(scale),
      start: pivotAt,
      tone: bullish ? "buy" : "sell",
      upper: price,
      zoneHeightMode: "price",
    });
  });
}

function pushGenericStructureReferences(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  chartEnd: number,
) {
  const specs = [
    ["qmd_structure_session_high", "Extended-session high", "Sess H", "var(--info)", "session", "Extended session H/L", false, "sell"],
    ["qmd_structure_session_low", "Extended-session low", "Sess L", "var(--info)", "session", "Extended session H/L", false, "buy"],
    ["qmd_structure_opening_range_high", "Opening range high", "OR H", "var(--foreground)", "opening-range", "Opening range", true, "sell"],
    ["qmd_structure_opening_range_low", "Opening range low", "OR L", "var(--foreground)", "opening-range", "Opening range", true, "buy"],
    ["qmd_structure_trade_volume_poc", "Eligible-trade volume POC", "POC", "var(--primary)", "poc", "Trade-volume POC", true, "neutral"],
    ["qmd_structure_luld_upper", "Estimated LULD upper", "LULD U", "var(--danger)", "luld", "Estimated LULD", false, "sell"],
    ["qmd_structure_luld_lower", "Estimated LULD lower", "LULD L", "var(--success)", "luld", "Estimated LULD", false, "buy"],
    ["qmd_structure_52_week_high", "52-week high", "52W H", "var(--warning)", "52-week", "52-week H/L", false, "sell"],
    ["qmd_structure_52_week_low", "52-week low", "52W L", "var(--info)", "52-week", "52-week H/L", false, "buy"],
    ["qmd_structure_prior_month_high", "Prior-month high", "PrevM H", "var(--primary)", "prior-month", "Prior month H/L/C", false, "sell"],
    ["qmd_structure_prior_month_low", "Prior-month low", "PrevM L", "var(--primary)", "prior-month", "Prior month H/L/C", false, "buy"],
    ["qmd_structure_prior_month_close", "Prior-month close", "PrevM C", "var(--muted-foreground)", "prior-month", "Prior month H/L/C", false, "neutral"],
  ] as const;
  specs.forEach(([column, label, compactLabel, color, settingsSuffix, legendLabel, axisLabelDefault, tone]) => {
    const settingsGroup = settingsSuffix === "session"
      ? "session-levels"
      : ["52-week", "prior-month"].includes(settingsSuffix)
        ? "higher-timeframe"
        : settingsSuffix;
    const groupedLegendLabel = settingsGroup === "session-levels"
      ? "Extended session H/L"
      : settingsGroup === "higher-timeframe"
        ? "Higher-timeframe levels"
        : legendLabel;
    pushTrailingLevelZones(zones, rows, column, chartEnd, LEVEL_SOURCE_HISTORY_BARS, {
      annotationKind: settingsGroup === "luld" ? "luld-line" : "level",
      axisLabelDefault,
      color,
      compactLabel,
      defaultVisible: ["opening-range", "poc"].includes(settingsGroup),
      displayItemId: "indicator.qmd_reference_levels",
      fillOpacity: 0.025,
      historicalLabelsDefault: false,
      label,
      legendLabel: groupedLegendLabel,
      minPixelHeight: 3,
      renderMode: "line",
      settingsId: `indicator.qmd_reference_levels.${settingsGroup}`,
      tone: tone === "neutral" ? undefined : tone,
    });
  });
}

type LevelZoneStyle = {
  annotationKind: "level" | "luld-line" | "liquidity-resistance" | "liquidity-support" | "swing-high" | "swing-low";
  axisLabelDefault?: boolean;
  borderStyle?: string;
  borderWidth?: number;
  color: string;
  compactLabel: string;
  confidence?: number;
  defaultVisible?: boolean;
  displayItemId: string;
  fillOpacity: number;
  historicalLabelsDefault?: boolean;
  label: string;
  legendLabel: string;
  minPixelHeight: number;
  renderMode?: "line" | "zone";
  settingsId: string;
  strength?: number;
  tone?: "buy" | "sell";
};

const LEVEL_SOURCE_HISTORY_BARS = 1000;


function pushTrailingLevelZones(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  column: string,
  chartEnd: number,
  barCount: number,
  style: LevelZoneStyle,
) {
  const firstIndex = Math.max(0, rows.length - Math.max(1, barCount));
  let segmentStart = firstIndex;
  let segmentValue = finiteNumber(rows[firstIndex]?.[column]);
  for (let index = firstIndex + 1; index <= rows.length; index += 1) {
    const nextValue = index < rows.length ? finiteNumber(rows[index][column]) : Number.NaN;
    if (index < rows.length && pricesMatch(nextValue, segmentValue)) continue;
    pushHistoricalLevelSegment(zones, rows, segmentStart, index, segmentValue, chartEnd, style);
    segmentStart = index;
    segmentValue = nextValue;
  }
}


function pushHistoricalLevelSegment(
  zones: NonNullable<ChartPayload["price_zones"]>,
  rows: HistoricalIndicator[],
  startIndex: number,
  endIndex: number,
  value: number,
  chartEnd: number,
  style: LevelZoneStyle,
) {
  if (!(value > 0) || startIndex >= rows.length) return;
  const start = rowTimestamp(rows[startIndex]);
  const end = endIndex < rows.length ? rowTimestamp(rows[endIndex]) : chartEnd;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
  zones.push({
    annotationKind: style.annotationKind,
    axisLabelDefault: style.axisLabelDefault,
    borderColor: style.color,
    borderOpacity: Math.min(0.4, style.fillOpacity * 2.5),
    borderStyle: style.borderStyle ?? "solid",
    borderWidth: style.borderWidth ?? 1,
    color: style.color,
    compactLabel: style.compactLabel,
    confidence: style.confidence,
    defaultVisible: style.defaultVisible,
    displayItemId: style.displayItemId,
    end,
    fillColor: style.color,
    fillOpacity: style.fillOpacity,
    historicalLabelsDefault: style.historicalLabelsDefault,
    label: `${style.label} · ${formatLevelPrice(value)}`,
    latest: endIndex >= rows.length,
    legendLabel: style.legendLabel,
    lower: value,
    minPixelHeight: style.minPixelHeight,
    renderMode: style.renderMode ?? "zone",
    settingsId: style.settingsId,
    start,
    strength: style.strength,
    tone: style.tone,
    upper: value,
    zoneHeightMode: "fixed_px",
  });
}

function rowTimestamp(row?: HistoricalIndicator) { return row ? Date.parse(String(row.bar_start)) / 1000 : Number.NaN; }

function pricesMatch(left: number, right: number) {
  return left > 0 && right > 0 && Math.abs(left - right) <= Math.max(0.00005, Math.abs(right) * 1e-8);
}

function structureValueMatches(left: number, right: number) {
  return (left <= 0 && right <= 0) || pricesMatch(left, right);
}

function percentLabel(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function formatLevelPrice(value: number) {
  return value >= 1 ? `$${value.toFixed(2)}` : `$${value.toFixed(4)}`;
}

function historicalIndicatorSeries(rows: HistoricalIndicator[], target: "oscillator" | "price", visibleIndicators: string[]): ChartPayload["overlay_series"] {
  const visible = new Set(visibleIndicators);
  const latestComposite = [...rows].reverse().find((row) => Number.isFinite(Number(row.flow_structure_composite_score)));
  const latestAnchoredFlow = [...rows].reverse().find((row) => Number.isFinite(Number(row.microstructure_cumulative_level1_ofi)) && Number.isFinite(Number(row.microstructure_cumulative_signed_volume_delta)));
  return INDICATOR_SERIES.filter((spec) => visible.has(spec.displayItemId) && (spec.pane === "price" ? "price" : "oscillator") === target).map((spec) => ({
    ...( "autoscaleMax" in spec ? { autoscaleMax: spec.autoscaleMax, autoscaleMin: spec.autoscaleMin } : {}),
    ...( "autoscaleScope" in spec ? { autoscaleScope: spec.autoscaleScope } : {}),
    ...( "axisTitle" in spec ? { axisTitle: spec.axisTitle } : {}),
    color: spec.color,
    ...( "colorMode" in spec ? { colorMode: spec.colorMode } : {}),
    column: spec.column,
    data: rows.map((row) => indicatorSeriesPoint(row, spec.column, "colorMode" in spec ? spec.colorMode : undefined)).filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value)),
    ...( "defaultVisible" in spec ? { defaultVisible: Boolean(spec.defaultVisible) } : {}),
    displayItemId: spec.displayItemId,
    label: spec.column === "flow_structure_composite_score"
      ? flowStructureCompositeLabel(latestComposite)
      : spec.column === "microstructure_anchored_flow_relationship"
        ? anchoredFlowRelationshipLabel(latestAnchoredFlow)
        : spec.label,
    ...( "lastValueVisible" in spec ? { lastValueVisible: Boolean(spec.lastValueVisible) } : {}),
    ...( "lineStyle" in spec ? { lineStyle: spec.lineStyle } : {}),
    lineWidth: "lineWidth" in spec ? spec.lineWidth : 1,
    ...( "opacity" in spec ? { opacity: spec.opacity } : {}),
    paneKey: spec.pane,
    ...( "priceScaleId" in spec ? { priceScaleId: spec.priceScaleId } : {}),
    style: "style" in spec ? spec.style : "line",
  }));
}

function indicatorSeriesPoint(row: HistoricalIndicator, column: string, colorMode?: string) {
  const time = Date.parse(String(row.bar_start)) / 1000;
  if (column === "microstructure_anchored_flow_relationship") {
    const relationship = anchoredFlowRelationship(String(row.microstructure_anchored_flow_relationship || "neutral"), Number(row.microstructure_anchored_flow_relationship_score));
    return { color: relationship.color, time, value: relationship.value };
  }
  return {
    ...(colorMode === "confidence-sign" ? { confidence: boundedUnit(column === "flow_structure_composite_score" ? row.flow_structure_composite_confidence : row.qmd_structure_confidence) } : {}),
    ...(column === "flow_structure_composite_score"
      ? { tone: flowStructureBiasTone(String(row.flow_structure_composite_bias || "neutral")) }
      : qmdDirectionalColumn(column)
        ? { tone: microstructureValueTone(Number(row[column])) }
        : {}),
    time,
    value: Number(row[column]),
  };
}

function anchoredFlowRelationship(value: string, score: number) {
  if (value === "bullish_confirmation") return { color: "var(--success)", label: "Bullish confirmation", value: 1 };
  if (value === "bearish_confirmation") return { color: "var(--danger)", label: "Bearish confirmation", value: -1 };
  if (value === "bullish_absorption") return { color: "var(--info)", label: "Bullish absorption", value: 0.55 };
  if (value === "bearish_absorption") return { color: "var(--warning)", label: "Bearish absorption", value: -0.55 };
  return { color: "var(--muted-foreground)", label: "Neutral", value: Number.isFinite(score) ? score : 0 };
}

function anchoredFlowRelationshipLabel(row?: HistoricalIndicator) {
  if (!row) return "Relationship · waiting";
  return `Relationship · ${anchoredFlowRelationship(String(row.microstructure_anchored_flow_relationship || "neutral"), Number(row.microstructure_anchored_flow_relationship_score)).label}`;
}

function microstructureActionTone(action: string): "buy" | "neutral" | "sell" {
  if (action.toUpperCase() === "BUY") return "buy";
  if (action.toUpperCase() === "SELL") return "sell";
  return "neutral";
}

function qmdDirectionalColumn(column: string) {
  return column.startsWith("microstructure_")
    && !column.endsWith("_confidence")
    && column !== "microstructure_regime_reliability"
    && column !== "microstructure_arrival_rate_per_second";
}

function microstructureValueTone(value: number): "buy" | "neutral" | "sell" {
  if (value > 0) return "buy";
  if (value < 0) return "sell";
  return "neutral";
}

function flowStructureBiasTone(bias: string): "buy" | "neutral" | "sell" {
  if (bias.toLowerCase() === "bullish") return "buy";
  if (bias.toLowerCase() === "bearish") return "sell";
  return "neutral";
}

function flowStructureCompositeLabel(row?: HistoricalIndicator) {
  const bias = String(row?.flow_structure_composite_bias || "neutral").toUpperCase();
  const confidence = boundedUnit(row?.flow_structure_composite_confidence);
  return `Composite ${bias} · ${Math.round(confidence * 100)}%`;
}

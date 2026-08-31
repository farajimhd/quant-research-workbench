import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import type { UTCTimestamp } from "lightweight-charts";

import { api, apiWebSocketUrl, query } from "../../api/client";
import type { CanvasChartTimeframe } from "../../app/canvasWorkspace";
import type { ChartPayload } from "../../app/components/ChartPanel";
import { CHART_INDICATORS, ENRICHED_QMD_TIMEFRAMES, MACRO_TIMEFRAMES } from "./configuration";
import type {
  CanvasLiveChartState,
  ChartHistoryCursor,
  HistoricalBar,
  HistoricalIndicator,
  QmdBarHistory,
  QmdLiveBar,
  QmdLiveChartPayload,
  QmdMarketSignalEvent,
  QmdStructureEvent,
  QmdStructureLevelCandidate,
} from "./contracts";
import { boundedUnit } from "./numbers";
import { isQmdStructureLevelCandidate, retainStructureEventsPerTimeframe } from "./structureModel";
import { dateInTimeZone } from "./time";

type ChartSnapshotCacheEntry = {
  cachedAt: number;
  payload: QmdBarHistory;
};

type QmdStreamSnapshot<T extends { bar_start: string }> = {
  action?: string;
  current?: T | null;
  error?: string;
  history?: T[];
  type?: string;
};

const CHART_SNAPSHOT_CACHE = new Map<string, ChartSnapshotCacheEntry>();
const CHART_SNAPSHOT_CACHE_TTL_MS = 2 * 60_000;
const CHART_SNAPSHOT_CACHE_LIMIT = 48;
const UNIFIED_STRUCTURE_TIMEFRAME: CanvasChartTimeframe = "1s";

// These indicators are deterministic functions of the canonical closed bars
// and are prepared with the bar artifact. Event-flow and microstructure fields
// remain on the full derived path; they must never be approximated from OHLCV.
const BAR_DERIVED_INDICATOR_COLUMNS = new Set([
  "bar_start",
  "atr_14",
  "bollinger_lower_20",
  "bollinger_mid_20",
  "bollinger_std_20",
  "bollinger_upper_20",
  "close_sma_20",
  "ema_9",
  "ema_20",
  "ema_50",
  "macd_histogram",
  "macd_line",
  "macd_signal",
  "price_vs_ema20_pct",
  "price_vs_vwap_pct",
  "return_1_bar",
  "rsi_14",
  "trend_score",
  "volume_sma_20",
  "vwap",
]);

// QMD attaches the causal structure snapshot to each completed BarRow before
// the prepared chart artifact is written.  Treating these projections as
// event-derived started a second full-session event replay after the chart was
// already ready.  The structural books themselves remain compacted by QMD
// History and the raw structure-event stream is still requested separately for
// swing/break audit overlays.
function isBarDerivedIndicatorColumn(column: string): boolean {
  return BAR_DERIVED_INDICATOR_COLUMNS.has(column) || column.startsWith("qmd_structure_");
}

type HistoricalChartMode = "backtest" | "debug" | "replay";

export function useCanvasHistoricalChart(symbol: string, timeframe: CanvasChartTimeframe, cutoffMs: number, sessionDate: string, visibleIndicatorIds: string[], liveTail = false, enabled = true, historicalMode: HistoricalChartMode = "replay", fullSession = false): CanvasLiveChartState {
  const pointInTime = !liveTail;
  const refreshCutoffMs = pointInTime
    ? Math.floor(cutoffMs / timeframeDurationMs(timeframe)) * timeframeDurationMs(timeframe)
    : cutoffMs;
  const indicatorColumns = useMemo(() => requestedIndicatorColumns(visibleIndicatorIds), [visibleIndicatorIds]);
  const unifiedStructureSelected = indicatorColumns.split(",").includes("qmd_structure_unified_levels");
  const standardIndicatorColumns = useMemo(
    () => indicatorColumns.split(",").filter((column) => column !== "qmd_structure_unified_levels").join(","),
    [indicatorColumns],
  );
  const barIndicatorColumns = useMemo(
    () => standardIndicatorColumns.split(",").filter(isBarDerivedIndicatorColumn).join(","),
    [standardIndicatorColumns],
  );
  const eventIndicatorColumns = useMemo(
    () => [
      "bar_start",
      ...standardIndicatorColumns
        .split(",")
        .filter((column) => column !== "bar_start" && !isBarDerivedIndicatorColumn(column)),
    ].join(","),
    [standardIndicatorColumns],
  );
  const standardIndicatorsRequested = eventIndicatorColumns !== "bar_start";
  const baseIndicatorColumns = barIndicatorColumns.split(",")
    .filter(Boolean)
    .filter((value, index, values) => values.indexOf(value) === index)
    .join(",");
  const unifiedStructureColumns = "bar_start,qmd_structure_unified_levels";
  const historyTimeoutMs = unifiedStructureSelected ? 180_000 : 120_000;
  const auxiliaryProjection = useMemo(() => requestedChartAuxiliary(visibleIndicatorIds), [visibleIndicatorIds]);
  const projectionKey = `${indicatorColumns}|signals=${auxiliaryProjection.includeMarketSignals}|structure=${auxiliaryProjection.includeStructure}`;
  const rowBudget = useMemo(() => chartRowBudget(indicatorColumns), [indicatorColumns]);
  const [state, setState] = useState<Omit<CanvasLiveChartState, "loadEarlier" | "ready">>({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", historyNotice: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, marketSignalEvents: [], pointInTime, structureEvents: [], structureLevelHistory: [] });
  const [readyKey, setReadyKey] = useState("");
  const stateRef = useRef(state);
  stateRef.current = state;
  const historyCursorRef = useRef<ChartHistoryCursor | null>(null);
  const historyRequestRef = useRef(false);
  const historyAbortRef = useRef<AbortController | null>(null);
  const requestKeyRef = useRef("");
  const displayedIdentityRef = useRef("");
  const displayedRequestKeyRef = useRef("");
  const loadedCutoffRef = useRef(0);

  const loadEarlier = useCallback(() => {
    if (!enabled) return;
    const ticker = symbol.trim().toUpperCase();
    const requestKey = chartRequestKey(ticker, timeframe, projectionKey, sessionDate, historicalMode, fullSession);
    const cursor = historyCursorRef.current;
    if (!cursor || historyRequestRef.current || requestKeyRef.current !== requestKey) return;
    if (!cursor.nextBefore && !cursor.previousSessionBefore) return;
    const request = earlierChartHistoryRequest(cursor, ticker, timeframe, standardIndicatorColumns, historicalMode);
    if (!request) return;
    const controller = new AbortController();
    historyAbortRef.current = controller;
    historyRequestRef.current = true;
    setState((current) => ({ ...current, historyError: "", loadingEarlier: true }));
    const page = api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...request.params, include_market_signals: auxiliaryProjection.includeMarketSignals, include_structure: auxiliaryProjection.includeStructure, indicator_columns: baseIndicatorColumns, stage: "bars" })}`, { signal: controller.signal, timeoutMs: historyTimeoutMs });
    const standardIndicatorPage = standardIndicatorsRequested
      ? api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...request.params, include_market_signals: false, include_structure: false, indicator_columns: eventIndicatorColumns, stage: "full" })}`, { signal: controller.signal, timeoutMs: 120_000 })
      : null;
    page
      .then((payload) => {
        if (requestKeyRef.current !== requestKey) return;
        updateHistoryCursor(historyCursorRef, payload);
        const aligned = alignHistoricalChartRows(
          closedRowsAtCutoff(payload.history, timeframe, cutoffMs),
          closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs),
          payload.indicators_available,
        );
        setState((current) => {
          const merged = mergeHistoricalChartPage(current.bars, current.indicators, aligned.bars, aligned.indicators, rowBudget);
          return {
            ...current,
            bars: merged.bars,
            canLoadEarlier: payload.has_more && !merged.atCapacity,
            marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
            historyError: "",
            historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : "",
            indicators: merged.indicators,
            indicatorsAvailable: payload.indicators_available,
            indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
            structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
            structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
          };
        });
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        if (requestKeyRef.current !== requestKey) return;
        setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
      })
      .finally(() => {
        if (historyAbortRef.current === controller) {
          historyAbortRef.current = null;
          historyRequestRef.current = false;
        }
        if (requestKeyRef.current === requestKey) setState((current) => ({ ...current, loadingEarlier: false }));
      });
    void standardIndicatorPage?.then((payload) => {
      if (controller.signal.aborted || requestKeyRef.current !== requestKey) return;
      const rows = closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs);
      setState((current) => ({
        ...current,
        indicators: limitIndicatorRowsToLatest(mergeIndicatorRowsByTime(current.indicators, rows), rowBudget),
        indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
      }));
    }).catch((reason) => {
      if (controller.signal.aborted || requestKeyRef.current !== requestKey) return;
      setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
    });
  }, [auxiliaryProjection.includeMarketSignals, auxiliaryProjection.includeStructure, baseIndicatorColumns, cutoffMs, enabled, eventIndicatorColumns, fullSession, historicalMode, historyTimeoutMs, projectionKey, rowBudget, sessionDate, standardIndicatorsRequested, symbol, timeframe, unifiedStructureSelected]);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const historyController = new AbortController();
    const ticker = symbol.trim().toUpperCase();
    const requestKey = chartRequestKey(ticker, timeframe, projectionKey, sessionDate, historicalMode, fullSession);
    const identityKey = chartIdentityKey(ticker, sessionDate, historicalMode, fullSession);
    historyAbortRef.current?.abort();
    historyAbortRef.current = historyController;
    requestKeyRef.current = requestKey;
    setReadyKey("");
    historyCursorRef.current = null;
    historyRequestRef.current = false;
    loadedCutoffRef.current = refreshCutoffMs;
    const cached = readChartSnapshot(requestKey);
    const cachedRows = cached
      ? alignHistoricalChartRows(
        closedRowsAtCutoff(cached.history, timeframe, cutoffMs),
        closedRowsAtCutoff(cached.indicators, timeframe, cutoffMs),
        cached.indicators_available,
      )
      : null;
    const previous = stateRef.current;
    const retainPreviousTimeframe = !cachedRows
      && previous.bars.length > 0
      && displayedIdentityRef.current === identityKey
      && displayedRequestKeyRef.current !== requestKey;
    if (cachedRows) {
      displayedIdentityRef.current = identityKey;
      displayedRequestKeyRef.current = requestKey;
    } else if (!retainPreviousTimeframe) {
      displayedIdentityRef.current = "";
      displayedRequestKeyRef.current = "";
    }
    setState(retainPreviousTimeframe ? {
      ...previous,
      canLoadEarlier: false,
      connected: false,
      error: "",
      historyError: "",
      historyNotice: `Loading ${timeframe} chart…`,
      loading: true,
      loadingEarlier: false,
      pointInTime,
    } : {
      bars: cachedRows?.bars ?? [],
      canLoadEarlier: false,
      connected: false,
      error: "",
      historyError: "",
      historyNotice: cachedRows?.bars.length ? "Refreshing cached chart data..." : "",
      indicators: cachedRows?.indicators ?? [],
      indicatorsAvailable: cached?.indicators_available ?? ENRICHED_QMD_TIMEFRAMES.has(timeframe),
      indicatorProvenance: cached?.indicator_provenance,
      lastUpdateAt: "",
      loading: true,
      loadingEarlier: false,
      marketSignalEvents: [],
      pointInTime,
      structureEvents: [],
      structureLevelHistory: [],
    });

    const fetchHistoricalPage = () => {
      historyRequestRef.current = true;
      const requestParams = { allow_persisted_bars: liveTail, as_of: new Date(cutoffMs).toISOString(), full_session: fullSession, include_market_signals: auxiliaryProjection.includeMarketSignals, include_structure: auxiliaryProjection.includeStructure, mode: liveTail ? "live" : historicalMode, row_limit: fullSession ? chartFullSessionPageSize(timeframe) : chartInitialPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe };
      const progressive = ENRICHED_QMD_TIMEFRAMES.has(timeframe);
      // Candles are the chart's base authority. Optional indicators may enrich
      // them after the first paint, but must never gate or replace that paint.
      const barsRequest = progressive
        ? api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, indicator_columns: baseIndicatorColumns, stage: "bars" })}`, { signal: historyController.signal, timeoutMs: historyTimeoutMs })
        : api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, indicator_columns: indicatorColumns, stage: "full" })}`, { signal: historyController.signal, timeoutMs: historyTimeoutMs });
      const fullRequest = progressive && standardIndicatorsRequested
        ? () => api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, indicator_columns: eventIndicatorColumns, stage: "full" })}`, { signal: historyController.signal, timeoutMs: 120_000 })
        : null;
      const unifiedStructureRequest = progressive && unifiedStructureSelected
        ? () => api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, full_session: true, include_market_signals: false, include_structure: false, indicator_columns: unifiedStructureColumns, row_limit: chartFullSessionPageSize(UNIFIED_STRUCTURE_TIMEFRAME), stage: "full", timeframe: UNIFIED_STRUCTURE_TIMEFRAME })}`, { signal: historyController.signal, timeoutMs: historyTimeoutMs })
        : null;
      // Live bars and closed indicators come from the recent materializations.
      // Signal/structure history remains causal QMD History work and advances
      // after the first bar paint so chart work cannot exhaust the browser's
      // connection pool and starve Canvas persistence or other control calls.
      const auxiliaryRequest = progressive && liveTail && (auxiliaryProjection.includeMarketSignals || auxiliaryProjection.includeStructure)
        ? () => api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, allow_persisted_bars: false, mode: "replay", indicator_columns: indicatorColumns, stage: "full" })}`, { signal: historyController.signal, timeoutMs: historyTimeoutMs })
        : null;
      const mergeAuxiliaryPayload = (payload: QmdBarHistory) => {
          if (!active || requestKeyRef.current !== requestKey) return;
          const aligned = alignHistoricalChartRows(
            closedRowsAtCutoff(payload.history, timeframe, cutoffMs),
            closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs),
            payload.indicators_available,
          );
          setState((current) => {
            const merged = mergeHistoricalChartPage(current.bars, current.indicators, aligned.bars, aligned.indicators, rowBudget);
            return {
              ...current,
              bars: merged.bars,
              indicators: merged.indicators,
              indicatorsAvailable: payload.indicators_available,
              indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
              marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
              structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
              structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
            };
          });
        };
      const mergeUnifiedStructurePayload = (payload: QmdBarHistory) => {
          if (!active || requestKeyRef.current !== requestKey) return;
          const rows = unifiedStructureProjectionRows(payload.indicators, cutoffMs);
          setState((current) => ({
            ...current,
            indicators: limitIndicatorRowsToLatest(mergeIndicatorRowsByTime(current.indicators, rows), rowBudget),
            indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
            // Unified structure is fetched independently from the ordinary
            // indicator projection. When it is the only deferred request it
            // owns completion of the loading notice as well as the rows.
            historyNotice: !standardIndicatorsRequested && current.historyNotice === "Loading requested indicators..."
              ? ""
              : current.historyNotice,
          }));
        };
      barsRequest
        .then((payload) => {
          if (!active || requestKeyRef.current !== requestKey) return;
          if (!progressive) rememberChartSnapshot(requestKey, payload);
          setReadyKey(requestKey);
          updateHistoryCursor(historyCursorRef, payload);
          const closedBars = closedRowsAtCutoff(payload.history, timeframe, cutoffMs);
          const closedIndicators = closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs);
          const aligned = alignHistoricalChartRows(closedBars, closedIndicators, payload.indicators_available);
          const replaceDisplayedTimeframe = displayedRequestKeyRef.current !== requestKey;
          displayedIdentityRef.current = identityKey;
          displayedRequestKeyRef.current = requestKey;
          setState((current) => {
            const merged = mergeHistoricalChartPage(
              replaceDisplayedTimeframe ? [] : current.bars,
              replaceDisplayedTimeframe ? [] : current.indicators,
              aligned.bars,
              aligned.indicators,
              rowBudget,
            );
            return {
              ...current,
              bars: merged.bars,
              canLoadEarlier: payload.has_more && !merged.atCapacity,
              marketSignalEvents: mergeMarketSignalEvents(replaceDisplayedTimeframe ? [] : current.marketSignalEvents, payload.market_signal_events),
              historyError: "",
              historyNotice: merged.atCapacity
                ? chartHistoryLimitNotice(rowBudget)
                : progressive ? liveTail ? "Loading current QMD indicators..." : standardIndicatorsRequested || unifiedStructureSelected ? "Loading requested indicators..." : "" : liveTail ? "Historical base loaded; connecting the QMD live tail..." : "",
              indicators: merged.indicators,
              indicatorsAvailable: progressive ? current.indicatorsAvailable : payload.indicators_available,
              indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
              structureEvents: mergeStructureEvents(replaceDisplayedTimeframe ? [] : current.structureEvents, payload.structure_events),
              structureLevelHistory: mergeStructureLevelHistory(replaceDisplayedTimeframe ? [] : current.structureLevelHistory, payload.structure_level_history),
              loading: false,
            };
          });
          if (!progressive) {
            return null;
          }
          void auxiliaryRequest?.().then(mergeAuxiliaryPayload).catch(() => undefined);
          void unifiedStructureRequest?.().then(mergeUnifiedStructurePayload).catch((reason) => {
            if (historyController.signal.aborted || !active || requestKeyRef.current !== requestKey) return;
            setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason), historyNotice: "" }));
          });
          return fullRequest?.() ?? null;
        })
        .then((payload) => {
          if (!payload || !active || requestKeyRef.current !== requestKey) return;
          rememberChartSnapshot(requestKey, payload);
          updateHistoryCursor(historyCursorRef, payload);
          const aligned = alignHistoricalChartRows(
            closedRowsAtCutoff(payload.history, timeframe, cutoffMs),
            closedRowsAtCutoff(payload.indicators, timeframe, cutoffMs),
            payload.indicators_available,
          );
          setState((current) => {
            const merged = mergeHistoricalChartPage(current.bars, current.indicators, aligned.bars, aligned.indicators, rowBudget);
            return {
              ...current,
              bars: merged.bars,
              canLoadEarlier: payload.has_more && !merged.atCapacity,
              marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
              historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : "",
              indicators: merged.indicators,
              indicatorsAvailable: payload.indicators_available,
              indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
              structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
              structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
            };
          });
        })
        .catch((reason) => {
          if (historyController.signal.aborted) return;
          if (!active || requestKeyRef.current !== requestKey) return;
          setReadyKey(requestKey);
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason), historyNotice: "", loading: false }));
        })
        .finally(() => {
          if (historyAbortRef.current === historyController) {
            historyAbortRef.current = null;
            historyRequestRef.current = false;
          }
        });
    };

    fetchHistoricalPage();

    return () => {
      active = false;
      if (requestKeyRef.current === requestKey) requestKeyRef.current = "";
      historyController.abort();
    };
  }, [auxiliaryProjection.includeMarketSignals, auxiliaryProjection.includeStructure, baseIndicatorColumns, enabled, eventIndicatorColumns, fullSession, historicalMode, historyTimeoutMs, indicatorColumns, pointInTime, projectionKey, rowBudget, sessionDate, standardIndicatorsRequested, symbol, timeframe, unifiedStructureSelected]);

  useEffect(() => {
    if (!enabled || liveTail) return;
    const ticker = symbol.trim().toUpperCase();
    const requestKey = chartRequestKey(ticker, timeframe, projectionKey, sessionDate, historicalMode, fullSession);
    if (!ticker || refreshCutoffMs === loadedCutoffRef.current || requestKeyRef.current !== requestKey) return;
    const replacingRewind = refreshCutoffMs < loadedCutoffRef.current;
    loadedCutoffRef.current = refreshCutoffMs;
    const controller = new AbortController();
    const requestParams = { as_of: new Date(refreshCutoffMs).toISOString(), full_session: fullSession, mode: historicalMode, row_limit: fullSession ? chartFullSessionPageSize(timeframe) : chartInitialPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe };
    api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, include_market_signals: auxiliaryProjection.includeMarketSignals, include_structure: auxiliaryProjection.includeStructure, indicator_columns: baseIndicatorColumns, stage: "bars" })}`, {
      signal: controller.signal,
      timeoutMs: 120_000,
    })
      .then((payload) => {
        if (controller.signal.aborted || requestKeyRef.current !== requestKey) return;
        rememberChartSnapshot(requestKey, payload);
        updateHistoryCursor(historyCursorRef, payload);
        const aligned = alignHistoricalChartRows(
          closedRowsAtCutoff(payload.history, timeframe, refreshCutoffMs),
          closedRowsAtCutoff(payload.indicators, timeframe, refreshCutoffMs),
          payload.indicators_available,
        );
        setState((current) => {
          const merged = replacingRewind
            ? mergeHistoricalChartPage([], [], aligned.bars, aligned.indicators, rowBudget)
            : mergeHistoricalChartPage(current.bars, current.indicators, aligned.bars, aligned.indicators, rowBudget);
          return {
            ...current,
            bars: merged.bars,
            canLoadEarlier: payload.has_more && !merged.atCapacity,
            indicators: merged.indicators,
            indicatorsAvailable: payload.indicators_available,
            indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
            lastUpdateAt: new Date().toISOString(),
            marketSignalEvents: mergeMarketSignalEvents(replacingRewind ? [] : current.marketSignalEvents, payload.market_signal_events),
            structureEvents: mergeStructureEvents(replacingRewind ? [] : current.structureEvents, payload.structure_events),
            structureLevelHistory: mergeStructureLevelHistory(replacingRewind ? [] : current.structureLevelHistory, payload.structure_level_history),
          };
        });
      })
      .catch((reason) => {
        if (!controller.signal.aborted && requestKeyRef.current === requestKey) {
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
        }
      });
    if (standardIndicatorsRequested) {
      void api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, include_market_signals: false, include_structure: false, indicator_columns: eventIndicatorColumns, stage: "full" })}`, {
        signal: controller.signal,
        timeoutMs: 180_000,
      }).then((payload) => {
        if (controller.signal.aborted || requestKeyRef.current !== requestKey) return;
        const rows = closedRowsAtCutoff(payload.indicators, timeframe, refreshCutoffMs);
        setState((current) => ({
          ...current,
          indicators: limitIndicatorRowsToLatest(mergeIndicatorRowsByTime(current.indicators, rows), rowBudget),
          indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
        }));
      }).catch((reason) => {
        if (!controller.signal.aborted && requestKeyRef.current === requestKey) {
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
        }
      });
    }
    if (unifiedStructureSelected) {
      void api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, full_session: true, include_market_signals: false, include_structure: false, indicator_columns: unifiedStructureColumns, row_limit: chartFullSessionPageSize(UNIFIED_STRUCTURE_TIMEFRAME), stage: "full", timeframe: UNIFIED_STRUCTURE_TIMEFRAME })}`, {
        signal: controller.signal,
        timeoutMs: historyTimeoutMs,
      }).then((payload) => {
        if (controller.signal.aborted || requestKeyRef.current !== requestKey) return;
        const rows = unifiedStructureProjectionRows(payload.indicators, refreshCutoffMs);
        setState((current) => ({
          ...current,
          indicators: limitIndicatorRowsToLatest(mergeIndicatorRowsByTime(current.indicators, rows), rowBudget),
          indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
        }));
      }).catch((reason) => {
        if (!controller.signal.aborted && requestKeyRef.current === requestKey) {
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
        }
      });
    }
    return () => controller.abort();
  }, [auxiliaryProjection.includeMarketSignals, auxiliaryProjection.includeStructure, baseIndicatorColumns, enabled, eventIndicatorColumns, fullSession, historicalMode, historyTimeoutMs, liveTail, projectionKey, refreshCutoffMs, rowBudget, sessionDate, standardIndicatorsRequested, symbol, timeframe, unifiedStructureSelected]);

  useEffect(() => {
    if (!enabled || !liveTail) return;
    const ticker = symbol.trim().toUpperCase();
    if (!ticker) return;
    const requestKey = chartRequestKey(ticker, timeframe, projectionKey, sessionDate, historicalMode, fullSession);
    const identityKey = chartIdentityKey(ticker, sessionDate, historicalMode, fullSession);
    let active = true;
    let resnapshotController: AbortController | null = null;
    const sockets: Partial<Record<"bars" | "indicators", WebSocket>> = {};
    const reconnectTimers: Partial<Record<"bars" | "indicators", number>> = {};
    const reconnectAttempts = { bars: 0, indicators: 0 };

    const applyRows = (kind: "bars" | "indicators", rows: QmdLiveBar[] | HistoricalIndicator[]) => {
      if (!active) return;
      setState((current) => {
        const replacingDisplayedTimeframe = displayedRequestKeyRef.current !== requestKey;
        if (kind === "indicators" && replacingDisplayedTimeframe) return current;
        if (kind === "bars" && replacingDisplayedTimeframe && rows.length === 0) return current;
        if (kind === "bars" && replacingDisplayedTimeframe && rows.length > 0) {
          displayedIdentityRef.current = identityKey;
          displayedRequestKeyRef.current = requestKey;
        }
        const bars = kind === "bars"
          ? limitLiveRowsWithHysteresis(
            mergeRowsByTime(replacingDisplayedTimeframe ? [] : current.bars, rows as QmdLiveBar[]),
            rowBudget,
          )
          : current.bars;
        const admittedTimes = new Set(bars.map(barStartTime));
        const indicators = kind === "indicators"
          ? limitLiveRowsWithHysteresis(mergeRowsByTime(current.indicators, rows as HistoricalIndicator[]), rowBudget).filter((row) => admittedTimes.has(barStartTime(row)))
          : (replacingDisplayedTimeframe ? [] : current.indicators).filter((row) => admittedTimes.has(barStartTime(row)));
        return {
          ...current,
          bars,
          connected: kind === "bars" ? true : current.connected,
          error: kind === "bars" ? "" : current.error,
          historyNotice: "QMD live stream connected; bar revisions are merged by timestamp.",
          indicators,
          lastUpdateAt: kind === "bars" ? new Date().toISOString() : current.lastUpdateAt,
          loading: false,
          pointInTime: false,
        };
      });
    };

    const resnapshot = () => {
      if (!active || resnapshotController || document.visibilityState === "hidden") return;
      const controller = new AbortController();
      resnapshotController = controller;
      api<QmdLiveChartPayload>(`/api/trading/canvas-live-chart${query({ indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), symbol: ticker, timeframe })}`, { signal: controller.signal, timeoutMs: 20_000 })
        .then((payload) => {
          if (!active || controller.signal.aborted) return;
          applyRows("bars", qmdSnapshotRows(payload.bars));
          applyRows("indicators", qmdSnapshotRows(payload.indicators));
          const liveError = Object.values(payload.errors ?? {}).filter(Boolean).join("; ");
          if (liveError) setState((current) => ({ ...current, error: liveError }));
        })
        .catch((reason) => {
          if (!active || controller.signal.aborted) return;
          setState((current) => ({ ...current, error: reason instanceof Error ? reason.message : String(reason), historyNotice: "Live resnapshot failed; retaining the last complete chart while the stream continues or reconnects." }));
        })
        .finally(() => {
          if (resnapshotController === controller) resnapshotController = null;
        });
    };

    const connect = (kind: "bars" | "indicators") => {
      if (!active || document.visibilityState === "hidden") return;
      const existing = sockets[kind];
      if (existing && (existing.readyState === WebSocket.CONNECTING || existing.readyState === WebSocket.OPEN)) return;
      const socket = new WebSocket(canvasLiveStreamUrl(kind, ticker, timeframe, indicatorColumns));
      sockets[kind] = socket;
      socket.onopen = () => {
        reconnectAttempts[kind] = 0;
      };
      socket.onmessage = (event) => {
        if (!active) return;
        try {
          const payload = JSON.parse(String(event.data)) as QmdStreamSnapshot<QmdLiveBar> | QmdStreamSnapshot<HistoricalIndicator>;
          if (payload.type === "stream_gap" || payload.action === "resnapshot_required") {
            resnapshot();
            socket.close();
            return;
          }
          if (payload.error) {
            setState((current) => ({ ...current, connected: kind === "bars" ? false : current.connected, error: payload.error ?? "QMD live stream error." }));
            return;
          }
          const rows = kind === "bars"
            ? qmdSnapshotRows(payload as QmdStreamSnapshot<QmdLiveBar>)
            : qmdSnapshotRows(payload as QmdStreamSnapshot<HistoricalIndicator>);
          applyRows(kind, rows);
        } catch {
          setState((current) => ({ ...current, connected: kind === "bars" ? false : current.connected, error: `QMD live ${kind} stream returned invalid data.` }));
        }
      };
      socket.onclose = () => {
        if (sockets[kind] !== socket) return;
        delete sockets[kind];
        if (!active || document.visibilityState === "hidden") return;
        if (kind === "bars") setState((current) => ({ ...current, connected: false, historyNotice: "Live stream disconnected; retaining the last chart while reconnecting." }));
        const delay = Math.min(15_000, 500 * (2 ** reconnectAttempts[kind]));
        reconnectAttempts[kind] += 1;
        reconnectTimers[kind] = window.setTimeout(() => connect(kind), delay);
      };
    };

    connect("bars");
    if (ENRICHED_QMD_TIMEFRAMES.has(timeframe)) connect("indicators");
    // Register the focused QMD computation target and capture one coherent
    // live bars+indicators snapshot. WebSockets then carry only revisions.
    resnapshot();
    const handleVisibilityChange = () => {
      if (!active) return;
      if (document.visibilityState === "hidden") {
        Object.entries(sockets).forEach(([kind, socket]) => {
          delete sockets[kind as "bars" | "indicators"];
          socket?.close();
        });
        return;
      }
      connect("bars");
      if (ENRICHED_QMD_TIMEFRAMES.has(timeframe)) connect("indicators");
      resnapshot();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      resnapshotController?.abort();
      Object.values(reconnectTimers).forEach((timer) => timer !== undefined && window.clearTimeout(timer));
      Object.values(sockets).forEach((socket) => socket?.close());
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [enabled, fullSession, historicalMode, indicatorColumns, liveTail, projectionKey, rowBudget, sessionDate, symbol, timeframe]);

  const currentRequestKey = chartRequestKey(symbol.trim().toUpperCase(), timeframe, projectionKey, sessionDate, historicalMode, fullSession);
  const causalBars = pointInTime ? closedRowsAtCutoff(state.bars, timeframe, cutoffMs) : state.bars;
  const causalTimes = pointInTime ? new Set(causalBars.map(barStartTime)) : null;
  const causalIndicators = causalTimes
    ? state.indicators.filter((row) => causalTimes.has(barStartTime(row)) || isUnifiedStructureProjectionRow(row) && barStartTime(row) <= cutoffMs)
    : state.indicators;
  return { ...state, bars: causalBars, indicators: causalIndicators, loadEarlier, ready: enabled && readyKey === currentRequestKey };
}

function chartRequestKey(ticker: string, timeframe: string, indicatorColumns: string, sessionDate: string, historicalMode: HistoricalChartMode, fullSession: boolean): string {
  return [ticker, timeframe, indicatorColumns, sessionDate, historicalMode, fullSession ? "full-session" : "paged"].join("|");
}

function chartIdentityKey(ticker: string, sessionDate: string, historicalMode: HistoricalChartMode, fullSession: boolean): string {
  // Indicator selection changes the enrichment projection, not the underlying
  // instrument/session chart. Keep the last complete candles mounted while
  // the new projection is requested.
  return [ticker, sessionDate, historicalMode, fullSession ? "full-session" : "paged"].join("|");
}

function rememberChartSnapshot(key: string, payload: QmdBarHistory) {
  CHART_SNAPSHOT_CACHE.delete(key);
  CHART_SNAPSHOT_CACHE.set(key, { cachedAt: Date.now(), payload });
  while (CHART_SNAPSHOT_CACHE.size > CHART_SNAPSHOT_CACHE_LIMIT) {
    const oldest = CHART_SNAPSHOT_CACHE.keys().next().value;
    if (typeof oldest !== "string") break;
    CHART_SNAPSHOT_CACHE.delete(oldest);
  }
}

function readChartSnapshot(key: string): QmdBarHistory | null {
  const cached = CHART_SNAPSHOT_CACHE.get(key);
  if (!cached) return null;
  if (Date.now() - cached.cachedAt > CHART_SNAPSHOT_CACHE_TTL_MS) {
    CHART_SNAPSHOT_CACHE.delete(key);
    return null;
  }
  CHART_SNAPSHOT_CACHE.delete(key);
  CHART_SNAPSHOT_CACHE.set(key, cached);
  return cached.payload;
}

function qmdSnapshotRows<T extends { bar_start: string }>(payload: { current?: T | null; history?: T[] }): T[] {
  return mergeRowsByTime(payload.history ?? [], payload.current ? [payload.current] : []);
}

export function canvasLiveStreamUrl(kind: "bars" | "indicators", symbol: string, timeframe: string, indicatorColumns = "") {
  return apiWebSocketUrl(`/api/trading/canvas-live-chart/stream/${kind}/${encodeURIComponent(symbol)}${query({ indicator_columns: kind === "indicators" ? indicatorColumns : undefined, limit: chartPageSize(timeframe), timeframe })}`);
}

export function mergeStructureEvents(current: QmdStructureEvent[], incoming: QmdStructureEvent[] | undefined) {
  const byId = new Map<number, QmdStructureEvent>();
  [...current, ...(incoming ?? [])].forEach((event) => {
    if (
      Number.isFinite(event.event_id)
      && event.event_id > 0
      && ["level_promoted", "structure_crossed", "structure_break", "bos", "choch"].includes(event.event_kind)
    ) {
      byId.set(event.event_id, event);
    }
  });
  return retainStructureEventsPerTimeframe(
    [...byId.values()].sort((left, right) =>
      Date.parse(left.confirmed_at) - Date.parse(right.confirmed_at) || left.event_id - right.event_id),
    () => true,
  );
}

export function mergeStructureLevelHistory(
  current: QmdStructureLevelCandidate[],
  incoming: QmdStructureLevelCandidate[] | undefined,
) {
  const byIdentity = new Map<string, QmdStructureLevelCandidate>();
  [...current, ...(incoming ?? [])].forEach((level) => {
    if (!isQmdStructureLevelCandidate(level)) return;
    const key = `${level.footprint_session_date}:${level.created_at_ms}:${level.side}:${Number(level.price).toFixed(8)}`;
    const existing = byIdentity.get(key);
    if (!existing || level.footprint_as_of_ms >= existing.footprint_as_of_ms) {
      byIdentity.set(key, level);
    }
  });
  return [...byIdentity.values()]
    .sort((left, right) =>
      left.footprint_as_of_ms - right.footprint_as_of_ms
      || left.created_at_ms - right.created_at_ms)
    .slice(-4_000);
}

export function mergeMarketSignalEvents(current: QmdMarketSignalEvent[], incoming: QmdMarketSignalEvent[] | undefined) {
  const merged = new Map<string, QmdMarketSignalEvent>();
  [...current, ...(incoming ?? [])].forEach((event) => {
    if (event.event_id) merged.set(event.event_id, event);
  });
  return [...merged.values()]
    .sort((left, right) => Date.parse(left.effective_at) - Date.parse(right.effective_at)
      || left.event_id.localeCompare(right.event_id))
    .slice(-10_000);
}

export function chartPageSize(timeframe: string) {
  const pageSizes: Record<string, number> = {
    "100ms": 2_400,
    "1s": 1_800,
    "5s": 1_200,
    "10s": 900,
    "30s": 600,
    "1m": 240,
    "5m": 192,
    "1h": 64,
    "1d": 240,
    "1w": 156,
    "1mo": 36,
    "1y": 20,
  };
  return pageSizes[timeframe] ?? 240;
}

export function chartInitialPageSize(timeframe: string) {
  const pageSizes: Record<string, number> = {
    // Focused live charts must arrive with enough causal history to pan across
    // the active session. A tiny visual-window fetch made liquid 10-second
    // charts appear to begin only minutes before the operator opened them.
    "100ms": 5_000,
    "1s": 5_000,
    "5s": 5_000,
    "10s": 5_000,
    "30s": 1_920,
    "1m": 960,
    "5m": 192,
    "1h": 64,
  };
  return pageSizes[timeframe] ?? chartPageSize(timeframe);
}

export function chartFullSessionPageSize(timeframe: string) {
  const pageSizes: Record<string, number> = {
    "100ms": 25_000,
    "1s": 25_000,
    "5s": 5_000,
    "10s": 5_000,
    "30s": 1_920,
    "1m": 960,
    "5m": 192,
    "1h": 64,
  };
  return pageSizes[timeframe] ?? chartInitialPageSize(timeframe);
}

export function chartRowBudget(indicatorColumns: string): number {
  const projectedColumnCount = indicatorColumns ? indicatorColumns.split(",").length : 1;
  return Math.max(5_000, Math.min(25_000, Math.floor(500_000 / (projectedColumnCount + 20))));
}

export function chartHistoryLimitNotice(rowBudget: number): string {
  return `${rowBudget.toLocaleString()} chart points loaded. Choose a higher timeframe to inspect earlier history.`;
}

export function requestedIndicatorColumns(visibleIndicatorIds: string[]): string {
  const selected = new Set(visibleIndicatorIds.map((value) => value.toLowerCase()));
  const columns = new Set<string>(["bar_start"]);
  CHART_INDICATORS.forEach((indicator) => {
    if (!selected.has(indicator.id.toLowerCase())) return;
    indicator.sourceColumns?.forEach((column) => columns.add(column));
  });
  return [...columns].sort().join(",");
}

export function requestedChartAuxiliary(visibleIndicatorIds: string[]) {
  const selected = new Set(visibleIndicatorIds.map((value) => value.toLowerCase()));
  return {
    includeMarketSignals: selected.has("indicator.qmd_market_signals"),
    includeStructure: selected.has("indicator.qmd_generic_structure") || selected.has("indicator.qmd_level_footprint"),
  };
}

export function alignHistoricalChartRows(
  bars: QmdLiveBar[],
  indicators: HistoricalIndicator[],
  indicatorsRequired: boolean,
) {
  if (!indicatorsRequired) return { bars, indicators: [] };
  const indicatorTimes = new Set(indicators.map((row) => row.bar_start));
  const alignedBars = bars.filter((row) => indicatorTimes.has(row.bar_start));
  const barTimes = new Set(alignedBars.map((row) => row.bar_start));
  return {
    bars: alignedBars,
    indicators: indicators.filter((row) => barTimes.has(row.bar_start)),
  };
}

export function updateHistoryCursor(ref: MutableRefObject<ChartHistoryCursor | null>, payload: QmdBarHistory) {
  ref.current = {
    asOf: payload.as_of,
    nextBefore: payload.next_before,
    previousSessionBefore: payload.previous_session_before,
    sessionDate: payload.earliest_session_date,
  };
}

export function earlierChartHistoryRequest(
  cursor: ChartHistoryCursor | null,
  ticker: string,
  timeframe: CanvasChartTimeframe,
  indicatorColumns: string,
  historicalMode: HistoricalChartMode = "replay",
): { key: string; params: Record<string, string | number | undefined> } | null {
  if (!cursor || (!cursor.nextBefore && !cursor.previousSessionBefore)) return null;
  const params = cursor.nextBefore
    ? { as_of: cursor.asOf, before_bar: cursor.nextBefore, indicator_columns: indicatorColumns, mode: historicalMode, row_limit: chartPageSize(timeframe), session_date: cursor.sessionDate, symbol: ticker, timeframe }
    : { before: cursor.previousSessionBefore, indicator_columns: indicatorColumns, mode: historicalMode, row_limit: chartPageSize(timeframe), symbol: ticker, timeframe };
  return {
    key: [ticker, timeframe, indicatorColumns, cursor.asOf, cursor.sessionDate, cursor.nextBefore, cursor.previousSessionBefore].join("|"),
    params,
  };
}

export function closedRowsAtCutoff<T extends { bar_start: string }>(rows: T[], timeframe: string, cutoffMs = Date.now()): T[] {
  const durationMs = timeframeDurationMs(timeframe);
  return rows.filter((row) => {
    const closeMetadata = row as T & { bar_end?: string; is_closed?: boolean };
    // QMD History builds the current macro period causally from completed
    // daily bars through the requested as-of clock. It remains an explicitly
    // partial macro row, but it is still the correct last candle to present.
    // Other open bars stay excluded from historical Canvas charts.
    if (closeMetadata.is_closed === false && !["1w", "1mo", "1y"].includes(timeframe)) return false;
    const startMs = Date.parse(row.bar_start);
    const endMs = closeMetadata.bar_end ? Date.parse(closeMetadata.bar_end) : startMs + durationMs;
    return Number.isFinite(startMs) && Number.isFinite(endMs) && endMs <= cutoffMs;
  });
}

export function timeframeDurationMs(timeframe: string): number {
  if (timeframe === "1d") return 24 * 60 * 60 * 1_000;
  if (timeframe === "1w") return 7 * 24 * 60 * 60 * 1_000;
  if (timeframe === "1mo") return 30 * 24 * 60 * 60 * 1_000;
  if (timeframe === "1y") return 365 * 24 * 60 * 60 * 1_000;
  const match = /^(\d+)(ms|s|m|h)$/.exec(timeframe.trim().toLowerCase());
  if (!match) return 60_000;
  const value = Number(match[1]);
  const unitMs = match[2] === "ms" ? 1 : match[2] === "s" ? 1_000 : match[2] === "m" ? 60_000 : 3_600_000;
  return value * unitMs;
}

export function mergeRowsByTime<T extends { bar_start: string }>(existing: T[], incoming: T[]): T[] {
  const nextRows = normalizedRowsByTime(incoming);
  if (!nextRows.length) return existing;
  const merged: T[] = [];
  let leftIndex = 0;
  let rightIndex = 0;
  let changed = false;
  while (leftIndex < existing.length || rightIndex < nextRows.length) {
    const left = existing[leftIndex];
    const right = nextRows[rightIndex];
    const leftTime = left ? barStartTime(left) : Number.POSITIVE_INFINITY;
    const rightTime = right ? barStartTime(right) : Number.POSITIVE_INFINITY;
    if (!right || (left && leftTime < rightTime)) {
      merged.push(left);
      leftIndex += 1;
      continue;
    }
    if (!left || rightTime < leftTime) {
      merged.push(right);
      rightIndex += 1;
      changed = true;
      continue;
    }
    const replacement = shallowRowEqual(left, right) ? left : right;
    merged.push(replacement);
    changed ||= replacement !== left;
    leftIndex += 1;
    rightIndex += 1;
  }
  if (!changed && merged.length === existing.length) return existing;
  return merged;
}

export function mergeIndicatorRowsByTime(existing: HistoricalIndicator[], incoming: HistoricalIndicator[]): HistoricalIndicator[] {
  const incomingByTime = new Map(normalizedRowsByTime(incoming).map((row) => [barStartTime(row), row]));
  const merged = existing.map((row) => {
    const replacement = incomingByTime.get(barStartTime(row));
    if (!replacement) return row;
    incomingByTime.delete(barStartTime(row));
    const next = { ...row };
    if (hasUnifiedStructureProjection(replacement)) {
      // Snapshot and delta are mutually exclusive representations of one
      // level-book state transition. Generic object spreading retained an old
      // counterpart at the same timestamp, allowing a later settings redraw
      // to reveal stale per-bar fragments.
      delete next.qmd_structure_unified_levels;
      delete next.qmd_structure_unified_level_delta;
    }
    return { ...next, ...replacement };
  });
  incomingByTime.forEach((row) => merged.push(row));
  return normalizedRowsByTime(merged);
}

function hasUnifiedStructureProjection(row: HistoricalIndicator): boolean {
  return Object.prototype.hasOwnProperty.call(row, "qmd_structure_unified_levels")
    || Object.prototype.hasOwnProperty.call(row, "qmd_structure_unified_level_delta");
}

function isUnifiedStructureProjectionRow(row: HistoricalIndicator): boolean {
  return hasUnifiedStructureProjection(row);
}

function unifiedStructureProjectionRows(rows: HistoricalIndicator[], cutoffMs: number): HistoricalIndicator[] {
  return rows.filter((row) => isUnifiedStructureProjectionRow(row) && barStartTime(row) <= cutoffMs);
}

export function mergeHistoricalChartPage(
  currentBars: QmdLiveBar[],
  currentIndicators: HistoricalIndicator[],
  incomingBars: QmdLiveBar[],
  incomingIndicators: HistoricalIndicator[],
  rowBudget: number,
) {
  const existingTimes = new Set(currentBars.map(barStartTime));
  const availableSlots = Math.max(0, rowBudget - currentBars.length);
  const replacementBars = incomingBars.filter((row) => existingTimes.has(barStartTime(row)));
  const newBars = incomingBars.filter((row) => !existingTimes.has(barStartTime(row)));
  const admittedBars = availableSlots < newBars.length ? newBars.slice(newBars.length - availableSlots) : newBars;
  // Later authority revisions must replace matching timestamps; filtering
  // duplicates here would leave the first OHLC/volume values on screen.
  const bars = limitRowsToLatest(mergeRowsByTime(currentBars, [...replacementBars, ...admittedBars]), rowBudget);
  const admittedTimes = new Set(bars.map(barStartTime));
  const indicators = limitIndicatorRowsToLatest(
    mergeIndicatorRowsByTime(currentIndicators, incomingIndicators.filter((row) => admittedTimes.has(barStartTime(row)) || isUnifiedStructureProjectionRow(row))),
    rowBudget,
  ).filter((row) => admittedTimes.has(barStartTime(row)) || isUnifiedStructureProjectionRow(row));
  return { atCapacity: bars.length >= rowBudget, bars, indicators };
}

export function limitRowsToLatest<T>(rows: T[], rowBudget: number): T[] {
  return rows.length <= rowBudget ? rows : rows.slice(rows.length - rowBudget);
}

export function limitIndicatorRowsToLatest(rows: HistoricalIndicator[], rowBudget: number): HistoricalIndicator[] {
  const normalized = normalizedRowsByTime(rows);
  // Unified structural rows form a causal state machine: the initial snapshot
  // is the authority that every later delta modifies. Applying the candle row
  // budget to this stream can retain only tail deltas, leaving the renderer
  // with no book to update and therefore no visible levels. Retain the complete
  // bounded structural projection while independently limiting ordinary
  // bar-aligned indicators.
  const ordinaryRows = normalized.filter((row) => !isUnifiedStructureProjectionRow(row));
  if (ordinaryRows.length <= rowBudget) return normalized;
  const retainedOrdinaryTimes = new Set(
    ordinaryRows.slice(ordinaryRows.length - rowBudget).map(barStartTime),
  );
  return normalized.filter((row) => (
    isUnifiedStructureProjectionRow(row) || retainedOrdinaryTimes.has(barStartTime(row))
  ));
}

export function limitLiveRowsWithHysteresis<T>(rows: T[], rowBudget: number): T[] {
  const evictionChunk = Math.max(250, Math.min(2_000, Math.floor(rowBudget * 0.2)));
  return rows.length <= rowBudget + evictionChunk ? rows : rows.slice(rows.length - rowBudget);
}

export function normalizedRowsByTime<T extends { bar_start: string }>(rows: T[]): T[] {
  const valid = rows.filter((row) => row && Number.isFinite(barStartTime(row)));
  let ordered = true;
  for (let index = 1; index < valid.length; index += 1) {
    if (barStartTime(valid[index - 1]) > barStartTime(valid[index])) {
      ordered = false;
      break;
    }
  }
  const sorted = ordered ? valid : [...valid].sort((left, right) => barStartTime(left) - barStartTime(right));
  if (sorted.length < 2) return sorted;
  const deduplicated: T[] = [];
  sorted.forEach((row) => {
    if (deduplicated.length && barStartTime(deduplicated[deduplicated.length - 1]) === barStartTime(row)) deduplicated[deduplicated.length - 1] = row;
    else deduplicated.push(row);
  });
  return deduplicated;
}

export const barStartTimeCache = new WeakMap<object, number>();

export function barStartTime(row: { bar_start: string }): number {
  const cached = barStartTimeCache.get(row);
  if (cached !== undefined) return cached;
  const parsed = Date.parse(row.bar_start);
  const value = Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
  barStartTimeCache.set(row, value);
  return value;
}

export function shallowRowEqual<T extends object>(left: T, right: T): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  return leftKeys.every((key) => leftRecord[key] === rightRecord[key]);
}
export function qmdMarketSignalChartMarkers(
  events: QmdMarketSignalEvent[],
  bars: HistoricalBar[],
  visibleIndicators: string[],
): ChartPayload["markers"] {
  if (!visibleIndicators.includes("indicator.qmd_market_signals") || !events.length || !bars.length) {
    return [];
  }
  const intervals = bars.map((bar) => ({
    end: Date.parse(bar.bar_end || "") || Date.parse(bar.bar_start) + 1,
    start: Date.parse(bar.bar_start),
    time: Date.parse(bar.bar_start) / 1000,
  }));
  return events
    .filter((event) => event.state === "triggered" && ["bullish", "bearish"].includes(event.direction))
    .map((event) => {
      const effectiveAt = Date.parse(event.effective_at);
      if (!Number.isFinite(effectiveAt)) return null;
      const interval = intervals.find((candidate) => effectiveAt >= candidate.start && effectiveAt < candidate.end)
        ?? intervals.find((candidate) => candidate.start >= effectiveAt);
      if (!interval) return null;
      const bullish = event.direction === "bullish";
      return {
        color: bullish ? "var(--success)" : "var(--danger)",
        displayItemId: "indicator.qmd_market_signals",
        position: bullish ? "belowBar" : "aboveBar",
        shape: bullish ? "arrowUp" : "arrowDown",
        size: 1,
        text: `${Math.round(boundedUnit(event.confidence) * 100)}% · ${event.working_timeframe}`,
        time: interval.time as UTCTimestamp,
      } satisfies NonNullable<ChartPayload["markers"]>[number];
    })
    .filter((marker): marker is NonNullable<typeof marker> => marker !== null);
}
export function extendedSessionRegions(bars: QmdLiveBar[]) {
  const sessions = new Set(bars.map((bar) => marketSessionDate(bar.bar_start)).filter(Boolean));
  return [...sessions].sort().flatMap((sessionDate) => [
    {
      color: "var(--chart-premarket)",
      end: dateInTimeZone(sessionDate, "09:30", "America/New_York").getTime() / 1000,
      label: "Premarket",
      start: dateInTimeZone(sessionDate, "04:00", "America/New_York").getTime() / 1000,
    },
    {
      color: "var(--chart-after-hours)",
      end: dateInTimeZone(sessionDate, "20:00", "America/New_York").getTime() / 1000,
      label: "After hours",
      start: dateInTimeZone(sessionDate, "16:00", "America/New_York").getTime() / 1000,
    },
  ]);
}

export function marketSessionDate(timestamp: string) {
  const instant = new Date(timestamp);
  if (Number.isNaN(instant.getTime())) return "";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "2-digit", timeZone: "America/New_York", year: "numeric" }).formatToParts(instant).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

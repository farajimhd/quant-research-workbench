import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import type { UTCTimestamp } from "lightweight-charts";

import { api, query } from "../../api/client";
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
export function useCanvasHistoricalChart(symbol: string, timeframe: CanvasChartTimeframe, cutoffMs: number, sessionDate: string, visibleIndicatorIds: string[], liveTail = false, enabled = true): CanvasLiveChartState {
  const pointInTime = !liveTail;
  const indicatorColumns = useMemo(() => requestedIndicatorColumns(visibleIndicatorIds), [visibleIndicatorIds]);
  const rowBudget = useMemo(() => chartRowBudget(indicatorColumns), [indicatorColumns]);
  const [state, setState] = useState<Omit<CanvasLiveChartState, "loadEarlier" | "ready">>({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", historyNotice: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, marketSignalEvents: [], pointInTime, structureEvents: [], structureLevelHistory: [] });
  const [readyKey, setReadyKey] = useState("");
  const historyCursorRef = useRef<ChartHistoryCursor | null>(null);
  const historyRequestRef = useRef(false);
  const historyAbortRef = useRef<AbortController | null>(null);
  const requestKeyRef = useRef("");
  const loadedCutoffRef = useRef(0);

  const loadEarlier = useCallback(() => {
    if (!enabled) return;
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    const cursor = historyCursorRef.current;
    if (!cursor || historyRequestRef.current || requestKeyRef.current !== requestKey) return;
    if (!cursor.nextBefore && !cursor.previousSessionBefore) return;
    const request = earlierChartHistoryRequest(cursor, ticker, timeframe, indicatorColumns);
    if (!request) return;
    const controller = new AbortController();
    historyAbortRef.current = controller;
    historyRequestRef.current = true;
    setState((current) => ({ ...current, historyError: "", loadingEarlier: true }));
    const page = api<QmdBarHistory>(`/api/trading/canvas-chart/history${query(request.params)}`, { signal: controller.signal, timeoutMs: 120000 });
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
  }, [cutoffMs, enabled, indicatorColumns, rowBudget, symbol, timeframe]);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const historyController = new AbortController();
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    historyAbortRef.current?.abort();
    historyAbortRef.current = historyController;
    requestKeyRef.current = requestKey;
    setReadyKey("");
    historyCursorRef.current = null;
    historyRequestRef.current = false;
    loadedCutoffRef.current = cutoffMs;
    setState({ bars: [], canLoadEarlier: false, connected: false, error: "", historyError: "", historyNotice: "", indicators: [], indicatorsAvailable: ENRICHED_QMD_TIMEFRAMES.has(timeframe), lastUpdateAt: "", loading: true, loadingEarlier: false, marketSignalEvents: [], pointInTime, structureEvents: [], structureLevelHistory: [] });

    const fetchHistoricalPage = () => {
      historyRequestRef.current = true;
      const requestParams = { as_of: new Date(cutoffMs).toISOString(), row_limit: chartPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe };
      const progressive = ENRICHED_QMD_TIMEFRAMES.has(timeframe);
      const barsRequest = progressive
        ? api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, row_limit: chartInitialPageSize(timeframe), stage: "bars" })}`, { signal: historyController.signal, timeoutMs: 120000 })
        : api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, indicator_columns: indicatorColumns, stage: "full" })}`, { signal: historyController.signal, timeoutMs: 120000 });
      barsRequest
        .then((payload) => {
          if (!active || requestKeyRef.current !== requestKey) return;
          setReadyKey(requestKey);
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
              historyNotice: merged.atCapacity ? chartHistoryLimitNotice(rowBudget) : progressive ? "Loading requested indicators..." : liveTail ? "Historical base loaded; connecting the QMD live tail..." : "",
              indicators: merged.indicators,
              indicatorsAvailable: progressive ? current.indicatorsAvailable : payload.indicators_available,
              indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
              structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
              structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
              loading: false,
            };
          });
          if (!progressive) {
            return null;
          }
          return api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...requestParams, indicator_columns: indicatorColumns, stage: "full" })}`, { signal: historyController.signal, timeoutMs: 120000 });
        })
        .then((payload) => {
          if (!payload || !active || requestKeyRef.current !== requestKey) return;
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
  }, [enabled, indicatorColumns, pointInTime, rowBudget, sessionDate, symbol, timeframe]);

  useEffect(() => {
    if (!enabled || liveTail) return;
    const ticker = symbol.trim().toUpperCase();
    const requestKey = `${ticker}:${timeframe}:${indicatorColumns}`;
    if (!ticker || cutoffMs <= loadedCutoffRef.current || requestKeyRef.current !== requestKey) return;
    loadedCutoffRef.current = cutoffMs;
    const controller = new AbortController();
    api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ as_of: new Date(cutoffMs).toISOString(), indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), session_date: sessionDate, symbol: ticker, timeframe })}`, {
      signal: controller.signal,
      timeoutMs: 120000,
    })
      .then((payload) => {
        if (controller.signal.aborted || requestKeyRef.current !== requestKey) return;
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
            indicators: merged.indicators,
            indicatorsAvailable: payload.indicators_available,
            indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance,
            lastUpdateAt: new Date().toISOString(),
            marketSignalEvents: mergeMarketSignalEvents(current.marketSignalEvents, payload.market_signal_events),
            structureEvents: mergeStructureEvents(current.structureEvents, payload.structure_events),
            structureLevelHistory: mergeStructureLevelHistory(current.structureLevelHistory, payload.structure_level_history),
          };
        });
      })
      .catch((reason) => {
        if (!controller.signal.aborted && requestKeyRef.current === requestKey) {
          setState((current) => ({ ...current, historyError: reason instanceof Error ? reason.message : String(reason) }));
        }
      });
    return () => controller.abort();
  }, [cutoffMs, enabled, indicatorColumns, liveTail, rowBudget, sessionDate, symbol, timeframe]);

  useEffect(() => {
    if (!enabled || !liveTail) return;
    const ticker = symbol.trim().toUpperCase();
    if (!ticker) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const refresh = async () => {
      if (cancelled || controller || document.visibilityState === "hidden") {
        if (!cancelled) timer = window.setTimeout(refresh, 1_000);
        return;
      }
      const request = new AbortController();
      controller = request;
      try {
        const payload = await api<QmdLiveChartPayload>(`/api/trading/canvas-live-chart${query({ indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), symbol: ticker, timeframe })}`, { signal: request.signal, timeoutMs: 75_000 });
        if (cancelled || request.signal.aborted) return;
        const bars = [...(payload.bars.history ?? []), ...(payload.bars.current ? [payload.bars.current] : [])];
        const indicators = [...(payload.indicators.history ?? []), ...(payload.indicators.current ? [payload.indicators.current] : [])];
        const liveError = Object.values(payload.errors ?? {}).filter(Boolean).join("; ");
        setState((current) => {
          const mergedBars = limitLiveRowsWithHysteresis(mergeRowsByTime(current.bars, bars), rowBudget);
          const admittedTimes = new Set(mergedBars.map(barStartTime));
          return {
            ...current,
            bars: mergedBars,
            connected: true,
            error: liveError,
            historyNotice: liveError ? `Live bars are current; one derived stream is partial: ${liveError}` : "QMD live tail connected; current-bar replacements are applied by bar timestamp.",
            indicators: limitLiveRowsWithHysteresis(mergeRowsByTime(current.indicators, indicators), rowBudget).filter((row) => admittedTimes.has(barStartTime(row))),
            lastUpdateAt: new Date().toISOString(),
            loading: false,
            pointInTime: false,
          };
        });
        const minimumRefreshMs = ["1d", "1w", "1mo", "1y"].includes(timeframe) ? 15_000 : timeframeDurationMs(timeframe) >= 300_000 ? 5_000 : 1_000;
        timer = window.setTimeout(refresh, Math.max(minimumRefreshMs, payload.stream_interval_ms ?? 1_000));
      } catch (reason) {
        if (!cancelled && !request.signal.aborted) {
          setState((current) => ({ ...current, connected: false, error: reason instanceof Error ? reason.message : String(reason), historyNotice: "Live tail is stale; the chart is retaining the last complete historical/live snapshot while reconnecting." }));
          timer = window.setTimeout(refresh, 5_000);
        }
      } finally {
        if (controller === request) controller = null;
      }
    };
    void refresh();
    const resume = () => {
      if (document.visibilityState !== "visible" || cancelled || controller) return;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      void refresh();
    };
    document.addEventListener("visibilitychange", resume);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [enabled, indicatorColumns, liveTail, rowBudget, symbol, timeframe]);

  const currentRequestKey = `${symbol.trim().toUpperCase()}:${timeframe}:${indicatorColumns}`;
  return { ...state, loadEarlier, ready: enabled && readyKey === currentRequestKey };
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
  };
  return pageSizes[timeframe] ?? 240;
}

export function chartInitialPageSize(timeframe: string) {
  const pageSizes: Record<string, number> = {
    "100ms": 600,
    "1s": 300,
    "5s": 120,
    "10s": 90,
    "30s": 40,
    "1m": 20,
    "5m": 12,
    "1h": 4,
  };
  return pageSizes[timeframe] ?? chartPageSize(timeframe);
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
): { key: string; params: Record<string, string | number | undefined> } | null {
  if (!cursor || (!cursor.nextBefore && !cursor.previousSessionBefore)) return null;
  const params = cursor.nextBefore
    ? { as_of: cursor.asOf, before_bar: cursor.nextBefore, indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), session_date: cursor.sessionDate, symbol: ticker, timeframe }
    : { before: cursor.previousSessionBefore, indicator_columns: indicatorColumns, row_limit: chartPageSize(timeframe), symbol: ticker, timeframe };
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

export function mergeHistoricalChartPage(
  currentBars: QmdLiveBar[],
  currentIndicators: HistoricalIndicator[],
  incomingBars: QmdLiveBar[],
  incomingIndicators: HistoricalIndicator[],
  rowBudget: number,
) {
  const existingTimes = new Set(currentBars.map(barStartTime));
  const availableSlots = Math.max(0, rowBudget - currentBars.length);
  const newBars = incomingBars.filter((row) => !existingTimes.has(barStartTime(row)));
  const admittedBars = availableSlots < newBars.length ? newBars.slice(newBars.length - availableSlots) : newBars;
  const bars = limitRowsToLatest(mergeRowsByTime(currentBars, admittedBars), rowBudget);
  const admittedTimes = new Set(bars.map(barStartTime));
  const indicators = limitRowsToLatest(
    mergeRowsByTime(currentIndicators, incomingIndicators.filter((row) => admittedTimes.has(barStartTime(row)))),
    rowBudget,
  ).filter((row) => admittedTimes.has(barStartTime(row)));
  return { atCapacity: bars.length >= rowBudget, bars, indicators };
}

export function limitRowsToLatest<T>(rows: T[], rowBudget: number): T[] {
  return rows.length <= rowBudget ? rows : rows.slice(rows.length - rowBudget);
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

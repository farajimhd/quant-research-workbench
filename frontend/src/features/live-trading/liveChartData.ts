import { api, query } from "../../api/client";
import type { ChartPayload } from "../../app/components/ChartPanel";
import type { RecordRow } from "./contracts";

export const LIVE_FEATURE_GROUPS = ["core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure"];

export function availableSessionDates(records: RecordRow[]) {
  return Array.from(new Set(records.filter((record) => record.exists && record.group === "bars" && record.timeframe === "1m").map((record) => record.session_date))).sort();
}

export function loadChart(processedRoot: string, startDate: string, endDate: string, timeframe: string, ticker: string, displayItems: string[]) {
  return api<ChartPayload>(
    `/api/market-data/chart${query({
      processed_root: processedRoot,
      start_date: startDate,
      end_date: endDate,
      timeframe,
      ticker,
      feature_groups: LIVE_FEATURE_GROUPS.join(","),
      display_items: displayItems.join(","),
      min_confidence: 0.4,
    })}`
  );
}

export function openOnlyChartPayload(payload: ChartPayload | null, cutoffTime: number | null, currentOpen: number): ChartPayload | null {
  return castOpenChartPayload(payload, cutoffTime, currentOpen);
}

export function castOpenChartPayload(payload: ChartPayload | null, cutoffTime: number | null, currentOpen: number): ChartPayload | null {
  if (!payload || !cutoffTime) return payload;
  const priorCandles = payload.candles.filter((candle) => candle.time < cutoffTime);
  const open = currentOpen || priorCandles.at(-1)?.close || 0;
  const currentCandle = open > 0 ? [{ time: cutoffTime, open, high: open, low: open, close: open }] : [];
  const trimmed = trimChartPayload(payload, cutoffTime) ?? payload;
  return {
    ...trimmed,
    candles: [...priorCandles, ...currentCandle],
    markers: payload.markers.filter((marker) => Number(marker.time) < cutoffTime),
    volume: [...payload.volume.filter((point) => Number(point.time) < cutoffTime), { color: "rgba(37, 99, 235, 0.25)", time: cutoffTime, value: 0 }],
  };
}

export function trimChartPayload(payload: ChartPayload | null, cutoffTime: number | null): ChartPayload | null {
  if (!payload || !cutoffTime) return payload;
  return {
    ...payload,
    candles: payload.candles.filter((candle) => candle.time < cutoffTime),
    markers: payload.markers.filter((marker) => Number(marker.time) < cutoffTime),
    oscillator_series: payload.oscillator_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < cutoffTime) })),
    overlay_series: payload.overlay_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < cutoffTime) })),
    price_zones: (payload.price_zones ?? []).filter((zone) => zone.start < cutoffTime).map((zone) => ({ ...zone, end: Math.min(zone.end, cutoffTime) })),
    regions: payload.regions.filter((region) => region.start < cutoffTime).map((region) => ({ ...region, end: Math.min(region.end, cutoffTime) })),
    trade_annotations: [],
    volume: payload.volume.filter((point) => Number(point.time) < cutoffTime),
  };
}

export function dayOpenOnlyChartPayload(payload: ChartPayload | null, sessionDate: string, currentOpen: number, cutoffTime: number | null): ChartPayload | null {
  if (!payload || !sessionDate) return payload;
  const dayStart = Date.parse(`${sessionDate}T00:00:00-04:00`);
  const sessionDayTime = Number.isFinite(dayStart) ? Math.floor(dayStart / 1000) : cutoffTime;
  if (!sessionDayTime || !cutoffTime) return payload;
  const priorCandles = payload.candles.filter((candle) => candle.time < sessionDayTime).slice(-60);
  const priorOscillators = payload.oscillator_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < sessionDayTime).slice(-60) }));
  const priorOverlays = payload.overlay_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < sessionDayTime).slice(-60) }));
  if (!currentOpen) {
    return {
      ...payload,
      candles: priorCandles,
      markers: [],
      oscillator_series: priorOscillators,
      overlay_series: priorOverlays,
      price_zones: [],
      regions: [],
      trade_annotations: [],
      volume: [],
    };
  }
  return {
    ...payload,
    candles: [...priorCandles, { time: cutoffTime, open: currentOpen, high: currentOpen, low: currentOpen, close: currentOpen }],
    markers: [],
    oscillator_series: priorOscillators,
    overlay_series: priorOverlays,
    price_zones: [],
    regions: [],
    trade_annotations: [],
    volume: [],
  };
}

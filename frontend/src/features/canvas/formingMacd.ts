import { api, query } from '../../api/client';
import type { QmdBarHistory } from './contracts';

export const MACD_TIMEFRAMES = ['100ms', '1s', '5s', '10s', '30s', '1m', '5m', '1h', '1d', '1w', '1mo', '1y'] as const;
export type MacdTimeframe = typeof MACD_TIMEFRAMES[number];
export const MACD_DIFFERENCE_ID = 'indicator.forming_macd_difference';
export const MACD_DIFFERENCE_PANE = 'forming-macd-difference';
const durations: Partial<Record<MacdTimeframe, number>> = { '100ms': .1, '1s': 1, '5s': 5, '10s': 10, '30s': 30, '1m': 60, '5m': 300, '1h': 3600 };
export const macdDuration = (timeframe: MacdTimeframe) => durations[timeframe];
export type MacdBase = { start: number; end: number; close: number; line: number; signal: number };
export type MacdSample = { time: number; endTime?: number; isClosed?: boolean; close: number };
export type MacdSource = { rows: MacdBase[]; through: number };

/** Recover QMD's EMA state instead of reseeding it at the chart's left edge.
 * Two consecutive returned closes suffice because MACD = EMA12 - EMA26.
 * Preview updates always start from the completed state, never another preview.
 */
export function projectFormingMacd(samples: MacdSample[], source: MacdSource, chartSeconds: number | null, asOf: number) {
  const points: Array<{ time: number; value: number }> = [];
  let index = -1, missing = 0;
  for (const sample of samples) {
    if (sample.time > asOf) continue;
    const end = sample.endTime ?? (chartSeconds === null ? NaN : sample.time + chartSeconds);
    // A completed candle's final close must never be borrowed before its end.
    if (sample.isClosed !== false && end > asOf) continue;
    const at = sample.isClosed === false ? Math.min(end, asOf) : end;
    while (index + 1 < source.rows.length && source.rows[index + 1].end <= at) index++;
    const base = source.rows[index], previous = source.rows[index - 1];
    let value = NaN;
    if (Number.isFinite(at) && at <= source.through && base && Number.isFinite(sample.close) && sample.close > 0) {
      if (Math.abs(base.end - at) < 1e-6) value = base.line - base.signal;
      else if (previous && [previous.line, base.line, base.signal, base.close].every(Number.isFinite)) {
        const af = 2 / 13, slowAlpha = 2 / 27;
        const previousSlow = base.close - (base.line - (1 - af) * previous.line) / (af - slowAlpha);
        const slow = slowAlpha * base.close + (1 - slowAlpha) * previousSlow;
        const line = af * sample.close + (1 - af) * (slow + base.line) - (slowAlpha * sample.close + (1 - slowAlpha) * slow);
        value = line - (.2 * line + .8 * base.signal);
      }
    }
    if (!Number.isFinite(value)) missing++;
    points.push({ time: sample.time, value });
  }
  return { points, missing };
}

/** Bounded, sequential canonical pages, including two seed rows before the chart.
 * Never use chart OHLC to invent a lower-timeframe close history.
 */
export async function loadMacdSource(symbol: string, timeframe: MacdTimeframe, firstSample: number, asOf: number, signal: AbortSignal): Promise<MacdSource> {
  const duration = macdDuration(timeframe);
  if (!duration) throw Error(`${timeframe}: QMD does not provide MACD for this timeframe yet.`);
  const rows = new Map<number, MacdBase>();
  let params: Record<string, string | number | boolean> = {
    symbol, timeframe, as_of: new Date(asOf * 1000).toISOString(),
    session_date: new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(asOf * 1000)),
  };
  const cursors = new Set<string>();
  for (let pageNumber = 0; pageNumber < 20; pageNumber++) {
    const page = await api<QmdBarHistory>(`/api/trading/canvas-chart/history${query({ ...params,
      mode: 'replay', stage: 'bars', row_limit: 10000, indicator_columns: 'bar_start,macd_line,macd_signal',
      include_structure: false, include_market_signals: false,
    })}`, { signal, timeoutMs: 120000 });
    if (page.indicator_provenance?.complete === false || page.indicator_provenance?.stale_reason) throw Error(`${timeframe}: MACD source is incomplete or stale.`);
    const indicators = new Map((page.indicators ?? []).map(row => [Date.parse(row.bar_start) / 1000, row]));
    for (const bar of page.history ?? []) {
      const start = Date.parse(bar.bar_start) / 1000;
      const end = bar.bar_end ? Date.parse(bar.bar_end) / 1000 : start + duration;
      if (bar.is_closed === false || end > asOf) continue;
      const indicator = indicators.get(start);
      const line = indicator?.macd_line, macdSignal = indicator?.macd_signal;
      // Missing rows must not be skipped: otherwise EMA recovery would silently
      // treat nonconsecutive MACD observations as consecutive source candles.
      if (typeof line !== 'number' || typeof macdSignal !== 'number' || ![start, end, bar.close, line, macdSignal].every(Number.isFinite) || end <= start || bar.close <= 0) {
        throw Error(`${timeframe}: authoritative MACD is missing for ${bar.bar_start}.`);
      }
      const next = { start, end, close: bar.close, line, signal: macdSignal };
      const prior = rows.get(start);
      if (prior && JSON.stringify(prior) !== JSON.stringify(next)) throw Error(`${timeframe}: conflicting MACD source pages at ${bar.bar_start}.`);
      rows.set(start, next);
    }
    const sorted = [...rows.values()].sort((a, b) => a.start - b.start);
    if (sorted.filter(row => row.end <= firstSample).length >= 2 || !page.has_more) {
      if (!sorted.length) throw Error(`${timeframe}: no completed MACD history is available.`);
      // Before the next source boundary no additional source candle can close.
      // Chart-price updates may therefore preview this same completed state.
      return { rows: sorted, through: Math.max(asOf, (Math.floor(asOf / duration) + 1) * duration - 1e-6) };
    }
    const cursor = `${page.next_before}|${page.previous_session_before}|${page.earliest_session_date}`;
    if (cursors.has(cursor) || (!page.next_before && !page.previous_session_before)) throw Error(`${timeframe}: MACD history pagination did not advance.`);
    cursors.add(cursor);
    params = page.next_before
      ? { symbol, timeframe, as_of: new Date(asOf * 1000).toISOString(), session_date: page.earliest_session_date, before_bar: page.next_before }
      : { symbol, timeframe, before: page.previous_session_before };
  }
  throw Error(`${timeframe}: the requested chart span exceeds 200,000 source candles; narrow the chart span or choose a larger MACD timeframe.`);
}

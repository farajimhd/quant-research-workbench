import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { dateInTimeZone } from "../timeZones";
import { ChartPanel, type ChartPayload } from "./ChartPanel";

type Bar = { bar_start: string; bar_end: string; open: number; high: number;
  low: number; close: number; volume: number };
type Indicator = { bar_start: string; macd_line?: number; macd_signal?: number;
  macd_histogram?: number };
type ChartPage = { bars: Bar[]; indicators: Indicator[]; has_more: boolean;
  next_before: string; session_date: string; ticker: string; timeframe: string;
  verified_boundary_ms: number; indicator_provenance: { unavailable_columns: string[] } };
const FRAMES = ["100ms", "1s", "5s", "10s", "30s"] as const;
const MACD = ["macd_line", "macd_signal", "macd_histogram"] as const;
const pageCache = new Map<string, Promise<ChartPage>>();

function loadPage(path: string): Promise<ChartPage> {
  const cached = pageCache.get(path);
  if (cached) return cached;
  const pending = api<ChartPage>(path, { timeoutMs: 60_000 }).catch(error => {
    pageCache.delete(path);
    throw error;
  });
  if (pageCache.size >= 32) pageCache.delete(pageCache.keys().next().value!);
  pageCache.set(path, pending);
  return pending;
}

function pageBoundary(page: ChartPage): number | null {
  if (!page.has_more || !page.next_before) return null;
  const origin = dateInTimeZone(page.session_date, "04:00", "America/New_York").getTime();
  const boundary = Date.parse(page.next_before) - origin;
  return Number.isFinite(boundary) && boundary > 0 ? boundary : null;
}

export function BacktestV4SavedChart({ runId, ticker, onClose }: {
  runId: string; ticker: string; onClose: () => void;
}) {
  const [symbol, setSymbol] = useState(ticker);
  const [draftSymbol, setDraftSymbol] = useState(ticker);
  const [frame, setFrame] = useState<(typeof FRAMES)[number]>("1s");
  const [showMacd, setShowMacd] = useState(true);
  const [page, setPage] = useState<ChartPage | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [before, setBefore] = useState<number | null>(null);

  useEffect(() => {
    setSymbol(ticker);
    setDraftSymbol(ticker);
    setBefore(null);
    setPage(null);
    setBars([]);
    setIndicators([]);
  }, [runId, ticker]);

  function changeScope(next: { symbol?: string; frame?: (typeof FRAMES)[number]; macd?: boolean }) {
    if (next.symbol !== undefined) setSymbol(next.symbol);
    if (next.frame !== undefined) setFrame(next.frame);
    if (next.macd !== undefined) setShowMacd(next.macd);
    setBefore(null);
    setPage(null);
    setBars([]);
    setIndicators([]);
  }

  function submitTicker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = draftSymbol.trim().toUpperCase();
    if (!/^[A-Z0-9.-]{1,24}$/.test(normalized)) {
      setError("Enter a valid ticker.");
      return;
    }
    if (normalized !== symbol) changeScope({ symbol: normalized });
  }

  useEffect(() => {
    const normalized = symbol.trim().toUpperCase();
    if (!normalized || !/^[A-Z0-9.-]{1,24}$/.test(normalized)) {
      setError("Enter a valid ticker.");
      return;
    }
    let cancelled = false;
    const params = new URLSearchParams({ ticker: normalized, timeframe: frame,
      row_limit: "1000" });
    if (before !== null) params.set("before_boundary_ms", String(before));
    // The saved-chart API accepts one comma-separated projection parameter.
    // Repeated keys would request only the last MACD column.
    if (showMacd) params.set("indicator_columns", MACD.join(","));
    setLoading(true);
    setError("");
    void loadPage(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-chart?${params}`).then(value => {
      if (cancelled) return;
      setPage(value);
      setBars(current => before === null ? value.bars : [...value.bars, ...current]);
      setIndicators(current => before === null ? value.indicators : [...value.indicators, ...current]);
    }).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [runId, ticker, symbol, frame, showMacd, before]);

  const payload = useMemo<ChartPayload>(() => {
    const series = (column: (typeof MACD)[number], label: string, color: string) => ({
      column, label, color, style: "line" as const, lineWidth: 1,
      paneKey: "macd", data: indicators.filter(row => typeof row[column] === "number")
        .map(row => ({ time: Date.parse(row.bar_start) / 1000, value: row[column]! })),
    });
    return { timeframe: frame, candles: bars.map(bar => ({
      time: Date.parse(bar.bar_start) / 1000,
      endTime: Date.parse(bar.bar_end) / 1000, isClosed: true,
      open: bar.open, high: bar.high, low: bar.low, close: bar.close,
    })), volume: bars.map(bar => ({ time: Date.parse(bar.bar_start) / 1000,
      value: bar.volume, color: bar.close >= bar.open ? "var(--success)" : "var(--danger)" })),
      overlay_series: [], oscillator_series: showMacd ? [
        series("macd_line", "MACD", "var(--primary)"),
        series("macd_signal", "Signal", "var(--warning)"),
        series("macd_histogram", "Histogram", "var(--info)"),
      ] : [], markers: [], regions: [],
    };
  }, [bars, indicators, frame, showMacd]);

  const older = page && pageBoundary(page);
  return <section className="backtest-v4-saved-chart" aria-label={`Saved ${symbol} chart`}>
    <header><h4>Persisted market chart</h4><button className="button secondary compact" type="button" onClick={onClose}>Close chart</button></header>
    <div className="backtest-v4-chart-controls">
      <form onSubmit={submitTicker}><label>Ticker <input aria-label="Chart ticker" value={draftSymbol} onChange={event => setDraftSymbol(event.target.value.toUpperCase())} maxLength={24} /></label><button className="button secondary compact" type="submit">Show</button></form>
      <label>Resolution <select aria-label="Chart resolution" value={frame} onChange={event => changeScope({ frame: event.target.value as (typeof FRAMES)[number] })}>{FRAMES.map(value => <option key={value}>{value}</option>)}</select></label>
      <label><input type="checkbox" checked={showMacd} onChange={event => changeScope({ macd: event.target.checked })} /> Closed MACD</label>
    </div>
    <p className="backtest-v4-chart-source">ARTE closed bars and indicators · {page ? `verified through ${page.verified_boundary_ms.toLocaleString()} ms from 04:00 ET` : "verifying saved run…"}</p>
    {page?.indicator_provenance.unavailable_columns.length ? <p role="note">Stale indicators: {page.indicator_provenance.unavailable_columns.join(", ")}</p> : null}
    {error ? <p role="alert">Chart unavailable: {error}</p> : null}
    <ChartPanel persistedOnly payload={payload} ticker={symbol} timeframe={frame} timeframes={[...FRAMES]}
      featureOptions={[]} indicatorOptions={[]} visibleColumns={showMacd ? [...MACD] : []} onVisibleColumnsChange={() => {}}
      onTickerChange={value => changeScope({ symbol: value })} onTimeframeChange={value => changeScope({ frame: value as (typeof FRAMES)[number] })}
      emptyMessage="No price-bearing bars in this verified run window." loading={loading && !page}
      showIndicatorControls={false} tickerEditable={false} enableFullscreen={false} baseHeight={320} />
    {older ? <button className="button secondary compact" type="button" disabled={loading} onClick={() => setBefore(older)}>{loading ? "Loading…" : "Load earlier bars"}</button> : null}
  </section>;
}

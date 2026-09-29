import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../../api/client";
import { dateInTimeZone } from "../timeZones";
import { ChartPanel, type ChartPayload, type ChartDisplayItem } from "./ChartPanel";

type Bar = { bar_start: string; bar_end: string; open: number; high: number;
  low: number; close: number; volume: number; is_closed?: boolean };
type Indicator = { bar_start: string; [column: string]: string | number | undefined };
export type ChartPage = { bars: Bar[]; indicators: Indicator[]; has_more: boolean;
  structural_levels?: Array<{ level_id: string; role: string;
    lower: number; upper: number; start: number; end: number }>;
  structural_provenance?: { authority: string; available: boolean; reason: string };
  next_before: string; session_date: string; ticker: string; timeframe: string;
  verified_boundary_ms: number; indicator_provenance: { unavailable_columns: string[] };
  history_limited?: boolean; history_first_session?: string;
  quote?: { bid: number; ask: number; bid_size: number; ask_size: number;
    quote_timestamp_us: number; age_ms: number; fresh: boolean } | null };
export const SAVED_CHART_FRAMES = ["100ms", "1s", "5s", "10s", "30s"] as const;
const FRAMES = [...SAVED_CHART_FRAMES, "1d", "1mo"] as const;
const MACD = ["macd_line", "macd_signal", "macd_histogram"] as const;
const EMA = ["ema_7", "ema_9", "ema_12", "ema_15", "ema_20", "ema_26", "ema_50"] as const;
const INDICATOR_DISPLAY: ChartDisplayItem[] = [
  { id: "saved.closed_macd", title: "Closed MACD", category: "Indicators", sourceColumns: [...MACD] },
  ...EMA.map(column => ({ id: `saved.${column}`, title: `EMA ${column.slice(4)}`, category: "Indicators", sourceColumns: [column] })),
  { id: "saved.rsi_14", title: "RSI 14", category: "Indicators", sourceColumns: ["rsi_14"] },
  { id: "saved.atr_14", title: "ATR 14", category: "Indicators", sourceColumns: ["atr_14"] },
  { id: "saved.execution_vwap", title: "VWAP · execution", category: "Indicators", sourceColumns: ["execution_vwap"] },
  { id: "saved.structural_v7", title: "V7 structural bands · provisional", category: "Indicators", sourceColumns: [] },
];
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

export function BacktestV4SavedChart({ runId, ticker, onClose, embedded = false, initialFrame = "1s", onQuoteChange, toolbarAction, panelLabel, enabled = true, allowedFrames = SAVED_CHART_FRAMES, initialShowMacd = true, prefetchedPage, tradeAnnotations = [], tradeError = "" }: {
  runId: string; ticker: string; onClose?: () => void; embedded?: boolean;
  initialFrame?: (typeof FRAMES)[number]; onQuoteChange?: (quote: ChartPage["quote"]) => void; toolbarAction?: ReactNode; panelLabel?: string; enabled?: boolean;
  allowedFrames?: readonly (typeof FRAMES)[number][]; initialShowMacd?: boolean;
  prefetchedPage?: ChartPage;
  tradeAnnotations?: NonNullable<ChartPayload["trade_annotations"]>;
  tradeError?: string;
}) {
  const [symbol, setSymbol] = useState(ticker);
  const [draftSymbol, setDraftSymbol] = useState(ticker);
  const [frame, setFrame] = useState<(typeof FRAMES)[number]>(initialFrame);
  const [showMacd, setShowMacd] = useState(initialShowMacd);
  const [selectedIndicators, setSelectedIndicators] = useState<string[]>(initialShowMacd ? ["saved.closed_macd"] : []);
  const [structureLoading, setStructureLoading] = useState(false);
  const [structureReason, setStructureReason] = useState("");
  const [page, setPage] = useState<ChartPage | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [levels, setLevels] = useState<NonNullable<ChartPage["structural_levels"]>>([]);
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
    setLevels([]);
  }, [runId, ticker]);

  const canUsePrefetch = Boolean(prefetchedPage && symbol === ticker && frame === initialFrame
    && before === null && selectedIndicators.length === 1 && selectedIndicators[0] === "saved.closed_macd");
  useEffect(() => {
    if (!prefetchedPage || !canUsePrefetch) return;
    setPage(prefetchedPage);
    setBars(prefetchedPage.bars);
    setIndicators(prefetchedPage.indicators);
    setLevels(prefetchedPage.structural_levels ?? []);
  }, [prefetchedPage, canUsePrefetch]);

  useEffect(() => { onQuoteChange?.(page?.quote); }, [onQuoteChange, page?.quote]);

  function changeScope(next: { symbol?: string; frame?: (typeof FRAMES)[number]; macd?: boolean }) {
    if (next.symbol !== undefined) setSymbol(next.symbol);
    if (next.frame !== undefined) setFrame(next.frame);
    if (next.macd !== undefined) { setShowMacd(next.macd); setSelectedIndicators(current => next.macd
      ? [...new Set([...current, "saved.closed_macd"])] : current.filter(value => value !== "saved.closed_macd")); }
    setBefore(null);
    setPage(null);
    setBars([]);
    setIndicators([]);
    setLevels([]);
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
    if (!enabled || canUsePrefetch) return;
    const normalized = symbol.trim().toUpperCase();
    if (!normalized || !/^[A-Z0-9.-]{1,24}$/.test(normalized)) {
      setError("Enter a valid ticker.");
      return;
    }
    let cancelled = false;
    const params = new URLSearchParams({ ticker: normalized, timeframe: frame,
      row_limit: "1000" });
    if (before !== null) params.set("before_boundary_ms", String(before));
    // One projected, comma-separated column set; never derive indicators locally.
    const columns = new Set(INDICATOR_DISPLAY.filter(item => selectedIndicators.includes(item.id))
      .flatMap(item => item.sourceColumns));
    if (columns.size && frame !== "1d" && frame !== "1mo") params.set("indicator_columns", [...columns].join(","));
    const wantsStructure = selectedIndicators.includes("saved.structural_v7");
    setLoading(true);
    setStructureLoading(wantsStructure);
    setStructureReason("");
    setError("");
    void loadPage(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-chart?${params}`).then(value => {
      if (cancelled) return;
      setPage(value);
      setBars(current => before === null ? value.bars : [...value.bars, ...current]);
      setIndicators(current => before === null ? value.indicators : [...value.indicators, ...current]);
      if (wantsStructure) {
        // The V7 certificate can be substantially slower cold than the bar
        // projection. Keep the chart interactive while its overlay verifies.
        const structureParams = new URLSearchParams(params);
        structureParams.set("include_structure", "true");
        void loadPage(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-chart?${structureParams}`).then(structurePage => {
          if (cancelled) return;
          setLevels(current => before === null ? structurePage.structural_levels ?? []
            : [...(structurePage.structural_levels ?? []), ...current]);
          setStructureReason(structurePage.structural_provenance?.reason ?? "");
        }).catch(reason => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
        }).finally(() => { if (!cancelled) setStructureLoading(false); });
      } else { setLevels([]); setStructureReason(""); }
    }).catch(reason => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : String(reason));
        setStructureLoading(false);
      }
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [enabled, canUsePrefetch, runId, ticker, symbol, frame, selectedIndicators, before]);

  const payload = useMemo<ChartPayload>(() => {
    const series = (column: string, label: string, color: string, displayItemId = "saved.closed_macd", paneKey = "macd") => ({
      column, displayItemId, label, color,
      style: column === "macd_histogram" ? "histogram" as const : "line" as const, lineWidth: 1,
      paneKey, data: indicators.filter(row => typeof row[column] === "number")
        .map(row => ({ time: Date.parse(row.bar_start) / 1000, value: Number(row[column]) })),
    });
    return { timeframe: frame, candles: bars.map(bar => ({
      time: Date.parse(bar.bar_start) / 1000,
      endTime: Date.parse(bar.bar_end) / 1000, isClosed: bar.is_closed !== false,
      open: bar.open, high: bar.high, low: bar.low, close: bar.close,
    })), volume: bars.map(bar => ({ time: Date.parse(bar.bar_start) / 1000,
      value: bar.volume, color: bar.close >= bar.open ? "var(--success)" : "var(--danger)" })),
      overlay_series: [...EMA.filter(column => selectedIndicators.includes(`saved.${column}`))
        .map(column => series(column, `EMA ${column.slice(4)}`, "var(--info)", `saved.${column}`, "price")),
        ...(selectedIndicators.includes("saved.execution_vwap")
          ? [series("execution_vwap", "VWAP", "var(--warning)", "saved.execution_vwap", "price")] : [])],
      oscillator_series: [...(showMacd ? [
        series("macd_line", "MACD", "var(--primary)"),
        series("macd_signal", "Signal", "var(--warning)"),
        series("macd_histogram", "Histogram", "var(--info)"),
      ] : []), ...(selectedIndicators.includes("saved.rsi_14") ? [series("rsi_14", "RSI 14", "var(--info)", "saved.rsi_14", "rsi")] : []),
      ...(selectedIndicators.includes("saved.atr_14") ? [series("atr_14", "ATR 14", "var(--warning)", "saved.atr_14", "atr")] : [])],
      price_zones: selectedIndicators.includes("saved.structural_v7")
        ? levels.map(level => ({
          annotationKind: "unified-structure-level" as const,
          displayItemId: "saved.structural_v7", label: level.role === "support" ? "V7 S" : level.role === "resistance" ? "V7 R" : "V7 T",
          color: level.role === "support" ? "var(--success)" : level.role === "resistance" ? "var(--danger)" : "var(--muted-foreground)",
          lower: level.lower, upper: level.upper,
          // The compact interval product stores lower/upper geometry. Its
          // midpoint is the available band reference, not a new V7 fit.
          levelPrice: (level.lower + level.upper) / 2,
          start: level.start, end: level.end, renderMode: "zone" as const,
          fillOpacity: 0.08,
        })) : [],
      markers: [], regions: [], trade_annotations: tradeAnnotations,
    };
  }, [bars, indicators, levels, frame, showMacd, selectedIndicators, tradeAnnotations]);

  const older = page && pageBoundary(page);
  const compactContext = embedded && (frame === "1d" || frame === "1mo");
  return <section className="backtest-v4-saved-chart" aria-label={`Saved ${symbol} chart`}>
    {panelLabel ? <span className="backtest-v4-panel-label" title={compactContext && page
      ? `Certified ARTE history from ${page.history_first_session}; daily/monthly indicators are not persisted.`
      : undefined}>{panelLabel}{compactContext ? " · indicators stale" : ""}</span> : null}
    {!embedded ? <header><h4>Persisted market chart</h4>{onClose ? <button className="button secondary compact" type="button" onClick={onClose}>Close chart</button> : null}</header> : null}
    {!embedded ? <div className="backtest-v4-chart-controls">
      <form onSubmit={submitTicker}><label>Ticker <input aria-label="Chart ticker" value={draftSymbol} onChange={event => setDraftSymbol(event.target.value.toUpperCase())} maxLength={24} /></label><button className="button secondary compact" type="submit">Show</button></form>
      <label>Resolution <select aria-label="Chart resolution" value={frame} onChange={event => changeScope({ frame: event.target.value as (typeof FRAMES)[number] })}>{FRAMES.map(value => <option key={value}>{value}</option>)}</select></label>
      {frame !== "1d" && frame !== "1mo" ? <label><input type="checkbox" checked={showMacd} onChange={event => changeScope({ macd: event.target.checked })} /> Closed MACD</label> : null}
    </div> : null}
    {!embedded ? <p className="backtest-v4-chart-source">ARTE closed bars and indicators · {page ? `verified through ${page.verified_boundary_ms.toLocaleString()} ms from 04:00 ET` : "verifying saved run…"}</p> : null}
    {page ? <div className="backtest-v4-quote" aria-label={`${symbol} saved bid and ask`}>
      {page.quote ? <><span><small>Bid</small><strong>{page.quote.bid.toFixed(4)}</strong><em>{page.quote.bid_size.toLocaleString()} shares</em></span><span><small>Ask</small><strong>{page.quote.ask.toFixed(4)}</strong><em>{page.quote.ask_size.toLocaleString()} shares</em></span><span><small>Quote at saved boundary</small><strong>{page.quote.fresh ? "Fresh" : "Stale"}</strong><em>{page.quote.age_ms.toLocaleString()} ms old · pinned liquidity</em></span></>
        : <span><small>Quote at saved boundary</small><strong>Unavailable</strong><em>No certified quote in this window</em></span>}
    </div> : null}
    {!compactContext && page?.indicator_provenance.unavailable_columns.length ? <p role="note">Stale indicators: {page.indicator_provenance.unavailable_columns.join(", ")}</p> : null}
    {structureLoading ? <p role="status">Verifying V7 structural levels…</p> : null}
    {selectedIndicators.includes("saved.structural_v7") && structureReason
      ? <p role="note">V7 structure unavailable: {structureReason}</p> : null}
    {!compactContext && page?.history_limited ? <p role="note">ARTE history available from {page.history_first_session}; earlier {frame === "1mo" ? "months" : "sessions"} are unavailable.</p> : null}
    {error ? <p role="alert">Chart unavailable: {error}</p> : null}
    <ChartPanel persistedOnly payload={payload} ticker={symbol} timeframe={frame} timeframes={[...allowedFrames]}
      featureOptions={[]} indicatorOptions={[]} displayItemOptions={frame === "1d" || frame === "1mo" ? [] : INDICATOR_DISPLAY}
      visibleColumns={selectedIndicators} onVisibleColumnsChange={values => {
        setSelectedIndicators(values); setShowMacd(values.includes("saved.closed_macd"));
        setBefore(null); setPage(null); setBars([]); setIndicators([]); setLevels([]);
      }}
      onTickerChange={value => changeScope({ symbol: value })} onTimeframeChange={value => changeScope({ frame: value as (typeof FRAMES)[number] })}
      emptyMessage="No price-bearing bars in this verified run window." loading={loading && !page}
      showIndicatorControls={frame !== "1d" && frame !== "1mo"} strategyPresentationEnabled={Boolean(toolbarAction) || tradeAnnotations.length > 0}
      dataStatus={tradeError || undefined}
      tickerEditable={false} enableFullscreen={false} baseHeight={320} fillHeight={embedded} toolbarVariant={embedded ? "compact" : "full"}
      toolbarActions={toolbarAction} canLoadEarlier={Boolean(older)} loadingEarlier={loading && before !== null}
      onLoadEarlier={() => { if (older) setBefore(older); }} />
  </section>;
}

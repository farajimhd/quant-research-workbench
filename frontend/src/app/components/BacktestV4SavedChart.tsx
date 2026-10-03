import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../../api/client";
import { dateInTimeZone } from "../timeZones";
import { ChartPanel, type ChartPayload, type ChartDisplayItem } from "./ChartPanel";

type Bar = { bar_start: string; bar_end: string; open: number; high: number;
  low: number; close: number; volume: number; is_closed?: boolean };
type Indicator = { bar_start: string; [column: string]: string | number | undefined };
export type ChartPage = { bars: Bar[]; indicators: Indicator[]; has_more: boolean;
  structural_levels?: Array<{ level_id: string; role: string;
    historical: boolean; lower: number; upper: number; start: number; end: number }>;
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
type OverlayPage = { schema_version: string; run_id: string; ticker: string; timeframe: string;
  indicators: Indicator[]; structural_levels: NonNullable<ChartPage["structural_levels"]>;
  structural_provenance: { reason: string }; bucket_indices: number[] };
const overlayCache = new Map<string, Promise<OverlayPage>>();
const INDICATOR_SELECTION_KEY = "backtest-v4-saved-chart.indicators-v2";
const LEGACY_INDICATOR_SELECTION_KEY = "backtest-v4-saved-chart.indicators-v1";
const V7_STYLE_KEY = "backtest-v4-saved-chart.v7-style-v1";
type V7Role = "resistance" | "support" | "transition";
type V7Style = { source: "both" | "historical" | "streaming";
  roles: Record<V7Role, { line: { visible: boolean; color: string; opacity: number };
    band: { visible: boolean; color: string; opacity: number } }> };
const DEFAULT_V7_STYLE: V7Style = { source: "both", roles: {
  resistance: { line: { visible: true, color: "#dc3545", opacity: 0.9 }, band: { visible: true, color: "#dc3545", opacity: 0.13 } },
  support: { line: { visible: true, color: "#18a957", opacity: 0.9 }, band: { visible: true, color: "#18a957", opacity: 0.13 } },
  transition: { line: { visible: true, color: "#858b96", opacity: 0.9 }, band: { visible: true, color: "#858b96", opacity: 0.13 } },
} };
function savedV7Style(): V7Style {
  try {
    const stored = JSON.parse(window.localStorage.getItem(V7_STYLE_KEY) ?? "null");
    if (!stored || typeof stored !== "object") return DEFAULT_V7_STYLE;
    const source = ["both", "historical", "streaming"].includes(stored.source) ? stored.source : "both";
    const roles = Object.fromEntries((Object.keys(DEFAULT_V7_STYLE.roles) as V7Role[]).map(role =>
      [role, Object.fromEntries((["line", "band"] as const).map(part => {
        const value = stored.roles?.[role]?.[part];
        const fallback = DEFAULT_V7_STYLE.roles[role][part];
        return [part, { visible: typeof value?.visible === "boolean" ? value.visible : fallback.visible,
          color: typeof value?.color === "string" && /^#[0-9a-fA-F]{6}$/.test(value.color) ? value.color : fallback.color,
          opacity: typeof value?.opacity === "number" && value.opacity >= 0 && value.opacity <= 1 ? value.opacity : fallback.opacity }];
      }))]));
    return { source, roles } as V7Style;
  } catch { return DEFAULT_V7_STYLE; }
}

function savedIndicatorSelection(initialShowMacd: boolean): string[] {
  if (!initialShowMacd) return [];
  const defaults = ["saved.closed_macd", "saved.execution_vwap", "saved.structural_v7"];
  try {
    const stored = JSON.parse(window.localStorage.getItem(INDICATOR_SELECTION_KEY) ?? "null");
    const valid = (values: unknown[]) => values.filter((value): value is string =>
      typeof value === "string" && INDICATOR_DISPLAY.some(item => item.id === value));
    if (Array.isArray(stored)) return [...new Set(valid(stored))];
    const legacy = JSON.parse(window.localStorage.getItem(LEGACY_INDICATOR_SELECTION_KEY) ?? "null");
    if (Array.isArray(legacy)) return [...new Set([...valid(legacy), "saved.execution_vwap", "saved.structural_v7"])];
  } catch { /* Browser preference storage may be disabled; keep safe defaults. */ }
  return defaults;
}

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

export function BacktestV4SavedChart({ runId, ticker, onClose, embedded = false, initialFrame = "1s", onQuoteChange, onMarkChange, toolbarAction, panelLabel, enabled = true, allowedFrames = SAVED_CHART_FRAMES, initialShowMacd = true, prefetchedPage, tradeAnnotations = [], tradeError = "" }: {
  runId: string; ticker: string; onClose?: () => void; embedded?: boolean;
  initialFrame?: (typeof FRAMES)[number]; onQuoteChange?: (quote: ChartPage["quote"]) => void;
  onMarkChange?: (mark: { price: number; barEnd: string } | null) => void;
  toolbarAction?: ReactNode; panelLabel?: string; enabled?: boolean;
  allowedFrames?: readonly (typeof FRAMES)[number][]; initialShowMacd?: boolean;
  prefetchedPage?: ChartPage;
  tradeAnnotations?: NonNullable<ChartPayload["trade_annotations"]>;
  tradeError?: string;
}) {
  const [symbol, setSymbol] = useState(ticker);
  const [draftSymbol, setDraftSymbol] = useState(ticker);
  const [frame, setFrame] = useState<(typeof FRAMES)[number]>(initialFrame);
  const [selectedIndicators, setSelectedIndicators] = useState<string[]>(() => savedIndicatorSelection(initialShowMacd));
  const [v7Style, setV7Style] = useState<V7Style>(savedV7Style);
  const showMacd = selectedIndicators.includes("saved.closed_macd");
  const [structureLoading, setStructureLoading] = useState(false);
  const [structureReason, setStructureReason] = useState("");
  const [overlayError, setOverlayError] = useState("");
  const [page, setPage] = useState<ChartPage | null>(null);
  const [latestPage, setLatestPage] = useState<ChartPage | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [barPages, setBarPages] = useState<Bar[][]>([]);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [levels, setLevels] = useState<NonNullable<ChartPage["structural_levels"]>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [before, setBefore] = useState<number | null>(null);

  useEffect(() => {
    if (!allowedFrames.some(value => SAVED_CHART_FRAMES.includes(value as typeof SAVED_CHART_FRAMES[number]))) return;
    try { window.localStorage.setItem(INDICATOR_SELECTION_KEY, JSON.stringify(selectedIndicators)); }
    catch { /* Keep chart usable when preference storage is unavailable. */ }
  }, [selectedIndicators, allowedFrames]);
  useEffect(() => {
    try { window.localStorage.setItem(V7_STYLE_KEY, JSON.stringify(v7Style)); }
    catch { /* Presentation remains in memory when preference storage is unavailable. */ }
  }, [v7Style]);

  const v7Editor = <div className="saved-v7-editor">
    <label>Source <select aria-label="V7 source" value={v7Style.source} onChange={event =>
      setV7Style(current => ({ ...current, source: event.target.value as V7Style["source"] }))}>
      <option value="both">Both</option><option value="historical">Prior checkpoint</option><option value="streaming">Intraday stream</option>
    </select></label>
    {(Object.keys(v7Style.roles) as V7Role[]).map(role => <fieldset key={role}>
      <legend>{role === "transition" ? "Transitioning" : role[0].toUpperCase() + role.slice(1)}</legend>
      {(["line", "band"] as const).map(part => {
        const value = v7Style.roles[role][part];
        const update = (change: Partial<typeof value>) => setV7Style(current => ({ ...current,
          roles: { ...current.roles, [role]: { ...current.roles[role], [part]: { ...current.roles[role][part], ...change } } },
        }));
        return <div className="saved-v7-editor-row" key={part}>
          <label><input type="checkbox" aria-label={`Show ${role} ${part}`} checked={value.visible}
            onChange={event => update({ visible: event.target.checked })} />{part === "line" ? "Level" : "Band"}</label>
          <input type="color" aria-label={`${role} ${part} color`} value={value.color}
            onChange={event => update({ color: event.target.value })} />
          <label>Opacity <input type="range" aria-label={`${role} ${part} opacity`} min={0} max={100}
            value={Math.round(value.opacity * 100)} onChange={event => update({ opacity: Number(event.target.value) / 100 })} />
            <output>{Math.round(value.opacity * 100)}%</output></label>
        </div>;
      })}
    </fieldset>)}
  </div>;
  const displayItems = INDICATOR_DISPLAY.map(item => item.id === "saved.structural_v7"
    ? { ...item, customEditor: v7Editor, customReset: () => setV7Style(DEFAULT_V7_STYLE) } : item);

  useEffect(() => {
    setSymbol(ticker);
    setDraftSymbol(ticker);
    setBefore(null);
    setPage(null);
    setLatestPage(null);
    setBars([]);
    setBarPages([]);
    setIndicators([]);
    setLevels([]);
  }, [runId, ticker]);

  const canUsePrefetch = Boolean(prefetchedPage && symbol === ticker && frame === initialFrame
    && before === null);
  useEffect(() => {
    if (!prefetchedPage || !canUsePrefetch) return;
    setPage(prefetchedPage);
    setLatestPage(prefetchedPage);
    setBars(prefetchedPage.bars);
    setBarPages([prefetchedPage.bars]);
    setIndicators(prefetchedPage.indicators);
    setLevels(prefetchedPage.structural_levels ?? []);
  }, [prefetchedPage, canUsePrefetch]);

  useEffect(() => { onQuoteChange?.(latestPage?.quote); }, [onQuoteChange, latestPage?.quote]);
  useEffect(() => {
    const last = latestPage?.bars.at(-1);
    onMarkChange?.(last && Number.isFinite(last.close) && last.close > 0
      ? { price: last.close, barEnd: last.bar_end } : null);
  }, [onMarkChange, latestPage]);

  function changeScope(next: { symbol?: string; frame?: (typeof FRAMES)[number]; macd?: boolean }) {
    if (next.symbol !== undefined) setSymbol(next.symbol);
    if (next.frame !== undefined) setFrame(next.frame);
    if (next.macd !== undefined) { setSelectedIndicators(current => next.macd
      ? [...new Set([...current, "saved.closed_macd"])] : current.filter(value => value !== "saved.closed_macd")); }
    if (next.symbol === undefined && next.frame === undefined) return;
    setBefore(null);
    setPage(null);
    setLatestPage(null);
    setBars([]);
    setBarPages([]);
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
    // Stable candle projection: indicator selection must never change this URL.
    if (frame !== "1d" && frame !== "1mo") params.set("indicator_columns", [...MACD, "execution_vwap"].join(","));
    setLoading(true);
    setError("");
    void loadPage(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-chart?${params}`).then(value => {
      if (cancelled) return;
      setPage(value);
      if (before === null) setLatestPage(value);
      setBars(current => before === null ? value.bars : [...value.bars, ...current]);
      setBarPages(current => before === null ? [value.bars] : [value.bars, ...current]);
      setIndicators(current => before === null ? value.indicators : [...value.indicators, ...current]);
    }).catch(reason => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [enabled, canUsePrefetch, runId, ticker, symbol, frame, before]);

  useEffect(() => {
    setStructureLoading(false);
    setOverlayError("");
    if (!enabled || !barPages.length || frame === "1d" || frame === "1mo") return;
    const columns = [...new Set(INDICATOR_DISPLAY.filter(item => selectedIndicators.includes(item.id))
      .flatMap(item => item.sourceColumns))].filter(column => column !== "execution_vwap" && !MACD.includes(column as typeof MACD[number])).sort();
    const includeStructure = selectedIndicators.includes("saved.structural_v7");
    if (!columns.length && !includeStructure) return;
    let cancelled = false;
    const resolution = { "100ms": 100, "1s": 1000, "5s": 5000, "10s": 10000, "30s": 30000 }[frame];
    if (!resolution || !page) return;
    const origin = dateInTimeZone(page.session_date, "04:00", "America/New_York").getTime();
    // A page retains its immutable bucket identity when older history is
    // prepended, so cached overlays are not re-read or re-certified.
    const chunks = barPages.flatMap(chunk => Array.from({ length: Math.ceil(chunk.length / 1000) },
      (_, index) => chunk.slice(index * 1000, (index + 1) * 1000)));
    setStructureLoading(includeStructure);
    setStructureReason("");
    setOverlayError("");
    let remainingStructures = includeStructure ? chunks.length : 0;
    const structures: OverlayPage[] = [];
    const requests = chunks.flatMap(chunk => {
      const buckets = chunk.map(bar => Math.round((Date.parse(bar.bar_start) - origin + 14_400_000) / resolution));
      return [
        ...(columns.length ? [{ buckets, columns, structure: false }] : []),
        ...(includeStructure ? [{ buckets, columns: [] as string[], structure: true }] : []),
      ];
    });
    for (const request of requests) {
      const { buckets, columns: requestedColumns, structure } = request;
      const key = JSON.stringify([runId, symbol, frame, buckets, requestedColumns, structure]);
      let pending = overlayCache.get(key);
      if (!pending) {
        pending = api<OverlayPage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-chart-overlays`, {
          method: "POST", timeoutMs: 60_000,
          body: JSON.stringify({ ticker: symbol, timeframe: frame, bucket_indices: buckets,
            indicator_columns: requestedColumns, include_structure: structure }),
        }).then(value => {
          if (value.schema_version !== "strategy-one-v4-chart-overlays-v1"
              || value.run_id !== runId || value.ticker !== symbol || value.timeframe !== frame
              || JSON.stringify(value.bucket_indices) !== JSON.stringify(buckets)) {
            throw new Error("Saved chart overlay certificate mismatch");
          }
          return value;
        }).catch(reason => { overlayCache.delete(key); throw reason; });
        if (overlayCache.size >= 32) overlayCache.delete(overlayCache.keys().next().value!);
        overlayCache.set(key, pending);
      }
      void pending.then(value => {
        if (cancelled) return;
        if (structure) {
          structures.push(value);
          setLevels(structures.flatMap(item => item.structural_levels));
          if (value.structural_provenance.reason) setStructureReason(value.structural_provenance.reason);
        } else {
          setIndicators(current => {
            const byStart = new Map(current.map(row => [row.bar_start, row]));
            for (const row of value.indicators) byStart.set(row.bar_start, { ...byStart.get(row.bar_start), ...row });
            return [...byStart.values()].sort((left, right) => left.bar_start.localeCompare(right.bar_start));
          });
        }
      }).catch(reason => {
        if (cancelled) return;
        const message = reason instanceof Error ? reason.message : String(reason);
        if (structure) setStructureReason(message);
        else setOverlayError(message);
      }).finally(() => {
        if (!cancelled && structure && --remainingStructures === 0) setStructureLoading(false);
      });
    }
    return () => { cancelled = true; };
  }, [enabled, barPages, frame, page, runId, symbol, selectedIndicators]);

  const payload = useMemo<ChartPayload>(() => {
    const series = (column: string, label: string, color: string, displayItemId = "saved.closed_macd", paneKey = "macd") => ({
      column, displayItemId, label, color,
      style: column === "macd_histogram" ? "histogram" as const : "line" as const, lineWidth: 1,
      paneKey, data: indicators.filter(row => typeof row[column] === "number")
        .map(row => ({ time: Date.parse(row.bar_start) / 1000, value: Number(row[column]) })),
    });
    const session = page?.session_date;
    const regions = session ? [
      { label: "Premarket", color: "var(--chart-premarket)",
        start: dateInTimeZone(session, "04:00", "America/New_York").getTime() / 1000,
        end: dateInTimeZone(session, "09:30", "America/New_York").getTime() / 1000 },
      { label: "After hours", color: "var(--chart-after-hours)",
        start: dateInTimeZone(session, "16:00", "America/New_York").getTime() / 1000,
        end: dateInTimeZone(session, "20:00", "America/New_York").getTime() / 1000 },
    ] : [];
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
          savedSourceVisible: v7Style.source === "both"
            || (v7Style.source === "historical" ? level.historical === true : level.historical === false),
          color: v7Style.roles[(level.role in v7Style.roles ? level.role : "transition") as V7Role].line.color,
          savedLineColor: v7Style.roles[(level.role in v7Style.roles ? level.role : "transition") as V7Role].line.color,
          savedLineOpacity: v7Style.roles[(level.role in v7Style.roles ? level.role : "transition") as V7Role].line.opacity,
          savedLineVisible: v7Style.roles[(level.role in v7Style.roles ? level.role : "transition") as V7Role].line.visible,
          savedBandColor: v7Style.roles[(level.role in v7Style.roles ? level.role : "transition") as V7Role].band.color,
          savedBandOpacity: v7Style.roles[(level.role in v7Style.roles ? level.role : "transition") as V7Role].band.opacity,
          savedBandVisible: v7Style.roles[(level.role in v7Style.roles ? level.role : "transition") as V7Role].band.visible,
          lower: level.lower, upper: level.upper,
          // The compact interval product stores lower/upper geometry. Its
          // midpoint is the available band reference, not a new V7 fit.
          levelPrice: (level.lower + level.upper) / 2,
          start: level.start, end: level.end, renderMode: "zone" as const,
          fillOpacity: 0.08,
        })) : [],
      markers: [], regions, trade_annotations: tradeAnnotations,
    };
  }, [bars, indicators, levels, frame, showMacd, selectedIndicators, v7Style, tradeAnnotations, page?.session_date]);

  const older = page && pageBoundary(page);
  useEffect(() => {
    // Saved position evidence can precede the most recent candle page. Fill
    // that gap in the background; never move or refit the user's viewport.
    if (!enabled || loading || !older || !bars.length || !tradeAnnotations.length
        || frame === "1d" || frame === "1mo") return;
    const earliest = Math.min(...tradeAnnotations.map(trade => trade.entryTime));
    if (Number.isFinite(earliest) && Date.parse(bars[0].bar_start) / 1000 > earliest - 60) {
      setBefore(older);
    }
  }, [enabled, loading, older, bars, tradeAnnotations, frame]);
  const compactContext = embedded && (frame === "1d" || frame === "1mo");
  return <section className="backtest-v4-saved-chart" aria-label={`Saved ${symbol} chart`} aria-busy={structureLoading || (loading && !page)}>
    {panelLabel ? <span className="backtest-v4-panel-label" title={compactContext && page
      ? `Certified ARTE history from ${page.history_first_session}; daily/monthly indicators are not persisted.`
      : undefined}>{panelLabel}{compactContext ? " · indicators stale" : ""}</span> : null}
    {!embedded ? <header><h4>Persisted market chart</h4>{onClose ? <button className="button secondary compact" type="button" onClick={onClose}>Close chart</button> : null}</header> : null}
    {!embedded ? <div className="backtest-v4-chart-controls">
      <form onSubmit={submitTicker}><label>Ticker <input aria-label="Chart ticker" value={draftSymbol} onChange={event => setDraftSymbol(event.target.value.toUpperCase())} maxLength={24} /></label><button className="button secondary compact" type="submit">Show</button></form>
      <label>Resolution <select aria-label="Chart resolution" value={frame} onChange={event => changeScope({ frame: event.target.value as (typeof FRAMES)[number] })}>{FRAMES.map(value => <option key={value}>{value}</option>)}</select></label>
      {frame !== "1d" && frame !== "1mo" ? <label><input type="checkbox" checked={showMacd} onChange={event => changeScope({ macd: event.target.checked })} /> Closed MACD</label> : null}
    </div> : null}
    {!embedded ? <p className="backtest-v4-chart-source">ARTE closed bars and indicators · {latestPage ? `verified through ${latestPage.verified_boundary_ms.toLocaleString()} ms from 04:00 ET` : "verifying saved run…"}</p> : null}
    {latestPage ? <div className="backtest-v4-quote" aria-label={`${symbol} saved bid and ask`}>
      {latestPage.quote ? <><span><small>Bid</small><strong>{latestPage.quote.bid.toFixed(4)}</strong><em>{latestPage.quote.bid_size.toLocaleString()} shares</em></span><span><small>Ask</small><strong>{latestPage.quote.ask.toFixed(4)}</strong><em>{latestPage.quote.ask_size.toLocaleString()} shares</em></span><span><small>Quote at saved boundary</small><strong>{latestPage.quote.fresh ? "Fresh" : "Stale"}</strong><em>{latestPage.quote.age_ms.toLocaleString()} ms old · pinned liquidity</em></span></>
        : <span><small>Quote at saved boundary</small><strong>Unavailable</strong><em>No certified quote in this window</em></span>}
    </div> : null}
    {!compactContext && page?.indicator_provenance.unavailable_columns.length ? <p role="note">Stale indicators: {page.indicator_provenance.unavailable_columns.join(", ")}</p> : null}
    {selectedIndicators.includes("saved.structural_v7") && structureReason
      ? <p role="note">V7 structure unavailable: {structureReason}</p> : null}
    {!compactContext && page?.history_limited ? <p role="note">ARTE history available from {page.history_first_session}; earlier {frame === "1mo" ? "months" : "sessions"} are unavailable.</p> : null}
    {/* ChartPanel owns the centered failure state; errors must not insert a
        second row above the toolbar or shift the chart layout. */}
    <ChartPanel persistedOnly settingsStorageKey="backtest-v4-strategy-one" payload={payload}
      ticker={symbol} timeframe={frame} timeframes={[...allowedFrames]}
      featureOptions={[]} indicatorOptions={[]} displayItemOptions={frame === "1d" || frame === "1mo" ? [] : displayItems}
      visibleColumns={selectedIndicators} onVisibleColumnsChange={values => {
        setSelectedIndicators(values);
      }}
      onTickerChange={value => changeScope({ symbol: value })} onTimeframeChange={value => changeScope({ frame: value as (typeof FRAMES)[number] })}
      emptyMessage="No price-bearing bars in this verified run window." errorMessage={error || undefined} loading={loading && !page}
      showIndicatorControls={frame !== "1d" && frame !== "1mo"} strategyPresentationEnabled={Boolean(toolbarAction) || tradeAnnotations.length > 0}
      dataStatus={overlayError || tradeError || undefined}
      tickerEditable={false} enableFullscreen={false} baseHeight={320} fillHeight={embedded} toolbarVariant={embedded ? "compact" : "full"}
      toolbarActions={toolbarAction} canLoadEarlier={Boolean(older)} loadingEarlier={loading && before !== null}
      onLoadEarlier={() => { if (older) setBefore(older); }} />
  </section>;
}

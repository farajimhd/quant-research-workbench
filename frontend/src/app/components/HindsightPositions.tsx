import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Eye, EyeOff, LoaderCircle, SlidersHorizontal, X } from "lucide-react";
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from "lightweight-charts";
import { api } from "../../api/client";

export type HindsightPosition = {
  entry_time: number; exit_time: number; entry_price: number; exit_price: number;
  direction: 'long' | 'short'; gross_move_per_share: number; gross_return_bps: number;
  position_number: number;
};
type Result = { positions: HindsightPosition[]; position_count: number; interval_count?: number; direction_counts?: Record<string, number>; interval_rejections?: Record<string, number>; rejection_reasons?: Record<string, number>; valid_prices?: number;
  display_filter?: { cutoff_bps: number; retained_count: number; removed_count: number } };
type Job = { id: string; status: "queued" | "running" | "completed" | "failed"; stage?: string; trades: number; error?: string; result?: Result };
const EMPTY: HindsightPosition[] = [];
const LABEL_DEFAULTS = { lookback_seconds: 2 };
const LABEL_FIELDS = [
  ['lookback_seconds', 'Swing lookback (seconds)', 0, 30, .1],
] as const;

export function HindsightDetails({ anchor, onClose, children, title = 'Hindsight statistics' }: { anchor: HTMLButtonElement; onClose: () => void; children: ReactNode; title?: string }) {
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 8, top: 8, maxHeight: window.innerHeight - 16 });
  useLayoutEffect(() => {
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const box = panel.current?.getBoundingClientRect();
      const zoom = panel.current ? Number.parseFloat(getComputedStyle(panel.current).zoom) || 1 : 1;
      if (box) setPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - box.width - 8)) / zoom,
        top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - box.height - 8)) / zoom,
        maxHeight: (window.innerHeight - 16) / zoom });
    };
    place();
    const observer = new ResizeObserver(place);
    if (panel.current) observer.observe(panel.current);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [anchor]);
  useEffect(() => {
    panel.current?.focus();
    const pointer = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !anchor.contains(event.target as Node)) onClose();
    };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); anchor.focus(); } };
    document.addEventListener("pointerdown", pointer);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", pointer); document.removeEventListener("keydown", key); };
  }, [anchor, onClose]);
  return createPortal(<div ref={panel} className="chart-settings-slot hindsight-details" role="dialog" aria-label={title === 'Hindsight statistics' ? 'Hindsight statistics and filter' : title} tabIndex={-1} style={position}>
    <div className="chart-settings-header"><b>{title}</b><button type="button" className="toolbar-button" aria-label={`Close ${title.toLowerCase()}`} onClick={() => { onClose(); anchor.focus(); }}><X size={14} /></button></div>
    {children}
  </div>, document.body);
}

export function useHindsightPositions(ticker: string, sessionDate?: string) {
  const identity = `${ticker}:${sessionDate ?? ""}`;
  const [state, setState] = useState<{ identity: string; job?: Job; visible: boolean; error?: string }>({ identity, visible: false });
  const [settings, setSettings] = useState(LABEL_DEFAULTS);
  const [request, setRequest] = useState({ identity: "", nonce: 0, settings: LABEL_DEFAULTS });
  const generation = useRef(0);
  const [hideSmallProfits, setHideSmallProfits] = useState(true);
  const [direction, setDirection] = useState<'both' | 'long' | 'short'>('both');
  const [detailsAnchor, setDetailsAnchor] = useState<HTMLButtonElement | null>(null);
  const closeDetails = useCallback(() => setDetailsAnchor(null), []);
  const current = state.identity === identity ? state : { identity, visible: false };
  useEffect(() => {
    generation.current += 1;
    setState({ identity, visible: false });
    setRequest({ identity: "", nonce: 0, settings: LABEL_DEFAULTS });
    setDetailsAnchor(null);
  }, [identity]);
  useEffect(() => {
    if (request.identity !== identity || !sessionDate) return;
    const controller = new AbortController();
    const epoch = ++generation.current;
    let timer: number | undefined;
    const receive = (job: Job) => {
      if (controller.signal.aborted || epoch !== generation.current) return;
      setState((value) => ({ ...value, identity, job, error: job.error }));
      if (job.status === "running" || job.status === "queued") {
        timer = window.setTimeout(() => {
          void api<Job>(`/api/research/hindsight/${job.id}`, { signal: controller.signal, timeoutMs: 15_000 }).then(receive).catch(failed);
        }, 1000);
      }
    };
    const failed = (error: unknown) => {
      if (!controller.signal.aborted && epoch === generation.current)
        setState({ identity, visible: false, error: error instanceof Error ? error.message : "Hindsight request failed" });
    };
    setState({ identity, visible: true });
    void api<Job>("/api/research/hindsight", { method: "POST", body: JSON.stringify({ ticker, session_date: sessionDate, ...request.settings }), signal: controller.signal, timeoutMs: 15_000 }).then(receive).catch(failed);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [request, identity, ticker, sessionDate]);
  const busy = Boolean(request.identity === identity && !current.error && (!current.job || ["queued", "running"].includes(current.job.status)));
  const result = current.job?.result;
  const filter = result?.display_filter;
  const selected = useMemo(() => result?.positions.filter((p) => (direction === 'both' || p.direction === direction) && (!hideSmallProfits || !filter || p.gross_return_bps >= filter.cutoff_bps)), [result, direction, hideSmallProfits, filter]);
  const displayedCount = selected?.length ?? 0;
  const title = `Hindsight price labels · ${sessionDate} 04:00–20:00 New York · completed-1s MACD > signal defines longs, MACD < signal defines shorts, regardless of zero. Long: lookback low to interval high. Short: lookback high to interval low. Canonical trade prices only; no liquidity, spread, costs or execution-policy gates. Both directions are retained for training; these are not executable profit estimates.`;
  const controls = sessionDate ? <div className="hindsight-controls">
    <button type="button" className="toolbar-button" aria-label="Hindsight positions" aria-pressed={current.visible} disabled={busy} title={title}
      onClick={() => result ? setState((value) => ({ ...value, visible: !value.visible })) : setRequest((n) => ({ identity, nonce: n.nonce + 1, settings }))}>
      {busy ? <LoaderCircle size={15} /> : current.visible ? <EyeOff size={15} /> : <Eye size={15} />}
      <span>{busy ? current.job?.stage === "macd" ? "Finding MACD intervals…" : "Finding positions…" : "Hindsight"}</span>
    </button>
    <button type="button" className="toolbar-button" aria-label="Hindsight statistics and filter" aria-haspopup="dialog" aria-expanded={Boolean(detailsAnchor)} title="Hindsight statistics and filter" onClick={(event) => setDetailsAnchor(detailsAnchor ? null : event.currentTarget)}><SlidersHorizontal size={15} /></button>
    {detailsAnchor ? <HindsightDetails anchor={detailsAnchor} onClose={closeDetails}>
    <span className="hindsight-summary" role="status" title={current.error || title}>
      {current.error ? `Failed: ${current.error} — click Hindsight to retry` : busy ? `${(current.job?.trades ?? 0).toLocaleString()} trades` : result ? `${(result.interval_count ?? 0).toLocaleString()} MACD intervals · ${(result.direction_counts?.long ?? 0).toLocaleString()} long / ${(result.direction_counts?.short ?? 0).toLocaleString()} short labels · ${current.visible ? `${displayedCount.toLocaleString()} shown` : "overlay hidden"}` : "Click Hindsight to generate labels for this session."}
    </span>
    {filter && current.visible ? <label className="hindsight-summary" title={title}>
      <input type="checkbox" checked={hideSmallProfits} onChange={(event) => setHideSmallProfits(event.target.checked)} /> Hide small moves
      {hideSmallProfits ? ` (<${filter.cutoff_bps.toFixed(1)} bps; ${filter.removed_count} hidden)` : ""}
    </label> : null}
    <label className="chart-setting-row">Show direction<select aria-label="Hindsight direction" value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}><option value="both">Both</option><option value="long">Long</option><option value="short">Short</option></select></label>
    <p className="chart-settings-help">5% gross price-move filter for display only. All valid long and short labels remain in the result. Execution eligibility is separate.</p>
    {result ? <details className="chart-settings-help"><summary>Price data and skipped intervals</summary>
      <p>{result.valid_prices?.toLocaleString()} valid trade prices</p>
      {Object.entries(result.interval_rejections ?? {}).map(([reason, count]) => <div key={reason}>{reason.replaceAll('_', ' ')}: {count.toLocaleString()}</div>)}
      {Object.entries(result.rejection_reasons ?? {}).map(([reason, count]) => <div key={reason}>Prices — {reason.replaceAll('_', ' ')}: {count.toLocaleString()}</div>)}
    </details> : null}
    <form className="chart-settings-section" onSubmit={(event) => { event.preventDefault(); setRequest((n) => ({ identity, nonce: n.nonce + 1, settings })); }}>
      <h3>Swing labels</h3>
      {LABEL_FIELDS.map(([key, label, min, max, step]) => <label className="chart-setting-row" key={key}>{label}
        <span className="chart-setting-inline"><input aria-label={label} type="range" min={min} max={max} step={step} value={settings[key]} onChange={(event) => setSettings((value) => ({ ...value, [key]: event.target.valueAsNumber }))} /><b>{settings[key].toLocaleString()}</b></span>
      </label>)}
      <button type="submit" className="toolbar-button hindsight-apply" disabled={busy}>Apply and regenerate</button>
      <span className="chart-settings-help">Lookback changes apply when regenerated. Direction and move filters only change the chart. Labels are independent and can overlap.</span>
    </form>
    </HindsightDetails> : null}
  </div> : null;
  return { controls, lookbackSeconds: request.settings.lookback_seconds, positions: current.visible ? selected ?? EMPTY : EMPTY };
}

/** Independent paint-only layer: contributes nothing to autoscale or trade state. */
export class HindsightPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private update: (() => void) | undefined;
  private positions: HindsightPosition[] = EMPTY;
  private coordinate: (time: number) => number | null = () => null;
  private color = "";
  private shortColor = "";
  private backing = "";
  private readonly view: IPrimitivePaneView = {
    zOrder: () => "top",
    renderer: () => ({ draw: (target) => {
      if (!this.chart || !this.series || !this.positions.length) return;
      target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
        ctx.save();
        ctx.font = "11px sans-serif";
        ctx.lineWidth = 2;
        let lastLabelX = -Infinity;
        for (let index = 0; index < this.positions.length; index++) {
          const p = this.positions[index];
          const x1 = this.coordinate(p.entry_time), x2 = this.coordinate(p.exit_time);
          const y1 = this.series!.priceToCoordinate(p.entry_price), y2 = this.series!.priceToCoordinate(p.exit_price);
          if (y1 === null || y2 === null || (x1 === null && x2 === null)) continue;
          const color = p.direction === 'short' ? this.shortColor : this.color;
          ctx.strokeStyle = color;
          ctx.fillStyle = color;
          ctx.setLineDash([5, 3]);
          if (x1 !== null && x2 !== null && x2 >= 0 && x1 <= mediaSize.width) {
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          }
          ctx.setLineDash([]);
          for (const [x, y, entry] of [[x1, y1, true], [x2, y2, false]] as const) {
            if (x === null || x < 0 || x > mediaSize.width) continue;
            const sign = (entry ? 1 : -1) * (p.direction === 'short' ? -1 : 1);
            ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - 4, y + sign * 8); ctx.lineTo(x + 4, y + sign * 8); ctx.closePath(); ctx.fill();
            // Keep every marker, but avoid stacking hundreds of labels at low zoom.
            if (x - lastLabelX >= 65) {
              const label = `${p.direction === 'short' ? 'Short' : 'Long'} ${entry ? "entry" : "exit"} H${p.position_number ?? index + 1}`;
              const labelY = Math.max(14, Math.min(mediaSize.height - 5, y + sign * 22));
              ctx.fillStyle = this.backing;
              ctx.fillRect(x + 5, labelY - 12, ctx.measureText(label).width + 6, 15);
              ctx.fillStyle = color; ctx.fillText(label, x + 8, labelY);
              lastLabelX = x;
            }
          }
        }
        ctx.restore();
      });
    } }),
  };
  attached({ chart, series, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>["attached"]>>[0]) {
    this.chart = chart; this.series = series as ISeriesApi<"Candlestick">; this.update = requestUpdate;
  }
  detached() { this.chart = null; this.series = null; this.update = undefined; }
  paneViews() { return [this.view]; }
  setState(positions: HindsightPosition[], coordinate: (time: number) => number | null) {
    this.positions = positions; this.coordinate = coordinate;
    const style = window.getComputedStyle(document.documentElement);
    this.color = style.getPropertyValue("--accent").trim();
    this.shortColor = style.getPropertyValue("--warning").trim();
    this.backing = style.getPropertyValue("--card").trim();
    this.update?.();
  }
}

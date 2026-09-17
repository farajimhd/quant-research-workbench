import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, SlidersHorizontal } from 'lucide-react';
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, MouseEventParams, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { Modal } from './Modal';

type Outcome = { entry_price: number; exit_price: number; exit_time: number; hold_seconds: number; gross_profit: number; net_profit: number };
type Opportunity = { time: number; action: 'buy' | 'sell' | 'wait' | 'unavailable'; profit: number | null; hold_seconds: number | null; exit_time: number | null; long: Outcome | null; short: Outcome | null; reason: string | null; available_at: number };
type Candle = { time: number; endTime?: number; isClosed?: boolean; low: number };
export type ActionValues = { ticker: string; start: string; end: string; source_end: string; labels: Opportunity[]; max_hold_seconds: number; counts: Record<string, number>; limitations: string[] };
type Job = { id: string; status: 'queued' | 'running' | 'completed' | 'failed'; events?: number; stage?: string; error?: string; result?: ActionValues };
const defaults = { start_time: '04:00', window_minutes: 30, cost_bps: 0, max_spread_bps: 150 };
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false });
const money = (v: number | null) => v == null ? '--' : `${v < 0 ? '-' : '+'}$${Number(Math.abs(v).toFixed(4))}`;
const actionName = (v: Opportunity['action']) => ({ buy: 'Buy', sell: 'Sell', wait: 'Wait', unavailable: 'N/A' })[v];
const actionLegend = <div className="action-values-legend"><span style={{ color: 'var(--semantic-positive)' }}>Buy (long)</span><span style={{ color: 'var(--semantic-neutral)' }}>Wait / N/A</span><span style={{ color: 'var(--semantic-negative)' }}>Sell (short)</span></div>;

export function useHindsightActions(ticker: string, sessionDate?: string, focus?: (start: number, end: number) => void) {
  const identity = `${ticker}:${sessionDate ?? ''}`;
  const [state, setState] = useState<{ identity: string; job?: Job; visible: boolean; error?: string }>({ identity, visible: false });
  const [settings, setSettings] = useState(defaults);
  const [request, setRequest] = useState<{ identity: string; settings: typeof defaults; nonce: number }>();
  const [open, setOpen] = useState(false);
  const detailsButton = useRef<HTMLButtonElement>(null);
  const focusRef = useRef(focus); focusRef.current = focus;
  const [step, setStep] = useState(0);
  const current = state.identity === identity ? state : { identity, visible: false };
  const result = current.job?.result;
  const busy = request?.identity === identity && !current.error && (!current.job || ['queued', 'running'].includes(current.job.status));
  const close = useCallback(() => setOpen(false), []);
  const inspect = useCallback((index: number) => { setStep(index); if (!open) detailsButton.current?.focus(); setOpen(true); }, [open]);
  useEffect(() => { setState({ identity, visible: false }); setOpen(false); setRequest(undefined); setStep(0); }, [identity]);
  useEffect(() => {
    if (!request || request.identity !== identity || !sessionDate) return;
    const controller = new AbortController(); let timer: number | undefined;
    const failed = (e: unknown) => { if (!controller.signal.aborted) setState({ identity, visible: false, error: String(e) }); };
    const receive = (job: Job) => {
      if (controller.signal.aborted) return;
      setState({ identity, job, visible: true, error: job.error });
      if (job.status === 'queued' || job.status === 'running') timer = window.setTimeout(() => {
        void api<Job>(`/api/research/hindsight-actions/${job.id}`, { signal: controller.signal, timeoutMs: 15000 }).then(receive).catch(failed);
      }, 1000);
      else if (job.result) {
        const index = Math.max(0, job.result.labels.findIndex(x => x.action === 'buy' || x.action === 'sell'));
        setStep(index); const t = job.result.labels[index]?.time;
        if (t != null) focusRef.current?.(t - 8, t + 8);
      }
    };
    setState({ identity, visible: true });
    void api<Job>('/api/research/hindsight-actions', { method: 'POST', body: JSON.stringify({ ticker, session_date: sessionDate, ...request.settings }), signal: controller.signal, timeoutMs: 15000 }).then(receive).catch(failed);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [identity, request, ticker, sessionDate]);
  const selected = result?.labels[Math.min(step, result.labels.length - 1)];
  const generate = () => setRequest(r => ({ identity, settings: { ...settings }, nonce: (r?.nonce ?? 0) + 1 }));
  const controls = sessionDate ? <div className="hindsight-controls action-values-controls">
    <button type="button" className="toolbar-button" aria-label="Hindsight action values" aria-pressed={current.visible} disabled={busy}
      title="Best gross profit per share within 90 seconds. Uses future quotes; not a prediction."
      onClick={() => result ? setState(s => ({ ...s, visible: !s.visible })) : generate()}><Activity size={15} /><span>{busy ? 'Finding best exits...' : 'Action values'}</span></button>
    <button ref={detailsButton} type="button" className="toolbar-button" aria-label="Action value details" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}><SlidersHorizontal size={15} /></button>
    {current.error ? <span className="action-values-error" role="alert">Action values failed. Open details to retry.</span> : null}
    {open ? <Modal className="action-values-modal" onClose={close} title="Hindsight action values" closeOnBackdrop>
      <p className="chart-settings-help">{ticker} / {sessionDate} / New York time. Size 1. Maximum hold: 90s.</p>
      <div className="action-values-status" role="status">{current.error || (busy ? `${(current.job?.events ?? 0).toLocaleString()} canonical events / ${current.job?.stage ?? 'queued'}` : result ? `${result.counts.seconds} independent entry opportunities. Profits cannot be added together.` : 'Generate labels for the selected ticker and window.')}</div>
      {actionLegend}
      <p className="chart-settings-help">Each completed candle: action, best gross profit/share, time to that exit. Buy opens a long; Sell opens a hypothetical short. Wait means neither direction has positive gross profit. N/A means insufficient eligible data.</p>
      {result && selected ? <section className="action-values-inspector" aria-label="Action value inspector">
        <label className="chart-setting-row">Entry decision <b>{clock(selected.time)}</b></label>
        <input className="action-values-time" aria-label="Action decision second" type="range" min={0} max={result.labels.length - 1} step={1} value={step} onChange={e => setStep(e.target.valueAsNumber)} />
        <p><strong>{actionName(selected.action)} / {money(selected.profit)} / {selected.hold_seconds == null ? '--' : `${selected.hold_seconds}s`}</strong></p>
        {selected.reason ? <p className="chart-settings-help">{selected.reason.replaceAll('_', ' ')}</p> : null}
        <table className="action-values-table"><thead><tr><th>Entry</th><th>Price</th><th>Best exit</th><th>Gross/share</th><th>Hold</th></tr></thead><tbody>{(['long', 'short'] as const).map(side => {
          const value = selected[side];
          return <tr key={side}><td>{side === 'long' ? 'Buy' : 'Sell short'}</td><td>{value?.entry_price.toFixed(4) ?? '--'}</td><td>{value ? `${value.exit_price.toFixed(4)} @ ${clock(value.exit_time)}` : '--'}</td><td>{value ? money(value.gross_profit) : '--'}</td><td>{value ? `${value.hold_seconds}s` : '--'}</td></tr>;
        })}</tbody></table>
        <p className="chart-settings-help">After configured fees: long {money(selected.long?.net_profit ?? null)}, short {money(selected.short?.net_profit ?? null)}. Chart labels show gross profit.</p>
        <button className="toolbar-button hindsight-apply" type="button" onClick={() => { focus?.(selected.time - 8, selected.time + 8); close(); }}>Center this second on chart</button>
      </section> : null}
      <form className="chart-settings-section" onSubmit={e => { e.preventDefault(); generate(); }}>
        <h3>Label assumptions</h3>
        <label className="chart-setting-row">Start (New York)<input aria-label="Action window start" type="time" value={settings.start_time} onChange={e => setSettings(s => ({ ...s, start_time: e.target.value }))} required /></label>
        {([
          ['window_minutes', 'Window minutes', 1, 120, 1],
          ['cost_bps', 'Fee per side (bps)', 0, 100, .1], ['max_spread_bps', 'Entry spread limit (bps)', 0, 1000, 1],
        ] as const).map(([key, label, min, max, increment]) => <label className="chart-setting-row" key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={increment} value={settings[key]} required onChange={e => setSettings(s => ({ ...s, [key]: e.target.valueAsNumber }))} /></label>)}
        <button type="submit" className="toolbar-button hindsight-apply" disabled={busy}>Generate action values</button>
      </form>
      <details className="chart-settings-help"><summary>Calculation and limitations</summary>
        <p>At each completed candle end, enter long at the fresh ask or short at the fresh bid. Search the next 1-90 seconds for the highest bid or lowest ask. Equal best exits choose the earliest time. Choose the direction with the larger positive gross profit; equal profits choose the shorter hold, then Buy.</p>
        <p>Quotes must be at most 1s old with one unit available. Entries require three trades in the trailing 10s and the configured spread limit. Gross profit already reflects quoted spread; fees are separate. This is retrospective labeling, not model training or a trading strategy.</p>
        <p>No borrow, queue, market-impact or stop-loss model. Overlapping opportunities cannot be summed into a portfolio return. Incomplete 90s windows are unavailable, not silently shortened.</p>
      </details>
    </Modal> : null}
  </div> : null;
  const caption = current.visible && result ? <div className="action-values-caption">
    <span>Hindsight / max hold 90s / action, gross $/share, hold time / click a label</span>{actionLegend}<span>Size 1 / zoom in to read each candle / shorts hypothetical</span>
  </div> : null;
  return { controls, caption, result: current.visible ? result : undefined, inspect };
}

/** Three text rows below each completed candle. No horizontal action lines. */
export class HindsightActionsPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<'Candlestick'> | null = null;
  private update: (() => void) | undefined;
  private result?: ActionValues;
  private candles: Candle[] = [];
  private coordinate: (time: number) => number | null = () => null;
  private inspect: (index: number) => void = () => {};
  private labelHits: { x1: number; x2: number; y1: number; y2: number; index: number; candleTime: number }[] = [];
  private colors = { buy: '', sell: '', wait: '', backing: '' };
  private click = (event: MouseEventParams<Time>) => {
    if (!event.point) return;
    const element = this.chart?.chartElement();
    const zoom = element && element.clientWidth ? element.getBoundingClientRect().width / element.clientWidth : 1;
    const x = event.point.x / zoom, y = event.point.y / zoom;
    const hit = this.labelHits.find(h => x >= h.x1 && x <= h.x2 && y >= h.y1 && y <= h.y2);
    if (hit) this.inspect(hit.index);
  };
  private view: IPrimitivePaneView = { zOrder: () => 'top', renderer: () => ({ draw: target => {
    this.labelHits = [];
    if (!this.result || !this.series) return;
    const r = this.result, byTime = new Map(r.labels.map((row, index) => [row.time, index]));
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save(); ctx.font = '10px sans-serif'; ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
      for (const candle of this.candles) {
        if (candle.isClosed === false) continue;
        const index = byTime.get(candle.endTime ?? candle.time + 1);
        if (index == null) continue;
        const row = r.labels[index], x = this.coordinate(candle.time), low = this.series!.priceToCoordinate(candle.low);
        if (x == null || low == null || x < 0 || x > mediaSize.width || low < 0 || low > mediaSize.height) continue;
        const text = [actionName(row.action), money(row.profit), row.hold_seconds == null ? '--' : `${row.hold_seconds}s`];
        const width = Math.max(...text.map(t => ctx.measureText(t).width)) + 4, y = low + 10;
        ctx.fillStyle = this.colors.backing; ctx.globalAlpha = .9; ctx.fillRect(x - width / 2, y - 6, width, 38); ctx.globalAlpha = 1;
        ctx.fillStyle = row.action === 'buy' ? this.colors.buy : row.action === 'sell' ? this.colors.sell : this.colors.wait;
        text.forEach((t, line) => ctx.fillText(t, x, y + line * 13));
        this.labelHits.push({ x1: x - width / 2, x2: x + width / 2, y1: y - 6, y2: y + 32, index, candleTime: candle.time });
      }
      ctx.restore();
    });
  } }) };
  autoscaleInfo() { return this.result ? { priceRange: null, margins: { above: 0, below: 48 } } : null; }
  attached({ chart, series, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) {
    this.chart = chart; this.series = series as ISeriesApi<'Candlestick'>; this.update = requestUpdate; chart.subscribeClick(this.click);
  }
  detached() { this.chart?.unsubscribeClick(this.click); this.chart = null; this.series = null; this.update = undefined; this.labelHits = []; }
  paneViews() { return [this.view]; }
  setState(result: ActionValues | undefined, coordinate: (time: number) => number | null, inspect: (index: number) => void, candles: Candle[]) {
    this.result = result; this.coordinate = coordinate; this.inspect = inspect; this.candles = candles;
    const css = getComputedStyle(document.documentElement);
    this.colors = { buy: css.getPropertyValue('--semantic-positive').trim(), sell: css.getPropertyValue('--semantic-negative').trim(), wait: css.getPropertyValue('--semantic-neutral').trim(), backing: css.getPropertyValue('--card').trim() };
    this.update?.();
  }
}

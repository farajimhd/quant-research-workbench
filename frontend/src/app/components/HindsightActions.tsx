import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, SlidersHorizontal } from 'lucide-react';
import type { IChartApi, ISeriesApi, ISeriesPrimitive, MouseEventParams, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { Modal } from './Modal';

type Outcome = { entry_price: number; exit_price: number; exit_time: number; hold_seconds: number; gross_profit: number; net_profit: number };
type Opportunity = { time: number; action: 'buy' | 'sell' | 'wait' | 'unavailable'; profit: number | null; hold_seconds: number | null; exit_time: number | null; long: Outcome | null; short: Outcome | null; reason: string | null; available_at: number };
type Candle = { time: number; endTime?: number; isClosed?: boolean; low: number; high: number };
export type ActionValues = { ticker: string; start: string; end: string; source_end: string; labels: Opportunity[]; max_hold_seconds: number; counts: Record<string, number>; limitations: string[] };
type Job = { id: string; status: 'queued' | 'running' | 'completed' | 'failed'; events?: number; stage?: string; error?: string; result?: ActionValues };
const defaults = { start_time: '04:00', window_minutes: 30, cost_bps: 0, max_spread_bps: 150 };
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false });
const money = (v: number | null) => v == null ? '--' : `${v < 0 ? '-' : '+'}$${Number(Math.abs(v).toFixed(4))}`;
const actionName = (v: Opportunity['action']) => ({ buy: 'Buy long', sell: 'Open short', wait: 'Stay flat', unavailable: 'N/A' })[v];
const actionLegend = <div className="action-values-legend"><span style={{ color: 'var(--semantic-positive)' }}>Buy long</span><span style={{ color: 'var(--semantic-neutral)' }}>Stay flat / unavailable</span><span style={{ color: 'var(--semantic-negative)' }}>Open short</span></div>;

export function useHindsightActions(ticker: string, sessionDate?: string, focus?: (start: number, end: number) => void) {
  const identity = `${ticker}:${sessionDate ?? ''}`;
  const [state, setState] = useState<{ identity: string; job?: Job; visible: boolean; error?: string }>({ identity, visible: false });
  const [settings, setSettings] = useState(defaults);
  const [request, setRequest] = useState<{ identity: string; settings: typeof defaults; nonce: number }>();
  const [open, setOpen] = useState(false);
  const detailsButton = useRef<HTMLButtonElement>(null);
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
        setStep(index);
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
      <div className="action-values-status" role="status">{current.error || (busy ? `${(current.job?.events ?? 0).toLocaleString()} canonical events / ${current.job?.stage ?? 'queued'}` : result ? `${result.counts.seconds} independent entry opportunities. Profits cannot be added together.` : 'Generate action values, then click a completed candle.')}</div>
      {actionLegend}
      <p className="chart-settings-help">Compare independent actions starting flat at this candle close. Buy long and Open short are separate new trades, not a connected entry and exit. Values are gross dollars per share within the next 90 seconds.</p>
      {result && selected ? <section className="action-values-inspector" aria-label="Action value inspector">
        <label className="chart-setting-row">Candle close / decision <b>{clock(selected.time)}</b></label>
        <input className="action-values-time" aria-label="Action decision second" type="range" min={0} max={result.labels.length - 1} step={1} value={step} onChange={e => setStep(e.target.valueAsNumber)} />
        <p><strong>Best entry action: {actionName(selected.action)}</strong></p>
        {selected.reason ? <p className="chart-settings-help">{selected.reason.replaceAll('_', ' ')}</p> : null}
        <table className="action-values-table"><thead><tr><th>Action</th><th>Entry price</th><th>Best exit price / time</th><th>Value ($/share)</th><th>Hold</th></tr></thead><tbody>{(['long', 'short'] as const).map(side => {
          const value = selected[side];
          return <tr key={side}><td>{side === 'long' ? 'Buy long' : 'Open short'}</td><td>{value?.entry_price.toFixed(4) ?? '--'}</td><td>{value ? `${value.exit_price.toFixed(4)} @ ${clock(value.exit_time)}` : '--'}</td><td>{value ? money(value.gross_profit) : '--'}</td><td>{value ? `${value.hold_seconds}s` : '--'}</td></tr>;
        })}<tr><td>Stay flat</td><td>--</td><td>No trade</td><td>{money(0)}</td><td>0s</td></tr></tbody></table>
        <p className="chart-settings-help">Stay flat is the $0 no-trade baseline, not the value of waiting and entering later. Unavailable entries show --; negative returns remain visible.</p>
        <details className="chart-settings-help"><summary>Actions for an existing position</summary>
          <table className="action-values-table"><thead><tr><th>Action</th><th>Value</th><th>Required context</th></tr></thead><tbody>
            {['Hold long', 'Exit long', 'Hold short', 'Cover short'].map(action => <tr key={action}><td>{action}</td><td>Not calculated</td><td>Position entry price and time</td></tr>)}
          </tbody></table>
          <p>No position is selected. These actions are not valued by the current independent-entry algorithm. A short-entry value is never an exit-long value.</p>
        </details>
        <p className="chart-settings-help">After configured fees: long {money(selected.long?.net_profit ?? null)}, short {money(selected.short?.net_profit ?? null)}. The action table shows gross profit.</p>
        <button className="toolbar-button hindsight-apply" type="button" onClick={() => { focus?.(selected.time - 8, selected.time + 8); close(); }}>Center this second on chart</button>
      </section> : null}
      <form className="chart-settings-section" onSubmit={e => { e.preventDefault(); generate(); }}>
        <h3>Calculation settings</h3>
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
    <span>Hindsight / click a completed candle to compare action values / max hold 90s</span>{actionLegend}<span>Independent entries / shorts hypothetical</span>
  </div> : null;
  return { controls, caption, result: current.visible ? result : undefined, inspect };
}

/** Candle interaction only: no labels, lines or autoscale changes. */
export class HindsightActionsPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<'Candlestick'> | null = null;
  private result?: ActionValues;
  private candles: Candle[] = [];
  private coordinate: (time: number) => number | null = () => null;
  private inspect: (index: number) => void = () => {};
  private click = (event: MouseEventParams<Time>) => {
    if (!event.point || !this.result || !this.series || !this.chart) return;
    const element = this.chart.chartElement();
    const zoom = element.clientWidth ? element.getBoundingClientRect().width / element.clientWidth : 1;
    const x = event.point.x / zoom, y = event.point.y / zoom;
    const halfWidth = this.chart.timeScale().options().barSpacing / 2;
    let candle: Candle | undefined, distance = Infinity;
    for (const candidate of this.candles) {
      const cx = this.coordinate(candidate.time);
      if (cx == null) continue;
      const dx = Math.abs(x - cx);
      if (dx <= halfWidth && dx < distance) { candle = candidate; distance = dx; }
    }
    if (!candle || candle.isClosed === false) return;
    const high = this.series.priceToCoordinate(candle.high), low = this.series.priceToCoordinate(candle.low);
    if (high == null || low == null || y < Math.min(high, low) - 5 || y > Math.max(high, low) + 5) return;
    const index = this.result.labels.findIndex(row => row.time === (candle.endTime ?? candle.time + 1));
    if (index >= 0) this.inspect(index);
  };
  attached({ chart, series }: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) {
    this.chart = chart; this.series = series as ISeriesApi<'Candlestick'>; chart.subscribeClick(this.click);
  }
  detached() { this.chart?.unsubscribeClick(this.click); this.chart = null; this.series = null; }
  setState(result: ActionValues | undefined, coordinate: (time: number) => number | null, inspect: (index: number) => void, candles: Candle[]) {
    this.result = result; this.coordinate = coordinate; this.inspect = inspect; this.candles = candles;
  }
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, SlidersHorizontal } from 'lucide-react';
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, MouseEventParams, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { HindsightDetails } from './HindsightPositions';

type Step = { time: number; before: number; after: number; action: string; price: number | null; advantage: number; state_index: number; forced_terminal_unwind: boolean };
type Move = { number: number; direction: 'long' | 'short'; start_index: number; end_index: number; entry_time: number; exit_time: number; entry_price: number; exit_price: number; net_cash: number };
type ActionRun = { action: string; before: number; start_index: number; end_index: number; start_time: number; end_time: number; price: number | null };
export type ActionValues = {
  ticker: string; start: string; end: string; inventory: number[]; path: Step[]; moves: Move[];
  action_runs: ActionRun[]; values: (number | null)[][][]; net_cash: number; risk_penalty: number; objective: number;
  position_size: 1; source_counts: Record<string, number>;
  counts: { seconds: number; moves: number; adjustments: number }; limitations: string[];
};
type Job = { id: string; status: 'queued' | 'running' | 'completed' | 'failed'; events?: number; stage?: string; error?: string; result?: ActionValues };
const defaults = { start_time: '04:00', window_minutes: 30, cost_bps: 5, max_spread_bps: 150, risk_bps_per_second: .01 };
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false });
const dollars = (v: number) => `${v < 0 ? '−' : '+'}$${Math.abs(v).toFixed(4)}`;
const position = (q: number) => q > 0 ? 'Long' : q < 0 ? 'Short' : 'Flat';
const action = (before: number, after: number) => before === after ? (before ? 'Hold' : 'Wait') : !before ? `Enter ${position(after).toLowerCase()}` : `Exit ${position(before).toLowerCase()}`;
const words = (v: string) => v.replaceAll('_', ' ');

export function useHindsightActions(ticker: string, sessionDate?: string, focus?: (start: number, end: number) => void) {
  const identity = `${ticker}:${sessionDate ?? ''}`;
  const [state, setState] = useState<{ identity: string; job?: Job; visible: boolean; error?: string }>({ identity, visible: false });
  const [settings, setSettings] = useState(defaults);
  const [request, setRequest] = useState<{ identity: string; settings: typeof defaults; nonce: number }>();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const detailsButton = useRef<HTMLButtonElement>(null);
  const [step, setStep] = useState(0);
  const [inventoryIndex, setInventoryIndex] = useState<number | null>(null);
  const current = state.identity === identity ? state : { identity, visible: false };
  const result = current.job?.result;
  const busy = request?.identity === identity && !current.error && (!current.job || ['queued', 'running'].includes(current.job.status));
  const close = useCallback(() => setAnchor(null), []);
  const inspect = useCallback((index: number) => { setStep(index); setInventoryIndex(null); setAnchor(detailsButton.current); }, []);
  useEffect(() => { setState({ identity, visible: false }); setAnchor(null); setRequest(undefined); setStep(0); setInventoryIndex(null); }, [identity]);
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
      else if (job.result) { setStep(job.result.moves[0]?.start_index ?? 0); setInventoryIndex(null); }
    };
    setState({ identity, visible: true });
    void api<Job>('/api/research/hindsight-actions', { method: 'POST', body: JSON.stringify({ ticker, session_date: sessionDate, ...request.settings }), signal: controller.signal, timeoutMs: 15000 }).then(receive).catch(failed);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [identity, request, ticker, sessionDate]);
  const selected = result?.path[Math.min(step, result.path.length - 1)];
  const stateIndex = inventoryIndex ?? selected?.state_index ?? 1;
  const vector = result?.values[step]?.[stateIndex];
  const move = result?.moves.find(m => step >= m.start_index && step <= m.end_index);
  const generate = () => setRequest(r => ({ identity, settings: { ...settings }, nonce: (r?.nonce ?? 0) + 1 }));
  const controls = sessionDate ? <div className="hindsight-controls action-values-controls">
    <button type="button" className="toolbar-button" aria-label="Hindsight action values" aria-pressed={current.visible} disabled={busy}
      title="Retrospective 1s inventory action values. Uses future prices; not trading signals."
      onClick={() => result ? setState(s => ({ ...s, visible: !s.visible })) : generate()}><Activity size={15} /><span>{busy ? `Values: ${current.job?.stage === 'values' ? 'solving…' : 'loading…'}` : 'Action values'}</span></button>
    <button ref={detailsButton} type="button" className="toolbar-button" aria-label="Action value details" aria-haspopup="dialog" aria-expanded={Boolean(anchor)} onClick={e => setAnchor(anchor ? null : e.currentTarget)}><SlidersHorizontal size={15} /></button>
    {current.error ? <span className="action-values-error" role="alert">Action values failed. Open details to retry.</span> : null}
    {anchor ? <HindsightDetails anchor={anchor} onClose={close} title="Hindsight action values">
      <p className="chart-settings-help">{ticker} · {sessionDate} · New York time · 1s decisions. Full-window hindsight; hypothetical shorts.</p>
      <div className="action-values-status" role="status">{current.error || (busy ? `${(current.job?.events ?? 0).toLocaleString()} canonical events · ${current.job?.stage ?? 'queued'}` : result ? `${result.counts.moves} moves · ${result.counts.adjustments} adjustments · ${dollars(result.net_cash)} modeled net cash · size 1` : 'Generate action values for the selected ticker and window.')}</div>
      <div className="action-values-legend"><span>Worse ≤ −$0.05/unit</span><i aria-hidden="true" /><span>Better ≥ +$0.05/unit</span></div>
      <p className="chart-settings-help">One line per uninterrupted action, anchored at the price when it begins. Labels stay on that line. Color shows benefit versus holding/waiting; neutral = $0 and color saturates at ±$0.05/unit.</p>
      {result && selected ? <section className="action-values-inspector" aria-label="Action value inspector">
        <label className="chart-setting-row">Move<select aria-label="Inspect hindsight move" value={move?.number ?? ''} onChange={e => { const m = result.moves.find(x => x.number === Number(e.target.value)); if (m) { inspect(m.start_index); focus?.(m.entry_time - 15, m.exit_time + 15); } }}><option value="">Between moves</option>{result.moves.map(m => <option key={m.number} value={m.number}>{m.number} · {m.direction} · {clock(m.entry_time)} · {dollars(m.net_cash)}</option>)}</select></label>
        <label className="chart-setting-row">Decision time <b>{clock(selected.time)}</b></label>
        <input className="action-values-time" aria-label="Action decision second" type="range" min={0} max={result.path.length - 1} step={1} value={step} onChange={e => { setStep(e.target.valueAsNumber); setInventoryIndex(null); }} />
        <label className="chart-setting-row">Position before action<select aria-label="Action starting position" value={stateIndex} onChange={e => setInventoryIndex(Number(e.target.value))}>{result.inventory.map((q, index) => <option key={q} value={index}>{position(q)}{index === selected.state_index ? ' · path' : ''}</option>)}</select></label>
        <p className="chart-settings-help">Path: {words(selected.action)} · {position(selected.before)} → {position(selected.after)}{selected.forced_terminal_unwind ? ' · required to finish flat' : ''}. Values assume optimal future continuation. They are not additive across seconds.</p>
        <table className="action-values-table"><thead><tr><th>Action</th><th>Resulting position</th><th>$/unit vs hold/wait</th></tr></thead><tbody>{result.inventory.map((target, index) => {
          const q = target - result.inventory[stateIndex]; const value = vector?.[index];
          if (Math.abs(q) > 1) return null;
          return <tr key={target}><td>{action(result.inventory[stateIndex], target)}</td><td>{position(target)}</td><td className={value == null ? '' : value > 0 ? 'positive' : value < 0 ? 'negative' : ''}>{value == null ? 'Infeasible' : dollars(value)}{selected.forced_terminal_unwind && stateIndex === selected.state_index && target === selected.after ? ' *' : ''}</td></tr>;
        })}</tbody></table>
        {vector?.[stateIndex] == null ? <p className="chart-settings-help">Holding cannot finish flat under the remaining constraints. Values use the best feasible unwind as the $0 reference.</p> : null}
        <button className="toolbar-button hindsight-apply" type="button" onClick={() => { focus?.(selected.time - 30, selected.time + 30); close(); }}>Center this second on chart</button>
      </section> : null}
      <form className="chart-settings-section" onSubmit={e => { e.preventDefault(); generate(); }}>
        <h3>Label assumptions</h3>
        <label className="chart-setting-row">Start (New York)<input aria-label="Action window start" type="time" value={settings.start_time} onChange={e => setSettings(s => ({ ...s, start_time: e.target.value }))} required /></label>
        {([
          ['window_minutes', 'Window minutes', 1, 120, 1],
          ['cost_bps', 'Cost per side (bps)', 0, 100, .1], ['max_spread_bps', 'Entry spread limit (bps)', 0, 1000, 1],
          ['risk_bps_per_second', 'Holding penalty (bps/s)', 0, 10, .01],
        ] as const).map(([key, label, min, max, increment]) => <label className="chart-setting-row" key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={increment} value={settings[key]} required onChange={e => setSettings(s => ({ ...s, [key]: e.target.valueAsNumber }))} /></label>)}
        <button type="submit" className="toolbar-button hindsight-apply" disabled={busy}>Generate action values</button>
      </form>
      <details className="chart-settings-help"><summary>Value definition and limitations</summary>
        <p>Size is fixed at one. Positions are flat, long or short and must finish flat. Quotes must be ≤1s old with one unit available; entries require three trades in the trailing 10s. No sizing or scaling decisions.</p>
        <p>While in a position, the penalty per second is: current midpoint × penalty bps / 10,000.</p>
        <p>Short borrow, queue position, market impact and stop-loss execution are not modeled. One-ticker values have no whole-market rank.</p>
        {result ? <p>Risk penalty {dollars(-result.risk_penalty)} · objective {dollars(result.objective)} · {result.source_counts.seconds_without_fresh_quote ?? 0} seconds without a fresh quote. Model labels become available only at {new Date(result.end).toLocaleString('en-US', { timeZone: 'America/New_York' })} New York.</p> : null}
      </details>
    </HindsightDetails> : null}
  </div> : null;
  const caption = current.visible && result ? <div className="action-values-caption">
    <span>Hindsight · chosen action vs hold/wait · $/unit · click a line to inspect</span>
    <div className="action-values-legend"><span>≤ −$0.05</span><i aria-hidden="true" /><span>$0</span><i aria-hidden="true" /><span>≥ +$0.05</span></div>
    <span>One line per action run · size 1 · shorts hypothetical</span>
  </div> : null;
  return { controls, caption, result: current.visible ? result : undefined, inspect };
}

/** Paint-only price-anchored horizontal spectra; never contributes to autoscale. */
export class HindsightActionsPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<'Candlestick'> | null = null;
  private update: (() => void) | undefined;
  private result?: ActionValues;
  private coordinate: (time: number) => number | null = () => null;
  private inspect: (index: number) => void = () => {};
  private hits: { x1: number; x2: number; y: number; index: number }[] = [];
  private colors = { positive: '', negative: '', neutral: '', backing: '', text: '' };
  private click = (event: MouseEventParams<Time>) => {
    if (!event.point) return;
    // Native chart pointer coordinates include CSS zoom; primitive coordinates do not.
    const element = this.chart?.chartElement();
    const zoom = element && element.clientWidth ? element.getBoundingClientRect().width / element.clientWidth : 1;
    const x = event.point.x / zoom, y = event.point.y / zoom;
    const hit = this.hits.find(h => x >= h.x1 && x <= h.x2 && Math.abs(y - h.y) <= 7);
    if (hit) this.inspect(hit.index);
  };
  private view: IPrimitivePaneView = { zOrder: () => 'top', renderer: () => ({ draw: target => {
    this.hits = [];
    if (!this.result || !this.series) return;
    const r = this.result;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save(); ctx.font = '11px sans-serif'; ctx.textBaseline = 'middle';
      const labels: { x: number; y: number; width: number; text: string }[] = [];
      const label = (text: string, x: number, y: number, align: 'left' | 'right') => {
        const width = ctx.measureText(text).width + 8;
        x = Math.max(0, Math.min(mediaSize.width - width, align === 'right' ? x - width : x));
        if (labels.some(l => Math.abs(l.y - y) < 15 && x < l.x + l.width && x + width > l.x)) return;
        labels.push({ x, y, width, text });
      };
      for (const run of r.action_runs) {
        if (run.price == null) continue;
        const y = this.series!.priceToCoordinate(run.price);
        if (y == null || y < 0 || y > mediaSize.height) continue;
        const startX = this.coordinate(run.start_time), endX = this.coordinate(run.end_time);
        if (startX == null || endX == null || endX < 0 || startX > mediaSize.width) continue;
        for (let i = run.start_index; i <= run.end_index; i++) {
          const step = r.path[i], x1 = this.coordinate(step.time);
          const x2 = this.coordinate(Math.min(run.end_time, r.path[i + 1]?.time ?? run.end_time));
          if (x1 == null || x2 == null || x2 < 0 || x1 > mediaSize.width) continue;
          const value = step.advantage;
          ctx.strokeStyle = Math.abs(value) < 1e-8 ? this.colors.neutral : value > 0 ? this.colors.positive : this.colors.negative;
          ctx.globalAlpha = .3 + .7 * Math.min(1, Math.abs(value) / .05);
          ctx.lineWidth = 4; ctx.setLineDash([]);
          ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(Math.max(x1 + 1, x2), y); ctx.stroke(); ctx.globalAlpha = 1;
          this.hits.push({ x1: Math.max(0, x1), x2: Math.min(mediaSize.width, Math.max(x1 + 1, x2)), y, index: i });
        }
        const name = run.action === 'hold' ? `Hold ${position(run.before).toLowerCase()}` : run.action === 'stay_flat' ? 'Wait' : words(run.action);
        if (startX >= 0 && startX <= mediaSize.width) label(`${name} ${clock(run.start_time)}`, startX, y, 'left');
        if (endX - Math.max(0, startX) > 230 && endX <= mediaSize.width) label(`${name} ${clock(run.end_time)} ${dollars(r.path[run.end_index].advantage)}/unit`, endX, y, 'right');
      }
      // Paint labels last so a later action at the same price cannot strike through them.
      for (const { x, y, width, text } of labels) {
        ctx.globalAlpha = .95; ctx.fillStyle = this.colors.backing; ctx.fillRect(x, y - 7.5, width, 15);
        ctx.fillStyle = this.colors.text; ctx.fillText(text, x + 4, y); ctx.globalAlpha = 1;
      }
      ctx.restore();
    });
  } }) };
  attached({ chart, series, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) {
    this.chart = chart; this.series = series as ISeriesApi<'Candlestick'>; this.update = requestUpdate; chart.subscribeClick(this.click);
  }
  detached() { this.chart?.unsubscribeClick(this.click); this.chart = null; this.series = null; this.update = undefined; this.hits = []; }
  paneViews() { return [this.view]; }
  setState(result: ActionValues | undefined, coordinate: (time: number) => number | null, inspect: (index: number) => void) {
    this.result = result; this.coordinate = coordinate; this.inspect = inspect;
    const css = getComputedStyle(document.documentElement);
    this.colors = { positive: css.getPropertyValue('--semantic-positive').trim(), negative: css.getPropertyValue('--semantic-negative').trim(), neutral: css.getPropertyValue('--semantic-neutral').trim(), backing: css.getPropertyValue('--card').trim(), text: css.getPropertyValue('--foreground').trim() };
    this.update?.();
  }
}

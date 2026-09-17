import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, SlidersHorizontal } from 'lucide-react';
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, MouseEventParams, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { HindsightDetails } from './HindsightPositions';

type Step = { time: number; before: number; after: number; action: string; price: number | null; advantage: number; state_index: number; forced_terminal_unwind: boolean };
type Move = { number: number; direction: 'long' | 'short'; start_index: number; end_index: number; entry_time: number; exit_time: number; entry_price: number; exit_price: number; net_cash: number };
export type ActionValues = {
  ticker: string; start: string; end: string; inventory: number[]; path: Step[]; moves: Move[];
  values: (number | null)[][][]; net_cash: number; risk_penalty: number; objective: number;
  parameters: { lot_shares: number; inventory_steps: number }; source_counts: Record<string, number>;
  counts: { seconds: number; moves: number; adjustments: number }; limitations: string[];
};
type Job = { id: string; status: 'queued' | 'running' | 'completed' | 'failed'; events?: number; stage?: string; error?: string; result?: ActionValues };
const defaults = { start_time: '04:00', window_minutes: 30, lot_shares: 25, inventory_steps: 4,
  max_notional: 1000, cost_bps: 5, max_spread_bps: 150, participation: .05, risk_bps_per_second: .01 };
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false });
const dollars = (v: number) => `${v < 0 ? '−' : '+'}$${Math.abs(v).toFixed(2)}`;
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
  const stateIndex = inventoryIndex ?? selected?.state_index ?? 0;
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
      <div className="action-values-status" role="status">{current.error || (busy ? `${(current.job?.events ?? 0).toLocaleString()} canonical events · ${current.job?.stage ?? 'queued'}` : result ? `${result.counts.moves} moves · ${result.counts.adjustments} adjustments · ${dollars(result.net_cash)} modeled net cash` : 'Generate action values for the selected ticker and window.')}</div>
      <div className="action-values-legend"><span>Worse ≤ −$1</span><i aria-hidden="true" /><span>Better ≥ +$1</span></div>
      <p className="chart-settings-help">Line color: value of adding one lot in the move’s direction versus holding, at that second’s path inventory. Neutral = $0; dashed = infeasible. Color saturates at ±$1; exact values remain available.</p>
      {result && selected ? <section className="action-values-inspector" aria-label="Action value inspector">
        <label className="chart-setting-row">Move<select aria-label="Inspect hindsight move" value={move?.number ?? ''} onChange={e => { const m = result.moves.find(x => x.number === Number(e.target.value)); if (m) { inspect(m.start_index); focus?.(m.entry_time - 15, m.exit_time + 15); } }}><option value="">Between moves</option>{result.moves.map(m => <option key={m.number} value={m.number}>{m.number} · {m.direction} · {clock(m.entry_time)} · {dollars(m.net_cash)}</option>)}</select></label>
        <label className="chart-setting-row">Decision time <b>{clock(selected.time)}</b></label>
        <input className="action-values-time" aria-label="Action decision second" type="range" min={0} max={result.path.length - 1} step={1} value={step} onChange={e => { setStep(e.target.valueAsNumber); setInventoryIndex(null); }} />
        <label className="chart-setting-row">Inventory before action<select aria-label="Action starting inventory" value={stateIndex} onChange={e => setInventoryIndex(Number(e.target.value))}>{result.inventory.map((q, index) => <option key={q} value={index}>{q} shares{index === selected.state_index ? ' · path' : ''}</option>)}</select></label>
        <p className="chart-settings-help">Path: {words(selected.action)} · {selected.before} → {selected.after} shares{selected.forced_terminal_unwind ? ' · required to finish flat' : ''}. Values assume optimal future continuation. They are not additive across seconds.</p>
        <table className="action-values-table"><thead><tr><th>Adjustment</th><th>Target shares</th><th>Value vs hold</th></tr></thead><tbody>{result.inventory.map((target, index) => {
          const q = target - result.inventory[stateIndex]; const value = vector?.[index];
          if (Math.abs(q) > result.parameters.lot_shares) return null;
          return <tr key={target}><td>{q > 0 ? `Buy ${q}` : q < 0 ? `Sell ${-q}` : 'Hold'}</td><td>{target}</td><td className={value == null ? '' : value > 0 ? 'positive' : value < 0 ? 'negative' : ''}>{value == null ? 'Infeasible' : dollars(value)}{selected.forced_terminal_unwind && stateIndex === selected.state_index && target === selected.after ? ' *' : ''}</td></tr>;
        })}</tbody></table>
        {vector?.[stateIndex] == null ? <p className="chart-settings-help">Holding cannot finish flat under the remaining constraints. Values use the best feasible unwind as the $0 reference.</p> : null}
        <button className="toolbar-button hindsight-apply" type="button" onClick={() => { focus?.(selected.time - 30, selected.time + 30); close(); }}>Center this second on chart</button>
      </section> : null}
      <form className="chart-settings-section" onSubmit={e => { e.preventDefault(); generate(); }}>
        <h3>Label assumptions</h3>
        <label className="chart-setting-row">Start (New York)<input aria-label="Action window start" type="time" value={settings.start_time} onChange={e => setSettings(s => ({ ...s, start_time: e.target.value }))} required /></label>
        {([
          ['window_minutes', 'Window minutes', 1, 120, 1], ['lot_shares', 'Shares per adjustment', 1, 1000, 1],
          ['inventory_steps', 'Maximum lots per side', 1, 8, 1], ['max_notional', 'Maximum exposure ($)', 1, 100000, 1],
          ['cost_bps', 'Cost per side (bps)', 0, 100, .1], ['max_spread_bps', 'Entry spread limit (bps)', 0, 1000, 1],
          ['participation', 'Trailing-volume fraction', .001, 1, .001], ['risk_bps_per_second', 'Holding penalty (bps/s)', 0, 10, .01],
        ] as const).map(([key, label, min, max, increment]) => <label className="chart-setting-row" key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={increment} value={settings[key]} required onChange={e => setSettings(s => ({ ...s, [key]: e.target.valueAsNumber }))} /></label>)}
        <button type="submit" className="toolbar-button hindsight-apply" disabled={busy}>Generate action values</button>
      </form>
      <details className="chart-settings-help"><summary>Value definition and limitations</summary>
        <p>Backward optimization of net cash minus a quadratic exposure penalty; inventory must finish flat. One lot may be traded per second. Quotes must be ≤1s old; additions require three trades in the trailing 10s. Each adjustment is limited by displayed size and the configured fraction of trailing 10s volume.</p>
        <p>The penalty per second is: (inventory / maximum shares)² × maximum shares × current midpoint × penalty bps / 10,000.</p>
        <p>Short borrow, queue position, market impact and stop-loss execution are not modeled. One-ticker values have no whole-market rank.</p>
        {result ? <p>Risk penalty {dollars(-result.risk_penalty)} · objective {dollars(result.objective)} · {result.source_counts.seconds_without_fresh_quote ?? 0} seconds without a fresh quote. Model labels become available only at {new Date(result.end).toLocaleString('en-US', { timeZone: 'America/New_York' })} New York.</p> : null}
      </details>
    </HindsightDetails> : null}
  </div> : null;
  const caption = current.visible && result ? <div className="action-values-caption">
    <span>Hindsight · add {result.parameters.lot_shares} shares vs hold · click a line to inspect</span>
    <div className="action-values-legend"><span>≤ −$1</span><i aria-hidden="true" /><span>$0</span><i aria-hidden="true" /><span>≥ +$1</span></div>
    <span>Dashed: infeasible · shorts hypothetical</span>
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
      ctx.save(); ctx.font = '11px sans-serif';
      const labels: { x: number; y: number; width: number }[] = [];
      const label = (text: string, x: number, y: number, align: 'left' | 'right') => {
        const width = ctx.measureText(text).width + 8;
        x = Math.max(0, Math.min(mediaSize.width - width, align === 'right' ? x - width : x));
        y = Math.max(14, Math.min(mediaSize.height - 2, y));
        if (labels.some(l => Math.abs(l.y - y) < 15 && x < l.x + l.width && x + width > l.x)) return;
        labels.push({ x, y, width }); ctx.globalAlpha = .95; ctx.fillStyle = this.colors.backing; ctx.fillRect(x, y - 12, width, 15);
        ctx.fillStyle = this.colors.text; ctx.fillText(text, x + 4, y); ctx.globalAlpha = 1;
      };
      for (const m of r.moves) {
        const y = this.series!.priceToCoordinate(m.entry_price);
        if (y == null || y < 0 || y > mediaSize.height) continue;
        const startX = this.coordinate(m.entry_time);
        if (startX != null && startX >= 0 && startX <= mediaSize.width) label(`${m.direction === 'long' ? 'L' : 'S'}${m.number} ${clock(m.entry_time)} @ ${m.entry_price.toFixed(2)}`, startX, y - 8, 'left');
        for (let i = m.start_index; i < m.end_index; i++) {
          const step = r.path[i], x1 = this.coordinate(step.time), x2 = this.coordinate(r.path[i + 1].time);
          if (x1 == null || x2 == null || x2 < 0 || x1 > mediaSize.width) continue;
          const targetQ = step.before + (m.direction === 'long' ? 1 : -1) * r.parameters.lot_shares;
          const j = r.inventory.indexOf(targetQ);
          const value = j < 0 ? null : r.values[i][step.state_index][j];
          ctx.strokeStyle = value == null || Math.abs(value) < 1e-8 ? this.colors.neutral : value > 0 ? this.colors.positive : this.colors.negative;
          ctx.globalAlpha = value == null ? .65 : .3 + .7 * Math.min(1, Math.abs(value));
          ctx.lineWidth = 4; ctx.setLineDash(value == null ? [2, 3] : []);
          ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke(); ctx.globalAlpha = 1; ctx.setLineDash([]);
          this.hits.push({ x1: Math.max(0, x1), x2: Math.min(mediaSize.width, x2), y, index: i });
          if (step.before !== step.after) {
            ctx.fillStyle = this.colors.text; ctx.beginPath(); ctx.arc(x1, y, 3, 0, Math.PI * 2); ctx.fill();
            if (i > m.start_index && x1 >= 0 && x1 <= mediaSize.width) label(`${step.after - step.before > 0 ? 'Buy' : 'Sell'} ${Math.abs(step.after - step.before)}`, x1, y - 8, 'left');
          }
        }
        const x1 = this.coordinate(m.entry_time), x2 = this.coordinate(m.exit_time);
        if (x2 != null && x2 >= 0 && x2 <= mediaSize.width) label(`Exit ${clock(m.exit_time)} · ${dollars(m.net_cash)}`, x2, y + 20, 'right');
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

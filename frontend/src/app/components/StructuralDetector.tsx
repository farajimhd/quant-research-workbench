import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time, MouseEventParams } from 'lightweight-charts';
import { api } from '../../api/client';
import { createRoot, type Root } from 'react-dom/client';
import { Modal } from './Modal';
import { Button } from './Button';
// The shared Modal styles currently live in the table stylesheet. Indicators
// must also work on chart-only pages where no table has loaded that stylesheet.
import './DataTable.css';
import './structuralDetector.css';

type Candle = { time: number; endTime?: number; open: number; high: number; low: number; close: number; volume?: number | null };
type Event = { state: string; level: Record<string, unknown>; band_id?: string; encounters?: number; rejection_closes?: number; source_ids?: string[] };
type Cycle = { number: number; attempts: number; failed_attempts: number; depth: number; candles: number; recovery_progress: number };
export type StructuralState = { time: number; effective_at: number; state: string; reason: string;
  technical_signal?: {context?:{regime:string;direction:number;completed_buckets:number;context_through:number|null};phase:string;action:string;reason:string;changed:boolean;execution_eligibility:string;previous_exits?:unknown;management?:string[];evaluations?:unknown[];level_map?:unknown[];level_map_total?:number;setup:null|{barrier?:{lower:number;upper:number};thesis?:{expected_next_move:string;regime:string;invalidation:string;path:unknown[];evidence:unknown[]};assessment?:{expected_next_move:string;base_intact:boolean;pullback_allowance:number};direction:string;lifecycle?:string;exit_class?:string;entry_mode?:string;reentry_basis?:string;progress_r?:number;unrealized_r?:number;giveback_r?:number;pattern?:string;stop:number;initial_stop?:number;target:number|null;entry_reference?:number;armed_at:number;importance?:number;room_basis?:string;reward_risk?:number|null;mfe?:number;mae?:number;remaining_room_atr?:number|null}};
  momentum?: {agreement:string;tags:string[];macd:{watch:string|null;zone:string;entered_from:string|null;trend:string;transitions:string[]};rsi:{value:number|null;period:number;trend:string;zone:string}};
  labels?: Record<string,string[]>;
  summary?: {text:string;family:string;label:string;priority:number;changed:boolean;scope?:string;level?:Record<string,unknown>};
  qualification?: {atr:number|null;ready:boolean;observations:number;period:number};
  volume_analysis?: {status:string;volume:number|null;rate:number|null;color:string;previous_ratio:number|null;relative_volume:number|null;tags:string[];
    divergence:{direction:string;score:number|null;status:string;interpretation:string};
    reversal_candidates:{direction:string;score:number}[];reversal_outcomes:{direction:string;outcome:string}[]};
  session_levels?: {scope:string;status:string;high:{price:number};low:{price:number};ranked_highs:{price:number}[];ranked_lows:{price:number}[];near:{side:string;rank:number;kind:string;price:number}[]};
  global_context: string; global_status: string; local_events: Event[]; global_events: Event[];
  direction?: string; local_bias?: string; global_bias?: string; candle_shape?: { tags: string[] };
  progression?: { tags: string[]; cycle?: Cycle | null; closed_cycle?: Cycle | null; completed_cycles: number;
    crossed: { local: number; global: number }; lost: { local: number; global: number }; accepted_levels: number; accepted_by_scope: {local:number;global:number}; structure_context: string };
  retained_context?: { local: { tracked_bands: number; pending_retests: number }; global_context: { tracked_bands: number; pending_retests: number } };
  focus_interactions?: { local: {primary:Event|null;other_bands:number}; global_context: {primary:Event|null;other_bands:number} };
  body_baseline: number; candle: Candle; local_swings: Record<string, unknown>[];
  confirmed_swings: Record<string, unknown>[]; developing_swings?: Record<string, unknown>;
  macd: { histogram_bps: number; warmup: boolean; active?: boolean; direction?: number; episode_started_at?: number | null }; gap_before: boolean };
type Result = { rows: StructuralState[]; pending_count: number; global_available_count: number;
  global_book: { id: string; fingerprint: string } | null; context_start: number | null };
const defaults = { reversal_bps: 50, volatility_multiple: 2, body_half_life: 5,
  consolidation_body_multiple: .25, proximity_body_multiple: 1, macd_gap_bps: 25, tail_range_fraction: .5, indecision_body_fraction: .2, expansion_body_multiple: 1.5, expansion_body_fraction: .65,
  movement_body_multiple: .1, movement_min_bps: 1, deep_correction_multiple: 2, evidence_memory_candles: 1800, pressure_closes: 2,
  volume_half_life: 5, volume_change_fraction: .1, volume_warmup_candles: 5, session_level_count: 3,
  volume_expansion_multiple: 1.5, volume_divergence_min_score: 30, volume_setup_max_candles: 20,
  atr_period:14,atr_warmup_candles:5,break_body_atr:.3,break_body_fraction:.4,penetration_atr:.1,acceptance_closes:2,
  macd_change_bps:.1,momentum_confirm_closes:2,rsi_period:14,rsi_neutral_band:5,rsi_change_points:.5,
  signal_setup_candles:60,signal_confirmation_candles:3,signal_hold_candles:300,signal_min_reward_risk:1.5,signal_stop_atr:.1,
  signal_zone_atr:.2,signal_min_stop_atr:1,signal_max_risk_atr:4,signal_max_extension_atr:3,signal_min_room_atr:1,signal_progress_candles:20,
  signal_follow_through_candles:5,signal_profit_activation_r:1,signal_profit_giveback_fraction:.4 };
const fields = [ ['reversal_bps', 'Minimum local reversal (bps)', 1, 1000, 1],
  ['signal_setup_candles','Prior reaction evidence window (candles)',1,10000,1],
  ['signal_confirmation_candles','Adverse closes with weak momentum before exit',1,10000,1],
  ['signal_zone_atr','Group nearby levels within ATR multiple',.01,10,.01],
  ['signal_follow_through_candles','Initial follow-through deadline (candles)',1,10000,1],
  ['signal_profit_activation_r','Activate profit protection at initial risk multiple',.1,100,.1],
  ['signal_profit_giveback_fraction','Maximum close-progress giveback fraction',.01,.99,.01],
  ['signal_min_stop_atr','Minimum protection distance / prior ATR',.1,100,.1],
  ['signal_max_risk_atr','Maximum initial stop distance / ATR',.1,100,.1],
  ['signal_max_extension_atr','Maximum extension from structure / ATR',.1,100,.1],
  ['signal_min_room_atr','Minimum room to meaningful barrier / ATR',.1,100,.1],
  ['signal_progress_candles','No-progress window with weak momentum (candles)',1,10000,1],
  ['signal_hold_candles','Technical holding time limit (candles)',1,10000,1],
  ['signal_min_reward_risk','Minimum target distance / stop distance',.1,100,.1],
  ['signal_stop_atr','Stop buffer / setup ATR',.01,100,.01],
  ['volatility_multiple', 'Local volatility multiple', .1, 10, .1],
  ['body_half_life', 'Body average half-life (candles)', 1, 100, 1],
  ['proximity_body_multiple', 'Level proximity body multiple', .1, 10, .1],
  ['macd_gap_bps', 'MACD watch-zone boundary (bps)', .1, 1000, 1],
  ['macd_change_bps', 'MACD meaningful separation change (bps)', .01, 100, .01],
  ['momentum_confirm_closes', 'MACD direction persistence (candles)', 1, 20, 1],
  ['rsi_period', 'Wilder RSI period (candles)', 2, 200, 1],
  ['rsi_neutral_band', 'RSI neutral half-band around 50', .1, 19.9, .1],
  ['rsi_change_points', 'RSI meaningful change (points)', .1, 100, .1],
  ['tail_range_fraction', 'Tail minimum fraction of range', .1, 1, .05],
  ['indecision_body_fraction', 'Indecision maximum body fraction', .01, 1, .05],
  ['expansion_body_multiple', 'Expansion recent-body multiple', .1, 10, .1],
  ['expansion_body_fraction', 'Expansion minimum body fraction', .1, 1, .05],
  ['movement_body_multiple', 'Meaningful close progress · recent body multiple', .01, 2, .01],
  ['movement_min_bps', 'Minimum close progress (bps)', .01, 100, .1],
  ['deep_correction_multiple', 'Deep correction · starting ATR multiple', .5, 20, .5],
  ['evidence_memory_candles', 'Retain absent structural evidence (candles)', 10, 20000, 1],
  ['pressure_closes', 'Persistent pressure · rejection closes', 2, 20, 1],
  ['volume_half_life', 'Volume rate average half-life (candles)', 1, 100, 1],
  ['volume_change_fraction', 'Meaningful volume change (fraction)', .01, 1, .01],
  ['volume_warmup_candles', 'Volume warmup (observed candles)', 1, 100, 1],
  ['session_level_count', 'Ranked session swing highs / lows', 1, 10, 1],
  ['volume_expansion_multiple', 'High volume · prior average multiple', 1, 20, .1],
  ['volume_divergence_min_score', 'Exhaustion candidate minimum evidence score', 1, 100, 1],
  ['volume_setup_max_candles', 'Reversal candidate lifetime (candles)', 1, 1000, 1],
  ['atr_period','Prior ATR window (candles)',5,200,1],
  ['atr_warmup_candles','Prior ATR warmup (candles)',1,200,1],
  ['break_body_atr','Break minimum body / prior ATR',.01,10,.05],
  ['break_body_fraction','Break minimum body / range',.01,1,.05],
  ['penetration_atr','Meaningful penetration / prior ATR',.01,5,.01],
  ['acceptance_closes','Later closes to accept a break',2,20,1] ] as const;
const human = (s: string) => s.replaceAll('_', ' ');
const volumeText = (s:string) => ({volume_falling:'volume ↓',volume_rising:'volume ↑',volume_stable:'volume steady',
  red_to_green:'red → green',green_to_red:'green → red',price_volume_expansion:'price + volume expansion',
  countermove_volume_fading:'fading pullback volume',recovery_after_countermove:'recovery',recovery_at_session_level:'at session level',
  observed_hod_cross:'HOD cross',observed_hod_rejection:'HOD rejection',observed_hod_test:'HOD test',observed_hod_approach:'near HOD',
  observed_lod_cross:'LOD cross',observed_lod_rejection:'LOD rejection',observed_lod_test:'LOD test',observed_lod_approach:'near LOD'}[s] || human(s));
const labelFields = { summary: 'Important or changed', everySummary:'Every candle summary', signal:'Technical signal', signalReason:'Signal reason', state: 'Movement', direction: 'Direction', local: 'Local interactions', global: 'Global interactions',
  localBias: 'Local swing trend', globalBias: 'Global swing trend', shape: 'Candle shape', macd: 'MACD context', rsi: 'RSI context', momentum: 'Momentum agreement', body: 'Recent body size', source: 'Global availability',
  progression: 'Progression', cycle: 'Recovery cycle', levelProgress: 'Levels crossed / lost', context: 'Combined structure context', retained: 'Retained structural history',
  localDetails: 'All local band details', globalDetails: 'All global band details',
  volume: 'Volume', volumeTrend: 'Volume progression', divergence: 'Volume divergence score', reversal: 'Reversal evidence progression', dayLevels: 'Observed HOD / LOD', rankedHighs: 'Ranked session highs', rankedLows: 'Ranked session lows', nearDayLevel: 'Session level interaction' };
type LabelField = keyof typeof labelFields;
export type LabelRows = LabelField[][];
const defaultRows: LabelRows = [['signal']];
const entryExitActions = new Set(['long_enter','short_enter','long_exit','short_exit']);
const aboveCandle = (row: StructuralState) => row.technical_signal?.action==='short_enter' || row.technical_signal?.action==='long_exit';
const decisionActions = new Set([...entryExitActions,'long_hold','short_hold']);
const price = (n: unknown) => typeof n==='number' ? n.toLocaleString('en-US',{maximumFractionDigits:6}) : '?';
const eventText = (events: Event[]) => [...new Map(events.map(e => [e.state+':'+(e.band_id || JSON.stringify(e.level)),
  `${human(e.state)} ${e.level.lower!=null ? price(e.level.lower)+'–'+price(e.level.upper) : price(e.level.price)}${e.encounters ? ' #'+e.encounters : ''}${(e.rejection_closes || 0)>1 ? ' · '+e.rejection_closes+' rejection closes' : ''}`])).values()].join(' · ') || 'none';
export function structuralLabelRows(row: StructuralState, rows: LabelRows) {
  const p=row.progression, c=p?.cycle || p?.closed_cycle, v=row.volume_analysis, d=v?.divergence, s=row.session_levels;
  const focused=(scope:'local'|'global_context',fallback:Event[]) => {
    const focus=row.focus_interactions?.[scope];
    return focus ? eventText(focus.primary ? [focus.primary] : [])+(focus.other_bands ? ` · +${focus.other_bands} other bands` : '') : eventText(fallback);
  };
  const values: Record<LabelField,string> = { summary: row.summary?.text || human(row.state),everySummary:row.summary?.text || human(row.state), state: human(row.state), direction: row.direction || 'unknown',
    signal:row.technical_signal ? human(row.technical_signal.action).replace(/^./, c=>c.toUpperCase()) : 'Signal unavailable',
    signalReason:row.technical_signal ? human(row.technical_signal.reason) : 'Signal unavailable',
    volume: v?.status==='available' ? `${price(v.volume)} vol · ${v.color} · ${v.relative_volume==null ? 'RV warming up' : v.relative_volume.toFixed(2)+'× RV'}` : 'Volume unavailable',
    volumeTrend: v?.status==='available' ? v.tags.map(volumeText).join(' · ') || 'Volume: no prior comparison' : 'Volume unavailable',
    divergence: d?.status==='available' ? d.direction==='none' ? 'No qualified divergence' : `${d.direction} div. ${d.score?.toFixed(0) ?? '—'}/100` : `Divergence ${human(d?.status || 'unavailable')}`,
    reversal: v ? [...v.reversal_candidates.map(c=>`${c.direction} candidate ${c.score.toFixed(0)}/100`),...v.reversal_outcomes.map(o=>o.direction+' '+human(o.outcome))].join(' · ') || 'No active reversal evidence' : 'Reversal evidence unavailable',
    dayLevels: s?.status==='partial_session' ? `Obs. HOD ${price(s.high.price)} · LOD ${price(s.low.price)}` : 'Session extremes unavailable',
    rankedHighs: s ? 'Observed highs: '+(s.ranked_highs.map((l,i)=>`#${i+1} ${price(l.price)}`).join(' · ') || 'awaiting confirmed swings') : 'Session highs unavailable',
    rankedLows: s ? 'Observed lows: '+(s.ranked_lows.map((l,i)=>`#${i+1} ${price(l.price)}`).join(' · ') || 'awaiting confirmed swings') : 'Session lows unavailable',
    nearDayLevel: s ? s.near.map(l=>`${l.kind==='running_extreme' ? 'Prior '+(l.side==='high'?'HOD':'LOD') : l.side+' #'+l.rank} ${price(l.price)}`).join(' · ') || 'Away from observed session levels' : 'Session levels unavailable',
    local: 'L: '+focused('local',row.local_events), global: 'G: '+focused('global_context',row.global_events),
    localDetails:'Local details: '+eventText(row.local_events), globalDetails:'Global details: '+eventText(row.global_events),
    localBias: 'Local: '+(row.local_bias || 'unknown'), globalBias: 'Global: '+(row.global_bias || 'unknown'),
    shape: (row.candle_shape?.tags || ['unavailable']).map(human).join(' · '),
    macd: row.macd.warmup ? 'MACD warming up' : `MACD ${row.macd.histogram_bps.toFixed(1)} bps${row.momentum ? ' · '+human(row.momentum.macd.watch || row.momentum.macd.zone) : ''}`,
    rsi: row.momentum?.rsi.value==null ? 'RSI warming up' : `RSI ${row.momentum.rsi.value.toFixed(1)} · ${row.momentum.rsi.trend}`,
    momentum: row.momentum ? `Momentum ${human(row.momentum.agreement)}` : 'Momentum unavailable',
    body: `Body ${row.body_baseline.toFixed(4)}`, source: human(row.global_status),
    progression: p?.tags.filter(tag => tag!=='levels_crossed' || !p.tags.includes('multiple_levels_crossed')).map(human).join(' · ') || 'unavailable',
    cycle: c ? `Cycle ${c.number} · attempt ${c.attempts} · ${c.failed_attempts} failed · depth ${price(c.depth)} · ${c.candles} candles · ${(c.recovery_progress*100).toFixed(0)}% recovered` : `${p?.completed_cycles || 0} completed cycles`,
    levelProgress: p ? `Crossed L${p.crossed.local}/G${p.crossed.global} · lost L${p.lost.local}/G${p.lost.global} · accepted L${p.accepted_by_scope?.local ?? 0}/G${p.accepted_by_scope?.global ?? 0}` : 'unavailable',
    context: `Local ${row.local_bias || 'unknown'} / global ${row.global_bias || 'unknown'}${row.local_bias==='bullish' && row.global_bias==='bearish' || row.local_bias==='bearish' && row.global_bias==='bullish' ? ' · opposing trends' : ''}`,
    retained: row.retained_context ? `History L${row.retained_context.local.tracked_bands}/G${row.retained_context.global_context.tracked_bands} bands (includes distant levels)` : 'unavailable' };
  return rows.filter(fields => fields.length).map(fields => fields.map(field => values[field]).join(' · '));
}
function readRows(value: unknown): LabelRows {
  return Array.isArray(value) ? value.slice(0,10).map(row => Array.isArray(row) ? row.filter((key): key is LabelField => key==='signal' || key==='signalReason') : []) : defaultRows;
}
/** The same label component is used on candles and in the settings preview. */
export function StructuralCandleLabel({ row, layout, onInspect }: { row: StructuralState; layout: LabelRows; onInspect?:()=>void }) {
  if (!entryExitActions.has(row.technical_signal?.action || '')) return null;
  if (!layout.some(fields => fields.length)) return null;
  const compact=layout.length===1 && layout[0].length===1 && layout[0][0]==='signal';
  const action=row.technical_signal?.action || 'wait';
  const kind=action.endsWith('_exit') ? 'exit' : action==='cancel_setup' ? 'cancel' : action.endsWith('_watch') ? 'watch' : action.startsWith('long_') ? 'long' : action.startsWith('short_') ? 'short' : 'neutral';
  const buy=action==='long_enter' || action==='short_exit';
  const sell=action==='short_enter' || action==='long_exit';
  return <button type="button" className="structural-candle-label" data-compact={compact} data-signal-tone={kind} data-candle-time={row.time}
    title={`${human(row.technical_signal?.action || 'wait')} · Click for candle evidence`} aria-label={`Inspect candle: ${human(row.technical_signal?.action || 'wait')}`} onClick={onInspect}>
    {buy && <span className="structural-signal-marker" data-placement="above" aria-hidden="true">▲</span>}
    <div className="structural-signal-label-body">{structuralLabelRows(row,layout).map((text,index) => <div className="structural-candle-label-row" key={index}>{text}</div>)}</div>
    {sell && <span className="structural-signal-marker" data-placement="below" aria-hidden="true">▼</span>}
  </button>;
}

function CandleInspector({row,onClose,onMove,hasPrevious,hasNext}:{row:StructuralState;onClose:()=>void;onMove:(step:number)=>void;hasPrevious:boolean;hasNext:boolean}) {
  return <Modal title="Candle evidence" className="structural-candle-inspector" onClose={onClose}>
    <div className="structural-inspector-navigation"><Button disabled={!hasPrevious} onClick={()=>onMove(-1)}>Previous candle</Button><Button disabled={!hasNext} onClick={()=>onMove(1)}>Next candle</Button></div>
    <div className="structural-inspector-body">
    <p>{new Date(row.time*1000).toISOString()} · Completed {new Date(row.effective_at*1000).toISOString()}</p>
    <p><strong>{row.summary?.text || human(row.state)}</strong> · {human(row.reason)}</p>
    {row.technical_signal && <section><h3>Technical signal · {human(row.technical_signal.action)}</h3><p>{human(row.technical_signal.phase)} · {human(row.technical_signal.reason)}</p>
      {row.technical_signal.setup && <><p>{human(row.technical_signal.setup.pattern || 'setup')} · Reference entry {price(row.technical_signal.setup.entry_reference)} · Initial stop {price(row.technical_signal.setup.initial_stop)} · Current stop {price(row.technical_signal.setup.stop)} · Next barrier {row.technical_signal.setup.barrier ? price(row.technical_signal.setup.direction==='long' ? row.technical_signal.setup.barrier.lower : row.technical_signal.setup.barrier.upper) : row.technical_signal.setup.target==null ? 'No known barrier' : price(row.technical_signal.setup.target)}</p><p>Lifecycle {human(row.technical_signal.setup.lifecycle || 'initial follow through')} · Exit context {human(row.technical_signal.setup.exit_class || 'not exited')} · Entry {human(row.technical_signal.setup.entry_mode || 'aligned')} · Re-entry {human(row.technical_signal.setup.reentry_basis || 'first entry')} · Close progress {row.technical_signal.setup.progress_r?.toFixed(2) ?? '0'} R · Current {row.technical_signal.setup.unrealized_r?.toFixed(2) ?? '0'} R · Giveback {row.technical_signal.setup.giveback_r?.toFixed(2) ?? '0'} R</p><p>Importance {row.technical_signal.setup.importance ?? '—'} · Initial reward/risk {row.technical_signal.setup.reward_risk?.toFixed(2) ?? 'Unknown'} · Favorable excursion {price(row.technical_signal.setup.mfe)} · Adverse excursion {price(row.technical_signal.setup.mae)}</p></>}
      {row.technical_signal.context && <p>Broader context: {human(row.technical_signal.context.regime)} | {row.technical_signal.context.direction===1 ? 'Uptrend' : row.technical_signal.context.direction===-1 ? 'Downtrend' : 'No established direction'} | {row.technical_signal.context.completed_buckets} completed 30-second observations</p>}
      {row.technical_signal.setup?.thesis && <><p>Planned target: {price(row.technical_signal.setup.target)} | Room basis: {human(row.technical_signal.setup.room_basis || 'unknown')}</p><p>Entry thesis: {human(row.technical_signal.setup.thesis.expected_next_move)} | Invalidated by {human(row.technical_signal.setup.thesis.invalidation)}</p>{row.technical_signal.setup.assessment && <p>Current expectation: {human(row.technical_signal.setup.assessment.expected_next_move)} | Base {row.technical_signal.setup.assessment.base_intact ? 'intact' : 'failed'} | Pullback allowance {price(row.technical_signal.setup.assessment.pullback_allowance)}</p>}<details><summary>Entry thesis, level path and prior sequence</summary><pre>{JSON.stringify(row.technical_signal.setup.thesis,null,2)}</pre></details></>}
      {!!row.technical_signal.management?.length && <p>Management: {row.technical_signal.management.map(human).join(' · ')}</p>}
      <details><summary>Level importance, room and rejected entries</summary><pre>{JSON.stringify({previous_exits:row.technical_signal.previous_exits,zone_count:row.technical_signal.level_map_total,nearest_zones:row.technical_signal.level_map,evaluations:row.technical_signal.evaluations},null,2)}</pre></details>
      <p>Hypothetical signal only. Spread, quote freshness and borrow eligibility are not assessed. Boundary touches are reported after candle close; these are not fills.</p></section>}
    <p>Prior ATR {row.qualification?.atr==null ? '—' : price(row.qualification.atr)} · {row.qualification?.observations ?? 0}/{row.qualification?.period ?? '—'} candles · {row.qualification?.ready ? 'ready' : 'warming up'}</p>
    {row.momentum && <><p>{structuralLabelRows(row,[['macd','rsi','momentum']])[0]}</p><p>MACD 12/26/9 and RSI {row.momentum.rsi.period} use completed chart candles. Watch zones and RSI extremes are context, not entry or reversal confirmation.</p><details><summary>Momentum values and transitions</summary><pre>{JSON.stringify(row.momentum,null,2)}</pre></details></>}
    <table><thead><tr><th>Family</th><th>Labels</th></tr></thead><tbody>{Object.entries(row.labels || {movement:[row.state]}).map(([family,labels])=><tr key={family}><th>{human(family)}</th><td>{labels.length ? labels.map(human).join(' · ') : 'None'}</td></tr>)}</tbody></table>
    <p>Local / global identifies the source. Outer / internal describes position in the prior enclosing range; unknown means that range was unavailable. Scores describe evidence, not probabilities.</p>
    <details><summary>Level interactions and qualification reasons</summary><pre>{JSON.stringify({local:row.local_events,global:row.global_events},null,2)}</pre></details>
    <details><summary>All candle evidence</summary><pre>{JSON.stringify(row,null,2)}</pre></details>
    </div>
  </Modal>;
}
const EMPTY: StructuralState[] = [];
function duration(timeframe: string) {
  if (timeframe === '1M' || timeframe === '1Y') return 86400;
  const match = /^(\d+)(ms|s|m|h|d|w)$/.exec(timeframe.toLowerCase());
  return match ? Number(match[1])*({ ms: .001, s: 1, m: 60, h: 3600, d: 86400, w: 604800 }[match[2]] || 0) : 0;
}
function candleEnd(candle: Candle, timeframe: string, seconds: number) {
  if (candle.endTime != null && Number.isFinite(candle.endTime)) return candle.endTime;
  const at = new Date(candle.time*1000);
  if (timeframe === '1M') { at.setUTCMonth(at.getUTCMonth()+1); return at.getTime()/1000; }
  if (timeframe === '1Y') { at.setUTCFullYear(at.getUTCFullYear()+1); return at.getTime()/1000; }
  return candle.time+seconds;
}

export function useStructuralDetector(ticker: string, timeframe: string, candles: Candle[], asOf: string | undefined, storageKey: string, splitAdjusted: boolean, volumes?: {time:number;value:number}[]) {
  const [stored, setStored] = useState<{ key: string; enabled: boolean; settings: typeof defaults; labelRows: LabelRows }>({ key: '', enabled: false, settings: defaults, labelRows: defaultRows });
  useEffect(() => {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey+'.structural-detector') || '{}');
      setStored({ key: storageKey, enabled: value.enabled === true, settings: { ...defaults, ...value.settings }, labelRows: value.labelVersion===3 ? readRows(value.labelRows) : defaultRows });
    } catch { setStored({ key: storageKey, enabled: false, settings: defaults, labelRows: defaultRows }); }
  }, [storageKey]);
  const enabled = stored.key === storageKey && stored.enabled;
  const change = (next: typeof stored) => { setStored(next); localStorage.setItem(storageKey+'.structural-detector', JSON.stringify({...next,labelVersion:3})); };
  const [state, setState] = useState<{ identity: string; result?: Result; error?: string; busy?: boolean }>({ identity: '' });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const seconds = duration(timeframe);
  const rawCutoff = asOf && Number.isFinite(Date.parse(asOf)) ? Math.min(Date.now(), Date.parse(asOf))/1000 : Date.now()/1000;
  const cutoff = Math.min(rawCutoff, candles.length ? candleEnd(candles.at(-1)!, timeframe, seconds) : rawCutoff);
  const identity = `${ticker}:${timeframe}:${splitAdjusted}:${JSON.stringify(stored.settings)}`;
  // Forecasts never enter this request. End-time gating excludes a forming bar.
  const closed = useMemo(() => candles.filter(c => candleEnd(c, timeframe, seconds) <= cutoff), [candles, timeframe, seconds, cutoff]);
  const volumeByTime = useMemo(() => new Map((volumes || []).map(v=>[v.time,v.value])),[volumes]);
  const body = useMemo(() => enabled ? JSON.stringify({ ticker, timeframe, as_of: closed.length ? candleEnd(closed.at(-1)!, timeframe, seconds) : cutoff,
    candles: closed.map(c => ({ time: c.time, end: candleEnd(c, timeframe, seconds), open: c.open, high: c.high, low: c.low, close: c.close, volume: volumeByTime.get(c.time) ?? c.volume ?? null })),
    settings: stored.settings, split_adjusted: splitAdjusted }) : '', [enabled, ticker, timeframe, closed, seconds, cutoff, stored.settings, splitAdjusted, volumeByTime]);
  const latestBody = useRef(body); latestBody.current = body;
  useEffect(() => {
    if (!enabled || !seconds) return;
    const controller = new AbortController();
    let timer = 0, previousBody = '';
    const refresh = async () => {
      const next = latestBody.current;
      if (next && next !== previousBody) {
        previousBody = next;
        setState(s => ({ ...s, busy: true, error: undefined }));
        try {
          const result = await api<Result>('/api/indicators/structural-detector', { method: 'POST', body: next, signal: controller.signal, timeoutMs: 120000 });
          if (!controller.signal.aborted) setState({ identity, result });
        } catch (error) {
          if (!controller.signal.aborted) setState({ identity, error: String(error) });
        }
      }
      if (!controller.signal.aborted) timer = window.setTimeout(refresh, 500);
    };
    timer = window.setTimeout(refresh, 150);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [enabled, identity, seconds]);
  const result = state.identity === identity ? state.result : undefined;
  const rows = useMemo(() => {
    const times = new Set(closed.map(c => c.time));
    return enabled ? (result?.rows || EMPTY).filter(row => row.effective_at <= cutoff && times.has(row.time)) : EMPTY;
  }, [enabled, result, cutoff, closed]);
  const status = !seconds ? 'Unsupported candle duration' : state.error || (state.busy ? 'Updating candle states…' :
    result ? `${rows.length} closed candles · ${candles.length-closed.length} pending · V6 context ${result.global_available_count}/${rows.length}` : 'Enable to classify loaded candles');
  const checkbox = <label className="chart-setting-row structural-detector-option"><span>Structural detector <small>Local swings + global V6 context · every closed candle</small></span>
    <input type="checkbox" aria-label="Structural detector" checked={enabled} onChange={e => change({ ...stored, key: storageKey, enabled: e.target.checked })} /></label>;
  const controls = <>
    <button type="button" className="toolbar-button structural-detector-toolbar" aria-pressed={enabled} onClick={() => change({ ...stored, key: storageKey, enabled: !enabled })}>Structural detector</button>
    <button type="button" className="toolbar-button structural-detector-toolbar" aria-label="Structural detector settings" onClick={() => setSettingsOpen(true)}>Detector settings</button>
    {enabled ? <span className="chart-data-status" role="status">{state.error ? 'Detector unavailable' : state.busy ? 'Detecting·' : `${rows.length} states${result?.global_available_count ? ' · V6' : ' · global unavailable'}`}</span> : null}
    {enabled && decisionActions.has(rows.at(-1)?.technical_signal?.action || '') && <span className="chart-data-status" title="Causal decision for a hypothetical position; not an order">Signal: {human(rows.at(-1)!.technical_signal!.action)}</span>}
    {settingsOpen ? <Modal className="structural-detector-settings" title="Structural detector settings" onClose={() => setSettingsOpen(false)}>
      <div className="structural-detector-settings-body">
        {checkbox}<p className="chart-settings-help" role="status">{status}</p>
        <section className="chart-settings-section"><h3>Candle labels</h3>
          <p className="chart-settings-help">Short entry and long exit appear above candles; long entry and short exit appear below. The toolbar shows the current hold decision while a hypothetical position is active. No qualifying entry means no chart signal.</p>
          <p className="chart-settings-help">Signals appear on the next available candle after the decision closes. Until that candle exists, the decision remains in the toolbar. Clicking a signal opens evidence from its originating candle.</p>
          <p className="chart-settings-help">Setup evidence remains internal and can be inspected on a candle. Add Signal reason for entry/exit explanations. Initial references stay fixed; confirmed structure can tighten protection for subsequent candles. Trade execution eligibility is not assessed.</p>
          <p className="chart-settings-help">Initiation, continuation and reversal entries use meaningful level zones and available room. Initial risk references remain recorded. Current protection can tighten after confirmed structure; accepted barriers advance to the next level. Open room is not a price forecast. Inspect a candle for entry rejections and management reasons.</p>
          <p className="chart-settings-help">New positions must follow through. Established positions can hold normal pullbacks; profitable progress activates a close-based protective floor. Strong rejection at important levels or a confirmed opposing reversal can close without a momentum flip. Re-entry requires the prior exit's structure to be repaired, its barrier accepted, or a new confirmed pullback base. R means initial reference risk, not realized profit.</p>
          <p className="chart-settings-help">Entry/exit markers sit outside the text box: ▲ above long entry and short exit; ▼ below short entry and long exit. Long entries use positive color, short entries negative color, and exits caution color.</p>
          <p className="chart-settings-help">A reversal candidate requires divergence near an extreme or structural rejection. A later close must cross its candle boundary with a matching structural break to confirm. Candidates expire or invalidate on volume-supported continuation; past labels stay unchanged.</p>
          {stored.labelRows.map((row,index) => <fieldset className="structural-label-row-config" key={index}>
            <legend>Row {index+1}</legend>
            <div className="structural-label-fields">{Object.entries(labelFields).filter(([key])=>key==='signal' || key==='signalReason').map(([key,label]) => <label className="chart-setting-toggle" key={key}>
              <input type="checkbox" aria-label={`Row ${index+1}: ${label}`} checked={row.includes(key as LabelField)} onChange={e => change({ ...stored,
                labelRows: stored.labelRows.map((current,i) => i!==index ? current : e.target.checked ? [...current,key as LabelField] : current.filter(v => v!==key)) })} />{label}
            </label>)}</div>
            <Button variant="ghost" onClick={() => change({ ...stored,labelRows: stored.labelRows.filter((_,i) => i!==index) })}>Remove row {index+1}</Button>
          </fieldset>)}
          <div className="structural-label-actions"><Button disabled={stored.labelRows.length>=10} onClick={() => change({ ...stored,labelRows: [...stored.labelRows,[]] })}>Add row</Button>
            <Button variant="ghost" onClick={() => change({ ...stored,labelRows: defaultRows })}>Reset label rows</Button></div>
          {rows.length ? <div className="structural-label-preview"><span>Preview · latest completed candle</span><StructuralCandleLabel row={rows.at(-1)!} layout={stored.labelRows} /></div> : null}
        </section>
        <section className="chart-settings-section"><h3>Detection</h3>
          <p className="chart-settings-help">Local swings and available global V6 structure are evaluated at every close, in both directions. Candle shapes are evidence, not automatic reversal signals. Changes recalculate the loaded history.</p>
          {fields.map(([key,label,min,max,step]) => <label className="chart-setting-row" key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={stored.settings[key]} onChange={e => {
            const value=e.target.valueAsNumber; if (Number.isFinite(value) && value>=min && value<=max) change({ ...stored,settings: { ...stored.settings,[key]:value } });
          }} /></label>)}
        </section>
      </div>
      <div className="structural-label-actions"><Button variant="primary" onClick={() => setSettingsOpen(false)}>Done</Button></div>
    </Modal> : null}
  </>;
  return { rows, checkbox, controls, enabled, status, cutoff, labelRows: stored.labelRows,
    menuItem: {id:'indicator.structural_detector',title:'Structural detector',group:'price_action',category:'Closed-candle states',selected:enabled,
      onToggle:()=>change({...stored,key:storageKey,enabled:!enabled}),onConfigure:()=>setSettingsOpen(true)} };
}

function LabelCommit({onCommit,children}:{onCommit:()=>void;children:ReactNode}) {
  useLayoutEffect(onCommit);
  return <>{children}</>;
}

/** Geometry follows every chart draw; React updates content only. */
export class StructuralDetectorPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<'Candlestick'> | null = null;
  private chart: IChartApi | null = null;
  private update?: () => void;
  private rows: StructuralState[] = EMPTY;
  private candles: Candle[] = [];
  private cutoff = 0;
  private layout: LabelRows = defaultRows;
  private coordinate: (t:number) => number | null = () => null;
  private host?: HTMLDivElement;
  private root?: Root;
  private frame = 0;
  private labels: {row:StructuralState;displayTime:number;x:number;y:number}[]=[];
  private signature='';
  private dirty=true;
  private selected: number | null = null;
  private inspect = (row:StructuralState) => {this.selected=row.time;this.dirty=true;this.update?.();};
  private position = () => {
    if (!this.host) return;
    const coordinates=new Map(this.labels.map(label=>[label.row.time,label]));
    const boxes=Array.from(this.host.querySelectorAll<HTMLElement>('.structural-label-anchor'),node=>({node,
      point:coordinates.get(Number(node.dataset.sourceTime)),width:node.offsetWidth,height:node.offsetHeight}));
    const occupied:{left:number;right:number;top:number;bottom:number}[]=[];
    boxes.sort((a,b)=>Number(b.node.dataset.priority)-Number(a.node.dataset.priority)).forEach(({node,point,width,height})=>{
      if (!point) {node.style.visibility='hidden';return;}
      const top=aboveCandle(point.row) ? point.y-height : point.y;
      const rect={left:point.x-width/2,right:point.x+width/2,top,bottom:top+height};
      const overlap=occupied.some(r=>rect.left<r.right+2 && rect.right>r.left-2 && rect.top<r.bottom+2 && rect.bottom>r.top-2);
      node.style.left=`${point.x}px`;node.style.top=`${top}px`;node.style.visibility=overlap?'hidden':'visible';
      if (!overlap) occupied.push(rect);
    });
  };
  private click = (event:MouseEventParams<Time>) => { const row=this.rows.find(r=>r.time===event.time);if(row)this.inspect(row); };
  private readonly view: IPrimitivePaneView = { zOrder: () => 'top', renderer: () => ({ draw: target => {
    target.useMediaCoordinateSpace(({mediaSize}) => {
      if (!this.series || !this.chart) return;
      if (this.host) {
        this.host.style.left=`${this.chart.priceScale('left').width()}px`;
        this.host.style.right=`${this.chart.priceScale('right').width()}px`;
        this.host.style.height=`${mediaSize.height}px`;
      }
      const range=this.chart.timeScale().getVisibleRange();
      const labels: { row: StructuralState; displayTime:number; x:number; y:number }[]=[];
      for (const row of this.rows) {
        if (!entryExitActions.has(row.technical_signal?.action || '')) continue;
        // Find the first real candle on which the completed decision is known.
        // It may be forming; no next-candle prices enter the signal calculation.
        let low=0,high=this.candles.length;
        while(low<high) {const mid=(low+high)>>>1;if(this.candles[mid].time<row.effective_at)low=mid+1;else high=mid;}
        const destination=this.candles[low];
        if (!destination || destination.time>this.cutoff) continue;
        const displayTime=destination.time;
        if (range && typeof range.from==='number' && displayTime<range.from) continue;
        if (range && typeof range.to==='number' && displayTime>range.to) break;
        const above=aboveCandle(row);
        const x=this.coordinate(displayTime), y=this.series.priceToCoordinate(above ? destination.high : destination.low);
        if (x!=null && y!=null && x>=0 && x<=mediaSize.width && y>=0 && y<mediaSize.height) labels.push({row,displayTime,x,y:y+(above ? -5 : 5)});
      }
      this.labels=labels;
      this.position();
      const signature=labels.map(label=>`${label.row.time}:${label.displayTime}`).join(',');
      if (signature!==this.signature) {this.signature=signature;this.dirty=true;}
      // Do not cancel pending content commits during continuous interaction.
      if (this.dirty && !this.frame) this.frame=requestAnimationFrame(() => {
        this.frame=0;this.dirty=false;
        const selectedIndex=this.rows.findIndex(row=>row.time===this.selected), selected=this.rows[selectedIndex];
        this.root?.render(<LabelCommit onCommit={this.position}>{this.labels.map(({row,displayTime,x,y}) => <div className="structural-label-anchor" data-time={displayTime} data-source-time={row.time} data-effective-at={row.effective_at} data-priority={row.technical_signal?.action.endsWith('_enter') || row.technical_signal?.action.endsWith('_exit') ? 100 : 20} key={row.time} style={{left:x,top:y}}><StructuralCandleLabel row={row} layout={this.layout} onInspect={()=>this.inspect(row)} /></div>)}
          {selected ? <CandleInspector row={selected} hasPrevious={selectedIndex>0} hasNext={selectedIndex<this.rows.length-1} onClose={()=>{this.selected=null;this.dirty=true;this.update?.();}} onMove={step=>this.inspect(this.rows[selectedIndex+step])} /> : null}</LabelCommit>);
      });
    });
  } }) };
  attached({series,chart,requestUpdate}: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) {
    this.series=series as ISeriesApi<'Candlestick'>; this.chart=chart; this.update=requestUpdate;
    this.host=document.createElement('div'); this.host.className='structural-label-layer';
    chart.chartElement().appendChild(this.host); this.root=createRoot(this.host);
    chart.subscribeClick(this.click);
  }
  detached() { this.chart?.unsubscribeClick(this.click); cancelAnimationFrame(this.frame); this.frame=0;this.dirty=true;this.signature=''; this.root?.unmount(); this.host?.remove(); this.root=undefined; this.host=undefined; this.series=null; this.chart=null; this.update=undefined; }
  paneViews() { return [this.view]; }
  autoscaleInfo() {
    if (!this.rows.length || !this.layout.some(fields => fields.length)) return null;
    const margin=Math.min(180,this.layout.filter(fields=>fields.length).length*28);
    return { priceRange: null, margins: { above: margin, below: margin } };
  }
  setState(rows:StructuralState[],coordinate:(t:number)=>number|null,layout:LabelRows,candles:Candle[],cutoff:number) { if(this.rows!==rows || this.layout!==layout)this.dirty=true; this.rows=rows;this.candles=candles;this.cutoff=cutoff; if(!rows.some(r=>r.time===this.selected))this.selected=null; this.coordinate=coordinate; this.layout=layout; this.update?.(); }
}

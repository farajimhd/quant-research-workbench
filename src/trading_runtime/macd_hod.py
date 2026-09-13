"""Completed-100ms MACD episodes with causal HOD selection and broker protection.

No range/ATR proximity filter. Entry references are immutable; acquired-position
management and episode memory are separate from permission to acquire shares.
"""
from copy import deepcopy
from dataclasses import replace
from math import floor, isfinite
from zoneinfo import ZoneInfo

from .structural_recovery import DEFAULTS as QUALITY_DEFAULTS, LIQUIDITY_181, tradability

CONTRACT = 'macd-hod-100ms-1'
BOOK_VERSION = 'causal-swing-closing-book-6'
BOOK_VERSIONS = (BOOK_VERSION, 'causal-level-book-v7-mle-1')
DEFAULTS = dict(exit_gap_bps=10., stop_buffer_bps=5., reentry_buffer_bps=5.,
                minimum_selection_score=30., minimum_reward_risk=1.5,
                cost_bps_per_side=5., maximum_chase_bps=15., risk_fraction=.005,
                maximum_quantity=10000., confirmation_lifetime_ms=100.,
                maximum_quote_age_ms=1000., maximum_source_age_ms=2000.,
                target_body_multiple=2., target_gap_multiple=1.)
NY = ZoneInfo('America/New_York')


def configure(p):
    if p.get('macd_hod_contract') != CONTRACT:
        raise ValueError('Unknown 100ms MACD HOD contract')
    if any(p.get(k) for k in ('structural_recovery_contract','v5_breakout_contract','swing_gap_contract','swing_evidence_contract','swing_momentum_contract')):
        raise ValueError('MACD HOD must not compose another entry policy')
    raw = p.get('macd_hod', {})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown MACD HOD setting')
    settings = dict(DEFAULTS, **raw)
    if any(type(v) not in (int,float) or not isfinite(v) or v <= 0 for v in settings.values()):
        raise ValueError('MACD HOD settings must be finite and positive')
    if settings['risk_fraction'] > 1 or settings['confirmation_lifetime_ms'] > 100:
        raise ValueError('Invalid risk or completed-100ms acquisition lifetime')
    p['macd_hod'] = settings
    p['liquidity_admission'] = dict(LIQUIDITY_181, **p.get('liquidity_admission', {}))
    if not p['liquidity_admission']['enabled']:
        raise ValueError('Tradability is mandatory')
    p['entry_candle_confirmation']['enabled'] = False
    p['structural_entry']['enabled'] = False
    p['protection']['trailing']['enabled'] = False
    p['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=False)
    p['momentum_management']['macd_backstop']['enabled'] = False


def levels(o, s, before):
    """Only pre-trigger certified zones; overlapping representations count once."""
    rows = []
    for raw in o.structural_resistance_levels:
        if (raw.get('book_version') not in BOOK_VERSIONS or raw.get('side') not in (-1,'resistance')
                or raw.get('confirmed_at_ms', float('inf')) > before*1000
                or (raw.get('fit',{}).get('status')!='estimated' if raw.get('book_version')=='causal-level-book-v7-mle-1' else float(raw.get('selection_score',0)) < s['minimum_selection_score'])):
            continue
        if not 0 < raw.get('lower', 0) <= raw.get('upper', 0):
            continue
        rows.append(deepcopy(raw))
    merged = []
    for row in sorted(rows, key=lambda r:(r['lower'],r['upper'])):
        if merged and row['lower'] <= merged[-1]['upper']:
            merged[-1]['upper'] = max(merged[-1]['upper'],row['upper'])
            merged[-1].setdefault('merged_ids',[]).append(row.get('unified_level_id'))
        else:
            merged.append(row)
    return merged


def below(value, s, tick):
    return floor((value-max(tick,value*s['stop_buffer_bps']/10000))/tick+1e-9)*tick


def target_selection(rows, price, bodies, s, tick):
    # Keep the saved candidate's legacy body/gap settings loadable, but distant
    # book gaps must never push this checkpoint past the nearest resistance.
    above = sorted((r for r in rows if r['lower']-tick > price),key=lambda r:r['lower'])
    if not above:
        return None
    level=above[0]
    return dict(price=floor((level['lower']-tick)/tick+1e-9)*tick,level=level,
                selection_method='nearest_qualified_overhead_resistance',
                target_reference=level['lower'])


def observe(o, d, s):
    now = o.observed_at.timestamp()
    closed = 'bar_close' in o.evaluation_events
    if closed and o.source_timeframe == '1s' and now > d.get('swing_observed_at', 0):
        # Two completed candles to each side; pivot is usable only now.
        bars = [*d.get('swing_bars',[]),dict(at=now,low=o.bar_low,high=o.bar_high)][-5:]
        if len(bars)==5 and all(b['low'] is not None for b in bars):
            pivot=bars[2]
            if all(pivot['low'] < b['low'] for i,b in enumerate(bars) if i!=2):
                d['swing_low']=dict(price=pivot['low'],pivot_at=pivot['at'],confirmed_at=now)
        d.update(swing_bars=bars,swing_observed_at=now)
    if not closed or o.source_timeframe != '100ms' or now <= d.get('closed_at',0):
        return False
    if any(v is None or not isfinite(v) for v in (o.macd_line,o.macd_signal,o.bar_open,o.bar_high,o.bar_low)):
        d['sample_valid']=False
        return False
    before=now-.1
    gap=(o.macd_line-o.macd_signal)/o.price*10000
    old_high=d.get('episode_high',0.)
    if gap < -s['exit_gap_bps']:
        d['episode']=None
        d['episode_high']=0.
    elif gap > 0 and d.get('episode') is None:
        d.update(episode=now,episode_high=0.,bodies=[],breaks=[],used_episode=False)
        old_high=0.
    d['prior_episode_high']=old_high
    if d.get('episode') is not None:
        d['episode_high']=max(d.get('episode_high',0.),o.bar_high)
        d['bodies']=[*d.get('bodies',[]),max(0.,o.price-o.bar_open)][-8:]
    prior_hod=d.get('hod')
    # A first observation can seed the HOD, but cannot also trade against it.
    d['prior_hod']=prior_hod
    d['hod']=max(prior_hod or 0.,o.bar_high,o.structural_session_high or 0.)
    rows=levels(o,s,before)
    previous=d.get('close')
    # A broken resistance can already be support in the new book snapshot.
    # Recognize the crossing against the prior known resistance, not its new role.
    crossed=[r for r in d.get('rows',[]) if previous is not None and previous <= r['upper'] < o.price]
    d.update(decision_rows=deepcopy(d.get('rows',[])),closed_at=now,close=o.price,gap_bps=gap,rows=rows,crossed=crossed,sample_valid=True,
             vwap=o.execution_vwap,bar_volume=o.bar_volume)
    return True


def evaluate(host, a, o, p, state):
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    from .signals import CapitalRequest, StrategyIntent
    state=deepcopy(state);s=p['macd_hod'];now=o.observed_at.timestamp();tick=p['execution']['tick_size']
    session=o.observed_at.astimezone(NY).date().isoformat()
    d=state.setdefault('macd_hod_state',{})
    if d.get('session') != session:
        d.clear();d['session']=session
    fresh=observe(o,d,s)
    active=state.get('macd_hod_entry') or {}
    stop=float(state.get('active_stop') or 0)
    target=float((state.get('structural_profit_targets') or [0])[0])
    evidence=dict(contract=CONTRACT,implementation_revision='macd-hod-repair-2',macd=dict(timeframe='100ms',gap_bps=d.get('gap_bps'),
                  observed_at=d.get('closed_at'),episode=d.get('episode')))
    def result(action,reason,status=None,**kw):
        exit_metadata = ({'reentry_after_fill': reason != 'session_flatten' and a.permissions.reenter}
                         if action == 'exit' else {})
        return host._result(a,o,action,reason,1. if action=='enter_long' else 0.,1.,state,status or a.status,
            metadata={**evidence,**exit_metadata,**kw.pop('metadata',{})},**kw)
    acquired=o.position_quantity > 0
    pending=a.status==Status.ENTRY_PENDING or bool(state.get('pending_capital_request'))
    if acquired and d.get('episode') is not None:
        d['used_episode']=True
    if a.status==Status.EXIT_PENDING or o.pending_exit_quantity > 0:
        if acquired and o.position_quantity > o.pending_exit_quantity:
            return result('exit',state.get('last_exit_reason') or 'exit_pending',Status.EXIT_PENDING,
                          quantity=o.position_quantity,metadata={'cancel_entry_acquisition':True,'position_fraction':1.})
        return result('hold' if acquired else 'wait','exit_fill_pending',Status.EXIT_PENDING)
    if not acquired and not pending and active:
        state.pop('macd_hod_entry',None);active={}
        state.pop('entry_acquisition_exit_latched',None)
    flatten=_at_or_after_session_time(o.observed_at,p.get('strategy_behavior',{}).get('flatten_time','15:55:00'))
    if acquired or pending:
        reason=('session_flatten' if flatten else 'protective_stop' if stop and o.price <= stop else
                'macd_bearish_gap_100ms' if fresh and d['gap_bps'] < -s['exit_gap_bps'] else
                'breakout_support_invalidated' if o.source_timeframe=='1s' and 'bar_close' in o.evaluation_events
                and active and o.price < active['invalidation'] else '')
        if reason:
            state.update(last_exit_reason=reason,entry_acquisition_exit_latched=True)
            state.pop('pending_capital_request',None)
            return result('exit',reason,Status.EXIT_PENDING,quantity=o.position_quantity,
                          invalidation_price=stop,metadata={'cancel_entry_acquisition':True,'position_fraction':1.})
    quality_p=dict(p,structural_recovery=dict(QUALITY_DEFAULTS,**{k:v for k,v in s.items() if k in QUALITY_DEFAULTS}))
    ready,quality=tradability(o,quality_p,dict(effective_at=d.get('closed_at',0),candle={'volume':d.get('bar_volume')}),state,producer_freshness=True)
    evidence['liquidity_admission']=quality
    if pending and (not ready or now-active.get('confirmed_at',0)>=s['confirmation_lifetime_ms']/1000
                    or (d.get('gap_bps') or 0)<=0 or o.ask>active.get('maximum_buy_price',0)):
        state.pop('pending_capital_request',None)
        base=result('hold' if acquired else 'wait','entry_acquisition_invalidated',Status.MANAGING if acquired else Status.WATCHING)
        cancel=StrategyIntent(intent_id=base.evaluation.signals[0].signal_id,ticker=o.ticker,event_time=o.observed_at,
            action='cancel_entry',quantity=0,reference_price=o.price,reason='entry_acquisition_invalidated',
            metadata={'assignment_id':a.assignment_id})
        return replace(base,evaluation=replace(base.evaluation,intents=(cancel,)))
    if acquired:
        # A break can earn a stop update later, when its higher low confirms.
        if fresh and d['crossed']:
            active['break_at']=now
            if d['gap_bps']>0 and d.get('bodies') and d['bodies'][-1]>=sum(d['bodies'])/len(d['bodies'])*.5:
                selection=target_selection(d['rows'],o.price,d['bodies'],s,tick)
                if selection and selection['price']>target and o.price<target:
                    active['pending_target']=selection
        low=d.get('swing_low') or {}
        if active.get('break_at') and low.get('confirmed_at',float('inf'))<=now:
            proposed=below(low.get('price',0),s,tick)
            if proposed>stop and proposed<o.bid and low.get('confirmed_at',0)>active.get('stop_swing_at',0):
                state['active_stop']=proposed
                return result('replace_protective_stop','broken_resistance_higher_low',Status.MANAGING,
                    quantity=o.position_quantity,invalidation_price=proposed,profit_target_price=target,
                    metadata={'previous_stop':stop,'active_stop':proposed,'protective_stop_selection':deepcopy(low)})
        selection=active.get('pending_target')
        if selection and selection['price']>target and o.price<target:
            state['structural_profit_targets']=[selection['price']]
            return result('replace_profit_target','confirmed_resistance_target_advance',Status.MANAGING,
                quantity=o.position_quantity,invalidation_price=stop,profit_target_price=selection['price'],
                metadata={'profit_target_selection':selection,'profit_target':selection['price'],
                          'previous_profit_target':target})
        return result('hold','bullish_episode_hold',Status.MANAGING,invalidation_price=stop,profit_target_price=target)
    if pending:
        if state.get('pending_capital_request'):
            return result('enter_long','macd_hod_100ms_entry',Status.ENTRY_PENDING,
                invalidation_price=stop,profit_target_price=target,
                capital_request=CapitalRequest(mode='risk_fraction',value=s['risk_fraction'],maximum_quantity=s['maximum_quantity'],allow_replacement=False),
                order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
                metadata={'initial_stop':stop,'active_stop':stop,'profit_targets':[target],'profit_target':target,
                    'mandatory_broker_target':True,'maximum_buy_price':active['maximum_buy_price'],
                    'unified_structural_trigger':{'current_snapshot':{'levels':[dict(r,entry_boundary=r['upper']) for r in active['references']],
                        'session_high':active['session_high'],'selected_at':state['entry_at'],'frozen_at_entry':True}}})
        return result('wait','entry_fill_pending',Status.ENTRY_PENDING)
    if a.status in (Status.DISABLED,Status.PAUSED,Status.COMPLETED,Status.ERROR) or not a.permissions.observe or not a.permissions.enter:
        return result('wait','assignment_not_active')
    if state.get('entries',0) and not a.permissions.reenter:
        return result('wait','reentry_not_authorized')
    local=o.observed_at.astimezone(NY).time();behavior=p.get('strategy_behavior',{})
    market_session='premarket' if (local.hour,local.minute)<(9,30) else 'regular' if local.hour<16 else 'after_hours'
    if not o.market_open or market_session not in behavior.get('eligible_sessions',['premarket','regular']) or flatten or _at_or_after_session_time(o.observed_at,behavior.get('entry_cutoff_time','15:45:00')):
        return result('wait','outside_entry_session')
    if not fresh or not d.get('sample_valid') or d['gap_bps']<=0:
        return result('wait','waiting_for_completed_positive_100ms_macd')
    if not ready:
        return result('wait','tradability_incomplete')
    hod=d.get('prior_hod')
    if not hod or not d.get('vwap'):
        return result('wait','hod_or_vwap_unavailable')
    entry_rows=d.get('decision_rows',[])
    refs=sorted([r for r in entry_rows if r['upper']<=hod],key=lambda r:r['upper'],reverse=True)[:3]
    boundary=refs[2] if len(refs)>=3 else refs[1] if len(refs)==2 else dict(lower=hod,upper=hod,reference_kind='hod')
    threshold=max(boundary['upper'],d['vwap'])
    if d.get('used_episode'):
        threshold=max(threshold,d['prior_episode_high']*(1+s['reentry_buffer_bps']/10000))
    evidence['entry_selection']=dict(vwap=d['vwap'],prior_hod=hod,threshold=threshold,
        resistance_boundaries=[r['upper'] for r in refs],same_episode_reentry=bool(d.get('used_episode')),
        prior_episode_high=d.get('prior_episode_high'))
    if o.price<=threshold:
        return result('wait','waiting_for_episode_high' if d.get('used_episode') else 'waiting_for_hod_zone')
    low=d.get('swing_low') or {}
    anchor=low['price'] if low.get('confirmed_at',float('inf'))<=now-.1 and low['price']<o.price else boundary['lower']
    stop=below(anchor,s,tick)
    selected=target_selection(entry_rows,o.ask,d.get('bodies',[]),s,tick)
    if not selected:
        return result('wait','qualified_target_unavailable')
    target=selected['price'];cost=o.ask*s['cost_bps_per_side']*2/10000+max(0,o.ask-o.bid)
    ceiling=min(o.price*(1+s['maximum_chase_bps']/10000),
                (target+s['minimum_reward_risk']*stop-cost)/(1+s['minimum_reward_risk']))
    first=next((r for r in entry_rows if r['lower']>o.ask),None)
    evidence['entry_risk']=dict(stop=stop,target=target,maximum_buy_price=ceiling,
        ask=o.ask,bid=o.bid,estimated_cost=cost,minimum_reward_risk=s['minimum_reward_risk'],
        nearest_resistance=first['lower'] if first else None)
    if not 0<stop<o.bid<=o.ask<=ceiling or not first or first['lower']-tick-o.ask<=cost:
        return result('wait','insufficient_room_or_invalid_stop')
    entry=dict(confirmed_at=now,maximum_buy_price=ceiling,invalidation=anchor,stop=stop,target=target,
               references=deepcopy(refs),session_high=hod,stop_swing_at=low.get('confirmed_at',0),episode=d['episode'],break_at=now)
    state.update(macd_hod_entry=entry,initial_stop=stop,active_stop=stop,structural_profit_targets=[target],
                 entry_reference_price=o.ask,entry_at=o.observed_at.isoformat(),last_exit_reason='',
                 entry_acquisition_exit_latched=False,entries=state.get('entries',0)+1)
    return result('enter_long','macd_hod_100ms_entry',Status.ENTRY_PENDING,invalidation_price=stop,profit_target_price=target,
        capital_request=CapitalRequest(mode='risk_fraction',value=s['risk_fraction'],maximum_quantity=s['maximum_quantity'],allow_replacement=False),
        order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
        metadata={'initial_stop':stop,'active_stop':stop,'profit_targets':[target],'profit_target':target,
            'mandatory_broker_target':True,'maximum_buy_price':ceiling,'protective_stop_selection':{'anchor':anchor},
            'profit_target_selection':dict(selected,selected_target_prices=[target]),
            'unified_structural_trigger':{'current_snapshot':{'levels':[dict(r,entry_boundary=r['upper']) for r in refs],
                'session_high':hod,'selected_at':o.observed_at.isoformat(),'frozen_at_entry':True}}})

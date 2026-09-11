"""Independent 1s historical/HOD breakouts within completed 5s MACD episodes.

The policy consumes certified V6 snapshots and passive detector observations.
Position management never grants permission to acquire additional shares.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from math import ceil, floor, isfinite
from zoneinfo import ZoneInfo

from .structural_recovery import DEFAULTS as QUALITY_DEFAULTS, LIQUIDITY_181, tradability

CONTRACT = 'historical-hod-1s-macd-5s-1'
BOOK_VERSION = 'causal-swing-closing-book-6'
NY = ZoneInfo('America/New_York')
DEFAULTS = dict(stop_buffer_bps=5., target_offset_ticks=1., target_distance_fraction=.05,
    management_tolerance_atr=.1, management_failure_closes=2, historical_hold_closes=2,
    maximum_macd_age_ms=5000., maximum_source_age_ms=2000., maximum_quote_age_ms=1000.,
    confirmation_lifetime_ms=1000., maximum_chase_bps=15.,
    minimum_candle_volume=1., risk_fraction=.005, maximum_quantity=10000.)


def configure(p):
    if p.get('historical_hod_contract') != CONTRACT:
        raise ValueError('Unknown historical HOD contract')
    conflicts = ('macd_hod_contract','macd_threshold_contract','macd_r3_contract',
        'structural_recovery_contract','v5_breakout_contract','swing_gap_contract',
        'swing_evidence_contract','swing_momentum_contract')
    if any(p.get(k) for k in conflicts):
        raise ValueError('Historical HOD cannot compose another strategy policy')
    raw = p.get('historical_hod', {})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown historical HOD setting')
    s = dict(DEFAULTS, **raw)
    if any(type(v) not in (int,float) or not isfinite(v) or v <= 0 for v in s.values()):
        raise ValueError('Historical HOD settings must be finite and positive')
    if s['risk_fraction'] > 1 or s['confirmation_lifetime_ms'] > 1000 or s['maximum_macd_age_ms'] > 5000:
        raise ValueError('Invalid risk or completed-candle freshness limit')
    if any(int(s[k]) != s[k] for k in ('management_failure_closes','historical_hold_closes','target_offset_ticks')):
        raise ValueError('Candle counts and tick offsets must be integers')
    # Reuse the validated liquidity configuration, not the recovery strategy.
    from .structural_recovery import configure as configure_quality
    checked = deepcopy(p)
    checked['structural_recovery_contract'] = 'v6-structural-recovery-1'
    checked['liquidity_admission'] = dict(LIQUIDITY_181, **p.get('liquidity_admission', {}))
    configure_quality(checked)
    p['historical_hod'] = s
    p['liquidity_admission'] = checked['liquidity_admission']
    p['structural_detector_settings'] = checked['structural_detector_settings']
    p['entry_candle_confirmation']['enabled'] = False
    p['structural_entry']['enabled'] = False
    p.setdefault('entry', {})['breakout_timeframe'] = '1s'
    p['protection']['trailing']['enabled'] = False
    p['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=False)
    p['momentum_management']['macd_backstop']['enabled'] = False


def historical(level, session):
    stamp = level.get('oldest_member_confirmed_at_ms')
    # V6 must publish member lineage; newest confirmation loses mixed ancestry.
    return stamp is not None and datetime.fromtimestamp(stamp/1000, NY).date().isoformat() < session


def band_levels(rows):
    """Recover the original V6 bands from the shared point-price projection."""
    return [dict(r,lower=r.get('band_lower',r['lower']),upper=r.get('band_upper',r['upper']),
        strategy_level_contract=CONTRACT) for r in rows]


def selected_levels(o, s, before):
    result = []
    for raw in band_levels((*o.structural_support_levels, *o.structural_resistance_levels)):
        if (raw.get('book_version') != BOOK_VERSION or raw.get('lifecycle') not in ('active',None)
                or raw.get('confirmed_at_ms', float('inf')) > before*1000):
            continue
        if (not raw.get('unified_level_id') or any(type(raw.get(k)) not in (int,float)
                or not isfinite(raw[k]) for k in ('lower','price','upper','confirmed_at_ms','oldest_member_confirmed_at_ms'))
                or not 0 < raw['lower'] <= raw['price'] <= raw['upper']
                or not 0 < raw['oldest_member_confirmed_at_ms'] <= raw['confirmed_at_ms']):
            raise ValueError('Historical HOD requires valid V6 bands and historical member provenance')
        result.append(deepcopy(raw))
    return result


def resistance(level):
    return level.get('side') in (-1, 'resistance')


def entry_level(rows, hod, session):
    below = [r for r in rows if resistance(r) and r['upper'] <= hod]
    old = [r for r in below if historical(r,session)]
    if old or below:
        return deepcopy(max(old or below, key=lambda r:(r['upper'],r['price'])))
    return dict(lower=hod, upper=hod, price=hod, reference_kind='hod')


def stop_below(value, s, tick):
    return floor((value-max(tick,value*s['stop_buffer_bps']/10000))/tick+1e-9)*tick


def target_selection(rows, broken, price, s, tick, *, minimum_target=0.):
    reference = broken['price']*(1+s['target_distance_fraction'])
    def placement(r):
        offset = s['target_offset_ticks']*tick
        return (ceil((r['upper']+offset)/tick-1e-9)*tick if r['price'] < reference
            else floor((r['lower']-offset)/tick+1e-9)*tick)
    eligible = [r for r in rows if resistance(r) and r['lower'] > broken['upper']
        and placement(r) > price]
    if not eligible:
        return None
    level = min(eligible, key=lambda r:(abs(r['price']-reference),r['price']))
    target = placement(level)
    if target <= minimum_target+tick/2:
        return None
    return dict(price=target, level=deepcopy(level), reference=reference, broken_level=deepcopy(broken),
        placement='above_upper_band' if level['price'] < reference else 'below_lower_band',
        selection_method='resistance_nearest_five_percent_above_broken_level')


def management(row, active, bar, s, tick):
    """Warnings need subsequent price failure; the broker stop is independent."""
    events = row.get('local_events', [])+row.get('global_events', [])
    atr = row.get('qualification', {}).get('atr') or 0.
    base = active['management_base']
    for event in row.get('local_events', []):
        level = event.get('level', {})
        if (event.get('state') == 'higher_low_confirmed' and level.get('lower',0) > base['lower']
                and level.get('confirmed_at',0) > active['confirmed_at']):
            base = dict(lower=level['lower'], tolerance=max(tick,s['management_tolerance_atr']*atr))
            active['management_base'] = base
            active['failure_closes'] = 0
    below = bar['close'] < base['lower']-base['tolerance']
    active['failure_closes'] = active.get('failure_closes',0)+1 if below else 0
    if active['failure_closes'] >= s['management_failure_closes']:
        return 'protective_swing_failed'
    if below and any(e.get('direction') == 'bearish' and e.get('outcome') == 'structural_reversal_confirmation'
            for e in row.get('volume_analysis',{}).get('reversal_outcomes', [])):
        return 'confirmed_structural_reversal'
    rejection = active.get('rejection')
    if rejection and bar['close'] > rejection['upper']:
        active.pop('rejection',None)
        rejection = None
    if rejection:
        for e in row.get('local_events', []):
            level = e.get('level', {})
            if (e.get('state') == 'lower_high_confirmed' and level.get('confirmed_at',0) > rejection['at']
                    and level.get('pivot_at',0) > rejection['at']
                    and level.get('price',float('inf')) < rejection['upper']):
                rejection['failed_high'] = level['price']
        if (rejection.get('failed_high') and bar['close'] < rejection['reaction_low']-rejection['tolerance']):
            return 'resistance_rejection_failed_recovery'
        if not rejection.get('failed_high'):
            rejection['reaction_low'] = min(rejection['reaction_low'],bar['low'])
    if not rejection:
        for e in events:
            level = e.get('level', {})
            if (e.get('state') in ('rejection','failed_breakout') and resistance(level)
                    and level.get('upper',0) >= bar['close'] and bar['high'] >= level.get('lower',float('inf'))):
                active['rejection'] = dict(at=bar['end'],upper=level['upper'],reaction_low=bar['low'],
                    tolerance=max(tick,s['management_tolerance_atr']*atr))
                break
    return ''


def observe(o, d, s):
    now = o.observed_at.timestamp()
    if 'bar_close' not in o.evaluation_events:
        return False, False
    macd_closed = False
    if o.source_timeframe == '5s' and now > d.get('macd_at',0):
        valid = all(v is not None and isfinite(v) for v in (o.macd_line,o.macd_signal))
        positive = valid and o.macd_line > o.macd_signal
        was_open = d.get('episode') is not None
        if positive and not was_open:
            d.update(episode=now,body_high=0.,used_episode=False,
                breakout_upper=None,failed_breakout=False)
        elif valid and not positive:
            d['episode'] = None
        d.update(macd_at=now,macd_valid=valid,macd_positive=positive,
            macd_line=o.macd_line,macd_signal=o.macd_signal)
        macd_closed = valid and not positive
    if o.source_timeframe != '1s' or now <= d.get('closed_at',0):
        return False, macd_closed
    if any(v is None or not isfinite(v) for v in (o.bar_open,o.bar_low,o.bar_high)):
        return False, macd_closed
    contiguous = now-d.get('closed_at',0) == 1
    d['prior_close'] = d.get('close') if contiguous else None
    d['prior_rows'] = deepcopy(d.get('rows', [])) if contiguous else []
    d['prior_hod'] = d.get('hod')
    d['prior_body_high'] = d.get('body_high',0.)
    if d.get('episode') is not None:
        # Observe attempts before admission gates, including before assignment.
        # A rejected excursion cannot become a new first entry at the old band.
        upper = d.get('breakout_upper')
        if upper is not None and o.price <= upper:
            d['failed_breakout'] = True
        if contiguous and d.get('prior_hod'):
            boundary = entry_level(d['prior_rows'],d['prior_hod'],d['session'])
            if d['prior_close'] <= boundary['upper'] < o.price and o.price >= o.bar_open:
                d['breakout_upper'] = boundary['upper']
        d['body_high'] = max(d.get('body_high',0.),o.bar_open,o.price)
    d['hod'] = max(d.get('hod',0.),o.bar_high,o.structural_session_high or 0.)
    d.update(closed_at=now,close=o.price,rows=selected_levels(o,s,now),contiguous=contiguous,
        bar=dict(time=now-1,end=now,open=o.bar_open,high=o.bar_high,low=o.bar_low,
            close=o.price,volume=o.bar_volume),vwap=o.execution_vwap)
    return True, macd_closed


def observe_frame(frame, saved, parameters, snapshot=None):
    """Maintain episode history before discovery creates a trade assignment."""
    from types import SimpleNamespace
    d = deepcopy(saved)
    session = frame.as_of.astimezone(NY).date().isoformat()
    if d.get('session') != session:
        d = {'session':session}
    snapshot = snapshot or {}
    o = SimpleNamespace(observed_at=frame.as_of, source_timeframe=frame.timeframe,
        evaluation_events=('bar_close',), macd_line=frame.indicator.get('macd_line'),
        macd_signal=frame.indicator.get('macd_signal'), price=frame.bar['close'],
        bar_open=frame.bar['open'],bar_low=frame.bar['low'],bar_high=frame.bar['high'],
        bar_volume=frame.bar.get('volume'),structural_support_levels=(),
        structural_resistance_levels=tuple(snapshot.get('unified_levels', [])),
        structural_session_high=frame.indicator.get('qmd_structure_session_high') or snapshot.get('session_high'),
        execution_vwap=frame.indicator.get('execution_vwap'))
    observe(o,d,parameters.get('historical_hod',DEFAULTS))
    d['observed_at'] = frame.as_of.timestamp()
    return d


def evaluate(host, a, o, p, state):
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    from .signals import CapitalRequest, StrategyIntent
    state = deepcopy(state)
    s = p['historical_hod']; tick = p['execution']['tick_size']; now = o.observed_at.timestamp()
    d = state.setdefault('historical_hod_state', {})
    session = o.observed_at.astimezone(NY).date().isoformat()
    if d.get('session') != session:
        d.clear(); d['session'] = session
    passive = (o.structural_detector_state or {}).get('historical_hod_observation',{})
    if passive.get('observed_at') == now and passive.get('session') == session:
        fresh = o.source_timeframe == '1s' and passive.get('closed_at',0) > d.get('closed_at',0)
        macd_closed = (o.source_timeframe == '5s' and passive.get('macd_at',0) > d.get('macd_at',0)
            and passive.get('macd_valid') and not passive.get('macd_positive'))
        used = d.get('used_episode',False) if d.get('episode') == passive.get('episode') else False
        d = deepcopy(passive)
        d['used_episode'] = used
        # Use the runtime's resolved execution VWAP for the current 1s candle.
        if fresh:
            d['vwap'] = o.execution_vwap
        state['historical_hod_state'] = d
    else:
        fresh, macd_closed = observe(o,d,s)
    active = state.get('historical_hod_entry') or {}
    stop = float(state.get('active_stop') or 0)
    target = float((state.get('structural_profit_targets') or [0])[0])
    acquired = o.position_quantity > 0
    pending = a.status == Status.ENTRY_PENDING or bool(state.get('pending_capital_request'))
    evidence = dict(contract=CONTRACT,macd=dict(timeframe='5s',observed_at=d.get('macd_at'),
        line=d.get('macd_line'),signal=d.get('macd_signal'),episode=d.get('episode')))
    def result(action, reason, status=None, **kw):
        metadata = dict(evidence, **kw.pop('metadata', {}))
        if action == 'exit':
            metadata.update(reentry_after_fill=reason != 'session_flatten' and a.permissions.reenter,
                cancel_entry_acquisition=True,position_fraction=1.)
        return host._result(a,o,action,reason,1. if action=='enter_long' else 0.,1.,state,status or a.status,
            metadata=metadata,**kw)
    if acquired and d.get('episode') is not None:
        d['used_episode'] = True
    if a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0:
        if acquired and o.position_quantity > o.pending_exit_quantity:
            return result('exit',state.get('last_exit_reason') or 'exit_pending',Status.EXIT_PENDING,quantity=o.position_quantity)
        return result('hold' if acquired else 'wait','exit_fill_pending',Status.EXIT_PENDING)
    if not acquired and not pending and active:
        state.pop('historical_hod_entry',None); active = {}
    flatten = _at_or_after_session_time(o.observed_at,p.get('strategy_behavior',{}).get('flatten_time','15:55:00'))
    market = o.structural_detector_state or {}
    row = market.get('row', {})
    detector_fresh = (fresh and market.get('book',{}).get('version') == BOOK_VERSION
        and bool(market.get('book',{}).get('fingerprint')) and row.get('effective_at') == now)
    if acquired or pending:
        reason = ('session_flatten' if flatten else 'protective_stop' if stop and o.price <= stop
            else 'manual_exit' if state.get('manual_exit_requested') else 'macd_episode_ended' if macd_closed else '')
        if not reason and acquired and active and detector_fresh:
            if not d['contiguous'] or row.get('gap_before'):
                active['failure_closes'] = 0; active.pop('rejection',None)
            else:
                reason = management(row,active,d['bar'],s,tick)
        if reason:
            state.update(last_exit_reason=reason,entry_acquisition_exit_latched=True)
            state.pop('pending_capital_request',None)
            return result('exit',reason,Status.EXIT_PENDING,quantity=o.position_quantity,invalidation_price=stop)
    macd_ready = (d.get('macd_valid') and d.get('macd_positive') and d.get('episode') is not None
        and 0 <= (now-d.get('macd_at',0))*1000 <= s['maximum_macd_age_ms'])
    quality_p = dict(p,structural_recovery=dict(QUALITY_DEFAULTS,**{k:v for k,v in s.items() if k in QUALITY_DEFAULTS}))
    ready, quality = tradability(o,quality_p,dict(effective_at=d.get('closed_at',0),candle=d.get('bar',{})),state,producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if pending and (not active or not ready or not macd_ready or o.price <= (d.get('vwap') or float('inf'))
            or now-active.get('confirmed_at',0) >= s['confirmation_lifetime_ms']/1000
            or o.ask > active.get('maximum_buy_price',0)):
        state.pop('pending_capital_request',None)
        base = result('hold' if acquired else 'wait','entry_acquisition_invalidated',Status.MANAGING if acquired else Status.WATCHING)
        cancel = StrategyIntent(intent_id=base.evaluation.signals[0].signal_id,ticker=o.ticker,event_time=o.observed_at,
            action='cancel_entry',quantity=0,reference_price=o.price,reason='entry_acquisition_invalidated',metadata={'assignment_id':a.assignment_id})
        return replace(base,evaluation=replace(base.evaluation,intents=(cancel,)))
    if acquired:
        if not active:
            return result('hold','position_context_missing',Status.MANAGING,invalidation_price=stop)
        if not active.get('fill_risk_frozen') and not pending and o.average_price > 0:
            active['initial_risk'] = o.average_price-active['stop']
            active['fill_risk_frozen'] = True
        if fresh:
            active.pop('desired_target',None)
            previous = d.get('prior_close')
            crossed = [r for r in d.get('prior_rows',[]) if resistance(r) and previous is not None and previous <= r['upper'] < o.price]
            pending_levels = active.setdefault('hold_levels',{})
            target_breaks = active.setdefault('target_breaks',{})
            if not d['contiguous']:
                target_breaks.clear()
            for r in sorted(crossed,key=lambda level:level['upper']):
                # Stop confirmation includes the breakout close itself.
                if historical(r,session):
                    pending_levels[str(r['unified_level_id'])] = dict(level=r,count=0)
                target_breaks[str(r['unified_level_id'])] = deepcopy(r)
            for key,r in sorted(list(target_breaks.items()),key=lambda item:item[1]['upper']):
                if o.price <= r['upper']:
                    del target_breaks[key]; continue
                if o.price < o.bar_open:
                    continue
                # A red breakout remains pending until a non-red close confirms
                # it, or a completed close falls back through its frozen band.
                del target_breaks[key]
                selected = target_selection(d['prior_rows'],active['target']['level'],max(o.price,o.ask),s,tick,
                    minimum_target=target)
                if selected:
                    selected.update(triggering_breakout=deepcopy(r),
                        selection_method='resistance_nearest_five_percent_above_current_target_resistance')
                if selected and selected['price'] >= active.get('desired_target',{}).get('price',target):
                    active['desired_target'] = selected
            if len(target_breaks)>4096:
                raise ValueError('Target breakout confirmation capacity exceeded')
            if not d['contiguous']:
                pending_levels.clear()
            for key, item in list(pending_levels.items()):
                r = item['level']
                if o.price <= r['upper']:
                    del pending_levels[key]; continue
                item['count'] += 1
                if item['count'] >= s['historical_hold_closes']:
                    if historical(r,session):
                        active['desired_stop'] = max(active.get('desired_stop',0),stop_below(r['lower'],s,tick))
                    del pending_levels[key]
            if len(pending_levels)>4096:
                raise ValueError('Historical stop confirmation capacity exceeded')
            active['best_close'] = max(active.get('best_close',o.price),o.price)
            if not any(historical(r,session) and r['upper'] < o.price for r in d['rows']):
                trailing = floor((active['best_close']-active['initial_risk'])/tick+1e-9)*tick
                active['desired_stop'] = max(active.get('desired_stop',0),trailing)
        proposed = active.get('desired_stop',0)
        replacements = []
        if fresh and stop < proposed < o.bid:
            state['active_stop'] = proposed
            replacements.append(result('replace_protective_stop','historical_hold_or_initial_risk_trail',Status.MANAGING,
                quantity=o.position_quantity,invalidation_price=proposed,profit_target_price=target,
                metadata={'previous_stop':stop,'active_stop':proposed}))
        selection = active.get('desired_target')
        if fresh and o.price >= o.bar_open and selection and selection['price'] > target and selection['price'] > max(o.price,o.ask):
            previous_selection = deepcopy(active['target'])
            active['target'] = deepcopy(selection)
            state['structural_profit_targets'] = [selection['price']]
            replacements.append(result('replace_profit_target','resistance_break_target_advance',Status.MANAGING,
                quantity=o.position_quantity,invalidation_price=state['active_stop'],profit_target_price=selection['price'],
                metadata={'previous_profit_target':target,'profit_target':selection['price'],'profit_target_selection':selection,
                    'previous_historical_hod_target':previous_selection}))
        if replacements:
            return replace(replacements[-1], evaluation=replace(replacements[-1].evaluation,
                signals=tuple(signal for r in replacements for signal in r.evaluation.signals),
                intents=tuple(intent for r in replacements for intent in r.evaluation.intents)))
        return result('hold','structure_valid' if detector_fresh else 'awaiting_completed_structure',Status.MANAGING,
            invalidation_price=stop,profit_target_price=target)
    def enter(entry, reason):
        return result('enter_long',reason,Status.ENTRY_PENDING,invalidation_price=entry['stop'],profit_target_price=entry['target']['price'],
            capital_request=CapitalRequest(mode='risk_fraction',value=s['risk_fraction'],maximum_quantity=s['maximum_quantity'],allow_replacement=False),
            order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
            metadata={'initial_stop':entry['stop'],'active_stop':entry['stop'],'profit_targets':[entry['target']['price']],
                'profit_target':entry['target']['price'],'mandatory_broker_target':True,'maximum_buy_price':entry['maximum_buy_price'],
                'entry_selection':entry['level'],'profit_target_selection':entry['target'],
                'unified_structural_trigger':{'current_snapshot':{'levels':[entry['level']], 'session_high':entry['hod'],
                    'selected_at':o.observed_at.isoformat(),'frozen_at_entry':True}}})
    if pending:
        return enter(active,'historical_hod_entry') if state.get('pending_capital_request') else result('wait','entry_fill_pending',Status.ENTRY_PENDING)
    if (a.status in (Status.DISABLED,Status.PAUSED,Status.COMPLETED,Status.ERROR)
            or not a.permissions.observe or not a.permissions.enter
            or (state.get('entries',0) and not a.permissions.reenter)):
        return result('wait','entry_permission_closed')
    local = o.observed_at.astimezone(NY).time()
    market_session = 'premarket' if (local.hour,local.minute)<(9,30) else 'regular' if local.hour<16 else 'after_hours'
    if (market_session not in p['strategy_behavior'].get('eligible_sessions',['premarket','regular']) or flatten
            or _at_or_after_session_time(o.observed_at,p['strategy_behavior'].get('entry_cutoff_time','15:45:00')) or not o.market_open):
        return result('wait','outside_entry_session')
    if not fresh or not macd_ready:
        return result('wait','waiting_for_completed_1s_and_bullish_5s_macd')
    if not ready:
        return result('wait','tradability_incomplete')
    if not detector_fresh:
        return result('wait','certified_detector_unavailable')
    hod = d.get('prior_hod'); previous = d.get('prior_close')
    vwap = d.get('vwap')
    if not hod or previous is None or vwap is None or not isfinite(vwap) or o.price <= vwap:
        return result('wait','hod_history_or_vwap_gate')
    boundary = entry_level(d['prior_rows'],hod,session)
    reentry = bool(d.get('used_episode'))
    failed_breakout = bool(d.get('failed_breakout'))
    require_body_high = reentry or failed_breakout
    threshold = max(boundary['upper'],d['prior_body_high']) if require_body_high else boundary['upper']
    evidence['entry_selection'] = dict(level=boundary,prior_hod=hod,threshold=threshold,reentry=reentry,
        failed_breakout=failed_breakout)
    if o.price < o.bar_open:
        return result('wait','red_breakout_candle')
    if not previous <= threshold < o.price:
        return result('wait','waiting_for_fresh_body_high_break' if require_body_high else 'waiting_for_fresh_resistance_break')
    selected = target_selection(d['prior_rows'],boundary,max(o.ask,o.price),s,tick)
    if not selected:
        return result('wait','qualified_target_unavailable')
    stop = stop_below(boundary['lower'],s,tick)
    # Bound execution slippage from the executable quote, not the last trade.
    ceiling = min(o.ask*(1+s['maximum_chase_bps']/10000),selected['price']-tick)
    if not 0 < stop < o.bid <= o.ask <= ceiling:
        return result('wait','invalid_stop_or_entry_price')
    atr = row.get('qualification',{}).get('atr') or 0.
    entry = dict(confirmed_at=now,level=boundary,hod=hod,stop=stop,target=selected,maximum_buy_price=ceiling,
        initial_risk=o.ask-stop,best_close=o.price,episode=d['episode'],
        management_base=dict(lower=boundary['lower'],tolerance=max(tick,s['management_tolerance_atr']*atr)),hold_levels={})
    state.update(historical_hod_entry=entry,initial_stop=stop,active_stop=stop,structural_profit_targets=[selected['price']],
        entry_reference_price=o.ask,entry_at=o.observed_at.isoformat(),entries=state.get('entries',0)+1,
        last_exit_reason='',entry_acquisition_exit_latched=False)
    return enter(entry,'historical_hod_entry')

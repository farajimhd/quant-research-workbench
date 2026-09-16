"""Causal R1/HOD resistance ladder, with fixed stops and full broker targets.

The historical-HOD adapter supplies shared market inputs only. Its entry,
trailing, reversal and cash-tranche policies are never evaluated here.
"""
from copy import deepcopy
from datetime import time
from math import ceil, floor, isfinite

from . import historical_hod as H, session_relative_volume

CONTRACT = 'r1-hod-resistance-ladder-v2'
LEGACY_CONTRACT = 'r1-hod-resistance-ladder-v1'
DEFAULTS = dict(minimum_rvol=2., cash_fraction=.9, maximum_quantity=10000.,
    entry_start_time='04:02:00', minimum_target_gap_atr=2.,
    fallback_target_gap_atr=2.5, late_entry_gap_fraction=.5,
    target_midpoint_offset_ticks=1., stop_offset_bps=20.)


def configure(p):
    if p.get('r1_ladder_contract') == LEGACY_CONTRACT:
        from .r1_ladder_v1 import configure as configure_v1
        return configure_v1(p)
    if p.get('r1_ladder_contract') != CONTRACT or p.get('historical_hod_contract') != H.CONTRACT:
        raise ValueError('R1 ladder requires its versioned shared market adapter')
    raw = p.get('r1_ladder', {})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown R1 ladder setting')
    s = dict(DEFAULTS, **raw)
    numeric = {k:v for k,v in s.items() if k != 'entry_start_time'}
    if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in numeric.values()):
        raise ValueError('R1 ladder settings must be finite and positive')
    try:
        time.fromisoformat(s['entry_start_time'])
    except (TypeError, ValueError):
        raise ValueError('R1 ladder entry start time must be HH:MM:SS') from None
    if (s['cash_fraction'] > 1 or s['minimum_rvol'] != 2
            or s['fallback_target_gap_atr'] < s['minimum_target_gap_atr']
            or not 0 < s['late_entry_gap_fraction'] < 1):
        raise ValueError('Invalid R1 ladder policy thresholds')
    if p['historical_hod']['forming_macd_entry_enabled']:
        raise ValueError('R1 ladder requires completed 5s MACD')
    p['r1_ladder'] = s


def stop_price(entry, swing_lower, tick):
    """Clamp swing protection, rounding within the permitted distance interval."""
    maximum = .30 if entry < 2 else entry*.05
    lower = max(tick, entry-maximum)
    upper = entry-.10
    low_tick = ceil((lower-1e-10)/tick)
    high_tick = floor((upper+1e-10)/tick)
    if low_tick > high_tick:
        return None
    desired = floor((swing_lower-tick+1e-10)/tick)
    return round(max(low_tick, min(high_tick, desired))*tick, 10)


def continuation_stop(level, tick, offset_bps=20.):
    """Place protection below a crossed resistance band's lower edge."""
    lower = level.get('lower')
    if type(lower) not in (int, float) or not isfinite(lower) or lower <= 0:
        return None
    raw = lower*(1-offset_bps/10000)
    return round(floor((raw+1e-10)/tick)*tick, 10)


def r1_level(rows, hod):
    return max((r for r in rows if resistance(r) and r['upper'] < hod),
               key=lambda r:(r['upper'], r['price'], str(r['unified_level_id'])), default=None)


def next_target(rows, price):
    # Sell at the approaching edge; reentry requires clearing the far edge.
    return min((r for r in rows if resistance(r) and r['lower'] > price),
               key=lambda r:(r['lower'], str(r['unified_level_id'])), default=None)


def _midpoint(level):
    return (float(level['lower'])+float(level['upper']))/2


def target_plan(rows, boundary, price, atr, settings, tick):
    """Select a causal overhead target and the resistance that earns its stop."""
    if not boundary or type(atr) not in (int, float) or not isfinite(atr) or atr <= 0:
        return None
    all_resistance = sorted((r for r in rows if resistance(r)),
                            key=lambda r:(_midpoint(r), str(r['unified_level_id'])))
    boundary_index = next((i for i,row in enumerate(all_resistance)
                           if row.get('unified_level_id') == boundary.get('unified_level_id')), -1)
    if boundary_index < 0:
        return None
    overhead = all_resistance[boundary_index+1:]
    if not overhead:
        return None
    base = _midpoint(boundary)
    immediate = overhead[0]
    gap_atr = (_midpoint(immediate)-base)/atr
    index = 0
    selection = 'minimum_2atr_gap'
    if gap_atr < settings['minimum_target_gap_atr']:
        selection = 'first_resistance_at_or_above_2_5atr'
        index = next((i for i,row in enumerate(overhead)
                      if _midpoint(row)-base >= settings['fallback_target_gap_atr']*atr), -1)
        if index < 0:
            return None
    halfway = base+(_midpoint(immediate)-base)*settings['late_entry_gap_fraction']
    if price > halfway:
        if len(overhead) < 2:
            return None
        index = max(index, 1)
        selection += '_late_episode_extension'
    target = overhead[index]
    target_index = next((i for i,row in enumerate(all_resistance)
                         if row.get('unified_level_id') == target.get('unified_level_id')), -1)
    if target_index <= 0:
        return None
    stop_anchor = all_resistance[target_index-1]
    target_price = floor((_midpoint(target)-settings['target_midpoint_offset_ticks']*tick+1e-10)/tick)*tick
    if target_price <= price:
        return None
    return dict(target=target, target_price=round(target_price,10), stop_anchor=stop_anchor,
                selection=selection, immediate_target=immediate, immediate_gap_atr=gap_atr,
                halfway_price=halfway)


def observe_session_hod(o, state, settings):
    """Maintain a separate HOD that excludes every completed bar before the configured start."""
    if o.source_timeframe != '1s' or 'bar_close' not in o.evaluation_events:
        return
    now = o.observed_at.timestamp()
    state['prior_r1_hod_after_start'] = state.get('r1_hod_after_start')
    state['prior_r1_hod_at'] = state.get('r1_hod_at')
    if o.observed_at.astimezone(H.NY).time() < time.fromisoformat(settings['entry_start_time']):
        return
    high = o.bar_high
    if type(high) in (int, float) and isfinite(high) and high > 0:
        state['r1_hod_after_start'] = max(state.get('r1_hod_after_start') or 0., high)
        state['r1_hod_at'] = now


def resistance(level):
    return level.get('side') in (-1, 'resistance') and level.get('role') != 'transition'


def _track_level(state, level, role, at):
    """Retain every structural level used by the session ladder and its roles."""
    if not level:
        return
    level_id = str(level.get('unified_level_id') or '')
    ledger = state.setdefault('r1_levels', [])
    existing = next((item for item in ledger if item.get('level_id') == level_id), None)
    use = dict(at=float(at), role=role)
    if existing is None:
        ledger.append(dict(level_id=level_id, level=deepcopy(level), uses=[use]))
    else:
        existing['level'] = deepcopy(level)
        existing.setdefault('uses', []).append(use)


def _update_macd_episode(d, now, line, signal, high=None):
    """Advance the causal completed-5s episode ledger."""
    valid = all(type(value) in (int, float) and isfinite(value) for value in (line, signal))
    bullish = valid and line > signal
    episode = d.get('macd_episode')
    if bullish:
        if not episode:
            episode = dict(episode_id=f"{d.get('session','')}:{now}", started_at=now,
                           high=0., high_at=None)
            d['macd_episode'] = episode
        episode.update(last_macd_at=now, line=line, signal=signal)
        if type(high) in (int, float) and isfinite(high) and high > episode.get('high', 0):
            episode.update(high=high, high_at=now)
    elif episode:
        closed = dict(episode, ended_at=now, closing_line=line, closing_signal=signal)
        d.setdefault('macd_episodes', []).append(closed)
        d.pop('macd_episode', None)
    d['macd'] = dict(at=now, line=line, signal=signal)


def record_exit(state, at, role, remaining):
    """Only a fully liquidated, actually filled target advances the ladder."""
    active = state.get('r1_entry')
    if not active:
        return
    active = dict(active, non_target_exit=bool(active.get('non_target_exit') or role != 'profit_target'))
    state['r1_entry'] = active
    if remaining is None or abs(float(remaining)) > 1e-9:
        return
    origin = role if active['non_target_exit'] else 'profit_target'
    if active['non_target_exit'] and origin == 'profit_target':
        origin = 'mixed_exit'
    target = deepcopy(active['target_level']) if origin == 'profit_target' else None
    state['r1_exit'] = dict(at=at.timestamp(), role=origin, level=target)
    if target:
        _track_level(state, target, 'broken_profit_target', at.timestamp())
    state.pop('r1_entry', None)


def evaluate(host, a, o, p, state):
    if p.get('r1_ladder_contract') == LEGACY_CONTRACT:
        from .r1_ladder_v1 import evaluate as evaluate_v1
        return evaluate_v1(host,a,o,p,state)
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    from .signals import CapitalRequest
    previous = state.get('r1_market', {})
    state = deepcopy({k:v for k,v in state.items() if k != 'r1_market'})
    d = dict(previous)
    state['r1_market'] = d
    now = o.observed_at.timestamp()
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    if d.get('session') != session:
        d.clear(); d['session'] = session
        state.pop('r1_exit', None)
        state.pop('r1_levels', None)
    adapter = p['historical_hod']; s = p['r1_ladder']; tick = p['execution']['tick_size']
    market = o.structural_detector_state or {}
    passive = market.get('historical_hod_observation') or {}
    # Seed certified pre-assignment history without granting an old breakout.
    if not d.get('closed_at') and passive.get('session') == session and passive.get('observed_at') == now:
        if passive.get('prior_bar'):
            prior = passive['prior_bar']
            d.update(closed_at=prior['end'], close=prior['close'],
                     rows=passive.get('prior_rows', []),
                     hod=passive.get('prior_r1_hod_after_start') or 0.,
                     atr=passive.get('closed_atr'))
        completed = passive.get('completed_macd') or {}
        if completed:
            _update_macd_episode(d, completed.get('at'), completed.get('line'),
                                 completed.get('signal'), (passive.get('prior_bar') or {}).get('high'))
    fresh = ('bar_close' in o.evaluation_events and o.source_timeframe == '1s'
             and now > d.get('closed_at', 0))
    if 'bar_close' in o.evaluation_events and o.source_timeframe == '5s':
        if now > d.get('macd', {}).get('at', 0):
            _update_macd_episode(d, now, o.macd_line, o.macd_signal, o.bar_high)
    prior_close, prior_at = d.get('close'), d.get('closed_at')
    prior_episode = deepcopy(d.get('macd_episode') or {})
    prior_rows, prior_hod = d.get('rows', []), d.get('hod') or 0
    selected_r1 = r1_level(prior_rows, prior_hod)
    if fresh:
        if not all(type(v) in (int,float) and isfinite(v) and v > 0
                   for v in (o.price,o.bar_high,o.bar_low,o.bar_open)):
            fresh = False
        else:
            after_start = o.observed_at.astimezone(H.NY).time() >= time.fromisoformat(s['entry_start_time'])
            d.update(closed_at=now,close=o.price,rows=H.selected_levels(o,adapter,now),
                     hod=max(prior_hod,o.bar_high) if after_start else prior_hod,
                     atr=o.volatility if type(o.volatility) in (int,float) and isfinite(o.volatility) else None)
            episode = d.get('macd_episode')
            if episode and o.bar_high > episode.get('high', 0):
                episode.update(high=o.bar_high, high_at=now)
    current_r1 = r1_level(d.get('rows', []), d.get('hod') or 0)
    saved_exit = state.get('r1_exit') or {}
    boundary = saved_exit.get('level') or selected_r1
    reference_level = boundary or current_r1
    reference = dict(contract=CONTRACT,hod=prior_hod if fresh else d.get('hod'),
        resistance_upper=(reference_level or {}).get('upper'),
        threshold=(boundary or {}).get('upper'),level_id=(reference_level or {}).get('unified_level_id'))
    evidence = dict(contract=CONTRACT, historical_hod_reference=dict(reference,at=now,
        changed=reference != previous.get('chart_reference')),macd=dict(d.get('macd') or {},timeframe='5s',kind='completed'),
        macd_episode=dict(prior_episode or d.get('macd_episode') or {}),
        completed_macd_episode_count=len(d.get('macd_episodes', [])))
    d['chart_reference'] = reference
    def result(action, reason, status=None, **kw):
        metadata = dict(evidence, **kw.pop('metadata', {}))
        if action == 'exit':
            state['last_exit_reason'] = reason
            metadata.update(position_fraction=1.,cancel_entry_acquisition=True,
                            reentry_after_fill=reason != 'session_flatten' and a.permissions.reenter)
        return host._result(a,o,action,reason,1. if action=='enter_long' else 0.,1.,state,
                            status or a.status,metadata=metadata,**kw)
    active = state.get('r1_entry') or {}
    acquired = o.position_quantity > 0
    pending = a.status == Status.ENTRY_PENDING or bool(state.get('pending_capital_request'))
    behavior = p['strategy_behavior']
    flatten = _at_or_after_session_time(o.observed_at,behavior.get('flatten_time','15:55:00'))
    if acquired:
        if flatten or state.get('manual_exit_requested') or state.get('r1_stop_error'):
            quantity = max(0.,o.position_quantity-o.pending_exit_quantity)
            if not quantity:
                return result('hold','exit_fill_pending',Status.EXIT_PENDING)
            reason = 'session_flatten' if flatten else 'unrepresentable_fill_stop' if state.get('r1_stop_error') else 'manual_exit'
            return result('exit',reason,Status.EXIT_PENDING,quantity=quantity)
        stop_anchor = active.get('stop_anchor_level') or {}
        proposed = continuation_stop(stop_anchor,tick,s['stop_offset_bps'])
        current_stop = state.get('active_stop') or 0.
        if (fresh and stop_anchor and o.price > stop_anchor.get('upper',float('inf'))
                and proposed is not None and current_stop < proposed < o.bid):
            state['active_stop'] = proposed
            _track_level(state,stop_anchor,'protective_stop_earned',now)
            return result('replace_protective_stop','target_predecessor_resistance_crossed',Status.MANAGING,
                quantity=o.position_quantity,invalidation_price=proposed,
                profit_target_price=(state.get('structural_profit_targets') or [None])[0],
                metadata={'previous_stop':current_stop,'active_stop':proposed,
                    'stop_anchor_level':deepcopy(stop_anchor),
                    'stop_offset_bps':s['stop_offset_bps']})
        # Broker brackets own the full target and the latest earned structural stop.
        return result('hold','fixed_stop_and_resistance_target',Status.MANAGING,
                      invalidation_price=state.get('active_stop'),
                      profit_target_price=(state.get('structural_profit_targets') or [None])[0])
    if pending:
        return result('wait','entry_fill_pending',Status.ENTRY_PENDING)
    if a.status in (Status.DISABLED,Status.PAUSED,Status.COMPLETED,Status.ERROR,Status.EXIT_PENDING):
        return result('wait','entry_permission_closed')
    if not a.permissions.observe or not a.permissions.enter or (state.get('entries',0) and not a.permissions.reenter):
        return result('wait','entry_permission_closed')
    local = o.observed_at.astimezone(H.NY)
    phase = 'premarket' if (local.hour,local.minute)<(9,30) else 'regular' if local.hour<16 else 'after_hours'
    if flatten or not o.market_open or phase not in behavior.get('eligible_sessions',[]) or _at_or_after_session_time(o.observed_at,behavior.get('entry_cutoff_time','15:45:00')):
        return result('wait','outside_entry_session')
    if local.time() < time.fromisoformat(s['entry_start_time']):
        return result('wait','before_configured_entry_start')
    if not fresh:
        return result('wait','waiting_for_completed_1s')
    row = market.get('row') or {}
    if market.get('book',{}).get('version') not in H.BOOK_VERSIONS or not market.get('book',{}).get('fingerprint') or row.get('effective_at') != now:
        return result('wait','completed_structure_unavailable')
    macd = d.get('macd') or {}
    if not (0 <= now-macd.get('at',0) <= adapter['maximum_macd_age_ms']/1000
            and all(type(macd.get(k)) in (int,float) and isfinite(macd[k]) for k in ('line','signal'))
            and macd['line'] > macd['signal']):
        return result('wait','completed_5s_macd_not_bullish')
    continuation = bool(saved_exit.get('role') == 'profit_target'
                        and saved_exit.get('level') and d.get('macd_episode'))
    ready, quality = H.tradability(o,dict(p,structural_recovery=dict(H.QUALITY_DEFAULTS,**adapter)),row,state,producer_freshness=True)
    if continuation:
        ignored = [item for item in quality['failed'] if item == 'current_spread']
        remaining = [item for item in quality['failed'] if item != 'current_spread']
        quality = dict(quality, ignored_for_macd_continuation=ignored,
                       effective_failed=remaining)
        ready = not remaining
    evidence['liquidity_admission'] = quality
    if not ready:
        return result('wait','liquidity_or_spread_gate')
    rvol = session_relative_volume.confirm((o.market_pressure or {}).get('session_relative_volume'),now=now,minimum_ratio=s['minimum_rvol'])
    rvol['passed'] = rvol['passed'] and rvol['ratio'] > s['minimum_rvol']
    evidence['session_relative_volume'] = rvol
    if not rvol['passed']:
        return result('wait','session_rvol_not_above_two')
    if not boundary:
        return result('wait','resistance_below_hod_unavailable')
    if saved_exit and now <= saved_exit['at']:
        return result('wait','waiting_for_post_exit_breakout')
    if continuation:
        episode_high = prior_episode.get('high')
        evidence['continuation'] = dict(broken_level=deepcopy(saved_exit['level']),
            episode_id=prior_episode.get('episode_id'), episode_high=episode_high,
            spread_ignored=True)
        if type(episode_high) not in (int, float) or not isfinite(episode_high) or episode_high <= 0:
            return result('wait','macd_episode_high_unavailable')
        if not o.price > episode_high:
            return result('wait','waiting_for_macd_episode_high_break')
    elif prior_close is None or prior_at is None or not prior_close <= boundary['upper'] < o.price:
        # Initial entries still require a fresh resistance crossover.
        return result('wait','waiting_for_fresh_resistance_break')
    entry_price = round(ceil((o.ask-1e-10)/tick)*tick,10)
    # A completed trade can be above the current ask. Both execution and
    # completed-candle geometry must have an overhead target.
    target_floor = max(entry_price, o.price)
    plan = target_plan(d['rows'],boundary,target_floor,d.get('atr'),s,tick)
    if not plan:
        return result('wait','atr_qualified_resistance_target_unavailable')
    target = plan['target']
    stop_anchor = plan['stop_anchor']
    if continuation:
        stop = continuation_stop(saved_exit['level'], tick, s['stop_offset_bps'])
        stop_selection = dict(source='broken_resistance_lower_20bps',
            offset_bps=s['stop_offset_bps'], level=deepcopy(saved_exit['level']))
    else:
        swing = H.initial_swing_low(row,dict(lower=o.bid),now)
        if not swing:
            return result('wait','confirmed_swing_low_unavailable')
        stop = stop_price(entry_price,swing['lower'],tick)
        stop_selection = swing
    earned_stop = continuation_stop(stop_anchor,tick,s['stop_offset_bps'])
    if (earned_stop is not None and o.price > stop_anchor['upper']
            and earned_stop < o.bid):
        stop = earned_stop
        stop_selection = dict(source='target_predecessor_resistance_lower_offset',
            offset_bps=s['stop_offset_bps'],level=deepcopy(stop_anchor))
    target_price = plan['target_price']
    if (stop is None or not 0 < stop < o.bid <= o.ask <= entry_price
            or target_price <= target_floor):
        return result('wait','invalid_executable_stop_or_target')
    active = dict(level=deepcopy(boundary),target_level=deepcopy(target),confirmed_at=now,
                  stop=stop,maximum_buy_price=entry_price,hod=prior_hod,
                  macd_episode=deepcopy(prior_episode),stop_anchor_level=deepcopy(stop_anchor),
                  target_plan=deepcopy(plan))
    _track_level(state, boundary, 'continuation_support' if continuation else 'initial_breakout', now)
    _track_level(state, target, 'profit_target', now)
    _track_level(state, stop_anchor, 'protective_stop_anchor', now)
    state.update(r1_entry=active,initial_stop=stop,active_stop=stop,
        structural_profit_targets=[target_price],entry_reference_price=entry_price,
        entry_at=o.observed_at.isoformat(),entries=state.get('entries',0)+1,
        last_exit_reason='',entry_acquisition_exit_latched=False)
    state.pop('r1_stop_error', None)
    return result('enter_long','r1_macd_episode_continuation' if continuation else 'r1_resistance_breakout',Status.ENTRY_PENDING,
        invalidation_price=stop,profit_target_price=target_price,
        capital_request=CapitalRequest(mode='mandate_fraction',value=s['cash_fraction'],maximum_quantity=s['maximum_quantity'],allow_replacement=False),
        order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
        metadata=dict(initial_stop=stop,active_stop=stop,profit_targets=[target_price],
            profit_target=target_price,mandatory_broker_target=True,maximum_buy_price=entry_price,
            wait_for_capital=False,entry_selection=deepcopy(boundary),initial_stop_selection=stop_selection,
            profit_target_selection=dict(deepcopy(target),target_price=target_price,
                selection_method=plan['selection'],immediate_gap_atr=plan['immediate_gap_atr'],
                halfway_price=plan['halfway_price']),stop_anchor_level=deepcopy(stop_anchor),
            **({'r1_fixed_stop':dict(price=stop,source=stop_selection['source'],
                offset_bps=s['stop_offset_bps'])} if stop_selection.get('source') else
               {'r1_stop_bounds':dict(swing_lower=stop_selection['lower'],tick_size=tick)}),
            unified_structural_trigger={'current_snapshot':{'levels':[dict(boundary,entry_boundary=boundary['upper'])],
                'session_high':prior_hod,'selected_at':o.observed_at.isoformat(),'frozen_at_entry':True}}))

"""Causal setup entry and position-owned consolidation breakout phase."""
from copy import deepcopy
from math import isfinite


def fresh_support_momentum(assessment, facts, *, now, maximum_age, minimum_acceleration):
    """Alternative entry evidence; never manufactures a MACD episode."""
    checks = assessment.get('checks') or {}
    swing = assessment.get('swing') or {}
    stamp = assessment.get('observed_at')
    confirmed = swing.get('confirmed_at')
    pivot = swing.get('pivot_at')
    fast, slow = facts.get('trade_rate_10s'), facts.get('trade_rate_60s')
    values = (now, stamp, confirmed, pivot, fast, slow)
    valid = all(type(v) in (int, float) and isfinite(v) for v in values)
    required = {'prior_range', 'fresh_support', 'rising_close', 'green_candle',
        'risk_limit', 'range_limit', 'extension_limit'}
    passed = bool(valid and required <= checks.keys() and all(v is True for v in checks.values())
        and pivot <= confirmed <= stamp <= now
        and 0 <= now-confirmed <= maximum_age and slow > 0
        and fast/slow >= minimum_acceleration)
    return dict(passed=passed, observed_at=stamp, support_confirmed_at=confirmed,
        support_age_s=now-confirmed if valid else None,
        trade_rate_acceleration=fast/slow if valid and slow > 0 else None,
        maximum_support_age_s=maximum_age, minimum_trade_rate_acceleration=minimum_acceleration)


def observe(state, market, settings, fresh):
    if state.get('session') != market.get('session'):
        state.clear()
        state.update(session=market.get('session'), bars=[])
    if not fresh or not market.get('bar'):
        return
    bar = market['bar']
    if bar['end'] <= state.get('at', 0):
        return
    if settings.get('setup_minimum_300s_range_pct',0):
        # This activity window is independent of short-range gaps and MACD
        # episodes. Store only observed completed bars, never synthetic bars.
        history=[b for b in state.get('range_bars_300s',[]) if bar['end']-300<b['end']<bar['end']]
        state['range_bars_300s']=[*history,dict(end=bar['end'],high=bar['high'],low=bar['low'])]
    if settings.get('setup_minimum_60s_progress_pct',0):
        # Separate from the short consolidation range: sparse observed bars
        # remain usable as as-of references, but are never manufactured.
        history=[b for b in state.get('progress_bars',[]) if bar['end']-65<=b['end']<bar['end']]
        state['progress_bars']=[*history,dict(end=bar['end'],close=bar['close'])]
    bars = state['bars']
    if bars:
        gap=bar['time']-bars[-1]['end']
        if gap<0 or gap>settings.get('setup_maximum_bar_gap_s',0):
            bars = []
    bars = [b for b in bars if b['end'] > bar['time'] - settings['setup_range_seconds']]
    state['range'] = (dict(high=max(b['high'] for b in bars), low=min(b['low'] for b in bars),
        start=bars[0]['time'], end=bars[-1]['end'], count=len(bars))
        if len(bars) >= settings['setup_minimum_bars'] else None)
    state.update(at=bar['end'], bars=[*bars, deepcopy(bar)])
    if state.get('episode') != market.get('episode'):
        state.update(episode=market.get('episode'), episode_high=None, episode_low=None, body_high=None)
    state['prior_episode_high'] = state.get('episode_high')
    if market.get('episode') is not None:
        state['episode_high'] = max(state.get('episode_high') or bar['high'], bar['high'])
        state['episode_low'] = min(state.get('episode_low') or bar['low'], bar['low'])
        state['body_high'] = max(state.get('body_high') or 0, bar['open'], bar['close'])


def entry_range(state,market,minimum):
    at=market['bar']['end'];history=state.get('range_bars_300s',[])
    evidence=dict(observed_at=at,minimum_pct=minimum,lookback_seconds=300,observed_bars=len(history))
    if not history or history[-1]['end']!=at:
        return dict(**evidence,passed=False,reason='current_completed_bar_missing')
    if any(not all(isfinite(b[k]) for k in ('end','low','high'))
            or not at-300<b['end']<=at or not 0<b['low']<=b['high'] for b in history) or any(
            a['end']>=b['end'] for a,b in zip(history,history[1:])):
        return dict(**evidence,passed=False,reason='invalid_completed_history')
    low=min(b['low'] for b in history);high=max(b['high'] for b in history)
    span=(high/low-1)*100
    return dict(**evidence,passed=span+1e-9>=minimum,
        reason='' if span+1e-9>=minimum else 'range_below_minimum',
        oldest_at=history[0]['end'],high=high,low=low,range_pct=span)


def entry_progress(state,market,minimum):
    bar=market['bar'];at=bar['end'];history=state.get('progress_bars',[])
    evidence=dict(observed_at=at,minimum_pct=minimum,lookback_seconds=60,maximum_reference_staleness_seconds=5)
    if not history or history[-1]['end']!=at:
        return dict(**evidence,passed=False,reason='current_completed_bar_missing')
    prior=next((b for b in reversed(history) if b['end']<=at-60),None)
    if not prior or at-prior['end']>65:
        return dict(**evidence,passed=False,reason='historical_reference_missing_or_stale')
    progress=(bar['close']/prior['close']-1)*100
    return dict(**evidence,passed=progress+1e-9>=minimum,
        reason='' if progress+1e-9>=minimum else 'progress_below_minimum',
        reference_at=prior['end'],reference_close=prior['close'],close=bar['close'],progress_pct=progress)


def add_candle_quality(bar,maximum):
    span=bar['high']-bar['low']
    fraction=(bar['high']-max(bar['open'],bar['close']))/span if span>0 else 0.
    return dict(observed_at=bar['end'],upper_wick_fraction=fraction,maximum=maximum,
        passed=fraction<=maximum+1e-12)


def phase(entry, market, fresh, minimum_progress_r=0.):
    setup = entry.get('setup')
    if not setup or not fresh or setup['phase'] == 'post_breakout':
        return False
    bar = market['bar']
    if minimum_progress_r:
        fill=entry.get('initial_fill_price',0)
        risk=entry.get('initial_risk',0)
        threshold=fill+minimum_progress_r*risk if fill>0 and risk>0 else None
        ready=threshold is not None and bar['close']>=threshold-1e-9
        setup['phase_progress']=dict(initial_fill=fill,initial_risk=risk,
            required_r=minimum_progress_r,threshold=threshold,close=bar['close'],
            observed_at=bar['end'],ready=ready)
        if not ready:
            return False
    if (bar['end'] > entry['confirmed_at'] and bar['close'] >= bar['open']
            and bar['close'] > setup['breakout_threshold']):
        setup.update(phase='post_breakout', breakout_at=bar['end'])
        return True
    return False


def evidence(state, entry):
    return dict(**{k:state.get(k) for k in ('episode', 'episode_high', 'episode_low', 'body_high')},
        **deepcopy(entry.get('setup') or dict(phase='seeking_setup', range=state.get('range'))))


def swing_key(swing):
    return str((swing.get('scale'), swing.get('pivot_at'), swing.get('lower')))


def recovery_observe(state, entry, market, observation, stop, row, fresh, *, preserve_peak=False, stop_gain_guard=False):
    """Persist filled lifecycle failures and retire breached support across positions."""
    if observation.position_quantity > 0 and entry:
        body_high = market.get('body_high', 0)
        held=state.get('held',{})
        if preserve_peak:
            previous_peak = held.get('body_high', 0) if held.get('entry_at') == entry['confirmed_at'] else 0
            body_high = max(previous_peak or 0, body_high or 0)
        state['held'] = dict(entry_at=entry['confirmed_at'], setup=deepcopy(entry.get('setup', {})),
            stop=stop, body_high=body_high)
        if stop_gain_guard:
            initial=float(entry.get('initial_fill_price') or 0.)
            state['held']['initial_fill_price']=initial
            remembered=held.get('entry_at')==entry['confirmed_at'] and held.get('stop_above_initial_fill',False)
            state['held']['stop_above_initial_fill']=bool(remembered or initial>0 and stop>initial+1e-9)
    elif state.get('held'):
        held = state.pop('held')
        state['last_exit'] = dict(at=observation.observed_at.timestamp(), **held)
    retired = state.setdefault('retired_swings', {})
    if fresh and market.get('bar'):
        bar = market['bar']
        for swing in row.get('local_swings', []) + row.get('confirmed_swings', []):
            if swing.get('side') in (1, 'support') and swing.get('confirmed_at', float('inf')) <= bar['end']:
                if bar['low'] < swing.get('lower', 0) - 1e-9:
                    retired[swing_key(swing)] = bar['end']
    return {**row, **{field:[s for s in row.get(field, []) if swing_key(s) not in retired]
        for field in ('local_swings', 'confirmed_swings')}}


def recovery_permission(state, swing, market, *, stop_gain_guard=False, tight_base=False, unprotected_reentry=False, entry_reclaim=False, regular_session_start=0., regular_full_range=False):
    previous = state.get('last_exit')
    if not previous:
        return '', 'building'
    if swing_key(swing) in state.get('retired_swings', {}):
        return 'breached_setup_swing', ''
    if swing['pivot_at'] <= previous['at'] or swing['confirmed_at'] <= previous['at']:
        return 'waiting_for_new_support_after_exit', ''
    failed_entry = previous['setup'].get('entry_failure_recovery')
    if failed_entry and market['bar']['close'] <= failed_entry:
        return 'waiting_for_failed_setup_reclaim', ''
    # The caller supplies a boundary only for a qualified regular-session
    # early base. Keep prior recovery evidence and require a new post-open
    # support; a position exited after the open cannot use this exception.
    base_range = state.get('range') or {}
    full_regular_range = (base_range.get('start',0)>=regular_session_start
        and base_range.get('start',0)<base_range.get('end',0)<=market['bar'].get('end',0))
    if ((not regular_full_range or full_regular_range)
            and regular_session_start>0 and previous['at']<regular_session_start
            <=swing['pivot_at']<=swing['confirmed_at']<=market['bar']['end']):
        return '', 'building'
    # A preliminary attempt can cross its range without ever protecting a
    # profit. Require observed fill evidence; older checkpoints with unknown
    # protection history retain the stricter recovery rule.
    if (unprotected_reentry and stop_gain_guard
            and previous.get('initial_fill_price',0)>0
            and previous.get('stop_above_initial_fill') is False
            and (not entry_reclaim or market['bar']['close']>previous['initial_fill_price'])):
        return '', 'building'
    if (previous['setup'].get('phase') != 'post_breakout'
            and not (stop_gain_guard and previous.get('stop_above_initial_fill'))):
        return '', 'building'
    # A fresh higher base can prepare the next leg without reclaiming the top.
    if swing['lower'] > previous['stop']:
        return '', 'building'
    # A compact new base can start a new leg below the previous peak. Fresh
    # post-exit support and failed-entry reclaim were checked above; a broad
    # rebound does not get this exception. This never changes position phase.
    if stop_gain_guard and tight_base:
        return '', 'building'
    # Otherwise require recovery; never relabel a failed mature move as a setup.
    reclaim = max(previous['setup']['breakout_threshold'], previous['body_high'])
    if market['bar']['close'] > reclaim:
        # Recovery permits a new entry; it does not prove a breakout of that
        # new position's frozen range. Its own phase() must confirm that.
        return '', 'building'
    return 'waiting_for_post_move_recovery_or_higher_base', ''


def entry_failure(entry, market, settings, tick, fresh):
    """A short-lived, completed-candle failure of the frozen entry candle."""
    window = settings.get('setup_failure_seconds', 0)
    setup = entry.get('setup', {})
    initial = setup.get('entry_bar')
    if not window or not fresh or not initial or setup.get('phase') != 'building':
        return None
    bar = market.get('bar')
    if not bar or not market.get('contiguous'):
        return None
    elapsed = bar['end'] - entry['confirmed_at']
    buffer = tick * settings['setup_failure_buffer_ticks']
    threshold = initial['low'] - buffer
    if (0 < elapsed <= window and bar['close'] < bar['open']
            and round(bar['close'],9) <= round(threshold,9)):
        return dict(entry_low=initial['low'],threshold=threshold,close=bar['close'],
            confirmed_at=bar['end'],elapsed_seconds=elapsed,
            reclaim_threshold=max(initial['open'],initial['close'])+buffer)
    return None


def risk_trail_ready(entry, settings):
    return risk_progress_ready(entry,settings.get('setup_trail_activation_r',0))


def risk_progress_ready(entry, multiple):
    fill = entry.get('initial_fill_price', 0)
    risk = entry.get('initial_risk', 0)
    return bool(multiple and fill > 0 and risk > 0
                and entry.get('best_close',0) >= fill + multiple*risk - 1e-9)

"""Causal setup entry and position-owned consolidation breakout phase."""
from copy import deepcopy


def observe(state, market, settings, fresh):
    if state.get('session') != market.get('session'):
        state.clear()
        state.update(session=market.get('session'), bars=[])
    if not fresh or not market.get('bar'):
        return
    bar = market['bar']
    if bar['end'] <= state.get('at', 0):
        return
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


def phase(entry, market, fresh):
    setup = entry.get('setup')
    if not setup or not fresh or setup['phase'] == 'post_breakout':
        return False
    bar = market['bar']
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


def recovery_permission(state, swing, market, *, stop_gain_guard=False, tight_base=False):
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
    multiple = settings.get('setup_trail_activation_r', 0)
    fill = entry.get('initial_fill_price', 0)
    risk = entry.get('initial_risk', 0)
    return bool(multiple and fill > 0 and risk > 0
                and entry.get('best_close',0) >= fill + multiple*risk - 1e-9)

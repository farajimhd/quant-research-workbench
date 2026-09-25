"""Price-normalized MACD episodes and completed resistance-break management.

Episode state belongs to the ticker; gap samples and protection belong to a
single acquired position. Only past, causally available levels are used.
"""
from math import floor, isfinite, isclose
from dataclasses import replace
from datetime import datetime

from . import v5_breakout as v5, swing_gap
from .v5_hod_ladder import target

CONTRACT = 'swing-v5-macd-episode-1'


def strictly_below(price, boundary):
    # Ignore binary floating-point noise, not a trading-price buffer. Bands
    # retain their sub-tick precision; 3.53 and 3.5300000000000002 are equal.
    return price < boundary and not isclose(price, boundary, rel_tol=1e-12, abs_tol=0.)


def gap_bps(o):
    if o.price <= 0 or o.macd_line is None or o.macd_signal is None:
        return None
    value = (o.macd_line - o.macd_signal) / o.price * 10_000
    return value if isfinite(value) else None


def completed_macd(o, p, state):
    """Freeze all MACD operands, including the normalization close, at 1s close."""
    if p.get('macd_evaluation_mode') != 'completed_1s':
        return o
    sample = state.get('confirmed_episode_macd', {})
    if (o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
            and o.observed_at.timestamp() > sample.get('timestamp', 0)):
        sample = dict(timestamp=o.observed_at.timestamp(), observed_at=o.observed_at.isoformat(),
                      line=o.macd_line, signal=o.macd_signal, histogram=o.macd_histogram,
                      close=o.price, gap_bps=gap_bps(o))
        state['confirmed_episode_macd'] = sample
    values = dict(o.source_values)
    for name in ('line', 'signal', 'histogram'):
        source = {'value': sample.get(name), 'observed_at': sample.get('observed_at')}
        values[f'indicator.macd.{name}'] = source
        values[f'indicator.macd.{name}@1s'] = source
    return replace(o, macd_line=sample.get('line'), macd_signal=sample.get('signal'),
                   macd_histogram=sample.get('histogram'), source_values=values)


def acquisition_valid(o, p, state=None):
    gap = ((state or {}).get('confirmed_episode_macd', {}).get('gap_bps')
           if p.get('macd_evaluation_mode') == 'completed_1s' else gap_bps(o))
    settings = p['v5_breakout']
    return (gap is not None and gap >= settings['minimum_macd_gap_bps'] - 1e-9
            and o.execution_vwap is not None
            and o.price > o.execution_vwap * (1 + settings['vwap_offset_bps'] / 10_000))


def observe(o, p, state, *, typed_persistence=False):
    o = completed_macd(o, p, state)
    now = o.observed_at.timestamp()
    d = dict(state.get('v5_breakout_state') or {'contract': p['v5_breakout_contract']})
    if now < d.get('observed_at', 0):
        return
    gap = (state.get('confirmed_episode_macd', {}).get('gap_bps')
           if p.get('macd_evaluation_mode') == 'completed_1s' else gap_bps(o))
    normalizer = (state.get('confirmed_episode_macd', {}).get('close')
                  if p.get('macd_evaluation_mode') == 'completed_1s' else o.price)
    line_bps = o.macd_line / normalizer * 10000 if o.macd_line is not None and normalizer and normalizer > 0 else None
    opened = gap is not None and gap >= p['v5_breakout']['minimum_macd_gap_bps'] - 1e-9
    if gap is not None and not opened:
        d.pop('episode_started_at', None)
        if d.get('period_max', 0) > 0:
            d['previous_episode_body_high'] = d['period_max']
        if d.get('macd_open') or d.get('period_max', 0) > 0:
            d['episode_reset_at'] = o.observed_at.isoformat()
            d['episode_reset_gap_bps'] = gap
        d['period_max'] = 0.0
        for key in ('observed_episode_high', 'prior_observed_episode_high', 'high_observation'):
            d.pop(key, None)
    if opened and d.get('episode_started_at') is None:
        d['episode_started_at'] = now
    if opened and o.position_quantity > 0:
        # A position can span several qualifying episodes. Exiting and then
        # entering again in an episode it already occupied is still re-entry.
        state['last_held_macd_episode'] = d['episode_started_at']
    if (opened and (p.get('episode_management') or {}).get('same_episode_reentry_stop')
            and not (p.get('episode_management') or {}).get('completed_body_reentry_enabled')):
        # Snapshot before consuming this price: a breakout must not compare
        # against itself. Repeated evaluation of the same frame is idempotent.
        witness = [now, o.price]
        if d.get('high_observation') != witness:
            d['prior_observed_episode_high'] = d.get('observed_episode_high', 0.)
            d['observed_episode_high'] = max(d.get('observed_episode_high', 0.), o.price)
            d['high_observation'] = witness
    prior = d.get('levels', [])
    current_levels = v5.rows(o, p, typed_persistence=typed_persistence)
    d.update(observed_at=now, macd_open=opened, macd_gap_bps=gap, macd_line_bps=line_bps,
             prior_max=d.get('period_max', 0.0), decision_levels=prior, crossed=[])
    closed = o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
    window = (p.get('episode_management') or {}).get('entry_confirmation_window_ms', 0.)
    if window:
        if closed and now > d.get('closed_at', 0):
            d['entry_confirmation'] = dict(at=now, price=o.price, prior_max=d['prior_max'])
        elif d.get('entry_confirmation'):
            d['prior_max'] = d['entry_confirmation']['prior_max']
    if p.get('episode_management'):
        from .v5_episode_management import observe_entry
        observe_entry(o, d, closed, p['episode_management'])
    if p['v5_breakout_contract'] == v5.MACD_REJECTION_CONTRACT:
        if (p.get('episode_management') or {}).get('position_structure_enabled'):
            from .position_structure import observe as observe_structure
            observe_structure(o, d, closed, p['episode_management'], current_levels)
        elif p.get('episode_management'):
            from .v5_episode_management import observe_rejection as observe_attempt
            observe_attempt(o, d, current_levels, closed, p['episode_management'])
        else:
            observe_rejection(o, d, current_levels, closed)
    if closed and now > d.get('closed_at', 0):
        previous = d.get('closed_price')
        # Trade observations must not erase the last completed-bar witnesses
        # when the book changes role at the same close boundary.
        d['crossed'] = [r for r in d.get('closed_levels', prior)
                        if previous is not None and previous <= r['upper'] < o.price]
        if p['v5_breakout_contract'] == v5.MACD_REJECTION_CONTRACT:
            d['crossed'] = [r for r in d.get('closed_levels', prior)
                            if previous is not None and not strictly_below(r['upper'], previous)
                            and strictly_below(r['upper'], o.price)]
        if opened:
            d['period_max'] = max(d.get('period_max', 0.0), o.bar_open or o.price, o.price)
            # At the close boundary this candle is now part of the past.
            # A close-only decision compares this closing candle with the
            # already completed prior candles, then retains it for next time.
            if not (p.get('episode_management') or {}).get('entry_on_close'):
                d['prior_max'] = d['period_max']
        d.update(closed_at=now, closed_price=o.price, closed_levels=current_levels)
        if p.get('episode_management'):
            d['closed_atr'] = max(0., o.volatility) if isfinite(o.volatility) else 0.
            high, low = o.bar_high, o.bar_low
            d['entry_close_location'] = ((o.price-low)/(high-low)
                if high is not None and low is not None and high > low and low <= o.price <= high else None)
        d['decision_levels'] = list({r['unified_level_id']: r for r in [*current_levels, *d['crossed']]}.values())
        if (p.get('episode_management') or {}).get('defensive_structure_enabled'):
            from .expansion_stop import observe_break
            observe_break(o, d)
    d['levels'] = current_levels
    if (p.get('episode_management') or {}).get('adaptive_target_enabled'):
        from .adaptive_episode_target import observe as observe_target
        observe_target(o, d, p['episode_management'])
    d['entry_high_threshold'] = d['prior_max'] * (1 + p['v5_breakout']['episode_high_offset_bps'] / 10_000)
    if (p.get('episode_management') or {}).get('entry_range_seconds') and d.get('entry_range_high') is not None:
        d['entry_high_threshold'] = max(d['entry_high_threshold'],
            d['entry_range_high'] * (1 + p['v5_breakout']['episode_high_offset_bps'] / 10_000))
    if ((p.get('episode_management') or {}).get('completed_body_reentry_enabled')
            and d.get('episode_started_at') is not None
            and d['episode_started_at'] in (state.get('last_acquired_macd_episode'), state.get('last_held_macd_episode'))):
        high = d.get('prior_max', 0.) if closed else max(d.get('prior_max', 0.), d.get('period_max', 0.))
        d['entry_high_threshold'] = high*(1+p['v5_breakout']['episode_high_offset_bps']/10000)
    if o.position_quantity <= 0:
        for key in ('position_gaps', 'pending_target', 'fill_stop_initialized', 'profit_trail', 'expansion_stop',
                    'defensive_structure_failure'):
            d.pop(key, None)
    if (p.get('episode_management') or {}).get('continuation_detector_enabled'):
        from .continuation_detector import observe as observe_detector
        if (p['episode_management'].get('detector_candle_states_enabled')
                and getattr(o, 'candle_detector_state', None) is not None):
            d['continuation_detector'] = o.candle_detector_state
        else:
            observe_detector(o, d, p['episode_management'])
    state['v5_breakout_state'] = d


def observe_rejection(o, d, levels, closed):
    """Freeze a contacted band until a completed close resolves its attempt.

    Use actual observed prices while holding, not a candle high that might
    predate acquisition. A removed/role-changed level keeps its touch witness.
    """
    if o.position_quantity <= 0:
        d.pop('resistance_contacts', None)
        d.pop('resistance_rejection', None)
        return
    contacts = dict(d.get('resistance_contacts') or {})
    now = o.observed_at.timestamp()
    for level in levels:
        if not strictly_below(o.price, level['lower']) and not strictly_below(level['upper'], o.price):
            contacts.setdefault(str(level['unified_level_id']), {
                'level': dict(level), 'touched_at': o.observed_at.isoformat(), 'touch_price': o.price})
    if closed and now > d.get('closed_at', 0):
        rejected = [contact for contact in contacts.values() if strictly_below(o.price, contact['level']['lower'])]
        if rejected and not d.get('resistance_rejection'):
            witness = max(rejected, key=lambda row: row['level']['lower'])
            d['resistance_rejection'] = {**witness, 'confirmed_at': o.observed_at.isoformat(),
                                         'close_price': o.price, 'timeframe': '1s'}
        contacts = {key: contact for key, contact in contacts.items()
                    if not strictly_below(o.price, contact['level']['lower'])
                    and not strictly_below(contact['level']['upper'], o.price)}
    d['resistance_contacts'] = contacts


def initial_stop(o, p, entry_price):
    supports = [r for r in swing_gap.levels(o, {'minimum_p_norm': 0}, side=1)
                if r['side'] == 1 and r.get('scale') == 'major' and r['upper'] < entry_price]
    if supports:
        support = max(supports, key=lambda r: (r['confirmed_at_ms'], r['created_at_ms'], r['unified_level_id']))
        return v5.below(support, p), {'source': 'outer_swing_low', 'level': support}
    tick = p['execution']['tick_size']
    stop = floor(entry_price * (1 - p['v5_breakout']['initial_stop_pct'] / 100) / tick + 1e-9) * tick
    return stop, {'source': 'entry_percent', 'percent': p['v5_breakout']['initial_stop_pct']}


def overhead(levels, price, p):
    return sorted((r for r in levels if r['lower'] > price and target(r, p)['price'] > price),
                  key=lambda r: (r['lower'], r['upper'], str(r['unified_level_id'])))


def select(o, p, state):
    d = state.get('v5_breakout_state', {})
    policy = p.get('episode_management') or {}
    at_close = o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
    confirmation = d.get('entry_confirmation') or {}
    episode = d.get('episode_started_at')
    same_episode_reentry = (policy.get('same_episode_reentry_stop') and episode is not None
        and episode in (state.get('last_acquired_macd_episode'), state.get('last_held_macd_episode')))
    body_reentry = same_episode_reentry and policy.get('completed_body_reentry_enabled')
    if policy.get('same_episode_reentry_stop'):
        filled_at = (state.get('last_profit_target_fill') or {}).get('filled_at')
        if filled_at:
            fill_time = datetime.fromisoformat(filled_at).timestamp()
            # A cached pre-fill close cannot authorize another acquisition.
            if d.get('closed_at', 0.) <= fill_time:
                return {'reason': 'v5_waiting_for_post_target_close'}
            if policy.get('entry_confirmation_window_ms') and confirmation.get('at', 0.) <= fill_time:
                return {'reason': 'v5_waiting_for_post_target_close'}
    if policy.get('entry_on_close') and not at_close and not body_reentry:
        age_ms = (o.observed_at.timestamp()-confirmation.get('at', 0))*1000
        if not (policy.get('entry_confirmation_window_ms', 0) and
                0 <= age_ms < policy['entry_confirmation_window_ms']):
            return {'reason': 'v5_waiting_for_entry_close'}
    if not d.get('macd_open'):
        return {'reason': 'v5_macd_gap_below_minimum'}
    if policy.get('require_range_context') and not d.get('entry_range_context_valid'):
        return {'reason': 'v5_canonical_range_history_unavailable'}
    if policy.get('maximum_macd_line_bps') and (d.get('macd_line_bps') is None
            or d['macd_line_bps'] > policy['maximum_macd_line_bps']):
        return {'reason': 'v5_macd_trend_extended'}
    if not body_reentry and policy.get('entry_minimum_close_location') and (d.get('entry_close_location') is None
            or d['entry_close_location'] < policy['entry_minimum_close_location'] - 1e-12):
        return {'reason': 'v5_breakout_close_weak'}
    if ((policy.get('stop_atr_multiple', 0) or policy.get('rejection_atr_multiple', 0)
         or policy.get('profit_trail_atr_multiple', 0))
            and d.get('closed_atr', 0) <= 0):
        return {'reason': 'v5_volatility_unavailable'}
    if not acquisition_valid(o, p, state):
        return {'reason': 'v5_price_not_above_vwap'}
    # At the close, compare with earlier candles. During the execution window,
    # the confirming candle is already complete and belongs to the past too.
    reentry_high = (d.get('prior_max', 0.) if at_close else
                    max(d.get('prior_max', 0.), d.get('period_max', 0.)))
    if not body_reentry:
        reentry_high = max(reentry_high, d.get('prior_observed_episode_high', 0.))
    threshold = d.get('prior_max', 0.0) * (1 + p['v5_breakout']['episode_high_offset_bps'] / 10_000)
    if same_episode_reentry:
        threshold = max(threshold, reentry_high*(1+p['v5_breakout']['episode_high_offset_bps']/10000))
    if policy.get('entry_range_seconds') and not body_reentry:
        if d.get('entry_range_high') is None:
            return {'reason': 'v5_recent_range_unavailable'}
        threshold = max(threshold, d['entry_range_high']*(1+p['v5_breakout']['episode_high_offset_bps']/10_000))
    if not strictly_below(threshold, o.price):
        return {'reason': 'v5_period_high_not_reclaimed'}
    confirmation_threshold = d.get('prior_max', 0.0)*(1+p['v5_breakout']['episode_high_offset_bps']/10000)
    if policy.get('entry_range_seconds') and not body_reentry:
        confirmation_threshold = max(confirmation_threshold, d['entry_range_high']*(1+p['v5_breakout']['episode_high_offset_bps']/10000))
    if not body_reentry and policy.get('entry_confirmation_window_ms') and not strictly_below(confirmation_threshold, confirmation.get('price', 0)):
        return {'reason': 'v5_breakout_close_not_confirmed'}
    levels = d.get('decision_levels', [])
    above = overhead(levels, max(o.price, o.ask), p)
    ordinal = p['v5_breakout']['initial_target_ordinal']
    if len(above) < ordinal:
        return {'reason': 'v5_second_target_unavailable'}
    selected = target(above[ordinal - 1], p)
    if policy.get('adaptive_target_enabled'):
        from .adaptive_episode_target import select as select_target
        selected = select_target(above, d, p)
        if selected is None:
            return {'reason': 'v5_second_target_unavailable'}
    stop, stop_selection = initial_stop(o, p, o.price)
    if same_episode_reentry:
        high = reentry_high
        if not isfinite(high) or high <= 0:
            return {'reason': 'v5_reentry_episode_high_unavailable'}
        tick = p['execution']['tick_size']
        offset = policy['reentry_stop_offset_bps']
        stop = floor((high-max(tick, high*offset/10000))/tick+1e-9)*tick
        stop_selection = dict(source='macd_episode_high', episode_started_at=episode,
                              high=high, offset_bps=offset, tick_size=tick)
    if stop <= 0 or stop >= (o.price if body_reentry else min(o.price, o.bid)):
        return {'reason': 'v5_stop_already_triggered'}
    return dict(reason='', stop=stop, stop_selection=stop_selection,
                target=selected['price'], target_selection=selected,
                broken_at=o.observed_at.timestamp(), references=levels,
                session_high=o.structural_session_high, interval_based=True,
                entry_atr=d.get('closed_atr', 0.), episode_started_at=episode)


def sample_gaps(d, levels, anchor):
    """Freeze each adjacent level pair once during this position's lifetime."""
    samples = dict(d.get('position_gaps') or {})
    above = sorted((r for r in levels if r['lower'] > anchor['lower']),
                   key=lambda r: (r['lower'], r['upper'], str(r['unified_level_id'])))
    chain = [anchor, *above[:2]]
    for left, right in zip(chain, chain[1:]):
        key = str(left['unified_level_id']) + '->' + str(right['unified_level_id'])
        samples.setdefault(key, right['lower'] - left['lower'])
    d['position_gaps'] = samples
    return sum(samples.values()) / len(samples) if samples else None


def manage(o, p, state):
    d = state['v5_breakout_state']
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    if not d.get('fill_stop_initialized') and o.average_price > 0:
        # Freeze the selected swing at entry; do not discover a new initial
        # anchor after acquisition. Rebase only the percentage fallback.
        selection = state.get('v5_entry_selection', {}).get('stop_selection', {})
        if selection.get('source') == 'entry_percent':
            tick = p['execution']['tick_size']
            current = floor(o.average_price * (1 - p['v5_breakout']['initial_stop_pct'] / 100) / tick + 1e-9) * tick
        d['fill_stop_initialized'] = True
        entry_levels = state.get('v5_entry_selection', {}).get('references', d['decision_levels'])
        below = [r for r in entry_levels if r['upper'] < o.average_price]
        if below:
            sample_gaps(d, entry_levels, max(below, key=lambda r: r['upper']))
    for broken in d.get('crossed', []):
        if (p.get('episode_management') or {}).get('expansion_stop_enabled'):
            continue
        proposed = v5.below(broken, p)
        multiple = (p.get('episode_management') or {}).get('stop_atr_multiple', 0.)
        if multiple:
            atr = d.get('closed_atr', 0.)
            if atr <= 0:
                continue
            tick = p['execution']['tick_size']
            proposed = min(proposed, floor((broken['lower']-multiple*atr)/tick+1e-9)*tick)
        current = max(current, proposed)
        if (p.get('episode_management') or {}).get('adaptive_target_enabled'):
            continue
        average = sample_gaps(d, d['decision_levels'], broken)
        above = overhead(d['decision_levels'], max(o.price, o.ask), p)
        if average is None or len(above) < 2:
            continue
        reference = above[0]['lower'] + average
        chosen = min(above[1:], key=lambda r: (abs(r['lower'] - reference), r['lower']))
        selected = dict(target(chosen, p), average_gap=average, target_reference=reference,
                        broken_level_id=broken['unified_level_id'], confirmed_at=o.observed_at.isoformat())
        existing = max([*(state.get('structural_profit_targets') or [0]),
                        (d.get('pending_target') or {}).get('price', 0)])
        if selected['price'] > existing:
            d['pending_target'] = selected
    if (p.get('episode_management') or {}).get('adaptive_target_enabled'):
        from .adaptive_episode_target import select as select_target
        s = d.get('adaptive_target') or {}
        # One proposal for all breaks at this close; no intrabar target chase.
        if (d.get('crossed') and d.get('macd_open') and not s.get('paused')
                and s.get('closed_at') == o.observed_at.timestamp()):
            above = overhead(d['decision_levels'], max(o.price, o.ask), p)
            selected = select_target(above, d, p) if above else None
            existing = max([*(state.get('structural_profit_targets') or [0]),
                            (d.get('pending_target') or {}).get('price', 0)])
            if selected and selected['price'] > existing:
                d['pending_target'] = dict(selected, confirmed_at=o.observed_at.isoformat(),
                    broken_level_ids=[r['unified_level_id'] for r in d['crossed']])
    if (p.get('episode_management') or {}).get('expansion_stop_enabled'):
        from .expansion_stop import manage as manage_expansion_stop
        current = manage_expansion_stop(o, p, state, current)
    if p.get('episode_management'):
        from .v5_episode_management import profit_trail
        current = profit_trail(o, d, state, p['episode_management'], p['execution']['tick_size'], current)
    return current

"""Causal V5 resistance breakout policy; no ticker or session-specific rules."""
from math import ceil, floor, isfinite

from . import swing_gap

CONTRACT = 'swing-v5-breakout-1'
STAGED_CONTRACT = 'swing-v5-staged-breakout-1'
STAGED_CONTINUOUS_CONTRACT = 'swing-v5-staged-breakout-2'
INTERVAL_CONTRACT = 'swing-v5-interval-breakout-1'
HOD_CONTRACT = 'swing-v5-hod-ladder-1'
MACD_GAP_CONTRACT = 'swing-v5-macd-gap-1'
MACD_EPISODE_CONTRACT = 'swing-v5-macd-episode-1'
MACD_REJECTION_CONTRACT = 'swing-v5-macd-episode-2'
DEFAULTS = dict(direction_window_ms=400., maximum_sample_gap_ms=250.,
                breakout_lifetime_ms=1000., stop_offset_bps=5., target_offset_ticks=1,
                minimum_selection_score=30., entry_resistance_count=3)


def enabled(parameters):
    return parameters.get('v5_breakout_contract') in (CONTRACT, STAGED_CONTRACT, STAGED_CONTINUOUS_CONTRACT, INTERVAL_CONTRACT, HOD_CONTRACT, MACD_GAP_CONTRACT, MACD_EPISODE_CONTRACT, MACD_REJECTION_CONTRACT)


def staged(parameters):
    return parameters.get('v5_breakout_contract') in (STAGED_CONTRACT, STAGED_CONTINUOUS_CONTRACT, INTERVAL_CONTRACT, HOD_CONTRACT, MACD_GAP_CONTRACT, MACD_EPISODE_CONTRACT, MACD_REJECTION_CONTRACT)


def continuous(parameters):
    return parameters.get('v5_breakout_contract') in (STAGED_CONTINUOUS_CONTRACT, INTERVAL_CONTRACT, HOD_CONTRACT, MACD_GAP_CONTRACT, MACD_EPISODE_CONTRACT, MACD_REJECTION_CONTRACT)


def episode(parameters):
    return parameters.get('v5_breakout_contract') in (MACD_EPISODE_CONTRACT, MACD_REJECTION_CONTRACT)


def configure(parameters):
    if not enabled(parameters):
        raise ValueError('Unknown V5 breakout contract')
    if not parameters.get('swing_evidence_contract'):
        raise ValueError('V5 breakout requires MACD entry rules')
    if parameters.get('swing_gap_contract') or parameters.get('swing_momentum_contract'):
        raise ValueError('V5 breakout must not inherit another structural policy')
    settings = dict(DEFAULTS, **parameters.get('v5_breakout', {}))
    if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in settings.values()):
        raise ValueError('V5 breakout settings must be finite and positive')
    if not 30 <= settings['minimum_selection_score'] <= 100:
        raise ValueError('V5 grade must be between 30 and 100')
    for key in ('entry_resistance_count', 'target_offset_ticks'):
        if type(settings[key]) is not int:
            raise ValueError(key+' must be an integer')
    parameters['v5_breakout'] = settings
    if parameters.get('v5_breakout_contract') == MACD_GAP_CONTRACT or episode(parameters):
        settings.setdefault('vwap_offset_bps', 10.)
    if episode(parameters):
        from .v5_episode_management import configure as configure_episode_management
        configure_episode_management(parameters)
        parameters.setdefault('macd_evaluation_mode', 'intrabar')
        if parameters['macd_evaluation_mode'] not in ('intrabar', 'completed_1s'):
            raise ValueError('MACD evaluation mode must be intrabar or completed_1s')
        settings.setdefault('minimum_macd_gap_bps', 25.)
        settings.setdefault('episode_high_offset_bps', 15.)
        settings.setdefault('entry_acquisition_buffer_bps', 500.)
        settings.setdefault('initial_target_ordinal', 2)
        if type(settings['initial_target_ordinal']) is not int or settings['initial_target_ordinal'] < 2:
            raise ValueError('Initial target ordinal must be an integer of at least two')
    if staged(parameters):
        settings['entry_resistance_count'] = 4
        settings.setdefault('initial_stop_pct', 5.)
        if not 0 < settings['initial_stop_pct'] < 100:
            raise ValueError('Initial stop percentage must be between zero and 100')
    if parameters.get('v5_breakout_contract') == HOD_CONTRACT:
        for key in ('direction_window_ms', 'maximum_sample_gap_ms', 'breakout_lifetime_ms'):
            settings.pop(key, None)
    parameters.update(completed_macd_setup=False, require_completed_entry_candle=False,
                      require_breakout_reset=False)
    parameters['entry_body_breakout'] = dict(enabled=parameters.get('v5_breakout_contract') != MACD_GAP_CONTRACT and not episode(parameters), offset_ticks=1)
    parameters['entry_candle_confirmation'].update(enabled=False, require_closed_bar=False,
        evaluate_macd_intrabar=True, reject_bearish_close=False)
    parameters['structural_entry'].update(enabled=False, accept_live_price_above_entry_level=True)
    parameters['market_pressure']['enabled'] = False
    parameters['protection']['stop'].update(method='structure', cap_initial_stop_distance=False)
    parameters['protection']['trailing'].update(enabled=False, mode='qualified_support')
    parameters['reentry'].update(after_protective_exit=True, require_new_confirmation=False, cooldown_ms=0)
    parameters['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=False)
    parameters['momentum_management']['macd_backstop']['enabled'] = False


def rows(observation, parameters, *, typed_persistence=False):
    selected = sorted((r for r in swing_gap.levels(observation, {'minimum_p_norm': 0,
                   'include_retained_resistances': parameters.get('v5_hod_vwap_fallback', False)}, side=-1)
                   if r['book_version'] in ('causal-swing-closing-book-5','causal-swing-closing-book-6') and r['side'] == -1
                   and r['selection_score'] >= parameters['v5_breakout']['minimum_selection_score']),
                  key=lambda r: (r['upper'], r['lower'], str(r['unified_level_id'])))
    if typed_persistence:
        from .typed_v5_structural_row import validate_typed_v5_structural_row
        return [validate_typed_v5_structural_row(row) for row in selected]
    return selected


def below(level, parameters):
    tick = parameters['execution']['tick_size']
    offset = max(tick, level['lower'] * parameters['v5_breakout']['stop_offset_bps']/10000)
    return floor((level['lower']-offset)/tick+1e-9)*tick


def target(levels, broken, average, parameters):
    reference = broken['upper'] + average
    above = [r for r in levels if r['lower'] > reference]
    if len(above) < 2:
        return None
    row = above[1]
    tick = parameters['execution']['tick_size']
    price = (ceil(row['lower']/tick-1e-9)-parameters['v5_breakout']['target_offset_ticks'])*tick
    return dict(price=price, level=row, average_body=average, reference=reference)


def observe(observation, parameters, state, *, typed_persistence=False):
    if episode(parameters):
        from .v5_macd_episode import observe as operation
        return operation(observation, parameters, state, typed_persistence=typed_persistence)
    if parameters.get('v5_breakout_contract') == MACD_GAP_CONTRACT:
        from .v5_macd_gap import observe as operation
        return operation(observation, parameters, state, typed_persistence=typed_persistence)
    if parameters.get('v5_breakout_contract') == HOD_CONTRACT:
        from .v5_hod_ladder import observe as observe_ladder
        return observe_ladder(observation, parameters, state, typed_persistence=typed_persistence)
    now, price = observation.observed_at.timestamp(), observation.price
    policy = parameters['v5_breakout']
    keep_crossing = continuous(parameters)
    interval = parameters['v5_breakout_contract'] == INTERVAL_CONTRACT
    data = dict(state.get('v5_breakout_state') or {})
    previous = data.get('sample')
    if previous and now < previous[0]:
        return
    levels = rows(observation, parameters, typed_persistence=typed_persistence)
    crossed = []
    if 'market_data_update' in observation.evaluation_events and isfinite(price) and price > 0:
        history = [r for r in data.get('history', []) if now-2 <= r[0] < now]
        history.append((now, price))
        data['history'] = history[-1000:]
        if previous and 0 < now-previous[0] and (keep_crossing or now-previous[0] <= policy['maximum_sample_gap_ms']/1000):
            # Only boundaries already known before this trade can be broken.
            crossed = [r for r in data.get('levels', []) if previous[1] <= r['upper'] < price]
        data['sample'] = (now, price)
    data['levels'] = levels
    data['crossed'] = crossed
    if observation.position_quantity <= 0:
        data.pop('move', None)
        data.pop('stages', None)
        data.pop('pending_target', None)
        high = data.get('session_high') if interval else observation.structural_session_high
        vwap = observation.execution_vwap
        ranked = sorted((r for r in data.get('prior_levels', levels)
                         if interval or (high and r['upper'] <= high)), key=lambda r:r['upper'], reverse=True)
        ids = {r['unified_level_id'] for r in ranked[:policy['entry_resistance_count']]}
        eligible = [r for r in crossed if r['unified_level_id'] in ids and vwap and r['lower'] > vwap]
        data['entry_ranked'] = ranked[:policy['entry_resistance_count']]
        if interval:
            eligible = [r for r in crossed if vwap and r['lower'] > vwap]
        elif staged(parameters):
            eligible = [r for r in eligible if len(ranked) >= 4 and r['unified_level_id'] == ranked[3]['unified_level_id']]
        if interval and eligible:
            broken = max(eligible, key=lambda r:r['upper'])
            above = sorted((r for r in ranked if r['lower'] > broken['upper']), key=lambda r:r['upper'])
            if above and price < above[0]['lower']:
                refs = list(reversed(above[:3])) + [broken]
                data['breakout'] = dict(level=broken, at=now, references=refs, session_high=high,
                    interval_based=True, crossed_prior_hod=bool(high and previous and previous[1] <= high < price))
        elif eligible and not (keep_crossing and data.get('breakout')):
            data['breakout'] = dict(level=eligible[-1], at=now, references=ranked[:4], session_high=high)
        breakout = data.get('breakout')
        invalid = None
        if breakout and keep_crossing:
            live_ids = {r['unified_level_id'] for r in levels}
            if any(r['unified_level_id'] not in live_ids for r in breakout['references'][:-1]):
                invalid = 'reference_no_longer_qualified'
            elif price >= breakout['references'][-2]['lower']:
                invalid = 'passed_interval_ceiling' if interval else 'passed_r3_entry_corridor'
        if breakout and (price <= breakout['level']['upper'] or invalid or
                         (not keep_crossing and now-breakout['at'] > policy['breakout_lifetime_ms']/1000)):
            data['breakout_invalidated'] = dict(at=now, reason=(invalid or ('returned_below_interval' if interval else 'returned_below_r4')) if keep_crossing else 'expired_or_returned')
            data.pop('breakout', None)
    data['prior_levels'] = levels
    data['session_high'] = observation.structural_session_high
    move = dict(data.get('move') or {})
    if move and observation.source_timeframe == '1s' and 'bar_close' in observation.evaluation_events:
        # Exclude the partial candle that contains the resistance break.
        if now-1 >= move['started'] and now > move.get('last_bar', 0):
            opening = observation.bar_open
            if opening and isfinite(opening):
                move['sum'] += abs(price-opening)
                move['count'] += 1
                move['last_bar'] = now
        data['move'] = move
    state['v5_breakout_state'] = data


def select(observation, parameters, state):
    if episode(parameters):
        from .v5_macd_episode import select as operation
        return operation(observation, parameters, state)
    if parameters.get('v5_breakout_contract') == MACD_GAP_CONTRACT:
        from .v5_macd_gap import select as operation
        selected = operation(observation, parameters, state)
        selected['gate_evidence'] = evidence(observation, state)
        return selected
    selected = _select(observation, parameters, state)
    if continuous(parameters):
        selected['gate_evidence'] = evidence(observation, state)
    return selected


def evidence(observation, state):
    data = state.get('v5_breakout_state') or {}
    if data.get('contract') in (MACD_GAP_CONTRACT, MACD_EPISODE_CONTRACT, MACD_REJECTION_CONTRACT):
        return dict(observed_at=observation.observed_at.isoformat(), price=observation.price,
            contract=data['contract'], macd_open=data.get('macd_open'), macd_gap_bps=data.get('macd_gap_bps'),
            macd_line_bps=data.get('macd_line_bps'),
            reentry_restricted=data.get('exited'), prior_period_body_high=data.get('prior_max'),
            entry_high_threshold=data.get('entry_high_threshold'),
            entry_range_high=data.get('entry_range_high'), entry_range_samples=data.get('entry_range_samples'),
            entry_range_context=data.get('entry_range_context'),
            episode_reset_at=data.get('episode_reset_at'),
            episode_reset_gap_bps=data.get('episode_reset_gap_bps'),
            previous_episode_body_high=data.get('previous_episode_body_high'),
            confirmed_macd=state.get('confirmed_episode_macd'),
            profit_trail=data.get('profit_trail'),
            entry_confirmation=data.get('entry_confirmation'),
            entry_close_location=data.get('entry_close_location'),
            crossed_lower=[r['lower'] for r in data.get('crossed', [])],
            forming_resistance=data.get('forming'))
    breakout = data.get('breakout') or {}
    return dict(observed_at=observation.observed_at.isoformat(), price=observation.price,
        session_high=data.get('decision_high', observation.structural_session_high),
        contract=data.get('contract'), ladder_stage=dict(data.get('stages') or {}),
        ranked_upper=[r['upper'] for r in data.get('entry_ranked', [])],
        crossed_upper=[r['upper'] for r in data.get('crossed', [])],
        breakout_at=breakout.get('at'), frozen_upper=[r['upper'] for r in breakout.get('references', [])],
        invalidated=data.get('breakout_invalidated'), body_reference=state.get('entry_body_reference'))


def _select(observation, parameters, state):
    if parameters.get('v5_breakout_contract') == HOD_CONTRACT:
        from .v5_hod_ladder import select as select_ladder
        return select_ladder(observation, parameters, state)
    data = state.get('v5_breakout_state') or {}
    reference = state.get('entry_body_reference') or {}
    now, price = observation.observed_at.timestamp(), observation.price
    result = dict(reason='v5_body_reference_unavailable')
    if not reference or not reference['end'] <= now < reference['expires'] or 'market_data_update' not in observation.evaluation_events:
        return result
    threshold = max(reference['open'], reference['close'])
    state['entry_body_trigger'] = dict(reference, threshold=threshold, price=price)
    if price <= threshold:
        return dict(reason='v5_previous_body_not_broken')
    policy = parameters['v5_breakout']
    history = data.get('history', [])
    start = next((i for i in range(len(history)-1, -1, -1)
                  if history[i][0] <= now-policy['direction_window_ms']/1000), None)
    if start is None:
        return dict(reason='v5_direction_warming')
    window = history[start:]
    if (price <= window[0][1] or (not continuous(parameters) and any(b[0]-a[0] > policy['maximum_sample_gap_ms']/1000
                                   for a,b in zip(window, window[1:])))):
        return dict(reason='v5_direction_not_upward')
    vwap = observation.execution_vwap
    if not vwap or price <= vwap:
        return dict(reason='v5_price_not_above_vwap')
    breakout = data.get('breakout')
    if not breakout:
        return dict(reason='v5_waiting_for_resistance_break')
    broken = breakout['level']
    if broken['lower'] <= vwap:
        return dict(reason='v5_resistance_not_above_vwap')
    is_staged = staged(parameters)
    refs = breakout.get('references', [])
    interval = parameters['v5_breakout_contract'] == INTERVAL_CONTRACT
    if interval and (len(refs) < 2 or price >= refs[-2]['lower']):
        return dict(reason='v5_outside_entry_interval')
    if is_staged and not interval and (len(refs) != 4 or price >= refs[2]['lower']):
        return dict(reason='v5_entry_not_below_r3')
    tick = parameters['execution']['tick_size']
    stop = (floor(price*(1-policy['initial_stop_pct']/100)/tick+1e-9)*tick if is_staged else below(broken, parameters))
    if not 0 < stop < min(price, observation.bid):
        return dict(reason='v5_stop_already_triggered')
    target_row = refs[max(0,len(refs)-3)] if interval else (refs[1] if is_staged else None)
    selected = (dict(price=(ceil(target_row['lower']/tick-1e-9)-policy['target_offset_ticks'])*tick,
                     level=target_row, average_body=0., reference=broken['upper']) if is_staged
                else target(data['levels'], broken, 0., parameters))
    if not selected or selected['price'] <= max(price, observation.ask):
        return dict(reason='v5_second_target_unavailable')
    return dict(reason='', stop=stop, target=selected['price'], target_selection=selected,
                broken=broken, broken_at=breakout['at'], direction_reference=window[0],
                references=refs, session_high=breakout.get('session_high'), interval_based=interval)


def manage(observation, parameters, state):
    if episode(parameters):
        from .v5_macd_episode import manage as operation
        return operation(observation, parameters, state)
    if parameters.get('v5_breakout_contract') == MACD_GAP_CONTRACT:
        from .v5_macd_gap import manage as operation
        return operation(observation, parameters, state)
    if parameters.get('v5_breakout_contract') == HOD_CONTRACT:
        from .v5_hod_ladder import manage as manage_ladder
        return manage_ladder(observation, parameters, state)
    if staged(parameters):
        from .v5_staged import manage as staged_manage
        return staged_manage(observation, parameters, state)
    data = state['v5_breakout_state']
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    selection = state.get('v5_entry_selection')
    if not selection:
        return current
    move = data.get('move')
    if not move:
        move = dict(started=selection['broken_at'], sum=0., count=0,
                    broken=selection['broken'], seen={r['unified_level_id'] for r in data['levels']})
    # Keep assignment state JSON serializable.
    seen = set(move['seen'])
    crossed = [r for r in data['crossed'] if r['upper'] > move['broken']['upper']]
    if crossed:
        broken = max(crossed, key=lambda r:r['upper'])
        average = move['sum']/move['count'] if move['count'] else 0.
        selected = target(data['levels'], broken, average, parameters)
        if selected and selected['price'] > observation.price:
            data['pending_target'] = selected
        current = max(current, below(broken, parameters))
        move.update(started=observation.observed_at.timestamp(), sum=0., count=0, broken=broken)
    for row in data['levels']:
        if (row['unified_level_id'] not in seen and row['confirmed_at_ms']/1000 > selection['broken_at']
                and row['lower'] <= float(state.get('high_water_price') or observation.price)):
            current = max(current, below(row, parameters))
    move['seen'] = [r['unified_level_id'] for r in data['levels']]
    data['move'] = move
    return current

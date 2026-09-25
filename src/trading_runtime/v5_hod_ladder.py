"""Dynamic prior-HOD ladder with completed-close-only progression.

Entry snapshots are immutable journal evidence; management uses the current
causal ladder. Neither ranking changes nor newly observed rows imply a break.
"""
from math import ceil, floor

from . import v5_breakout as v5


def observe(observation, parameters, state, *, typed_persistence=False):
    now = observation.observed_at.timestamp()
    data = dict(state.get('v5_breakout_state') or {})
    if data.get('contract') != v5.HOD_CONTRACT:
        data = dict(contract=v5.HOD_CONTRACT)
    if now < data.get('observed_at', 0):
        return
    levels = v5.rows(observation, parameters, typed_persistence=typed_persistence)
    # Use the previous event's authority. In particular, this trade cannot
    # create a new HOD and simultaneously make a level eligible for a break.
    prior = data.get('levels', [])
    high = data.get('session_high')
    ranked = sorted((r for r in prior if high and r['upper'] <= high),
                    key=lambda r: r['upper'], reverse=True)[:4]
    vwap = observation.execution_vwap
    if parameters.get('v5_hod_sparse_entry'):
        above_vwap = [r for r in ranked if vwap and r['lower'] > vwap]
        if len(above_vwap) <= 2:
            ranked = above_vwap
    fallback_counts = (1, 2, 3) if parameters.get('v5_hod_sparse_entry') else (3,)
    if parameters.get('v5_hod_vwap_fallback') and len(ranked) in fallback_counts:
        if vwap and 0 < vwap < ranked[-1]['lower']:
            ranked.append(dict(unified_level_id='reference:vwap', reference_kind='vwap',
                               price=vwap, lower=vwap, upper=vwap, side=-1))
    data['previous_price'] = data.get('price')
    data['price'] = observation.price
    data.update(entry_ranked=ranked, decision_high=high, decision_levels=prior,
                crossed=[], new_levels=[], observed_at=now)
    old_ids = {r['unified_level_id'] for r in prior}
    data['new_levels'] = [r for r in levels if r['unified_level_id'] not in old_ids]
    if observation.source_timeframe == '1s' and 'bar_close' in observation.evaluation_events:
        previous = data.get('completed_close')
        if not previous or now > previous[0]:
            if previous:
                data['crossed'] = [r for r in ranked
                                   if previous[1] <= r['upper'] < observation.price]
            data['completed_close'] = [now, observation.price]
            data['break_close_at'] = now
    if observation.position_quantity <= 0:
        data.pop('stages', None)
        data.pop('pending_target', None)
    data.update(levels=levels, session_high=observation.structural_session_high)
    state['v5_breakout_state'] = data


def target(row, parameters):
    tick = parameters['execution']['tick_size']
    price = (ceil(row['lower']/tick-1e-9)
             - parameters['v5_breakout']['target_offset_ticks']) * tick
    return dict(price=price, level=row, reference=row['lower'])


def select(observation, parameters, state):
    data = state.get('v5_breakout_state') or {}
    reference = state.get('entry_body_reference') or {}
    now, price = observation.observed_at.timestamp(), observation.price
    if (not reference or not reference['end'] <= now < reference['expires']
            or 'market_data_update' not in observation.evaluation_events):
        return dict(reason='v5_body_reference_unavailable')
    if observation.bar_open is None or observation.bar_open <= 0:
        return dict(reason='v5_forming_open_unavailable')
    if price <= observation.bar_open:
        return dict(reason='v5_forming_candle_not_green')
    threshold = max(reference['open'], reference['close'])
    state['entry_body_trigger'] = dict(reference, threshold=threshold, price=price)
    if price <= threshold:
        return dict(reason='v5_previous_body_not_broken')
    if not observation.execution_vwap or price <= observation.execution_vwap:
        return dict(reason='v5_price_not_above_vwap')
    refs = data.get('entry_ranked', [])
    sparse = (parameters.get('v5_hod_sparse_entry') and len(refs) in (2, 3)
              and refs[-1].get('reference_kind') == 'vwap')
    if len(refs) != 4 and not sparse:
        return dict(reason=('v5_resistance_above_vwap_unavailable' if parameters.get('v5_hod_sparse_entry') else
                            'v5_three_resistances_and_lower_vwap_unavailable'
                            if parameters.get('v5_hod_vwap_fallback') else
                            'v5_four_resistances_below_hod_unavailable'))
    if price <= refs[-1]['upper']:
        return dict(reason='v5_price_not_above_r4')
    selected = target(refs[0], parameters)
    if sparse:
        above = [r for r in data['decision_levels'] if r['lower'] > refs[0]['upper']]
        ordinal = 4-len(refs)
        if len(above) < ordinal:
            return dict(reason='v5_sparse_upper_target_unavailable')
        selected = target(above[ordinal-1], parameters)
    if selected['price'] <= max(price, observation.ask):
        return dict(reason='v5_r1_target_not_above_entry')
    tick = parameters['execution']['tick_size']
    stop = floor(price*(1-parameters['v5_breakout']['initial_stop_pct']/100)/tick+1e-9)*tick
    if sparse:
        stop = max(stop, v5.below(refs[-1], parameters))
    if not 0 < stop < min(price, observation.bid):
        return dict(reason='v5_stop_already_triggered')
    return dict(reason='', stop=stop, target=selected['price'], target_selection=selected,
                broken=refs[-1], broken_at=now, references=refs, sparse_entry=bool(sparse),
                session_high=data['decision_high'], interval_based=False)


def manage(observation, parameters, state):
    data = state['v5_breakout_state']
    selection = state.get('v5_entry_selection')
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    if not selection:
        return current
    now = observation.observed_at.timestamp()
    stage = dict(data.get('stages') or dict(phase=0, last_break_at=0,
                                           target=selection['target'], advanced_at=None))
    refs = data.get('entry_ranked', [])
    crossed = {r['unified_level_id'] for r in data.get('crossed', [])}
    if (len(refs) == 4 and data.get('break_close_at') == now
            and now > max(stage['last_break_at'], selection['broken_at'])):
        phase = stage['phase']
        if refs[2]['unified_level_id'] in crossed:
            phase = max(phase, 1)
        if phase >= 1 and refs[1]['unified_level_id'] in crossed:
            phase = max(phase, 2)
        if phase > stage['phase']:
            stage['phase'] = phase
            stage['target_anchor'] = refs[0]
            stage['target_pending_phase'] = phase
        stage['last_break_at'] = now
    # A missing upper level never invents a price. Retry using known rows as
    # they arrive, while retaining the previous executable target.
    pending = stage.get('target_pending_phase')
    if pending:
        above = [r for r in data['decision_levels']
                 if r['lower'] > stage['target_anchor']['upper']]
        if len(above) >= pending:
            selected = target(above[pending-1], parameters)
            if selected['price'] > stage['target']:
                data['pending_target'] = selected
                stage.update(target=selected['price'], advanced_at=now)
            stage.pop('target_pending_phase', None)
    if stage['phase'] >= 1 and len(refs) == 4:
        current = max(current, v5.below(refs[3], parameters))
    watch_from = selection['broken_at'] if parameters.get('v5_hod_sparse_entry') else stage['advanced_at']
    if watch_from is not None:
        # Only genuinely new, causally confirmed resistance after the watch start
        # can provide the requested alarming local stop. A returned old row
        # or a future confirmation is not a newly forming resistance.
        for row in data['new_levels']:
            confirmed = row['confirmed_at_ms']/1000
            if (watch_from < confirmed <= now
                    and row['lower'] <= float(state.get('high_water_price') or observation.price)):
                current = max(current, v5.below(row, parameters))
    # Remember each known overhead band when price first tests it from below.
    # A subsequent rejection is actionable even if V5 has not created a new row.
    tested = stage.setdefault('tested_resistances', {})
    previous_price = data.get('previous_price')
    if previous_price is not None:
        for row in data.get('decision_levels', []):
            if (row['lower'] > selection['broken']['upper']
                    and previous_price < row['lower'] <= observation.price):
                tested[row['unified_level_id']] = row
    for row in tested.values():
        rejection_stop = v5.below(row, parameters)
        if observation.price <= rejection_stop:
            current = max(current, rejection_stop)
            stage['failed_resistance'] = row['unified_level_id']
    data['stages'] = stage
    return current

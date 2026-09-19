"""Candidate 324: causal 100 ms confirmation and event-driven protection."""
from copy import deepcopy
from dataclasses import replace
from math import ceil, floor, isfinite

from . import early_squeeze_breakout as E, historical_hod as H, vwap_resistance_ladder as V
from .signals import CapitalRequest

CONTRACT = 'early-squeeze-r1-100ms-v7'
CORRECTED_CONTRACT = 'early-squeeze-r1-100ms-v8'


def below(value, tick):
    return round((ceil(value / tick - 1e-9) - 1) * tick, 10)


def swing_key(row):
    return str((row.get('pivot_at'), row.get('confirmed_at'), row.get('lower')))


def observe_candle(frame, previous, row):
    """Session statistics precede activation; the current body never averages itself."""
    session = frame.as_of.astimezone(H.NY).date().isoformat()
    now = frame.as_of.timestamp()
    d = deepcopy(previous) if previous.get('session') == session else dict(
        session=session, green_body_sum=0., green_count=0, invalid_swings=[])
    if now <= d.get('at', 0):
        return d
    b = frame.bar
    if not all(isinstance(b.get(k), (int, float)) and isfinite(b[k]) and b[k] > 0
               for k in ('open', 'high', 'low', 'close')):
        raise ValueError('Invalid canonical 100 ms candle')
    d.update(at=now, previous_close=d.get('close'), prior_hod=d.get('hod'),
             mean_before=d['green_body_sum']/d['green_count'] if d['green_count'] else None,
             count_before=d['green_count'], close=b['close'], open=b['open'], low=b['low'],
             high=b['high'], hod=max(d.get('hod', 0), b['high']))
    body = b['close'] - b['open']
    if body > 0:
        d['green_body_sum'] += body
        d['green_count'] += 1
    invalid = set(d['invalid_swings'])
    for level in row.get('local_swings', []) + row.get('confirmed_swings', []):
        if level.get('side') in (1, 'support') and b['low'] < level.get('lower', 0):
            invalid.add(swing_key(level))
    d['invalid_swings'] = sorted(invalid)
    return d


def levels(o):
    rows = V.levels(o)
    for raw in (*o.structural_support_levels, *o.structural_resistance_levels, *o.structural_transition_levels):
        key = str(raw.get('unified_level_id', ''))
        if key in rows:
            rows[key]['transition_from'] = raw.get('transition_from')
    return rows


def is_resistance(r):
    return r.get('role') == 'resistance' or not r.get('role') and r.get('side') in (-1, 'resistance')


def entry_level(rows, hod):
    candidates = [r for r in rows.values() if hod and r['upper'] < hod and (
        is_resistance(r) or r.get('role') == 'transition' and r.get('transition_from') == 'resistance')]
    return max(candidates, key=lambda r: (r['upper'], r['unified_level_id']), default=None)


def evaluate(host, a, o, p, old_state):
    from .strategy_engine import AssignmentStatus as Status
    state = deepcopy(old_state)
    a = replace(a, parameters=p)
    corrected = p['early_squeeze_breakout_contract'] == CORRECTED_CONTRACT
    multiplier = 1.25 if corrected else 2.
    now, tick = o.observed_at.timestamp(), p['execution']['tick_size']
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    d = state.setdefault('squeeze_breakout', {})
    if d.get('session') != session:
        d.clear()
        d['session'] = session
    market = o.structural_detector_state or {}
    ctx, row = market.get('fast_squeeze_context', {}), market.get('row', {})
    fresh = (o.source_timeframe == '100ms' and 'bar_close' in o.evaluation_events
             and ctx.get('session') == session and ctx.get('at') == now and now > d.get('closed_at', 0))
    green = fresh and o.price > o.bar_open
    mean = ctx.get('mean_before')
    big = green and mean is not None and o.price-o.bar_open + 1e-10 >= multiplier*mean
    sample = o.source_values.get(E.SIGNAL, {})
    at = E.stamp(sample.get('observed_at'))
    if sample.get('value') is True and at and at <= o.observed_at and at.astimezone(H.NY).date().isoformat() == session:
        d['activated_at'] = min(d.get('activated_at', now), at.timestamp())
        d.setdefault('activation_event_id', sample.get('event_id'))
    rows = levels(o)
    filtered = bool(rows) and all(r.get('input_policy') == V.POLICY and r.get('seed_input_policy') == V.POLICY for r in rows.values())
    structure_fresh = filtered and 0 <= now-row.get('effective_at', 0) <= 1.000001
    if corrected:
        proof = market.get('fast_structure_evidence', {})
        cutoff, last_input = proof.get('as_of'), proof.get('max_input_timestamp')
        structure_fresh = (filtered and type(cutoff) in (int,float) and type(last_input) in (int,float)
            and isfinite(cutoff) and isfinite(last_input) and last_input <= cutoff
            and 0 <= now-cutoff < 1.000001)
    held, active = o.position_quantity > 0, state.get('squeeze_entry') or {}
    stop = float(state.get('active_stop') or 0)
    target = float((state.get('structural_profit_targets') or [0])[0])
    quote = V.fresh_quote(o)
    previous = d.get('close')
    prior_rows = d.get('levels', rows)
    crossed = []
    if fresh:
        if green and structure_fresh and previous is not None:
            crossed = [(k, r) for k, r in prior_rows.items() if is_resistance(r)
                       and min(previous, o.bar_low) <= r['upper'] < o.price]
            if corrected:
                stop_crossings = [r for r in prior_rows.values()
                    if (is_resistance(r) or r.get('role') == 'transition' and r.get('transition_from') == 'resistance')
                    and min(previous, o.bar_low) <= r['upper'] < o.price]
                if stop_crossings:
                    d['latest_broken_resistance'] = deepcopy(max(stop_crossings,
                        key=lambda r:(r['upper'],r['unified_level_id'])))
        if held and active:
            broken = active.setdefault('broken_levels', [])
            for key, _ in crossed:
                if key not in broken:
                    broken.append(key)
        d.update(close=o.price, closed_at=now, levels=deepcopy(rows))
        if active and (held or a.status == Status.ENTRY_PENDING) and not active.get('stopout_reference'):
            active['peak_close'] = max(active.get('peak_close', o.price), o.price)
    evidence = dict(contract=p['early_squeeze_breakout_contract'], filtered_v7=filtered,
        activation={k:d.get(k) for k in ('activated_at', 'activation_event_id')},
        candle_strength=dict(timeframe='100ms', body=o.price-o.bar_open if fresh else None,
            prior_green_count=ctx.get('count_before'), prior_mean_body=mean, multiplier=multiplier, qualifies=bool(big)))

    def emit(action, reason, status=None, **kw):
        metadata = dict(evidence, active_stop=state.get('active_stop'),
            profit_targets=list(state.get('structural_profit_targets') or []),
            position_resistance_breaks=len(active.get('broken_levels', [])),
            fixed_trail_distance=active.get('trail_distance'), trailing_bid_high=active.get('peak_price'),
            bid=o.bid, ask=o.ask, **kw.pop('metadata', {}))
        if action == 'exit':
            if reason != 'complete_position_liquidation':
                state['last_exit_reason'] = reason
            state['entry_acquisition_exit_latched'] = True
            metadata.update(position_fraction=1., cancel_entry_acquisition=True,
                reentry_after_fill=reason == 'protective_stop' or state.get('liquidation_origin_fill_role') in ('protective_stop','trailing_stop','protective_exit'))
        result = host._result(a, o, action, reason, float(action in ('enter_long', 'add_long')), 1.,
            state, status or a.status, metadata=metadata,
            order_intent=dict(execution_policy='adaptive_urgent', protection_profile='structural-single-target'), **kw)
        if action in ('enter_long', 'add_long'):
            result = replace(result, evaluation=replace(result.evaluation, intents=tuple(
                replace(i, reference_price=o.ask, metadata={**i.metadata, 'mandatory_broker_target':True,
                    'wait_for_capital':False}) for i in result.evaluation.intents)))
            if action == 'add_long':
                for intent in result.evaluation.intents:
                    active.setdefault('add_requests', {})[intent.intent_id] = list(metadata['squeeze_add_levels'])
        return result

    # A partially filled full target owns liquidation. Never add into it, or
    # freeze indefinitely because an acquisition callback changed the status.
    if held and (state.get('entry_acquisition_exit_latched') or a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0):
        remaining = max(0., o.position_quantity-o.pending_exit_quantity)
        if remaining > 1e-9:
            return emit('exit', 'complete_position_liquidation', Status.EXIT_PENDING, quantity=remaining)
        return emit('hold', 'exit_fill_pending', Status.EXIT_PENDING)
    if held:
        if not active:
            return emit('hold', 'position_entry_state_unavailable', Status.MANAGING)
        if not active.get('trail_distance') and o.average_price > stop > 0:
            active.update(trail_distance=o.average_price-stop, peak_price=o.average_price)
        if quote and o.bid <= stop:
            return emit('exit', 'protective_stop', Status.EXIT_PENDING, quantity=o.position_quantity)
        if state.get('manual_exit_requested'):
            return emit('exit', 'manual_exit', Status.EXIT_PENDING, quantity=o.position_quantity)
        results = []
        if quote and active.get('trail_distance'):
            active['peak_price'] = max(active.get('peak_price', o.bid), o.bid)
            proposal = round(floor((active['peak_price']-active['trail_distance'])/tick+1e-9)*tick, 10)
            desired = active.get('structural_stop', stop)
            if desired < o.bid:
                proposal = max(proposal, desired)
            if stop < proposal < o.bid:
                previous_stop = stop
                state['active_stop'] = stop = proposal
                results.append(emit('replace_protective_stop', 'fixed_distance_price_trail', Status.MANAGING,
                    quantity=o.position_quantity, invalidation_price=stop, metadata=dict(previous_stop=previous_stop)))
        # Re-rank only at completed 100 ms closes using causal V7 snapshots.
        if fresh and quote and structure_fresh:
            count = len(active.get('broken_levels', []))
            ordinal = 1 if count >= 6 else 2 if count >= 4 else 3
            overhead = sorted((r for r in rows.values() if is_resistance(r)
                and E.target_price(r, tick, E.CONTRACT) > o.ask), key=lambda r:(r['lower']+r['upper'], r['unified_level_id']))
            if len(overhead) >= ordinal:
                selected = overhead[ordinal-1]
                proposal = E.target_price(selected, tick, E.CONTRACT)
                if proposal > target:
                    state['structural_profit_targets'] = [proposal]
                    active['target_moves'] += 1
                    results.append(emit('replace_profit_target', 'resistance_target_advance', Status.MANAGING,
                        quantity=o.position_quantity, profit_target_price=proposal,
                        metadata=dict(previous_profit_target=target,
                            squeeze_previous_target_moves=active['target_moves']-1,
                            profit_target_selection=dict(level=deepcopy(selected), price=proposal, ordinal=ordinal))))
                    target = proposal
            if o.bid >= target > 0 and not any(r.evaluation.intents for r in results):
                return emit('exit', 'profit_target', Status.EXIT_PENDING, quantity=o.position_quantity)
        if green and structure_fresh and quote:
            pending = active.setdefault('pending_adds', {})
            seen = active.setdefault('added_levels', [])
            for key, level in crossed:
                if key not in seen:
                    pending.setdefault(key, deepcopy(level))
            for key in list(pending):
                if o.price <= pending[key]['upper']:
                    pending.pop(key)
            if pending and a.permissions.add and active.get('slice_notional', 0) > 0 and stop < o.bid <= o.ask < target:
                keys = sorted(pending)
                results.append(emit('add_long', 'green_resistance_break_addition', Status.MANAGING,
                    invalidation_price=stop, profit_target_price=target,
                    capital_request=CapitalRequest(mode='fixed_notional', value=active['slice_notional']*len(keys)),
                    metadata=dict(squeeze_add_levels=keys, slice_notional=active['slice_notional'])))
                seen.extend(keys)
                pending.clear()
        if results:
            return replace(results[-1], state=state, evaluation=replace(results[-1].evaluation,
                intents=tuple(i for r in results for i in r.evaluation.intents)))
        return emit('hold', 'fixed_distance_trail_and_resistance_target', Status.MANAGING)
    return evaluate_entry(host, a, o, p, state, d, rows, ctx, row, fresh, green, big,
                          structure_fresh, quote, evidence, emit)


def evaluate_entry(host, a, o, p, state, d, rows, ctx, row, fresh, green, big,
                   structure_fresh, quote, evidence, emit):
    from .strategy_engine import AssignmentStatus as Status
    now, tick = o.observed_at.timestamp(), p['execution']['tick_size']
    corrected = p['early_squeeze_breakout_contract'] == CORRECTED_CONTRACT
    if a.status == Status.ENTRY_PENDING or state.get('pending_capital_request'):
        return emit('wait', 'entry_fill_pending', Status.ENTRY_PENDING)
    if a.status in (Status.DISABLED, Status.PAUSED, Status.COMPLETED, Status.ERROR):
        return emit('wait', 'entry_permission_closed')
    if not a.permissions.observe or not (a.permissions.reenter if state.get('entries', 0) else a.permissions.enter):
        return emit('wait', 'entry_permission_closed')
    if not o.market_open or not 4 <= o.observed_at.astimezone(H.NY).hour < 20:
        return emit('wait', 'outside_entry_session')
    if now <= d.get('activated_at', float('inf')):
        return emit('wait', 'waiting_for_post_squeeze_candle')
    if not fresh:
        return emit('wait', 'waiting_for_completed_green_100ms')
    if not structure_fresh:
        return emit('wait', 'filtered_v7_unavailable')
    anchor = entry_level(rows, ctx.get('prior_hod'))
    setup = d.get('initial_breakout')
    if setup and (not anchor or setup['anchor']['unified_level_id'] != anchor['unified_level_id']
                  or o.price <= anchor['upper']):
        d.pop('initial_breakout', None)
        setup = None
    if anchor:
        if not setup and ctx.get('previous_close') is not None and min(ctx['previous_close'], o.bar_low) <= anchor['upper'] < o.price:
            setup = dict(anchor=deepcopy(anchor), breakout_at=now, peak_close=o.price)
            d['initial_breakout'] = setup
        elif setup:
            setup['anchor'] = deepcopy(anchor)
            setup['peak_close'] = max(setup['peak_close'], o.price)
    if not green:
        return emit('wait', 'waiting_for_completed_green_100ms')
    if not big:
        return emit('wait', 'green_body_below_1_25_session_average' if corrected else 'green_body_below_twice_session_average')
    quality_row = dict(effective_at=now, candle=dict(volume=o.bar_volume)) if corrected else row
    ready, quality = H.tradability(o, dict(p, structural_recovery=dict(H.QUALITY_DEFAULTS, **p['structural_recovery'])),
                                  quality_row, state, producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if not ready:
        return emit('wait', 'liquidity_or_spread_gate')
    vwap_source = 'indicator.vwap.execution_value@100ms' if corrected else 'indicator.vwap.execution_value@1s'
    source = o.source_values.get(vwap_source, {})
    at, vwap = E.stamp(source.get('observed_at')), source.get('value')
    evidence['vwap_gate'] = dict(source_id=vwap_source, value=vwap,
                               observed_at=source.get('observed_at'), close=o.price)
    if not at or not 0 <= now-at.timestamp() <= 1.000001 or type(vwap) not in (int,float) or not isfinite(vwap) or not 0 < vwap < o.price:
        return emit('wait', 'fresh_price_above_vwap_required')
    # No special post-stop prohibition: sufficient confirmation decides whether
    # a fresh current R1 setup or the frozen close-high recovery can enter.
    recovery = d.get('recovery')
    confirmed = bool(setup and anchor and o.price+1e-9 >= anchor['upper']+5*tick
                     and o.bar_high > o.bar_low and o.price >= o.bar_low+.75*(o.bar_high-o.bar_low))
    if confirmed:
        recovery = None
        desired = below(anchor['lower'], tick)
        stop_source = 'broken_resistance_lower'
    elif recovery and now > recovery['stopped_at'] and o.price > recovery['high']:
        anchor = recovery['anchor']
        if o.price+1e-9 < anchor['upper']+5*tick:
            return emit('wait', 'five_tick_upper_band_clearance_required')
        if corrected:
            # Changing the stop anchor must not tighten the recovery signal.
            anchor = d.get('latest_broken_resistance', anchor)
            desired = below(anchor['lower'], tick)
            stop_source = 'latest_broken_resistance_lower'
        else:
            invalid = set(ctx.get('invalid_swings', []))
            swing = H.initial_swing_low(row, dict(lower=float('inf')), now,
                pivot_not_before=recovery['breakout_at'],
                eligible=lambda s:s['lower'] > anchor['upper'] and swing_key(s) not in invalid)
            desired = below(swing['lower'], tick) if swing else round(floor(o.bar_open/tick+1e-9)*tick, 10)
            stop_source = 'confirmed_swing_above_resistance' if swing else 'last_completed_candle_open_offset'
    else:
        return emit('wait', 'waiting_for_confirmed_r1_or_recovery')
    stop = min(desired, below(o.bid, tick))
    overhead = sorted((r for r in rows.values() if is_resistance(r)
        and r['unified_level_id'] != anchor['unified_level_id'] and E.target_price(r,tick,E.CONTRACT) > o.ask),
        key=lambda r:(r['lower']+r['upper'],r['unified_level_id']))
    if len(overhead) < 3:
        return emit('wait', 'overhead_resistance_target_unavailable')
    target = E.target_price(overhead[2], tick, E.CONTRACT)
    if not quote or not 0 < stop < o.bid <= o.ask < target:
        return emit('wait', 'unrepresentable_stop_or_target')
    if corrected:
        d['latest_broken_resistance'] = deepcopy(anchor)
    for key in ('liquidation_origin_fill_role', 'liquidation_origin_reentry_after_fill',
                'profit_target_liquidation_required', 'target_replenishment_pending', 'last_exit_reason'):
        state.pop(key, None)
    state.update(squeeze_entry=dict(anchor=deepcopy(anchor), structural_stop=desired,
        breakout_at=recovery['breakout_at'] if recovery else setup['breakout_at'],
        peak_close=o.price, entry_price=o.ask, requested_at=now, added_levels=[],
        pending_adds={}, broken_levels=[], late=False, target_moves=0),
        initial_stop=stop, active_stop=stop, structural_profit_targets=[target],
        entry_reference_price=o.ask, entry_at=o.observed_at.isoformat(),
        entries=state.get('entries',0)+1, entry_acquisition_exit_latched=False)
    if corrected:
        state['squeeze_entry']['recovery_trigger_anchor'] = deepcopy(recovery['anchor'] if recovery else anchor)
    return emit('enter_long', 'stopout_close_high_reentry' if recovery else 'squeeze_r1_100ms_breakout', Status.ENTRY_PENDING,
        invalidation_price=stop, profit_target_price=target,
        capital_request=CapitalRequest(mode='mandate_fraction', value=1/3),
        metadata=dict(unreserved_cash_slice=True, entry_selection=deepcopy(anchor), stop_source=stop_source,
            structural_stop=desired, upper_band_clearance_ticks=5,
            frozen_reentry_high=recovery['high'] if recovery else None,
            profit_target_selection=dict(level=deepcopy(overhead[2]),price=target,ordinal=3),
            unified_structural_trigger={'current_snapshot':{'levels':[dict(anchor, entry_boundary=anchor['upper']+5*tick)],
                'session_high':ctx.get('prior_hod'),'selected_at':o.observed_at.isoformat(),'frozen_at_entry':True}}))

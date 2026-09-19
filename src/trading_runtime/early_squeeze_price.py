"""Versioned event-price strategies over midpoint-selected resistance."""
from copy import deepcopy
from dataclasses import replace
from math import floor, isfinite
from . import early_squeeze_breakout as E, historical_hod as H, vwap_resistance_ladder as V
from .early_squeeze_fast import below, levels, is_resistance
from .signals import CapitalRequest

CONTRACT = 'early-squeeze-r1-price-gap-v9'
STRICT_CONTRACT = 'early-squeeze-r1-price-high-v10'
EPISODE_CONTRACT = 'early-squeeze-r1-price-episode-v11'
RESISTANCE_CEILING_CONTRACT = 'early-squeeze-r1-price-resistance-ceiling-v12'
BROKEN_RESISTANCE_CEILING_CONTRACT = 'early-squeeze-r1-price-broken-resistance-ceiling-v13'
GREEN_CLOSE_CEILING_CONTRACT = 'early-squeeze-r1-price-green-close-ceiling-v14'
EPISODE_CONTRACTS = (EPISODE_CONTRACT, RESISTANCE_CEILING_CONTRACT,
                     BROKEN_RESISTANCE_CEILING_CONTRACT, GREEN_CLOSE_CEILING_CONTRACT)


def midpoint(row):
    return (row['lower'] + row['upper']) / 2


def eligible(row):
    return is_resistance(row) or row.get('role') == 'transition' and row.get('transition_from') == 'resistance'


def entry_level(rows, hod):
    return max((r for r in rows.values() if eligible(r) and hod and midpoint(r) < hod),
               key=lambda r:(midpoint(r), r['unified_level_id']), default=None)


def boundary(anchor, rows):
    above = [r for r in rows.values() if is_resistance(r) and midpoint(r) > midpoint(anchor)]
    if not above:
        return None
    next_level = min(above, key=lambda r:(midpoint(r), r['unified_level_id']))
    return dict(price=midpoint(anchor)+.1*(midpoint(next_level)-midpoint(anchor)),
                midpoint=midpoint(anchor), next_midpoint=midpoint(next_level),
                next_level_id=next_level['unified_level_id'], gap_fraction=.1)


def unbroken_resistance_ceiling(rows, price, stop):
    """Return the nearest still-unbroken resistance lower edge above the stop."""
    candidates = [r for r in rows.values() if eligible(r)
                  and r['lower'] >= stop - 1e-9 and price <= r['upper'] + 1e-9]
    return min(candidates, key=lambda r:(r['lower'], r['upper'], r['unified_level_id']), default=None)


def broken_resistance_ceiling(active, breakout_anchors, rows, price, confirmed_ids=None):
    """Advance only after price fully clears a resistance's upper edge."""
    current = active.get('trail_resistance_ceiling') or active['anchor']
    catalog = {r['unified_level_id']: r for r in breakout_anchors.values()}
    catalog.update({r['unified_level_id']: r for r in rows.values() if eligible(r)})
    cleared = [r for r in catalog.values() if (confirmed_ids is None or r['unified_level_id'] in confirmed_ids)
               and r['lower'] > current['lower'] + 1e-9
               and price > r['upper'] + 1e-9]
    if cleared:
        current = max(cleared, key=lambda r:(r['lower'], r['upper'], r['unified_level_id']))
    active['trail_resistance_ceiling'] = deepcopy(current)
    return current


def evaluate(host, a, o, p, old_state):
    from .strategy_engine import AssignmentStatus as Status
    episode = p['early_squeeze_breakout_contract'] in EPISODE_CONTRACTS
    resistance_ceiling = p['early_squeeze_breakout_contract'] == RESISTANCE_CEILING_CONTRACT
    broken_resistance_cap = p['early_squeeze_breakout_contract'] == BROKEN_RESISTANCE_CEILING_CONTRACT
    green_close_cap = p['early_squeeze_breakout_contract'] == GREEN_CLOSE_CEILING_CONTRACT
    strict = episode or p['early_squeeze_breakout_contract'] == STRICT_CONTRACT
    state = deepcopy(old_state)
    a = replace(a, parameters=p)
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
    sample = o.source_values.get(E.SIGNAL, {})
    at = E.stamp(sample.get('observed_at'))
    if sample.get('value') is True and at and at <= o.observed_at and at.astimezone(H.NY).date().isoformat() == session:
        d['activated_at'] = min(d.get('activated_at', now), at.timestamp())
        d.setdefault('activation_event_id', sample.get('event_id'))
    rows = levels(o)
    filtered = bool(rows) and all(r.get('input_policy') == V.POLICY and r.get('seed_input_policy') == V.POLICY for r in rows.values())
    proof = market.get('fast_structure_evidence', {})
    cutoff, last_input = proof.get('as_of'), proof.get('max_input_timestamp')
    structure_fresh = (filtered and type(cutoff) in (int,float) and type(last_input) in (int,float)
        and isfinite(cutoff) and isfinite(last_input) and last_input <= cutoff
        and 0 <= now-cutoff < 1.000001)
    held, active = o.position_quantity > 0, state.get('squeeze_entry') or {}
    stop = float(state.get('active_stop') or 0)
    target = float((state.get('structural_profit_targets') or [0])[0])
    quote = V.fresh_quote(o)
    sample_price = o.source_values.get('market.last_price', {})
    price_event = ('market_data_update' in o.evaluation_events
        and 'market.last_price' in o.changed_source_ids
        and E.stamp(sample_price.get('observed_at')) == o.observed_at
        and sample_price.get('value') == o.price
        and not any(s.startswith('quote:') for s in o.source_signal_ids))
    previous = d.get('trade_price', ctx.get('close') if ctx.get('at', now) < now else None)
    prior_hod = max(ctx.get('hod') or 0., d.get('trade_hod') or 0.)
    evidence_price = dict(price_event=price_event, previous_price=previous, prior_hod=prior_hod)
    prior_rows = d.get('levels', rows)
    crossed = []
    if episode and price_event and structure_fresh:
        # A failed band starts a new episode only below its lower edge.
        # Do not reset HOD, the held position's trail, or lifecycle targets.
        for key in list(d.get('breakout_highs', {})):
            band = rows.get(key) or d.get('breakout_anchors', {}).get(key)
            if band and o.price < band['lower']:
                d['breakout_highs'].pop(key)
                d['entered_levels'] = [value for value in d.get('entered_levels', []) if value != key]
                setup = d.get('initial_breakout')
                if setup and setup['anchor']['unified_level_id'] == key:
                    d.pop('initial_breakout', None)
                active.get('pending_adds', {}).pop(key, None)
                d.setdefault('reset_at', {})[key] = now
    prior_breakout_highs = dict(d.get('breakout_highs', {}))
    if strict and price_event:
        highs = d.setdefault('breakout_highs', {})
        for key in highs:
            highs[key] = max(highs[key], o.price)
    if price_event:
        if structure_fresh and previous is not None and o.price > previous:
            for key, level in prior_rows.items():
                limit = boundary(level, prior_rows)
                if eligible(level) and limit and previous < limit['price'] <= o.price:
                    crossed.append((key, level))
                    if strict:
                        d.setdefault('breakout_highs', {}).setdefault(key, o.price)
                        if episode:
                            d.setdefault('breakout_anchors', {})[key] = deepcopy(level)
            if crossed:
                d['latest_broken_resistance'] = deepcopy(max((r for _,r in crossed), key=midpoint))
        if held and active:
            broken = active.setdefault('broken_levels', [])
            for key, level in crossed:
                if is_resistance(level) and key not in broken:
                    broken.append(key)
        d.update(trade_price=o.price, trade_hod=max(prior_hod,o.price), levels=deepcopy(rows))
    if fresh:
        d['closed_at'] = now
        pending_setup = d.get('initial_breakout')
        if pending_setup and now > pending_setup['breakout_at']:
            pending_setup['peak_close'] = max(pending_setup.get('peak_close') or o.price, o.price)
        if active and (held or a.status == Status.ENTRY_PENDING) and not active.get('stopout_reference'):
            active['peak_close'] = max(active.get('peak_close') or o.price, o.price)
    green_close = bool(active and held and green_close_cap and o.source_timeframe == '1s'
                       and 'bar_close' in o.evaluation_events and o.bar_open is not None
                       and o.price > o.bar_open)
    if green_close:
        confirmations = active.setdefault('green_resistance_closes', {})
        catalog = {r['unified_level_id']: r for r in d.get('breakout_anchors', {}).values()}
        catalog.update({r['unified_level_id']: r for r in rows.values() if eligible(r)})
        for key, level in catalog.items():
            if o.price > level['upper'] + 1e-9:
                confirmations[key] = dict(closed_at=now, close=o.price, open=o.bar_open,
                                          upper=level['upper'])
    evidence = dict(contract=p['early_squeeze_breakout_contract'], filtered_v7=filtered,
        activation={k:d.get(k) for k in ('activated_at', 'activation_event_id')})
    evidence['price_breakout'] = evidence_price
    if strict:
        evidence.update(stop_trigger_source='eligible_trade', prior_breakout_highs=prior_breakout_highs)

    def emit(action, reason, status=None, **kw):
        metadata = dict(evidence, active_stop=state.get('active_stop'),
            profit_targets=list(state.get('structural_profit_targets') or []),
            position_resistance_breaks=len(active.get('broken_levels', [])),
            fixed_trail_distance=active.get('trail_distance'), trailing_bid_high=None if strict else active.get('peak_price'),
            trailing_trade_high=active.get('peak_price') if strict else None,
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
        if strict and active:
            key = active['anchor']['unified_level_id']
            if (not episode or key in d.get('breakout_highs', {})) and key not in d.setdefault('entered_levels', []):
                d['entered_levels'].append(key)
        if not active:
            return emit('hold', 'position_entry_state_unavailable', Status.MANAGING)
        if not active.get('trail_distance') and o.average_price > stop > 0:
            distance = o.average_price-stop
            if green_close_cap:
                distance = max(distance, .10)
            active.update(trail_distance=distance, peak_price=o.average_price)
        if (price_event and o.price <= stop) if strict else (quote and o.bid <= stop):
            return emit('exit', 'protective_stop', Status.EXIT_PENDING, quantity=o.position_quantity)
        if state.get('manual_exit_requested'):
            return emit('exit', 'manual_exit', Status.EXIT_PENDING, quantity=o.position_quantity)
        results = []
        trail_update = price_event or green_close if strict else bool(quote)
        if trail_update and active.get('trail_distance'):
            trail_price = (o.price if price_event else active.get('peak_price', o.price)) if strict else o.bid
            active['peak_price'] = max(active.get('peak_price', trail_price), trail_price)
            proposal = round(floor((active['peak_price']-active['trail_distance'])/tick+1e-9)*tick, 10)
            desired = active.get('structural_stop', stop)
            if desired < (o.price if strict else o.bid):
                proposal = max(proposal, desired)
            ceiling = (broken_resistance_ceiling(active, d.get('breakout_anchors', {}), rows, trail_price,
                           set(active.get('green_resistance_closes', {})))
                       if green_close_cap else
                       broken_resistance_ceiling(active, d.get('breakout_anchors', {}), rows, trail_price)
                       if broken_resistance_cap else
                       unbroken_resistance_ceiling(rows, trail_price, stop) if resistance_ceiling else None)
            if ceiling:
                proposal = min(proposal, ceiling['lower'])
            if stop < proposal < (o.price if strict else o.bid):
                previous_stop = stop
                state['active_stop'] = stop = proposal
                results.append(emit('replace_protective_stop', 'fixed_distance_price_trail', Status.MANAGING,
                    quantity=o.position_quantity, invalidation_price=stop, metadata=dict(previous_stop=previous_stop,
                        resistance_ceiling=deepcopy(ceiling))))
        # Re-rank on eligible trade prices using the causal V7 snapshot.
        if price_event and quote and structure_fresh:
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
        if price_event and structure_fresh and quote:
            pending = active.setdefault('pending_adds', {})
            seen = d.setdefault('attempted_add_levels', []) if strict else active.setdefault('added_levels', [])
            for key, level in crossed:
                if is_resistance(level) and key not in seen and (not strict or is_resistance(rows.get(key, {}))):
                    pending.setdefault(key, deepcopy(level))
            for key in list(pending):
                if (strict and not is_resistance(rows.get(key, {}))) or not boundary(pending[key], rows) or o.price < boundary(pending[key], rows)['price']:
                    pending.pop(key)
            if pending and a.permissions.add and active.get('slice_notional', 0) > 0 and stop < o.bid <= o.ask < target:
                keys = sorted(pending)
                if strict and not episode:
                    anchor_stop = max(below(rows[k]['lower'], tick) for k in keys)
                    proposal = max(stop, min(anchor_stop, below(min(o.price, o.ask), tick)))
                    if proposal > stop:
                        state['active_stop'] = stop = proposal
                        active['structural_stop'] = proposal
                        results.append(emit('replace_protective_stop', 'new_resistance_protection', Status.MANAGING,
                            quantity=o.position_quantity, invalidation_price=stop))
                results.append(emit('add_long', 'price_gap_resistance_break_addition', Status.MANAGING,
                    invalidation_price=stop, profit_target_price=target,
                    capital_request=CapitalRequest(mode='fixed_notional', value=active['slice_notional']*len(keys)),
                    metadata=dict(squeeze_add_levels=keys, slice_notional=active['slice_notional'])))
                seen.extend(keys)
                pending.clear()
        if results:
            return replace(results[-1], state=state, evaluation=replace(results[-1].evaluation,
                intents=tuple(i for r in results for i in r.evaluation.intents)))
        return emit('hold', 'fixed_distance_trail_and_resistance_target', Status.MANAGING)
    return evaluate_entry(host, a, o, p, state, d, rows, ctx, row, fresh,
                          structure_fresh, quote, evidence, emit, price_event, previous, prior_hod)


def evaluate_entry(host, a, o, p, state, d, rows, ctx, row, fresh,
                   structure_fresh, quote, evidence, emit, price_event, previous, prior_hod):
    from .strategy_engine import AssignmentStatus as Status
    episode = p['early_squeeze_breakout_contract'] in EPISODE_CONTRACTS
    strict = episode or p['early_squeeze_breakout_contract'] == STRICT_CONTRACT
    now, tick = o.observed_at.timestamp(), p['execution']['tick_size']
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
    if not price_event and not fresh:
        return emit('wait', 'waiting_for_price_update')
    if not structure_fresh:
        return emit('wait', 'filtered_v7_unavailable')
    anchor = entry_level(rows, prior_hod)
    setup = d.get('initial_breakout')
    limit = boundary(anchor, rows) if anchor else None
    if setup and (not anchor or setup['anchor']['unified_level_id'] != anchor['unified_level_id']
                  or not limit or o.price < limit['price']):
        d.pop('initial_breakout', None)
        setup = None
    if price_event and anchor and limit:
        new_high = evidence.get('prior_breakout_highs', {}).get(anchor['unified_level_id'])
        later_high = strict and anchor['unified_level_id'] in d.get('entered_levels', []) and new_high is not None and o.price > new_high
        if not setup and previous is not None and (previous < limit['price'] <= o.price or later_high and o.price >= limit['price']):
            setup = dict(anchor=deepcopy(anchor), breakout_at=now, peak_close=None)
            d['initial_breakout'] = setup
        elif setup:
            setup['anchor'] = deepcopy(anchor)
    evidence['price_breakout'].update(selected_level=deepcopy(anchor), boundary=limit)
    confirmed = bool(price_event and setup and limit and o.price >= limit['price'])
    if strict and confirmed and anchor['unified_level_id'] in d.get('entered_levels', []):
        previous_high = evidence['prior_breakout_highs'].get(anchor['unified_level_id'])
        confirmed = previous_high is None or o.price > previous_high
        if not confirmed:
            return emit('wait', 'resistance_breakout_new_high_required')
    recovery = None if strict else d.get('recovery')
    if price_event and recovery:
        if o.price <= recovery['high']:
            recovery.pop('crossed_at', None)
        elif previous is not None and previous <= recovery['high'] < o.price and now > recovery['stopped_at']:
            recovery['crossed_at'] = now
    recovering = bool(price_event and recovery and recovery.get('crossed_at')
                      and now > recovery['stopped_at'] and o.price > recovery['high'])
    if not confirmed and not recovering:
        return emit('wait', 'waiting_for_price_gap_breakout' if strict else 'waiting_for_price_gap_breakout_or_recovery')
    quality_row = dict(effective_at=now, candle=dict(volume=o.source_values.get('market.trade_size', {}).get('value') if price_event else o.bar_volume))
    ready, quality = H.tradability(o, dict(p, structural_recovery=dict(H.QUALITY_DEFAULTS, **p['structural_recovery'])),
                                  quality_row, state, producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if not ready:
        return emit('wait', 'liquidity_or_spread_gate')
    vwap_source = 'indicator.vwap.execution_value@100ms'
    source = o.source_values.get(vwap_source, {})
    at, vwap = E.stamp(source.get('observed_at')), source.get('value')
    evidence['vwap_gate'] = dict(source_id=vwap_source, value=vwap,
                               observed_at=source.get('observed_at'), close=o.price)
    prefix = (o.structural_detector_state or {}).get('price_vwap_evidence', {})
    latest_completed = (prefix.get('authority') == 'latest-completed-qmd-100ms'
        and E.stamp(prefix.get('as_of')) == o.observed_at
        and prefix.get('source_observed_at') == source.get('observed_at'))
    evidence['vwap_gate']['prefix_evidence'] = prefix
    if (not at or at > o.observed_at or at.astimezone(H.NY).date() != o.observed_at.astimezone(H.NY).date()
            or not latest_completed or type(vwap) not in (int,float) or not isfinite(vwap) or not 0 < vwap < o.price):
        return emit('wait', 'fresh_price_above_vwap_required')
    if confirmed:
        recovery = None
        desired = below(anchor['lower'], tick)
        stop_source = 'broken_resistance_lower'
    else:
        anchor = d.get('latest_broken_resistance', recovery['anchor'])
        desired = below(anchor['lower'], tick)
        stop_source = 'latest_broken_resistance_lower'
    # Trigger/trailing authority is the trade, but a new buy's fixed stop
    # must also be below its executable entry reference. Trades can be above
    # the current ask; offset immediately rather than emitting invalid risk.
    stop = min(desired, below(min(o.price, o.ask) if strict else o.bid, tick))
    overhead = sorted((r for r in rows.values() if is_resistance(r)
        and r['unified_level_id'] != anchor['unified_level_id'] and E.target_price(r,tick,E.CONTRACT) > o.ask),
        key=lambda r:(r['lower']+r['upper'],r['unified_level_id']))
    if len(overhead) < 3:
        return emit('wait', 'overhead_resistance_target_unavailable')
    target = E.target_price(overhead[2], tick, E.CONTRACT)
    if not quote or not (0 < stop < (o.price if strict else o.bid) and 0 < o.bid <= o.ask < target):
        return emit('wait', 'unrepresentable_stop_or_target')
    d['latest_broken_resistance'] = deepcopy(anchor)
    if strict:
        key = anchor['unified_level_id']
        d.setdefault('breakout_highs', {}).setdefault(key, o.price)
        if key not in d.setdefault('attempted_add_levels', []):
            d['attempted_add_levels'].append(key)
        d.pop('recovery', None)
    for key in ('liquidation_origin_fill_role', 'liquidation_origin_reentry_after_fill',
                'profit_target_liquidation_required', 'target_replenishment_pending', 'last_exit_reason'):
        state.pop(key, None)
    state.update(squeeze_entry=dict(anchor=deepcopy(anchor), structural_stop=stop if episode else desired,
        breakout_at=recovery['breakout_at'] if recovery else setup['breakout_at'],
        peak_close=setup.get('peak_close') if not recovery else None,
        entry_price=o.ask, requested_at=now, single_use_adds=strict, added_levels=[anchor['unified_level_id']] if strict else [],
        pending_adds={}, broken_levels=[], late=False, target_moves=0),
        initial_stop=stop, active_stop=stop, structural_profit_targets=[target],
        entry_reference_price=o.ask, entry_at=o.observed_at.isoformat(),
        entries=state.get('entries',0)+1, entry_acquisition_exit_latched=False)
    state['squeeze_entry']['recovery_trigger_anchor'] = deepcopy(recovery['anchor'] if recovery else anchor)
    return emit('enter_long', 'stopout_close_high_reentry' if recovery else 'squeeze_r1_price_gap_breakout', Status.ENTRY_PENDING,
        invalidation_price=stop, profit_target_price=target,
        capital_request=CapitalRequest(mode='mandate_fraction', value=1/3),
        metadata=dict(unreserved_cash_slice=True, entry_selection=deepcopy(anchor), stop_source=stop_source,
            structural_stop=desired, breakout_boundary=limit if not recovery else dict(price=recovery['high'], kind='frozen_closing_high'),
            frozen_reentry_high=recovery['high'] if recovery else None,
            profit_target_selection=dict(level=deepcopy(overhead[2]),price=target,ordinal=3),
            unified_structural_trigger={'current_snapshot':{'levels':[dict(anchor, entry_boundary=limit['price'] if not recovery else recovery['high'])],
                'session_high':prior_hod,'selected_at':o.observed_at.isoformat(),'frozen_at_entry':True}}))

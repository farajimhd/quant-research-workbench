"""Causal mechanics for the Early Squeeze momentum candidate.

Prices, geometry and clocks in returned evidence are frozen at the decision.
This module never consumes chart annotations or retrospective extrema.
"""
from copy import deepcopy
from dataclasses import replace
from math import floor

from . import early_squeeze_consistent as C, early_squeeze_price as P
from . import vwap_resistance_ladder as V, historical_hod as H, early_squeeze_breakout as E
from .early_squeeze_fast import below, levels
from .signals import CapitalRequest

CONTRACT = 'early-squeeze-momentum-v24'


def fresh_structure(rows, evidence, now, maximum_age=1.):
    cutoff, last = evidence.get('as_of'), evidence.get('max_input_timestamp')
    return bool(rows and C.finite(cutoff, last) and 0 < last <= cutoff <= now
        and now-cutoff <= maximum_age
        and all(r.get('input_policy') == V.POLICY and r.get('seed_input_policy') == V.POLICY
                for r in rows.values()))


def freeze_gap(rows, price, now):
    """Consecutive resistance-midpoint gaps, measured only at activation.

    +300% is four times the activation price. Entry-to-first-level distance
    is not an inter-resistance gap. Fewer than two levels cannot define one.
    """
    if not C.finite(price, now) or price <= 0:
        raise ValueError('Activation price and clock must be finite and positive')
    selected = sorted((deepcopy(r) for r in rows.values()
        if P.eligible(r) and price < P.midpoint(r) <= 4*price),
        key=lambda r: (P.midpoint(r), r['unified_level_id']))
    gaps = [P.midpoint(b)-P.midpoint(a) for a, b in zip(selected, selected[1:])]
    return dict(frozen_at=now, reference_price=price, ceiling=4*price,
        levels=selected, gaps=gaps, average=sum(gaps)/len(gaps) if gaps else None)


def confirmed_pivots(row, now, side):
    """Read event-time confirmed local pivots from the shared detector only."""
    from ..market_engine.structural_detector import VERSION
    at = row.get('effective_at')
    if row.get('contract') != VERSION or not C.finite(at) or not 0 < at <= now:
        return []
    found = {}
    for pivot in row.get('local_swings', []) + row.get('confirmed_swings', []):
        values = [pivot.get(k) for k in ('price', 'pivot_at', 'confirmed_at')]
        if (pivot.get('side') not in ((1, 'support') if side == 'low' else (-1, 'resistance'))
                or pivot.get('state', 'active') != 'active' or not C.finite(*values)):
            continue
        price, occurred, confirmed = values
        if not 0 < price or not 0 < occurred < confirmed <= at:
            continue
        found[(occurred, confirmed, price)] = deepcopy(pivot)
    return sorted(found.values(), key=lambda r: (r['pivot_at'], r['confirmed_at']))


def supported_swing(row, rows, now):
    """Age is measured from the low itself, never from delayed confirmation."""
    supports = [r for r in rows.values() if r.get('role') == 'support'
        or not r.get('role') and r.get('side') in (1, 'support')]
    for pivot in reversed(confirmed_pivots(row, now, 'low')):
        if not 0 <= now-pivot['pivot_at'] <= 10:
            continue
        bands = [r for r in supports if r['lower'] <= pivot['price'] <= r['upper']]
        if bands:
            band = min(bands, key=lambda r: (r['upper']-r['lower'], r['unified_level_id']))
            return dict(pivot=pivot, level=deepcopy(band))
    return None


def initial_stop(row, rows, now, price, vwap, tick, *, distance_reference, fallback_percent=1):
    if fallback_percent not in (1, 5):
        raise ValueError('Momentum fallback stop must be 1% or 5%')
    if distance_reference not in ('vwap', 'entry'):
        raise ValueError('Support distance reference must be explicitly selected')
    swing = supported_swing(row, rows, now)
    if swing:
        return dict(price=below(swing['pivot']['price'], tick),
            reason='supported_swing_low_stop', **swing)
    supports = sorted((r for r in rows.values() if (r.get('role') == 'support'
        or not r.get('role') and r.get('side') in (1, 'support')) and r['upper'] < vwap),
        key=lambda r: (r['upper'], r['unified_level_id']), reverse=True)
    reference = vwap if distance_reference == 'vwap' else price
    if supports and 0 <= reference-supports[0]['lower'] <= .01*price:
        return dict(price=below(supports[0]['lower'], tick), reason='below_vwap_support_stop',
            level=deepcopy(supports[0]), reference=reference, maximum_distance=.01*price)
    return dict(price=round(floor((1-fallback_percent/100)*price/tick+1e-9)*tick, 10),
        reason='five_percent_entry_stop' if fallback_percent == 5 else 'one_percent_entry_stop',
        fallback_percent=fallback_percent, entry_reference=price)


def observe_resistances(state, observation, rows, fresh, trade):
    """Require the candle itself to open below/equal and close above midpoint."""
    events, closed, opening = C.observe(state, observation, rows, fresh, trade, reclaim=True)
    book = state.get('resistance_1s', {})
    if closed:
        book['pending'] = [p for p in book.get('pending', [])
            if p['candle_open'] <= p['threshold'] < p['close']]
    return events, closed, opening


def observe_bos(state, row, now, price, trade, fresh):
    """Break a previously confirmed swing high; hold the gate awaiting filters.

    A newly delivered pivot already behind price cannot manufacture a crossing.
    """
    gate = state.setdefault('bos', {})
    pivots = confirmed_pivots(row, now, 'high') if fresh else []
    latest = pivots[-1] if pivots else None
    prior = gate.get('reference')
    if latest and (prior is None or latest['pivot_at'] > prior['pivot_at']):
        gate['reference'] = prior = deepcopy(latest)
    previous = gate.get('last_trade')
    if trade:
        if (fresh and prior and previous is not None
                and previous <= prior['price'] < price and prior['confirmed_at'] < now):
            gate.update(open=True, broken_at=now, broken_pivot=deepcopy(prior), break_price=price)
        gate.update(last_trade=price, last_trade_at=now)
    return deepcopy(gate)


def record_breaks(active, events):
    """Distinct position-owned levels; disjoint fast triples upgrade twice."""
    seen = active.setdefault('broken_levels', [])
    pending = active.setdefault('fast_breaks', [])
    upgrades = []
    for event in events:
        key = event['level']['unified_level_id']
        if key in seen:
            continue
        seen.append(key)
        at = event['opened_at']
        pending[:] = [p for p in pending if 0 <= at-p['opened_at'] < 3]
        pending.append(deepcopy(event))
        multiplier = active.get('target_multiplier', 5)
        if len(pending) >= 3 and multiplier < 10:
            active['target_multiplier'] = 8 if multiplier == 5 else 10
            upgrades.append(dict(previous_multiplier=multiplier,
                multiplier=active['target_multiplier'], confirmations=deepcopy(pending)))
            pending.clear()
    return upgrades


def next_stop(active, rows, tick):
    """Every three distinct acceptances advance one resistance above the stop."""
    earned = len(active.get('broken_levels', []))//3
    moved = active.get('stop_steps', 0)
    stop = active['stop']
    selected = []
    for _ in range(max(0, earned-moved)):
        higher = sorted((r for r in rows.values() if P.eligible(r)
            and r['lower'] > active.get('stop_anchor_lower', stop)
            and below(r['lower'], tick) > stop), key=lambda r: (r['lower'], r['unified_level_id']))
        if not higher:
            break
        level = higher[0]
        stop = below(level['lower'], tick)
        selected.append(deepcopy(level))
        # Use a local bound so one call can advance across two earned steps.
        rows = {k: r for k, r in rows.items() if r['lower'] > level['lower']}
    return dict(price=stop, steps=moved+len(selected), levels=selected)


def target_price(fill_price, average_gap, multiplier, tick):
    if not C.finite(fill_price, average_gap, tick) or min(fill_price, average_gap, tick) <= 0:
        raise ValueError('Target needs a positive actual fill, frozen gap and tick')
    if multiplier not in (5, 8, 10):
        raise ValueError('Momentum target multiplier must be 5, 8 or 10')
    return round(floor((fill_price+multiplier*average_gap)/tick+.5+1e-9)*tick, 10)


def observe_session(saved, at, price):
    """Called on every eligible canonical trade, including before activation."""
    local = at.astimezone(H.NY)
    if not 4 <= local.hour < 20 or not C.finite(price) or price <= 0:
        return saved
    session = local.date().isoformat()
    d = dict(saved) if saved.get('session') == session else dict(session=session,
        open=price, opened_at=at.timestamp(), high=price)
    if at.timestamp() < d.get('observed_at', 0):
        return d
    d.update(high=max(d['high'], price), observed_at=at.timestamp())
    d['late'] = bool(d.get('late') or d['high'] >= 1.2*d['open'])
    return d


def purchase_update(state, intent_id, *, filled=False, terminal=False, stop=None):
    """Entitlements are consumed by first fill, not submission or retry."""
    d = state.setdefault('squeeze_breakout', {})
    request = d.get('momentum_requests', {}).get(intent_id)
    if request:
        request['filled'] = request.get('filled', False) or filled
        request['terminal'] = request.get('terminal', False) or terminal
    if stop and state.get('squeeze_entry') and not state['squeeze_entry'].get('stop_steps'):
        state['active_stop'] = state['squeeze_entry']['stop'] = stop


def evaluate(host, a, o, p, old_state):
    from .strategy_engine import AssignmentStatus as Status
    state = deepcopy(old_state)
    d = state.setdefault('squeeze_breakout', {})
    now, tick = o.observed_at.timestamp(), p['execution']['tick_size']
    local = o.observed_at.astimezone(H.NY)
    session = local.date().isoformat()
    if d.get('session') != session:
        d.clear()
        d['session'] = session
    rows = levels(o)
    market = o.structural_detector_state or {}
    fresh = fresh_structure(rows, market.get('fast_structure_evidence', {}), now)
    sample = o.source_values.get('market.last_price', {})
    trade = bool('market_data_update' in o.evaluation_events and 'market.last_price' in o.changed_source_ids
        and E.stamp(sample.get('observed_at')) == o.observed_at and sample.get('value') == o.price
        and not any(s.startswith('quote:') for s in o.source_signal_ids))
    events, closed, _ = observe_resistances(d, o, rows, fresh, trade)
    preview = P.forming_macd_1s(o, d, trade, sparse=True)
    episode = d.setdefault('macd_1s', {})
    if closed:
        bullish = C.finite(o.macd_line, o.macd_signal) and o.macd_line > o.macd_signal
        if bullish and not episode.get('open'):
            episode['episode_id'] = now
        episode.update(open=bullish, observed_at=now, line=o.macd_line, signal=o.macd_signal)
    if o.source_timeframe == '100ms' and 'bar_close' in o.evaluation_events:
        gate = d.setdefault('macd_100ms', {})
        if now > gate.get('observed_at', 0):
            gate.update(observed_at=now, open=C.finite(o.macd_line, o.macd_signal)
                and o.macd_line > o.macd_signal, line=o.macd_line, signal=o.macd_signal)
    fast = d.get('macd_100ms', {})
    bos = observe_bos(d, market.get('row', {}), now, o.price, trade, fresh)
    context = market.get('momentum_session', {})
    session_valid = (context.get('complete') is True and context.get('session') == session and C.finite(context.get('open'), context.get('observed_at'))
        and context['open'] > 0 and context['observed_at'] <= now)
    if session_valid:
        d['late_mode'] = bool(d.get('late_mode') or context.get('late'))
    # Select below the prior HOD; a trade cannot create its own HOD reference.
    previous_high = d.get('prior_hod')
    if trade and session_valid:
        previous_high = max(previous_high or 0, context.get('prior_high') or context['open'])
        catalog = d.get('resistance_1s', {}).get('catalog', {})
        candidate = max((r for k, r in rows.items() if (P.eligible(r) or k in catalog)
            and P.midpoint(r) < previous_high), key=P.midpoint, default=None) if fresh else None
        previous_price = d.get('hod_last_trade')
        prior_level = d.get('hod_reference')
        accepted = d.get('hod_gate')
        if accepted:
            current = rows.get(accepted['level']['unified_level_id'])
            if (not current or (current['lower'], current['upper']) !=
                    (accepted['level']['lower'], accepted['level']['upper']) or o.price <= accepted['threshold']):
                d.pop('hod_gate', None)
        if (candidate and prior_level and all(candidate[k] == prior_level[k] for k in ('unified_level_id', 'lower', 'upper')) and previous_price is not None
                and previous_price <= P.midpoint(candidate) < o.price):
            d['hod_gate'] = dict(level=deepcopy(candidate), threshold=P.midpoint(candidate), at=now, hod=previous_high)
        d.update(prior_hod=max(previous_high, o.price), hod_last_trade=o.price, hod_reference=deepcopy(candidate))
    activation = o.source_values.get(E.SIGNAL, {})
    activated_at = E.stamp(activation.get('observed_at'))
    if ('activated_at' not in d and activation.get('value') is True and activated_at
            and activated_at <= o.observed_at and activated_at.astimezone(H.NY).date() == local.date()):
        d['activated_at'] = activated_at.timestamp()
        # Never reprice a missing activation snapshot using later geometry.
        saved = market.get('momentum_activation', {})
        if saved.get('activated_at') == activated_at.timestamp():
            d['frozen_gap'] = deepcopy(saved.get('frozen_gap', {}))
        elif fresh and activated_at == o.observed_at:
            d['frozen_gap'] = freeze_gap(rows, o.price, now)
    active = state.get('squeeze_entry') or {}
    held = o.position_quantity > 0
    quote = V.fresh_quote(o)
    evidence = dict(contract=CONTRACT, frozen_gap=deepcopy(d.get('frozen_gap')), bos=bos,
        late_mode=bool(d.get('late_mode')), hod_gate=deepcopy(d.get('hod_gate')),
        session_context=deepcopy(context), resistance_break_events=deepcopy(events),
        macd_1s_episode=deepcopy(episode), macd_100ms_gate=deepcopy(fast), forming_macd_1s=preview,
        bid=o.bid, ask=o.ask, reference_price=o.price)
    management_results = []

    def emit(action, reason, status=None, **kw):
        metadata = dict(evidence, **kw.pop('metadata', {}))
        if action == 'exit':
            reason = state.get('last_exit_reason') if state.get('entry_acquisition_exit_latched') else reason
            reason = reason or 'position_liquidation'
            state.update(entry_acquisition_exit_latched=True, last_exit_reason=reason)
            metadata.update(exit_reason=reason, position_fraction=1., cancel_entry_acquisition=True, reentry_after_fill=True)
        result = host._result(a, o, action, reason, float(action in ('enter_long', 'add_long')), 1.,
            state, status or a.status, metadata=metadata,
            order_intent=dict(execution_policy='adaptive_urgent', protection_profile='structural-single-target'), **kw)
        if action in ('enter_long', 'add_long'):
            result = replace(result, evaluation=replace(result.evaluation, intents=tuple(
                replace(i, reference_price=o.ask, metadata={**i.metadata, 'mandatory_broker_target':True,
                    'wait_for_capital':False, 'stop_trigger_source':'eligible_trade'}) for i in result.evaluation.intents)))
            for intent in result.evaluation.intents:
                d.setdefault('momentum_requests', {})[intent.intent_id] = dict(
                    lifecycle=active['requested_at'], keys=metadata.get('squeeze_add_levels', []),
                    episode_id=episode.get('episode_id'), filled=False, terminal=False)
                if action == 'add_long':
                    d.setdefault('midpoint_add_requests', {})[intent.intent_id] = deepcopy(d['momentum_requests'][intent.intent_id])
        if management_results and action in ('hold', 'wait', 'add_long'):
            result = replace(result, state=state, evaluation=replace(result.evaluation,
                intents=tuple(i for r in management_results for i in r.evaluation.intents) + result.evaluation.intents))
        return result

    if held:
        if state.get('entry_acquisition_exit_latched') or a.status == Status.EXIT_PENDING or o.pending_exit_quantity:
            remaining = max(0., o.position_quantity-o.pending_exit_quantity)
            return emit('exit', state.get('last_exit_reason') or 'position_liquidation', Status.EXIT_PENDING,
                quantity=remaining) if remaining > 1e-9 else emit('hold', 'exit_fill_pending', Status.EXIT_PENDING)
        if local.hour >= 20 or local.hour < 4:
            return emit('exit', 'session_flatten', Status.EXIT_PENDING, quantity=o.position_quantity)
        if state.get('manual_exit_requested'):
            return emit('exit', 'manual_exit', Status.EXIT_PENDING, quantity=o.position_quantity)
        stop = state.get('active_stop') or active.get('stop', 0)
        if trade and stop and o.price <= stop:
            return emit('exit', active.get('stop_reason') or 'protective_stop', Status.EXIT_PENDING,
                quantity=o.position_quantity, metadata=dict(stop_price=stop, trigger_price=o.price,
                    protective_stop_selection=deepcopy(active.get('stop_selection'))))
        if not active:
            return emit('hold', 'position_entry_state_unavailable', Status.MANAGING)
        results = []
        active['stop'] = stop
        upgrades = record_breaks(active, events)
        if fresh and quote:
            # Keep accepted resistance identity even after it becomes support.
            catalog = {k:dict(r, role='resistance') for k, r in rows.items()
                if k in d.get('resistance_1s', {}).get('catalog', {}) or P.eligible(r)}
            step = next_stop(active, catalog, tick)
            if stop < step['price'] < min(o.price, o.bid):
                old_selection = deepcopy(active.get('stop_selection'))
                old_steps = active.get('stop_steps', 0)
                old_reason, old_anchor = active['stop_reason'], active.get('stop_anchor_lower', stop)
                active.update(stop=step['price'], stop_steps=step['steps'],
                    stop_anchor_lower=step['levels'][-1]['lower'], stop_reason='three_resistance_step_stop',
                    stop_selection=deepcopy(step))
                state['active_stop'] = step['price']
                results.append(emit('replace_protective_stop', 'three_resistance_step_stop', Status.MANAGING,
                    quantity=o.position_quantity, invalidation_price=step['price'], metadata=dict(
                        previous_stop=stop, momentum_previous_stop_steps=old_steps,
                        momentum_previous_stop_selection=old_selection, momentum_previous_stop_reason=old_reason,
                        momentum_previous_stop_anchor=old_anchor, stop_exit_reason='three_resistance_step_stop')))
        desired_multiplier = active.get('target_multiplier', 5)
        if quote and desired_multiplier > active.get('submitted_multiplier', 5):
            indicative = target_price(o.average_price or active['entry_price'], active['average_gap'], desired_multiplier, tick)
            state['structural_profit_targets'] = [indicative]
            results.append(emit('replace_profit_target', 'momentum_target_upgrade', Status.MANAGING,
                quantity=o.position_quantity, profit_target_price=indicative, metadata=dict(
                    momentum_target_multiplier=desired_multiplier, momentum_upgrades=upgrades,
                    momentum_previous_multiplier=active.get('submitted_multiplier', 5))))
            active['submitted_multiplier'] = desired_multiplier
        management_results.extend(results)

    if not 4 <= local.hour < 20 or not o.market_open:
        # Cancel any acquisition that has not filled before the session cutoff.
        if a.status == Status.ENTRY_PENDING:
            return emit('exit', 'session_flatten', Status.EXIT_PENDING, quantity=max(0., o.position_quantity))
        return emit('wait', 'outside_trading_session')
    if 'activated_at' not in d:
        return emit('wait', 'waiting_for_early_squeeze')
    if not held and now < d.get('target_reentry_not_before', 0):
        return emit('wait', 'target_hit_same_1s_candle', metadata=dict(
            reentry_not_before=d['target_reentry_not_before']))
    if not trade or not fresh:
        return emit('hold' if held else 'wait', 'fresh_causal_trade_and_v7_required')
    if not session_valid:
        return emit('wait', 'session_open_context_unavailable')
    if d.get('late_mode') and not d.get('hod_gate'):
        return emit('wait', 'late_mode_below_hod_resistance_required')
    if not held and (a.status == Status.ENTRY_PENDING or state.get('pending_capital_request')):
        return emit('wait', 'entry_fill_pending')
    if not held and (a.status in (Status.DISABLED, Status.PAUSED, Status.COMPLETED, Status.ERROR)
            or not a.permissions.observe or not (a.permissions.reenter if state.get('entries', 0) else a.permissions.enter)):
        return emit('wait', 'entry_permission_closed')
    if not held and not bos.get('open'):
        return emit('wait', 'waiting_for_confirmed_swing_high_bos')
    if not (episode.get('open') and 0 <= now-episode.get('observed_at', 0) <= 1.
            and preview and C.finite(preview.get('line'), preview.get('signal')) and preview['line'] > preview['signal']):
        return emit('wait', 'fresh_bullish_completed_and_forming_1s_macd_required')
    if not fast.get('open') or not 0 <= now-fast.get('observed_at', 0) <= .100001:
        return emit('wait', 'fresh_bullish_completed_100ms_macd_required')
    source = o.source_values.get('indicator.vwap.execution_value@100ms', {})
    at, vwap = E.stamp(source.get('observed_at')), source.get('value')
    prefix = market.get('price_vwap_evidence', {})
    if (not at or not 0 <= now-at.timestamp() <= .100001 or not C.finite(vwap) or not 0 < vwap < o.price
            or prefix.get('authority') != 'latest-completed-qmd-100ms'
            or E.stamp(prefix.get('as_of')) != o.observed_at or prefix.get('source_observed_at') != source.get('observed_at')):
        return emit('wait', 'fresh_price_above_vwap_required')
    if not quote:
        return emit('wait', 'fresh_quote_required')
    gap = (d.get('frozen_gap') or {}).get('average')
    if not C.finite(gap) or gap <= 0:
        return emit('wait', 'activation_resistance_gap_unavailable')
    addition = None
    if held:
        requests = [r for r in d.get('momentum_requests', {}).values() if r['lifecycle'] == active['requested_at']]
        if not a.permissions.add or sum(r['filled'] or not r['terminal'] for r in requests) >= 3:
            return emit('hold', 'three_purchase_limit_or_add_permission', Status.MANAGING)
        addition = next((ev for ev in events if P.midpoint_add_available(d,
            ev['level']['unified_level_id'], episode['episode_id'])), None)
        if not addition:
            return emit('hold', 'waiting_for_fresh_resistance_addition', Status.MANAGING)
        stop = state['active_stop']
    else:
        selection = initial_stop(market.get('row', {}), rows, now, o.ask, vwap, tick, distance_reference='entry',
            fallback_percent=p.get('momentum_fallback_stop_percent', 1))
        stop = selection['price']
        active = dict(requested_at=now, entry_price=o.ask, average_gap=gap, stop=stop,
            stop_reason=selection['reason'], stop_selection=selection, stop_steps=0,
            stop_anchor_lower=(selection.get('level') or {}).get('lower', stop),
            target_multiplier=5, submitted_multiplier=5, broken_levels=[])
    multiplier = active['target_multiplier']
    target = target_price(o.ask, gap, multiplier, tick)
    if not 0 < stop < min(o.price, o.bid) <= o.ask < target:
        return emit('wait', 'unrepresentable_stop_or_target')
    if not held:
        for key in ('last_exit_reason', 'liquidation_origin_fill_role', 'liquidation_origin_reentry_after_fill',
                'profit_target_liquidation_required', 'target_replenishment_pending'):
            state.pop(key, None)
        state.update(squeeze_entry=active, initial_stop=stop, active_stop=stop,
            structural_profit_targets=[target], entry_reference_price=o.ask, entry_at=o.observed_at.isoformat(),
            entries=state.get('entries', 0)+1, entry_acquisition_exit_latched=False)
    return emit('add_long' if held else 'enter_long', 'accepted_resistance_addition' if held else 'bos_vwap_momentum_entry',
        Status.MANAGING if held else Status.ENTRY_PENDING, invalidation_price=stop, profit_target_price=target,
        capital_request=CapitalRequest(mode='mandate_fraction', value=1/3), metadata=dict(
            unreserved_cash_slice=True, cash_fraction_of_unreserved=True,
            squeeze_add_levels=[addition['level']['unified_level_id']] if addition else [],
            resistance_confirmation=deepcopy(addition), stop_exit_reason=active['stop_reason'],
            momentum_target=dict(average_gap=gap, multiplier=multiplier, tick_size=tick),
            momentum_initial_stop=deepcopy(active['stop_selection']) if not held else None,
            protective_stop_selection=deepcopy(active['stop_selection']), vwap_gate=dict(value=vwap, observed_at=source['observed_at'])))

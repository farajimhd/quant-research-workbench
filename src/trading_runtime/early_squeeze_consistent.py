"""V22: one causal 1s resistance acceptance event for every consumer.

No retrospective candle open or chart projection can execute an order. The
first eligible trade after a completed candle is the next candle's open.
"""
from copy import deepcopy
from dataclasses import replace
from math import floor, isfinite

from . import early_squeeze_price as P, early_squeeze_breakout as E
from . import historical_hod as H, vwap_resistance_ladder as V
from .early_squeeze_fast import levels, below
from .signals import CapitalRequest

CONTRACT = 'early-squeeze-consistent-1s-resistance-v22'
STRUCTURAL_CONTRACT = 'early-squeeze-structural-1s-resistance-v23'


def finite(*values):
    return all(type(v) in (int, float) and isfinite(v) for v in values)


def observe(d, o, rows, structure_fresh, trade, *, reclaim=False):
    """Return each shared break once, only at the actual next 1s open.

    Levels must be known before the confirming candle. A moved level cannot
    manufacture a crossing. Resistance identity, not its transient role,
    owns an already accepted break until a completed close fails its midpoint.
    """
    now = o.observed_at.timestamp()
    book = d.setdefault('resistance_1s', {})
    closed = o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
    new_close = closed and now > book.get('at', 0)
    if new_close:
        previous = book.get('close')
        prior_rows = book.get('levels', {})
        catalog = book.setdefault('catalog', {})
        if structure_fresh:
            for key, level in rows.items():
                if P.eligible(level) or key in catalog:
                    # The shared acceptance state, not a detector role flip,
                    # decides whether an encountered resistance was broken.
                    catalog[key] = dict(level, role='resistance')
        accepted = book.setdefault('accepted', {})
        pending = []
        for key, proof in list(accepted.items()):
            if o.price <= P.midpoint(proof['level']):
                accepted.pop(key)
        if structure_fresh and finite(previous, o.price, o.bar_open) and o.price > o.bar_open:
            for key, level in prior_rows.items():
                current = rows.get(key)
                if (not P.eligible(level) or current is None
                        or (level['lower'], level['upper']) != (current['lower'], current['upper'])):
                    continue
                threshold = P.midpoint(level)
                if (previous <= threshold or reclaim and o.bar_open <= threshold) and threshold < o.price:
                    pending.append(dict(level=deepcopy(level), threshold=threshold,
                        previous_close=previous, close=o.price, candle_open=o.bar_open,
                        closed_at=now, timeframe='1s'))
        book.update(at=now, close=o.price, levels=deepcopy(catalog) if structure_fresh else {},
                    pending=pending)
    events = []
    opening = False
    if trade:
        bucket = floor(now)
        opening = bucket > book.get('trade_bucket', -1)
        book['trade_bucket'] = max(bucket, book.get('trade_bucket', -1))
        if opening:
            pending = book.pop('pending', [])
            for proof in pending:
                # Empty seconds do not authorize delayed execution.
                if (structure_fresh and bucket == proof['closed_at']
                        and o.price > proof['threshold']):
                    current = rows.get(proof['level']['unified_level_id'])
                    if current is None or (current['lower'], current['upper']) != (
                            proof['level']['lower'], proof['level']['upper']):
                        continue
                    proof = dict(proof, opened_at=now, next_open=o.price)
                    book.setdefault('accepted', {})[current['unified_level_id']] = deepcopy(proof)
                    events.append(proof)
    return sorted(events, key=lambda proof:proof['threshold']), new_close, opening


def ceiling(d, rows, stop):
    accepted = d.get('resistance_1s', {}).get('accepted', {})
    catalog = dict(d.get('resistance_1s', {}).get('catalog', {}))
    for key, row in rows.items():
        if P.eligible(row) or key in catalog:
            catalog[key] = dict(row, role='resistance')
    for key, proof in accepted.items():
        catalog.setdefault(key, proof['level'])
    return min((r for key, r in catalog.items() if P.eligible(r)
        and r['lower'] >= stop-1e-9 and not (key in accepted
            and P.midpoint(r) == accepted[key]['threshold'])),
        key=lambda r:(r['lower'], r['unified_level_id']), default=None)


def evaluate(host, a, o, p, old_state):
    from .strategy_engine import AssignmentStatus as Status
    state = deepcopy(old_state)
    structural_only = p['early_squeeze_breakout_contract'] == STRUCTURAL_CONTRACT
    d = state.setdefault('squeeze_breakout', {})
    now, tick = o.observed_at.timestamp(), p['execution']['tick_size']
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    if d.get('session') != session:
        d.clear()
        d['session'] = session
    rows = levels(o)
    market = o.structural_detector_state or {}
    proof = market.get('fast_structure_evidence', {})
    cutoff, last_input = proof.get('as_of'), proof.get('max_input_timestamp')
    structure_fresh = bool(rows and all(r.get('input_policy') == V.POLICY
        and r.get('seed_input_policy') == V.POLICY for r in rows.values())
        and finite(cutoff, last_input) and last_input <= cutoff and 0 <= now-cutoff < 1.000001)
    sample = o.source_values.get('market.last_price', {})
    trade = bool('market_data_update' in o.evaluation_events
        and 'market.last_price' in o.changed_source_ids and E.stamp(sample.get('observed_at')) == o.observed_at
        and sample.get('value') == o.price and not any(s.startswith('quote:') for s in o.source_signal_ids))
    activation = o.source_values.get(E.SIGNAL, {})
    at = E.stamp(activation.get('observed_at'))
    if activation.get('value') is True and at and at <= o.observed_at and at.astimezone(H.NY).date().isoformat() == session:
        d['activated_at'] = min(d.get('activated_at', now), at.timestamp())
    events, closed, opening = observe(d, o, rows, structure_fresh, trade, reclaim=structural_only)
    preview = P.forming_macd_1s(o, d, trade, sparse=True)
    episode = d.setdefault('macd_1s', {})
    # Forming values remain an entry gate; only completed candles reset the
    # episode identity, break count and filled-add entitlement.
    if closed:
        prior_high = episode.get('high', 0.)
        valid = finite(o.macd_line, o.macd_signal)
        bullish = valid and o.macd_line > o.macd_signal
        if bullish and not episode.get('open'):
            episode.clear()
            episode.update(open=True, episode_id=now, broken_levels=[], high=o.price)
        elif valid and not bullish:
            episode['open'] = False
        episode.update(available=valid, line=o.macd_line, signal=o.macd_signal, observed_at=now)
        if episode.get('open'):
            episode['high'] = max(episode.get('high', o.price), o.price)
            episode['high_before_close'] = prior_high if episode.get('episode_id') != now else 0.
        volatility = P.observe_chop_volatility(d, now, o.bar_high, o.bar_low, o.price)
        d['decision_volatility'] = volatility
    if o.source_timeframe == '100ms' and 'bar_close' in o.evaluation_events:
        gate = d.setdefault('macd_100ms', {})
        if now > gate.get('observed_at', 0):
            gate.update(observed_at=now, line=o.macd_line, signal=o.macd_signal,
                open=finite(o.macd_line, o.macd_signal) and o.macd_line > o.macd_signal)
    gate = d.get('macd_100ms', {})
    if episode.get('open'):
        for event in events:
            key = event['level']['unified_level_id']
            if key not in episode['broken_levels']:
                episode['broken_levels'].append(key)
    active = state.get('squeeze_entry') or {}
    held = o.position_quantity > 0
    stop = float(state.get('active_stop') or 0)
    target = float((state.get('structural_profit_targets') or [0])[0])
    quote = V.fresh_quote(o)
    evidence = dict(contract=p['early_squeeze_breakout_contract'], resistance_break_events=deepcopy(events),
        resistance_acceptance=deepcopy(d.get('resistance_1s', {}).get('accepted', {})),
        macd_1s_episode=deepcopy(episode), forming_macd_1s=preview,
        macd_100ms_gate=dict(gate), decision_volatility=deepcopy(d.get('decision_volatility')),
        bid=o.bid, ask=o.ask, reference_price=o.price, active_stop=stop,
        profit_targets=[target] if target else [])

    def emit(action, reason, status=None, **kw):
        metadata = dict(evidence, **kw.pop('metadata', {}))
        if action == 'exit':
            state['entry_acquisition_exit_latched'] = True
            state['last_exit_reason'] = reason
            metadata.update(position_fraction=1., cancel_entry_acquisition=True, reentry_after_fill=True)
        result = host._result(a, o, action, reason, float(action in ('enter_long', 'add_long')), 1.,
            state, status or a.status, metadata=metadata,
            order_intent=dict(execution_policy='adaptive_urgent', protection_profile='structural-single-target'), **kw)
        if action in ('enter_long', 'add_long'):
            result = replace(result, evaluation=replace(result.evaluation, intents=tuple(
                replace(i, reference_price=o.ask, metadata={**i.metadata,
                    'mandatory_broker_target':True, 'wait_for_capital':False}) for i in result.evaluation.intents)))
        if action == 'add_long':
            for intent in result.evaluation.intents:
                d.setdefault('midpoint_add_requests', {})[intent.intent_id] = dict(
                    keys=metadata['squeeze_add_levels'], episode_id=episode.get('episode_id'), filled=False, terminal=False)
        return result

    if held and (state.get('entry_acquisition_exit_latched') or a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0):
        remaining = max(0., o.position_quantity-o.pending_exit_quantity)
        return emit('exit', 'complete_position_liquidation', Status.EXIT_PENDING, quantity=remaining) if remaining > 1e-9 else emit('hold', 'exit_fill_pending', Status.EXIT_PENDING)
    if held:
        if not active:
            return emit('hold', 'position_entry_state_unavailable', Status.MANAGING)
        if trade and o.price <= stop:
            return emit('exit', 'protective_stop', Status.EXIT_PENDING, quantity=o.position_quantity)
        if state.get('manual_exit_requested'):
            return emit('exit', 'manual_exit', Status.EXIT_PENDING, quantity=o.position_quantity)
        if closed and structure_fresh:
            P.observe_midpoint_chop(active, rows, now, o.price, volatility_gate=True,
                volatility=d.get('decision_volatility'))
        if active.get('midpoint_chop_exit'):
            return emit('exit', 'resistance_midpoint_chop_exit', Status.EXIT_PENDING, quantity=o.position_quantity,
                metadata=dict(resistance_chop=deepcopy(active['midpoint_chop_exit'])))
        results = []
        book = d.get('resistance_1s', {})
        # Ratchet only from a finished candle, never from its intrabar spike.
        if not structural_only and opening and structure_fresh and book.get('at') == floor(now):
            close = book['close']
            active['peak_close'] = max(active.get('peak_close') or close, close)
            distance = active['trail_distance']
            cap = ceiling(d, rows, stop)
            proposal = floor((active['peak_close']-distance)/tick+1e-9)*tick
            if cap:
                proposal = min(proposal, floor(cap['lower']/tick+1e-9)*tick)
            if quote and stop < proposal < min(o.price, o.bid):
                previous_stop = stop
                state['active_stop'] = stop = proposal
                results.append(emit('replace_protective_stop', 'confirmed_1s_resistance_trail', Status.MANAGING,
                    quantity=o.position_quantity, invalidation_price=stop,
                    metadata=dict(previous_stop=previous_stop, resistance_ceiling=deepcopy(cap),
                        trail_close_at=book['at'], trail_close=close, trail_distance=distance)))
        if structural_only and events and quote and d.get('decision_volatility'):
            higher = [event for event in events if event['threshold'] > active.get(
                'last_stop_midpoint', P.midpoint(active['anchor']))]
            if higher:
                confirmed = max(higher, key=lambda event:event['threshold'])
                proposed = below(confirmed['level']['lower']-.5*d['decision_volatility']['value'], tick)
                cap = ceiling(d, rows, stop)
                if cap:
                    proposed = min(proposed, below(cap['lower'], tick))
                if stop < proposed < min(o.price, o.bid):
                    previous_stop = stop
                    state['active_stop'] = stop = proposed
                    active['last_stop_midpoint'] = confirmed['threshold']
                    results.append(emit('replace_protective_stop', 'confirmed_resistance_step_stop', Status.MANAGING,
                        quantity=o.position_quantity, invalidation_price=stop,
                        metadata=dict(previous_stop=previous_stop, resistance_ceiling=deepcopy(cap),
                            resistance_confirmation=deepcopy(confirmed), stop_buffer=.5*d['decision_volatility']['value'])))
        if events and quote:
            ordinal = P.target_ordinal(len(episode.get('broken_levels', [])))
            overhead = sorted((r for r in rows.values() if P.is_resistance(r)
                and E.target_price(r, tick, E.CONTRACT) > o.ask), key=P.midpoint)
            if len(overhead) >= ordinal:
                selected = overhead[ordinal-1]
                proposal = E.target_price(selected, tick, E.CONTRACT)
                if proposal > target:
                    state['structural_profit_targets'] = [proposal]
                    active['target_moves'] = active.get('target_moves', 0)+1
                    results.append(emit('replace_profit_target', 'resistance_target_advance', Status.MANAGING,
                        quantity=o.position_quantity, profit_target_price=proposal,
                        metadata=dict(previous_profit_target=target, squeeze_previous_target_moves=active['target_moves']-1,
                            profit_target_selection=dict(level=deepcopy(selected), price=proposal, ordinal=ordinal))))
                    target = proposal
        if (events and quote and gate.get('open') and episode.get('open') and a.permissions.add
                and preview and finite(preview.get('line'), preview.get('signal')) and preview['line'] > preview['signal']):
            for event in events:
                key = event['level']['unified_level_id']
                if (active.get('slice_notional', 0) > 0 and key != active['anchor']['unified_level_id']
                        and P.midpoint_add_available(d, key, episode['episode_id'])
                        and stop < o.bid <= o.ask < target and purchase_window(event, o, d, stop, tick)):
                    results.append(emit('add_long', 'accepted_1s_resistance_addition', Status.MANAGING,
                        invalidation_price=stop, profit_target_price=target,
                        capital_request=CapitalRequest(mode='fixed_notional', value=active['slice_notional']),
                        metadata=dict(squeeze_add_levels=[key], resistance_confirmation=deepcopy(event))))
        if results:
            return replace(results[-1], state=state, evaluation=replace(results[-1].evaluation,
                intents=tuple(i for r in results for i in r.evaluation.intents)))
        return emit('hold', 'confirmed_1s_position_management', Status.MANAGING)

    if a.status == Status.ENTRY_PENDING or state.get('pending_capital_request'):
        return emit('wait', 'entry_fill_pending')
    if a.status in (Status.DISABLED, Status.PAUSED, Status.COMPLETED, Status.ERROR) or not a.permissions.observe or not (
            a.permissions.reenter if state.get('entries', 0) else a.permissions.enter):
        return emit('wait', 'entry_permission_closed')
    if not o.market_open or not 4 <= o.observed_at.astimezone(H.NY).hour < 20 or now <= d.get('activated_at', float('inf')):
        return emit('wait', 'waiting_for_post_squeeze_candle')
    if not events:
        return emit('wait', 'waiting_for_1s_resistance_acceptance')
    if not episode.get('open') or not preview or not finite(preview.get('line'), preview.get('signal')) or preview['line'] <= preview['signal']:
        return emit('wait', 'macd_1s_line_above_signal_required')
    if not gate.get('open'):
        return emit('wait', 'macd_100ms_line_above_signal_required')
    vol = d.get('decision_volatility')
    if not vol:
        return emit('wait', 'waiting_for_1s_volatility_warmup')
    same = d.get('last_entry_macd_episode') == episode.get('episode_id')
    if same and (d.get('last_entry_confirmation_at', now) >= events[-1]['closed_at']
                 or events[-1]['close'] <= episode.get('high_before_close', 0)):
        return emit('wait', 'waiting_for_confirmed_episode_high_break')
    event = events[-1]
    anchor = event['level']
    desired = below(anchor['lower']-.5*vol['value'], tick)
    stop = min(desired, below(min(o.price, o.ask)-max(.1 if not same else 0., 2*vol['value']), tick))
    if not quote or not purchase_window(event, o, d, stop, tick):
        return emit('wait', 'expired_or_extended_resistance_entry')
    quality_row = dict(effective_at=now, candle=dict(volume=o.source_values.get('market.trade_size', {}).get('value')))
    ready, quality = H.tradability(o, dict(p, structural_recovery=dict(H.QUALITY_DEFAULTS, **p['structural_recovery'])),
                                  quality_row, state, producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if not ready:
        return emit('wait', 'liquidity_or_spread_gate')
    source = o.source_values.get('indicator.vwap.execution_value@100ms', {})
    at, vwap = E.stamp(source.get('observed_at')), source.get('value')
    prefix = market.get('price_vwap_evidence', {})
    if (not at or at > o.observed_at or at.astimezone(H.NY).date() != o.observed_at.astimezone(H.NY).date()
            or prefix.get('authority') != 'latest-completed-qmd-100ms' or E.stamp(prefix.get('as_of')) != o.observed_at
            or prefix.get('source_observed_at') != source.get('observed_at') or not finite(vwap) or not 0 < vwap < o.price):
        return emit('wait', 'fresh_price_above_vwap_required')
    evidence['vwap_gate'] = dict(value=vwap, observed_at=source['observed_at'], close=o.price)
    count = len(episode.get('broken_levels', []))
    ordinal = P.target_ordinal(count)
    overhead = sorted((r for r in rows.values() if P.is_resistance(r) and r['unified_level_id'] != anchor['unified_level_id']
        and E.target_price(r, tick, E.CONTRACT) > o.ask), key=P.midpoint)
    if len(overhead) < ordinal:
        return emit('wait', 'overhead_resistance_target_unavailable')
    target = E.target_price(overhead[ordinal-1], tick, E.CONTRACT)
    if not 0 < stop < o.bid <= o.ask < target:
        return emit('wait', 'unrepresentable_stop_or_target')
    for key in ('liquidation_origin_fill_role', 'liquidation_origin_reentry_after_fill',
                'profit_target_liquidation_required', 'target_replenishment_pending', 'last_exit_reason'):
        state.pop(key, None)
    state.update(squeeze_entry=dict(anchor=deepcopy(anchor), structural_stop=stop, breakout_at=event['closed_at'],
        peak_close=event['close'], entry_price=o.ask, requested_at=now, target_moves=0,
        trail_distance=o.ask-stop, same_macd_episode_reentry=same),
        initial_stop=stop, active_stop=stop, structural_profit_targets=[target], entry_reference_price=o.ask,
        entry_at=o.observed_at.isoformat(), entries=state.get('entries', 0)+1, entry_acquisition_exit_latched=False)
    d.update(last_entry_macd_episode=episode['episode_id'], last_entry_confirmation_at=event['closed_at'],
        last_entry_episode_high=episode['high'])
    return emit('enter_long', 'accepted_1s_resistance_entry', Status.ENTRY_PENDING,
        invalidation_price=stop, profit_target_price=target, capital_request=CapitalRequest(mode='mandate_fraction', value=1/3),
        metadata=dict(unreserved_cash_slice=True, entry_selection=deepcopy(anchor), resistance_confirmation=deepcopy(event),
            stop_source='volatility_buffered_resistance_lower', same_episode_reentry=same,
            profit_target_selection=dict(level=deepcopy(overhead[ordinal-1]), price=target, ordinal=ordinal, episode_resistance_breaks=count)))


def purchase_window(event, o, d, stop, tick):
    """Fixed prospective anti-chase and spread/risk bounds; no ticker tuning."""
    volatility = d.get('decision_volatility')
    if not volatility:
        return False
    width = event['level']['upper']-event['level']['lower']
    extension = max(width, 2*volatility['value'], tick)
    return (event['opened_at'] == o.observed_at.timestamp() and event['threshold'] < o.price
        and o.ask <= event['threshold']+extension+1e-9 and 0 <= o.ask-o.bid <= .5*(o.ask-stop)+1e-9)

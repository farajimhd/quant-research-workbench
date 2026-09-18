"""Signal-gated, completed-candle resistance entries with a real-time fixed trail.

Shared helpers supply filtered V7 geometry and cash-slice plumbing only.
This executor owns all entry and management decisions.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from math import floor, isfinite

from . import historical_hod as H, vwap_resistance_ladder as V
from .signals import CapitalRequest

LEGACY_CONTRACT = 'early-squeeze-r1-fixed-trail-v1'
VWAP_CONTRACT = 'early-squeeze-r1-fixed-trail-v2'
RECOVERY_CONTRACT = 'early-squeeze-r1-fixed-trail-v3'
MIDPOINT_CONTRACT = 'early-squeeze-r1-fixed-trail-v4'
CONTRACT = 'early-squeeze-r1-fixed-trail-v5'
SIGNAL = 'signal.activation.price-squeeze-early'


def configure(p):
    if p.get('early_squeeze_breakout_contract') not in (LEGACY_CONTRACT, VWAP_CONTRACT, RECOVERY_CONTRACT, MIDPOINT_CONTRACT, CONTRACT):
        raise ValueError('Early Squeeze breakout requires its versioned filtered V7 adapter')
    foreign = [k for k,v in p.items() if k.endswith('_contract') and v
               and k not in ('early_squeeze_breakout_contract', 'structural_recovery_contract')]
    if foreign:
        raise ValueError('Early Squeeze cannot compose other trading contracts: '+', '.join(foreign))


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def below(anchor, bid, tick):
    """Offset an unusable anchor immediately, never relax executable geometry."""
    return round(floor((min(anchor, bid)-tick)/tick+1e-9)*tick, 10)


def record_exit(state, at, role, remaining, *, contract=CONTRACT):
    """Only actual exit fills can authorize stop-out recovery."""
    active = state.get('squeeze_entry') or {}
    if contract in (RECOVERY_CONTRACT, MIDPOINT_CONTRACT, CONTRACT) and not active.get('first_fill_at'):
        # Multiple child fills may each report an already-flat aggregate.
        # The first completed lifecycle owns its frozen recovery reference.
        return
    stopped = role in ('protective_stop', 'trailing_stop', 'protective_exit') or (
        role == 'managed_exit' and state.get('last_exit_reason') == 'protective_stop')
    d = state.setdefault('squeeze_breakout', {})
    if stopped and active.get('first_fill_at') and active.get('peak_close'):
        active.setdefault('stopout_reference', dict(high=active['peak_close'], anchor=deepcopy(active['anchor']),
                             breakout_at=active['breakout_at'], stopped_at=at.timestamp()))
    if remaining is None or abs(remaining) > 1e-9:
        return
    if active.get('stopout_reference'):
        d['recovery'] = deepcopy(active['stopout_reference'])
    else:
        d.pop('recovery', None)
    state.pop('squeeze_entry', None)


def release_add(state, keys):
    """An unfunded/unfilled addition remains eligible on a later green close."""
    active = deepcopy(state.get('squeeze_entry') or {})
    if not active:
        return
    for key in keys:
        if key in active.get('added_levels', []):
            active['added_levels'].remove(key)
        level = state.get('squeeze_breakout', {}).get('levels', {}).get(key)
        if level:
            active.setdefault('pending_adds', {})[key] = deepcopy(level)
    state['squeeze_entry'] = active


def observe_context(o, previous):
    """Passive completed-candle context cannot emit orders or activate a ticker."""
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    prior = {k:deepcopy(v) for k,v in previous.items() if k != 'previous'} if previous.get('session') == session else {}
    now = o.observed_at.timestamp()
    if now <= prior.get('closed_at', 0):
        return deepcopy(previous)
    rows = V.levels(o)
    if not rows or any(r.get('input_policy') != V.POLICY or r.get('seed_input_policy') != V.POLICY for r in rows.values()):
        return dict(session=session, closed_at=now)
    known = deepcopy(prior.get('levels', {}))
    broken = list(prior.get('broken', []))
    for key, r in known.items():
        if prior.get('close') is not None and prior['close'] <= r['upper'] < o.price and key not in broken:
            broken.append(key)
    resistance = {k:r for k,r in rows.items() if r.get('side') in (-1,'resistance') or r.get('role') == 'resistance'}
    known.update(resistance)
    return dict(session=session, closed_at=now, close=o.price, hod=o.structural_session_high,
                levels=known, resistance=resistance, broken=broken, previous=prior)


def target_price(level, tick, contract):
    if contract in (MIDPOINT_CONTRACT, CONTRACT):
        # A sell limit uses the nearest valid tick to the band midpoint.
        return round(floor(((level['lower']+level['upper'])/2)/tick+.5+1e-9)*tick, 10)
    return round((floor(level['upper']/tick+1e-9)+1)*tick, 10)


def overhead_levels(market, ask, tick, contract, exclude=''):
    if contract in (MIDPOINT_CONTRACT, CONTRACT):
        # A band containing the ask is not overhead when its midpoint is
        # already below the executable entry. Use current resistance roles.
        return sorted((r for k,r in market.get('resistance', {}).items()
            if k != exclude and (r['lower']+r['upper'])/2 > ask
            and target_price(r,tick,contract) > ask), key=lambda r:((r['lower']+r['upper'])/2,r['unified_level_id']))
    return sorted((r for k,r in market.get('levels', {}).items()
        if k not in market.get('broken', []) and k != exclude and r['upper'] > ask), key=lambda r:r['lower'])


def evaluate(host, a, o, p, old_state):
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    state = deepcopy(old_state)
    aligned = p['early_squeeze_breakout_contract'] == CONTRACT
    # Use resolved infrastructure settings, never the raw inherited profile.
    a = replace(a, parameters=p)
    tick = p['execution']['tick_size']
    now = o.observed_at.timestamp()
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    d = state.setdefault('squeeze_breakout', {})
    if d.get('session') != session:
        d.clear()
        d.update(session=session)
    passive = (o.structural_detector_state or {}).get('early_squeeze_context') or {}
    context = passive.get('previous', {}) if passive.get('closed_at') == now else passive
    if (context.get('session') == session and d.get('closed_at', 0) < context.get('closed_at', 0) < now):
        for key in ('closed_at','close','hod','levels','resistance','broken'):
            if key in context:
                d[key] = deepcopy(context[key])
    sample = o.source_values.get(SIGNAL, {})
    activated = stamp(sample.get('observed_at'))
    if (sample.get('value') is True and activated and activated <= o.observed_at
            and activated.astimezone(H.NY).date().isoformat() == session):
        d['activated_at'] = min(d.get('activated_at', now), activated.timestamp())
        d.setdefault('activation_event_id', sample.get('event_id', ''))
    active = state.get('squeeze_entry') or {}
    stop = float(state.get('active_stop') or 0.)
    target = float((state.get('structural_profit_targets') or [0.])[0])
    held = o.position_quantity > 0
    fresh = (o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
             and now > d.get('closed_at', 0))
    valid_bar = all(type(v) in (int, float) and isfinite(v) and v > 0
                    for v in (o.bar_open, o.bar_high, o.bar_low, o.price))
    fresh = fresh and valid_bar and o.bar_low <= min(o.bar_open, o.price) <= max(o.bar_open, o.price) <= o.bar_high
    previous_close = d.get('close')
    prior_rows = deepcopy(d.get('levels', {}))
    prior_resistance = deepcopy(d.get('resistance', prior_rows))
    prior_hod = d.get('hod')
    current_rows = V.levels(o)
    filtered = bool(current_rows) and all(r.get('input_policy') == V.POLICY
        and r.get('seed_input_policy') == V.POLICY for r in current_rows.values())
    # Reject a stale structural snapshot, including one from before activation.
    row = (o.structural_detector_state or {}).get('row', {})
    structure_fresh = row.get('effective_at') == now
    green = fresh and o.price > o.bar_open
    crossed = []
    if fresh:
        if filtered and structure_fresh:
            for key, level in prior_rows.items():
                if previous_close is not None and previous_close <= level['upper'] < o.price:
                    crossed.append((key, level))
            broken = d.setdefault('broken', [])
            for key, _ in crossed:
                if key not in broken:
                    broken.append(key)
            # Retain witnessed resistance geometry across role flips.
            known = deepcopy(prior_rows)
            known.update({k:r for k,r in current_rows.items()
                          if r.get('side') in (-1, 'resistance') or r.get('role') == 'resistance'})
            d['levels'] = known
            d['resistance'] = {k:r for k,r in current_rows.items()
                if r.get('side') in (-1,'resistance') or r.get('role') == 'resistance'}
            d['hod'] = o.structural_session_high
        else:
            d.pop('levels', None)
            d.pop('hod', None)
            d.pop('resistance', None)
        d.update(close=o.price, closed_at=now, last_open=o.bar_open)
        if active and (held or a.status == Status.ENTRY_PENDING) and not active.get('stopout_reference'):
            active['peak_close'] = max(active.get('peak_close', o.price), o.price)
    evidence = dict(contract=p['early_squeeze_breakout_contract'], activation=deepcopy({k:v for k,v in d.items()
        if k in ('activated_at', 'activation_event_id')}), filtered_v7=filtered)

    def emit(action, reason, status=None, **kw):
        metadata = dict(evidence, active_stop=state.get('active_stop'),
            profit_targets=list(state.get('structural_profit_targets') or []), **kw.pop('metadata', {}))
        if action == 'exit':
            state.update(last_exit_reason=reason, entry_acquisition_exit_latched=True)
            metadata.update(position_fraction=1., cancel_entry_acquisition=True,
                            reentry_after_fill=reason == 'protective_stop')
        result = host._result(a, o, action, reason, float(action in ('enter_long', 'add_long')), 1.,
            state, status or a.status, metadata=metadata,
            order_intent={'execution_policy':'adaptive_urgent', 'protection_profile':'structural-single-target'}, **kw)
        if action in ('enter_long', 'add_long'):
            result = replace(result, evaluation=replace(result.evaluation, intents=tuple(
                replace(i, reference_price=o.ask, execution_policy=i.resolved_execution_policy() if aligned else replace(i.resolved_execution_policy(),
                    envelope=replace(i.resolved_execution_policy().envelope, deadline_ms=100,
                        persist_until_cancelled=False, maximum_buy_price=o.ask)),
                    metadata={**i.metadata, 'mandatory_broker_target':True, 'wait_for_capital':False})
                for i in result.evaluation.intents)))
            if action == 'add_long':
                for intent in result.evaluation.intents:
                    active.setdefault('add_requests', {})[intent.intent_id] = list(metadata['squeeze_add_levels'])
        return result

    if a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0:
        return emit('hold' if held else 'wait', 'exit_fill_pending', Status.EXIT_PENDING)
    behavior = p['strategy_behavior']
    flatten = _at_or_after_session_time(o.observed_at, behavior['flatten_time'])
    if held:
        if not active:
            return emit('hold', 'position_entry_state_unavailable', Status.MANAGING)
        if not active.get('trail_distance') and o.average_price > stop > 0:
            active.update(trail_distance=o.average_price-stop, peak_price=o.average_price)
        # Broker stops/targets remain authoritative while quote data is stale.
        quote = V.fresh_quote(o)
        reason = ('manual_exit' if state.get('manual_exit_requested') else 'session_flatten' if flatten
                  else 'protective_stop' if quote and o.bid <= stop else
                  'profit_target' if quote and o.bid >= target > 0 else '')
        if reason:
            return emit('exit', reason, Status.EXIT_PENDING, quantity=o.position_quantity)
        results = []
        if quote and active.get('trail_distance'):
            active['peak_price'] = max(active.get('peak_price', o.bid), o.bid)
            proposal = round(floor((active['peak_price']-active['trail_distance'])/tick+1e-9)*tick, 10)
            if stop < proposal < o.bid:
                previous_stop = stop
                state['active_stop'] = stop = proposal
                results.append(emit('replace_protective_stop', 'fixed_distance_price_trail', Status.MANAGING,
                                    quantity=o.position_quantity, invalidation_price=stop,
                                    metadata=dict(previous_stop=previous_stop)))
        if (fresh if aligned else green) and filtered and structure_fresh:
            # Every distinct held-position break gets an opportunity, without MACD.
            seen = active.setdefault('added_levels', [])
            pending = active.setdefault('pending_adds', {})
            for key, level in crossed if green else []:
                if key not in seen and (aligned or (key != active['anchor']['unified_level_id'] and level['upper'] > active['entry_price'])):
                    pending.setdefault(key, deepcopy(level))
            for key in list(pending):
                if o.price <= pending[key]['upper']:
                    pending.pop(key)  # A future fresh green recross can renew it.
            overhead = overhead_levels(d,o.ask,tick,p['early_squeeze_breakout_contract'])
            count = len(d.get('broken', []))
            distance = 1 if count >= 6 else 2 if count >= 4 else 3
            if len(overhead) >= distance and (aligned or (crossed and (not active['late'] or active['target_moves'] < 2))):
                proposal = target_price(overhead[distance-1],tick,p['early_squeeze_breakout_contract'])
                if proposal > max(target, o.ask):
                    pending_target = active.get('pending_target', {})
                    if proposal > pending_target.get('price', 0):
                        active['pending_target'] = dict(price=proposal, moves=active['target_moves']+1)
            pending_target = active.get('pending_target', {})
            if quote and pending_target.get('price', 0) > max(target, o.ask):
                proposal = pending_target['price']
                state['structural_profit_targets'] = [proposal]
                previous_moves = active['target_moves']
                active['target_moves'] = pending_target['moves']
                results.append(emit('replace_profit_target', 'resistance_target_advance', Status.MANAGING,
                    quantity=o.position_quantity, profit_target_price=proposal,
                    metadata=dict(previous_profit_target=target, squeeze_previous_target_moves=previous_moves)))
                target = proposal
            if green and pending and quote and a.permissions.add and active.get('slice_notional', 0) > 0 and stop < o.bid <= o.ask < target:
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
    if a.status == Status.ENTRY_PENDING or state.get('pending_capital_request'):
        return emit('wait', 'entry_fill_pending', Status.ENTRY_PENDING)
    if a.status in (Status.DISABLED, Status.PAUSED, Status.COMPLETED, Status.ERROR):
        return emit('wait', 'entry_permission_closed')
    if not a.permissions.observe or not (a.permissions.reenter if state.get('entries', 0) else a.permissions.enter):
        return emit('wait', 'entry_permission_closed')
    local = o.observed_at.astimezone(H.NY)
    phase = 'premarket' if local.hour < 9 or (local.hour == 9 and local.minute < 30) else 'regular' if local.hour < 16 else 'after_hours'
    if flatten or not o.market_open or phase not in behavior['eligible_sessions'] or _at_or_after_session_time(o.observed_at, behavior['entry_cutoff_time']):
        return emit('wait', 'outside_entry_session')
    if 'activated_at' not in d or (now if aligned else now-1) < d['activated_at']:
        return emit('wait', 'waiting_for_post_squeeze_candle')
    if not green:
        return emit('wait', 'waiting_for_completed_green_1s')
    if not filtered or not structure_fresh:
        return emit('wait', 'filtered_v7_unavailable')
    ready, quality = H.tradability(o, dict(p, structural_recovery=dict(H.QUALITY_DEFAULTS, **p['structural_recovery'])),
                                  row, state, producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if not ready:
        return emit('wait', 'liquidity_or_spread_gate')
    vwap_key = ('indicator.vwap.execution_value@1s' if p['early_squeeze_breakout_contract'] != LEGACY_CONTRACT
                else 'indicator.vwap.execution_value')
    vwap_source = o.source_values.get(vwap_key, {})
    vwap_at = stamp(vwap_source.get('observed_at'))
    vwap = vwap_source.get('value') if p['early_squeeze_breakout_contract'] != LEGACY_CONTRACT else o.execution_vwap
    evidence['vwap_gate'] = dict(source_id=vwap_key, observed_at=vwap_source.get('observed_at'),
                                 value=vwap, close=o.price)
    if (not vwap_at or not 0 <= (o.observed_at-vwap_at).total_seconds() <= 2
            or type(vwap) not in (int, float) or not isfinite(vwap) or vwap <= 0 or o.price <= vwap):
        return emit('wait', 'fresh_price_above_vwap_required')
    recovery = d.get('recovery')
    if recovery:
        if now <= recovery['stopped_at'] or o.price <= recovery['high']:
            return emit('wait', 'waiting_for_frozen_close_high')
        anchor = recovery['anchor']
        swing = H.initial_swing_low(row, dict(lower=float('inf') if p['early_squeeze_breakout_contract'] in (RECOVERY_CONTRACT, MIDPOINT_CONTRACT, CONTRACT) else o.bid),
                                   now, pivot_not_before=recovery['breakout_at'],
                                   eligible=lambda s:s['lower'] > anchor['upper'])
        stop_anchor = swing['lower'] if swing else o.bar_open
        stop = below(stop_anchor, o.bid, tick)
        if aligned and not swing and stop_anchor < o.bid:
            stop = round(floor(stop_anchor/tick+1e-9)*tick, 10)
        stop_source = 'confirmed_swing_above_resistance' if swing else 'last_completed_candle_open_offset'
    else:
        candidates = [r for r in prior_resistance.values() if prior_hod and r['upper'] < prior_hod
                      and r['confirmed_at_ms']/1000 <= now-1]
        anchor = max(candidates, key=lambda r:(r['upper'], r['unified_level_id']), default=None)
        if not anchor or previous_close is None or not previous_close <= (anchor['lower']+anchor['upper'])/2 < o.price:
            return emit('wait', 'waiting_for_fresh_r1_midpoint_break')
        if o.bar_high <= o.bar_low or o.price < o.bar_low+.75*(o.bar_high-o.bar_low):
            return emit('wait', 'initial_close_not_top_quarter')
        stop = below(anchor['lower'], o.bid, tick)
        stop_source = 'broken_resistance_lower'
    count = len(d.get('broken', []))
    distance = 1 if count >= 6 else 2 if count >= 4 else 3
    overhead = overhead_levels(d,o.ask,tick,p['early_squeeze_breakout_contract'],anchor['unified_level_id'])
    if len(overhead) < distance:
        return emit('wait', 'overhead_resistance_target_unavailable')
    target = target_price(overhead[distance-1],tick,p['early_squeeze_breakout_contract'])
    if not 0 < stop < o.bid <= o.ask < target:
        return emit('wait', 'unrepresentable_stop_or_target')
    for key in ('liquidation_origin_fill_role', 'liquidation_origin_reentry_after_fill',
                'profit_target_liquidation_required', 'target_replenishment_pending', 'last_exit_reason'):
        state.pop(key, None)
    state.update(squeeze_entry=dict(anchor=deepcopy(anchor), breakout_at=recovery['breakout_at'] if recovery else now,
        peak_close=o.price, entry_price=o.ask, requested_at=now, added_levels=[], pending_adds={},
        late=count>=6, target_moves=0), initial_stop=stop, active_stop=stop, structural_profit_targets=[target],
        entry_reference_price=o.ask, entry_at=o.observed_at.isoformat(), entries=state.get('entries', 0)+1,
        entry_acquisition_exit_latched=False)
    return emit('enter_long', 'stopout_close_high_reentry' if recovery else 'squeeze_r1_midpoint_breakout', Status.ENTRY_PENDING,
        invalidation_price=stop, profit_target_price=target,
        capital_request=CapitalRequest(mode='mandate_fraction', value=1./3),
        metadata=dict(unreserved_cash_slice=True, entry_selection=deepcopy(anchor), stop_source=stop_source,
            frozen_reentry_high=recovery['high'] if recovery else None, session_resistance_breaks=count,
            profit_target_selection=dict(level=deepcopy(overhead[distance-1]), price=target, ordinal=distance),
            unified_structural_trigger={'current_snapshot':{'levels':[dict(anchor,
                entry_boundary=recovery['high'] if recovery else (anchor['lower']+anchor['upper'])/2)],
                'session_high':prior_hod, 'selected_at':o.observed_at.isoformat(), 'frozen_at_entry':True}}))

"""Causal mechanics for the Early Squeeze momentum candidate.

Prices, geometry and clocks in returned evidence are frozen at the decision.
This module never consumes chart annotations or retrospective extrema.
"""
from copy import deepcopy
from math import floor

from . import early_squeeze_consistent as C, early_squeeze_price as P
from . import vwap_resistance_ladder as V
from .early_squeeze_fast import below

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


def initial_stop(row, rows, now, price, vwap, tick, *, distance_reference):
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
    return dict(price=round(floor(.99*price/tick+1e-9)*tick, 10),
        reason='one_percent_entry_stop', entry_reference=price)


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
    previous = gate.get('last_trade')
    if trade:
        if (fresh and prior and previous is not None
                and previous <= prior['price'] < price and prior['confirmed_at'] < now):
            gate.update(open=True, broken_at=now, broken_pivot=deepcopy(prior), break_price=price)
        gate.update(last_trade=price, last_trade_at=now)
    if latest and (prior is None or latest['pivot_at'] > prior['pivot_at']):
        gate['reference'] = deepcopy(latest)
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

"""Causal later-entry references and targets; no indicator or execution authority."""
from copy import deepcopy
from math import ceil, floor
from statistics import mean


def average_gap(market):
    rows = [market['break_rows'][k] for k in market.get('broken', [])]
    gaps = [b['lower']-a['upper'] for a, b in zip(rows, rows[1:]) if b['lower'] > a['upper']]
    return mean(gaps) if gaps else None


def breakout_reference(market, hod):
    eligible = [r for r in market.get('known', {}).values() if r['upper'] < hod]
    return deepcopy(max(eligible, key=lambda r:r['upper'])) if eligible else None


def breakout_crossing(o, market, previous_price, tick, offset_ticks):
    if not o.structural_session_high or previous_price is None:
        return None
    reference = breakout_reference(market, o.structural_session_high)
    if not reference:
        return None
    trigger = (ceil(reference['upper']/tick-1e-9)+offset_ticks)*tick
    if previous_price < trigger <= o.price:
        return dict(anchor=reference, trigger=trigger)
    return None


def pending_breakout(o, market, clock, previous_price, tick, offset_ticks, episode, observe):
    """Keep a witnessed crossing until confirmation or structural invalidation.

    No crossing is invented from an already-above price. The current reference,
    price and episode are checked again on every strategy evaluation.
    """
    reference = breakout_reference(market, o.structural_session_high or 0)
    pending = clock.get('pending_breakout')
    def identity(row):
        return tuple(row.get(k) for k in ('unified_level_id', 'lower', 'upper'))
    if pending and (not reference or identity(pending['anchor']) != identity(reference)
            or o.price < pending['trigger'] or not episode.get('bullish')
            or o.price >= pending['target_price']
            or episode.get('used') or pending['episode_id'] != episode.get('started_at')):
        clock.pop('pending_breakout', None)
        pending = None
    if observe and episode.get('bullish') and not episode.get('used'):
        crossing = breakout_crossing(o, market, previous_price, tick, offset_ticks)
        if crossing:
            target = midpoint_target(market, crossing['anchor'], tick)
            if target and o.price < target['price']:
                pending = dict(crossing, witnessed_at=o.observed_at.isoformat(),
                               target_price=target['price'], episode_id=episode.get('started_at'))
                clock['pending_breakout'] = pending
    return deepcopy(pending)


def midpoint_target(market, reference, tick):
    gap = average_gap(market)
    if gap is None:
        return None
    threshold = reference['upper']+gap
    rows = [r for k,r in market.get('known', {}).items()
            if k not in market.get('broken', []) and r['lower'] > reference['upper']]
    above = [r for r in rows if r['lower'] >= threshold]
    if not above:
        return None
    upper = min(above, key=lambda r:r['lower'])
    below = [reference]+[r for r in rows if r['lower'] < threshold]
    lower = max(below, key=lambda r:r['upper'])
    if lower['upper'] >= upper['lower']:
        return None
    midpoint = (lower['upper']+upper['lower'])/2
    price = floor(midpoint/tick+.5+1e-9)*tick
    return dict(price=price, raw_midpoint=midpoint, average_gap=gap,
                threshold=threshold, lower=deepcopy(lower), upper=deepcopy(upper))

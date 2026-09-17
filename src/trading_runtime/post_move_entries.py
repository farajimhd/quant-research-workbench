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

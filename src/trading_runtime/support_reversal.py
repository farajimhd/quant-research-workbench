"""Causal support-reversal evidence for an opt-in research entry family.

No order permission is granted here. Callers retain liquidity, support retirement,
stop/quote clearance, resistance room, session and portfolio checks.
"""
from datetime import datetime
from math import isfinite


def number(value):
    return type(value) in (int, float) and isfinite(value)


def observe_volume(state, bar, session):
    """Retain at most sixty contiguous completed one-second candles."""
    if state.get('session') != session:
        state.clear()
        state.update(session=session, bars=[])
    values=[bar.get(k) for k in ('time','end','volume')]
    if not all(number(v) for v in values) or bar['end']-bar['time'] != 1 or bar['volume']<0:
        raise ValueError('Invalid completed volume candle')
    bars=state['bars']
    if bars and bar['end']<=bars[-1]['end']:
        if bar['end']==bars[-1]['end'] and bar['volume']==bars[-1]['volume']:
            return
        raise ValueError('Revised or out-of-order volume candle')
    if bars and bar['time']!=bars[-1]['end']:
        bars=[]
    state['bars']=[*bars[-59:],dict(end=bar['end'],volume=bar['volume'])]


def volume_evidence(state, now):
    bars=state.get('bars',[])
    ready=(len(bars)==60 and bars[-1]['end']==now and all(
        b['end']-a['end']==1 for a,b in zip(bars,bars[1:])))
    recent=sum(b['volume'] for b in bars[-5:]) if ready else None
    prior=sum(b['volume'] for b in bars[:-5]) if ready else None
    ratio=11*recent/prior if ready and prior>0 else None
    return dict(observed_at=now,ready=ratio is not None,ratio=ratio,
                recent_5s=recent,prior_55s=prior,observed_candles=len(bars))


def assess(row, pressure, volume, *, now, maximum_quote_age_ms=1000., minimum_acceleration=1.5):
    """Completed engulfing support rejection with current classified buying."""
    if not number(now) or not number(maximum_quote_age_ms) or maximum_quote_age_ms<=0 or not number(minimum_acceleration) or minimum_acceleration<=0:
        raise ValueError('Invalid support-reversal policy')
    labels=row.get('labels') or {}
    candle=row.get('candle') or {}
    checks=dict(
        completed_candle=row.get('effective_at')==now and candle.get('end')==now,
        engulfing='bullish_engulfing' in labels.get('geometry',()),
        support_rejection=bool(set(labels.get('interaction',())) & {'local:support_rejection','global:support_rejection'}),
        **pressure_checks(pressure,now=now,maximum_quote_age_ms=maximum_quote_age_ms),
        volume_acceleration=volume.get('ready') is True and volume.get('observed_at')==now
            and number(volume.get('ratio')) and volume['ratio']>=minimum_acceleration)
    return dict(passed=all(checks.values()),checks=checks,
                failed=[k for k,v in checks.items() if not v],observed_at=now,
                volume=dict(volume),minimum_acceleration=minimum_acceleration)


def pressure_checks(pressure, *, now, maximum_quote_age_ms):
    try:
        stamp=datetime.fromisoformat(pressure['observed_at'])
        pressure_at=stamp.timestamp() if stamp.tzinfo is not None else None
    except (KeyError,TypeError,ValueError):
        pressure_at=None
    fast=pressure.get('fast') or {}
    fields=('trades','classified_fraction','trade_imbalance','quote_imbalance','progress_spreads')
    valid=all(number(fast.get(k)) for k in fields)
    quote_age=pressure.get('quote_age_ms')
    return dict(
        pressure_current=pressure.get('contract')=='trade-nbbo-pressure-v1' and pressure.get('ready') is True
            and pressure_at is not None and 0<=now-pressure_at<=.25,
        quote_current=number(quote_age) and 0<=quote_age<=maximum_quote_age_ms,
        classified_buying=bool(valid and fast.get('quote_ready') is True and fast['trades']>=3
            and .5<=fast['classified_fraction']<=1 and 0<fast['trade_imbalance']<=1
            and 0<fast['quote_imbalance']<=1 and fast['progress_spreads']>0))


def pending_ready(saved, pressure, *, now, price, lifetime_s, maximum_quote_age_ms):
    """Latch completed setup only for its original lifetime; refresh live flow."""
    return bool(saved.get('assessment', {}).get('passed')
        and number(saved.get('at')) and 0 <= now-saved['at'] < lifetime_s
        and number(saved.get('price')) and price >= saved['price']
        and all(pressure_checks(pressure, now=now,
            maximum_quote_age_ms=maximum_quote_age_ms).values()))

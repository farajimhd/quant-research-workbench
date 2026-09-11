"""Causal Tier-2 research estimate; never represents official SIP bands.

QMD supplies its rolling trade-price mean (older rows expose it through the
midpoint of estimated bands). Opening auctions, eligibility differences and halt/reopening resets
are not reconstructed. State is kept in the assignment journal for resume.
"""
from math import isfinite
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


def estimate(observation, state):
    sample = observation.backtest_luld_reference
    now = observation.observed_at.timestamp()
    local = observation.observed_at.astimezone(NY)
    if not (9, 30) <= (local.hour, local.minute) < (16, 0):
        return None
    prior = observation.previous_close
    reference = sample.get('reference_price')
    stamp = sample.get('available_at_ms')
    if (any(type(v) not in (int, float) or not isfinite(v) for v in (prior, reference, stamp))
            or prior <= 0 or reference <= 0 or not 0 <= now * 1000 - stamp <= 5000):
        return None
    session = local.date().isoformat()
    if state.get('session') != session:
        state.clear()
        opening = observation.price if local.hour == 9 and local.minute < 35 else reference
        if not isfinite(opening) or opening <= 0:
            return None
        state.update(session=session, reference=opening, checked_at=now, effective_at=now)
    if now < state['checked_at']:
        return None
    # Keep the opening proxy through 09:35. Thereafter check at 30s intervals,
    # replacing the reference only on a move of at least 1%.
    if (local.hour, local.minute) >= (9, 35) and now - state['checked_at'] >= 30:
        state['checked_at'] = now
        if abs(reference / state['reference'] - 1) >= .01 - 1e-12:
            state.update(reference=reference, effective_at=now)
    ref = state['reference']
    width = ref * (.10 if prior > 3 else .20) if prior >= .75 else min(.15, ref * .75)
    if prior <= 3 and (local.hour, local.minute) >= (15, 35):
        width *= 2
    return dict(source='estimated', model='qmd-rolling-mean-tier2-v1',
        session_date=session, lower=max(.01, ref-width), upper=ref+width,
        reference_price=ref, previous_close=prior, assumed_tier=2,
        effective_at_ms=state['effective_at']*1000, available_at_ms=stamp)


def reference_from_indicator(indicator, as_of, bar=None):
    """Only a current frame's estimate may be used, never a carried snapshot."""
    raw = (bar or {}).get('estimated_luld_reference_price')
    if type(raw) in (int,float) and isfinite(raw) and raw > 0 and (bar or {}).get('estimated_luld_active'):
        return dict(reference_price=raw, available_at_ms=as_of.timestamp()*1000)
    lower = indicator.get('qmd_structure_luld_lower')
    upper = indicator.get('qmd_structure_luld_upper')
    if (any(type(v) not in (int, float) or not isfinite(v) for v in (lower, upper))
            or not 0 < lower < upper):
        return {}
    return dict(reference_price=(lower+upper)/2, available_at_ms=as_of.timestamp()*1000)

"""Optional exit assessment for an acquired setup that never developed."""
from math import isfinite


def assess(entry, quality, *, now, minimum_seconds, completed_candle):
    setup = entry.get('setup') or {}
    progress = setup.get('phase_progress') or {}
    first_fill = entry.get('first_fill_at')
    valid_clock = (type(first_fill) in (int, float) and isfinite(first_fill)
                   and 0 < first_fill <= now)
    age = now - first_fill if valid_clock else None
    checks = quality.get('checks') or {}
    required = ('fresh_uncrossed_quote', 'detector_fresh',
                'market.trade_rate_10s_fresh', 'market.trade_rate_60s_fresh')
    conditions = dict(
        enabled=minimum_seconds > 0,
        completed_candle=bool(completed_candle),
        filled_clock=valid_clock,
        elapsed=age is not None and age >= minimum_seconds,
        building=setup.get('phase') == 'building',
        progress_current=progress.get('observed_at') == now,
        progress_insufficient=progress.get('ready') is False,
        progress_defined=all(type(progress.get(k)) in (int, float)
            and isfinite(progress[k]) and progress[k] > 0
            for k in ('initial_fill', 'initial_risk', 'required_r', 'threshold')),
        slow_short=checks.get('current_trade_rate_10s') is False,
        slow_long=checks.get('current_trade_rate_60s') is False,
        fresh_inputs=all(checks.get(k) is True for k in required),
    )
    return dict(passed=all(conditions.values()), checks=conditions,
                failed=[k for k, v in conditions.items() if not v],
                observed_at=now, first_fill_at=first_fill, age_seconds=age,
                minimum_seconds=minimum_seconds)

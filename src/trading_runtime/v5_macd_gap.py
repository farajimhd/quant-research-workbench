"""Causal MACD-period entries, upper-bound breaks, lower-bound protection and gap-sized targets."""
from collections import deque
from math import floor

from src.market_engine.swing_structure import SwingStructure
from . import v5_breakout as v5
from .v5_hod_ladder import target

CONTRACT = 'swing-v5-macd-gap-1'


def acquisition_valid(o, p):
    return (o.macd_line is not None and o.macd_signal is not None
            and o.macd_line > o.macd_signal and o.execution_vwap is not None
            and o.price > o.execution_vwap * (1+p.get('v5_breakout', {}).get('vwap_offset_bps', 10)/10000))


def observe(o, p, state, *, typed_persistence=False):
    now = o.observed_at.timestamp()
    d = state.setdefault('v5_breakout_state', dict(contract=CONTRACT))
    if now < d.get('observed_at', 0):
        return
    opened = o.macd_line is not None and o.macd_signal is not None and o.macd_line > o.macd_signal
    closed = o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
    # Only a completed bearish bar closes the period; intrabar noise does not.
    if closed and o.macd_line is not None and o.macd_signal is not None and not opened:
        d.update(period_max=None, exited=False)
    if d.get('holding') and o.position_quantity <= 0 and opened:
        d['exited'] = True
    d.update(macd_open=opened, holding=o.position_quantity > 0, observed_at=now,
             prior_max=d.get('period_max'), forming=None, crossed=[])
    prior = d.get('levels', [])
    d['decision_levels'] = prior
    closed = o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
    if closed and now > d.get('closed_at', 0):
        previous = d.get('closed_price')
        d['crossed'] = [r for r in prior if previous is not None and previous <= r['upper'] < o.price]
        if opened:
            d['period_max'] = max(d.get('period_max') or o.price, o.price, o.bar_open or o.price)
        # Reuse the structural detector's bounded stall state. Three tests at
        # an approached ceiling designate a provisional, not qualified, level.
        if o.bar_high is not None and o.bar_low is not None:
            probe = SwingStructure()
            probe.approach = deque(d.get('approach', []))
            probe.stalls = d.get('stalls', {'resistance': None, 'support': None})
            probe.previous_close = previous
            probe._stall(now, o.bar_high, o.bar_low, o.price)
            candidate = probe.stalls['resistance']
            if candidate and candidate['tests'] >= 3:
                identity = [candidate['first'], candidate['price']]
                if identity != d.get('forming_id'):
                    d['forming'] = dict(price=candidate['price'], available_at=now, provisional=True)
                    d['forming_id'] = identity
            d.update(approach=list(probe.approach), stalls=probe.stalls)
        d.update(closed_at=now, closed_price=o.price)
    d['levels'] = v5.rows(o, p, typed_persistence=typed_persistence)
    if o.position_quantity <= 0:
        d.pop('pending_target', None)
        d.pop('fill_stop_initialized', None)


def choose_target(levels, price, p, anchor=None):
    above = [r for r in levels if target(r, p)['price'] > price]
    if not above:
        return None
    if anchor is None:
        lower = [r for r in levels if r['lower'] <= price]
        anchor = lower[-1] if lower else None
    below = [r for r in levels if anchor and r['lower'] < anchor['lower']]
    gap = anchor['lower'] - below[-1]['lower'] if below else 0
    threshold = anchor['lower'] + gap if anchor else price
    row = next((r for r in above if r['lower'] >= threshold), None)
    return target(row, p) if row else None


def select(o, p, state):
    d = state.get('v5_breakout_state', {})
    if not d.get('macd_open'):
        return dict(reason='v5_macd_not_open')
    if not o.execution_vwap or o.price <= o.execution_vwap * (1+p['v5_breakout'].get('vwap_offset_bps', 10)/10000):
        return dict(reason='v5_price_not_above_vwap')
    if d.get('prior_max') is not None and o.price <= d['prior_max']:
        return dict(reason='v5_period_high_not_reclaimed')
    selected = choose_target(d.get('decision_levels', []), max(o.price, o.ask), p)
    if not selected:
        return dict(reason='v5_gap_target_unavailable')
    tick = p['execution']['tick_size']
    stop = floor(o.price * (1-p['v5_breakout']['initial_stop_pct']/100) / tick + 1e-9) * tick
    if stop >= min(o.price, o.bid):
        return dict(reason='v5_stop_already_triggered')
    return dict(reason='', stop=stop, target=selected['price'], target_selection=selected,
                broken_at=o.observed_at.timestamp(), references=d.get('decision_levels', []),
                session_high=o.structural_session_high, interval_based=True)


def manage(o, p, state):
    d = state['v5_breakout_state']
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    if not d.get('fill_stop_initialized') and o.average_price > 0:
        # The first actual fill establishes the 5% stop; later ratchets never fall.
        current = floor(o.average_price * (1-p['v5_breakout']['initial_stop_pct']/100) / p['execution']['tick_size'] + 1e-9) * p['execution']['tick_size']
        d['fill_stop_initialized'] = True
    for row in d.get('crossed', []):
        current = max(current, v5.below(row, p))
        selected = choose_target(d['decision_levels'], max(o.price, o.ask), p, row)
        if selected:
            existing = state.get('structural_profit_targets') or []
            if not existing or selected['price'] > existing[0]:
                d['pending_target'] = selected
    return current

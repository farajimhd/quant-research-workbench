"""Position-independent, causal V7 encounter state, persisted with the assignment."""
from copy import deepcopy
from math import isclose, isfinite


def threshold(level, settings, tick):
    return level['price'] + max(tick * settings['breakout_buffer_ticks'],
        level['price'] * settings['breakout_buffer_bps'] / 10000)


def eligible(level):
    return level.get('role') == 'transition' or level.get('side') in (-1, 'resistance')


def update(state, observation, market, settings, tick, fresh):
    session = market.get('session')
    if state.get('session') != session:
        state.clear()
        state.update(session=session, levels={})
    levels = state.setdefault('levels', {})
    now = observation.observed_at.timestamp()
    reason = ''
    # Quote updates cannot consume the next-opening opportunity.
    if not fresh:
        if ('market_data_update' in observation.evaluation_events
                and 'market.last_price' in observation.changed_source_ids):
            for encounter in levels.values():
                warning = encounter.get('warning')
                if not warning or warning.get('opening_seen') or not warning['end'] <= now < warning['end'] + 1:
                    continue
                warning['opening_seen'] = True
                price = observation.price
                if (isfinite(price) and price > 0 and price < warning['open']
                        and price < encounter['failure_threshold']):
                    encounter.update(status='failed', failed_at=now)
                    reason = 'topping_rejection_next_open'
        state['exit_reason'] = reason
        return reason
    bar = market['bar']
    previous = market.get('prior_bar')
    contiguous = previous and previous['end'] == bar['time']
    if state.get('at', 0) >= bar['end']:
        return ''
    state['at'] = bar['end']
    # A group of rejected overhead levels must be recovered together.
    blocked = [e for e in levels.values() if e['status'] in ('warning', 'failed')]
    recovery = max((e['threshold'] for e in blocked), default=0)
    if blocked and bar['close'] >= bar['open'] and bar['close'] > recovery:
        for encounter in blocked:
            encounter.update(status='broken', recovered_at=bar['end'], red_closes=0)
            encounter.pop('warning', None)
    for level in market.get('prior_rows', []):
        if not eligible(level) or level.get('confirmed_at_ms', float('inf')) > bar['time'] * 1000:
            continue
        key = str(level['unified_level_id'])
        boundary = threshold(level, settings, tick)
        if (key not in levels and contiguous and previous['close'] <= boundary
                and bar['high'] >= level['lower']
                and (bar['low'] <= level['upper'] or bar['close'] > boundary)):
            levels[key] = dict(level=deepcopy(level), threshold=boundary,
                failure_threshold=level['lower'] * (1-settings['rejection_break_offset_bps']/10000),
                status='approaching', red_closes=0)
    span = bar['high'] - bar['low']
    tail = bar['high'] - max(bar['open'], bar['close'])
    fraction = tail / span if span > 0 else 0
    topping = fraction > settings['topping_tail_fraction'] or isclose(fraction, settings['topping_tail_fraction'], abs_tol=1e-12)
    for encounter in levels.values():
        level = encounter['level']
        touching = bar['high'] >= level['lower'] and bar['low'] <= level['upper']
        if (encounter['status'] not in ('warning', 'failed') and bar['close'] >= bar['open']
                and bar['close'] > encounter['threshold']):
            encounter['status'] = 'broken'
        if touching and topping and bar['close'] <= encounter['threshold']:
            if encounter['status'] != 'failed':
                encounter['status'] = 'warning'
            encounter['warning'] = dict(open=bar['open'], end=bar['end'], fraction=fraction)
        encounter['red_closes'] = (encounter.get('red_closes', 0) + 1 if contiguous else 1) if bar['close'] < bar['open'] else 0
        # Departed, recovered encounters cannot turn unrelated red candles into failures.
        recent_touch = encounter.get('touch_at', 0) >= bar['time'] - 1
        if (contiguous and (touching or recent_touch or encounter['status'] in ('warning', 'failed'))
                and encounter['red_closes'] >= 2 and bar['low'] < previous['low']
                and bar['close'] < previous['open'] and bar['close'] < encounter['failure_threshold']):
            encounter.update(status='failed', failed_at=bar['end'])
            reason = reason or 'buffered_resistance_failure'
        if touching:
            encounter['touch_at'] = bar['end']
    state['exit_reason'] = reason
    return reason


def blocked(state):
    return any(e['status'] in ('warning', 'failed') for e in state.get('levels', {}).values())


def evidence(state):
    return {k:dict(status=e['status'], center=e['level']['price'], lower=e['level']['lower'],
        upper=e['level']['upper'], breakout_threshold=e['threshold'], failure_threshold=e['failure_threshold'],
        warning=deepcopy(e.get('warning')),failed_at=e.get('failed_at'))
        for k,e in state.get('levels', {}).items() if e['status'] in ('warning', 'failed')}

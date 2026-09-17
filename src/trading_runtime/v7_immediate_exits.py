"""Opt-in position-owned exits using forming candles and causal V7 bands."""
from copy import deepcopy
from math import isfinite


def bands(rows):
    result = {}
    for row in rows:
        values = [row.get(k) for k in ('lower', 'price', 'upper')]
        if (row.get('side') not in (-1, 'resistance') or row.get('role') == 'transition'
                or row.get('unified_level_id') is None
                or any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in values)
                or not values[0] <= values[1] <= values[2]):
            continue
        result[str(row['unified_level_id'])] = deepcopy(row)
    return result


def observe_floor(state, observation, market, fresh):
    floor = state.get('topping_tail_reentry')
    if not floor:
        return
    if floor['session'] != market.get('session'):
        state.pop('topping_tail_reentry', None)
        return
    now = observation.observed_at.timestamp()
    if now <= floor['at']:
        return
    # Completed lows cover intervening trades; quote-only observations cannot
    # invent a trade-price breach. The exit candle's own low predates the floor.
    bar = market.get('bar') or {}
    traded = ('market_data_update' in observation.evaluation_events
              and 'market.last_price' in observation.changed_source_ids)
    low = bar.get('low') if fresh and bar.get('time', 0) >= floor['at'] else None
    if ((traded and observation.price < floor['close'])
            or (low is not None and low < floor['close'])):
        floor.update(breached=True, breached_at=now)


def assess(entry, observation, market, settings, fresh):
    """Freeze encountered geometry so later role changes cannot erase a break."""
    now = observation.observed_at.timestamp()
    filled = entry.get('first_fill_at')
    if type(filled) not in (int, float) or not isfinite(filled) or not 0 < filled <= now:
        return '', {'ready': False, 'reason': 'first_fill_clock_unavailable'}
    current = entry.setdefault('immediate_exits', {})
    bar = market.get('bar') or {}
    completed = bool(fresh and bar.get('time', 0) >= filled
                     and bar.get('end', 0) > current.get('candle_at', 0))
    traded = ('market_data_update' in observation.evaluation_events
              and 'market.last_price' in observation.changed_source_ids)
    forming = (observation.bar_open, observation.bar_high, observation.price)
    if traded and all(type(v) in (int, float) and isfinite(v) and v > 0 for v in forming):
        opening, high, close = forming
        tail = high - max(opening, close)
        body = abs(close - opening)
        ratio = settings['setup_immediate_tail_body_ratio']
        if ratio and tail > 0 and tail > ratio * body + 1e-12:
            return 'topping_tail_immediate', dict(ready=True, candle=dict(
                open=opening, high=high, close=close, observed_at=now, forming=True),
                upper_wick=tail, body=body, minimum_body_ratio=ratio)
    if completed:
        current['candle_at'] = bar['end']
    if not (completed or traded):
        return '', {'ready': True}
    price = observation.price
    previous_price = current.get('last_price', observation.average_price)
    current['last_price'] = price
    tracked = current.setdefault('bands', {})
    for key, level in bands(market.get('prior_rows', [])).items():
        # Only geometry already available before this observation can arm a break.
        if level.get('confirmed_at_ms', float('inf')) <= now * 1000:
            tracked.setdefault(key, dict(level=level, broken=False))
    if len(tracked) > 4096:
        raise ValueError('Immediate resistance exit capacity exceeded')
    dwell = settings['setup_entry_resistance_seconds']
    for item in tracked.values():
        level = item['level']
        if settings['setup_resistance_return_exit']:
            if completed and item['broken'] and bar['close'] < level['lower']:
                return 'broken_resistance_close_below', dict(ready=True, level=deepcopy(level),
                    broken_at=item['broken_at'], candle=deepcopy(bar))
            if previous_price > 0 and previous_price <= level['upper'] < price:
                item.update(broken=True, broken_at=item.get('broken_at', now))
        # Entry bands are frozen with the order request, but the dwell clock
        # starts only on an actual fill. Leaving a band permanently disarms it.
    for item in entry.get('entry_resistance_bands', {}).values():
        level = item['level']
        if item.get('departed'):
            continue
        if not level['lower'] <= observation.average_price <= level['upper']:
            item['departed'] = True
            continue
        inside = level['lower'] <= price <= level['upper']
        if completed:
            inside = inside and bar['low'] >= level['lower'] and bar['high'] <= level['upper']
        if not inside:
            item['departed'] = True
        elif dwell and now - filled >= dwell:
            return 'entry_resistance_timeout', dict(ready=True, level=deepcopy(level),
                first_fill_at=filled, elapsed_seconds=now-filled)
    return '', {'ready': True}

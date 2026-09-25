"""Bounded, checkpointable impulse evidence from completed 100ms prices."""
from copy import deepcopy


def observe(state, at, price, minimum_pct):
    if at <= state.get('at', float('-inf')):
        return
    history = state.setdefault('prices', [])
    history.append([at, price])
    while len(history) > 1 and history[1][0] <= at - 30 + 1e-5:
        history.pop(0)
    base_at, base = history[0]
    # Native candles are sparse: an empty interval does not change last price.
    # Retain exactly the as-of boundary sample, never interpolate a future one.
    ready = base_at <= at - 30 + 1e-5
    rising = ready and price / base >= 1 + minimum_pct / 100 - 1e-12
    move = state.get('move')
    # Fluctuation around 5% must not keep rebasing the same rally. A successor
    # needs a correction and a wholly later measurement window.
    new_move = rising and (not move or
        (move.get('corrected') or move.get('invalid')) and base_at >= move['peak_at'])
    if new_move:
        move = dict(id=at, base=base, base_at=base_at, peak=price, peak_at=at)
        state['move'] = move
    if move and not move.get('invalid'):
        if price > move['peak']:
            move.update(peak=price, peak_at=at)
        else:
            correction = (move['peak'] - price) / (move['peak'] - move['base'])
            if correction >= .20 - 1e-12:
                move['corrected'] = True
            if correction > .45 + 1e-12:
                move['invalid'] = True
    state.update(at=at, rising=bool(rising))


def qualify(market, anchor, consumed, *, typed_persistence=False):
    move = market.get('pullback_impulse', {}).get('move')
    if not move or move.get('invalid') or move['id'] in consumed:
        return None
    swing = anchor['swing']
    # Pivot timestamps mark 1s candle ends; require the entire pivot candle
    # to follow the peak, avoiding an unknowable intrabar ordering.
    if move['peak_at'] > swing['pivot_at'] - 1:
        return None
    fraction = (move['peak'] - swing['price']) / (move['peak'] - move['base'])
    if not .20 - 1e-12 <= fraction <= .45 + 1e-12:
        return None
    result = dict(deepcopy(move), retracement=fraction)
    if typed_persistence:
        from .arte_assignment_vwap_pullback_move import validate_entry_pullback_move
        return validate_entry_pullback_move(result)
    return result

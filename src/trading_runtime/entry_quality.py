"""Opt-in acquisition economics shared by sizing and order execution."""
from math import isfinite


def blocked(policy, *, bid, ask, stop, target, quantity):
    if not policy:
        return ''
    values = (bid, ask, stop, target, quantity)
    if any(type(v) not in (int, float) or not isfinite(v) for v in values):
        return 'entry_quality_missing_geometry'
    if not 0 < stop < bid <= ask < target or quantity <= 0:
        return 'entry_quality_invalid_geometry'
    spread = ask - bid
    if bid - stop + 1e-9 < policy['quote_clearance_spreads'] * spread:
        return 'entry_stop_inside_quote_noise'
    fee = max(policy['minimum_fee'], policy['fee_per_share'] * quantity)
    costs = 2 * fee + quantity * (spread + 2 * ask * policy['slippage_bps'] / 10000)
    # Do not justify tiny orders with a distant, optimistic target.
    reward = quantity * min(target - ask, policy['reward_cap_r'] * (ask - stop))
    if reward + 1e-9 < costs * policy['minimum_reward_cost_multiple']:
        return 'entry_reward_below_execution_cost'
    return ''

"""Explicit migration of portfolio no-order rows into held identity labels.

A legacy no-order row when flat becomes WAIT. With H held identities it
becomes H HOLD examples, one for every already-held ticker, each weighted
1/H. This represents all causal valid identities without picking an arbitrary
ticker or multiplying a portfolio snapshot's optimization weight. It creates
no order outcomes and does not change account state, clocks or order labels.
Exact HOLD token accuracy must be read alongside H: multiple valid held
identities share the same observation and cannot all be the single argmax.
"""
from dataclasses import replace

from research.rl_trading.v6.action_contract import ActionAxes
from research.rl_trading.v6.training import _validate


def split_wait_hold(decisions, outcomes, listings):
    """Return new immutable tuples; original audited labels remain untouched."""
    _validate(decisions, outcomes, listings)
    migrated = []
    remap = {}
    clock = None
    order = 0
    for item in decisions:
        if item.close_us != clock:
            clock, order = item.close_us, 0
        axes = ActionAxes(listings, len(item.held_index))
        tokens = ([axes.hold_base + slot for slot in range(axes.holdings)]
                  if item.token == 0 and axes.holdings else [item.token])
        remap[(item.close_us, item.order_index)] = order
        for token in tokens:
            migrated.append(replace(item, order_index=order, token=token,
                                    sample_weight=item.sample_weight/len(tokens)))
            order += 1
    migrated_outcomes = tuple(replace(item, source_order_index=remap[
        (item.source_close_us, item.source_order_index)]) for item in outcomes)
    result = tuple(migrated)
    _validate(result, migrated_outcomes, listings, wait_hold=True)
    return result, migrated_outcomes

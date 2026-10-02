"""Explicit research clock contract; never relabel the faithful app release.

The main clock drives both policy and broker. Source liquidity may be finer,
but searched feature timeframes must be at least the main clock. Completed
1s MACD stays a producer-owned input, also when main-clock bars are 500ms.
"""

import torch

from .atomic_graph import Threshold, trace
from .strategy_one_program import _inputs


def validate_clock(clock_ms):
    if type(clock_ms) is not int or clock_ms not in (500, 1000):
        raise ValueError("Unified main clock must be 500ms or 1000ms")
    return clock_ms


def unified_add_graph():
    """Lower the coarse research gate into ordinary searchable primitives."""
    return trace(
        unified_add_admission,
        _inputs(unified_add_admission),
        bounds={
            (int, 10000): Threshold(
                "integer_price_scale", 10000, 10000, True, "integer_price_per_price"
            )
        },
    )


def unified_add_admission(x):
    """Research add gate: main-clock price bar and completed 1s MACD.

    `break_new` compares against the PREVIOUS accepted-ID set; `break_accepted`
    compares against the just-confirmed protection state. Purchases count order
    groups, never the number of partial fills. Financial state refresh between
    accepted adds belongs to the portfolio coordinator.
    """
    at = x["boundary_ms"]
    return (
        (at > 0)
        & (at.remainder(1000) == 0)
        & (x["break_boundary_ms"] == at)
        & x["break_is_resistance"]
        & x["break_new"]
        & x["break_accepted"]
        & (x["quantity"] > 0)
        & ~x["pending_exit"]
        & ~x["pending_entry"]
        & ~x["pending_capital_request"]
        & (x["purchase_ordinal"] >= 2)
        & (x["purchase_ordinal"] <= 3)
        & (x["current_purchase_groups"] == x["purchase_ordinal"] - 1)
        & x["bars_valid"]
        & (x["bar_base_boundary_ms"] == at)
        & (x["bar_1s_boundary_ms"] == at)
        & torch.isfinite(x["macd_1s_line"])
        & torch.isfinite(x["macd_1s_signal"])
        & torch.isfinite(x["macd_1s_line"])
        & torch.isfinite(x["macd_1s_signal"])
        & (x["macd_1s_line"] > x["macd_1s_signal"])
        & (x["macd_1s_line"] > x["macd_1s_signal"])
        & (x["open_1s_int"] > 0)
        & (x["close_1s_int"] > x["open_1s_int"])
        & (x["close_base_int"] > 0)
        & (x["close_base_int"] > x["break_midpoint"] * 10000)
        & x["quote_valid"]
        & (x["bid"] > 0)
        & (x["ask"] >= x["bid"])
        & torch.isfinite(x["fresh_bid"])
        & torch.isfinite(x["fresh_ask"])
        & ((x["bid"] - x["fresh_bid"]).abs() <= 1e-9)
        & ((x["ask"] - x["fresh_ask"]).abs() <= 1e-9)
        & (x["stop"] > 0)
        & (torch.round(x["stop"] * 10000) < torch.round(x["bid"] * 10000))
        & (torch.round(x["ask"] * 10000) < torch.round(x["target"] * 10000))
    )

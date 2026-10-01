"""A reproducible random valid program, sampled from a bounded 1s grammar."""

import random

from research.vectorized_backtest.v1.strategy_encoding import Instruction as I
from research.vectorized_backtest.v1.strategy_encoding import Operation as O
from research.vectorized_backtest.v1.strategy_encoding import (
    Parameter,
    Program,
    Unit,
    arte_catalog,
)

from .compiler import HistoryOperation as H


def seeded_example(seed=20261001):
    """Sample EMA input/operation labels and thresholds; not Candidate 328."""
    rng = random.Random(seed)
    period = rng.choice((7, 9, 12, 20, 50))
    comparison = rng.choice((O.GREATER, O.GREATER_EQUAL))
    values = (rng.uniform(0, 20), rng.uniform(45, 65), rng.uniform(0.005, 0.03))
    catalog = arte_catalog(
        (
            Parameter("gap_bps", Unit.BPS, -100, 100),
            Parameter("rsi", Unit.RSI, 0, 100),
            Parameter("cash_fraction", Unit.FRACTION, 0.001, 1),
        )
    )
    labels = {f.name: f.label for f in catalog.inputs}
    program = Program.encode(
        [
            I(O.GAP_BPS, labels["close@1000ms"], labels[f"ema_{period}@1000ms"]),
            I(O.PARAMETER, parameter=0),
            I(comparison, -1, -2),
            I(O.PARAMETER, parameter=1),
            I(O.GREATER_EQUAL, labels["rsi_14@1000ms"], -4),
            I(O.AND, -3, -5),
            I(O.PARAMETER, parameter=2),
            I(O.ENTER, -6, -7),
            I(O.LESS, labels["macd_line@1000ms"], labels["macd_signal@1000ms"]),
            I(O.EXIT, -9),
        ],
        values,
        pad_to=16,
    )
    return program, catalog


def history_example(lookback=12):
    """Close/high/RSI source windows; lookback is an optimizable count in [1,64].

    This is a research policy downstream of the shared Early Squeeze admission.
    Window field labels remain atomic and preserve their own resolution/units.
    """
    catalog = arte_catalog(
        (
            Parameter("source_bars", Unit.COUNT, 1, 64, integer=True),
            Parameter("mean_gap_bps", Unit.BPS, -100, 100),
            Parameter("minimum_rsi", Unit.RSI, 0, 100),
            Parameter("cash_fraction", Unit.FRACTION, 0.001, 1),
        )
    )
    labels = {f.name: f.label for f in catalog.inputs}
    close, high, rsi = (
        labels[name] for name in ("close@1000ms", "high@1000ms", "rsi_14@1000ms")
    )
    nodes = [
        I(H.BAR_MEAN, close, parameter=0),  # -1: recent close mean
        I(O.GAP_BPS, close, -1),  # -2: gap in basis points
        I(O.PARAMETER, parameter=1),  # -3: gap threshold
        I(O.GREATER_EQUAL, -2, -3),  # -4
        I(H.BAR_MAX, high, parameter=0),  # -5: recent high envelope
        I(O.LESS_EQUAL, close, -5),  # -6
        I(H.BAR_MEAN, rsi, parameter=0),  # -7: recent RSI mean
        I(O.GREATER_EQUAL, rsi, -7),  # -8
        I(O.PARAMETER, parameter=2),  # -9: absolute RSI floor
        I(O.GREATER_EQUAL, rsi, -9),  # -10
        I(O.AND, -4, -6),  # -11
        I(O.AND, -11, -8),  # -12
        I(O.AND, -12, -10),  # -13
        I(O.PARAMETER, parameter=3),  # -14: order fraction
        I(O.ENTER, -13, -14),  # terminal
        I(O.LESS, labels["macd_line@1000ms"], labels["macd_signal@1000ms"]),  # -16
        I(O.EXIT, -16),
    ]
    return Program.encode(nodes, (lookback, 2.0, 50.0, 0.02), pad_to=24), catalog


def v7_example(lookback=12):
    """V6 nearest-slot history gate, with level-derived stops and targets.

    A source window follows the slot at each completed candle; level IDs are
    intentionally not inferred. The stop multiplier is a dimensionless ratio.
    """
    from .v7 import arte_catalog

    catalog = arte_catalog(
        (
            Parameter("source_bars", Unit.COUNT, 1, 64, integer=True),
            Parameter("overhead_gap_bps", Unit.BPS, 0, 1000),
            Parameter("cash_fraction", Unit.FRACTION, 0.001, 1),
            Parameter("stop_price_ratio", Unit.RATIO, 0.9, 1),
        )
    )
    labels = {f.name: f.label for f in catalog.inputs}
    above = lambda field: labels[f"v7.above[0].{field}"]
    below = lambda field: labels[f"v7.below[0].{field}"]
    return Program.encode(
        [
            I(H.BAR_MEAN, above("center_distance_bps"), parameter=0),  # -1
            I(O.PARAMETER, parameter=1),  # -2: searchable overhead gap
            I(O.GREATER, -1, -2),  # -3
            I(O.AND, above("present"), -3),  # -4
            I(O.AND, below("present"), -4),  # -5: both slots needed for entry
            I(O.PARAMETER, parameter=2),  # -6
            I(O.ENTER, -5, -6),
            I(O.PARAMETER, parameter=3),  # -8
            I(O.MULTIPLY, below("lower_price"), -8),  # -9: buffered lower band
            I(O.SET_STOP, below("present"), -9),
            I(O.SET_TARGET, above("present"), above("center_price")),
        ],
        (lookback, 10.0, 0.02, 0.995),
        pad_to=16,
    ), catalog

"""Readable construction of the two encoded arrays; no opaque numeric literals."""

from .catalog import arte_catalog
from .core import Instruction as I
from .core import Operation as O
from .core import Parameter, Program, Unit


def momentum_example(gap_bps=2.0, min_rsi=50.0, cash_fraction=0.02):
    """A research policy downstream of Early Squeeze, not Candidate 328.

    Relative EMA gap and RSI admission are compiled, while the replay engine
    supplies account state and actual fill-dependent protection.
    """
    catalog = arte_catalog(
        (
            Parameter("ema_gap_bps", Unit.BPS, -100, 100),
            Parameter("rsi_threshold", Unit.RSI, 0, 100),
            Parameter("cash_fraction", Unit.FRACTION, 0.001, 1),
        )
    )
    labels = {feature.name: feature.label for feature in catalog.inputs}
    instructions = [
        I(O.GAP_BPS, labels["close@1000ms"], labels["ema_20@1000ms"]),  # result -1
        I(O.PARAMETER, parameter=0),  # result -2
        I(O.GREATER_EQUAL, -1, -2),  # result -3
        I(O.PARAMETER, parameter=1),  # result -4
        I(O.GREATER_EQUAL, labels["rsi_14@1000ms"], -4),  # result -5
        I(O.AND, -3, -5),  # result -6
        I(O.PARAMETER, parameter=2),  # result -7
        I(O.ENTER, -6, -7),  # terminal proposal
        I(O.LESS, labels["macd_line@1000ms"], labels["macd_signal@1000ms"]),
        I(O.EXIT, -9),
    ]
    return Program.encode(
        instructions, (gap_bps, min_rsi, cash_fraction), pad_to=16
    ), catalog

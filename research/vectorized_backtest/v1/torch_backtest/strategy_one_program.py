"""Strategy 1 scalar gates as atomic operation/input/threshold tensors.

The exact released formulas are lowered at setup, not called as opaque blocks
inside a searched strategy. IDs, clocks, quotes and financial registers each
have their own input class. Recurrent register updates remain engine-owned;
they are consequences of fills/acknowledgements, not invented market features.
"""

import ast
import inspect

from .atomic_graph import Input, Threshold, trace
from .strategy_one import add_admission, entry_admission

BOOLEAN_INPUTS = frozenset(
    {
        "candidate_valid",
        "pending_entry",
        "pending_exit",
        "pending_capital_request",
        "entry_permission",
        "protection_valid",
        "break_is_resistance",
        "break_new",
        "break_accepted",
        "bars_valid",
        "quote_valid",
    }
)


def _inputs(function):
    """Deterministic source parsing establishes the complete input contract.

    A missing input or unsupported mathematical operation fails at compilation.
    No source evaluation, inference about values, or market-row Python callback
    is involved. Sorted names provide reproducible class labels.
    """
    tree = ast.parse(inspect.getsource(function))
    names = sorted(
        {
            node.slice.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "x"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        }
    )

    def unit(name):
        if name in BOOLEAN_INPUTS:
            return "boolean"
        if name.endswith("_ms"):
            return "milliseconds"
        if name.endswith("_us"):
            return "microseconds"
        if name.endswith("_int"):
            return "integer_price"
        if name.endswith("_id"):
            return "identity"
        if name.endswith(("_ordinal", "_groups")) or name == "completed_entries":
            return "count"
        return "shares" if name == "quantity" else "price"

    return tuple(
        Input(
            index,
            name,
            "bool"
            if name in BOOLEAN_INPUTS
            else "integer"
            if name.endswith(("_ms", "_us", "_int", "_id", "_ordinal", "_groups"))
            or name == "completed_entries"
            else "real",
            unit(name),
        )
        for index, name in enumerate(names)
    )


def released_entry_graph():
    """Primitive instructions; no Strategy 1 opcode or bundled input.

    Episode lifetime and rapid-reentry duration can be searched. The released
    point is 300s/10s. Identity sentinels, timestamp alignment, integer price
    scale and session horizon are structural constants and remain fixed.
    """
    return trace(
        entry_admission,
        _inputs(entry_admission),
        bounds={
            (int, 300_000): Threshold(
                "episode_lifetime_ms", 100, 300_000, True, "milliseconds"
            ),
            (int, 10_000): Threshold(
                "rapid_reentry_ms", 0, 300_000, True, "milliseconds"
            ),
            (int, 1_000_000): Threshold(
                "maximum_quote_age_us", 0, 1_000_000, True, "microseconds"
            ),
            (float, 10000.0): Threshold(
                "integer_price_scale", 10000, 10000, False, "integer_price_per_price"
            ),
        },
    )


def released_add_graph():
    """80 primitive instructions over completed bars and confirmed registers."""
    return trace(
        add_admission,
        _inputs(add_admission),
        bounds={
            (int, 10000): Threshold(
                "integer_price_scale", 10000, 10000, True, "integer_price_per_price"
            )
        },
    )


def released_action_graphs():
    """Atomic numeric actions, separate from broker/Portfolio invariants.

    The release consumes certified initial stop/target facts and requests one
    third of available mandate cash. Research variants can replace these
    numeric expression arrays, while the engine retains cash/risk/no-short and
    valid-bracket constraints. They cannot write cash or invent a fill.
    """
    return {
        "capital_fraction": trace(
            lambda x: 1 / 3,
            (),
            gate=False,
            output_unit="ratio",
            bounds={
                (float, 1 / 3): Threshold(
                    "capital_mandate_fraction", 0.01, 1, False, "ratio"
                )
            },
        ),
        "initial_stop": trace(
            lambda x: x["stop"] * 1,
            (Input(0, "stop", "real", "price"),),
            gate=False,
            output_unit="price",
        ),
        "initial_target": trace(
            lambda x: x["target"] * 1,
            (Input(0, "target", "real", "price"),),
            gate=False,
            output_unit="price",
        ),
    }


def searchable_action_graphs():
    """Research brackets: scale distance to causal bid/ask, not price itself.

    Multiplier one returns the certified bracket exactly (zero adjustment).
    Engine bracket validation still rejects crossed/nonpositive prices.
    These graphs do not alter the faithful released action contracts.
    """
    graphs = released_action_graphs()
    graphs["initial_stop"] = trace(
        lambda x: x["stop"] + ((x["bid"] - x["stop"]) - (x["bid"] - x["stop"]) * 1.0),
        (Input(0, "stop", "real", "price"), Input(1, "bid", "real", "price")),
        gate=False,
        output_unit="price",
        bounds={
            (float, 1.0): Threshold(
                "initial_stop_distance_multiplier", 0.25, 2, False, "ratio"
            )
        },
    )
    graphs["initial_target"] = trace(
        lambda x: (
            x["target"] + ((x["target"] - x["ask"]) * 1.0 - (x["target"] - x["ask"]))
        ),
        (Input(0, "target", "real", "price"), Input(1, "ask", "real", "price")),
        gate=False,
        output_unit="price",
        bounds={
            (float, 1.0): Threshold(
                "initial_target_distance_multiplier", 0.25, 3, False, "ratio"
            )
        },
    )
    return graphs

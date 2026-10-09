"""Atomic, bounded entry expressions evaluated over [B,N] device lanes.

Each clause is [enabled, comparison_id, input_id, temporal_id, lookback,
threshold]. Connectors are AND/OR IDs. No timestamp/register can occupy the
threshold slot. This is a closed grammar, not arbitrary executable Python.
"""

from dataclasses import dataclass
from enum import IntEnum

import torch

from .semantics import Kind, compare_threshold

HISTORY = 12
CLAUSES = 4


class Compare(IntEnum):
    GREATER = 0
    GREATER_EQUAL = 1
    LESS = 2
    LESS_EQUAL = 3


class Temporal(IntEnum):
    CURRENT = 0
    LAG = 1
    MINIMUM = 2
    MAXIMUM = 3
    MEAN = 4


@dataclass(frozen=True)
class Atom:
    label: int
    name: str
    kind: Kind
    lower: float
    upper: float
    history: bool = False


ATOMS = (
    Atom(0, "close", Kind.PRICE, 0.01, 1000, True),
    Atom(1, "distance_above_vwap", Kind.RETURN, -0.5, 0.5, True),
    Atom(2, "spread_fraction", Kind.FRACTION, 0, 0.1, True),
    Atom(3, "interval_notional", Kind.MONEY, 0, 10_000_000, True),
    Atom(4, "interval_trades", Kind.COUNT, 0, 100_000, True),
    Atom(5, "episode_age_seconds", Kind.DURATION, 0, 57_600, True),
    Atom(6, "macd_1s_gap", Kind.PRICE_DELTA, -10, 10, True),
    Atom(7, "interval_volume", Kind.SHARES, 0, 10_000_000, True),
) + tuple(
    Atom(8 + i, f"resistance_{i + 1}_distance_return", Kind.RETURN, 0, 10, True)
    for i in range(5)
)


def validate_clause(row):
    if len(row) != 6 or any(int(row[i]) != row[i] for i in range(5)):
        raise ValueError("Rule class/count coordinates must be integer IDs")
    enabled, operation, label, temporal, window, threshold = row
    if (
        enabled not in (0, 1)
        or operation not in tuple(Compare)
        or not 0 <= label < len(ATOMS)
    ):
        raise ValueError("Unknown rule class ID")
    atom = ATOMS[int(label)]
    compare_threshold(atom.kind, atom.kind)
    if temporal not in tuple(Temporal) or not 1 <= window <= HISTORY:
        raise ValueError("Unknown temporal class or out-of-range completed lookback")
    if temporal != Temporal.CURRENT and not atom.history:
        raise ValueError("Input has no declared completed-history dependency")
    if not atom.lower <= threshold <= atom.upper:
        raise ValueError("Threshold exceeds this input's declared semantic range")


def evaluate(clauses, connectors, features, close_history):
    """[B,K,6], [B,K-1], [B,N,F], [N,H+1,F] -> [B,N].

    History[:,0,:] is the current completed atomic vector; later slots are
    previous completed decision boundaries. NaN gaps fail closed. LAG k excludes the current bar;
    reductions k include it. History capacity is fixed; lookback is searchable.
    """
    b, n, f = features.shape
    # A close-only history is supported for small witnesses; other inputs then
    # have no evidence. The runner supplies full [N,H+1,F] atomic history.
    if close_history.ndim == 2:
        close_history = torch.cat(
            (
                close_history[..., None],
                torch.full(
                    (*close_history.shape, f - 1),
                    float("nan"),
                    dtype=close_history.dtype,
                    device=close_history.device,
                ),
            ),
            -1,
        )
    result = torch.ones((b, n), dtype=torch.bool, device=features.device)
    valid = torch.ones_like(result)
    seen = torch.zeros_like(result)
    offsets = torch.arange(HISTORY + 1, device=features.device)[None, None]
    for index in range(CLAUSES):  # Static bounded instruction dispatch, not B/N.
        row = clauses[:, index]
        current = features.gather(
            -1, row[:, 2].to(torch.int64)[:, None, None].expand(b, n, 1)
        ).squeeze(-1)
        window = row[:, 4, None, None]
        history = (
            close_history[None]
            .expand(b, -1, -1, -1)
            .gather(
                -1,
                row[:, 2]
                .to(torch.int64)[:, None, None, None]
                .expand(b, n, HISTORY + 1, 1),
            )
            .squeeze(-1)
        )
        chosen = offsets < window
        lag = history.gather(-1, window.to(torch.int64).expand(b, n, 1)).squeeze(-1)
        complete = (~chosen | torch.isfinite(history)).all(-1)
        minimum = torch.where(chosen, history, float("inf")).amin(-1)
        maximum = torch.where(chosen, history, -float("inf")).amax(-1)
        mean = torch.where(chosen, history, 0).sum(-1) / window.squeeze(-1)
        temporal = row[:, 3, None]
        value = torch.where(
            temporal == Temporal.CURRENT,
            current,
            torch.where(
                temporal == Temporal.LAG,
                lag,
                torch.where(
                    temporal == Temporal.MINIMUM,
                    minimum,
                    torch.where(temporal == Temporal.MAXIMUM, maximum, mean),
                ),
            ),
        )
        known = torch.isfinite(value) & ((temporal <= Temporal.LAG) | complete)
        op, threshold = row[:, 1, None], row[:, 5, None]
        condition = torch.where(
            op == Compare.GREATER,
            value > threshold,
            torch.where(
                op == Compare.GREATER_EQUAL,
                value >= threshold,
                torch.where(op == Compare.LESS, value < threshold, value <= threshold),
            ),
        )
        enabled = row[:, 0, None] > 0
        # Unknown enabled clauses cannot be rescued by OR or NOT.
        valid &= ~enabled | known
        if index == 0:
            result = torch.where(enabled, condition, True)
        else:
            join = connectors[:, index - 1, None] > 0
            combined = torch.where(join, result | condition, result & condition)
            result = torch.where(
                enabled, torch.where(seen, combined, condition), result
            )
        seen |= enabled
    return result & valid

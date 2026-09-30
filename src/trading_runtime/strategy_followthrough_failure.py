"""Pure completed-five-second failure rule for the next research release.

This consumes producer facts and prior held-position authority. It never
calculates MACD, submits an order, or infers an intrabucket fill sequence.
Registration and normalized source persistence are separate launch gates.
"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class FollowThroughFailureInput:
    boundary_ms: int
    first_held_boundary_ms: int
    reference_ask: float
    initial_stop: float
    completed_five_second_boundary_ms: int | None
    completed_five_second_close_int: int | None
    price_valid: bool
    macd_line: float | None
    macd_signal: float | None
    bid: float | None
    ask: float | None
    quote_age_us: int | None
    position_quantity: float
    pending_exit: bool


@dataclass(frozen=True, slots=True)
class FollowThroughFailure:
    """Scalar witness requiring normalized persistence before an exit command."""
    boundary_ms: int
    first_held_boundary_ms: int
    reference_ask: float
    initial_stop: float
    completed_close_int: int
    macd_line: float
    macd_signal: float
    bid: float
    ask: float
    quote_age_us: int


def followthrough_failure(value: FollowThroughFailureInput) -> FollowThroughFailure | None:
    """Half original stop distance plus negative completed 5s MACD.

    Only a complete five-second bucket wholly after the first held bucket
    qualifies. A fresh quote must still be below the same threshold when the
    exit is proposed. Missing inputs consume no synthetic observation.
    """
    if (type(value) is not FollowThroughFailureInput
            or type(value.boundary_ms) is not int
            or not 0 < value.boundary_ms <= 57_600_000
            or value.boundary_ms % 100
            or type(value.first_held_boundary_ms) is not int
            or not 0 < value.first_held_boundary_ms <= value.boundary_ms
            or value.first_held_boundary_ms % 100
            or any(type(x) not in (int, float) or not isfinite(x)
                   for x in (value.reference_ask, value.initial_stop, value.position_quantity))
            or not 0 < value.initial_stop < value.reference_ask
            or value.position_quantity < 0
            or type(value.price_valid) is not bool
            or type(value.pending_exit) is not bool):
        raise ValueError('Follow-through rule needs exact causal position authority')
    if value.position_quantity == 0 or value.pending_exit or value.boundary_ms % 5000:
        return None
    if (value.completed_five_second_boundary_ms != value.boundary_ms
            or value.boundary_ms - 5000 < value.first_held_boundary_ms
            or not value.price_valid
            or type(value.completed_five_second_close_int) is not int
            or value.completed_five_second_close_int <= 0
            or any(type(x) not in (int, float) or not isfinite(x)
                   for x in (value.macd_line, value.macd_signal, value.bid, value.ask))
            or not 0 < value.bid <= value.ask
            or type(value.quote_age_us) is not int
            or not 0 <= value.quote_age_us <= 1_000_000):
        return None
    threshold = (value.reference_ask + value.initial_stop) / 2
    if (value.completed_five_second_close_int > threshold * 10_000
            or value.bid > threshold
            or value.macd_line >= value.macd_signal):
        return None
    return FollowThroughFailure(value.boundary_ms, value.first_held_boundary_ms,
        value.reference_ask, value.initial_stop, value.completed_five_second_close_int,
        float(value.macd_line), float(value.macd_signal), float(value.bid), float(value.ask),
        value.quote_age_us)

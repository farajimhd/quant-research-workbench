"""Completed-100ms late-HOD transition for unpublished Strategy 1.

STRATEGY CREATION RULES: a producer may evaluate this over pinned arte bars
and as-of V7 levels once and publish normalized candidate context. Backtest
only SELECTs the certified result. No event order inside a bucket is inferred.
Changing this trading rule after publication requires a new strategy number.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from .early_squeeze_price import eligible


@dataclass(frozen=True, slots=True)
class HodResistance:
    unified_level_id: str
    lower: float
    upper: float

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2


@dataclass(frozen=True, slots=True)
class HodObservation:
    boundary_ms: int = 0
    session_open_int: int = 0
    session_high_int: int = 0
    prior_hod_int: int = 0
    last_close_int: int = 0
    last_price_boundary_ms: int = 0
    reference: HodResistance | None = None
    gate: HodResistance | None = None
    late_mode: bool = False


def observe_completed_hod(
    state: HodObservation, bar: Mapping, *,
    admitted_levels: Sequence[Mapping],
) -> HodObservation:
    """Advance only after a completed bucket; preserve prior-HOD causality.

    A crossing needs two contiguous price-bearing buckets and unchanged
    reference geometry. A quote-only or missing bucket cannot manufacture a
    new break. The gate persists until its level changes or price falls back
    to its midpoint, as in the historical rule.
    """
    if (not isinstance(state, HodObservation) or not isinstance(bar, Mapping)
            or not isinstance(admitted_levels, (tuple, list))):
        raise ValueError("Strategy 1 HOD requires typed completed inputs")
    boundary = bar.get("boundary_ms")
    if (type(boundary) is not int or boundary <= state.boundary_ms
            or boundary > 57_600_000 or boundary % 100
            or bar.get("resolution_ms") != 100):
        raise ValueError("Strategy 1 HOD boundary is not causal 100ms")
    levels: list[HodResistance] = []
    ids = set()
    for row in admitted_levels:
        if not isinstance(row, Mapping):
            raise ValueError("Strategy 1 HOD level is malformed")
        identity, lower, upper = (row.get("unified_level_id"),
                                  row.get("lower"), row.get("upper"))
        if (not isinstance(identity, str) or not identity or identity in ids
                or type(lower) not in (int, float)
                or type(upper) not in (int, float)
                or not isfinite(lower) or not isfinite(upper)
                or not 0 < lower <= upper):
            raise ValueError("Strategy 1 HOD level geometry is invalid")
        ids.add(identity)
        if eligible(row):
            levels.append(HodResistance(identity, float(lower), float(upper)))
    if bar.get("price_valid") != 1:
        if bar.get("price_valid") != 0:
            raise ValueError("Strategy 1 HOD price validity is malformed")
        retained_gate = state.gate if state.gate in levels else None
        return HodObservation(
            boundary, state.session_open_int, state.session_high_int,
            state.prior_hod_int, state.last_close_int,
            state.last_price_boundary_ms, state.reference, retained_gate,
            state.late_mode)
    opened, high, low, closed = (bar.get("open_int"), bar.get("high_int"),
                                 bar.get("low_int"), bar.get("close_int"))
    if (bar.get("extremes_valid") != 1
            or any(type(value) is not int or value <= 0
            for value in (opened, high, low, closed))
            or not low <= min(opened, closed) <= max(opened, closed) <= high):
        raise ValueError("Strategy 1 HOD price bar is invalid")
    session_open = state.session_open_int or opened
    prior_hod = state.session_high_int or session_open
    reference = max(
        (level for level in levels if level.midpoint * 10_000 < prior_hod),
        key=lambda level: (level.midpoint, level.unified_level_id),
        default=None)
    gate = state.gate
    if gate is not None and (gate not in levels
                             or closed <= gate.midpoint * 10_000):
        gate = None
    previous = state.reference
    contiguous = state.last_price_boundary_ms == boundary - 100
    if (contiguous and previous is not None and previous == reference
            and state.last_close_int <= previous.midpoint * 10_000
            and opened <= previous.midpoint * 10_000
            < closed):
        gate = previous
    return HodObservation(
        boundary, session_open, max(prior_hod, high), prior_hod,
        closed, boundary, reference, gate,
        state.late_mode or high >= 1.15 * session_open)


def late_hod_admitted(state: HodObservation, *, price_int: int,
                      boundary_ms: int) -> bool:
    """Apply Candidate 350's late-mode zone and below-HOD gate, causally."""
    if (not isinstance(state, HodObservation) or type(price_int) is not int
            or price_int <= 0 or type(boundary_ms) is not int
            or boundary_ms != state.boundary_ms):
        raise ValueError("Strategy 1 HOD candidate clock is invalid")
    if not state.late_mode:
        return True
    return bool(state.gate is not None and state.prior_hod_int > 0
                and 0.7 * state.prior_hod_int <= price_int
                < state.prior_hod_int)

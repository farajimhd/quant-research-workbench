"""Causal completed-bar resistance acceptance for draft Strategy 1.

STRATEGY CREATION RULES: this transition consumes only certified completed
one-second bars and pre-admitted V7 geometry. It never creates a trade event,
queries market data, submits an order, or changes a published Strategy number.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from .strategy_one_position import ResistanceBreak


@dataclass(frozen=True, slots=True)
class KnownResistance:
    unified_level_id: str
    lower: float
    upper: float

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2


@dataclass(frozen=True, slots=True)
class ResistanceObservation:
    boundary_ms: int = 0
    close_int: int = 0
    known: tuple[KnownResistance, ...] = ()
    accepted_ids: frozenset[str] = frozenset()


def _known_levels(rows: Sequence[Mapping], *,
                  retain_ids: frozenset[str]) -> tuple[KnownResistance, ...]:
    found = []
    identities = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Strategy 1 V7 resistance row is malformed")
        identity = row.get("unified_level_id")
        lower, upper = row.get("lower"), row.get("upper")
        if (not isinstance(identity, str) or not identity
                or identity in identities
                or type(lower) not in (int, float)
                or type(upper) not in (int, float)
                or not isfinite(lower) or not isfinite(upper)
                or not 0 < lower <= upper):
            raise ValueError("Strategy 1 V7 resistance geometry is invalid")
        identities.add(identity)
        if (identity in retain_ids or row.get("role") == "resistance"
                or not row.get("role") and row.get("side") in (-1, "resistance")
                or row.get("role") == "transition"
                and row.get("transition_from") == "resistance"):
            found.append(KnownResistance(identity, float(lower), float(upper)))
    return tuple(sorted(found, key=lambda level: (level.midpoint,
                                                  level.unified_level_id)))


def observe_completed_resistance_second(
    state: ResistanceObservation, bar: Mapping, *,
    admitted_levels: Sequence[Mapping],
) -> tuple[ResistanceObservation, tuple[ResistanceBreak, ...]]:
    """Accept a prior-known unchanged level on a contiguous green 1s close.

    An empty or missing second cannot later manufacture a break. A new or
    moved level first becomes eligible after this completed observation.
    """
    if not isinstance(state, ResistanceObservation) or not isinstance(bar, Mapping):
        raise ValueError("Strategy 1 resistance transition needs typed state and bar")
    boundary = bar.get("boundary_ms")
    opened, closed = bar.get("open_int"), bar.get("close_int")
    if (type(boundary) is not int or boundary <= state.boundary_ms
            or boundary % 1_000 or bar.get("resolution_ms") != 1_000
            or type(opened) is not int or type(closed) is not int
            or opened <= 0 or closed <= 0
            or bar.get("price_valid") != 1
            or not isinstance(admitted_levels, (tuple, list))):
        raise ValueError("Strategy 1 resistance requires a completed 1s price bar")
    current = _known_levels(
        admitted_levels,
        retain_ids=frozenset(level.unified_level_id for level in state.known))
    by_id = {level.unified_level_id: level for level in current}
    accepted = set(state.accepted_ids)
    # As in the historical acceptance contract, a close at or below a band
    # midpoint revokes its acceptance; a later green crossing may reaccept it.
    for previous in state.known:
        if closed <= previous.midpoint * 10_000:
            accepted.discard(previous.unified_level_id)
    breaks = []
    contiguous = boundary == state.boundary_ms + 1_000
    if not contiguous or not current:
        # An authority gap ends this detector's acceptance episode. Position
        # history remains separately owned by the protection reducer.
        accepted.clear()
    if contiguous and closed > opened:
        for previous in state.known:
            present = by_id.get(previous.unified_level_id)
            if (present != previous
                    or previous.unified_level_id in accepted):
                continue
            threshold = previous.midpoint * 10_000
            if (state.close_int <= threshold or opened <= threshold) \
                    and threshold < closed:
                accepted.add(previous.unified_level_id)
                breaks.append(ResistanceBreak(boundary, {
                    "unified_level_id": previous.unified_level_id,
                    "lower": previous.lower, "upper": previous.upper,
                    "role": "resistance", "side": "resistance",
                }))
    return (ResistanceObservation(boundary, closed, current,
                                  frozenset(accepted)), tuple(breaks))

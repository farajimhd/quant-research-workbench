"""Completed-bar break-of-structure transition for unpublished Strategy 1.

STRATEGY CREATION RULES: a new behavior requires a new strategy number. This
consumer never derives pivots, bars, indicators, or V7 levels; its inputs must
come from certified, producer-owned ARTE products. It never invents the order
of trades inside a completed bucket or submits an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ConfirmedPivot:
    pivot_id: str
    side: str
    price_int: int
    pivot_boundary_ms: int
    confirmed_boundary_ms: int


@dataclass(frozen=True, slots=True)
class BosObservation:
    boundary_ms: int = 0
    close_int: int = 0
    reference: ConfirmedPivot | None = None


@dataclass(frozen=True, slots=True)
class BosBreak:
    boundary_ms: int
    broken_pivot: ConfirmedPivot
    close_int: int


def observe_completed_bos(
    state: BosObservation, bar: Mapping, *,
    confirmed_pivots: Sequence[ConfirmedPivot],
) -> tuple[BosObservation, BosBreak | None]:
    """Break only a previously visible high on a contiguous completed close.

    Newly confirmed highs become the next reference, but cannot create a
    retroactive crossing on the same boundary. A missing price-bearing second
    breaks close-to-close continuity; the next second only re-arms the gate.
    """
    if not isinstance(state, BosObservation) or not isinstance(bar, Mapping):
        raise ValueError("Strategy 1 BOS needs typed state and completed bar")
    boundary, close = bar.get("boundary_ms"), bar.get("close_int")
    if (type(boundary) is not int or boundary <= state.boundary_ms
            or boundary % 1_000 or bar.get("resolution_ms") != 1_000
            or bar.get("price_valid") != 1 or type(close) is not int
            or close <= 0 or not isinstance(confirmed_pivots, (tuple, list))):
        raise ValueError("Strategy 1 BOS requires a completed 1s price bar")
    seen = set()
    for pivot in confirmed_pivots:
        if (not isinstance(pivot, ConfirmedPivot) or not pivot.pivot_id
                or pivot.pivot_id in seen or pivot.side not in ("high", "low")
                or type(pivot.price_int) is not int or pivot.price_int <= 0
                or type(pivot.pivot_boundary_ms) is not int
                or type(pivot.confirmed_boundary_ms) is not int
                or not 0 < pivot.pivot_boundary_ms < pivot.confirmed_boundary_ms
                or pivot.confirmed_boundary_ms > boundary):
            raise ValueError("Strategy 1 BOS pivot evidence is invalid or future")
        seen.add(pivot.pivot_id)
    prior = state.reference
    contiguous = boundary == state.boundary_ms + 1_000
    broken = (BosBreak(boundary, prior, close)
              if contiguous and prior is not None
              and prior.confirmed_boundary_ms < boundary
              and state.close_int <= prior.price_int < close else None)
    highs = [pivot for pivot in confirmed_pivots if pivot.side == "high"]
    latest = max(highs, key=lambda pivot: (
        pivot.pivot_boundary_ms, pivot.confirmed_boundary_ms, pivot.pivot_id
    ), default=None)
    reference = (latest if latest is not None and (
        prior is None or latest.pivot_boundary_ms > prior.pivot_boundary_ms
    ) else prior)
    return BosObservation(boundary, close, reference), broken

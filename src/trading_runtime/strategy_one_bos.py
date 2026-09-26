"""Completed-bar break-of-structure transition for unpublished Strategy 1.

STRATEGY CREATION RULES: a new behavior requires a new strategy number. This
consumer never derives pivots, bars, indicators, or V7 levels; its inputs must
come from certified, producer-owned ARTE products. It never invents the order
of trades inside a completed bucket or submits an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
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
    open_break: BosBreak | None = None


@dataclass(frozen=True, slots=True)
class BosBreak:
    boundary_ms: int
    broken_pivot: ConfirmedPivot
    close_int: int


@dataclass(frozen=True, slots=True)
class BosSupport:
    kind: str
    level_id: str
    pivot_id: str | None = None


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
    return BosObservation(boundary, close, reference,
                          broken or state.open_break), broken


def supported_completed_bos(
    broken: BosBreak | None, *,
    candidate_boundary_ms: int,
    visible_pivots: Sequence[ConfirmedPivot],
    admitted_levels: Sequence[Mapping],
) -> BosSupport | None:
    """Apply Candidate 350's supported/reclaimed-base gate at this boundary.

    Evidence is as-of the candidate, not retroactively attached to the BOS
    candle. A low must precede the broken high; a support band must contain
    that low. If none exists, a reclaimed resistance below the high may
    substitute, matching the historical gate without inventing a pivot.
    """
    if (type(candidate_boundary_ms) is not int
            or candidate_boundary_ms <= 0 or candidate_boundary_ms % 100):
        raise ValueError("Strategy 1 BOS support needs a completed candidate boundary")
    if broken is None:
        return None
    if (not isinstance(broken, BosBreak)
            or not isinstance(broken.broken_pivot, ConfirmedPivot)
            or broken.boundary_ms > candidate_boundary_ms
            or broken.broken_pivot.confirmed_boundary_ms >= broken.boundary_ms
            or not isinstance(visible_pivots, (tuple, list))
            or not isinstance(admitted_levels, (tuple, list))):
        raise ValueError("Strategy 1 supported BOS needs typed causal evidence")
    if any(not isinstance(pivot, ConfirmedPivot)
           or pivot.confirmed_boundary_ms > candidate_boundary_ms
           for pivot in visible_pivots):
        raise ValueError("Strategy 1 supported BOS pivot is malformed")
    levels = []
    identities = set()
    for row in admitted_levels:
        if not isinstance(row, Mapping):
            raise ValueError("Strategy 1 supported BOS level is malformed")
        identity = row.get("unified_level_id")
        lower, upper = row.get("lower"), row.get("upper")
        if (not isinstance(identity, str) or not identity
                or identity in identities
                or type(lower) not in (int, float)
                or type(upper) not in (int, float)
                or not isfinite(lower) or not isfinite(upper)
                or not 0 < lower <= upper):
            raise ValueError("Strategy 1 supported BOS level geometry is invalid")
        identities.add(identity)
        levels.append(row)
    lows = sorted((pivot for pivot in visible_pivots
                   if pivot.side == "low"
                   and pivot.pivot_boundary_ms <
                   broken.broken_pivot.pivot_boundary_ms),
                  key=lambda pivot: (pivot.pivot_boundary_ms,
                                     pivot.confirmed_boundary_ms,
                                     pivot.pivot_id), reverse=True)
    for pivot in lows:
        price = pivot.price_int / 10_000
        supports = [row for row in levels if row["lower"] <= price <= row["upper"]
                    and (row.get("role") == "support"
                         or row.get("side") in (1, "support"))]
        if supports:
            selected = min(supports, key=lambda row: (
                row["upper"] - row["lower"], row["unified_level_id"]))
            return BosSupport("support", selected["unified_level_id"],
                              pivot.pivot_id)
    # A qualifying resistance reclaimed below the broken high is the legacy
    # fallback; never infer support from an arbitrary nearby V7 band.
    from .early_squeeze_price import eligible, midpoint
    reclaimed = [row for row in levels if eligible(row)
                 and midpoint(row) < broken.broken_pivot.price_int / 10_000]
    if reclaimed:
        selected = max(reclaimed, key=lambda row: (
            midpoint(row), row["unified_level_id"]))
        return BosSupport("reclaimed_resistance", selected["unified_level_id"])
    return None

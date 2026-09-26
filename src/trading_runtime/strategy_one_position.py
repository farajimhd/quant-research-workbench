"""Position-owned Strategy 1 protection over completed ARTE/V7 inputs.

STRATEGY CREATION RULES: this reducer is specific to unpublished Strategy 1.
Changing its clocks, stop/target precedence, or entry validation after
publication requires a new Strategy number. It never builds market products,
reads events, submits orders, journals, or mutates shared portfolio state.
Only a causal coordinator may apply its returned amendments to OMS.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Mapping, Sequence

from . import early_squeeze_price as price_rules
from .strategy_one_contract import (
    completed_30s_low_stop, ordinal_target, resistance_group_stop,
    upward_stop_update,
)


@dataclass(frozen=True, slots=True)
class ResistanceBreak:
    completed_boundary_ms: int
    level: Mapping


@dataclass(frozen=True, slots=True)
class AcceptedResistance:
    unified_level_id: str
    lower: float
    upper: float

    def row(self) -> dict:
        return {"unified_level_id": self.unified_level_id,
                "lower": self.lower, "upper": self.upper,
                "side": "resistance", "role": "resistance"}


@dataclass(frozen=True, slots=True)
class ProtectionState:
    boundary_ms: int
    stop: float
    target: float
    accepted_ids: frozenset[str] = frozenset()
    pending_group: tuple[AcceptedResistance, ...] = ()
    earned_group: tuple[AcceptedResistance, ...] = ()
    earned_groups: int = 0
    applied_groups: int = 0


@dataclass(frozen=True, slots=True)
class ProtectionTransition:
    state: ProtectionState
    stop_amendment: Mapping | None = None
    target_amendment: Mapping | None = None


def ordered_protection_amendments(
    transition: ProtectionTransition,
) -> tuple[tuple[str, Mapping], ...]:
    """Submit an expanding target before a stop that may need its new room."""
    if not isinstance(transition, ProtectionTransition):
        raise TypeError("Strategy 1 protection needs a typed transition")
    result = []
    if transition.target_amendment is not None:
        result.append(("replace_profit_target", transition.target_amendment))
    if transition.stop_amendment is not None:
        result.append(("replace_protective_stop", transition.stop_amendment))
    return tuple(result)


def confirm_protection_transition(
    previous: ProtectionState, transition: ProtectionTransition, *,
    target_confirmed: bool, stop_confirmed: bool,
) -> ProtectionState:
    """Commit only broker-acknowledged prices; retain causal break history.

    OMS acknowledgement (or exact broker reconciliation) is the authority for
    a replacement price. A refused stop leaves its earned resistance group
    unapplied so a later completed boundary may retry it. A refused target
    cannot license a stop above the still-working target.
    """
    if (not isinstance(previous, ProtectionState)
            or not isinstance(transition, ProtectionTransition)
            or type(target_confirmed) is not bool or type(stop_confirmed) is not bool
            or transition.state.boundary_ms <= previous.boundary_ms
            or target_confirmed and transition.target_amendment is None
            or stop_confirmed and transition.stop_amendment is None
            or (transition.target_amendment is None
                and transition.state.target != previous.target)
            or (transition.stop_amendment is None
                and transition.state.stop != previous.stop)
            or (transition.target_amendment is not None
                and transition.target_amendment.get("price") != transition.state.target)
            or (transition.stop_amendment is not None
                and transition.stop_amendment.get("price") != transition.state.stop)
            or not previous.accepted_ids <= transition.state.accepted_ids
            or transition.state.earned_groups < previous.earned_groups):
        raise ValueError("Strategy 1 protection confirmation lacks its proposal")
    state = transition.state
    stop = state.stop if stop_confirmed else previous.stop
    target = state.target if target_confirmed else previous.target
    if not 0 < stop < target:
        raise RuntimeError("Strategy 1 confirmed stop would cross working target")
    return replace(state, stop=stop, target=target,
                   applied_groups=(state.applied_groups if stop_confirmed
                                   else previous.applied_groups))


def _quote(*, bid: float, ask: float, tick: float) -> None:
    if not all(type(value) in (int, float) and isfinite(value)
               for value in (bid, ask, tick)) or not 0 < bid <= ask or tick <= 0:
        raise ValueError("Strategy 1 protection needs a valid completed quote")


def _low(*, boundary_ms: int | None, low_int: int | None, price_valid: bool,
         extremes_valid: bool, now_ms: int, tick: float) -> Mapping | None:
    if boundary_ms is None or low_int is None:
        if (boundary_ms is not None or low_int is not None
                or price_valid or extremes_valid):
            raise ValueError("Strategy 1 missing 30s bar must be explicit")
        return None
    return completed_30s_low_stop(
        low_int=low_int, boundary_ms=boundary_ms, now_ms=now_ms, tick=tick,
        price_valid=price_valid, extremes_valid=extremes_valid)


def open_protection(*, now_ms: int, bid: float, ask: float, tick: float,
                    low_boundary_ms: int, low_int: int,
                    low_price_valid: bool, low_extremes_valid: bool,
                    overhead_levels: Sequence[Mapping]) -> ProtectionTransition | None:
    """Admit no position without a completed stop and third overhead target."""
    _quote(bid=bid, ask=ask, tick=tick)
    if type(now_ms) is not int or now_ms <= 0 or now_ms % 100:
        raise ValueError("Strategy 1 entry needs a completed 100ms boundary")
    stop = _low(boundary_ms=low_boundary_ms, low_int=low_int,
                price_valid=low_price_valid, extremes_valid=low_extremes_valid,
                now_ms=now_ms, tick=tick)
    target = ordinal_target(rows=overhead_levels, ask=ask, tick=tick,
                            broken_count=0)
    if stop is None or target is None or not 0 < stop["price"] < bid <= ask < target["price"]:
        return None
    state = ProtectionState(now_ms, stop["price"], target["price"])
    return ProtectionTransition(state, stop, target)


def advance_protection(state: ProtectionState, *, now_ms: int,
                       bid: float, ask: float, tick: float,
                       low_boundary_ms: int | None, low_int: int | None,
                       low_price_valid: bool, low_extremes_valid: bool,
                       breaks: Sequence[ResistanceBreak],
                       overhead_levels: Sequence[Mapping],
                       price_bearing_bar: bool) -> ProtectionTransition:
    """Ratchet a filled position without looking beyond its completed clock.

    Distinct accepted resistances belong to this position, not the ticker's
    lifetime. On a simultaneous 30s-low and three-break proposal, resistance
    wins if it can raise the stop below the executable bid. Target reranking
    occurs on a completed price-bearing evaluation bar, as in the historical
    ordinal rule; a quote-only boundary cannot create a price crossing.
    """
    _quote(bid=bid, ask=ask, tick=tick)
    if (not isinstance(state, ProtectionState) or type(now_ms) is not int
            or now_ms <= state.boundary_ms or now_ms % 100
            or not 0 < state.stop < state.target
            or not isinstance(state.accepted_ids, frozenset)
            or not isinstance(state.pending_group, tuple)
            or not isinstance(state.earned_group, tuple)
            or len(state.pending_group) >= 3
            or len(state.earned_group) not in (0, 3)
            or type(state.earned_groups) is not int
            or type(state.applied_groups) is not int
            or not 0 <= state.applied_groups <= state.earned_groups
            or len(state.accepted_ids) != 3 * state.earned_groups + len(state.pending_group)):
        raise ValueError("Strategy 1 protection state or boundary is invalid")
    seen = state.accepted_ids
    pending_group = state.pending_group
    earned_group = state.earned_group
    earned_groups = state.earned_groups
    ordered_breaks = []
    break_geometry: dict[tuple[int, str], tuple[float, float]] = {}
    for event in breaks:
        if not isinstance(event, ResistanceBreak) or not isinstance(event.level, Mapping):
            raise ValueError("Strategy 1 resistance break is malformed")
        at = event.completed_boundary_ms
        if (type(at) is not int or at <= state.boundary_ms or at > now_ms
                or at % 1_000):
            raise ValueError("Strategy 1 resistance break is not a new completed 1s event")
        row = event.level
        identity = str(row.get("unified_level_id") or "")
        if (not identity or not all(key in row for key in ("lower", "upper"))
                or not all(type(row[key]) in (int, float) and isfinite(row[key])
                           for key in ("lower", "upper"))
                or not 0 < row["lower"] <= row["upper"]
                or not price_rules.eligible(row)):
            raise ValueError("Strategy 1 resistance break lacks pinned geometry")
        key = (at, identity)
        geometry = (float(row["lower"]), float(row["upper"]))
        prior_geometry = break_geometry.setdefault(key, geometry)
        if prior_geometry != geometry:
            raise ValueError("Strategy 1 resistance break has conflicting geometry")
        ordered_breaks.append((at, price_rules.midpoint(row), identity, row))
    for _, _, identity, row in sorted(ordered_breaks, key=lambda item: item[:3]):
        if identity not in seen:
            # Only new breaks allocate state. A completed triple, rather than
            # the full historical level catalogue, owns the next stop step.
            seen = seen | {identity}
            pending_group += (AcceptedResistance(
                identity, float(row["lower"]), float(row["upper"])),)
            if len(pending_group) == 3:
                earned_group = pending_group
                pending_group = ()
                earned_groups += 1
    swing = _low(boundary_ms=low_boundary_ms, low_int=low_int,
                 price_valid=low_price_valid, extremes_valid=low_extremes_valid,
                 now_ms=now_ms, tick=tick)
    resistance = None
    if earned_groups > state.applied_groups:
        proposal = resistance_group_stop(
            accepted_levels=[row.row() for row in earned_group],
            applied_groups=0, tick=tick)
        if proposal is None:
            raise RuntimeError("Strategy 1 earned resistance group is incomplete")
        resistance = {**proposal, "groups": earned_groups}
    target_amendment = (ordinal_target(
        rows=overhead_levels, ask=ask, tick=tick,
        broken_count=len(seen), previous_target=state.target)
        if price_bearing_bar else None)
    effective_target = (target_amendment["price"] if target_amendment
                        else state.target)
    # A still-working target may be above or below the latest bid after a
    # sparse jump. Never submit a crossed stop/target bracket while OMS is
    # resolving that target's fill or an explicit liquidation.
    stop_amendment = upward_stop_update(
        current=state.stop, executable_bid=min(bid, effective_target),
        swing=swing, resistance=resistance)
    updated = replace(
        state, boundary_ms=now_ms, accepted_ids=seen,
        pending_group=pending_group, earned_group=earned_group,
        earned_groups=earned_groups,
        stop=stop_amendment["price"] if stop_amendment else state.stop,
        target=effective_target,
        applied_groups=(int(stop_amendment["groups"])
                        if stop_amendment and stop_amendment["source"] ==
                        "three_resistance_step_stop" else state.applied_groups),
    )
    return ProtectionTransition(updated, stop_amendment, target_amendment)

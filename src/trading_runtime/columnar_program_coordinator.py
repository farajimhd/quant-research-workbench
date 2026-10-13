"""Shared sequential selection of market-only program results.

The input masks can be prepared concurrently. Actual filled positions and OMS
protection/pending state are supplied at the completed boundary. This selector
emits semantic proposals only: it never assumes fills, updates positions, reads
data, creates tables or bypasses Portfolio/OMS admission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Mapping
from types import MappingProxyType
from uuid import UUID, uuid5

from .columnar_strategy_release import ProgramRelease
from .filled_position_state import FilledPosition
from .execution_policies import ExecutionPolicy, ProtectionProfile, StopRuleType, TrailingRuleType
from .signals import CapitalRequest, StrategyIntent


@dataclass(frozen=True)
class ProgramBoundary:
    boundary_at: datetime
    available_at: datetime
    listing_id: str
    ticker: str
    close: float
    eligible: bool
    rules: Mapping[str, bool]
    validity: Mapping[str, bool]
    initial_stop: float | None

    def __post_init__(self):
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))
        object.__setattr__(self, "validity", MappingProxyType(dict(self.validity)))


@dataclass(frozen=True)
class ManagementState:
    """Completed OMS/manager projection, never reconstructed from signals."""
    observed_at: datetime
    pending_acquisition: bool
    pending_reduction: bool
    stop_price: float
    target_price: float
    high_water_price: float


@dataclass(frozen=True)
class ProgramProposal:
    intent_id: str
    stage: str
    action: str
    boundary_at: datetime
    ticker: str
    quantity: float
    reference_price: float
    requested_cash: float | None
    stop_price: float | None
    reason: str

    def to_intent(self, *, execution_policy: ExecutionPolicy,
                  protection_profile: ProtectionProfile | None,
                  outside_rth: bool) -> StrategyIntent:
        if not isinstance(execution_policy, ExecutionPolicy) or type(outside_rth) is not bool:
            raise ValueError("Proposal needs the resolved app execution contract")
        if self.action in {"enter_long", "add_long"} and not isinstance(protection_profile, ProtectionProfile):
            raise ValueError("Acquisition needs an explicit app protection profile")
        if self.action in {"enter_long", "add_long"} and any(
                item.stop.rule_type != StopRuleType.FIXED_PRICE or item.stop.price != self.stop_price
                or item.profit_target_price is not None or item.trailing.rule_type != TrailingRuleType.NONE
                for item in protection_profile.slices):
            raise ValueError("Program protection must preserve its fixed stop-only policy output")
        return StrategyIntent(intent_id=self.intent_id, ticker=self.ticker,
            event_time=self.boundary_at, action=self.action, quantity=self.quantity,
            reference_price=self.reference_price,
            capital_request=(CapitalRequest("fixed_notional", self.requested_cash)
                             if self.requested_cash is not None else None),
            invalidation_price=self.stop_price, execution_policy=execution_policy,
            protection_profile=protection_profile, outside_rth=outside_rth,
            reason=self.reason)


def validate_program_proposal(release: ProgramRelease, proposal: ProgramProposal) -> None:
    """Validate the semantic stage before journal, capital or OMS mutation."""
    actions = {"entry": {"enter_long"}, "add": {"add_long"},
               "reduce": {"reduce_long"}, "trail": {"replace_protective_stop"},
               "exit": {"exit", "cancel_entry"}}
    if (type(proposal) is not ProgramProposal
            or proposal.stage not in release.programs
            or proposal.action not in actions.get(proposal.stage, ())
            or not proposal.ticker or not proposal.reason
            or not isfinite(proposal.reference_price) or proposal.reference_price <= 0
            or not isfinite(proposal.quantity) or proposal.quantity < 0):
        raise ValueError("Program proposal has invalid stage or execution values")
    UUID(proposal.intent_id)
    acquisition = proposal.action in {"enter_long", "add_long"}
    if acquisition:
        cash_key = "entry_cash" if proposal.stage == "entry" else "add_cash"
        if (proposal.requested_cash is None or not isfinite(proposal.requested_cash)
                or Decimal(str(proposal.requested_cash)) != release.header[cash_key]
                or proposal.quantity != 0):
            raise ValueError("Program acquisition differs from immutable cash parameters")
    elif proposal.requested_cash is not None:
        raise ValueError("Program management cannot request acquisition cash")
    if proposal.action in {"reduce_long", "exit"} and proposal.quantity <= 0:
        raise ValueError("Program reduction requires actual positive held quantity")
    if acquisition or proposal.action == "replace_protective_stop":
        if (proposal.stop_price is None or not isfinite(proposal.stop_price)
                or proposal.stop_price <= 0
                or acquisition and proposal.stop_price >= proposal.reference_price):
            raise ValueError("Program proposal lacks valid protection")


def select_program_proposal(release: ProgramRelease, boundary: ProgramBoundary, *,
                            position: FilledPosition, management: ManagementState,
                            terminal: bool = False) -> tuple[ProgramProposal, ...]:
    """Select entry/open branches using actual state, age and fixed precedence.

    Policy state comes from the shared OMS/position manager. Target and stop
    prices are policy inputs, not features accessible to the vectorized DAG.
    A stop replacement precedes a simultaneous add/reduction. Execution and
    cash mutation remain in the app's existing global coordinator.
    """
    at = boundary.boundary_at
    for clock in (at, boundary.available_at, management.observed_at):
        if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Program coordinator needs timezone-aware completed clocks")
    if boundary.available_at > at or management.observed_at > at:
        raise ValueError("Program coordinator cannot consume future inputs or OMS state")
    if (position.listing_id != boundary.listing_id or position.ticker != boundary.ticker
            or position.strategy_id != release.header["strategy_id"]):
        raise ValueError("Program position attribution differs from release/input")
    if (set(boundary.rules) != set(release.programs) or set(boundary.validity) != set(release.programs)
            or any(type(v) is not bool for v in (*boundary.rules.values(), *boundary.validity.values()))
            or type(boundary.eligible) is not bool or type(terminal) is not bool
            or type(management.pending_acquisition) is not bool
            or type(management.pending_reduction) is not bool):
        raise ValueError("Program coordinator needs complete typed stage masks")
    if not isfinite(boundary.close) or boundary.close <= 0:
        raise ValueError("Program coordinator needs a completed price-bearing mark")
    age = position.age_seconds(at)
    def enabled(stage):
        return (boundary.validity[stage] and boundary.rules[stage]
                and (not position.position_open or age >= release.minimum_age_seconds[stage]))
    def proposal(stage, action, *, quantity=0., cash=None, stop=None, reason=None):
        identity = f"{position.account_id}:{position.assignment_id}:{boundary.listing_id}:{at.isoformat()}:{action}"
        return ProgramProposal(str(uuid5(UUID(release.header["release_id"]), identity)),
            stage, action, at, boundary.ticker, quantity, boundary.close, cash, stop,
            reason or f"program:{stage}")
    if not position.position_open:
        if terminal and management.pending_acquisition:
            return (proposal("exit", "cancel_entry", reason="program:cancel_pending_acquisition"),)
        if terminal or management.pending_acquisition or not boundary.eligible or not enabled("entry"):
            return ()
        stop = boundary.initial_stop
        if stop is None or not isfinite(stop) or not 0 < stop < boundary.close:
            return ()
        return (proposal("entry", "enter_long", cash=float(release.header["entry_cash"]), stop=stop),)
    if position.average_price <= 0 or position.last_management_fill_at is None:
        raise ValueError("Open position lacks verified execution cost and management clock")
    if position.last_management_fill_at > at:
        raise ValueError("Management cannot see a future fill")
    if management.pending_reduction:
        return ()
    policy = release.header
    # An unfilled remainder must be cancelled/negotiated by OMS. Never emit a
    # competing reduction or pretend that remainder is already cancelled.
    if management.pending_acquisition:
        if terminal or enabled("exit") or (
                isfinite(management.stop_price) and management.stop_price > 0
                and boundary.close <= management.stop_price):
            return (proposal("exit", "cancel_entry", reason="program:cancel_pending_acquisition"),)
        return ()
    # Held tickers continue management after leaving the ranked top10.
    if not all(isfinite(v) and v > 0 for v in (
            management.stop_price, management.target_price, management.high_water_price)):
        raise ValueError("Open position lacks its actual management/protection state")
    stop = management.stop_price
    trail = ()
    if enabled("trail"):
        stop = max(stop, max(management.high_water_price, boundary.close) * (1 - policy["trail_fraction"]))
        if stop > management.stop_price:
            trail = (proposal("trail", "replace_protective_stop", stop=stop),)
    if terminal or enabled("exit") or boundary.close <= stop:
        return (proposal("exit", "exit", quantity=float(position.quantity),
                        reason="program:terminal" if terminal else "program:exit"),)
    elapsed = (at - position.last_management_fill_at).total_seconds()
    manageable = elapsed >= policy["cooldown_seconds"]
    profit = Decimal(str(boundary.close)) / position.average_price - 1
    target_hit = boundary.close >= management.target_price
    if manageable and (target_hit or (enabled("reduce")
            and profit >= Decimal(str(policy["reduce_minimum_profit"])) )):
        quantity = float(position.quantity) * policy["reduce_fraction"]
        if quantity > 0:
            return trail + (proposal("reduce", "reduce_long", quantity=quantity,
                            reason="program:target" if target_hit else None),)
        return trail
    if (manageable and enabled("add") and position.adds < policy["maximum_adds"]
            and profit >= Decimal(str(policy["add_minimum_profit"]))):
        return trail + (proposal("add", "add_long", cash=float(policy["add_cash"]), stop=stop),)
    return trail

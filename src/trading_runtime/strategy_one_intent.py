"""Typed, metadata-free Strategy 1 entry intent; not an order submission.

STRATEGY CREATION RULES: this unpublished number may consume only its sealed
entry proposal. The shared Portfolio/OMS owns funding, orders and fills. A
published strategy number is immutable; a behavior change gets a new number.
Proposal evidence needs its own normalized journal family before launch.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .execution_policies import (
    ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName,
    PartialFillPolicy, ProtectionProfile, ProtectionSlice, StopRule,
    StopRuleType,
)
from .numbered_fixed_strategy import numbered_fixed_strategy
from .signals import CapitalRequest, StrategyIntent
from .strategy_one_stateful import StrategyOneEntryProposal
from .strategy_one_add import StrategyOneAddProposal


_NEW_YORK = ZoneInfo("America/New_York")


def require_no_replacement_capital(intents: tuple[StrategyIntent, ...]) -> None:
    """Strategy 1 never replaces another position to fund an entry."""
    if any(intent.capital_request is not None
           and intent.capital_request.allow_replacement for intent in intents):
        raise ValueError("Strategy 1 cannot request replacement capital")


def require_strategy_one_actions(intents: tuple[StrategyIntent, ...]) -> None:
    """Strategy 1 exits only through its broker-held full stop and target."""
    if any(intent.action not in {
            "enter_long", "add_long", "replace_protective_stop", "replace_profit_target"}
           for intent in intents):
        raise ValueError("Strategy 1 cannot submit a legacy managed exit")


def strategy_one_entry_intent(
    proposal: StrategyOneEntryProposal, *, session_date: date,
) -> StrategyIntent:
    """Represent the approved proposal in existing scalar intent/slice tables.

    No proposal-only evidence is hidden in metadata, reason or an opaque blob.
    The caller must journal that evidence separately before routing this intent.
    """
    if (not isinstance(proposal, StrategyOneEntryProposal)
            or not isinstance(session_date, date)
            or isinstance(session_date, datetime)
            or type(proposal.strategy_number) is not int or proposal.strategy_number not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
            or type(proposal.boundary_ms) is not int
            or not 0 < proposal.boundary_ms <= 57_600_000
            or proposal.boundary_ms % 100
            or not proposal.assignment_id or not proposal.account_id
            or not proposal.ticker or proposal.ticker != proposal.ticker.upper()
            or type(proposal.reference_ask) is not float
            or type(proposal.initial_stop) is not float
            or type(proposal.initial_target) is not float
            or not all(isfinite(value) for value in (
                proposal.reference_ask, proposal.initial_stop,
                proposal.initial_target))
            or not 0 < proposal.initial_stop < proposal.reference_ask
            < proposal.initial_target):
        raise ValueError("Strategy 1 intent needs an exact numbered proposal and session")
    if proposal.strategy_number in (12, 13, 14):
        from .strategy_recent_bos_entry import recent_bos_entry
        if not recent_bos_entry(boundary_ms=proposal.boundary_ms,
                                bos_break_boundary_ms=proposal.bos_break_boundary_ms):
            raise ValueError("Strategy 12 entry requires recent supported BOS")
    if proposal.strategy_number in (13, 14):
        from .strategy_rising_momentum_witness import rising_momentum_entry
        if (not rising_momentum_entry(proposal.momentum)
                or proposal.momentum.ticker != proposal.ticker
                or proposal.momentum.boundary_ms != proposal.boundary_ms):
            raise ValueError("Strategy 13 entry requires rising completed momentum")
    elif proposal.momentum is not None:
        raise ValueError("Old numbered entry cannot carry Strategy 13 momentum witness")
    boundary = (datetime.combine(session_date, time(4), tzinfo=_NEW_YORK)
                + timedelta(milliseconds=proposal.boundary_ms))
    identity = (
        f"strategy-{proposal.strategy_number}:{session_date.isoformat()}:{proposal.assignment_id}:"
        f"{proposal.account_id}:{proposal.ticker}:{proposal.boundary_ms}:"
        f"{proposal.episode_start_ms}"
    )
    return StrategyIntent(
        intent_id=str(uuid5(NAMESPACE_URL, identity)),
        ticker=proposal.ticker,
        event_time=boundary.astimezone(timezone.utc),
        action="enter_long",
        quantity=0.,
        reference_price=proposal.reference_ask,
        capital_request=CapitalRequest(mode="mandate_fraction", value=1 / 3),
        invalidation_price=proposal.initial_stop,
        profit_target_price=proposal.initial_target,
        execution_policy=ExecutionPolicy(
            policy_id="strategy-adaptive_urgent",
            name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(
                persist_until_cancelled=True,
                maximum_buy_price=(proposal.reference_ask if numbered_fixed_strategy(
                    proposal.strategy_number).caps_entry_at_reference_ask else None)),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER,
            quote_source="qmd",
        ),
        protection_profile=ProtectionProfile(
            "early-squeeze-fixed-stop-full-target", 1,
            slices=(ProtectionSlice(
                "all", 1., StopRule(StopRuleType.FIXED_PRICE,
                                     price=proposal.initial_stop),
                profit_target_price=proposal.initial_target,
            ),),
        ),
        urgency="urgent", time_in_force="",
        outside_rth=boundary.time() < time(9, 30)
        or boundary.time() >= time(16),
        reason="strategy_one_entry",
        metadata={},
    )


def strategy_one_add_intent(
    proposal: StrategyOneAddProposal, *, session_date: date,
) -> StrategyIntent:
    """Fund one distinct causal resistance add through shared Portfolio/OMS.

    Strategy never reserves cash or chooses a broker order. The active
    protection bracket is explicit and the resistance source is journaled in
    a separate normalized child, not hidden in metadata or the reason.
    """
    if (not isinstance(proposal, StrategyOneAddProposal)
            or not isinstance(session_date, date)
            or isinstance(session_date, datetime)
            or type(proposal.strategy_number) is not int or proposal.strategy_number not in (1, 2, 3)
            or proposal.purchase_ordinal not in (2, 3)
            or type(proposal.boundary_ms) is not int
            or not 0 < proposal.boundary_ms <= 57_600_000
            or proposal.boundary_ms % 1_000
            or not proposal.account_id or not proposal.assignment_id
            or not proposal.ticker or proposal.ticker != proposal.ticker.upper()
            or not proposal.resistance_id
            or any(type(value) is not float or not isfinite(value)
                   for value in (proposal.resistance_midpoint,
                                 proposal.reference_ask, proposal.working_stop,
                                 proposal.working_target))
            or not 0 < proposal.working_stop < proposal.reference_ask
            < proposal.working_target):
        raise ValueError("Strategy 1 add needs an exact completed-bar proposal")
    boundary = (datetime.combine(session_date, time(4), tzinfo=_NEW_YORK)
                + timedelta(milliseconds=proposal.boundary_ms))
    identity = (
        f"strategy-{proposal.strategy_number}-add:{session_date.isoformat()}:{proposal.assignment_id}:"
        f"{proposal.account_id}:{proposal.ticker}:{proposal.boundary_ms}:"
        f"{proposal.resistance_id}"
    )
    return StrategyIntent(
        intent_id=str(uuid5(NAMESPACE_URL, identity)),
        ticker=proposal.ticker,
        event_time=boundary.astimezone(timezone.utc),
        action="add_long", quantity=0.,
        reference_price=proposal.reference_ask,
        capital_request=CapitalRequest(mode="mandate_fraction", value=1 / 3),
        invalidation_price=proposal.working_stop,
        profit_target_price=proposal.working_target,
        execution_policy=ExecutionPolicy(
            policy_id="strategy-adaptive_urgent",
            name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER,
            quote_source="qmd",
        ),
        protection_profile=ProtectionProfile(
            "early-squeeze-fixed-stop-full-target", 1,
            slices=(ProtectionSlice(
                "all", 1., StopRule(StopRuleType.FIXED_PRICE,
                                     price=proposal.working_stop),
                profit_target_price=proposal.working_target,
            ),),
        ),
        urgency="urgent", time_in_force="",
        outside_rth=boundary.time() < time(9, 30)
        or boundary.time() >= time(16),
        reason="strategy_one_add", metadata={},
    )

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
from .signals import CapitalRequest, StrategyIntent
from .strategy_one_stateful import StrategyOneEntryProposal


_NEW_YORK = ZoneInfo("America/New_York")


def require_no_replacement_capital(intents: tuple[StrategyIntent, ...]) -> None:
    """Strategy 1 never replaces another position to fund an entry."""
    if any(intent.capital_request is not None
           and intent.capital_request.allow_replacement for intent in intents):
        raise ValueError("Strategy 1 cannot request replacement capital")


def require_strategy_one_actions(intents: tuple[StrategyIntent, ...]) -> None:
    """Strategy 1 exits only through its broker-held full stop and target."""
    if any(intent.action not in {
            "enter_long", "replace_protective_stop", "replace_profit_target"}
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
            or proposal.strategy_number != 1
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
    boundary = (datetime.combine(session_date, time(4), tzinfo=_NEW_YORK)
                + timedelta(milliseconds=proposal.boundary_ms))
    identity = (
        f"strategy-1:{session_date.isoformat()}:{proposal.assignment_id}:"
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
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
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

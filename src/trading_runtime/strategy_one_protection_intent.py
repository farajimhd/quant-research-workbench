"""Metadata-free Strategy 1 protection intents for shared Portfolio/OMS.

STRATEGY CREATION RULES: only unpublished Strategy 1 may use these exact
completed-boundary amendments. A changed stop/target rule needs a new Strategy
number. The caller journals normalized evidence and confirms OMS effects before
committing reducer state; this module never reads bars or submits an order.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .signals import StrategyIntent
from .strategy_one_position import (
    ProtectionState, ProtectionTransition, confirm_protection_transition,
    ordered_protection_amendments,
)
from .strategy_one_stateful import StrategyOneFinancialView


_NEW_YORK = ZoneInfo("America/New_York")
_STOP_REASONS = {"completed_30s_bar_low", "three_resistance_step_stop"}


def strategy_one_protection_intents(
    previous: ProtectionState, transition: ProtectionTransition,
    financial: StrategyOneFinancialView, *,
    session_date: date, bid: float, ask: float,
) -> tuple[StrategyIntent, ...]:
    """Project approved price amendments, target first, at one causal clock."""
    if (not isinstance(previous, ProtectionState)
            or not isinstance(transition, ProtectionTransition)
            or not isinstance(financial, StrategyOneFinancialView)
            or not isinstance(session_date, date)
            or isinstance(session_date, datetime)
            or not financial.account_id or not financial.assignment_id
            or not financial.ticker or financial.ticker != financial.ticker.upper()
            or type(financial.position_quantity) not in (int, float)
            or not isfinite(financial.position_quantity)
            or financial.position_quantity <= 0
            or financial.pending_exit
            or type(bid) not in (int, float) or type(ask) not in (int, float)
            or not isfinite(bid) or not isfinite(ask)
            or not 0 < bid <= ask
            or type(transition.state.boundary_ms) is not int
            or not 0 < transition.state.boundary_ms <= 57_600_000
            or transition.state.boundary_ms % 100):
        raise ValueError("Strategy 1 protection intent lacks completed position authority")
    confirm_protection_transition(
        previous, transition, target_confirmed=False, stop_confirmed=False)
    boundary = (datetime.combine(session_date, time(4), tzinfo=_NEW_YORK)
                + timedelta(milliseconds=transition.state.boundary_ms))
    outside_rth = boundary.time() < time(9, 30) or boundary.time() >= time(16)
    result = []
    for action, amendment in ordered_protection_amendments(transition):
        price = amendment.get("price")
        if (type(price) not in (int, float) or not isfinite(price)
                or price != (transition.state.target if action == "replace_profit_target"
                             else transition.state.stop)
                or price <= (previous.target if action == "replace_profit_target"
                             else previous.stop)
                or action == "replace_profit_target" and not ask < price
                or action == "replace_protective_stop" and not 0 < price < bid):
            raise ValueError("Strategy 1 protection amendment is not executable")
        reason = ("ordinal_resistance_target" if action == "replace_profit_target"
                  else amendment.get("source"))
        if (action == "replace_protective_stop" and reason not in _STOP_REASONS):
            raise ValueError("Strategy 1 stop has an unknown numbered rule")
        identity = (
            f"strategy-1-protection:{session_date.isoformat()}:"
            f"{financial.account_id}:{financial.assignment_id}:"
            f"{financial.ticker}:{transition.state.boundary_ms}:{action}:{price}"
        )
        result.append(StrategyIntent(
            intent_id=str(uuid5(NAMESPACE_URL, identity)),
            ticker=financial.ticker,
            event_time=boundary.astimezone(timezone.utc),
            action=action,
            quantity=float(financial.position_quantity),
            reference_price=float(bid),
            invalidation_price=float(price) if action == "replace_protective_stop" else None,
            profit_target_price=float(price) if action == "replace_profit_target" else None,
            urgency="urgent", time_in_force="", outside_rth=outside_rth,
            reason=str(reason), metadata={},
        ))
    return tuple(result)

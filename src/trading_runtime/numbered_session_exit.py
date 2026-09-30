"""Metadata-free session liquidation source, stored as typed scalar intent.

The run pins the session policy and certified market build. The event timestamp
identifies the completed source bucket; the scalar intent preserves quantity,
bid and execution policy, and Portfolio/OMS preserve assignment ownership.
No entry-evidence child is fabricated for this risk-reducing source.
"""
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .execution_policies import ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy
from .signals import StrategyIntent
from .numbered_fixed_strategy import numbered_fixed_strategy


def numbered_session_exit_intent(*, session_date: date, account_id: str,
                                assignment_id: str, ticker: str, boundary_ms: int,
                                quantity: float, bid: float, strategy_number: int = 2) -> StrategyIntent:
    if (not numbered_fixed_strategy(strategy_number).liquidation_due(boundary_ms)
            or type(boundary_ms) is not int or boundary_ms % 100
            or not 0 < boundary_ms <= 57_600_000
            or not account_id or not assignment_id or not ticker
            or not isfinite(quantity) or quantity <= 0
            or not isfinite(bid) or bid <= 0):
        raise ValueError("Strategy 2 session liquidation lacks exact causal source")
    at = datetime.combine(session_date, time(4), ZoneInfo("America/New_York")) + timedelta(milliseconds=boundary_ms)
    identity = f"strategy-{strategy_number}-session-exit:{session_date}:{account_id}:{assignment_id}:{ticker}:{boundary_ms}"
    return StrategyIntent(
        intent_id=str(uuid5(NAMESPACE_URL, identity)), ticker=ticker,
        event_time=at.astimezone(timezone.utc), action="exit", quantity=float(quantity),
        reference_price=float(bid), urgency="urgent", outside_rth=True,
        reason=("strategy_two_session_exit" if strategy_number == 2 else "strategy_three_session_exit" if strategy_number == 3 else "strategy_four_session_exit" if strategy_number == 4 else "strategy_five_session_exit" if strategy_number == 5 else "strategy_six_session_exit" if strategy_number == 6 else "strategy_seven_session_exit" if strategy_number == 7 else "strategy_eight_session_exit" if strategy_number == 8 else "strategy_nine_session_exit" if strategy_number == 9 else "strategy_ten_session_exit" if strategy_number == 10 else "strategy_eleven_session_exit" if strategy_number == 11 else "strategy_twelve_session_exit" if strategy_number == 12 else "strategy_thirteen_session_exit"), metadata={},
        execution_policy=ExecutionPolicy(
            policy_id="strategy-adaptive_urgent", name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source="qmd"))

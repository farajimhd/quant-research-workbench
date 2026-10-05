"""Prepared Strategy 34 exit factory; registration/persistence remain closed."""
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .execution_policies import ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy
from .signals import StrategyIntent
from .strategy_confirmed_ah_risk_failure import (
    ConfirmedAhRiskFailure, ConfirmedAhRiskFailureInput, confirmed_ah_risk_failure,
)
from .strategy_followthrough_failure import FollowThroughFailureInput
from .strategy_one_stateful import StrategyOneFinancialView

REASON = 'strategy_thirty_four_confirmed_ah_failure'


def confirmed_ah_reason(strategy_number):
    """Retain the parent's identity and assign its successor an exact reason."""
    if type(strategy_number) is not int or strategy_number not in (34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58):
        raise ValueError('AH confirmation requires Strategy 34 through 40')
    return {34: REASON, 35: 'strategy_thirty_five_confirmed_ah_failure',
            36: 'strategy_thirty_six_confirmed_ah_failure',
            37: 'strategy_thirty_seven_confirmed_ah_failure', 38: 'strategy_thirty_eight_confirmed_ah_failure', 39: 'strategy_thirty_nine_confirmed_ah_failure', 40: 'strategy_forty_confirmed_ah_failure', 41: 'strategy_forty_one_confirmed_ah_failure', 42: 'strategy_forty_two_confirmed_ah_failure', 46: 'strategy_forty_six_confirmed_ah_failure', 47: 'strategy_forty_seven_confirmed_ah_failure', 48: 'strategy_forty_eight_confirmed_ah_failure', 50: 'strategy_fifty_confirmed_ah_failure', 52: 'strategy_fifty_two_confirmed_ah_failure', 53: 'strategy_fifty_three_confirmed_ah_failure', 54: 'strategy_fifty_four_confirmed_ah_failure', 55: 'strategy_fifty_five_confirmed_ah_failure', 56: 'strategy_fifty_six_confirmed_ah_failure', 57: 'strategy_fifty_seven_confirmed_ah_failure', 58: 'strategy_fifty_eight_confirmed_ah_failure'}[strategy_number]


def validate_confirmed_ah_witness(witness):
    """Re-run both completed-bar conditions without altering original risk."""
    if type(witness) is not ConfirmedAhRiskFailure:
        raise ValueError('AH exit requires its complete typed confirmation witness')
    w = witness.five_second
    from .strategy_followthrough_failure import FollowThroughFailure
    if type(w) is not FollowThroughFailure:
        raise ValueError('AH exit requires its exact five-second witness')
    original = FollowThroughFailureInput(
        w.boundary_ms, w.first_held_boundary_ms, w.reference_ask, w.initial_stop,
        w.boundary_ms, w.completed_close_int, True, w.macd_line, w.macd_signal,
        w.bid, w.ask, w.quote_age_us, 1.0, False,
    )
    actual = confirmed_ah_risk_failure(ConfirmedAhRiskFailureInput(
        original, witness.completed_ten_second_boundary_ms, True,
        witness.ten_second_macd_line, witness.ten_second_macd_signal,
    ))
    if actual != witness:
        raise ValueError('AH witness does not satisfy both pinned producer observations')


def confirmed_ah_exit_intent(witness, financial, *, session_date, source_entry_intent_id, strategy_number=34):
    """Only the existing Portfolio/OMS path may execute this prepared intent."""
    reason = confirmed_ah_reason(strategy_number)
    validate_confirmed_ah_witness(witness)
    UUID(source_entry_intent_id)
    if (type(financial) is not StrategyOneFinancialView
            or not financial.account_id or not financial.assignment_id or not financial.ticker
            or not isfinite(financial.position_quantity) or financial.position_quantity <= 0
            or financial.pending_exit):
        raise ValueError('AH exit requires exact held financial authority')
    w = witness.five_second
    at = datetime.combine(session_date, time(4), ZoneInfo('America/New_York')) + timedelta(milliseconds=w.boundary_ms)
    identity = f'strategy-{strategy_number}-confirmed-ah-exit:{session_date}:{financial.account_id}:{financial.assignment_id}:{financial.ticker}:{source_entry_intent_id}:{w.boundary_ms}'
    return StrategyIntent(
        intent_id=str(uuid5(NAMESPACE_URL, identity)), ticker=financial.ticker,
        event_time=at.astimezone(timezone.utc), action='exit',
        quantity=float(financial.position_quantity), reference_price=w.bid,
        urgency='urgent', outside_rth=True, reason=reason, metadata={},
        execution_policy=ExecutionPolicy(
            policy_id='strategy-adaptive_urgent', name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source='qmd',
        ),
    )

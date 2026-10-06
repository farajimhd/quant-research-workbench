"""Immutable scalar Strategy 9 liquidation intent from the shared rule witness."""
from src.trading_runtime.numbered_fixed_strategy import declared_fixed_rule
from datetime import datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo
from math import isfinite

from .execution_policies import ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy
from .signals import StrategyIntent
from .strategy_followthrough_failure import FollowThroughFailure, FollowThroughFailureInput, followthrough_failure
from .strategy_one_stateful import StrategyOneFinancialView


def validate_witness(witness, *, strategy_number=9):
    if type(witness) is not FollowThroughFailure:
        raise ValueError("Follow-through exit requires the exact scalar witness")
    if type(strategy_number) is not int or (strategy_number not in (9, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) and not declared_fixed_rule(strategy_number, 'strategy-nine-followthrough-failure-v1')):
        raise ValueError("Failure factory requires legacy or inherited quarter-risk witness authority")
    from .strategy_premarket_quarter_risk_failure import premarket_quarter_risk_failure
    from .strategy_persistent_risk_failure import persistent_risk_failure
    from .strategy_zero_regime_risk_failure import zero_regime_risk_failure
    rule = (zero_regime_risk_failure if (strategy_number in (30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(strategy_number, 'strategy-thirty-zero-regime-original-risk-failure-v1'))
            else persistent_risk_failure if strategy_number == 29
            else premarket_quarter_risk_failure if strategy_number in (25, 26, 27, 28)
            else followthrough_failure)
    value = FollowThroughFailureInput(
        witness.boundary_ms, witness.first_held_boundary_ms,
        witness.reference_ask, witness.initial_stop, witness.boundary_ms,
        witness.completed_close_int, True, witness.macd_line, witness.macd_signal,
        witness.bid, witness.ask, witness.quote_age_us, 1.0, False)
    if strategy_number in (46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61):
        from .declared_followthrough_failure import declared_followthrough_failure
        from .numbered_fixed_strategy import numbered_fixed_strategy
        actual = declared_followthrough_failure(
            value, inherited=zero_regime_risk_failure,
            early_policy=numbered_fixed_strategy(strategy_number).early_original_risk_policy)
    elif declared_fixed_rule(strategy_number, 'held-extended-session-quarter-original-risk-failure@1'):
        from .declared_followthrough_failure import declared_followthrough_failure
        from .numbered_fixed_strategy import numbered_fixed_strategy
        actual = declared_followthrough_failure(
            value, inherited=rule,
            all_held_policy=numbered_fixed_strategy(strategy_number).all_held_original_risk_policy)
    else:
        actual = rule(value)
    if actual != witness:
        raise ValueError("Follow-through witness does not satisfy its pinned rule")


def followthrough_exit_intent(witness, financial, *, session_date, source_entry_intent_id, strategy_number=9):
    validate_witness(witness, strategy_number=strategy_number)
    UUID(source_entry_intent_id)
    if (type(financial) is not StrategyOneFinancialView
            or not financial.account_id or not financial.assignment_id or not financial.ticker
            or not isfinite(financial.position_quantity) or financial.position_quantity <= 0
            or financial.pending_exit):
        raise ValueError("Follow-through exit requires exact held financial authority")
    at = datetime.combine(session_date, time(4), ZoneInfo("America/New_York")) + timedelta(milliseconds=witness.boundary_ms)
    identity = f"strategy-{strategy_number}-followthrough-exit:{session_date}:{financial.account_id}:{financial.assignment_id}:{financial.ticker}:{source_entry_intent_id}:{witness.boundary_ms}"
    return StrategyIntent(intent_id=str(uuid5(NAMESPACE_URL, identity)),
        ticker=financial.ticker, event_time=at.astimezone(timezone.utc), action="exit",
        quantity=float(financial.position_quantity), reference_price=witness.bid,
        urgency="urgent", outside_rth=True, reason="strategy_nine_followthrough_failure", metadata={},
        execution_policy=ExecutionPolicy(policy_id="strategy-adaptive_urgent",
            name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source="qmd"))

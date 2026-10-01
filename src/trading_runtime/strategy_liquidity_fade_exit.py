"""Prepared Strategy 35 exit factory; native admission remains uninstalled."""
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .execution_policies import ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy
from .signals import StrategyIntent
from .strategy_followthrough_failure import FollowThroughFailureInput
from .strategy_liquidity_fade_failure import (
    LiquidityFadeFailure, LiquidityFadeInput, liquidity_fade_failure,
)
from .strategy_one_stateful import StrategyOneFinancialView

REASON = "strategy_thirty_five_liquidity_fade_failure"


def validate_liquidity_fade_witness(witness):
    """Replay complete observations; scalar validity is not source attestation."""
    if type(witness) is not LiquidityFadeFailure:
        raise ValueError("Liquidity fade exit requires its complete typed witness")
    w = witness
    original = FollowThroughFailureInput(
        w.boundary_ms, w.first_held_boundary_ms, w.reference_ask, w.initial_stop,
        w.completed_five_second_boundary_ms, w.completed_close_int, True,
        w.macd_line, w.macd_signal, w.bid, w.ask, w.quote_age_us, 1.0, False,
    )
    actual = liquidity_fade_failure(LiquidityFadeInput(original, w.candles))
    if actual != witness:
        raise ValueError("Liquidity fade witness differs from its producer observations")


def validate_liquidity_fade_financial(financial):
    """Require finite held quantity and the exact Portfolio identity types."""
    if (type(financial) is not StrategyOneFinancialView
            or any(type(v) is not str or not v for v in
                   (financial.account_id, financial.assignment_id, financial.ticker))
            or type(financial.position_quantity) not in (int, float)
            or not isfinite(financial.position_quantity) or financial.position_quantity <= 0
            or type(financial.pending_exit) is not bool
            or financial.pending_exit):
        raise ValueError("Liquidity fade exit requires exact held financial authority")


def liquidity_fade_exit_intent(witness, financial, *, session_date, source_entry_intent_id):
    """Use inherited Portfolio/OMS execution, never a direct broker command."""
    validate_liquidity_fade_witness(witness)
    validate_liquidity_fade_financial(financial)
    UUID(source_entry_intent_id)
    if type(session_date) is not date:
        raise ValueError("Liquidity fade exit requires an exact session date")
    at = datetime.combine(session_date, time(4), ZoneInfo("America/New_York")) + timedelta(milliseconds=witness.boundary_ms)
    identity = (f"strategy-35-liquidity-fade-exit:{session_date}:{financial.account_id}:"
                f"{financial.assignment_id}:{financial.ticker}:{source_entry_intent_id}:{witness.boundary_ms}")
    return StrategyIntent(
        intent_id=str(uuid5(NAMESPACE_URL, identity)), ticker=financial.ticker,
        event_time=at.astimezone(timezone.utc), action="exit",
        quantity=float(financial.position_quantity), reference_price=witness.bid,
        urgency="urgent", outside_rth=True, reason=REASON, metadata={},
        execution_policy=ExecutionPolicy(
            policy_id="strategy-adaptive_urgent", name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source="qmd",
        ),
    )

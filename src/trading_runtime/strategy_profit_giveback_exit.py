"""Immutable candidate liquidation intent; no submission or storage authority."""
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .execution_policies import (
    ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy,
)
from .signals import StrategyIntent
from .strategy_followthrough_failure import FollowThroughFailureInput
from .strategy_one_stateful import StrategyOneFinancialView
from .strategy_profit_giveback import ProfitGivebackInput, ProfitGivebackWitness, profit_giveback

REASON = 'strategy_thirty_one_profit_giveback'


def profit_giveback_reason(strategy_number: int) -> str:
    """One exact numbered identity shared by factory, persistence and recovery."""
    if type(strategy_number) is not int or strategy_number not in (31, 32, 33, 34, 35, 36, 37, 38, 39):
        raise ValueError('Profit protection requires Strategy 31 through 39')
    return {31: REASON, 32: 'strategy_thirty_two_profit_giveback',
            33: 'strategy_thirty_three_profit_giveback',
            34: 'strategy_thirty_four_profit_giveback',
            35: 'strategy_thirty_five_profit_giveback',
            36: 'strategy_thirty_six_profit_giveback',
            37: 'strategy_thirty_seven_profit_giveback', 38: 'strategy_thirty_eight_profit_giveback', 39: 'strategy_thirty_nine_profit_giveback'}[strategy_number]


def validate_profit_giveback_witness(witness: ProfitGivebackWitness) -> None:
    """Recompute the exact scalar predicate at persistence/factory boundaries."""
    if type(witness) is not ProfitGivebackWitness:
        raise ValueError('Profit protection requires exact scalar witness')
    completed = FollowThroughFailureInput(
        witness.boundary_ms, witness.first_held_boundary_ms,
        witness.reference_ask, witness.initial_stop, witness.boundary_ms,
        witness.completed_close_int, True, witness.macd_line, witness.macd_signal,
        witness.bid, witness.ask, witness.quote_age_us, 1., False)
    actual = profit_giveback(ProfitGivebackInput(
        completed, witness.prior_high_int, witness.prior_high_through_boundary_ms))
    if actual != witness:
        raise ValueError('Profit protection witness does not satisfy its pinned rule')


def profit_giveback_exit_intent(
    witness: ProfitGivebackWitness, financial: StrategyOneFinancialView, *,
    session_date: date, source_entry_intent_id: str, strategy_number: int = 31,
) -> StrategyIntent:
    reason = profit_giveback_reason(strategy_number)
    validate_profit_giveback_witness(witness)
    UUID(source_entry_intent_id)
    if (type(session_date) is not date
            or type(financial) is not StrategyOneFinancialView
            or not financial.account_id or not financial.assignment_id or not financial.ticker
            or type(financial.position_quantity) not in (int, float)
            or not isfinite(financial.position_quantity) or financial.position_quantity <= 0
            or type(financial.pending_exit) is not bool or financial.pending_exit):
        raise ValueError('Profit protection exit requires exact held financial authority')
    at = datetime.combine(session_date, time(4), ZoneInfo('America/New_York')) + timedelta(milliseconds=witness.boundary_ms)
    identity = (f'strategy-{strategy_number}-profit-giveback-exit:{session_date}:'
                f'{financial.account_id}:{financial.assignment_id}:{financial.ticker}:'
                f'{source_entry_intent_id}:{witness.boundary_ms}')
    return StrategyIntent(
        intent_id=str(uuid5(NAMESPACE_URL, identity)), ticker=financial.ticker,
        event_time=at.astimezone(timezone.utc), action='exit',
        quantity=float(financial.position_quantity), reference_price=witness.bid,
        urgency='urgent', outside_rth=True, reason=reason, metadata={},
        execution_policy=ExecutionPolicy(
            policy_id='strategy-adaptive_urgent', name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source='qmd'))

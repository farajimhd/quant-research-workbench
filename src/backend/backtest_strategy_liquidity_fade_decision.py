"""Prepared liquidity exit using the shared post-broker financial authority.

The coordinator must call this in its serialized completed-boundary lane and
publish only after original-entry, producer and native checkpoint verification.
This function neither submits an order nor installs the Strategy 35 runtime.
"""
from dataclasses import dataclass
from datetime import date

from .backtest_strategy_one_financial import read_strategy_one_financial_view
from src.trading_runtime.strategy_engine import StrategyAssignment
from src.trading_runtime.strategy_one_contract import STRATEGY_ID
from src.trading_runtime.strategy_liquidity_fade_exit import (
    liquidity_fade_exit_intent, validate_liquidity_fade_witness,
)
from src.trading_runtime.strategy_liquidity_fade_source import validate_liquidity_fade_state
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeFailure
from src.trading_runtime.signals import StrategyIntent


@dataclass(frozen=True, slots=True)
class PreparedLiquidityFadeDecision:
    """Exact observed financial view and ordinary exit; not a commit seal."""
    witness: LiquidityFadeFailure
    financial: StrategyOneFinancialView
    intent: StrategyIntent


async def prepare_liquidity_fade_decision(witness, state, assignment, broker, order_manager,
                                        *, session_date, source_entry_intent_id):
    """Re-read broker/OMS for every candidate and bind the native manager key.

    The shared reader uses exact broker quantity and indexed OMS groups where
    available. No database query or cross-boundary financial cache is added.
    Pending entries remain Portfolio/OMS cancellation authority; pending exits
    reject. Native checkpoint/prefix/market attestation remains mandatory before
    publication; a caller-supplied manager state alone does not establish it.
    """
    validate_liquidity_fade_witness(witness)
    if (type(assignment) is not StrategyAssignment or type(session_date) is not date
            or assignment.strategy_id != STRATEGY_ID
            or type(assignment.strategy_revision) is not int or assignment.strategy_revision != 35
            or type(assignment.conid) is not int or assignment.conid <= 0):
        raise ValueError('Liquidity decision requires its exact assignment and session day')
    financial = await read_strategy_one_financial_view(assignment, broker, order_manager)
    validate_liquidity_fade_state(witness, state, financial)
    intent = liquidity_fade_exit_intent(witness, financial, session_date=session_date,
                                      source_entry_intent_id=source_entry_intent_id)
    return PreparedLiquidityFadeDecision(witness, financial, intent)

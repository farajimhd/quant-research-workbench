"""Deterministic exit intent from the issued own native checkpoint handoff."""
from datetime import date,timezone
from math import isfinite
from uuid import UUID,NAMESPACE_URL,uuid5

from .execution_policies import ExecutionEnvelope,ExecutionPolicy,ExecutionPolicyName,PartialFillPolicy
from .signals import StrategyIntent
from .profit_armed_structural_rejection_confirmation import require_structural_rejection_confirmation

REASON='profit_armed_structural_rejection_liquidity'


def structural_rejection_exit_intent(confirmation):
    """Build only an intent; Portfolio/OMS remain the order and cash owners."""
    from src.backend.backtest_market_data import market_day_boundary
    require_structural_rejection_confirmation(confirmation)
    request=confirmation.request
    financial=request.financial
    witness=request.witness
    if (type(financial.position_quantity) not in (int,float)
            or not isfinite(financial.position_quantity) or financial.position_quantity<=0
            or financial.pending_exit is not False or financial.permissions.exit is not True):
        raise ValueError('Structural rejection exit requires finite held quantity and current exit permission')
    source_entry=request.source_entry_intent_id
    if str(UUID(source_entry))!=source_entry or UUID(source_entry).int==0:
        raise ValueError('Structural rejection exit lacks canonical original entry identity')
    day=date.fromisoformat(witness.predecessor.source.session_date)
    at=market_day_boundary(day,confirmation.boundary_ms).astimezone(timezone.utc)
    identity=(f'{REASON}:{confirmation.run_id}:{financial.account_id}:{financial.assignment_id}:'
        f'{financial.ticker}:{source_entry}:{confirmation.boundary_ms}')
    return StrategyIntent(intent_id=str(uuid5(NAMESPACE_URL,identity)),ticker=financial.ticker,
        event_time=at,action='exit',quantity=float(financial.position_quantity),
        reference_price=witness.quote.bid_int/10000,urgency='urgent',outside_rth=True,
        reason=REASON,metadata={},execution_policy=ExecutionPolicy(
            policy_id='strategy-adaptive_urgent',name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER,quote_source='qmd'))

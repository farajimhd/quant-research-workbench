"""One arming checkpoint candidate per position, before any giveback intent.

Candidate selection does not commit a snapshot. The native checkpoint publisher
must persist the complete current capture and return its verified identity.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from .strategy_one_stateful import StrategyOneFinancialView, StrategyOneEntryProposal


@dataclass(frozen=True, slots=True)
class ProfitArmCandidate:
    account_id: str
    assignment_id: str
    ticker: str
    boundary_ms: int
    first_held_boundary_ms: int
    reference_ask: float
    initial_stop: float
    high_int: int


def profit_arm_candidate(state, financial, *, already_checkpointed: bool) -> ProfitArmCandidate | None:
    """Select current completed high once; the current bucket cannot exit."""
    if (type(state) is not StrategyOneManagementState
            or type(financial) is not StrategyOneFinancialView
            or type(already_checkpointed) is not bool
            or type(financial.position_quantity) not in (int,float)
            or not isfinite(financial.position_quantity) or financial.position_quantity<0
            or type(financial.pending_exit) is not bool):
        raise ValueError('Profit arming requires exact manager and position authority')
    if already_checkpointed or financial.position_quantity <= 0 or financial.pending_exit:
        return None
    key=(financial.account_id, financial.assignment_id, financial.ticker)
    families={}
    for name in ('submitted','positions','position_highs','first_held_boundaries'):
        rows=getattr(state,name)
        if len({k for k,_ in rows})!=len(rows):
            raise ValueError('Profit arming repeats a manager identity')
        families[name]=dict(rows)
        if key not in families[name]:
            raise ValueError('Profit arming lacks its held position')
    source=families['submitted'][key]
    high=families['position_highs'][key]
    first=families['first_held_boundaries'][key]
    if (type(source) is not StrategyOneEntryProposal or source.strategy_number not in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47)
            or (source.account_id,source.assignment_id,source.ticker)!=key
            or type(high) is not int or not 0<high<2**64
            or type(first) is not int or first%100
            or type(state.boundary_ms) is not int or state.boundary_ms%100
            or not 0<state.boundary_ms<=57_600_000
            or not source.boundary_ms<first<=state.boundary_ms
            or not 0<source.initial_stop<source.reference_ask
            or any(type(x) not in (int,float) or not isfinite(x)
                   for x in (source.initial_stop,source.reference_ask))
            or families['positions'][key].boundary_ms>state.boundary_ms):
        raise ValueError('Profit arming lacks exact completed original-risk facts')
    ask=Decimal(str(source.reference_ask));stop=Decimal(str(source.initial_stop))
    if Decimal(high)<(ask+ask-stop)*10000:
        return None
    return ProfitArmCandidate(*key,state.boundary_ms,first,source.reference_ask,source.initial_stop,high)

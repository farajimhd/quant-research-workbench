"""Prepared scalar binding after checkpoint hash and commit verification.

This does not certify a checkpoint or an entry commit. Writer/recovery callers
must first load those authorities through the existing native sealed readers.
"""
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from .strategy_one_stateful import StrategyOneEntryProposal, StrategyOneFinancialView
from .strategy_profit_giveback_exit import validate_profit_giveback_witness


def validate_profit_giveback_state(witness, state, financial) -> StrategyOneEntryProposal:
    """Bind the prior high, held clock and original risk to the exact position."""
    validate_profit_giveback_witness(witness)
    if type(state) is not StrategyOneManagementState or type(financial) is not StrategyOneFinancialView:
        raise ValueError('Profit source needs exact manager and financial types')
    if state.boundary_ms != witness.prior_high_through_boundary_ms:
        raise ValueError('Profit high differs from prior manager boundary')
    key = (financial.account_id, financial.assignment_id, financial.ticker)
    families = {}
    for name in ('submitted', 'positions', 'position_highs', 'first_held_boundaries'):
        rows = getattr(state, name)
        if len({k for k, _ in rows}) != len(rows):
            raise ValueError('Profit source repeats a manager position identity')
        families[name] = dict(rows)
        if key not in families[name]:
            raise ValueError('Profit source lacks exact held position identity')
    source = families['submitted'][key]
    if (type(source) is not StrategyOneEntryProposal or source.strategy_number != 31
            or (source.account_id, source.assignment_id, source.ticker) != key
            or source.reference_ask != witness.reference_ask
            or source.initial_stop != witness.initial_stop
            or not source.boundary_ms < witness.first_held_boundary_ms
            or families['first_held_boundaries'][key] != witness.first_held_boundary_ms
            or families['position_highs'][key] != witness.prior_high_int
            or families['positions'][key].boundary_ms > state.boundary_ms):
        raise ValueError('Profit source differs from original entry or checkpoint high')
    return source

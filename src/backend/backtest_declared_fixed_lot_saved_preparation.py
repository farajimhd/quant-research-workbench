"""Route saved preparation through declared capabilities used by execution."""
from src.trading_runtime.strategy_registry import numbered_strategy
from src.trading_runtime.fixed_lot_management_native_preparation import (
    uses_management_native_preparation,
)


def prepare_declared_saved_fixed_lot_session(*, plans, number, run_id,
        session_date, market, candidates, entry, seeds, through_boundary_ms,
        client_factory):
    if type(number) is not int or number < 1:
        raise ValueError('Exact registered saved strategy identity required')
    release = numbered_strategy(number)
    if uses_management_native_preparation(release):
        from .backtest_fixed_structural_lot_execution_v20 import prepare_fixed_structural_lot_session
    else:
        from .backtest_fixed_structural_lot_execution_v13 import prepare_fixed_structural_lot_session
    # The normal selected implementation owns complete installed-manifest,
    # source@2, quote, plan and publication-profile issuance checks.
    return prepare_fixed_structural_lot_session(plans=plans, number=number,
        run_id=run_id, session_date=session_date, market=market,
        candidates=candidates, entry=entry, seeds=seeds,
        through_boundary_ms=through_boundary_ms, client_factory=client_factory)

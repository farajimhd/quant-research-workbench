"""Declared source capability routing before journal or broker admission."""
from contextlib import closing
from . import backtest_fixed_structural_lot_execution as legacy
from .backtest_fixed_structural_lot_execution import PreparedFixedStructuralLotSession
from .backtest_strategy_one_candidate_store import project_candidate_plan
from .backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
from .backtest_fixed_structural_lot_source_v2 import SOURCE_INPUT, verify_complete_scope


def prepare_fixed_structural_lot_session(*, plans, number, run_id, session_date,
        market, candidates, entry, seeds, through_boundary_ms, client_factory):
    from src.trading_runtime.strategy_registry import numbered_strategy
    release = numbered_strategy(number)
    if SOURCE_INPUT not in release.input_contracts:
        return legacy.prepare_fixed_structural_lot_session(number=number, run_id=run_id,
            session_date=session_date, market=market, candidates=candidates, entry=entry,
            seeds=seeds, through_boundary_ms=through_boundary_ms, client_factory=client_factory)
    if (plans.market is not market or plans.candidates is not candidates
            or plans.entry is not entry or plans.seeds is not seeds):
        raise ValueError('Source@2 session does not carry its exact whole certified plans')
    visible = project_candidate_plan(candidates, through_boundary_ms=through_boundary_ms)
    if not visible.prepared:
        from .backtest_fixed_structural_lot_empty_v2 import prepare_empty_fixed_structural_lot_source
        with closing(client_factory()) as client:
            source = prepare_empty_fixed_structural_lot_source(client, plans=plans,
                number=number, run_id=run_id, session_date=session_date,
                through_boundary_ms=through_boundary_ms)
        operation = NativeFixedStructuralLotOperation(source)
        authorities = (visible,None,None,None,None,(),None)
    else:
        from .backtest_strategy_one_execution import prepare_strategy_one_entry_authorities
        authorities = prepare_strategy_one_entry_authorities(market=market,
            candidates=candidates, entry=entry, through_boundary_ms=through_boundary_ms,
            strategy_number=number, run_id=run_id, client_factory=client_factory)
        if authorities[-1] is None:
            raise ValueError('Source@2 nonempty session lacks certified price authority')
        from .backtest_fixed_structural_lot_native_v2 import prepare_native_fixed_structural_lot_operation
        with closing(client_factory()) as client:
            operation = prepare_native_fixed_structural_lot_operation(client,
                number=number, run_id=run_id, session_date=session_date,
                plans=plans, price_authority=authorities[-1], through_boundary_ms=through_boundary_ms)
    from src.trading_runtime.fixed_structural_lot_profile import issue_fixed_structural_lot_profile
    profile = issue_fixed_structural_lot_profile(operation)
    prepared = PreparedFixedStructuralLotSession(operation, authorities, profile)
    with legacy._LOCK:
        legacy._SESSIONS[prepared] = (market,candidates,entry,through_boundary_ms,
            run_id,number,operation,authorities,profile)
    return prepared

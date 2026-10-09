"""Declared source capability routing before journal or broker admission."""
from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE as IDENTITY_RULE, BATCHED_DETAIL_SELECT_RULE as BATCHED_RULE, SELECTED_CHECKPOINT_PRODUCT_RULE as CHECKPOINT_RULE, OPERATION_CHECKPOINT_READER_RULE as READER_RULE
from src.trading_runtime.decimal_snapshot_readback import RULE as DECIMAL_RULE
from src.trading_runtime.packet_validation_reuse_policy import INPUT as REUSE_INPUT, RULE as REUSE_RULE
from src.trading_runtime.projected_configuration_reuse_policy import INPUT as PROJECTION_REUSE_INPUT, RULE as PROJECTION_REUSE_RULE
from src.trading_runtime.owned_scalar_snapshot_policy import INPUT as OWNED_INPUT, RULE as OWNED_RULE
from src.trading_runtime.empty_protection_confirmation_policy import INPUT as EMPTY_INPUT, RULE as EMPTY_RULE
from src.trading_runtime.complete_market_window_policy import INPUT as WINDOW_INPUT, RULE as WINDOW_RULE
from src.trading_runtime.selected_exit_publication_policy import INPUT as EXIT_INPUT, RULE as EXIT_RULE
from contextlib import closing
from . import backtest_fixed_structural_lot_execution_v18 as legacy
from .backtest_fixed_structural_lot_execution import PreparedFixedStructuralLotSession
from . import backtest_fixed_structural_lot_execution as issuer
from src.trading_runtime.fixed_structural_lot_interval_validator_v2 import VALIDATOR_RULE
from .backtest_fixed_structural_lot_projection_runtime_authority import PROJECTION_RULE
from src.trading_runtime.fixed_structural_lot_native_source_rule import NATIVE_SOURCE_RULE
from src.trading_runtime.fixed_structural_lot_causal_clock import CLOCK_RULE
from src.trading_runtime.fixed_structural_lot_warm_proof import RULE as WARM_RULE
from .backtest_strategy_one_candidate_store import project_candidate_plan
from .backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
from .backtest_fixed_structural_lot_source_v19 import SOURCE_INPUT, verify_complete_scope


def prepare_fixed_structural_lot_session(*, plans, number, run_id, session_date,
        market, candidates, entry, seeds, through_boundary_ms, client_factory):
    from src.trading_runtime.strategy_registry import numbered_strategy
    release = numbered_strategy(number)
    if EXIT_RULE not in release.rule_set_contracts and EXIT_INPUT not in release.input_contracts:
        return legacy.prepare_fixed_structural_lot_session(plans=plans, number=number, run_id=run_id,
            session_date=session_date, market=market, candidates=candidates, entry=entry,
            seeds=seeds, through_boundary_ms=through_boundary_ms, client_factory=client_factory)
    if release.rule_set_contracts.count(EXIT_RULE) != 1 or release.input_contracts.count(EXIT_INPUT) != 1 or release.rule_set_contracts.count(WINDOW_RULE) != 1 or release.input_contracts.count(WINDOW_INPUT) != 1 or release.rule_set_contracts.count(EMPTY_RULE) != 1 or release.input_contracts.count(EMPTY_INPUT) != 1 or release.rule_set_contracts.count(OWNED_RULE) != 1 or release.input_contracts.count(OWNED_INPUT) != 1 or release.rule_set_contracts.count(PROJECTION_REUSE_RULE) != 1 or release.input_contracts.count(PROJECTION_REUSE_INPUT) != 1 or release.rule_set_contracts.count(REUSE_RULE) != 1 or release.input_contracts.count(REUSE_INPUT) != 1 or release.rule_set_contracts.count(DECIMAL_RULE) != 1 or release.rule_set_contracts.count(READER_RULE) != 1 or release.rule_set_contracts.count(CHECKPOINT_RULE) != 1 or release.rule_set_contracts.count(BATCHED_RULE) != 1 or release.rule_set_contracts.count(IDENTITY_RULE) != 1 or release.rule_set_contracts.count(WARM_RULE) != 1 or release.rule_set_contracts.count(CLOCK_RULE) !=1 or release.rule_set_contracts.count(NATIVE_SOURCE_RULE) != 1 or release.input_contracts.count(SOURCE_INPUT) != 1 or release.rule_set_contracts.count(VALIDATOR_RULE) != 1 or release.rule_set_contracts.count(PROJECTION_RULE) != 1:
        raise ValueError('Canonical interval validator lacks exact source@2 declaration')
    if (plans.market is not market or plans.candidates is not candidates
            or plans.entry is not entry or plans.seeds is not seeds):
        raise ValueError('Source@2 session does not carry its exact whole certified plans')
    visible = project_candidate_plan(candidates, through_boundary_ms=through_boundary_ms)
    if not visible.prepared:
        from .backtest_fixed_structural_lot_empty_v19 import prepare_empty_fixed_structural_lot_source
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
        from .backtest_fixed_structural_lot_native_v19 import prepare_native_fixed_structural_lot_operation
        with closing(client_factory()) as client:
            operation = prepare_native_fixed_structural_lot_operation(client,
                number=number, run_id=run_id, session_date=session_date,
                plans=plans, price_authority=authorities[-1], through_boundary_ms=through_boundary_ms)
    from src.trading_runtime.fixed_structural_lot_profile import issue_fixed_structural_lot_profile
    profile = issue_fixed_structural_lot_profile(operation)
    prepared = PreparedFixedStructuralLotSession(operation, authorities, profile)
    with issuer._LOCK:
        issuer._SESSIONS[prepared] = (market,candidates,entry,through_boundary_ms,
            run_id,number,operation,authorities,profile)
    if visible.prepared:
        from .backtest_installed_complete_market_source import _bind_complete_market_plans
        with closing(client_factory()) as client:
            _bind_complete_market_plans(prepared, plans=plans,
                through_boundary_ms=through_boundary_ms, client=client)
    return prepared

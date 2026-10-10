"""Native declaration selector; this module grants no execution capability."""
from src.trading_runtime.publication_source_reuse_policy import (
    INPUT, RULE, PARAMETER, parse_declared_publication_source_reuse,
)


def selected_publication_source_reuse_policy(source):
    payload = source.installed_payload
    if payload is None:
        return None
    if type(payload) is not dict or type(payload.get('strategy')) is not dict:
        raise ValueError('Publication reuse requires a complete installed payload')
    strategy = payload['strategy']
    parameters = strategy.get('parameters')
    declaration = strategy.get('numbered_release', {}).get('contract', {})
    if type(parameters) is not dict or type(declaration) is not dict:
        raise ValueError('Publication reuse requires canonical strategy declarations')
    claimed = (PARAMETER in parameters or INPUT in declaration.get('input_contracts', ())
               or RULE in declaration.get('rule_set_contracts', ()))
    if not claimed:
        return None
    from .backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.trading_runtime.fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    release = numbered_strategy(strategy['strategy_number'])
    if (strategy.get('strategy_number') != release.number
            or strategy.get('revision') != release.number
            or source._revision != release.number
            or strategy.get('strategy_id') != source._strategy_id
            or declaration != release.canonical_payload()):
        raise ValueError('Publication reuse differs from installed sealed release')
    policy = parse_declared_publication_source_reuse(release, parameters.get(PARAMETER))
    factory = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).contract_factory()
    if (type(factory) is not FixedStructuralLotSelectedExitStrategyContract
            or factory.release != release or factory.publication_source_reuse_policy != policy):
        raise ValueError('Publication reuse differs from exact registered typed factory')
    factory.__post_init__()
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    if source.installed_payload != payload:
        raise ValueError('Publication installed payload changed during selection')
    return policy

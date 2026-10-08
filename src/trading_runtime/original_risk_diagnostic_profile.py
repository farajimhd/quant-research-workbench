"""Explicit declared diagnostic capability, never inferred from installed grants."""
from .confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy


def validate_original_risk_profile(automatic_ladder,entry_spread_risk,policy):
    if policy is None:return
    if (automatic_ladder is not False or entry_spread_risk is not False
            or type(policy) is not ConfirmedOriginalRiskPolicy):
        raise ValueError('Original-risk diagnostic profile needs its exclusive exact typed policy')
    policy.__post_init__()


def declared_fixed_runner_options(configuration):
    """Validate actual prepared configuration before selecting a principal."""
    from src.backend.backtest_declared_ladder_plan import automatic_policy
    from .squeeze_ladder_geometry import declared_ladder_runner_options
    if automatic_policy(configuration) is not None:
        return declared_ladder_runner_options(configuration)
    from .numbered_fixed_strategy import numbered_fixed_strategy
    strategy=configuration['strategy']
    contract=numbered_fixed_strategy(strategy.get('strategy_number',strategy['revision']))
    policy=contract.confirmed_original_risk_policy
    if policy is not None:
        from src.backend.backtest_strategy_one_configuration import is_numbered_fixed_configuration
        if not is_numbered_fixed_configuration(configuration):
            raise ValueError('Diagnostic runner profile requires exact prepared configuration')
        validate_original_risk_profile(False,False,policy)
        return {'confirmed_original_risk_policy':policy}
    return {'entry_spread_risk':True} if contract.entry_spread_risk_policy is not None else {}


def declared_contract_runner_options(contract, *, owner=None, publisher=None, run_id=None):
    """Confirmed checkpoints retain the manager's exact installed typed contract."""
    from .numbered_fixed_strategy import numbered_fixed_strategy
    actual=numbered_fixed_strategy(contract.strategy_number)
    if contract!=actual:
        raise ValueError('Checkpoint runner profile differs from installed contract')
    from .strategy_registry import numbered_strategy, OPERATION_CHECKPOINT_READER_RULE
    rule = OPERATION_CHECKPOINT_READER_RULE
    release = numbered_strategy(actual.strategy_number)
    rules = release.rule_set_contracts
    if rule in rules:
        from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
        from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        if (rules.count(rule) != 1
                or type(owner) is not NativeFixedStructuralLotManagement
                or type(publisher) is not BacktestTypedJournalPublisher):
            raise ValueError('Checkpoint reader lacks exact operation ownership')
        operation = owner.operation
        source = operation.source
        profile = require_fixed_structural_lot_profile(
            getattr(publisher.writer._client, 'fixed_structural_lot_profile', None))
        if (profile.operation is not operation
                or owner.publisher is not publisher
                or getattr(publisher, '_fixed_lot_source', None) is not source
                or source.run_id != run_id
                or source.price_authority is not publisher._first_price_source
                or source.installed_payload['strategy']['strategy_number'] != actual.strategy_number
                or source.installed_payload['strategy']['numbered_release']['contract']
                   != release.canonical_payload()):
            raise ValueError('Checkpoint reader profile differs from exact owner/source/run')
        return {'fixed_structural_lot_profile': profile}
    if actual.confirmed_original_risk_policy is not None:
        return {'confirmed_original_risk_policy':actual.confirmed_original_risk_policy}
    return {'entry_spread_risk':True} if actual.entry_spread_risk_policy is not None else {}

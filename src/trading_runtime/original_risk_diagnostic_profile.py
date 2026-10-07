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


def declared_contract_runner_options(contract):
    """Confirmed checkpoints retain the manager's exact installed typed contract."""
    from .numbered_fixed_strategy import numbered_fixed_strategy
    actual=numbered_fixed_strategy(contract.strategy_number)
    if contract!=actual:
        raise ValueError('Checkpoint runner profile differs from installed contract')
    if actual.confirmed_original_risk_policy is not None:
        return {'confirmed_original_risk_policy':actual.confirmed_original_risk_policy}
    return {'entry_spread_risk':True} if actual.entry_spread_risk_policy is not None else {}

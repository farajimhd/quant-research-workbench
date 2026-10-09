"""Immutable management successor declaring full installed native preparation."""
from .strategy_one_hundred_two_release import release_contract as prior_release
from .fixed_structural_lot_release_v22 import INPUT, RULE
from .strategy_registry import NumberedStrategyRelease

BEHAVIOR = ('Preserve every Strategy102 trading parameter, economics, decision clock, '
    'native preparation and management reuse declaration. Run full original historical '
    'authority loaders outside the current decision roster cache while retaining source '
    'ownership, frontier guards, cold verification and exact cleanup. Direct decision '
    'reads still reject foreign ownership. Backtest only; no financial acceptance.')


def release_contract():
    prior = prior_release()
    values = dict(number=103, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=103, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def derive_strategy_one_hundred_three_configuration(source, **approval):
    from .fixed_structural_lot_release_v22 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_two_release import derive_strategy_one_hundred_two_configuration
    return derive_fixed_structural_lot_release(source,
        inherited_derive=derive_strategy_one_hundred_two_configuration,
        inherited_release=prior_release(), release=release_contract(), **approval)


def verify_prepared_strategy_one_hundred_three_configuration(parent, payload):
    from .fixed_structural_lot_release_v22 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_two_release import derive_strategy_one_hundred_two_configuration
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        inherited_derive=derive_strategy_one_hundred_two_configuration,
        inherited_release=prior_release(), release=release_contract())

"""Immutable management successor declaring full installed native preparation."""
from .strategy_one_hundred_one_release import release_contract as prior_release
from .fixed_lot_management_native_preparation import INPUT, RULE
from .strategy_registry import NumberedStrategyRelease

BEHAVIOR = ('Preserve every Strategy101 trading parameter, management reuse policy, economics, '
    'entry, additions, reentry, sizing, protection, costs and decision clock. Declare complete '
    'native installed management-source preparation for nonempty and positive empty horizons, '
    'including cold resume. Require exact installed and parent certificates, complete inherited '
    'manifest rederivation, registered factory, source proof and issued operation ownership. '
    'Missing, foreign or changed authority fails closed. Backtest only; no financial acceptance.')


def release_contract():
    prior = prior_release()
    values = dict(number=102, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=102, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def derive_strategy_one_hundred_two_configuration(source, **approval):
    from .fixed_structural_lot_release_v21 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_one_release import derive_strategy_one_hundred_one_configuration
    return derive_fixed_structural_lot_release(source,
        inherited_derive=derive_strategy_one_hundred_one_configuration,
        inherited_release=prior_release(), release=release_contract(), **approval)


def verify_prepared_strategy_one_hundred_two_configuration(parent, payload):
    from .fixed_structural_lot_release_v21 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_one_release import derive_strategy_one_hundred_one_configuration
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        inherited_derive=derive_strategy_one_hundred_one_configuration,
        inherited_release=prior_release(), release=release_contract())

"""Immutable management successor declaring full installed native preparation."""
from .strategy_one_hundred_four_release import release_contract as prior_release
from .fixed_structural_lot_release_v24 import INPUT, RULES
from .strategy_registry import NumberedStrategyRelease

BEHAVIOR = ('Preserve every Strategy104 trading parameter, decision clock, sizing, '
    'cost, cash, fill and OCA rule. Reconstruct independent repaired-order metadata '
    'from causally fenced creation acknowledgements and only amendments preceding '
    'creation. Later per-lot stop amendments and retirement observations do not '
    'rewrite original order lineage. Retire repair bindings only after exact '
    'cancelled broker readback with unchanged fills; a submission response '
    'alone cannot authorize retirement. Never use raw metadata as authority. '
    'Backtest only; no profitability or full-session speed acceptance.')


def release_contract():
    prior = prior_release()
    values = dict(number=105, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=105, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, *RULES), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def derive_strategy_one_hundred_five_configuration(source, **approval):
    from .fixed_structural_lot_release_v24 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_four_release import derive_strategy_one_hundred_four_configuration
    return derive_fixed_structural_lot_release(source,
        inherited_derive=derive_strategy_one_hundred_four_configuration,
        inherited_release=prior_release(), release=release_contract(), **approval)


def verify_prepared_strategy_one_hundred_five_configuration(parent, payload):
    from .fixed_structural_lot_release_v24 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_four_release import derive_strategy_one_hundred_four_configuration
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        inherited_derive=derive_strategy_one_hundred_four_configuration,
        inherited_release=prior_release(), release=release_contract())

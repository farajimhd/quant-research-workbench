"""Immutable management successor declaring full installed native preparation."""
from .strategy_one_hundred_three_release import release_contract as prior_release
from .fixed_structural_lot_release_v23 import INPUT, RULE
from .strategy_registry import NumberedStrategyRelease

BEHAVIOR = ('Preserve every Strategy103 trading parameter, economics, decision clock, '
    'native preparation, original-authority isolation and declared management reuse. '
    'Verify normalized entry units with issued exact structural content guards instead '
    'of rebuilding their JSON images. Every mutable descendant is reread on every check; '
    'source, method-code, ownership, journal-frontier, cash, fills and OCA verification '
    'remain mandatory. Backtest only; no profitability or full-session speed acceptance.')


def release_contract():
    prior = prior_release()
    values = dict(number=104, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=104, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def derive_strategy_one_hundred_four_configuration(source, **approval):
    from .fixed_structural_lot_release_v23 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_three_release import derive_strategy_one_hundred_three_configuration
    return derive_fixed_structural_lot_release(source,
        inherited_derive=derive_strategy_one_hundred_three_configuration,
        inherited_release=prior_release(), release=release_contract(), **approval)


def verify_prepared_strategy_one_hundred_four_configuration(parent, payload):
    from .fixed_structural_lot_release_v23 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_three_release import derive_strategy_one_hundred_three_configuration
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        inherited_derive=derive_strategy_one_hundred_three_configuration,
        inherited_release=prior_release(), release=release_contract())

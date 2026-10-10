"""Draft source-only verification reuse; admission requires independent closure."""
from .strategy_one_hundred_eight_release import release_contract as prior_release
from .strategy_registry import NumberedStrategyRelease
from .first_inventory_source_reuse_policy import INPUT, RULE, FirstInventorySourceReusePolicy


def first_inventory_source_reuse_policy():
    return FirstInventorySourceReusePolicy(32, True, True)


BEHAVIOR = (
    'Preserve every Strategy108 activation, entry, quota, sizing, exposure, cost, '
    'target, structural cadence and exit rule. A first complete OMS inventory '
    'read in an exact issued initial-held or proposal operation may retain only '
    'its already verified publication-context source bindings. Decision and '
    'historical-prefix inventory caches remain disabled during that read. '
    'Complete journal, lineage, quantity, admission and prefix verification '
    'remain mandatory. Foreign readers and context-budget overflow use the '
    'original independent cold path. Oversized source-retained results are '
    'discarded and reloaded cold without truncation. Backtest only.')


def release_contract():
    prior = prior_release()
    values = dict(number=109, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=109, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def derive_strategy_one_hundred_nine_configuration(parent, **approval):
    from .fixed_structural_lot_release_v28 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_eight_release import derive_strategy_one_hundred_eight_configuration
    return derive_fixed_structural_lot_release(parent,
        inherited_derive=derive_strategy_one_hundred_eight_configuration,
        inherited_release=prior_release(), release=release_contract(),
        first_inventory_source_reuse_policy=first_inventory_source_reuse_policy(), **approval)


def verify_prepared_strategy_one_hundred_nine_configuration(parent, payload):
    from .fixed_structural_lot_release_v28 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_eight_release import derive_strategy_one_hundred_eight_configuration
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        inherited_derive=derive_strategy_one_hundred_eight_configuration,
        inherited_release=prior_release(), release=release_contract(),
        first_inventory_source_reuse_policy=first_inventory_source_reuse_policy())

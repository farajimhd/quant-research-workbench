"""Unpublished writer-source reuse successor; no financial acceptance claimed."""
from .strategy_one_hundred_ten_release import release_contract as prior_release
from .strategy_registry import NumberedStrategyRelease
from .publication_source_reuse_policy import INPUT, RULE, PublicationSourceReusePolicy

BEHAVIOR = (
    'Preserve every Strategy110 trading, sizing, cash, exposure, cost and exit rule. '
    'Retain one complete source replay per fixed-lot writer publication only while '
    'typed input/output, causal source facts and issued factory dependencies remain '
    'unchanged. Installed admission, database scalar hashes, family relationships, '
    'Keeper fencing and cold readback remain mandatory. No cross-publication or '
    'foreign-context reuse. Backtest only; full-session speed and profit unproved.')


def publication_source_reuse_policy():
    return PublicationSourceReusePolicy(max_image_bytes=67108864)


def release_contract():
    prior = prior_release()
    values = dict(number=111, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=111, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def derive_strategy_one_hundred_eleven_configuration(parent, **approval):
    from .fixed_structural_lot_release_v30 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_ten_release import derive_strategy_one_hundred_ten_configuration
    return derive_fixed_structural_lot_release(parent,
        inherited_derive=derive_strategy_one_hundred_ten_configuration,
        inherited_release=prior_release(), release=release_contract(),
        publication_source_reuse_policy=publication_source_reuse_policy(), **approval)


def verify_prepared_strategy_one_hundred_eleven_configuration(parent, payload):
    from .fixed_structural_lot_release_v30 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_ten_release import derive_strategy_one_hundred_ten_configuration
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        inherited_derive=derive_strategy_one_hundred_ten_configuration,
        inherited_release=prior_release(), release=release_contract(),
        publication_source_reuse_policy=publication_source_reuse_policy())

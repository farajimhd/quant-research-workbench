"""Prepared successor; no registration, source approval or financial result."""
from .strategy_ninety_eight_release import release_contract as prior_release
from .selected_exit_publication_policy import INPUT, RULE, SelectedExitPublicationPolicy
from .strategy_registry import NumberedStrategyRelease

SELECTED_EXIT_POLICY = SelectedExitPublicationPolicy(1)
BEHAVIOR = (
    'Preserve Strategy98 activation, entries, additions, reentries, sizing, aggregate exposure, '
    'cash, trading costs, fixed-lot protection, decision clocks and complete market transport. '
    'Publish selected-entry exits through the original typed writer with the exact installed '
    'source, verified committed predecessor and identical price authority, including inherited '
    'followthrough without an original-risk diagnostic. Retain complete entry ancestry, issued '
    'ownership, source certification, child validation and cold recovery. Missing or foreign '
    'context fails closed. Prepared only; no registration, source approval, native integration, '
    'financial result or full-session speed acceptance.'
)


def release_contract():
    prior = prior_release()
    values = dict(number=99, executor_strategy_id=prior.executor_strategy_id,
                  executor_revision=99, evaluation_interval=prior.evaluation_interval,
                  input_contracts=(*prior.input_contracts, INPUT),
                  rule_set_contracts=(*prior.rule_set_contracts, RULE),
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def _compiler_arguments():
    from . import strategy_forty_two_release as parent
    from .strategy_ninety_eight_release import (
        VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY, OWNED_SNAPSHOT_POLICY,
        EMPTY_CONFIRMATION_POLICY, COMPLETE_MARKET_POLICY,
    )
    return dict(parent_release=parent.release_contract(), release=release_contract(),
                reuse_policy=VALIDATION_REUSE_POLICY,
                projection_reuse_policy=PROJECTION_REUSE_POLICY,
                owned_snapshot_policy=OWNED_SNAPSHOT_POLICY,
                empty_confirmation_policy=EMPTY_CONFIRMATION_POLICY,
                complete_market_policy=COMPLETE_MARKET_POLICY,
                selected_exit_policy=SELECTED_EXIT_POLICY)


def _verify_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .strategy_forty_two_release import verify_strategy_forty_two_manifest
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Selected exit comparison requires certified Strategy42 parent')
    verify_strategy_forty_two_manifest(source.payload['strategy'])


def derive_strategy_ninety_nine_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    from .fixed_structural_lot_release_v19 import derive_fixed_structural_lot_release
    from .fixed_structural_lot_policy import FixedStructuralLotPolicy
    _verify_parent(source)
    return derive_fixed_structural_lot_release(source, **_compiler_arguments(),
        policy=FixedStructuralLotPolicy().payload(), approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)


def verify_prepared_strategy_ninety_nine_configuration(parent, payload):
    from .fixed_structural_lot_release_v19 import verify_prepared_fixed_structural_lot_release
    from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
    _verify_parent(parent)
    if parse_fixed_structural_lot_policy(payload['strategy']['parameters'].get(
            'fixed_structural_lot_policy')) != FixedStructuralLotPolicy():
        raise ValueError('Prepared comparison differs from declared fixed lot policy')
    return verify_prepared_fixed_structural_lot_release(parent, payload, **_compiler_arguments())

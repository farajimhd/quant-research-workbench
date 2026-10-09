"""Prepared comparison specification; no registration or installed approval."""
from .strategy_ninety_five_release import release_contract as prior_release, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY
from .projected_configuration_reuse_policy import parse_projected_configuration_reuse_policy
from .owned_scalar_snapshot_policy import OwnedScalarSnapshotPolicy, parse_owned_scalar_snapshot_policy
from .empty_protection_confirmation_policy import INPUT, RULE, EmptyProtectionConfirmationPolicy, parse_empty_protection_confirmation_policy
from .strategy_registry import NumberedStrategyRelease
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from . import strategy_forty_two_release as parent_release

OWNED_SNAPSHOT_POLICY = OwnedScalarSnapshotPolicy(2, VALIDATION_REUSE_POLICY.max_rows, 64 * 1024 * 1024)
EMPTY_CONFIRMATION_POLICY = EmptyProtectionConfirmationPolicy(1)
BEHAVIOR = ('Inherit unchanged Strategy95 trading economics, sizing, costs, entries, reentries, fixed lots, exits and recovery. Select commandless protection confirmation only through the complete issued source and exact installed factory. Omit only unused effective protection acknowledgement history when there are zero commands and no recovery context. Preserve fresh source, writer fence, prefix, live legs, residual ownership, stop ceiling and financial quantity checks. Nonempty commands and recovery retain full history reads. Prepared comparison only; no registration, installed source approval, publication, native speed or financial result.')


def release_contract():
    prior = prior_release()
    values = dict(number=96, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=96, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def derive_strategy_ninety_six_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .fixed_structural_lot_release_v17 import derive_fixed_structural_lot_release
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Projected-node comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(source.payload['strategy'])
    return derive_fixed_structural_lot_release(source, parent_release=parent_release.release_contract(),
        release=release_contract(), policy=FixedStructuralLotPolicy().payload(),
        reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY,
        owned_snapshot_policy=OWNED_SNAPSHOT_POLICY, empty_confirmation_policy=EMPTY_CONFIRMATION_POLICY,
        approved_code_commit=approved_code_commit, approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference)


def verify_prepared_strategy_ninety_six_configuration(parent, payload):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .fixed_structural_lot_release_v17 import verify_prepared_fixed_structural_lot_release
    from .packet_validation_reuse_policy import parse_packet_validation_reuse_policy
    if type(parent) is not CertifiedStrategyOneConfiguration or parent.strategy_number != 42:
        raise ValueError('Projected-node comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(parent.payload['strategy'])
    parameters = payload['strategy']['parameters']
    if (parse_fixed_structural_lot_policy(parameters.get('fixed_structural_lot_policy')) != FixedStructuralLotPolicy()
            or parse_packet_validation_reuse_policy(parameters.get('packet_validation_reuse_policy')) != VALIDATION_REUSE_POLICY
            or parse_projected_configuration_reuse_policy(parameters.get('projected_configuration_reuse_policy')) != PROJECTION_REUSE_POLICY
            or parse_owned_scalar_snapshot_policy(parameters.get('owned_scalar_snapshot_policy')) != OWNED_SNAPSHOT_POLICY
            or parse_empty_protection_confirmation_policy(parameters.get('empty_protection_confirmation_policy')) != EMPTY_CONFIRMATION_POLICY):
        raise ValueError('Prepared comparison differs from declared lot/reuse policies')
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        parent_release=parent_release.release_contract(), release=release_contract(),
        reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY, owned_snapshot_policy=OWNED_SNAPSHOT_POLICY, empty_confirmation_policy=EMPTY_CONFIRMATION_POLICY)

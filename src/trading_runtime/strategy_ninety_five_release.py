"""Prepared comparison specification; no registration or installed approval."""
from .strategy_ninety_four_release import release_contract as prior_release, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY
from .projected_configuration_reuse_policy import parse_projected_configuration_reuse_policy
from .owned_scalar_snapshot_policy import INPUT, RULE, OwnedScalarSnapshotPolicy, parse_owned_scalar_snapshot_policy
from .strategy_registry import NumberedStrategyRelease
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from . import strategy_forty_two_release as parent_release

OWNED_SNAPSHOT_POLICY = OwnedScalarSnapshotPolicy(2, VALIDATION_REUSE_POLICY.max_rows, 64 * 1024 * 1024)
BEHAVIOR = ('Inherit unchanged Strategy94 trading economics, sizing, costs, entries, reentries, lots, exits and recovery. Add declared privately owned projected-node row snapshots only. Preserve complete exact scalar keys and float bits, root and lot snapshots, source/request/semantic batch/full replay checks. Retain at most two owned groups, 30033 rows and 64 MiB; unknown or evicted groups use ordinary snapshots. Prepared comparison only; no installed source approval, publication, native speed or financial result.')


def release_contract():
    prior = prior_release()
    values = dict(number=95, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=95, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def derive_strategy_ninety_five_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .fixed_structural_lot_release_v16 import derive_fixed_structural_lot_release
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Projected-node comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(source.payload['strategy'])
    return derive_fixed_structural_lot_release(source, parent_release=parent_release.release_contract(),
        release=release_contract(), policy=FixedStructuralLotPolicy().payload(),
        reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY,
        owned_snapshot_policy=OWNED_SNAPSHOT_POLICY,
        approved_code_commit=approved_code_commit, approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference)


def verify_prepared_strategy_ninety_five_configuration(parent, payload):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .fixed_structural_lot_release_v16 import verify_prepared_fixed_structural_lot_release
    from .packet_validation_reuse_policy import parse_packet_validation_reuse_policy
    if type(parent) is not CertifiedStrategyOneConfiguration or parent.strategy_number != 42:
        raise ValueError('Projected-node comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(parent.payload['strategy'])
    parameters = payload['strategy']['parameters']
    if (parse_fixed_structural_lot_policy(parameters.get('fixed_structural_lot_policy')) != FixedStructuralLotPolicy()
            or parse_packet_validation_reuse_policy(parameters.get('packet_validation_reuse_policy')) != VALIDATION_REUSE_POLICY
            or parse_projected_configuration_reuse_policy(parameters.get('projected_configuration_reuse_policy')) != PROJECTION_REUSE_POLICY
            or parse_owned_scalar_snapshot_policy(parameters.get('owned_scalar_snapshot_policy')) != OWNED_SNAPSHOT_POLICY):
        raise ValueError('Prepared comparison differs from declared lot/reuse policies')
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        parent_release=parent_release.release_contract(), release=release_contract(),
        reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY, owned_snapshot_policy=OWNED_SNAPSHOT_POLICY)

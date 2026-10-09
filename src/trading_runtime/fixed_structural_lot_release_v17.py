"""Prepare declared commandless protection confirmation with complete parent inheritance.

No registration, installed source certificate or financial admission is issued.
"""
from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE as IDENTITY_RULE, BATCHED_DETAIL_SELECT_RULE as BATCHED_RULE, SELECTED_CHECKPOINT_PRODUCT_RULE as CHECKPOINT_RULE, OPERATION_CHECKPOINT_READER_RULE as READER_RULE
from .decimal_snapshot_readback import RULE as DECIMAL_RULE
from .packet_validation_reuse_policy import (
    INPUT as REUSE_INPUT, RULE as REUSE_RULE, PacketValidationReusePolicy,
    declared_packet_validation_reuse_policy,
)
from .projected_configuration_reuse_policy import (
    INPUT as PROJECTION_REUSE_INPUT, RULE as PROJECTION_REUSE_RULE,
    ProjectedConfigurationReusePolicy, declared_projected_configuration_reuse_policy,
)
from .owned_scalar_snapshot_policy import INPUT as OWNED_INPUT, RULE as OWNED_RULE, OwnedScalarSnapshotPolicy, declared_owned_scalar_snapshot_policy
from .empty_protection_confirmation_policy import INPUT as EMPTY_INPUT, RULE as EMPTY_RULE, EmptyProtectionConfirmationPolicy, declared_empty_protection_confirmation_policy
from copy import deepcopy
from hashlib import sha256
import re

from .fixed_structural_lot_policy import parse_fixed_structural_lot_policy
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_registry import NumberedStrategyRelease
from .numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from .fixed_structural_lot_interval_validator_v2 import VALIDATOR_RULE
from src.backend.backtest_fixed_structural_lot_projection_runtime_authority import PROJECTION_RULE
from .fixed_structural_lot_native_source_rule import NATIVE_SOURCE_RULE
from .fixed_structural_lot_causal_clock import CLOCK_RULE
from .independent_lot_initial_stop_lineage import RULE as INITIAL_STOP_RULE
from .fixed_structural_lot_warm_proof import RULE as WARM_RULE


def derive_fixed_structural_lot_release(
        parent, *, parent_release, release, policy, reuse_policy, projection_reuse_policy, owned_snapshot_policy, empty_confirmation_policy, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    """Pure preparation under an explicit parent and reviewed own release.

    Returned nodes are publication inputs, not a source certificate. The
    installed operation independently reloads both immutable configurations.
    """
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from src.backend.backtest_fixed_structural_lot_native_v13 import (
        ENTRY_RULE, SOURCE_INPUT, parent_reference,
    )
    if (type(parent) is not CertifiedStrategyOneConfiguration
            or type(parent_release) is not NumberedStrategyRelease
            or type(release) is not NumberedStrategyRelease):
        raise ValueError('Complete parent certificate and exact release contracts required')
    parent_release.verify()
    release.verify()
    if (parent_release.number != parent.strategy_number
            or release.number == parent_release.number
            or release.executor_revision != release.number
            or release.executor_strategy_id != parent_release.executor_strategy_id
            or release.evaluation_interval != parent_release.evaluation_interval
            or release.input_contracts != (*parent_release.input_contracts, DECLARED_FIXED_ADAPTER, SOURCE_INPUT, REUSE_INPUT, PROJECTION_REUSE_INPUT, OWNED_INPUT, EMPTY_INPUT)
            or release.rule_set_contracts != (*parent_release.rule_set_contracts, ENTRY_RULE, VALIDATOR_RULE, PROJECTION_RULE, NATIVE_SOURCE_RULE, CLOCK_RULE, INITIAL_STOP_RULE, WARM_RULE, IDENTITY_RULE, BATCHED_RULE, CHECKPOINT_RULE, READER_RULE, DECIMAL_RULE, REUSE_RULE, PROJECTION_REUSE_RULE, OWNED_RULE, EMPTY_RULE)):
        raise ValueError('Lot release must preserve exact parent contracts')
    if type(reuse_policy) is not PacketValidationReusePolicy:
        raise ValueError('Explicit typed packet validation reuse policy required')
    declared_packet_validation_reuse_policy(release, reuse_policy.payload())
    if type(projection_reuse_policy) is not ProjectedConfigurationReusePolicy:
        raise ValueError("Explicit typed projected-node reuse policy required")
    declared_projected_configuration_reuse_policy(release, projection_reuse_policy.payload())
    if type(owned_snapshot_policy) is not OwnedScalarSnapshotPolicy:
        raise ValueError('Explicit typed owned snapshot policy required')
    declared_owned_scalar_snapshot_policy(release, owned_snapshot_policy.payload())
    if owned_snapshot_policy.max_rows != reuse_policy.max_rows:
        raise ValueError('Ownership and packet validation bounds differ')
    if type(empty_confirmation_policy) is not EmptyProtectionConfirmationPolicy:
        raise ValueError('Explicit typed empty confirmation policy required')
    declared_empty_protection_confirmation_policy(release, empty_confirmation_policy.payload())
    payload = deepcopy(parent.payload)
    if (sha256(canonical_json(payload).encode()).hexdigest() != parent.payload_hash
            or node_hash(encode_nodes(payload)) != parent.node_hash
            or payload.get('assignments')):
        raise ValueError('Parent nodes differ or contain mutable assignments')
    if (not isinstance(approved_code_commit, str)
            or not re.fullmatch(r'[0-9a-f]{40}', approved_code_commit)
            or not isinstance(approved_code_fingerprint, str)
            or not re.fullmatch(r'[0-9a-f]{64}', approved_code_fingerprint)
            or type(approval_reference) is not str or not approval_reference.strip()):
        raise ValueError('Reviewed source approval identity required')
    selected_policy = parse_fixed_structural_lot_policy(policy)
    reference = parent_reference(parent)
    strategy = payload['strategy']
    if (strategy.get('strategy_id') != parent_release.executor_strategy_id
            or strategy.get('revision') != parent_release.executor_revision
            or strategy.get('execution_interval') != parent_release.evaluation_interval):
        raise ValueError('Parent execution identity differs from its release')
    if (strategy['numbered_release'].get('contract') != parent_release.canonical_payload()
            or strategy['numbered_release'].get('approved_digest') != parent_release.approved_digest):
        raise ValueError('Parent manifest differs from its complete release')
    parameters = strategy['parameters']
    if {'fixed_structural_lot_policy', 'fixed_structural_lot_parent', 'packet_validation_reuse_policy', 'projected_configuration_reuse_policy'} & set(parameters):
        raise ValueError('Parent already contains selected lot declaration')
    parameters.update(fixed_structural_lot_policy=selected_policy.payload(),
                      fixed_structural_lot_parent=reference,
                      packet_validation_reuse_policy=reuse_policy.payload(),
                      projected_configuration_reuse_policy=projection_reuse_policy.payload(),
                      owned_scalar_snapshot_policy=owned_snapshot_policy.payload(),
                      empty_protection_confirmation_policy=empty_confirmation_policy.payload())
    manifest = deepcopy(strategy['numbered_release'])
    manifest.pop('manifest_hash', None)
    manifest.update(contract=release.canonical_payload(),
                    approved_digest=release.approved_digest,
                    approved_code_commit=approved_code_commit,
                    approved_code_fingerprint=approved_code_fingerprint,
                    approval_reference=approval_reference,
                    publication_mode='backtest_only',
                    source_revision_id=reference['revision_id'],
                    source_payload_hash=reference['payload_hash'])
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    number = release.number
    profile_id = f'strategy-one-{number}'
    strategy.update(strategy_number=number, revision=number, profile_id=profile_id,
                    profile_revision=number, name=f'Early Squeeze Strategy {number}',
                    numbered_release=manifest)
    payload['strategy_profile'].update(profile_id=profile_id, revision=number,
        definition_revision=number, name=f'Early Squeeze Strategy {number}',
        description=release.behavior_specification)
    payload['run_plan'].update(profile_id=profile_id, name=f'Strategy {number} Backtest',
        description=release.behavior_specification)
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'fixed-structural-lots-from:{reference["revision_id"]}',
                source_candidate_hash=parent.payload_hash, payload=payload, nodes=nodes,
                payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
                node_hash=node_hash(nodes), node_count=len(nodes))


def verify_prepared_fixed_structural_lot_release(parent, payload, *, parent_release, release,
                                               reuse_policy, projection_reuse_policy, owned_snapshot_policy, empty_confirmation_policy):
    """Reconstruct the entire declared tree, including every manifest field.

    Source approval and installed publication remain independently verified
    by their owners; this function proves only exact compiler derivation.
    """
    if type(payload) is not dict:
        raise ValueError('Complete prepared configuration required')
    try:
        strategy = payload['strategy']
        manifest = strategy['numbered_release']
        policy = strategy['parameters']['fixed_structural_lot_policy']
        prepared = derive_fixed_structural_lot_release(parent,
            parent_release=parent_release, release=release, policy=policy,
            reuse_policy=reuse_policy, projection_reuse_policy=projection_reuse_policy,
            owned_snapshot_policy=owned_snapshot_policy, empty_confirmation_policy=empty_confirmation_policy,
            approved_code_commit=manifest['approved_code_commit'],
            approved_code_fingerprint=manifest['approved_code_fingerprint'],
            approval_reference=manifest['approval_reference'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Prepared declaration is incomplete') from exc
    if canonical_json(prepared['payload']) != canonical_json(payload):
        raise ValueError('Prepared declaration differs from complete parent derivation')
    return prepared

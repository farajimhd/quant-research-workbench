"""Complete inherited compiler tree with an explicit management-reuse extension."""
from copy import deepcopy
from hashlib import sha256

from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .fixed_lot_management_reuse_policy import (
    INPUT, RULE, FixedLotManagementReusePolicy, declared_management_reuse_policy,
)


def derive_fixed_structural_lot_release(parent, *, inherited_arguments, release,
        management_reuse_policy, policy, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    from .fixed_structural_lot_release_v19 import derive_fixed_structural_lot_release as inherited_derive
    inherited = inherited_arguments['release']
    inherited.verify()
    release.verify()
    if (release.number == inherited.number
            or release.executor_strategy_id != inherited.executor_strategy_id
            or release.executor_revision != release.number
            or release.evaluation_interval != inherited.evaluation_interval
            or release.input_contracts != (*inherited.input_contracts, INPUT)
            or release.rule_set_contracts != (*inherited.rule_set_contracts, RULE)
            or type(management_reuse_policy) is not FixedLotManagementReusePolicy):
        raise ValueError('Management reuse must preserve the complete inherited release')
    declared_management_reuse_policy(release, management_reuse_policy.payload())
    prepared = inherited_derive(parent, **inherited_arguments, policy=policy,
        approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)
    # The complete prior compiled tree is the authority. Only declared identity,
    # behavior description and the new typed policy may differ in this successor.
    payload = deepcopy(prepared['payload'])
    strategy = payload['strategy']
    strategy['parameters']['management_reuse_policy'] = management_reuse_policy.payload()
    manifest = strategy['numbered_release']
    manifest.update(contract=release.canonical_payload(), approved_digest=release.approved_digest)
    manifest['manifest_hash'] = sha256(canonical_json(
        {k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    number = release.number
    profile_id = f'strategy-one-{number}'
    strategy.update(strategy_number=number, revision=number, profile_id=profile_id,
        profile_revision=number, name=f'Early Squeeze Strategy {number}')
    payload['strategy_profile'].update(profile_id=profile_id, revision=number,
        definition_revision=number, name=f'Early Squeeze Strategy {number}',
        description=release.behavior_specification)
    payload['run_plan'].update(profile_id=profile_id, name=f'Strategy {number} Backtest',
        description=release.behavior_specification)
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=prepared['source_candidate_id'],
        source_candidate_hash=prepared['source_candidate_hash'], payload=payload, nodes=nodes,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes))


def verify_prepared_fixed_structural_lot_release(parent, payload, *, inherited_arguments,
        release, management_reuse_policy):
    if type(payload) is not dict:
        raise ValueError('Complete management reuse configuration required')
    try:
        manifest = payload['strategy']['numbered_release']
        prepared = derive_fixed_structural_lot_release(parent,
            inherited_arguments=inherited_arguments, release=release,
            management_reuse_policy=management_reuse_policy,
            policy=payload['strategy']['parameters']['fixed_structural_lot_policy'],
            approved_code_commit=manifest['approved_code_commit'],
            approved_code_fingerprint=manifest['approved_code_fingerprint'],
            approval_reference=manifest['approval_reference'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Management reuse configuration is incomplete') from exc
    if canonical_json(prepared['payload']) != canonical_json(payload):
        raise ValueError('Management reuse differs from complete inherited compiler tree')
    return prepared

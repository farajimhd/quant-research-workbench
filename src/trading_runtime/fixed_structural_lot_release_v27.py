"""Exact inherited native tree plus independent initial/proposal verification reuse declarations."""
from copy import deepcopy
from hashlib import sha256
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes,node_hash
from .initial_held_recovery_reuse_policy import RULE,INPUT,PARAMETER
from .initial_held_recovery_reuse_policy import require_declared_initial_held_reuse
from .proposal_decision_inventory_reuse_policy import INPUT as PROPOSAL_INPUT,RULE as PROPOSAL_RULE,PARAMETER as PROPOSAL_PARAMETER,require_declared_proposal_decision_reuse

def derive_fixed_structural_lot_release(parent,*,inherited_derive,inherited_release,release,initial_held_recovery_reuse_policy,proposal_decision_inventory_reuse_policy,
        approved_code_commit,approved_code_fingerprint,approval_reference):
    inherited_release.verify();release.verify()
    require_declared_initial_held_reuse(release,initial_held_recovery_reuse_policy)
    require_declared_proposal_decision_reuse(release,proposal_decision_inventory_reuse_policy)
    if (release.number==inherited_release.number or release.executor_strategy_id!=inherited_release.executor_strategy_id
            or release.executor_revision!=release.number or release.evaluation_interval!=inherited_release.evaluation_interval
            or release.input_contracts!=(*inherited_release.input_contracts,INPUT,PROPOSAL_INPUT)
            or release.rule_set_contracts!=(*inherited_release.rule_set_contracts,RULE,PROPOSAL_RULE)):
        raise ValueError('Initial-held reuse successor must preserve the exact inherited declaration')
    prior=inherited_derive(parent,approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint,approval_reference=approval_reference)
    payload=deepcopy(prior['payload']);strategy=payload['strategy'];manifest=strategy['numbered_release']
    if PARAMETER in strategy['parameters']:raise ValueError('Inherited initial-held reuse cannot be replaced')
    strategy['parameters'][PARAMETER]=initial_held_recovery_reuse_policy.payload()
    if PROPOSAL_PARAMETER in strategy['parameters']:raise ValueError('Inherited proposal reuse cannot be replaced')
    strategy['parameters'][PROPOSAL_PARAMETER]=proposal_decision_inventory_reuse_policy.payload()
    manifest.update(contract=release.canonical_payload(),approved_digest=release.approved_digest)
    manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    profile=f'strategy-one-{release.number}'
    strategy.update(strategy_number=release.number,revision=release.number,profile_id=profile,
        profile_revision=release.number,name=f'Early Squeeze Strategy {release.number}')
    payload['strategy_profile'].update(profile_id=profile,revision=release.number,definition_revision=release.number,
        name=f'Early Squeeze Strategy {release.number}',description=release.behavior_specification)
    payload['run_plan'].update(profile_id=profile,name=f'Strategy {release.number} Backtest',description=release.behavior_specification)
    nodes=encode_nodes(payload)
    return dict(source_candidate_id=prior['source_candidate_id'],source_candidate_hash=prior['source_candidate_hash'],
        payload=payload,payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),node_hash=node_hash(nodes),node_count=len(nodes))

def verify_prepared_fixed_structural_lot_release(parent,payload,*,inherited_derive,inherited_release,release,initial_held_recovery_reuse_policy,proposal_decision_inventory_reuse_policy):
    try:
        manifest=payload['strategy']['numbered_release']
        expected=derive_fixed_structural_lot_release(parent,inherited_derive=inherited_derive,inherited_release=inherited_release,
            release=release,initial_held_recovery_reuse_policy=initial_held_recovery_reuse_policy,proposal_decision_inventory_reuse_policy=proposal_decision_inventory_reuse_policy,approved_code_commit=manifest['approved_code_commit'],
            approved_code_fingerprint=manifest['approved_code_fingerprint'],approval_reference=manifest['approval_reference'])
    except (KeyError,TypeError) as exc:raise ValueError('Initial-held reuse successor tree is incomplete') from exc
    if canonical_json(payload)!=canonical_json(expected['payload']):raise ValueError('Initial-held reuse successor differs from whole inherited tree')
    return expected

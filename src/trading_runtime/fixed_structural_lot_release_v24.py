"""Complete inherited trading tree with acknowledged repair creation lineage."""
from copy import deepcopy
from hashlib import sha256
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
INPUT = 'independent-lot-repair-creation-history-source@1'
from .independent_lot_repair_creation_lineage import RULE
from .independent_lot_repair_retirement import RULE as RETIREMENT_RULE
RULES = (RULE, RETIREMENT_RULE)


def derive_fixed_structural_lot_release(parent, *, inherited_derive, inherited_release,
        release, approved_code_commit, approved_code_fingerprint, approval_reference):
    inherited_release.verify()
    release.verify()
    if (release.number == inherited_release.number
            or release.executor_strategy_id != inherited_release.executor_strategy_id
            or release.executor_revision != release.number
            or release.evaluation_interval != inherited_release.evaluation_interval
            or release.input_contracts != (*inherited_release.input_contracts, INPUT)
            or release.rule_set_contracts != (*inherited_release.rule_set_contracts, *RULES)):
        raise ValueError('Native preparation must preserve the complete inherited release')
    prior = inherited_derive(parent, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)
    payload = deepcopy(prior['payload'])
    strategy = payload['strategy']
    manifest = strategy['numbered_release']
    manifest.update(contract=release.canonical_payload(), approved_digest=release.approved_digest)
    manifest['manifest_hash'] = sha256(canonical_json(
        {key: value for key, value in manifest.items() if key != 'manifest_hash'}).encode()).hexdigest()
    profile_id = f'strategy-one-{release.number}'
    strategy.update(strategy_number=release.number, revision=release.number,
        profile_id=profile_id, profile_revision=release.number,
        name=f'Early Squeeze Strategy {release.number}')
    payload['strategy_profile'].update(profile_id=profile_id, revision=release.number,
        definition_revision=release.number, name=f'Early Squeeze Strategy {release.number}',
        description=release.behavior_specification)
    payload['run_plan'].update(profile_id=profile_id, name=f'Strategy {release.number} Backtest',
        description=release.behavior_specification)
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=prior['source_candidate_id'],
        source_candidate_hash=prior['source_candidate_hash'], payload=payload,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes))


def verify_prepared_fixed_structural_lot_release(parent, payload, *, inherited_derive,
        inherited_release, release):
    if type(payload) is not dict:
        raise ValueError('Complete native preparation configuration required')
    try:
        manifest = payload['strategy']['numbered_release']
        expected = derive_fixed_structural_lot_release(parent,
            inherited_derive=inherited_derive, inherited_release=inherited_release, release=release,
            approved_code_commit=manifest['approved_code_commit'],
            approved_code_fingerprint=manifest['approved_code_fingerprint'],
            approval_reference=manifest['approval_reference'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Native preparation configuration is incomplete') from exc
    if canonical_json(expected['payload']) != canonical_json(payload):
        raise ValueError('Native preparation differs from complete inherited compiler tree')
    return expected

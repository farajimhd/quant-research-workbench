"""Declared native source factory using the actual shared V4 certification API."""
from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE as IDENTITY_RULE, BATCHED_DETAIL_SELECT_RULE as BATCHED_RULE
from .backtest_fixed_structural_lot_native_v5 import (
    deepcopy, sha256, re, canonical_json, encode_nodes, node_hash,
    certify_numbered_configuration, CertifiedStrategyOneConfiguration,
    numbered_strategy, numbered_strategy_parent, fixed_strategy_executor,
    DECLARED_FIXED_ADAPTER, VALIDATOR_RULE, PROJECTION_RULE, ENTRY_RULE, SOURCE_INPUT,
    _IDENTITY_FIELDS, parent_reference, verify_current_installed_source,
    parse_fixed_structural_lot_policy,
    NativeFixedStructuralLotOperation, _issue_installed_source, require_installed_source,
)
from src.trading_runtime.fixed_structural_lot_native_source_rule import NATIVE_SOURCE_RULE
from src.trading_runtime.fixed_structural_lot_causal_clock import CLOCK_RULE
from src.trading_runtime.fixed_structural_lot_warm_proof import RULE as WARM_RULE
from src.trading_runtime.independent_lot_initial_stop_lineage import RULE as INITIAL_STOP_RULE


def verify_installed_configuration(parent, own, release, parent_release):
    """Additional semantic binding; authentic certificates are loaded below."""
    if type(parent) is not CertifiedStrategyOneConfiguration or type(own) is not CertifiedStrategyOneConfiguration:
        raise ValueError('Exact complete installed and parent certificates required')
    release.verify()
    parent_release.verify()
    if release.number != own.strategy_number or release.executor_revision != own.strategy_number or release.executor_strategy_id != own.payload['strategy']['strategy_id'] or (own.strategy_number == parent.strategy_number) or (release.input_contracts != (*parent_release.input_contracts, DECLARED_FIXED_ADAPTER, SOURCE_INPUT)) or (release.rule_set_contracts != (*parent_release.rule_set_contracts, ENTRY_RULE, VALIDATOR_RULE, PROJECTION_RULE, NATIVE_SOURCE_RULE, CLOCK_RULE, INITIAL_STOP_RULE, WARM_RULE, IDENTITY_RULE, BATCHED_RULE)):
        raise ValueError('Own lot release lacks exact inherited semantic bindings')
    strategy = own.payload['strategy']
    profile_id = f'strategy-one-{release.number}'
    for section, key, wanted in (('strategy', 'strategy_number', release.number), ('strategy', 'revision', release.number), ('strategy', 'profile_revision', release.number), ('strategy', 'profile_id', profile_id), ('strategy_profile', 'profile_id', profile_id), ('strategy_profile', 'revision', release.number), ('strategy_profile', 'definition_revision', release.number), ('run_plan', 'profile_id', profile_id)):
        value = own.payload.get(section, {}).get(key)
        if type(value) is not type(wanted) or value != wanted:
            raise ValueError('Own masked execution identity differs from registered release')
    for certificate in (parent, own):
        if sha256(canonical_json(certificate.payload).encode()).hexdigest() != certificate.payload_hash or node_hash(encode_nodes(certificate.payload)) != certificate.node_hash:
            raise ValueError('Full installed configuration nodes/content differ')
    params = own.payload['strategy']['parameters']
    policy = parse_fixed_structural_lot_policy(params.get('fixed_structural_lot_policy'))
    if params.get('fixed_structural_lot_parent') != parent_reference(parent):
        raise ValueError('Own lot declaration has foreign parent/source approval')
    inherited = deepcopy(own.payload)
    inherited['strategy']['parameters'].pop('fixed_structural_lot_policy')
    inherited['strategy']['parameters'].pop('fixed_structural_lot_parent')
    for section, fields in _IDENTITY_FIELDS.items():
        if section not in parent.payload or section not in inherited:
            raise ValueError('Full own published identity sections required')
        for key in fields:
            if key in parent.payload[section]:
                inherited[section][key] = deepcopy(parent.payload[section][key])
            else:
                inherited[section].pop(key, None)
    if canonical_json(inherited) != canonical_json(parent.payload):
        raise ValueError('Own lot declaration changes inherited entry/economics')
    manifest = own.payload['strategy']['numbered_release']
    if type(manifest) is not dict or manifest.get('contract') != release.canonical_payload() or manifest.get('approved_digest') != release.approved_digest or (manifest.get('source_revision_id') != parent.revision()['revision_id']) or (manifest.get('source_payload_hash') != parent.payload_hash):
        raise ValueError('Own release manifest differs from complete registered declaration')
    if not re.fullmatch('[0-9a-f]{40}', str(manifest.get('approved_code_commit', ''))) or not re.fullmatch('[0-9a-f]{64}', str(manifest.get('approved_code_fingerprint', ''))):
        raise ValueError('Own approved source manifest identity is malformed')
    return policy

def load_installed_configuration(client, *, number, parent):
    """Only actual immutable loader+registered release+full source proof issue it."""
    if type(number) is not int or number <= 0 or numbered_strategy_parent(number) != parent.strategy_number:
        raise ValueError('Installed lot declaration has different registered parent')
    release = numbered_strategy(number)
    own = certify_numbered_configuration(client, number)
    parent_release = numbered_strategy(parent.strategy_number)
    from src.trading_runtime.fixed_structural_lot_release_v10 import verify_prepared_fixed_structural_lot_release
    verify_prepared_fixed_structural_lot_release(parent, own.payload, parent_release=parent_release, release=release)
    policy = verify_installed_configuration(parent, own, release, parent_release)
    from src.trading_runtime.fixed_structural_lot_contract import FixedStructuralLotStrategyContract
    registered = fixed_strategy_executor(release.executor_strategy_id, release.number).contract_factory()
    if type(registered) is not FixedStructuralLotStrategyContract or registered.release != release or registered.fixed_structural_lot_policy != policy:
        raise ValueError('Installed lot policy differs from exact registered typed contract')
    from .backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    proof = certify_numbered_fixed_v4_projection(number)
    if type(proof) is not str or len(proof) != 64 or any((v not in '0123456789abcdef' for v in proof)):
        raise ValueError('Actual own complete source proof required')
    verify_current_installed_source(own)
    return (own, policy, proof)

def prepare_native_fixed_structural_lot_operation(client, *, number, run_id, session_date, plans, price_authority, through_boundary_ms=57600000):
    from .backtest_fixed_structural_lot_source_v10 import prepare_fixed_structural_lot_source
    source = prepare_fixed_structural_lot_source(client, run_id=run_id, parent_number=numbered_strategy_parent(number), session_date=session_date, policy=None, plans=plans, price_authority=price_authority, installed_number=number, through_boundary_ms=through_boundary_ms)
    return NativeFixedStructuralLotOperation(source)

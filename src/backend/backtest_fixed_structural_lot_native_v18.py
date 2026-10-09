"""Prepared successor source factory; registration/source proof remain mandatory.

Installed execution requires complete source and configuration certification.
"""
from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE as IDENTITY_RULE, BATCHED_DETAIL_SELECT_RULE as BATCHED_RULE, SELECTED_CHECKPOINT_PRODUCT_RULE as CHECKPOINT_RULE, OPERATION_CHECKPOINT_READER_RULE as READER_RULE
from src.trading_runtime.decimal_snapshot_readback import RULE as DECIMAL_RULE
from src.trading_runtime.packet_validation_reuse_policy import (
    INPUT as REUSE_INPUT, RULE as REUSE_RULE, parse_packet_validation_reuse_policy,
)
from src.trading_runtime.projected_configuration_reuse_policy import (
    INPUT as PROJECTION_REUSE_INPUT, RULE as PROJECTION_REUSE_RULE, parse_projected_configuration_reuse_policy,
)
from src.trading_runtime.owned_scalar_snapshot_policy import INPUT as OWNED_INPUT, RULE as OWNED_RULE, parse_owned_scalar_snapshot_policy
from src.trading_runtime.empty_protection_confirmation_policy import INPUT as EMPTY_INPUT, RULE as EMPTY_RULE, parse_empty_protection_confirmation_policy
from src.trading_runtime.complete_market_window_policy import INPUT as WINDOW_INPUT, RULE as WINDOW_RULE, parse_complete_market_window_policy
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
    if release.number != own.strategy_number or release.executor_revision != own.strategy_number or release.executor_strategy_id != own.payload['strategy']['strategy_id'] or (own.strategy_number == parent.strategy_number) or (release.input_contracts != (*parent_release.input_contracts, DECLARED_FIXED_ADAPTER, SOURCE_INPUT, REUSE_INPUT, PROJECTION_REUSE_INPUT, OWNED_INPUT, EMPTY_INPUT, WINDOW_INPUT)) or (release.rule_set_contracts != (*parent_release.rule_set_contracts, ENTRY_RULE, VALIDATOR_RULE, PROJECTION_RULE, NATIVE_SOURCE_RULE, CLOCK_RULE, INITIAL_STOP_RULE, WARM_RULE, IDENTITY_RULE, BATCHED_RULE, CHECKPOINT_RULE, READER_RULE, DECIMAL_RULE, REUSE_RULE, PROJECTION_REUSE_RULE, OWNED_RULE, EMPTY_RULE, WINDOW_RULE)):
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
    reuse_policy = parse_packet_validation_reuse_policy(params.get('packet_validation_reuse_policy'))
    projection_reuse_policy = parse_projected_configuration_reuse_policy(params.get('projected_configuration_reuse_policy'))
    owned_snapshot_policy = parse_owned_scalar_snapshot_policy(params.get('owned_scalar_snapshot_policy'))
    empty_policy = parse_empty_protection_confirmation_policy(params.get('empty_protection_confirmation_policy'))
    window_policy = parse_complete_market_window_policy(params.get('complete_market_window_policy'))
    if owned_snapshot_policy.max_rows != reuse_policy.max_rows:
        raise ValueError('Owned and validation row bounds differ')
    if params.get('fixed_structural_lot_parent') != parent_reference(parent):
        raise ValueError('Own lot declaration has foreign parent/source approval')
    inherited = deepcopy(own.payload)
    inherited['strategy']['parameters'].pop('fixed_structural_lot_policy')
    inherited['strategy']['parameters'].pop('fixed_structural_lot_parent')
    inherited['strategy']['parameters'].pop('packet_validation_reuse_policy')
    inherited['strategy']['parameters'].pop('projected_configuration_reuse_policy')
    inherited['strategy']['parameters'].pop('owned_scalar_snapshot_policy')
    inherited['strategy']['parameters'].pop('empty_protection_confirmation_policy')
    inherited['strategy']['parameters'].pop('complete_market_window_policy')
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
    return policy, reuse_policy, projection_reuse_policy, owned_snapshot_policy, empty_policy, window_policy

def load_installed_configuration(client, *, number, parent):
    """Only actual immutable loader+registered release+full source proof issue it."""
    if type(number) is not int or number <= 0 or numbered_strategy_parent(number) != parent.strategy_number:
        raise ValueError('Installed lot declaration has different registered parent')
    release = numbered_strategy(number)
    own = certify_numbered_configuration(client, number)
    parent_release = numbered_strategy(parent.strategy_number)
    from src.trading_runtime.fixed_structural_lot_release_v18 import verify_prepared_fixed_structural_lot_release
    from src.trading_runtime.fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
    registered = fixed_strategy_executor(release.executor_strategy_id, release.number).contract_factory()
    if type(registered) is not FixedStructuralLotCompleteMarketStrategyContract or registered.release != release:
        raise ValueError('Installed reuse factory differs from exact registered typed contract')
    registered.__post_init__()
    verify_prepared_fixed_structural_lot_release(parent, own.payload, parent_release=parent_release,
        release=release, reuse_policy=registered.validation_reuse_policy, projection_reuse_policy=registered.projection_reuse_policy, owned_snapshot_policy=registered.owned_snapshot_policy, empty_confirmation_policy=registered.empty_confirmation_policy, complete_market_policy=registered.complete_market_policy)
    policy, reuse_policy, projection_reuse_policy, owned_snapshot_policy, empty_policy, window_policy = verify_installed_configuration(parent, own, release, parent_release)
    if registered.fixed_structural_lot_policy != policy or registered.validation_reuse_policy != reuse_policy or registered.projection_reuse_policy != projection_reuse_policy or registered.owned_snapshot_policy != owned_snapshot_policy or registered.empty_confirmation_policy != empty_policy or registered.complete_market_policy != window_policy:
        raise ValueError('Installed lot/reuse policy differs from registered factory')
    from .backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    proof = certify_numbered_fixed_v4_projection(number)
    if type(proof) is not str or len(proof) != 64 or any((v not in '0123456789abcdef' for v in proof)):
        raise ValueError('Actual own complete source proof required')
    verify_current_installed_source(own)
    return (own, policy, proof)

def prepare_native_fixed_structural_lot_operation(client, *, number, run_id, session_date, plans, price_authority, through_boundary_ms=57600000):
    from .backtest_fixed_structural_lot_source_v18 import prepare_fixed_structural_lot_source
    source = prepare_fixed_structural_lot_source(client, run_id=run_id, parent_number=numbered_strategy_parent(number), session_date=session_date, policy=None, plans=plans, price_authority=price_authority, installed_number=number, through_boundary_ms=through_boundary_ms)
    return NativeFixedStructuralLotOperation(source)

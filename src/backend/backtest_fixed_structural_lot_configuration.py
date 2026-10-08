"""Registered semantic fixed-lot configuration routing; no numeric policy dispatch."""
from hashlib import sha256
import re
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.fixed_structural_lot_contract import FixedStructuralLotStrategyContract
from src.trading_runtime.fixed_structural_lot_policy import parse_fixed_structural_lot_policy
from src.trading_runtime.numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from src.trading_runtime.strategy_registry import numbered_strategy, numbered_strategy_parent, fixed_strategy_executor


def declared_fixed_structural_lot_contract(number):
    if type(number) is not int:
        return None
    try:
        release = numbered_strategy(number)
    except ValueError as exc:
        if str(exc) == f'Strategy {number} is not published':
            return None
        raise
    rule = 'fixed-structural-lot-entry@1'
    sources = tuple(s for s in ('fixed-structural-lot-source@1', 'fixed-structural-lot-source@2') if s in release.input_contracts)
    source = sources[0] if len(sources) == 1 else ''
    if rule not in release.rule_set_contracts and not sources:
        return None
    if (len(sources) != 1 or release.rule_set_contracts.count(rule) != 1 or release.input_contracts.count(source) != 1
            or release.input_contracts.count(DECLARED_FIXED_ADAPTER) != 1):
        raise ValueError('Fixed-lot configuration lacks exact semantic companions')
    factory = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).contract_factory
    contract = factory()
    if type(contract) is not FixedStructuralLotStrategyContract or contract.release != release:
        raise ValueError('Fixed-lot configuration lacks exact registered typed factory')
    contract.__post_init__()
    return contract


def verify_fixed_structural_lot_configuration(strategy):
    contract = declared_fixed_structural_lot_contract(strategy.get('strategy_number'))
    if contract is None:
        raise ValueError('Fixed-lot configuration is not a registered declaration')
    number = contract.strategy_number
    if (strategy.get('strategy_id') != contract.strategy_id or strategy.get('revision') != number
            or strategy.get('execution_interval') != contract.execution_interval):
        raise ValueError('Fixed-lot execution identity differs from registered contract')
    params = strategy.get('parameters')
    if type(params) is not dict or set(params) != {'execution','sizing','fixed_structural_lot_policy','fixed_structural_lot_parent'}:
        raise ValueError('Fixed-lot parameter companions differ')
    if parse_fixed_structural_lot_policy(params['fixed_structural_lot_policy']) != contract.fixed_structural_lot_policy:
        raise ValueError('Fixed-lot policy differs from exact registered factory')
    manifest = strategy.get('numbered_release')
    if type(manifest) is not dict:
        raise ValueError('Fixed-lot approved manifest required')
    reference = params['fixed_structural_lot_parent']
    parent_number = numbered_strategy_parent(number)
    if (type(reference) is not dict or set(reference) != {'revision_id','payload_hash','node_hash','token',
            'approved_code_commit','approved_code_fingerprint'}
            or type(reference['revision_id']) is not str
            or not re.fullmatch(rf'strategy-one-{parent_number}:[0-9a-fA-F-]{{36}}', reference['revision_id'])
            or any(type(reference[key]) is not str or not re.fullmatch(r'[0-9a-f]{64}',reference[key])
                   for key in ('payload_hash','node_hash','token','approved_code_fingerprint'))
            or type(reference['approved_code_commit']) is not str
            or not re.fullmatch(r'[0-9a-f]{40}',reference['approved_code_commit'])
            or manifest.get('source_revision_id') != reference['revision_id']
            or manifest.get('source_payload_hash') != reference['payload_hash']
            or type(manifest.get('approved_code_commit')) is not str
            or not re.fullmatch(r'[0-9a-f]{40}',manifest['approved_code_commit'])
            or type(manifest.get('approved_code_fingerprint')) is not str
            or not re.fullmatch(r'[0-9a-f]{64}',manifest['approved_code_fingerprint'])
            or type(manifest.get('approval_reference')) is not str
            or not manifest['approval_reference'].strip()):
        raise ValueError('Fixed-lot parent/source approval binding differs')
    content = {k:v for k,v in manifest.items() if k != 'manifest_hash'}
    if (manifest.get('manifest_hash') != sha256(canonical_json(content).encode()).hexdigest()
            or manifest.get('contract') != contract.release.canonical_payload()
            or manifest.get('approved_digest') != contract.release.approved_digest
            or manifest.get('publication_mode') != 'backtest_only'):
        raise ValueError('Fixed-lot manifest differs from exact registered declaration')
    return contract


def derive_registered_fixed_structural_lot_configuration(parent, *, number,
        approved_code_commit, approved_code_fingerprint, approval_reference):
    from src.trading_runtime.fixed_structural_lot_release import derive_fixed_structural_lot_release
    if 'fixed-structural-lot-source@2' in numbered_strategy(number).input_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v2 import derive_fixed_structural_lot_release
    from src.trading_runtime.fixed_structural_lot_interval_validator_v2 import VALIDATOR_RULE
    if VALIDATOR_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v3 import derive_fixed_structural_lot_release
    from .backtest_fixed_structural_lot_projection_authority import PROJECTION_RULE
    if PROJECTION_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v4 import derive_fixed_structural_lot_release
    from .backtest_fixed_structural_lot_projection_runtime_authority import PROJECTION_RULE as RUNTIME_HASH_RULE
    if RUNTIME_HASH_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v5 import derive_fixed_structural_lot_release
    from src.trading_runtime.fixed_structural_lot_native_source_rule import NATIVE_SOURCE_RULE
    if NATIVE_SOURCE_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v6 import derive_fixed_structural_lot_release
    from src.trading_runtime.fixed_structural_lot_causal_clock import CLOCK_RULE
    if CLOCK_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v7 import derive_fixed_structural_lot_release
    from src.trading_runtime.fixed_structural_lot_warm_proof import RULE as WARM_RULE
    if WARM_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v8 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE
    if IMMUTABLE_NUMBERED_IDENTITY_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v9 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import BATCHED_DETAIL_SELECT_RULE
    if BATCHED_DETAIL_SELECT_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v10 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import SELECTED_CHECKPOINT_PRODUCT_RULE
    if SELECTED_CHECKPOINT_PRODUCT_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v11 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import OPERATION_CHECKPOINT_READER_RULE
    if OPERATION_CHECKPOINT_READER_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v12 import derive_fixed_structural_lot_release
    contract = declared_fixed_structural_lot_contract(number)
    if contract is None or parent.strategy_number != numbered_strategy_parent(number):
        raise ValueError('Fixed-lot registered parent differs')
    return derive_fixed_structural_lot_release(parent,
        parent_release=numbered_strategy(parent.strategy_number), release=contract.release,
        policy=contract.fixed_structural_lot_policy.payload(),
        approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)


def compile_registered_fixed_structural_lot_configuration(parent, *, number, **approval):
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    result = derive_registered_fixed_structural_lot_configuration(parent, number=number, **approval)
    certify_numbered_fixed_v4_projection(number)
    return {key:value for key,value in result.items() if key != 'nodes'}

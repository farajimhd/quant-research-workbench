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
    from src.trading_runtime.fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
    require_declared_fixed_structural_lot_contract(contract, release)
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
    from src.trading_runtime.packet_validation_reuse_policy import RULE as REUSE_RULE, parse_packet_validation_reuse_policy
    reuse_selected = REUSE_RULE in contract.release.rule_set_contracts
    expected = {'execution','sizing','fixed_structural_lot_policy','fixed_structural_lot_parent'}
    if reuse_selected:
        expected.add('packet_validation_reuse_policy')
    from src.trading_runtime.projected_configuration_reuse_policy import RULE as PROJECTION_REUSE_RULE, parse_projected_configuration_reuse_policy
    projection_selected = PROJECTION_REUSE_RULE in contract.release.rule_set_contracts
    if projection_selected:
        expected.add('projected_configuration_reuse_policy')
    from src.trading_runtime.owned_scalar_snapshot_policy import RULE as OWNED_RULE, parse_owned_scalar_snapshot_policy
    owned_selected = OWNED_RULE in contract.release.rule_set_contracts
    if owned_selected:
        expected.add('owned_scalar_snapshot_policy')
    from src.trading_runtime.empty_protection_confirmation_policy import RULE as EMPTY_RULE, parse_empty_protection_confirmation_policy
    empty_selected = EMPTY_RULE in contract.release.rule_set_contracts
    if empty_selected:
        expected.add('empty_protection_confirmation_policy')
    from src.trading_runtime.complete_market_window_policy import RULE as WINDOW_RULE, parse_complete_market_window_policy
    window_selected = WINDOW_RULE in contract.release.rule_set_contracts
    if window_selected:
        expected.add('complete_market_window_policy')
    from src.trading_runtime.selected_exit_publication_policy import RULE as EXIT_RULE, parse_selected_exit_publication_policy
    exit_selected = EXIT_RULE in contract.release.rule_set_contracts
    if exit_selected:
        expected.add('selected_exit_publication_policy')
    from src.trading_runtime.fixed_lot_management_reuse_policy import declared_management_reuse_policy
    management_policy = declared_management_reuse_policy(
        contract.release, params.get('management_reuse_policy') if type(params) is dict else None)
    if management_policy is not None:
        expected.add('management_reuse_policy')
    if management_policy != getattr(contract, 'management_reuse_policy', None):
        raise ValueError('Management reuse differs from exact registered factory')
    from src.trading_runtime.portfolio_acquisition_policy import declared_acquisition_limit, PARAMETER
    quota=declared_acquisition_limit({'strategy':strategy})
    if quota is not None:
        expected.add(PARAMETER)
        from src.trading_runtime.portfolio_acquisition_contract import SessionAcquisitionQuotaPolicy
        if type(contract.session_acquisition_quota) is not SessionAcquisitionQuotaPolicy or quota!=contract.session_acquisition_quota.maximum:
            raise ValueError('Portfolio acquisition quota differs from exact registered factory')
    from src.trading_runtime.fixed_lot_management_cadence_policy import PARAMETER as CADENCE_PARAMETER, parse_declared_management_cadence
    cadence=parse_declared_management_cadence(contract.release,params.get(CADENCE_PARAMETER) if type(params) is dict else None)
    if cadence is not None: expected.add(CADENCE_PARAMETER)
    if cadence != getattr(contract,'management_cadence_policy',None):
        raise ValueError('Management cadence differs from exact registered factory')
    from src.trading_runtime.initial_held_recovery_reuse_policy import PARAMETER as INITIAL_PARAMETER, parse_declared_initial_held_reuse
    initial=parse_declared_initial_held_reuse(contract.release,params.get(INITIAL_PARAMETER) if type(params) is dict else None)
    if initial is not None: expected.add(INITIAL_PARAMETER)
    if initial != getattr(contract,'initial_held_recovery_reuse_policy',None):
        raise ValueError('Initial-held reuse differs from exact registered factory')
    from src.trading_runtime.proposal_decision_inventory_reuse_policy import PARAMETER as PROPOSAL_PARAMETER,parse_declared_proposal_decision_reuse
    proposal=parse_declared_proposal_decision_reuse(contract.release,params.get(PROPOSAL_PARAMETER) if type(params) is dict else None)
    if proposal is not None:expected.add(PROPOSAL_PARAMETER)
    if proposal!=getattr(contract,'proposal_decision_inventory_reuse_policy',None):
        raise ValueError('Proposal inventory reuse differs from exact registered factory')
    from src.trading_runtime.first_inventory_source_reuse_policy import PARAMETER as FIRST_SOURCE_PARAMETER,parse_declared_first_inventory_source_reuse
    first_source=parse_declared_first_inventory_source_reuse(contract.release,params.get(FIRST_SOURCE_PARAMETER) if type(params) is dict else None)
    if first_source is not None:expected.add(FIRST_SOURCE_PARAMETER)
    if first_source!=getattr(contract,'first_inventory_source_reuse_policy',None):
        raise ValueError('First-inventory source reuse differs from exact registered factory')
    from src.trading_runtime.publication_source_reuse_policy import PARAMETER as PUBLICATION_PARAMETER, parse_declared_publication_source_reuse
    publication=parse_declared_publication_source_reuse(contract.release,params.get(PUBLICATION_PARAMETER) if type(params) is dict else None)
    if publication is not None:expected.add(PUBLICATION_PARAMETER)
    if publication!=getattr(contract,'publication_source_reuse_policy',None):
        raise ValueError('Publication source reuse differs from exact registered factory')
    if type(params) is not dict or set(params) != expected:
        raise ValueError('Fixed-lot parameter companions differ')
    if reuse_selected and parse_packet_validation_reuse_policy(params['packet_validation_reuse_policy']) != contract.validation_reuse_policy:
        raise ValueError('Fixed-lot validation reuse bounds differ from registered factory')
    if projection_selected and parse_projected_configuration_reuse_policy(params['projected_configuration_reuse_policy']) != contract.projection_reuse_policy:
        raise ValueError('Projected-node reuse bounds differ from registered factory')
    if owned_selected and parse_owned_scalar_snapshot_policy(params['owned_scalar_snapshot_policy']) != contract.owned_snapshot_policy:
        raise ValueError('Owned snapshot bounds differ from registered factory')
    if empty_selected and parse_empty_protection_confirmation_policy(params['empty_protection_confirmation_policy']) != contract.empty_confirmation_policy:
        raise ValueError('Empty confirmation differs from registered factory')
    if window_selected and parse_complete_market_window_policy(params['complete_market_window_policy']) != contract.complete_market_policy:
        raise ValueError('Complete market window bounds differ from registered factory')
    if exit_selected and parse_selected_exit_publication_policy(params['selected_exit_publication_policy']) != contract.selected_exit_publication_policy:
        raise ValueError('Selected exit publication differs from registered factory')
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
    from src.trading_runtime.declared_native_manifest import registered_manifest_authority
    authority = registered_manifest_authority(number)
    if authority is not None:
        if (declared_fixed_structural_lot_contract(number) is None
                or parent.strategy_number != authority.parent_number):
            raise ValueError('Native fixed-lot manifest differs from its exact registered parent')
        return authority.derive(parent, approved_code_commit=approved_code_commit,
            approved_code_fingerprint=approved_code_fingerprint,
            approval_reference=approval_reference)
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
    from src.trading_runtime.decimal_snapshot_readback import RULE as DECIMAL_RULE
    if DECIMAL_RULE in numbered_strategy(number).rule_set_contracts:
        from src.trading_runtime.fixed_structural_lot_release_v13 import derive_fixed_structural_lot_release
    from src.trading_runtime.packet_validation_reuse_policy import RULE as REUSE_RULE
    reuse_selected = REUSE_RULE in numbered_strategy(number).rule_set_contracts
    if reuse_selected:
        from src.trading_runtime.fixed_structural_lot_release_v14 import derive_fixed_structural_lot_release
    from src.trading_runtime.projected_configuration_reuse_policy import RULE as PROJECTION_REUSE_RULE
    projection_selected = PROJECTION_REUSE_RULE in numbered_strategy(number).rule_set_contracts
    if projection_selected:
        from src.trading_runtime.fixed_structural_lot_release_v15 import derive_fixed_structural_lot_release
    from src.trading_runtime.owned_scalar_snapshot_policy import RULE as OWNED_RULE
    owned_selected = OWNED_RULE in numbered_strategy(number).rule_set_contracts
    if owned_selected:
        from src.trading_runtime.fixed_structural_lot_release_v16 import derive_fixed_structural_lot_release
    from src.trading_runtime.empty_protection_confirmation_policy import RULE as EMPTY_RULE
    empty_selected = EMPTY_RULE in numbered_strategy(number).rule_set_contracts
    if empty_selected:
        from src.trading_runtime.fixed_structural_lot_release_v17 import derive_fixed_structural_lot_release
    from src.trading_runtime.complete_market_window_policy import RULE as WINDOW_RULE
    window_selected = WINDOW_RULE in numbered_strategy(number).rule_set_contracts
    if window_selected:
        from src.trading_runtime.fixed_structural_lot_release_v18 import derive_fixed_structural_lot_release
    from src.trading_runtime.selected_exit_publication_policy import RULE as EXIT_RULE
    exit_selected = EXIT_RULE in numbered_strategy(number).rule_set_contracts
    if exit_selected:
        from src.trading_runtime.fixed_structural_lot_release_v19 import derive_fixed_structural_lot_release
    contract = declared_fixed_structural_lot_contract(number)
    if contract is None or parent.strategy_number != numbered_strategy_parent(number):
        raise ValueError('Fixed-lot registered parent differs')
    return derive_fixed_structural_lot_release(parent,
        parent_release=numbered_strategy(parent.strategy_number), release=contract.release,
        policy=contract.fixed_structural_lot_policy.payload(),
        **({'reuse_policy': contract.validation_reuse_policy} if reuse_selected else {}),
        **({'projection_reuse_policy': contract.projection_reuse_policy} if projection_selected else {}),
        **({'owned_snapshot_policy': contract.owned_snapshot_policy} if owned_selected else {}),
        **({'empty_confirmation_policy': contract.empty_confirmation_policy} if empty_selected else {}),
        **({'complete_market_policy': contract.complete_market_policy} if window_selected else {}),
        **({'selected_exit_policy': contract.selected_exit_publication_policy} if exit_selected else {}),
        approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)


def compile_registered_fixed_structural_lot_configuration(parent, *, number, **approval):
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    result = derive_registered_fixed_structural_lot_configuration(parent, number=number, **approval)
    certify_numbered_fixed_v4_projection(number)
    return {key:value for key,value in result.items() if key != 'nodes'}

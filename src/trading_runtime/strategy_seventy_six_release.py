"""Immutable76 identity for exact75 rules and completed exit-source checkpoint reuse."""
from copy import deepcopy
from hashlib import sha256
import re
import json

from . import strategy_seventy_five_release as parent_policy
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-75:aec64642-b1df-4fae-b380-a1a952d46998'
PARENT_PAYLOAD_HASH = '82b8b803d0f250528996cabf7225b3beb5a5bc4c0b237aa93cd686c496224b16'
PARENT_CODE_COMMIT = 'd66a2f151d9128145ed9ca2ea8dd07914b6120c2'
PARENT_CODE_FINGERPRINT = 'd4794eceba721508c2fc1e18a11fc68fb65d0f0c45a9b25c4e653b49cfb04484'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
HALF_RISK_LIQUIDITY_POLICY = deepcopy(parent_policy.HALF_RISK_LIQUIDITY_POLICY)
CONFIRMED_ORIGINAL_RISK_POLICY = parent_policy.CONFIRMED_ORIGINAL_RISK_POLICY
CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD = deepcopy(parent_policy.CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD)
BEHAVIOR = 'Strategy76 is a verification-performance successor of exact published75. All input and rule contracts, numerical policies, inherited42 economics, causal exits, sizing, costs and sequential Portfolio/OMS remain unchanged. Generic operation-owned completed profit-giveback and confirmed original-risk exit-source checkpoint reuse retains complete content and certified source verification, independent fresh initial and final proof scopes, bounded memory and unchanged fallback. Own immutable execution-source identity only; no ticker, date, outcome or numerical rule exception. Backtest-only; native financial and timing equivalence remain unproven.'



def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=76, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=76, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts,
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 75
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy76 requires exact pinned certified Strategy75')
    manifest = parent_policy.verify_strategy_seventy_five_manifest(source.payload['strategy'])
    if (manifest['approved_code_commit'] != PARENT_CODE_COMMIT
            or manifest['approved_code_fingerprint'] != PARENT_CODE_FINGERPRINT):
        raise ValueError('Strategy76 exact parent source approval differs')
    return manifest


def verify_prepared_strategy_seventy_six_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 76
            or type(strategy.get('revision')) is not int or strategy['revision'] != 76
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy76 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY, 'confirmed_original_risk_policy': CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy76 prepared manifest shape differs')
    if (manifest['contract'] != release.canonical_payload()
            or manifest['approved_digest'] != release.approved_digest
            or manifest['source_revision_id'] != PARENT_REVISION_ID
            or manifest['source_payload_hash'] != PARENT_PAYLOAD_HASH
            or any(manifest[name] != policy for name, policy in policies.items())
            or any(canonical_json(manifest[name]) != canonical_json(policy) for name, policy in policies.items())
            or canonical_json(manifest['contract']) != canonical_json(release.canonical_payload())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch('[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch('[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy76 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy76 prepared manifest seal differs')
    return manifest


def derive_strategy_seventy_six_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy76 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'confirmed_original_risk_policy': deepcopy(CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=76, revision=76, name='Early Squeeze Strategy 76',
        profile_id='strategy-one-76', profile_revision=76, numbered_release=manifest)
    verify_prepared_strategy_seventy_six_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-76', revision=76,
        definition_revision=76, name='Early Squeeze Strategy 76', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 76 Backtest',
        description='Sealed extended-session Strategy 76', profile_id='strategy-one-76')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-seventy-six-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_installed_strategy_seventy_six_release(manifest):
    """Source catalog admission is distinct from normalized publication."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(76)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, 76).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy76 published release differs from installed approval')
    return installed


def verify_strategy_seventy_six_manifest(strategy):
    manifest = verify_prepared_strategy_seventy_six_manifest(strategy)
    verify_installed_strategy_seventy_six_release(manifest)
    return manifest

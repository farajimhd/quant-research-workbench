"""Immutable69 identity for exact68 rules and declared-manager compatibility."""
from copy import deepcopy
from hashlib import sha256
import re
import json

from . import strategy_sixty_eight_release as parent_policy
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-68:e1bea8b6-60f3-425a-9171-1d5bf9b8c45a'
PARENT_PAYLOAD_HASH = '3ea87c65934c5669af18b640ad651c4b30713249211534dd4fb7e4cc7b2ab699'
PARENT_CODE_COMMIT = '18bd9890a0f60237b5e337046cceaeb56dcfbd85'
PARENT_CODE_FINGERPRINT = '21d91214e4b76c517e04fc33511a7a1b2e8ad659fbe5902adacc29e664818b1c'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
HALF_RISK_LIQUIDITY_POLICY = deepcopy(parent_policy.HALF_RISK_LIQUIDITY_POLICY)
CONFIRMED_ORIGINAL_RISK_POLICY = parent_policy.CONFIRMED_ORIGINAL_RISK_POLICY
CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD = deepcopy(parent_policy.CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD)
BEHAVIOR = 'Strategy69 is a declared-manager compatibility successor of exact published68. Numerical policies, causal two-completed-bucket quarter-original-risk extension, inherited42 entries/exits, anchors, TTL, reentry, sizing, exposure, costs and sequential Portfolio/OMS remain unchanged. Its own immutable identity and reviewed execution source permit generic declared-manager compatibility; no new numerical rule or ticker/date/outcome exception. Backtest-only; financial results unmeasured.'



def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=69, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=69, evaluation_interval=parent.evaluation_interval,
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
            or source.strategy_number != 68
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy69 requires exact pinned certified Strategy68')
    manifest = parent_policy.verify_strategy_sixty_eight_manifest(source.payload['strategy'])
    if (manifest['approved_code_commit'] != PARENT_CODE_COMMIT
            or manifest['approved_code_fingerprint'] != PARENT_CODE_FINGERPRINT):
        raise ValueError('Strategy69 exact parent source approval differs')
    return manifest


def verify_prepared_strategy_sixty_nine_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 69
            or type(strategy.get('revision')) is not int or strategy['revision'] != 69
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy69 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY, 'confirmed_original_risk_policy': CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy69 prepared manifest shape differs')
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
        raise ValueError('Strategy69 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy69 prepared manifest seal differs')
    return manifest


def derive_strategy_sixty_nine_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy69 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'confirmed_original_risk_policy': deepcopy(CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=69, revision=69, name='Early Squeeze Strategy 69',
        profile_id='strategy-one-69', profile_revision=69, numbered_release=manifest)
    verify_prepared_strategy_sixty_nine_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-69', revision=69,
        definition_revision=69, name='Early Squeeze Strategy 69', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 69 Backtest',
        description='Sealed extended-session Strategy 69', profile_id='strategy-one-69')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-sixty-nine-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_installed_strategy_sixty_nine_release(manifest):
    """Source catalog admission is distinct from normalized publication."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(69)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, 69).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy69 published release differs from installed approval')
    return installed


def verify_strategy_sixty_nine_manifest(strategy):
    manifest = verify_prepared_strategy_sixty_nine_manifest(strategy)
    verify_installed_strategy_sixty_nine_release(manifest)
    return manifest

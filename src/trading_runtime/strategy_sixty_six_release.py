"""Immutable declared all-held original-risk extension from exact published42; Backtest-only."""
from copy import deepcopy
from hashlib import sha256
import re
import json

from . import strategy_forty_two_release as parent_policy
from .strategy_half_risk_liquidity_fade import half_risk_liquidity_policy_payload
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-42:61d09336-6eb1-4298-bc8e-1b985e97aa78'
PARENT_PAYLOAD_HASH = '048fbd8a27269fcb7c12e1c49d7213e2eb08320a42d86e355d8e55fbdbce37b6'
PARENT_CODE_COMMIT = '9ad409381449c1f1b859206093b6851282b111bb'
PARENT_CODE_FINGERPRINT = 'bfa8ad70e0584f27d3159ee1017a78d50ec53e6f7ae2dc601e98429f964eb62e'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
HALF_RISK_LIQUIDITY_POLICY = half_risk_liquidity_policy_payload()
from .all_held_original_risk_failure import (
    AllHeldOriginalRiskPolicy, ALL_HELD_ORIGINAL_RISK_RULE, ALL_HELD_ORIGINAL_RISK_INPUT)
ALL_HELD_ORIGINAL_RISK_POLICY = AllHeldOriginalRiskPolicy()
ALL_HELD_ORIGINAL_RISK_POLICY_PAYLOAD = json.loads(canonical_json(ALL_HELD_ORIGINAL_RISK_POLICY.payload()))
BEHAVIOR = 'Strategy66 inherits exact published42 entries, original anchors, reentry, protection, sizing, exposure, costs and exits. After inherited exits, a separate declared all-held PM/AH rule exits only with wholly post-held completed5s close and fresh executable bid at or below original ask minus quarter original risk, and MACD line strictly below signal. No elapsed-time exit or first-minute cap; no synthetic missing evidence. Original session and shared Portfolio/OMS remain authoritative. Backtest-only; no financial claim.'



def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=66, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=66, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts + ('declared-numbered-fixed-policy-adapter@1', ALL_HELD_ORIGINAL_RISK_INPUT),
        rule_set_contracts=parent.rule_set_contracts + (ALL_HELD_ORIGINAL_RISK_RULE,),
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 42
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy66 requires exact pinned certified Strategy42')
    manifest = parent_policy.verify_prepared_strategy_forty_two_manifest(source.payload['strategy'])
    if (manifest['approved_code_commit'] != PARENT_CODE_COMMIT
            or manifest['approved_code_fingerprint'] != PARENT_CODE_FINGERPRINT):
        raise ValueError('Strategy66 exact parent source approval differs')
    return manifest


def verify_prepared_strategy_sixty_six_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 66
            or type(strategy.get('revision')) is not int or strategy['revision'] != 66
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy66 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY, 'all_held_original_risk_policy': ALL_HELD_ORIGINAL_RISK_POLICY_PAYLOAD}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy66 prepared manifest shape differs')
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
        raise ValueError('Strategy66 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy66 prepared manifest seal differs')
    return manifest


def derive_strategy_sixty_six_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy66 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'all_held_original_risk_policy': deepcopy(ALL_HELD_ORIGINAL_RISK_POLICY_PAYLOAD),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=66, revision=66, name='Early Squeeze Strategy 66',
        profile_id='strategy-one-66', profile_revision=66, numbered_release=manifest)
    verify_prepared_strategy_sixty_six_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-66', revision=66,
        definition_revision=66, name='Early Squeeze Strategy 66', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 66 Backtest',
        description='Sealed extended-session Strategy 66', profile_id='strategy-one-66')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-sixty-six-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_installed_strategy_sixty_six_release(manifest):
    """Source catalog admission is distinct from normalized publication."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(66)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, 66).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy66 published release differs from installed approval')
    return installed


def verify_strategy_sixty_six_manifest(strategy):
    manifest = verify_prepared_strategy_sixty_six_manifest(strategy)
    verify_installed_strategy_sixty_six_release(manifest)
    return manifest

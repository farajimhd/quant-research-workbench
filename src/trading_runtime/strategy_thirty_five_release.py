"""Exact-parent Strategy 35 declaration; prepared content grants no admission."""
from copy import deepcopy
from hashlib import sha256
import re

from . import strategy_thirty_four_release as parent_policy
from .journal_contract import canonical_json
from .strategy_registry import NumberedStrategyRelease
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_liquidity_fade_failure import liquidity_fade_policy_payload

PARENT_REVISION_ID = 'strategy-one-34:f0159656-6b65-4057-8b07-497b5668f9fc'
PARENT_PAYLOAD_HASH = 'd0c200a0f2fc7868ffe2bb6a7c298ad31884870b364c412932b4a3d0268d3403'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
PROFIT_PROTECTION_POLICY = deepcopy(parent_policy.PROFIT_PROTECTION_POLICY)
CONFIRMED_AH_FAILURE_POLICY = deepcopy(parent_policy.CONFIRMED_AH_FAILURE_POLICY)
LIQUIDITY_FADE_POLICY = liquidity_fade_policy_payload()
BEHAVIOR = (
    'Strategy 35 inherits the exact pinned Strategy 34 entries, inputs, sizing, '
    'costs, structural protection, failure and profit exits. After inherited '
    'exits, failed price follow-through may exit when four contiguous completed '
    'whole-held 5s candles show recent 10s trade count at most one quarter of '
    'strictly positive prior 10s activity, bearish completed 5s MACD, and both '
    'completed close and fresh bid at or below original entry ask. A fresh '
    '100ms quote may confirm the latest completed signal while that signal '
    'is less than 5s old; exchange quote age remains at most 1s. Missing '
    'observations or pending exits reject. Time alone never exits. A distinct '
    'native family retains four counts, both clocks and pinned producer attempts. '
    'V7 prior-day checkpoint and regular-session warm-up for after-hours remain '
    'inherited. Backtest only; public resume and live execution remain closed.'
)


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=35, executor_strategy_id=parent.executor_strategy_id,
                  executor_revision=35, evaluation_interval=parent.evaluation_interval,
                  input_contracts=parent.input_contracts,
                  rule_set_contracts=parent.rule_set_contracts + (LIQUIDITY_FADE_POLICY['policy_id'],),
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_installed_strategy_thirty_five_release(manifest):
    """An immutable declaration does not substitute for installed authority."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(35)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy 35 published release differs from installed approval')
    return installed


def verify_prepared_strategy_thirty_five_manifest(strategy):
    """Check draft policy content without registering or publishing a revision."""
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 35
            or type(strategy.get('revision')) is not int or strategy['revision'] != 35
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy 35 declared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'profit_protection_policy': PROFIT_PROTECTION_POLICY,
                'confirmed_ah_failure_policy': CONFIRMED_AH_FAILURE_POLICY,
                'liquidity_fade_policy': LIQUIDITY_FADE_POLICY}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy 35 manifest shape differs')
    if (manifest['contract'] != release.canonical_payload()
            or manifest['approved_digest'] != release.approved_digest
            or manifest['source_revision_id'] != PARENT_REVISION_ID
            or manifest['source_payload_hash'] != PARENT_PAYLOAD_HASH
            or any(manifest[name] != policy for name, policy in policies.items())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch(r'[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy 35 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy 35 approval/code manifest seal differs')
    return manifest


def verify_strategy_thirty_five_manifest(strategy):
    manifest = verify_prepared_strategy_thirty_five_manifest(strategy)
    verify_installed_strategy_thirty_five_release(manifest)
    return manifest


def derive_strategy_thirty_five_configuration(source, *, approved_code_commit,
                                             approved_code_fingerprint, approval_reference):
    """Prepare the exact certified parent; installed launch verification is separate."""
    if (source.strategy_number != 34 or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy 35 must derive from exact pinned certified Strategy 34')
    parent_policy.verify_strategy_thirty_four_manifest(source.payload['strategy'])
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy 35 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {
        **deepcopy(INHERITED_POLICIES), 'contract': release.canonical_payload(),
        'approved_digest': release.approved_digest, 'approved_code_commit': approved_code_commit,
        'approved_code_fingerprint': approved_code_fingerprint, 'approval_reference': approval_reference,
        'publication_mode': 'backtest_only', 'source_revision_id': PARENT_REVISION_ID,
        'source_payload_hash': PARENT_PAYLOAD_HASH,
        'profit_protection_policy': deepcopy(PROFIT_PROTECTION_POLICY),
        'confirmed_ah_failure_policy': deepcopy(CONFIRMED_AH_FAILURE_POLICY),
        'liquidity_fade_policy': deepcopy(LIQUIDITY_FADE_POLICY),
    }
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=35, revision=35, name='Early Squeeze Strategy 35',
                              profile_id='strategy-one-35', profile_revision=35, numbered_release=manifest)
    verify_prepared_strategy_thirty_five_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-35', revision=35,
        definition_revision=35, name='Early Squeeze Strategy 35', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle', {}).setdefault('trading_behavior', {})[
        'eligible_sessions'] = ['premarket', 'afterhours']
    payload['run_plan'].update(name='Strategy 35 Backtest',
        description='Sealed extended-session Strategy 35', profile_id='strategy-one-35')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-thirty-five-from:{PARENT_REVISION_ID}',
                source_candidate_hash=source.payload_hash,
                payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
                node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

"""Exact-parent Strategy38 contract with sealed Backtest-only registration."""
from copy import deepcopy
from hashlib import sha256
import re

from . import strategy_thirty_seven_release as parent_policy
from .strategy_episode_activity_veto import episode_activity_veto_policy_payload
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-37:1b587c06-febd-449d-a0ac-9580902c6422'
PARENT_PAYLOAD_HASH = '7e62df8144ab98efe9be48e113fed375f93babaac845de3f2398bcbe0e6f01ec'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
EPISODE_ACTIVITY_POLICY = episode_activity_veto_policy_payload()
BEHAVIOR = (
    'Strategy38 preserves every trading policy of exact pinned Strategy37. '
    'This release corrects service session-window enforcement, checkpointed '
    'liquidity-fade and profit-arming dispatch, and profit-arm candidate admission. '
    'Entry episode veto, original MACD1s and first-setup anchors, held exits, '
    'sizing, costs, V7 seed and full RTH warmup are unchanged. '
    'Backtest only; no live or public resume admission.'
)


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=38, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=38, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts,
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    """Validate the certified published parent without authorizing execution."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 37
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy38 requires exact pinned certified Strategy37')
    return parent_policy.verify_strategy_thirty_seven_manifest(source.payload['strategy'])


def verify_prepared_strategy_thirty_eight_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 38
            or type(strategy.get('revision')) is not int or strategy['revision'] != 38
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy38 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'episode_activity_policy': EPISODE_ACTIVITY_POLICY}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy38 prepared manifest shape differs')
    if (manifest['contract'] != release.canonical_payload()
            or manifest['approved_digest'] != release.approved_digest
            or manifest['source_revision_id'] != PARENT_REVISION_ID
            or manifest['source_payload_hash'] != PARENT_PAYLOAD_HASH
            or any(manifest[name] != policy for name, policy in policies.items())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch('[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch('[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy38 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy38 prepared manifest seal differs')
    return manifest


def verify_installed_strategy_thirty_eight_release(manifest):
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(38)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy38 published release differs from installed approval')
    return installed


def verify_strategy_thirty_eight_manifest(strategy):
    manifest = verify_prepared_strategy_thirty_eight_manifest(strategy)
    verify_installed_strategy_thirty_eight_release(manifest)
    return manifest


def derive_strategy_thirty_eight_configuration(source, *, approved_code_commit,
                                               approved_code_fingerprint, approval_reference):
    """Prepare immutable configuration only; installed execution proof is separate."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy38 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'episode_activity_policy': deepcopy(EPISODE_ACTIVITY_POLICY),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=38, revision=38, name='Early Squeeze Strategy 38',
        profile_id='strategy-one-38', profile_revision=38, numbered_release=manifest)
    verify_prepared_strategy_thirty_eight_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-38', revision=38,
        definition_revision=38, name='Early Squeeze Strategy 38', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle', {}).setdefault('trading_behavior', {})[
        'eligible_sessions'] = ['premarket', 'afterhours']
    payload['run_plan'].update(name='Strategy 38 Backtest',
        description='Sealed extended-session Strategy 38', profile_id='strategy-one-38')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-thirty-eight-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

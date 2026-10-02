"""Prepared exact-parent Strategy37 contract; public registration stays closed."""
from copy import deepcopy
from hashlib import sha256
import re

from . import strategy_thirty_six_release as parent_policy
from .strategy_episode_activity_veto import episode_activity_veto_policy_payload
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-36:6c026cd8-c2a6-4988-a12c-47e8224fa6b1'
PARENT_PAYLOAD_HASH = '628fd276a85aae4561624f6e311caa7385cb9c297954bb944a09e316262337d9'
INHERITED_POLICIES = deepcopy({**parent_policy.INHERITED_POLICIES,
    'entry_activity_policy': parent_policy.ENTRY_ACTIVITY_POLICY})
EPISODE_ACTIVITY_POLICY = episode_activity_veto_policy_payload()
BEHAVIOR = (
    'Strategy37 inherits exact pinned Strategy36 activation, original MACD1s '
    'episode and first-setup anchors, entry predicates, no-add policy, sizing, '
    'costs, V7 seeds, full RTH warmup for AH and all held-position management. '
    'A completed candidate that passes every inherited predicate except the '
    'fully observed entry-activity fade comparison vetoes subsequent new entries '
    'and reentries in the same original ticker episode. The triggering candidate '
    'is rejected. Later recovery of the rolling activity denominator cannot '
    'clear that veto. A new original episode resets the veto. Missing evidence '
    'rejects the current candidate under Strategy36 but never latches a failure; '
    'history before session activation does not latch. The veto is computed '
    'causally over the full certified candidate prefix before survivor pruning, '
    'including candidates observed while a position is held. It affects only '
    'future acquisitions and never forces or postpones an exit. Backtest only; '
    'no live or public resume admission.'
)


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=37, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=37, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts + (EPISODE_ACTIVITY_POLICY['policy_id'],),
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    """Validate the certified published parent without authorizing execution."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 36
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy37 requires exact pinned certified Strategy36')
    return parent_policy.verify_strategy_thirty_six_manifest(source.payload['strategy'])


def verify_prepared_strategy_thirty_seven_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 37
            or type(strategy.get('revision')) is not int or strategy['revision'] != 37
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy37 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'episode_activity_policy': EPISODE_ACTIVITY_POLICY}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy37 prepared manifest shape differs')
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
        raise ValueError('Strategy37 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy37 prepared manifest seal differs')
    return manifest


def derive_strategy_thirty_seven_configuration(source, *, approved_code_commit,
                                               approved_code_fingerprint, approval_reference):
    """Prepare immutable configuration only; installed execution proof is separate."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy37 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'episode_activity_policy': deepcopy(EPISODE_ACTIVITY_POLICY),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=37, revision=37, name='Early Squeeze Strategy 37',
        profile_id='strategy-one-37', profile_revision=37, numbered_release=manifest)
    verify_prepared_strategy_thirty_seven_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-37', revision=37,
        definition_revision=37, name='Early Squeeze Strategy 37', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle', {}).setdefault('trading_behavior', {})[
        'eligible_sessions'] = ['premarket', 'afterhours']
    payload['run_plan'].update(name='Strategy 37 Backtest',
        description='Sealed extended-session Strategy 37', profile_id='strategy-one-37')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-thirty-seven-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

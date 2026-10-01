"""Immutable Strategy 33 release; publication and execution preflight are separate."""
from .strategy_registry import NumberedStrategyRelease
from .strategy_thirty_two_release import release_contract as parent_release_contract
from copy import deepcopy
from hashlib import sha256
import re
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
from . import strategy_thirty_two_release as parent_policy

INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)

PARENT_REVISION_ID = 'strategy-one-32:0964eb3a-59b2-468d-938f-66f0598af31b'
PARENT_PAYLOAD_HASH = 'f6dcd3f8fe199abff4a10d8b10215fbc5803bb7bbc3c37b9deebf5335e3102c5'
PROFIT_PROTECTION_POLICY = {
    **deepcopy(parent_policy.PROFIT_PROTECTION_POLICY),
    'cutoff_admission_authority': 'installed_numbered_fixed_strategy_registry',
}

BEHAVIOR = (
    'Strategy 33 inherits exact pinned Strategy 32 trading inputs, entries, '
    'profit and failure exits, sizing, costs and structural protection. '
    'OMS session cutoff admission uses the installed numbered fixed strategy '
    'registry, preserving causal-clock checks and cancellation behavior. '
    'Native arming confirmation retains V4 server-readonly authority. '
    'No trading thresholds change. V7 prior-day checkpoint and regular-session '
    'warm-up for after-hours remain inherited. Backtest only; public resume '
    'and live execution remain closed.'
)




def release_contract() -> NumberedStrategyRelease:
    """Sealed source declaration; database publication is a separate approval."""
    parent = parent_release_contract()
    values = dict(number=33, executor_strategy_id=parent.executor_strategy_id,
                  executor_revision=33, evaluation_interval=parent.evaluation_interval,
                  input_contracts=parent.input_contracts,
                  rule_set_contracts=parent.rule_set_contracts,
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def verify_installed_strategy_thirty_three_release(manifest):
    """Require installed executor and exact immutable publication content."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(33)
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision)
    expected = release_contract()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest
            or manifest.get('profit_protection_policy') != PROFIT_PROTECTION_POLICY
            or manifest.get('source_revision_id') != PARENT_REVISION_ID
            or manifest.get('source_payload_hash') != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy 33 published release differs from installed approval')
    return installed


def verify_strategy_thirty_three_manifest(strategy):
    """Validate exact policy content, installed identity and approval seal."""
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 33
            or type(strategy.get('revision')) is not int or strategy['revision'] != 33
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy 33 installed execution identity differs')
    manifest = strategy.get('numbered_release')
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id',
                'source_payload_hash', 'manifest_hash', 'profit_protection_policy', *INHERITED_POLICIES}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy 33 manifest shape differs')
    verify_installed_strategy_thirty_three_release(manifest)
    if (any(manifest[name] != policy for name, policy in INHERITED_POLICIES.items())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch(r'[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy 33 manifest differs from inherited policy or code approval')
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != 'manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy 33 manifest approval/code seal differs')
    return manifest


def derive_strategy_thirty_three_configuration(source, *, approved_code_commit,
                                            approved_code_fingerprint, approval_reference):
    """Derive from the exact immutable parent without changing inherited data."""
    if (source.strategy_number != 32 or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy 33 must derive from exact pinned certified Strategy 32')
    parent_policy.verify_strategy_thirty_two_manifest(source.payload['strategy'])
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy 33 compiler cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH,
        'profit_protection_policy': deepcopy(PROFIT_PROTECTION_POLICY)}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=33, revision=33, name='Early Squeeze Strategy 33',
        profile_id='strategy-one-33', profile_revision=33, numbered_release=manifest)
    verify_strategy_thirty_three_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-33', revision=33,
        definition_revision=33, name='Early Squeeze Strategy 33', description=release.behavior_specification)
    payload['strategy_profile'].setdefault('lifecycle', {}).setdefault('trading_behavior', {})[
        'eligible_sessions'] = ['premarket', 'afterhours']
    payload['run_plan'].update(name='Strategy 33 Backtest',
        description='Sealed extended-session Strategy 33', profile_id='strategy-one-33')
    nodes = encode_nodes(payload)
    return {'source_candidate_id': f'strategy-thirty-three-from:{PARENT_REVISION_ID}',
        'source_candidate_hash': source.payload_hash,
        'payload_hash': sha256(canonical_json(payload).encode()).hexdigest(),
        'node_hash': node_hash(nodes), 'node_count': len(nodes), 'payload': payload}

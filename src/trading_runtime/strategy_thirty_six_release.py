"""Prepared exact-parent Strategy36 declaration; registration is separate."""
from copy import deepcopy
from hashlib import sha256
import re

from . import strategy_thirty_five_release as parent_policy
from .journal_contract import canonical_json
from .strategy_registry import NumberedStrategyRelease
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_entry_activity_fade import entry_activity_policy_payload

PARENT_REVISION_ID = 'strategy-one-35:06a82bde-1180-46d0-8b4c-673689cf0526'
PARENT_PAYLOAD_HASH = '69db7ead44ed0ace20fde2e6b92567c50d4f914e048132362c27f395c6613b0c'
INHERITED_POLICIES = deepcopy({**parent_policy.INHERITED_POLICIES,
    'profit_protection_policy': parent_policy.PROFIT_PROTECTION_POLICY,
    'confirmed_ah_failure_policy': parent_policy.CONFIRMED_AH_FAILURE_POLICY,
    'liquidity_fade_policy': parent_policy.LIQUIDITY_FADE_POLICY})
ENTRY_ACTIVITY_POLICY = entry_activity_policy_payload()
BEHAVIOR = (
    'Strategy36 inherits the exact pinned Strategy35 activation, first-setup '
    'anchors, entry predicates, reentry, no-add policy, sizing, costs and all '
    'exit/protection precedence. One extra entry filter rejects when two latest '
    'completed native 5s candles have at most half the trades of the preceding '
    'two, provided preceding activity is positive. Four exact candles must be '
    'in the current extended session. This filter activates after 20 seconds '
    'of session history; earlier decisions retain parent eligibility. Missing '
    'candles after activation reject; observed zero is never inferred from '
    'absence. Rejected proposals do not redefine original episode/first-setup '
    'anchors. Accepted entries retain normalized native activity witnesses '
    'and independent source readback. V7 prior-day seeds and full regular-session '
    'warm-up for after-hours remain inherited. Backtest only; live and public '
    'resume remain closed.'
)


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=36, executor_strategy_id=parent.executor_strategy_id,
                  executor_revision=36, evaluation_interval=parent.evaluation_interval,
                  input_contracts=parent.input_contracts,
                  rule_set_contracts=parent.rule_set_contracts + (ENTRY_ACTIVITY_POLICY['policy_id'],),
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_prepared_strategy_thirty_six_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 36
            or type(strategy.get('revision')) is not int or strategy['revision'] != 36
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy36 declared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'entry_activity_policy': ENTRY_ACTIVITY_POLICY}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy36 manifest shape differs')
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
        raise ValueError('Strategy36 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy36 approval/code manifest seal differs')
    return manifest


def verify_installed_strategy_thirty_six_release(manifest):
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(36)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy36 published release differs from installed approval')
    return installed


def verify_strategy_thirty_six_manifest(strategy):
    manifest = verify_prepared_strategy_thirty_six_manifest(strategy)
    verify_installed_strategy_thirty_six_release(manifest)
    return manifest


def derive_strategy_thirty_six_configuration(source, *, approved_code_commit,
                                            approved_code_fingerprint, approval_reference):
    """Prepare an exact parent; this cannot substitute for installed launch gates."""
    if (source.strategy_number != 35 or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy36 must derive from exact pinned certified Strategy35')
    parent_policy.verify_strategy_thirty_five_manifest(source.payload['strategy'])
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy36 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES), 'entry_activity_policy': deepcopy(ENTRY_ACTIVITY_POLICY),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=36, revision=36, name='Early Squeeze Strategy 36',
                              profile_id='strategy-one-36', profile_revision=36, numbered_release=manifest)
    verify_prepared_strategy_thirty_six_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-36', revision=36,
        definition_revision=36, name='Early Squeeze Strategy 36', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle', {}).setdefault('trading_behavior', {})[
        'eligible_sessions'] = ['premarket', 'afterhours']
    payload['run_plan'].update(name='Strategy 36 Backtest',
        description='Sealed extended-session Strategy 36', profile_id='strategy-one-36')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-thirty-six-from:{PARENT_REVISION_ID}',
                source_candidate_hash=source.payload_hash,
                payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
                node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

"""Prepared exact-parent Strategy39 specification; execution is not installed."""
from copy import deepcopy
from hashlib import sha256
import re

from . import strategy_thirty_eight_release as parent_policy
from .strategy_half_risk_liquidity_fade import half_risk_liquidity_policy_payload
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-38:ede94a8a-b4be-4ab5-aa5f-e2a22ebdd8fd'
PARENT_PAYLOAD_HASH = 'cf37fd40a682687e115c5b6d37bad334029ffa48ac7ce7e5b0668513fcc85aff'
INHERITED_POLICIES = deepcopy({**parent_policy.INHERITED_POLICIES,
    'episode_activity_policy': parent_policy.EPISODE_ACTIVITY_POLICY})
HALF_RISK_LIQUIDITY_POLICY = half_risk_liquidity_policy_payload()
BEHAVIOR = (
    'Strategy39 inherits exact pinned Strategy38 activation, original episode '
    'and first-setup anchors, entry veto, entries, no adds, sizing, costs, '
    'protection, prior-day V7 seed and full RTH warmup for after-hours. All '
    'inherited exits retain precedence. An additional held-long exit requires '
    'completed 5s close and fresh bid at/below the midpoint of original ask '
    'and initial stop, bearish completed 5s MACD, prior10 > 0 and '
    '2 * recent10 <= prior10 native trade counts. Four contiguous completed '
    '5s bars must be wholly after native first-held in the same extended '
    'session. At the completed 100ms decision, bar age is below 5s and quote '
    'age at most 1s. Native manager/broker checkpoints and no pending exit '
    'are required. Missing observations are never synthesized. Elapsed '
    'holding age alone never exits; no maximum holding age is introduced. '
    'Backtest only; no live or public resume admission.'
)


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=39, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=39, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts + (HALF_RISK_LIQUIDITY_POLICY['policy_id'],),
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 38
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy39 requires exact pinned certified Strategy38')
    return parent_policy.verify_strategy_thirty_eight_manifest(source.payload['strategy'])


def verify_prepared_strategy_thirty_nine_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 39
            or type(strategy.get('revision')) is not int or strategy['revision'] != 39
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy39 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy39 prepared manifest shape differs')
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
        raise ValueError('Strategy39 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy39 prepared manifest seal differs')
    return manifest


def verify_installed_strategy_thirty_nine_release(manifest):
    """An explicit fail-closed boundary until complete installation is proven."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(39)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy39 published release differs from installed approval')
    return installed


def verify_strategy_thirty_nine_manifest(strategy):
    manifest = verify_prepared_strategy_thirty_nine_manifest(strategy)
    verify_installed_strategy_thirty_nine_release(manifest)
    return manifest


def derive_strategy_thirty_nine_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy39 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=39, revision=39, name='Early Squeeze Strategy 39',
        profile_id='strategy-one-39', profile_revision=39, numbered_release=manifest)
    verify_prepared_strategy_thirty_nine_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-39', revision=39,
        definition_revision=39, name='Early Squeeze Strategy 39', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 39 Backtest',
        description='Sealed extended-session Strategy 39', profile_id='strategy-one-39')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-thirty-nine-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

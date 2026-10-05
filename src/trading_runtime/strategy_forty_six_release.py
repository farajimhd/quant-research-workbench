"""Immutable native AH early-risk alternative; Backtest-only admission."""
from copy import deepcopy
from hashlib import sha256
import re
import json

from .early_original_risk_failure import EarlyOriginalRiskPolicy

from . import strategy_forty_two_release as parent_policy
from .strategy_half_risk_liquidity_fade import half_risk_liquidity_policy_payload
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-42:61d09336-6eb1-4298-bc8e-1b985e97aa78'
PARENT_PAYLOAD_HASH = '048fbd8a27269fcb7c12e1c49d7213e2eb08320a42d86e355d8e55fbdbce37b6'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
HALF_RISK_LIQUIDITY_POLICY = half_risk_liquidity_policy_payload()
EARLY_FAILURE_POLICY = EarlyOriginalRiskPolicy('ah-first-minute-quarter-original-risk@1', None, (1, 4))
EARLY_FAILURE_POLICY_PAYLOAD = json.loads(canonical_json(EARLY_FAILURE_POLICY.payload()))
BEHAVIOR = ('Strategy46 inherits exact pinned Strategy42 activation, entry and re-entry, '
    'no adds, sizing, costs, target selection, protection and every inherited exit in its original precedence. '
    'After all inherited exits, an additional AH-only failure applies within the first 60000ms '
    'inclusive after native first-held: a wholly post-held completed 5s close and fresh bid at or below '
    'original reference ask minus one quarter original risk, with completed 5s MACD line strictly '
    'below signal. Fresh quote age is at most 1000000us; no pending exit. PM and later half-risk '
    'negative-regime failure are unchanged. Missing observations never become synthetic prices, '
    'bars or counts. Time alone never exits. Original re-entry remains eligible. AH causal prior-day '
    'V7 and complete regular-session warmup remain required. Immutable Backtest-only release; '
    'no live or public resume admission and no profitability claim.')


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=46, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=46, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts + (EARLY_FAILURE_POLICY.policy_id,),
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
        raise ValueError('Strategy46 requires exact pinned certified Strategy42')
    return parent_policy.verify_prepared_strategy_forty_two_manifest(source.payload['strategy'])


def verify_prepared_strategy_forty_six_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 46
            or type(strategy.get('revision')) is not int or strategy['revision'] != 46
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy46 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY, 'early_original_risk_failure_policy': EARLY_FAILURE_POLICY_PAYLOAD}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy46 prepared manifest shape differs')
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
        raise ValueError('Strategy46 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy46 prepared manifest seal differs')
    return manifest


def derive_strategy_forty_six_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy46 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'early_original_risk_failure_policy': deepcopy(EARLY_FAILURE_POLICY_PAYLOAD),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=46, revision=46, name='Early Squeeze Strategy 46',
        profile_id='strategy-one-46', profile_revision=46, numbered_release=manifest)
    verify_prepared_strategy_forty_six_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-46', revision=46,
        definition_revision=46, name='Early Squeeze Strategy 46', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 46 Backtest',
        description='Sealed extended-session Strategy 46', profile_id='strategy-one-46')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-forty-six-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_installed_strategy_forty_six_release(manifest):
    """Source catalog admission is distinct from normalized publication."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(46)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, 46).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy46 published release differs from installed approval')
    return installed


def verify_strategy_forty_six_manifest(strategy):
    manifest = verify_prepared_strategy_forty_six_manifest(strategy)
    verify_installed_strategy_forty_six_release(manifest)
    return manifest

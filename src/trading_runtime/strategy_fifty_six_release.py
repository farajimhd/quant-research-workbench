"""Immutable declared entry spread cost from exact published50; Backtest-only."""
from copy import deepcopy
from hashlib import sha256
import re
import json

from . import strategy_fifty_release as parent_policy
from .strategy_half_risk_liquidity_fade import half_risk_liquidity_policy_payload
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-50:9760ccc0-cc0f-490d-8314-dd4e79c2304f'
PARENT_CODE_FINGERPRINT = 'a129e45ba7a835c72feb82ec27507ec66ab9cbee021da713edfee57fbf505bdb'
PARENT_PAYLOAD_HASH = 'ff18cb6e01ed5d9433018bd7325c432c1e8a7d64155f37858646888ef66e5d8e'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
HALF_RISK_LIQUIDITY_POLICY = half_risk_liquidity_policy_payload()
EARLY_FAILURE_POLICY = parent_policy.EARLY_FAILURE_POLICY
EARLY_FAILURE_POLICY_PAYLOAD = deepcopy(parent_policy.EARLY_FAILURE_POLICY_PAYLOAD)
from .entry_spread_risk import EntrySpreadRiskPolicy
ENTRY_SPREAD_RISK_POLICY = EntrySpreadRiskPolicy('entry-spread-at-most-one-half-original-risk@1', (1, 2))
ENTRY_SPREAD_RISK_POLICY_PAYLOAD = json.loads(canonical_json(ENTRY_SPREAD_RISK_POLICY.payload()))
QUOTE_SOURCE_CONTRACT = 'declared-entry-spread-risk-quote-source@2'
QUOTE_SOURCE_POLICY_PAYLOAD = {'contract': QUOTE_SOURCE_CONTRACT, 'predicate': 'source_qualified_uuid_tuple', 'attempt_output': 'distinct_liquidity_attempt_id', 'clock': 'exact_completed_100ms_proposal', 'coverage': 'exact_parent_eligible_keys', 'missing_coverage': 'fail_certification'}
BEHAVIOR = 'Strategy56 inherits exact published50 entries, reentry, protection, sizing, costs and all exits including AH early risk; no52 floor. After unchanged full first-anchor/price/activity/veto compilation, fresh ask-bid must be <= one half original ask-stop, exact integer equality allowed. Certified quote-source@2 uses source-qualified UUID predicates and distinct liquidity_attempt_id output. Exact completed proposal keys and pinned attempts are required; absent/foreign/duplicate coverage fails certification. Covered unavailable/stale quotes reject candidates, age<=1000000us. Original anchors remain fixed. Vectorized reduction precedes scheduler/Portfolio/broker/fills/OCA. Immutable Backtest-only; no profitability claim. Old53/54 quote-source remains unchanged.'


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=56, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=56, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts + ('declared-entry-spread-risk-quote-source@2', 'typed-entry-spread-risk-v4@1'),
        rule_set_contracts=parent.rule_set_contracts + (ENTRY_SPREAD_RISK_POLICY.policy_id,),
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 50
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy56 requires exact pinned certified Strategy50')
    manifest = parent_policy.verify_prepared_strategy_fifty_manifest(source.payload['strategy'])
    if manifest['approved_code_fingerprint'] != PARENT_CODE_FINGERPRINT:
        raise ValueError('Strategy56 requires exact published Strategy50 source approval')
    return manifest


def verify_prepared_strategy_fifty_six_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 56
            or type(strategy.get('revision')) is not int or strategy['revision'] != 56
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy56 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY, 'early_original_risk_failure_policy': EARLY_FAILURE_POLICY_PAYLOAD, 'entry_spread_risk_policy': ENTRY_SPREAD_RISK_POLICY_PAYLOAD, 'entry_spread_risk_quote_source': QUOTE_SOURCE_POLICY_PAYLOAD}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy56 prepared manifest shape differs')
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
        raise ValueError('Strategy56 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy56 prepared manifest seal differs')
    return manifest


def derive_strategy_fifty_six_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy56 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'early_original_risk_failure_policy': deepcopy(EARLY_FAILURE_POLICY_PAYLOAD),
        'entry_spread_risk_policy': deepcopy(ENTRY_SPREAD_RISK_POLICY_PAYLOAD),
        'entry_spread_risk_quote_source': deepcopy(QUOTE_SOURCE_POLICY_PAYLOAD),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=56, revision=56, name='Early Squeeze Strategy 56',
        profile_id='strategy-one-56', profile_revision=56, numbered_release=manifest)
    verify_prepared_strategy_fifty_six_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-56', revision=56,
        definition_revision=56, name='Early Squeeze Strategy 56', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 56 Backtest',
        description='Sealed extended-session Strategy 56', profile_id='strategy-one-56')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-fifty-six-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_installed_strategy_fifty_six_release(manifest):
    """Source catalog admission is distinct from normalized publication."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(56)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, 56).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy56 published release differs from installed approval')
    return installed


def verify_strategy_fifty_six_manifest(strategy):
    manifest = verify_prepared_strategy_fifty_six_manifest(strategy)
    verify_installed_strategy_fifty_six_release(manifest)
    return manifest

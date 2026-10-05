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
ENTRY_SPREAD_RISK_POLICY = EntrySpreadRiskPolicy('entry-spread-at-most-one-quarter-original-risk@1', (1, 4))
ENTRY_SPREAD_RISK_POLICY_PAYLOAD = json.loads(canonical_json(ENTRY_SPREAD_RISK_POLICY.payload()))
BEHAVIOR = ('Strategy53 inherits exact pinned published Strategy50 entries, frozen first anchors, '
    're-entry, sizing, capital, costs, initial protection and every exit, including its AH early-risk rule. '
    'After compiling the unchanged complete first-anchor, price-break, activity and episode-veto masks, '
    'an additional generic necessary admission condition limits fresh executable ask minus bid to '
    'at most one quarter of original reference ask minus original requested stop. '
    'Canonical integer cross multiplication allows equality. Quotes must come from the exact completed '
    'proposal boundary and pinned liquidity attempt, age <=1000000us. Missing or foreign coverage fails certification; explicitly unavailable or stale quotes reject the candidate. '
    'The columnar reduction precedes scheduler, Portfolio cash, broker liquidity, fills and OCA ordering. '
    'No Strategy52 profit floor, no new time exit, no replacement first anchor. Immutable Backtest-only '
    'and no profitability claim.')


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=53, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=53, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts + ('declared-entry-spread-risk-quote-source@1', 'typed-entry-spread-risk-v4@1'),
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
        raise ValueError('Strategy53 requires exact pinned certified Strategy50')
    manifest = parent_policy.verify_prepared_strategy_fifty_manifest(source.payload['strategy'])
    if manifest['approved_code_fingerprint'] != PARENT_CODE_FINGERPRINT:
        raise ValueError('Strategy53 requires exact published Strategy50 source approval')
    return manifest


def verify_prepared_strategy_fifty_three_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 53
            or type(strategy.get('revision')) is not int or strategy['revision'] != 53
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy53 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY, 'early_original_risk_failure_policy': EARLY_FAILURE_POLICY_PAYLOAD, 'entry_spread_risk_policy': ENTRY_SPREAD_RISK_POLICY_PAYLOAD}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy53 prepared manifest shape differs')
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
        raise ValueError('Strategy53 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy53 prepared manifest seal differs')
    return manifest


def derive_strategy_fifty_three_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy53 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'early_original_risk_failure_policy': deepcopy(EARLY_FAILURE_POLICY_PAYLOAD),
        'entry_spread_risk_policy': deepcopy(ENTRY_SPREAD_RISK_POLICY_PAYLOAD),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=53, revision=53, name='Early Squeeze Strategy 53',
        profile_id='strategy-one-53', profile_revision=53, numbered_release=manifest)
    verify_prepared_strategy_fifty_three_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-53', revision=53,
        definition_revision=53, name='Early Squeeze Strategy 53', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 53 Backtest',
        description='Sealed extended-session Strategy 53', profile_id='strategy-one-53')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-fifty-three-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_installed_strategy_fifty_three_release(manifest):
    """Source catalog admission is distinct from normalized publication."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(53)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, 53).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy53 published release differs from installed approval')
    return installed


def verify_strategy_fifty_three_manifest(strategy):
    manifest = verify_prepared_strategy_fifty_three_manifest(strategy)
    verify_installed_strategy_fifty_three_release(manifest)
    return manifest

"""Exact-parent Strategy42 owner-profile journal repair with sealed Backtest registration."""
from copy import deepcopy
from hashlib import sha256
import re

from . import strategy_forty_one_release as parent_policy
from .strategy_half_risk_liquidity_fade import half_risk_liquidity_policy_payload
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-41:6fd9d95a-3442-49cd-bf7d-2637815685dc'
PARENT_PAYLOAD_HASH = '8cf4474174c222b900d39b72f7d5f7dc759ede2e56e753599cd9a1262942211d'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
HALF_RISK_LIQUIDITY_POLICY = half_risk_liquidity_policy_payload()
BEHAVIOR = 'Strategy42 inherits the exact pinned Strategy41 trading policy unchanged, including original MACD episode and first-setup anchors, entry veto, no adds, sizing, costs, protection, exit precedence and half-risk liquidity failure. Prior-day causal V7 seed and full regular-session warmup remain required for after-hours. The compound Backtest V4 publisher now explicitly propagates its owner profile through sequential and parallel detail insertion, selecting exact IEEE 754 RowBinary input for Float64 families. HTTP transport and Keeper operation identity cover the complete binary request and payload; acknowledged asynchronous inserts, deduplication and exact canonical row hashes remain mandatory. No tolerance, rounding, fabricated observations or time-based holding exit. Four completed 5s bars remain wholly after native first-held with native manager/broker checkpoint references and no pending exit. Backtest only; no live or public resume admission.'


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=42, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=42, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts,
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 41
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy42 requires exact pinned certified Strategy41')
    return parent_policy.verify_strategy_forty_one_manifest(source.payload['strategy'])


def verify_prepared_strategy_forty_two_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 42
            or type(strategy.get('revision')) is not int or strategy['revision'] != 42
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy42 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy42 prepared manifest shape differs')
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
        raise ValueError('Strategy42 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy42 prepared manifest seal differs')
    return manifest


def verify_installed_strategy_forty_two_release(manifest):
    """Require the exact installed sealed release; publication needs full source proof."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(42)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy42 published release differs from installed approval')
    return installed


def verify_strategy_forty_two_manifest(strategy):
    manifest = verify_prepared_strategy_forty_two_manifest(strategy)
    verify_installed_strategy_forty_two_release(manifest)
    return manifest


def derive_strategy_forty_two_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy42 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=42, revision=42, name='Early Squeeze Strategy 42',
        profile_id='strategy-one-42', profile_revision=42, numbered_release=manifest)
    verify_prepared_strategy_forty_two_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-42', revision=42,
        definition_revision=42, name='Early Squeeze Strategy 42', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 42 Backtest',
        description='Sealed extended-session Strategy 42', profile_id='strategy-one-42')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-forty-two-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

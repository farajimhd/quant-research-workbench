"""Immutable declared completed10s entry growth from exact published50; Backtest-only."""
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
from .entry_momentum_growth import EntryMomentumGrowthPolicy
ENTRY_MOMENTUM_GROWTH_POLICY = EntryMomentumGrowthPolicy('entry-momentum-first-5-current-10-percent@1', (1, 20), (1, 10))
ENTRY_MOMENTUM_GROWTH_POLICY_PAYLOAD = ENTRY_MOMENTUM_GROWTH_POLICY.payload()
# Replace only legacy entry histogram growth declarations; preserve all50 exits/economics.
for key in ('strong_ten_second_momentum_policy','initial_strong_momentum_policy','first_setup_growth_policy'):
    INHERITED_POLICIES.pop(key)
INHERITED_POLICIES['first_price_break_policy'].update(first_momentum='declared_entry_momentum_growth_policy_first_fraction', current_momentum='declared_entry_momentum_growth_policy_current_fraction', afterhours_policy='unchanged_parent50_price_requirements')
BEHAVIOR = ('Strategy60 inherits exact published50 all exits, reentry, sizing, costs and protection. '
 'Entry/reentry require strict positive completed10s histogram growth: frozen original first structural '
 'anchor 5 percent and current candidate 10 percent. Original first anchor selected '
 'before momentum pruning, never shifted by missing/weak data. Exact completed producer Float64 '
 'observations and all independent price/activity/quote/source gates retained. No spread risk cap '
 'or Strategy52 profit floor. Immutable Backtest-only; no financial claim.')


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=60, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=60, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts + ('declared-first-current-momentum-source@1',),
        rule_set_contracts=tuple(v for v in parent.rule_set_contracts if v not in ('strategy-seventeen-positive-ten-second-histogram-growth-10pct-v1', 'strategy-eighteen-first-strong-momentum-setup-v1', 'strategy-twenty-six-premarket-first-setup-ten-second-growth-10pct-v1')) + (ENTRY_MOMENTUM_GROWTH_POLICY.policy_id,),
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
        raise ValueError('Strategy60 requires exact pinned certified Strategy50')
    manifest = parent_policy.verify_prepared_strategy_fifty_manifest(source.payload['strategy'])
    if manifest['approved_code_fingerprint'] != PARENT_CODE_FINGERPRINT:
        raise ValueError('Strategy60 requires exact published Strategy50 source approval')
    return manifest


def verify_prepared_strategy_sixty_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 60
            or type(strategy.get('revision')) is not int or strategy['revision'] != 60
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy60 prepared execution identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY, 'early_original_risk_failure_policy': EARLY_FAILURE_POLICY_PAYLOAD, 'entry_momentum_growth_policy': ENTRY_MOMENTUM_GROWTH_POLICY_PAYLOAD}
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy60 prepared manifest shape differs')
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
        raise ValueError('Strategy60 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy60 prepared manifest seal differs')
    return manifest


def derive_strategy_sixty_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    """Prepare an immutable candidate; this does not publish or admit execution."""
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy60 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'half_risk_liquidity_policy': deepcopy(HALF_RISK_LIQUIDITY_POLICY),
        'early_original_risk_failure_policy': deepcopy(EARLY_FAILURE_POLICY_PAYLOAD),
        'entry_momentum_growth_policy': deepcopy(ENTRY_MOMENTUM_GROWTH_POLICY_PAYLOAD),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=60, revision=60, name='Early Squeeze Strategy 60',
        profile_id='strategy-one-60', profile_revision=60, numbered_release=manifest)
    verify_prepared_strategy_sixty_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-60', revision=60,
        definition_revision=60, name='Early Squeeze Strategy 60', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle',{}).setdefault('trading_behavior',{})[
        'eligible_sessions'] = ['premarket','afterhours']
    payload['run_plan'].update(name='Strategy 60 Backtest',
        description='Sealed extended-session Strategy 60', profile_id='strategy-one-60')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-sixty-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_installed_strategy_sixty_release(manifest):
    """Source catalog admission is distinct from normalized publication."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(60)
    expected = release_contract()
    fixed_strategy_executor(installed.executor_strategy_id, 60).verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy60 published release differs from installed approval')
    return installed


def verify_strategy_sixty_manifest(strategy):
    manifest = verify_prepared_strategy_sixty_manifest(strategy)
    verify_installed_strategy_sixty_release(manifest)
    return manifest

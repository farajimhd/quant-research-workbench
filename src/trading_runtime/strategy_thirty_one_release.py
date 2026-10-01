"""Immutable Strategy 31 release; publication and execution preflight are separate."""
from .strategy_registry import NumberedStrategyRelease
from .strategy_thirty_release import release_contract as parent_release_contract
from .strategy_profit_giveback import POLICY_ID, profit_giveback_policy_payload
from copy import deepcopy
from hashlib import sha256
import re
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
from . import strategy_thirty_release as parent_policy

INHERITED_POLICIES = {
    'session_policy': parent_policy.session_policy(),
    'activation_policy': parent_policy.ACTIVATION_POLICY,
    'add_policy': parent_policy.ADD_POLICY,
    'trailing_policy': parent_policy.TRAILING_POLICY,
    'target_policy': parent_policy.TARGET_POLICY,
    'entry_price_policy': parent_policy.ENTRY_PRICE_POLICY,
    'followthrough_policy': parent_policy.FOLLOWTHROUGH_POLICY,
    'entry_scope_policy': parent_policy.ENTRY_SCOPE_POLICY,
    'recent_bos_policy': parent_policy.RECENT_BOS_POLICY,
    'momentum_policy': parent_policy.MOMENTUM_POLICY,
    'strong_ten_second_momentum_policy': parent_policy.STRONG_TEN_SECOND_MOMENTUM_POLICY,
    'initial_strong_momentum_policy': parent_policy.INITIAL_STRONG_MOMENTUM_POLICY,
    'first_setup_growth_policy': parent_policy.FIRST_SETUP_GROWTH_POLICY,
    'first_price_break_policy': parent_policy.FIRST_PRICE_POLICY,
    'premarket_failure_policy': parent_policy.PREMARKET_FAILURE_POLICY,
}

PARENT_REVISION_ID = 'strategy-one-30:35ea2e85-6e6b-4d08-a915-559b21afda6a'
PARENT_PAYLOAD_HASH = '198ff70b7acf3ff5651d6c2e2b2f17075298a102e45e0ecd417cc07cce0d7327'
PROFIT_PROTECTION_POLICY = {
    **profit_giveback_policy_payload(),
    'arming_checkpoint': 'one_native_complete_checkpoint_when_one_original_risk_is_reached',
    'arming_confirmation': 'durable_verified_snapshot_and_committed_market_cursor_before_exit',
    'later_high_reference': 'frozen_prior_arming_checkpoint_high',
    'witness_contract': 'trading_profit_giveback_v4',
    'parent_release_revision': PARENT_REVISION_ID,
    'parent_release_payload': PARENT_PAYLOAD_HASH,
}
BEHAVIOR = (
    'Strategy 31 inherits exact pinned Strategy 30 entries, original-risk '
    'failure exits, sizing, costs, targets and structural protection. A '
    'completed position high reaching one original ask-to-stop risk arms '
    'profit protection through a single native committed checkpoint. A later '
    'completed 5s close and fresh current bid at or below half that risk above '
    'the original ask, with MACD line below signal, proposes an urgent full '
    'position exit. Current-bucket arming cannot exit immediately. The exact '
    'prior checkpoint high, source entry and causal clocks are normalized '
    'witness facts. Missing evidence and pending exits do not create synthetic '
    'orders. V7 prior-day seeding and RTH warming remain inherited. Backtest '
    'only; live and public resume remain closed.'
)


def release_contract() -> NumberedStrategyRelease:
    """Sealed source declaration; database publication is a separate approval."""
    parent = parent_release_contract()
    values = dict(number=31, executor_strategy_id=parent.executor_strategy_id,
                  executor_revision=31, evaluation_interval=parent.evaluation_interval,
                  input_contracts=parent.input_contracts,
                  rule_set_contracts=(*parent.rule_set_contracts, POLICY_ID),
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def verify_installed_strategy_thirty_one_release(manifest):
    """Require installed executor and exact immutable publication content."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(31)
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision)
    expected = release_contract()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest
            or manifest.get('profit_protection_policy') != PROFIT_PROTECTION_POLICY
            or manifest.get('source_revision_id') != PARENT_REVISION_ID
            or manifest.get('source_payload_hash') != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy 31 published release differs from installed approval')
    return installed


def verify_strategy_thirty_one_manifest(strategy):
    """Validate exact policy content, installed identity and approval seal."""
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 31
            or type(strategy.get('revision')) is not int or strategy['revision'] != 31
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy 31 installed execution identity differs')
    manifest = strategy.get('numbered_release')
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id',
                'source_payload_hash', 'manifest_hash', 'profit_protection_policy', *INHERITED_POLICIES}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy 31 manifest shape differs')
    verify_installed_strategy_thirty_one_release(manifest)
    if (any(manifest[name] != policy for name, policy in INHERITED_POLICIES.items())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch(r'[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy 31 manifest differs from inherited policy or code approval')
    seal = sha256(canonical_json({key: value for key, value in manifest.items()
                                 if key != 'manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy 31 manifest approval/code seal differs')
    return manifest


def derive_strategy_thirty_one_configuration(source, *, approved_code_commit,
                                            approved_code_fingerprint, approval_reference):
    """Derive from the exact immutable parent without changing inherited data."""
    if (source.strategy_number != 30 or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy 31 must derive from exact pinned certified Strategy 30')
    parent_policy.verify_strategy_thirty_manifest(source.payload['strategy'])
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy 31 compiler cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**deepcopy(INHERITED_POLICIES),
        'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': PARENT_REVISION_ID, 'source_payload_hash': PARENT_PAYLOAD_HASH,
        'profit_protection_policy': deepcopy(PROFIT_PROTECTION_POLICY)}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=31, revision=31, name='Early Squeeze Strategy 31',
        profile_id='strategy-one-31', profile_revision=31, numbered_release=manifest)
    verify_strategy_thirty_one_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-31', revision=31,
        definition_revision=31, name='Early Squeeze Strategy 31', description=release.behavior_specification)
    payload['strategy_profile'].setdefault('lifecycle', {}).setdefault('trading_behavior', {})[
        'eligible_sessions'] = ['premarket', 'afterhours']
    payload['run_plan'].update(name='Strategy 31 Backtest',
        description='Sealed extended-session Strategy 31', profile_id='strategy-one-31')
    nodes = encode_nodes(payload)
    return {'source_candidate_id': f'strategy-thirty-one-from:{PARENT_REVISION_ID}',
        'source_candidate_hash': source.payload_hash,
        'payload_hash': sha256(canonical_json(payload).encode()).hexdigest(),
        'node_hash': node_hash(nodes), 'node_count': len(nodes), 'payload': payload}

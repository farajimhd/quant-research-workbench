"""Immutable Strategy 34 declaration; publication requires independent approval."""
from copy import deepcopy
from hashlib import sha256
import re

from .strategy_registry import NumberedStrategyRelease
from . import strategy_thirty_three_release as parent_policy
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash

PARENT_REVISION_ID = 'strategy-one-33:66f5cdae-3cbb-4fdd-af99-a72d22e77e6c'
PARENT_PAYLOAD_HASH = '1dcf2e4d52de04e710880fc1be221ae68fafb4ed5a441691edd183e8c092815b'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
PROFIT_PROTECTION_POLICY = deepcopy(parent_policy.PROFIT_PROTECTION_POLICY)
CONFIRMED_AH_FAILURE_POLICY = {
    'policy_id': 'strategy.confirmed-ah-risk-failure.v1',
    'eligible_session': 'afterhours',
    'held_start_authority': 'native_first_held_boundary',
    'maximum_held_age_ms': 60_000,
    'maximum_held_age_inclusive': True,
    'loss_threshold': 'original_ask_minus_one_quarter_original_stop_distance',
    'price_confirmation': 'completed_5s_close_and_fresh_bid_at_or_below_threshold',
    'momentum_confirmation': 'completed_5s_and_completed_10s_macd_line_below_signal',
    'whole_held_candles': True,
    'maximum_10s_observation_age_ms_exclusive': 10_000,
    'maximum_quote_age_us_inclusive': 1_000_000,
    'pending_exit': 'reject',
    'missing_or_invalid_observation': 'reject',
    'exit_priority': 'inherited_failure_then_inherited_profit_then_confirmed_ah_failure',
    'native_witness_contract': 'trading_confirmed_ah_failure_v4',
}
BEHAVIOR = (
    'Strategy 34 inherits the exact pinned Strategy 33 entries, inputs, sizing, '
    'costs, structural protection, failure and profit exits. After inherited '
    'exits, an additional AH first-minute exit requires a completed whole-held '
    '5s close and fresh bid at or below entry minus one quarter original risk, '
    'with bearish completed whole-held 5s and latest 10s MACD. Missing, forming '
    'or stale observations and pending exits reject the additional rule. '
    'A separate complete normalized witness preserves both producer timeframes. '
    'V7 prior-day checkpoint and regular-session warm-up for after-hours remain '
    'inherited. Backtest only; public resume and live execution remain closed.'
)


def release_contract() -> NumberedStrategyRelease:
    """Seal declaration content; this does not install an executable revision."""
    parent = parent_policy.release_contract()
    values = dict(
        number=34, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=34, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts + (CONFIRMED_AH_FAILURE_POLICY['policy_id'],),
        behavior_specification=BEHAVIOR,
    )
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_installed_strategy_thirty_four_release(manifest):
    """Require the exact installed Backtest catalog seal before selection."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(34)
    expected = release_contract()
    fixed = fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision)
    fixed.verify()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest):
        raise ValueError('Strategy 34 published release differs from installed approval')
    return installed


def verify_strategy_thirty_four_manifest(strategy):
    """Validate exact policy content, installed identity and approval seal."""
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 34
            or type(strategy.get('revision')) is not int or strategy['revision'] != 34
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy 34 declared execution identity differs')
    manifest = strategy.get('numbered_release')
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
                'manifest_hash', 'profit_protection_policy', 'confirmed_ah_failure_policy', *INHERITED_POLICIES}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy 34 manifest shape differs')
    if (manifest['contract'] != release.canonical_payload()
            or manifest['approved_digest'] != release.approved_digest
            or manifest['source_revision_id'] != PARENT_REVISION_ID
            or manifest['source_payload_hash'] != PARENT_PAYLOAD_HASH
            or manifest['confirmed_ah_failure_policy'] != CONFIRMED_AH_FAILURE_POLICY
            or manifest['profit_protection_policy'] != PROFIT_PROTECTION_POLICY
            or any(manifest[name] != policy for name, policy in INHERITED_POLICIES.items())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch(r'[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy 34 differs from pinned policy or code approval')
    seal = sha256(canonical_json({k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    if manifest['manifest_hash'] != seal:
        raise ValueError('Strategy 34 approval/code manifest seal differs')
    verify_installed_strategy_thirty_four_release(manifest)
    return manifest


def derive_strategy_thirty_four_configuration(source, *, approved_code_commit,
                                             approved_code_fingerprint, approval_reference):
    """Prepare the exact parent derivation without installing an executor."""
    if (source.strategy_number != 33 or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy 34 must derive from exact pinned certified Strategy 33')
    parent_policy.verify_strategy_thirty_three_manifest(source.payload['strategy'])
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy 34 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {
        **deepcopy(INHERITED_POLICIES), 'contract': release.canonical_payload(),
        'approved_digest': release.approved_digest, 'approved_code_commit': approved_code_commit,
        'approved_code_fingerprint': approved_code_fingerprint, 'approval_reference': approval_reference,
        'publication_mode': 'backtest_only', 'source_revision_id': PARENT_REVISION_ID,
        'source_payload_hash': PARENT_PAYLOAD_HASH,
        'profit_protection_policy': deepcopy(PROFIT_PROTECTION_POLICY),
        'confirmed_ah_failure_policy': deepcopy(CONFIRMED_AH_FAILURE_POLICY),
    }
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=34, revision=34, name='Early Squeeze Strategy 34',
                              profile_id='strategy-one-34', profile_revision=34, numbered_release=manifest)
    verify_strategy_thirty_four_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-34', revision=34,
        definition_revision=34, name='Early Squeeze Strategy 34', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle', {}).setdefault('trading_behavior', {})[
        'eligible_sessions'] = ['premarket', 'afterhours']
    payload['run_plan'].update(name='Strategy 34 Backtest',
        description='Sealed extended-session Strategy 34', profile_id='strategy-one-34')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-thirty-four-from:{PARENT_REVISION_ID}',
                source_candidate_hash=source.payload_hash,
                payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
                node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

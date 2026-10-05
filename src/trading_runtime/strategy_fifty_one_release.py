"""Immutable native fixed swing ladder B baseline; exact42 economics only."""
from copy import deepcopy
from hashlib import sha256
import json
import re

from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_fifty_one_contract import strategy_fifty_one_contract

PARENT_REVISION_ID = 'strategy-one-42:61d09336-6eb1-4298-bc8e-1b985e97aa78'
PARENT_PAYLOAD_HASH = '048fbd8a27269fcb7c12e1c49d7213e2eb08320a42d86e355d8e55fbdbce37b6'
INPUT_CONTRACTS = (
    'certified-early-squeeze-signal-stream@completed-boundary',
    'arte.bars_v1.close@100ms:completed-observed-no-synthetic-buckets',
    'arte.liquidity_100ms_v1.execution_vwap@100ms:exact-ieee754',
    'arte.liquidity_100ms_v1@100ms:causal-session-shares-notional-and-10s-60s-trade-rates',
    'arte.liquidity_100ms_v1.bid-ask@100ms:quote-age<=1000000us',
    'arte.liquidity_execution_price_100ms_v1@100ms:later-certified-bucket-fills',
    'arte.strategy_one_pivot_interval_v1@as_of_1s:confirmed-causal-swing-low',
    'arte.structural_levels_v7@as_of_1s:complete-confirmed-native-band-geometry',
    'arte.strategy_one_identity_v1@session:stable-conid-ticker',
    'native-portfolio-broker-oms@exact-pre-entry-committed-prefix',
)
RULE_CONTRACTS = (
    'certified-early-squeeze-admission@1', 'completed-vwap-below-above-cross@1',
    'prepared-ladder-strict-liquidity@1', 'later-frozen-v7-upper-break@1',
    'confirmed-swing-low-fixed-stop@1', 'three-nearest-complete-overhead-targets-equal@1',
    'fixed-swing-three-equal-once-session@1', 'native-portfolio-mandate-third@1',
    'causal-cumulative-eligible-notional-desc-conid-ticker@1',
    'declared-population-exclusions@1:LGHL', 'extended-session-cutoff-liquidation@1', 'exact42-economic-configuration@1',
)
BEHAVIOR = ('Strategy51: certified EarlySqueeze admission, observed completed below-above VWAP cross '
    'with strict declared liquidity, then later causal frozen V7 upper-band break. The complete '
    'certified population excludes the user-declared LGHL ticker. Qualification '
    'freezes confirmed swing-low stop and three equal nearest complete overhead targets, one-tick '
    'buffers. Independent fixed lots; one accepted acquisition ACK/ticker/extended-session. '
    'No adds, reentry, trailing, replacement or overnight. One existing one-third mandate Portfolio '
    'request; exact42 budget/exposure/execution/costs. Rank causal completed cumulative eligible '
    'notional descending, stable conid/ticker ties. Acquisition ends PM19500000/AH57000000ms; '
    'liquidation starts PM19740000/AH57300000ms; deadlines PM19800000/AH57600000ms. Requested source '
    'prefixes end at actual extended session end; AH causal V7 and regular-session warmup required. '
    'Missing certified geometry/evidence fails closed. Backtest-only; no live/public-resume or '
    'profitability claim.')


def _normalized(value):
    return json.loads(canonical_json(value))


def policies():
    contract = strategy_fifty_one_contract()
    return _normalized(dict(automatic_entry_policy=contract.automatic_entry_policy.payload(),
        automatic_market_policy=contract.automatic_market_policy,
        session_policy=dict(acquisition_end_ms=dict(premarket=19_500_000, afterhours=57_000_000),
            liquidation_start_ms=dict(premarket=19_740_000, afterhours=57_300_000),
            deadline_ms=dict(premarket=19_800_000, afterhours=57_600_000), residual_at_end='fail'),
        ranking_policy=dict(score='completed_cumulative_eligible_notional', direction='descending',
            ties=['conid', 'ticker'], clock='current_completed_boundary'),
        source_policy=dict(missing='fail_closed', clock='completed_boundary_as_of',
            geometry='confirmed_causal_native_v7_and_swing_low', ah_warmup='complete_regular_session',
            source='certified_arte_only', indicator_computation='none',
            prior_checkpoint='exact_previous_xnys_session_canonical_nonprovisional',
            prior_checkpoint_availability='at_or_before_0400_ny_target_day'),
        economic_policy=dict(source_revision_id=PARENT_REVISION_ID,
            source_payload_hash=PARENT_PAYLOAD_HASH, scope='budget_exposure_execution_sizing_costs_only',
            capital_request_mode='mandate_fraction', capital_request_value=1/3)))


def release_contract():
    values = dict(number=51, executor_strategy_id='early-squeeze-strategy', executor_revision=51,
        evaluation_interval='100ms', input_contracts=INPUT_CONTRACTS,
        rule_set_contracts=RULE_CONTRACTS, behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .strategy_forty_two_release import verify_prepared_strategy_forty_two_manifest
    if (type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy51 requires exact pinned certified Strategy42 economics')
    return verify_prepared_strategy_forty_two_manifest(source.payload['strategy'])


def verify_prepared_strategy_fifty_one_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 51
            or type(strategy.get('revision')) is not int or strategy['revision'] != 51
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != '100ms'):
        raise ValueError('Strategy51 prepared identity differs')
    manifest = strategy.get('numbered_release')
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
        'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
        'manifest_hash', *policies()}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy51 complete manifest shape differs')
    if (manifest['contract'] != release.canonical_payload()
            or manifest['approved_digest'] != release.approved_digest
            or manifest['source_revision_id'] != PARENT_REVISION_ID
            or manifest['source_payload_hash'] != PARENT_PAYLOAD_HASH
            or any(manifest[name] != policy for name, policy in policies().items())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch('[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch('[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy51 differs from complete pinned B policy')
    if manifest['manifest_hash'] != sha256(canonical_json(
            {k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest():
        raise ValueError('Strategy51 manifest seal differs')
    return manifest


def derive_strategy_fifty_one_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy51 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**policies(), 'contract':release.canonical_payload(),
        'approved_digest':release.approved_digest, 'approved_code_commit':approved_code_commit,
        'approved_code_fingerprint':approved_code_fingerprint, 'approval_reference':approval_reference,
        'publication_mode':'backtest_only', 'source_revision_id':PARENT_REVISION_ID,
        'source_payload_hash':PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=51, revision=51, name='Early Squeeze Strategy 51',
        profile_id='strategy-one-51', profile_revision=51, numbered_release=manifest)
    verify_prepared_strategy_fifty_one_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-51', revision=51,
        definition_revision=51, name='Early Squeeze Strategy 51', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle', {})['trading_behavior'] = dict(
        eligible_sessions=['premarket','afterhours'], automatic_entry='fixed_swing_three_equal_ladder',
        allows_adds=False, allows_reentry=False, trailing=False, replacement=False)
    payload['run_plan'].update(name='Strategy 51 Backtest',
        description='Sealed fixed swing ladder Strategy 51', profile_id='strategy-one-51')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-fifty-one-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash, payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_strategy_fifty_one_manifest(strategy):
    manifest = verify_prepared_strategy_fifty_one_manifest(strategy)
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    release = release_contract()
    if numbered_strategy(51) != release:
        raise ValueError('Strategy51 installed release differs')
    fixed_strategy_executor(release.executor_strategy_id, 51).verify()
    return manifest

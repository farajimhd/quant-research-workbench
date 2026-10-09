"""Immutable native fixed swing ladder B baseline; exact42 economics only."""
from copy import deepcopy
from hashlib import sha256
import json
import re

from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_ninety_seven_contract import strategy_ninety_seven_contract

PARENT_REVISION_ID = 'strategy-one-42:61d09336-6eb1-4298-bc8e-1b985e97aa78'
PARENT_PAYLOAD_HASH = '048fbd8a27269fcb7c12e1c49d7213e2eb08320a42d86e355d8e55fbdbce37b6'
PARENT_CODE_COMMIT = '9ad409381449c1f1b859206093b6851282b111bb'
PARENT_CODE_FINGERPRINT = 'bfa8ad70e0584f27d3159ee1017a78d50ec53e6f7ae2dc601e98429f964eb62e'
INPUT_CONTRACTS = (
    'declared-automatic-ladder-native-adapter@1',
    'ladder-wait-first-complete-geometry-v1',
    'arte.trading_squeeze_ladder_geometry_binding_v1@exact-parent:earliest-causal-pair',
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
    'ladder-wait-first-complete-geometry-v1',
    'certified-early-squeeze-admission@1', 'completed-first-eligible-above-vwap@1',
    'prepared-ladder-strict-liquidity@1', 'later-frozen-v7-upper-break@1',
    'confirmed-swing-low-fixed-stop@1', 'three-nearest-complete-overhead-targets-equal@1',
    'fixed-swing-three-equal-once-session@1', 'native-portfolio-mandate-third@1',
    'causal-cumulative-eligible-notional-desc-conid-ticker@1',
    'extended-session-cutoff-liquidation@1', 'exact42-economic-configuration@1',
)
BEHAVIOR = ('Strategy97: certified EarlySqueeze admission and first eligible completed price above VWAP '
    'with strict liquidity trigger waiting. Within original admission TTL/cutoff, the first current '
    'eligible complete confirmed resistance/swing-low pair freezes once; only a later completed '
    'frozen V7 upper-band breakout may enter. Three equal nearest complete overhead targets, '
    'confirmed fixed swing-low stop and one-tick buffers. Independent fixed lots; one accepted '
    'acquisition ACK/ticker/extended session. No adds, reentry, trailing, replacement or overnight. '
    'Existing one-third mandate Portfolio request; exact42 cash/sizing/exposure/execution/costs. '
    'Rank completed cumulative eligible notional descending with stable conid/ticker ties. '
    'Exact declared PM/AH acquisition and liquidation windows; actual session-end source prefixes '
    'and complete regular-session AH warmup. Certified causal ARTE only; missing evidence fails '
    'closed. Prepared only: no registration, source approval, publication, financial result or live admission.')


def _normalized(value):
    return json.loads(canonical_json(value))


def policies():
    contract = strategy_ninety_seven_contract()
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
    values = dict(number=97, executor_strategy_id='early-squeeze-strategy', executor_revision=97,
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
        raise ValueError('Strategy97 requires exact pinned certified Strategy42 economics')
    manifest = verify_prepared_strategy_forty_two_manifest(source.payload['strategy'])
    if (manifest['approved_code_commit'] != PARENT_CODE_COMMIT
            or manifest['approved_code_fingerprint'] != PARENT_CODE_FINGERPRINT):
        raise ValueError('Strategy97 exact parent approved source differs')
    return manifest


def verify_prepared_strategy_ninety_seven_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != 97
            or type(strategy.get('revision')) is not int or strategy['revision'] != 97
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != '100ms'):
        raise ValueError('Strategy97 prepared identity differs')
    manifest = strategy.get('numbered_release')
    required = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
        'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash',
        'manifest_hash', *policies()}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy97 complete manifest shape differs')
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
        raise ValueError('Strategy97 differs from complete pinned B policy')
    if manifest['manifest_hash'] != sha256(canonical_json(
            {k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest():
        raise ValueError('Strategy97 manifest seal differs')
    return manifest


def derive_strategy_ninety_seven_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy97 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**policies(), 'contract':release.canonical_payload(),
        'approved_digest':release.approved_digest, 'approved_code_commit':approved_code_commit,
        'approved_code_fingerprint':approved_code_fingerprint, 'approval_reference':approval_reference,
        'publication_mode':'backtest_only', 'source_revision_id':PARENT_REVISION_ID,
        'source_payload_hash':PARENT_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload['strategy'].update(strategy_number=97, revision=97, name='Early Squeeze Strategy 97',
        profile_id='strategy-one-97', profile_revision=97, numbered_release=manifest)
    verify_prepared_strategy_ninety_seven_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id='strategy-one-97', revision=97,
        definition_revision=97, name='Early Squeeze Strategy 97', description=BEHAVIOR)
    payload['strategy_profile'].setdefault('lifecycle', {})['trading_behavior'] = dict(
        eligible_sessions=['premarket','afterhours'], automatic_entry='fixed_swing_three_equal_ladder',
        allows_adds=False, allows_reentry=False, trailing=False, replacement=False)
    payload['run_plan'].update(name='Strategy 97 Backtest',
        description='Sealed fixed swing ladder Strategy 97', profile_id='strategy-one-97')
    nodes = encode_nodes(payload)
    return dict(source_candidate_id=f'strategy-ninety-seven-from:{PARENT_REVISION_ID}',
        source_candidate_hash=source.payload_hash, payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_strategy_ninety_seven_manifest(strategy):
    manifest = verify_prepared_strategy_ninety_seven_manifest(strategy)
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    release = release_contract()
    if numbered_strategy(97) != release:
        raise ValueError('Strategy97 installed release differs')
    fixed_strategy_executor(release.executor_strategy_id, 97).verify()
    return manifest

"""Strategy 13 pins one momentum change and the exact certified Strategy 12."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from pipelines.strategy_one.strategy_thirteen_configuration import compile_strategy_thirteen_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_thirteen_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, MOMENTUM_POLICY,
    verify_strategy_thirteen_manifest,
)
from src.trading_runtime.strategy_registry import numbered_strategy, numbered_strategy_parent, fixed_strategy_executor
from test_strategy_twelve_configuration import compile_twelve, pinned_parent as eleventh_parent
from test_fixed_numbered_registry import assignment


def parent():
    # Synthetic envelope metadata exercises the compiler, not DB certification.
    envelope = compile_twelve(eleventh_parent())
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(':')[1],
        PARENT_PAYLOAD_HASH, envelope['node_hash'], envelope['source_candidate_id'],
        envelope['source_candidate_hash'], 'test-only', envelope['payload'])


def compile_thirteen(source):
    return compile_strategy_thirteen_configuration(source, approved_code_commit='d' * 40,
        approved_code_fingerprint='e' * 64, approval_reference='development-rising-completed-momentum')


def test_exact_parent_preserved_with_one_new_policy_and_no_mutation():
    source = parent()
    before = deepcopy(source.payload)
    envelope = compile_thirteen(source)
    _verified_numbered_envelope(envelope)
    assert source.payload == before
    payload = envelope['payload']
    assert payload['strategy']['parameters'] == before['strategy']['parameters']
    manifest = payload['strategy']['numbered_release']
    for key in ('activation_policy', 'session_policy', 'add_policy', 'trailing_policy',
                'target_policy', 'entry_price_policy', 'followthrough_policy',
                'entry_scope_policy', 'recent_bos_policy'):
        assert manifest[key] == before['strategy']['numbered_release'][key]
    assert manifest['momentum_policy'] == MOMENTUM_POLICY
    assert manifest['source_revision_id'] == PARENT_REVISION_ID
    assert manifest['source_payload_hash'] == PARENT_PAYLOAD_HASH
    assert numbered_strategy_parent(13) == 12
    assert numbered_strategy(13).number == 13


@pytest.mark.parametrize('changes', [dict(attempt_id='00000000-0000-0000-0000-000000000013'),
                                     dict(payload_hash='0' * 64)])
def test_another_parent_is_not_an_authority(changes):
    with pytest.raises(ValueError, match='exact pinned'):
        compile_thirteen(replace(parent(), **changes))


@pytest.mark.parametrize('key,value', [('publication_mode', 'live'), ('momentum_policy', {}),
    ('recent_bos_policy', {}), ('source_payload_hash', '0' * 64)])
def test_resealed_policy_changes_are_rejected(key, value):
    strategy = compile_thirteen(parent())['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[key] = value
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                     if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='sealed contract'):
        verify_strategy_thirteen_manifest(strategy)


def test_backtest_only_registration_and_inherited_capabilities():
    release = numbered_strategy(13)
    executor = fixed_strategy_executor(release.executor_strategy_id, 13)
    selected = replace(assignment(), strategy_revision=13)
    contract = executor.build([selected], mode='backtest').contract
    assert contract.strategy_number == 13 and contract.caps_entry_at_reference_ask
    assert not contract.allows_adds and not contract.allows_target_escalation
    assert not contract.allows_completed_30s_trailing
    assert contract.allows_followthrough_failure_exit
    with pytest.raises(ValueError, match='Backtest-only'):
        executor.build([selected], mode='live')


def test_thirteenth_source_certificate_binds_actual_routes():
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    assert len(certify_numbered_fixed_v4_projection(13)) == 64


@pytest.mark.parametrize('relative,original,changed', [
    ('trading_runtime/strategy_rising_momentum_entry.py',
     'histogram > prior_histogram', 'histogram >= prior_histogram'),
    ('backend/backtest_strategy_one_execution.py',
     'candidate_indices=base_gate.eligible_indices', 'candidate_indices=None'),
    ('backend/backtest_strategy_one_static_gate.py',
     '(reasons == 0) & ~momentum_plan.requested_mask',
     '(reasons == 0) & momentum_plan.requested_mask'),
    ('trading_runtime/strategy_one_intent.py',
     'not numbered_momentum_entry(proposal.momentum, proposal.strategy_number)',
     'numbered_momentum_entry(proposal.momentum, proposal.strategy_number)'),
    ('backend/backtest_strategy_rising_momentum.py',
     'requested_mask[indices] = True', 'requested_mask[indices] = False'),
    ('trading_runtime/arte_journal_commit_v4.py',
     'seal_rising_momentum_rows(rising_momentum_rows, entry_rows,',
     'seal_rising_momentum_rows((), entry_rows,'),
])
def test_source_certificate_rejects_behavior_and_authority_mutations(tmp_path, relative, original, changed):
    from src.backend.backtest_fixed_v4_certification import certify_rising_momentum_entry_source
    source = (Path(__file__).parents[1] / 'src' / relative).read_text(encoding='utf-8')
    assert original in source
    mutated = tmp_path / 'mutated.py'
    mutated.write_text(source.replace(original, changed, 1), encoding='utf-8')
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        certify_rising_momentum_entry_source(source_overrides={relative: mutated})

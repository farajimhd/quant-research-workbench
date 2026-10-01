"""Successor repairs actual app selection without replacing published 13."""
from dataclasses import replace
from datetime import date
import pytest
from test_strategy_thirteen_configuration import parent as twelfth_parent, compile_thirteen
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.strategy_fourteen_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, verify_strategy_fourteen_manifest,
)
from pipelines.strategy_one.strategy_fourteen_configuration import compile_strategy_fourteen_configuration


def parent():
    value = compile_thirteen(twelfth_parent())
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(':')[1],
        PARENT_PAYLOAD_HASH, value['node_hash'], value['source_candidate_id'],
        value['source_candidate_hash'], 'test-only', value['payload'])


def test_fourteenth_preserves_exact_parent_rule_and_parameters():
    original = parent()
    result = compile_strategy_fourteen_configuration(original, approved_code_commit='d'*40,
        approved_code_fingerprint='e'*64, approval_reference='repair-numbered-admission')
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_fourteen_manifest(result['payload']['strategy'])
    before = original.payload['strategy']
    assert result['payload']['strategy']['parameters'] == before['parameters']
    for key in ('momentum_policy', 'recent_bos_policy', 'session_policy', 'followthrough_policy',
                'activation_policy', 'add_policy', 'trailing_policy', 'target_policy',
                'entry_price_policy', 'entry_scope_policy'):
        assert manifest[key] == before['numbered_release'][key]
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    assert numbered_strategy_parent(14) == 13
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    assert len(certify_numbered_fixed_v4_projection(14)) == 64


@pytest.mark.parametrize('number', [13, 14, 15, 16, 17])
def test_actual_selector_accepts_new_identities_then_checks_certified_release(monkeypatch, number):
    import src.backend.backtest_strategy_one_configuration as module
    release = parent()
    identity = f'strategy-one-{number}:{release.attempt_id}'
    class Selected:
        def revision(self):
            return {'revision_id': identity, 'run_plan_id': 'test-plan'}
    calls = []
    def certify(client, selected):
        calls.append((client, selected))
        return Selected()
    monkeypatch.setattr(module, 'certify_numbered_configuration', certify)
    client = object()
    assert module.selected_numbered_revision(revision_id=identity, client=client)['revision_id'] == identity
    assert calls == [(client, number)]
    with pytest.raises(ValueError, match='immutable release'):
        module.selected_numbered_revision(revision_id=identity, run_plan_id='foreign', client=client)


def test_fourteenth_normalized_direct_commit_and_cold_restore():
    from test_arte_rising_momentum_entry_v4 import unit, BitClient
    from test_arte_journal_commit_v4 import attached_v4_client
    from src.trading_runtime.arte_journal_commit_v4 import publish_strategy_one_entry_batch_v4, load_verified_v4_prefix
    from src.trading_runtime.arte_strategy_one_entry_journal import load_committed_strategy_one_entry_page
    proposal, value = unit(strategy_number=14)
    client = attached_v4_client(BitClient())
    publish_strategy_one_entry_batch_v4(client, value.base, entry_evidence=value.entry_evidence,
                                      momentum_evidence=value.momentum_evidence)
    prefix = load_verified_v4_prefix(client, value.base.run_id)
    page = load_committed_strategy_one_entry_page(client, prefix)
    assert page.entries[0].proposal == proposal


def test_momentum_companions_cannot_cross_successor_number():
    from test_arte_rising_momentum_entry_v4 import unit
    from src.trading_runtime.arte_rising_momentum_entry_v4 import seal_rising_momentum_rows
    from src.trading_runtime.arte_journal_writer import _sealed_families
    _, value = unit(strategy_number=14)
    foreign = tuple({**{key: item for key, item in row.items() if key != 'content_hash'},
                     'strategy_number': 13} for row in value.momentum_evidence)
    families = dict(_sealed_families(value.base))
    with pytest.raises(ValueError, match='unrelated typed parent'):
        seal_rising_momentum_rows(foreign, value.entry_evidence,
            families['trading_strategy_intent_v1'], families['trading_event_v1'])

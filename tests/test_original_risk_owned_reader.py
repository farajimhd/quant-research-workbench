"""Controlled native owners, typed source and Keeper lease; no live grants."""
from types import SimpleNamespace

import pytest

from test_original_risk_verification_reuse import fenced


@pytest.fixture
def owners(monkeypatch, fenced):
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime import original_risk_verification_reuse as reuse
    initialize_numbered_fixed_strategies()
    owner_client, source, _, _ = fenced
    manager = object.__new__(StrategyOneManagementRunner)
    manager.contract = numbered_fixed_strategy(116)
    publisher = object.__new__(BacktestTypedJournalPublisher)
    publisher.writer = SimpleNamespace(_client=owner_client, journal_profile='backtest_v4')
    publisher.journal = SimpleNamespace(run_id=source.run_id)
    publisher._first_price_source = source
    publisher.expected_config = dict(strategy_revision=116, strategy_id=manager.contract.strategy_id)
    calls = []
    class Reader:
        def execute(self, sql):
            calls.append(sql)
            return '1' if 'readonly' in sql else 'scalar response'
    reader = Reader()
    # Selection producer is controlled; declaration parsing and profile pairing
    # have separate real-adapter tests. The source and lease are real typed fixtures.
    monkeypatch.setattr(reuse, 'selected_policy', lambda client, source:
                        manager.contract.original_risk_verification_reuse_policy)
    return reader, manager, publisher, source, calls


def issue(owners):
    from src.backend.backtest_original_risk_owned_reader import bind_original_risk_checkpoint_reader
    reader, manager, publisher, source, _ = owners
    return bind_original_risk_checkpoint_reader(reader, manager=manager,
                                                publisher=publisher, run_id=source.run_id)


def test_issued_reader_borrows_only_original_owner_fence_and_routes_select(owners):
    proxy = issue(owners)
    _, _, publisher, _, calls = owners
    assert proxy.backtest_v4_lease is publisher.writer._client.backtest_v4_lease
    assert proxy.typed_insert_dispatch is publisher.writer._client.typed_insert_dispatch
    assert proxy.execute('SELECT 1') == 'scalar response'
    assert calls == ["SELECT getSetting('readonly')", 'SELECT 1']
    with pytest.raises(ValueError, match='SELECT-only'):
        proxy.execute('INSERT INTO arte.any VALUES (1)')


def test_native_batched_parenthesized_detail_query_uses_shared_select_admission(owners):
    proxy = issue(owners)
    query = "(SELECT 'family-a' AS family_name, '[]' AS payload) UNION ALL (SELECT 'family-b' AS family_name, '[]' AS payload) FORMAT JSONEachRow"
    assert proxy.execute(query) == 'scalar response'
    assert owners[4][-1] == "SELECT * FROM (" + query.removesuffix(' FORMAT JSONEachRow') + ") FORMAT JSONEachRow"


@pytest.mark.parametrize('query', [
    '(SELECT 1) UNION ALL (SELECT 2)',
    '(SELECT 1) UNION ALL (INSERT INTO arte.any VALUES (1)) FORMAT JSONEachRow',
    'SELECT 1; DROP TABLE arte.any',
])
def test_checkpoint_query_normalization_cannot_admit_mutation_or_unformatted_union(owners, query):
    proxy = issue(owners)
    before = len(owners[4])
    with pytest.raises(ValueError):
        proxy.execute(query)
    assert len(owners[4]) == before


@pytest.mark.parametrize('changed', ['contract', 'publisher', 'client', 'source', 'configuration'])
def test_changed_native_owner_binding_rejected(owners, changed):
    proxy = issue(owners)
    _, manager, publisher, _, _ = owners
    if changed == 'contract':manager.contract = object()
    elif changed == 'publisher':publisher.writer = object()
    elif changed == 'client':publisher.writer._client = object()
    elif changed == 'source':publisher._first_price_source = object()
    else:publisher.expected_config['strategy_revision'] = 115
    with pytest.raises(ValueError, match='binding changed'):
        proxy.execute('SELECT 1')


def test_unissued_lookalike_cannot_obtain_fence():
    from src.backend.backtest_original_risk_owned_reader import _OwnedCheckpointReader
    with pytest.raises(ValueError, match='no issued'):
        _OwnedCheckpointReader().execute('SELECT 1')


def test_unselected_strategy_keeps_original_reader(owners):
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    owners[1].contract = numbered_fixed_strategy(115)
    assert issue(owners) is owners[0]
    assert owners[4] == []


def test_inflight_writer_declines_binding_without_losing_cold_path(owners, fenced):
    fenced[3].inflight = ('current journal unit',)
    assert issue(owners) is owners[0]


def test_foreign_run_cannot_issue(owners):
    from src.backend.backtest_original_risk_owned_reader import bind_original_risk_checkpoint_reader
    reader, manager, publisher, _, _ = owners
    with pytest.raises(ValueError, match='publisher run/source/profile'):
        bind_original_risk_checkpoint_reader(reader, manager=manager,
                                             publisher=publisher, run_id='foreign')


def test_pending_owner_read_can_reuse_duplicate_children(monkeypatch, owners):
    from src.trading_runtime import arte_journal_commit_v4 as commit
    proxy = issue(owners)
    source = owners[3]
    calls = []
    def complete(**options):
        calls.append(options['batch_id'])
        if options['batch_id'] == 'root':
            for _ in range(2):
                commit.load_verified_commit_v4(proxy, run_id=source.run_id,
                                              batch_id='child', first_price_source=source)
        return {'batch_id': options['batch_id']}, ()
    monkeypatch.setattr(commit, '_load_verified_commit_v4_complete', complete)
    commit.load_verified_commit_v4(proxy, run_id=source.run_id,
                                  batch_id='root', first_price_source=source)
    assert calls == ['root', 'child']

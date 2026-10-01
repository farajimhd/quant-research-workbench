"""Real native row/commit transport; checkpoint source attestation mocked only."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.trading_runtime.arte_journal_commit_v4 import (
    _publish_typed_batch_v4, load_verified_commit_v4, load_verified_v4_prefix,
    publish_base_typed_batch_v4,
)
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.arte_journal_writer import typed_row, v4_storage_contracts, v4_journal_write_tables
from src.trading_runtime.arte_profit_giveback_v4 import PROFIT_GIVEBACK, V4ProfitGivebackBatch
from test_profit_giveback_typed_batch import unit
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import batch


def context(monkeypatch, strategy_number=31):
    from src.trading_runtime import strategy_profit_giveback_source as source
    base, row = unit(strategy_number=strategy_number)
    seed = batch()
    events = tuple(typed_row('trading_event_v1', {
        **{k: v for k, v in seed.events[0].items() if k != 'content_hash'},
        'run_id': base.run_id, 'batch_id': base.prior_batch_id, 'attempt_id': base.attempt_id,
        'record_id': str(UUID(int=100 + index)), 'sequence': index,
    }) for index in range(1, 10))
    preceding = replace(seed, run_id=base.run_id, attempt_id=base.attempt_id,
                        batch_id=base.prior_batch_id, last_sequence=9, events=events)
    client = attached_v4_client()
    publish_base_typed_batch_v4(client, preceding)
    prefix = load_verified_v4_prefix(client, base.run_id)
    calls = []
    def check(_client, proof, witness_row, financial, **kwargs):
        assert witness_row['source_manager_checkpoint_sequence'] == 7
        assert financial.position_quantity > 0
        calls.append((proof, witness_row['batch_id']))
    monkeypatch.setattr(source, 'load_profit_giveback_checkpoint', check)
    return client, base, row, prefix, calls


@pytest.mark.parametrize('compound', [False, True])
@pytest.mark.parametrize('number', [31, 32])
def test_profit_native_commit_idempotent_and_cold_prefix_seals(monkeypatch, compound, number):
    client, base, row, prefix, calls = context(monkeypatch, strategy_number=number)
    if compound:
        merged = mixed_unit(base, row)
        publish = lambda: publish_compound_v4(client, merged, verified_prior_prefix=prefix)
        last = merged.base
    else:
        publish = lambda: _publish_typed_batch_v4(client, base,
            profit_giveback_rows=(row,), verified_prior_prefix=prefix)
        last = base
    assert publish() == last.batch_id
    inserted = tuple(client.inserts)
    assert publish() == last.batch_id
    assert tuple(client.inserts) == inserted
    final = load_verified_v4_prefix(client, base.run_id)
    assert final.last_sequence == last.last_sequence
    assert final.batch_ids == (base.prior_batch_id, last.batch_id)
    assert calls and all(proof == prefix for proof, _ in calls)
    assert {identity for _, identity in calls} <= {base.batch_id, last.batch_id}
    with pytest.raises(RuntimeError, match='preceding prefix'):
        load_verified_commit_v4(client, run_id=base.run_id, batch_id=last.batch_id)
    # Changing a stored witness must fail the cold row hash check before the
    # mocked checkpoint source can make that altered witness look admissible.
    stored = client.tables[PROFIT_GIVEBACK.name][0]
    stored['prior_high_int'] += 1
    with pytest.raises(RuntimeError, match='row hash'):
        load_verified_v4_prefix(client, base.run_id)


@pytest.mark.parametrize('change', ['missing', 'wrong_head', 'wrong_sequence', 'live'])
def test_profit_publication_rejects_bad_context_before_inserting(monkeypatch, change):
    client, base, row, prefix, _ = context(monkeypatch)
    before = tuple(client.inserts)
    if change == 'missing':
        proof = None
    elif change == 'wrong_head':
        proof = replace(prefix, last_batch_id=str(UUID(int=99)))
    elif change == 'wrong_sequence':
        proof = replace(prefix, last_sequence=8)
    else:
        proof = prefix
        client.live_v4_lease = SimpleNamespace(run_id=base.run_id, assert_current=lambda: None)
    with pytest.raises(ValueError):
        _publish_typed_batch_v4(client, base, profit_giveback_rows=(row,),
                                verified_prior_prefix=proof)
    assert tuple(client.inserts) == before


def mixed_unit(base, row):
    seed = batch()
    next_id = str(UUID(int=6))
    event = typed_row('trading_event_v1', {
        **{k: v for k, v in seed.events[0].items() if k != 'content_hash'},
        'run_id': base.run_id, 'batch_id': next_id, 'sequence': 11,
        'attempt_id': base.attempt_id,
    })
    second = replace(seed, run_id=base.run_id, attempt_id=base.attempt_id,
                     batch_id=next_id, prior_batch_id=base.batch_id,
                     first_sequence=11, last_sequence=11, events=(event,))
    return coalesce_v4_units((V4ProfitGivebackBatch(base, row), second))


@pytest.mark.parametrize('compound', [False, True])
def test_profit_queue_verifies_and_publishes_on_worker_thread(monkeypatch, compound):
    from threading import get_ident
    from src.trading_runtime import arte_journal_writer as writer
    client, base, row, prefix, calls = context(monkeypatch)
    monkeypatch.setattr(writer, 'storage_preflight', lambda *a, **k: None)
    monkeypatch.setattr(writer, 'journal_permission_preflight', lambda *a, **k: None)
    monkeypatch.setattr(writer, '_verify_run_identity',
                        lambda *a: {'mode': 'backtest', 'account_ids': ('DU1',)})
    journal = writer.ArteJournalWriter(client, run_id=base.run_id,
        journal_profile='backtest_v4', coalesce_batches=False)
    caller = get_ident()
    threads = []
    execute = client.execute
    def tracked(sql):
        threads.append(get_ident())
        return execute(sql)
    client.execute = tracked
    try:
        envelope = mixed_unit(base, row) if compound else V4ProfitGivebackBatch(base, row)
        future = (journal.submit_compound_v4(envelope) if compound
                  else journal.submit_profit_exit_v4(envelope))
        assert future.result(timeout=5) == envelope.base.batch_id
        assert threads and all(thread != caller for thread in threads)
        assert calls and all(proof == prefix for proof, _ in calls)
        assert journal.metrics()['committed_event_rows'] == len(envelope.base.events)
        inserted = tuple(client.inserts)
        retry = (journal.submit_compound_v4(envelope) if compound
                 else journal.submit_profit_exit_v4(envelope))
        assert retry.result(timeout=5) == envelope.base.batch_id
        assert tuple(client.inserts) == inserted
        assert all(thread != caller for thread in threads)
    finally:
        journal.close()
    assert load_verified_v4_prefix(client, base.run_id).last_sequence == envelope.base.last_sequence


@pytest.mark.parametrize('profile', ['live_v4', 'v1', 'backtest_v3'])
def test_profit_queue_rejects_other_profiles_without_reads(profile):
    from src.trading_runtime.arte_journal_writer import ArteJournalWriter
    base, row = unit()
    journal = object.__new__(ArteJournalWriter)
    journal._journal_profile = profile
    journal._run_id = base.run_id
    with pytest.raises(ValueError, match='Backtest writer'):
        journal.submit_profit_exit_v4(V4ProfitGivebackBatch(base, row))
    with pytest.raises(ValueError):
        journal.submit_compound_v4(mixed_unit(base, row))


def test_verified_source_predecessor_does_not_rescan_cold_prefix(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commit
    client, base, row, preceding, _ = context(monkeypatch)
    _publish_typed_batch_v4(client, base, profit_giveback_rows=(row,),
                            verified_prior_prefix=preceding)
    final = load_verified_v4_prefix(client, base.run_id)
    monkeypatch.setattr(commit, 'load_verified_v4_prefix',
                        lambda *a, **k: pytest.fail('source context rescanned cold prefix'))
    assert commit.verified_batch_predecessor(client, final, base.batch_id) == preceding
    assert commit.verified_batch_predecessor(client, final, base.prior_batch_id) is None
    with pytest.raises(ValueError, match='outside'):
        commit.verified_batch_predecessor(client, preceding, base.batch_id)
    with pytest.raises(RuntimeError, match='sequence ceiling'):
        commit.verified_batch_predecessor(client, replace(final, last_sequence=9), base.batch_id)
    client.tables['trading_commit_v4'][-1]['prior_batch_id'] = str(UUID(int=99))
    with pytest.raises(RuntimeError, match='scalar seal'):
        commit.verified_batch_predecessor(client, final, base.batch_id)


def test_profit_contract_is_required_for_storage_and_writer_grants():
    assert PROFIT_GIVEBACK in v4_storage_contracts()
    assert PROFIT_GIVEBACK.name in v4_journal_write_tables()
    assert "storage_policy = 'live_market_ssd'" in PROFIT_GIVEBACK.ddl()


def test_profit_intent_without_scalar_witness_never_inserts(monkeypatch):
    client, base, _, prefix, _ = context(monkeypatch)
    before = tuple(client.inserts)
    with pytest.raises(ValueError, match='missing or extra'):
        _publish_typed_batch_v4(client, base, verified_prior_prefix=prefix)
    assert tuple(client.inserts) == before

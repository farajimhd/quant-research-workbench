"""Real V4 seals/snapshot validators; memo-only checkpoint cost seam is explicit.

These tests do not attest a native held Portfolio/OMS checkpoint or finance.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
import json
from uuid import UUID

import pytest

from src.trading_runtime import _checkpoint_prefix_read as subject
from src.trading_runtime import arte_journal_commit_v4 as commits
from src.trading_runtime import original_risk_checkpoint as checkpoint
from src.backend.backtest_strategy_one_management import (
    OriginalRiskManagementState, StrategyOneManagementState,
)
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import batch


def bit_projection_client():
    from tests.test_arte_journal_writer import MemoryClient
    class BitProjectionTransport(MemoryClient):
        def execute(self, sql):
            if sql.startswith('SELECT ') and 'reinterpretAsUInt64' in sql:
                import struct
                from src.trading_runtime.arte_rising_momentum_entry_v4 import VALUES
                name = sql.split('FROM arte.', 1)[1].split(' ', 1)[0]
                return '\n'.join(json.dumps({**row, **{
                    column + '_bits': None if row[column] is None else
                    int.from_bytes(struct.pack('>d', float(row[column])), 'big')
                    for column in VALUES}}) for row in self.tables.get(name, ()))
            return super().execute(sql)
    return attached_v4_client(BitProjectionTransport())


def committed_pair():
    client = bit_projection_client()
    first = batch()
    commits.publish_base_typed_batch_v4(client, first)
    next_id = str(UUID(int=91))
    from src.trading_runtime.arte_journal_writer import typed_row
    content = {k: v for k, v in first.events[0].items() if k != 'content_hash'}
    content.update(batch_id=next_id, record_id=str(UUID(int=92)), sequence=2)
    second = replace(first, batch_id=next_id, prior_batch_id=first.batch_id,
        first_sequence=2, last_sequence=2, source_cursor='bucket-2',
        events=(typed_row('trading_event_v1', content),))
    commits.publish_base_typed_batch_v4(client, second)
    return client, first, second


def probe_checkpoint(monkeypatch, *, defect=None):
    """Only costly held-graph reconstruction is substituted for memo unit tests.

    Actual commit/prefix verification and memo ownership are not substituted.
    Native held financial/source acceptance is deliberately not claimed.
    """
    calls = []
    def reconstruct(client, prefix, failure, parent, event, diagnostic, *, first_price_source):
        calls.append((prefix, failure, parent, event, diagnostic, first_price_source))
        if defect is not None:
            raise ValueError('explicit checkpoint reconstruction fixture failure')
        return OriginalRiskManagementState(31000, (), (), ())
    monkeypatch.setattr(checkpoint, '_load_original_risk_checkpoint', reconstruct)
    original = commits.load_verified_commit_v4
    def actual_batch_then_probe(*args, **kwargs):
        result = original(*args, **kwargs)
        prefix = kwargs.get('verified_prior_prefix')
        if prefix is not None:
            for _ in range(2):
                checkpoint.load_original_risk_checkpoint(args[0], prefix,
                    {'run_id': prefix.run_id, 'root': 'a'*64}, {'quantity': 10.0},
                    {'sequence': 3}, {'manager_root': 'b'*64, 'broker_root': 'c'*64},
                    first_price_source=kwargs.get('first_price_source'))
        return result
    monkeypatch.setattr(commits, 'load_verified_commit_v4', actual_batch_then_probe)
    return calls


def test_two_fresh_full_proofs_each_deduplicate_exact_checkpoint(monkeypatch):
    client, first, _ = committed_pair()
    calls = probe_checkpoint(monkeypatch)
    with subject._checkpoint_prefix_scope(client, run_id=first.run_id,
            checkpoint_sequence=2, first_price_source=None) as scope:
        prefix = subject._scoped_prefix(scope, client, first.run_id, 2, None)
        assert prefix.last_sequence == 2
        assert len(calls) == 1  # two identical attempts, one full reconstruction
    assert len(calls) == 2  # final proof has a NEW memo, not warmed approval
    assert subject._FULL_OPERATION.get() is None
    with pytest.raises(ValueError, match='operation'):
        subject._scoped_prefix(scope, client, first.run_id, 2, None)


@pytest.mark.parametrize('defect', ['detail', 'commit', 'family', 'append'])
def test_same_operation_final_full_proof_rejects_actual_normalized_mutation(defect):
    client, first, _ = committed_pair()
    inserts = len(client.inserts)
    with pytest.raises((ValueError, RuntimeError)):
        with subject._checkpoint_prefix_scope(client, run_id=first.run_id,
                checkpoint_sequence=2, first_price_source=None):
            if defect == 'detail':
                # Stored hash unchanged; actual non-key scalar mutation must reject.
                client.tables['trading_event_v1'][0]['account_id'] = 'foreign'
            elif defect == 'commit':
                client.tables['trading_commit_v4'][0]['source_cursor'] = 'changed'
            elif defect == 'family':
                client.tables['trading_commit_family_v4'][0]['row_count'] += 1
            else:
                client.tables['trading_commit_v4'].append(dict(client.tables['trading_commit_v4'][-1]))
    assert len(client.inserts) == inserts
    assert subject._FULL_OPERATION.get() is None


@pytest.mark.parametrize('field', ['client', 'run', 'source', 'sequence', 'bool_sequence'])
def test_shared_scope_rejects_crossed_read_bindings(field):
    client, first, _ = committed_pair()
    with subject._checkpoint_prefix_scope(client, run_id=first.run_id,
            checkpoint_sequence=2, first_price_source=None) as scope:
        args = [scope, client, first.run_id, 2, None]
        index, value = {'client': (1, object()), 'run': (2, 'foreign'),
            'source': (4, object()), 'sequence': (3, 1), 'bool_sequence': (3, True)}[field]
        args[index] = value
        with pytest.raises(ValueError, match='operation'):
            subject._scoped_prefix(*args)


def test_caller_prefix_and_fake_scope_cannot_supply_initial_authority():
    client, first, _ = committed_pair()
    prefix = commits.load_verified_v4_prefix(client, first.run_id)
    with pytest.raises(ValueError, match='factory'):
        subject._CheckpointPrefixReadScope(object(), client, None, prefix)
    with pytest.raises(ValueError, match='exact private'):
        subject._scoped_prefix(prefix, client, first.run_id, 2, None)


def test_scope_is_thread_owned_and_exception_closes_without_second_proof(monkeypatch):
    client, first, _ = committed_pair()
    reads = []
    original = commits.load_verified_v4_prefix
    def read(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(commits, 'load_verified_v4_prefix', read)
    with pytest.raises(LookupError):
        with subject._checkpoint_prefix_scope(client, run_id=first.run_id,
                checkpoint_sequence=2, first_price_source=None) as scope:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(subject._scoped_prefix, scope, client, first.run_id, 2, None)
                with pytest.raises(ValueError, match='operation'):
                    future.result()
            raise LookupError('operation aborted')
    assert reads == [1]
    assert not scope._active


def test_memo_requires_exact_internally_verified_predecessor(monkeypatch):
    client, first, _ = committed_pair()
    calls = []
    monkeypatch.setattr(checkpoint, '_load_original_risk_checkpoint',
        lambda *a, **k: calls.append(1) or OriginalRiskManagementState(31000, (), (), ()))
    actual = commits.load_verified_commit_v4
    def read(*args, **kwargs):
        result = actual(*args, **kwargs)
        prefix = kwargs.get('verified_prior_prefix')
        if prefix is not None:
            for candidate in (prefix, prefix, replace(prefix), replace(prefix)):
                checkpoint.load_original_risk_checkpoint(client, candidate,
                    {}, {}, {}, {}, first_price_source=None)
        return result
    monkeypatch.setattr(commits, 'load_verified_commit_v4', read)
    commits.load_verified_v4_prefix(client, first.run_id)
    assert len(calls) == 3  # same-valued caller-created prefix never gains memo authority


@pytest.mark.parametrize('field', ['failure', 'parent', 'event', 'diagnostic'])
def test_complete_input_change_never_reuses_checkpoint(field, monkeypatch):
    client, first, _ = committed_pair()
    calls = []
    monkeypatch.setattr(checkpoint, '_load_original_risk_checkpoint',
        lambda *a, **k: calls.append(1) or OriginalRiskManagementState(31000, (), (), ()))
    actual = commits.load_verified_commit_v4
    def read(*args, **kwargs):
        result = actual(*args, **kwargs)
        prefix = kwargs.get('verified_prior_prefix')
        if prefix is not None:
            values = [{'root': 'a'*64}, {'quantity': 1}, {'sequence': 3}, {'source': 'b'*64}]
            checkpoint.load_original_risk_checkpoint(client, prefix, *values, first_price_source=None)
            values[['failure', 'parent', 'event', 'diagnostic'].index(field)] = {'changed': True}
            checkpoint.load_original_risk_checkpoint(client, prefix, *values, first_price_source=None)
        return result
    monkeypatch.setattr(commits, 'load_verified_commit_v4', read)
    commits.load_verified_v4_prefix(client, first.run_id)
    assert len(calls) == 2


def test_failure_never_enters_memo_and_fresh_retry_reconstructs(monkeypatch):
    client, first, _ = committed_pair()
    calls = probe_checkpoint(monkeypatch, defect=True)
    for _ in range(2):
        with pytest.raises(ValueError, match='fixture failure'):
            commits.load_verified_v4_prefix(client, first.run_id)
        assert subject._FULL_OPERATION.get() is None
    assert len(calls) == 2


def test_successful_nonempty_result_is_copied_without_mutable_state_alias(monkeypatch):
    from dataclasses import fields
    from tests.test_strategy_one_management_snapshot import _rows
    from src.trading_runtime.strategy_one_management_snapshot import restore_manager_snapshot
    base = restore_manager_snapshot(_rows())
    state = OriginalRiskManagementState(**{f.name: getattr(base, f.name) for f in fields(base)})
    client, first, _ = committed_pair()
    calls = []
    monkeypatch.setattr(checkpoint, '_load_original_risk_checkpoint',
        lambda *a, **k: calls.append(1) or state)
    actual = commits.load_verified_commit_v4
    def read(*args, **kwargs):
        result = actual(*args, **kwargs)
        prefix = kwargs.get('verified_prior_prefix')
        if prefix is not None:
            loaded = [checkpoint.load_original_risk_checkpoint(client, prefix, {}, {}, {}, {},
                first_price_source=None) for _ in range(2)]
            assert loaded[0] == loaded[1] == state
            loaded[1].pending_breaks[0][1][0].level['lower'] = -1
            restored = checkpoint.load_original_risk_checkpoint(client, prefix, {}, {}, {}, {},
                first_price_source=None)
            assert restored == state
        return result
    monkeypatch.setattr(commits, 'load_verified_commit_v4', read)
    commits.load_verified_v4_prefix(client, first.run_id)
    assert len(calls) == 1


def test_concurrent_full_proofs_have_distinct_memos(monkeypatch):
    from threading import Barrier, Lock
    barrier, lock = Barrier(2), Lock()
    calls = []
    def reconstruct(*args, **kwargs):
        with lock:
            calls.append(args[1].run_id)
        barrier.wait(timeout=5)
        return OriginalRiskManagementState(31000, (), (), ())
    monkeypatch.setattr(checkpoint, '_load_original_risk_checkpoint', reconstruct)
    actual = commits.load_verified_commit_v4
    def read(*args, **kwargs):
        result = actual(*args, **kwargs)
        prefix = kwargs.get('verified_prior_prefix')
        if prefix is not None:
            for _ in range(2):
                checkpoint.load_original_risk_checkpoint(args[0], prefix, {}, {}, {}, {},
                    first_price_source=None)
        return result
    client, first, _ = committed_pair()
    monkeypatch.setattr(commits, 'load_verified_commit_v4', read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commits.load_verified_v4_prefix(client, first.run_id), range(2)))
    assert results[0] == results[1]
    assert calls == [first.run_id, first.run_id]


def test_nested_full_proofs_share_only_completed_exact_historical_ancestry(monkeypatch):
    client, first, _ = committed_pair()
    calls, nested = [], []
    monkeypatch.setattr(checkpoint, '_load_original_risk_checkpoint',
        lambda *a, **k: calls.append(1) or OriginalRiskManagementState(31000, (), (), ()))
    actual = commits.load_verified_commit_v4
    def read(*args, **kwargs):
        result = actual(*args, **kwargs)
        prefix = kwargs.get('verified_prior_prefix')
        if prefix is not None:
            if not nested:
                nested.append(True)
                # Nested call still independently seals both actual batches.
                commits.load_verified_v4_prefix(client, first.run_id)
            checkpoint.load_original_risk_checkpoint(client, prefix,
                {'root': 'a'*64}, {}, {}, {}, first_price_source=None)
        return result
    monkeypatch.setattr(commits, 'load_verified_commit_v4', read)
    commits.load_verified_v4_prefix(client, first.run_id)
    assert len(calls) == 1  # nested and outer ledger attest the same prior digest
    commits.load_verified_v4_prefix(client, first.run_id)
    assert len(calls) == 2  # separate outer operation cannot reuse past acceptance


def test_caps_and_scalar_aliases_do_not_silently_reuse(monkeypatch):
    client, first, _ = committed_pair()
    calls = []
    monkeypatch.setattr(subject, '_MEMO_ENTRY_LIMIT', 1)
    monkeypatch.setattr(checkpoint, '_load_original_risk_checkpoint',
        lambda *a, **k: calls.append(1) or OriginalRiskManagementState(31000, (), (), ()))
    actual = commits.load_verified_commit_v4
    def read(*args, **kwargs):
        result = actual(*args, **kwargs)
        prefix = kwargs.get('verified_prior_prefix')
        if prefix is not None:
            for quantity in (1, 1, True, True, 1.0, 1.0):
                checkpoint.load_original_risk_checkpoint(client, prefix, {},
                    {'quantity': quantity}, {}, {}, first_price_source=None)
        return result
    monkeypatch.setattr(commits, 'load_verified_commit_v4', read)
    commits.load_verified_v4_prefix(client, first.run_id)
    assert len(calls) == 5
    monkeypatch.setattr(subject, '_MEMO_BYTE_LIMIT', 1)
    calls.clear()
    commits.load_verified_v4_prefix(client, first.run_id)
    assert len(calls) == 6


def test_actual_genuine_entry_source_mutation_rejected_by_final_fresh_proof():
    from tests.test_original_risk_pending_snapshot import genuine_entry_graph
    from datetime import datetime, timezone
    run = 'component-entry-source'
    _, _, source, tables = genuine_entry_graph(run)
    from src.trading_runtime.arte_journal_writer import _canonical_typed_content
    tables = {name: [{**_canonical_typed_content(name,
                      {k: v for k, v in row.items() if k != 'content_hash'}),
                      'content_hash': row['content_hash']} for row in values]
              for name, values in tables.items()}
    commit, families = commits.prepare_commit_v4(run_id=run, run_month=date(2026, 8, 1),
        attempt_id=str(UUID(int=3)), batch_id=str(UUID(int=1)), prior_batch_id=str(UUID(int=0)),
        first_sequence=1, last_sequence=1, source_cursor='2026-08-18:41000', status='running',
        sealed_families=tuple((name, values) for name, values in tables.items()),
        committed_at=datetime.now(timezone.utc))
    client = bit_projection_client()
    client.tables.update(tables)
    client.tables['trading_commit_v4'] = [commit]
    client.tables['trading_commit_family_v4'] = list(families)
    assert commits.load_verified_v4_prefix(client, run, first_price_source=source).last_sequence == 1
    entered = []
    with pytest.raises((ValueError, RuntimeError)):
        with subject._checkpoint_prefix_scope(client, run_id=run,
                checkpoint_sequence=1, first_price_source=source):
            entered.append(True)
            # Same authority object, changed immutable certified plan token.
            object.__setattr__(source.plan, 'token', 'f'*64)
    assert entered == [True]
    assert not client.inserts


def snapshot_case():
    """Actual cursor/manager/broker schemas; synthetic SELECT/Keeper transport."""
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.arte_journal_projection import backtest_cursor_batch
    from src.trading_runtime.journal_contract import JournalRecord
    from src.trading_runtime import strategy_one_management_snapshot as manager
    from src.trading_runtime import strategy_one_broker_match_snapshot as broker
    from tests.test_arte_journal_writer import MemoryClient
    day = date(2026, 8, 18)
    run, batch_id, boundary = 'snapshot-operation', str(UUID(int=101)), 31000
    at = market_day_boundary(day, boundary)
    record = JournalRecord(str(UUID(int=102)), run, 1, at, at,
        'checkpoint', 'market_boundary', f'{day}:{boundary}', '',
        dict(session_date=str(day), boundary_ms=boundary, market_sequence=1,
            frame_as_of=None, frame_ticker=None, frame_timeframe=None, frame_sequence=None))
    batch_row = backtest_cursor_batch(record, run_month=day.replace(day=1),
        attempt_id=str(UUID(int=103)), batch_id=batch_id, prior_batch_id=str(UUID(int=0)),
        source_cursor=record.entity_id)
    class JoinedTransport(MemoryClient):
        def execute(self, sql):
            if sql.startswith('SELECT ') and ' WHERE snapshot_id=toUUID(' in sql:
                name = sql.split('FROM arte.', 1)[1].split(' ', 1)[0]
                return '\n'.join(json.dumps(row) for row in self.tables.get(name, ()))
            if sql.startswith('SELECT * FROM arte.'):
                name = sql.split('FROM arte.', 1)[1].split(' ', 1)[0]
                return '\n'.join(json.dumps(row) for row in self.tables.get(name, ()))
            if 'INNER JOIN arte.trading_event_v1' in sql:
                cursor = self.tables['trading_backtest_cursor_v1'][0]
                event = self.tables['trading_event_v1'][0]
                return json.dumps({**cursor, 'event_sequence': event['sequence'],
                    'event_category': event['category'], 'event_entity_type': event['entity_type'],
                    'event_entity_id': event['entity_id']})
            return super().execute(sql)
    client = attached_v4_client(JoinedTransport())
    commits.publish_base_typed_batch_v4(client, batch_row)
    state = StrategyOneManagementState(boundary, (), (), ())
    manager_rows = manager.project_manager_snapshot(run_id=run, session_date=day,
        checkpoint_sequence=1, state=state)
    client.tables[manager.PARENT.name] = [manager_rows.snapshot]
    from src.trading_runtime.strategy_one_protection_snapshot import TABLES as protection_tables
    for contract, values in zip(protection_tables, ((manager_rows.protection.snapshot,),
            manager_rows.protection.states, manager_rows.protection.resistances), strict=True):
        client.tables[contract.name] = list(values)
    broker_state = dict(schema_version=4, bar_mode=True, initial_time=market_day_boundary(day, 0),
        account_ids=('account',), cash={'account': 10000.0}, realized_pnl={'account': 0.0},
        positions={'account': []}, orders=(), next_order_id=1, next_execution_id=1,
        performance_extrema=dict(complete=False, as_of=None, unrealized=0, market_value=0,
            peak_unrealized=0, worst_unrealized=0, equity_peak=0, maximum_drawdown=0))
    broker_rows = broker.project_broker_match_snapshot(run_id=run, session_date=day,
        checkpoint_sequence=1, boundary_ms=boundary, state=broker_state)
    for contract, values in zip(broker.TABLES, (tuple([broker_rows.snapshot]),
            broker_rows.accounts, broker_rows.positions, broker_rows.open_orders,
            broker_rows.tickers, broker_rows.marks), strict=True):
        client.tables[contract.name] = list(values)
    class HeadTransport:
        def __init__(self, head):
            self.head, self.reads = head, 0
        def read_head(self, *, run_id):
            assert run_id == self.head.run_id
            self.reads += 1
            return self.head
    manager_head = HeadTransport(manager.ManagerSnapshotHead(
        run, 1, batch_id, manager_rows.snapshot['content_hash'], 0))
    broker_head = HeadTransport(broker.BrokerMatchHead(
        run, 1, batch_id, broker_rows.snapshot['content_hash'], 0))
    return client, run, manager, broker, manager_head, broker_head, state, broker_rows


def test_actual_manager_broker_semantics_use_shared_initial_and_independent_final_proof(monkeypatch):
    client, run, manager, broker, manager_head, broker_head, state, broker_rows = snapshot_case()
    reads = []
    original = commits.load_verified_v4_prefix
    def read(*a, **k):
        reads.append(1)
        return original(*a, **k)
    monkeypatch.setattr(commits, 'load_verified_v4_prefix', read)
    with subject._checkpoint_prefix_scope(client, run_id=run,
            checkpoint_sequence=1, first_price_source=None) as scope:
        assert manager._load_attested_manager_snapshot(client, manager_head,
            run_id=run, checkpoint_sequence=1, _scope=scope) == state
        assert broker._load_attested_broker_match_snapshot(client, broker_head,
            run_id=run, checkpoint_sequence=1, _scope=scope) == broker_rows
        assert len(reads) == 1
    assert len(reads) == 2
    assert (manager_head.reads, broker_head.reads) == (2, 2)
    # Public standalone APIs each independently cold-prove, with no scope input.
    assert manager.load_attested_manager_snapshot(client, manager_head,
        run_id=run, checkpoint_sequence=1) == state
    assert broker.load_attested_broker_match_snapshot(client, broker_head,
        run_id=run, checkpoint_sequence=1) == broker_rows
    assert len(reads) == 4


@pytest.mark.parametrize('target', ['manager', 'broker'])
@pytest.mark.parametrize('defect', ['raw_root', 'selected_hash', 'head_advance'])
def test_actual_semantic_snapshot_guard_rejects_corruption_and_head_changes(target, defect):
    client, run, manager, broker, mh, bh, _, _ = snapshot_case()
    head = mh if target == 'manager' else bh
    contract = manager.PARENT if target == 'manager' else broker.ROOT
    function = (manager._load_attested_manager_snapshot if target == 'manager'
                else broker._load_attested_broker_match_snapshot)
    inserts = len(client.inserts)
    with pytest.raises((ValueError, RuntimeError)):
        with subject._checkpoint_prefix_scope(client, run_id=run,
                checkpoint_sequence=1, first_price_source=None) as scope:
            if defect == 'raw_root':
                client.tables[contract.name][0]['boundary_ms'] += 100
            elif defect == 'selected_hash':
                head.head = replace(head.head, snapshot_hash='0'*64)
            else:
                original = head.read_head
                def changing(*, run_id):
                    result = original(run_id=run_id)
                    return replace(result, keeper_version=1) if head.reads > 1 else result
                head.read_head = changing
            function(client, head, run_id=run, checkpoint_sequence=1, _scope=scope)
    assert len(client.inserts) == inserts



def test_observation_hooks_are_noop_without_exact_private_collector():
    for value in (None,object(),{}, {'operation':object()}):
        token=subject._CHECKPOINT_OBSERVATION.set(value)
        try:
            subject._observe_checkpoint_financial(object(),object(),object(),object(),object(),(),
                first_price_source=object(),diagnostic=object())
            subject._observe_checkpoint_completed(object(),object(),object(),object(),object(),
                first_price_source=object(),diagnostic=object())
        finally:subject._CHECKPOINT_OBSERVATION.reset(token)
    with subject._full_prefix_operation(object(),'run',None):
        operation=subject._FULL_OPERATION.get()
        fake={'operation':operation}
        token=subject._CHECKPOINT_OBSERVATION.set(fake)
        try:
            subject._observe_checkpoint_financial(operation.client,object(),{},None,None,(),
                first_price_source=None,diagnostic=None)
            assert fake=={'operation':operation}
        finally:subject._CHECKPOINT_OBSERVATION.reset(token)



def test_financial_observer_other_exit_family_none_is_always_noop():
    client=object();source=object();prefix=object()
    with subject._full_prefix_operation(client,'run',source):
        operation=subject._FULL_OPERATION.get()
        collector=dict(operation=operation,client=client,source=source,prefix=prefix,
            diagnostic=None,thread=subject.get_ident())
        operation.collector=collector
        token=subject._CHECKPOINT_OBSERVATION.set(collector)
        try:
            subject._observe_checkpoint_financial(client,prefix,{},None,None,(),
                first_price_source=source,diagnostic=None)
            assert 'financial' not in collector
        finally:
            subject._CHECKPOINT_OBSERVATION.reset(token)
            operation.collector=None

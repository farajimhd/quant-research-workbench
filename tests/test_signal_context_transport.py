"""Physical run isolation while preserving existing logical source facts."""
from dataclasses import replace
import json
import pytest
from src.backend.signal_context_transport import (
    ContextSignalSessionHeadKeeper, ContextSignalSourceInsertDispatch,
    ContextSignalDispatchInsertDispatch, read_context_rows, require_context_components,
    verify_context_seal,
)
from src.backend.signal_execution_context import project_context_row, seal_context
from src.backend.signal_context_schema import context_table
from src.backend.signal_stream_typed_cursor import STATE_DELTA
from src.backend.signal_source_clickhouse_storage import ClickHouseSignalSourceReadStorage
from tests.test_signal_execution_context import context, row

class Client:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []
    def execute(self, sql):
        self.sql.append(sql)
        if "FROM arte.signal_execution_context_v2" in sql and hasattr(self, "context"):
            return json.dumps(seal_context(self.context))
        return "\n".join(json.dumps(item) for item in self.rows)


def test_reader_queries_context_and_validates_outer_hash():
    selected = context()
    original = row()
    client = Client([project_context_row(selected, STATE_DELTA, original)])
    client.context = selected
    reader = ClickHouseSignalSourceReadStorage(client, {}, source_context=selected)
    assert reader.read_cursor_rows(STATE_DELTA.name, session_key=selected.session_key, batch_sequence=1) == [original]
    assert context_table(STATE_DELTA).name in client.sql[1]
    assert f"source_context_id='{selected.context_id}'" in client.sql[1]
    foreign = replace(selected, run_id='actual-second')
    client.rows = [project_context_row(foreign, STATE_DELTA, original)]
    with pytest.raises(ValueError, match='integrity'):
        reader.read_cursor_rows(STATE_DELTA.name, session_key=selected.session_key, batch_sequence=1)


def test_context_inventory_and_row_bounds():
    selected = context()
    client = Client([])
    client.context = selected
    reader = ClickHouseSignalSourceReadStorage(client, {}, source_context=selected)
    assert reader.has_any_source_rows(session_key=selected.session_key) is False
    assert all(selected.context_id in sql for sql in client.sql)
    client.rows = [project_context_row(selected, STATE_DELTA, row())] * 2
    with pytest.raises(RuntimeError, match='bound'):
        read_context_rows(client, selected, STATE_DELTA, '1', 1)


def test_context_gate_and_head_identities_cannot_cross_repeat_runs():
    a, b = context(), replace(context(), run_id='actual-second')
    first = ContextSignalSourceInsertDispatch(object(), source_context=a)
    second = ContextSignalSourceInsertDispatch(object(), source_context=b)
    assert first.run_id != second.run_id
    with pytest.raises(ValueError, match='contexts'):
        first._scope(second.run_id)
    head_a = ContextSignalSessionHeadKeeper(object(), endpoint='127.0.0.1:9181', source_context=a)
    head_b = ContextSignalSessionHeadKeeper(object(), endpoint='127.0.0.1:9181', source_context=b)
    assert head_a._base(a.session_key) != head_b._base(b.session_key)
    require_context_components(a, first, head_a)
    with pytest.raises(ValueError, match='context'):
        require_context_components(a, second)
    dispatch = ContextSignalDispatchInsertDispatch(object(), source_context=a)
    assert dispatch.run_id.endswith(a.context_id)


def test_context_seal_missing_duplicate_or_other_run_rejected():
    selected = context()
    verify_context_seal(Client([seal_context(selected)]), selected)
    for rows in ([], [seal_context(selected)] * 2, [seal_context(replace(selected, run_id='other'))]):
        with pytest.raises(ValueError):
            verify_context_seal(Client(rows), selected)


def test_actual_registered_context_dispatch_intent_ack_and_cold_recovery():
    from src.backend.signal_execution_context import SignalExecutionContext
    from src.backend.signal_dispatch_registered_publication import publish_registered_intents, publish_registered_ack
    from src.backend.signal_dispatch_typed_cursor import project_dispatch_ack, DISPATCH_TABLES, read_committed_dispatch_prefix
    from src.backend.signal_dispatch_clickhouse_storage import ClickHouseDispatchColdStorage
    from tests.test_signal_dispatch_registered_publication import _MemoryClient
    from tests.test_signal_dispatch_typed_cursor import _input, _intents, ACTIVATION_HASH
    from test_keeper_ownership import _Client, _Store
    selected = SignalExecutionContext('actual-native-1', 'backtest', '2026-09-24', 'approved-revision-1', 'source-1')
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(intents, [dict(delivery_id=delivery['delivery_id'], ack_kind='activation_durable', activation_receipt_hash=ACTIVATION_HASH)], acknowledged_at='2026-09-24T14:00:01+00:00')
    client = ContextMemoryClient([selected])
    client.rows = {context_table(table).name: [] for table in DISPATCH_TABLES}
    dispatch = ContextSignalDispatchInsertDispatch(_Client(_Store(), 11), source_context=selected)
    dispatch.initialize_new_session(dispatch.run_id, has_ch_rows=False)
    publish_registered_intents(client, dispatch, run_id=dispatch.run_id, projected=intents)
    publish_registered_ack(client, dispatch, run_id=dispatch.run_id, intents=intents, projected=acks)
    dispatch.close_for_cold(dispatch.run_id)
    dispatch.assert_cold_receipts(dispatch.run_id, {1: (intents['commit']['content_hash'], acks['commit']['content_hash'])})
    assert all(row['source_context_id'] == selected.context_id for rows in client.rows.values() for row in rows)
    reader = ClickHouseDispatchColdStorage(client, source_context=selected)
    recovered = read_committed_dispatch_prefix(reader, session_key=selected.session_key, source_commit_hashes=('b' * 64,), configuration_revision_id=selected.configuration_revision, registered_dispatch=dispatch)
    assert recovered == ((intents, acks),)
    foreign = ClickHouseDispatchColdStorage(client, source_context=replace(selected, run_id='other'))
    with pytest.raises(ValueError, match='context'):
        read_committed_dispatch_prefix(foreign, session_key=selected.session_key, source_commit_hashes=('b' * 64,), configuration_revision_id=selected.configuration_revision, registered_dispatch=dispatch)


class ContextMemoryClient:
    def __init__(self, contexts):
        self.contexts = contexts
        self.rows = {}
        self.sql = []
    def execute(self, sql, **kwargs):
        import re
        self.sql.append(sql)
        if sql.startswith('INSERT'):
            name = sql.split('arte.', 1)[1].split(' ', 1)[0]
            self.rows.setdefault(name, []).extend(json.loads(line) for line in sql.split('FORMAT JSONEachRow\n', 1)[1].splitlines())
            return ''
        name = sql.split('FROM arte.', 1)[1].split(' ', 1)[0]
        context_id = re.search("source_context_id='([^']+)'", sql).group(1)
        if name == 'signal_execution_context_v2':
            rows = [seal_context(item) for item in self.contexts if item.context_id == context_id]
        else:
            rows = [item for item in self.rows.get(name, []) if item['source_context_id'] == context_id]
        seq = re.search(r'(?:source_batch_sequence|batch_sequence)=([0-9]+)', sql)
        event = re.search("event_id='([^']+)'", sql)
        if seq:
            rows = [item for item in rows if item.get('source_batch_sequence', item.get('batch_sequence')) == int(seq.group(1))]
        if event:
            rows = [item for item in rows if item['event_id'] == event.group(1)]
        return '\n'.join(json.dumps(item) for item in rows)


def test_two_real_registered_source_batches_same_day_are_isolated():
    from src.backend.signal_execution_context import SignalExecutionContext
    from src.backend.signal_source_registered_storage import RegisteredSignalSourceStorage
    from src.backend.signal_stream_typed_publication import _publish_one
    from tests.test_signal_stream_typed_publication import _batch
    from tests.test_arte_typed_insert_dispatch import Keeper
    batch = _batch()
    first = SignalExecutionContext('actual-native-1', 'backtest', batch.session_key, batch.configuration_revision, batch.source_revision)
    second = replace(first, run_id='actual-native-2')
    client = ContextMemoryClient([first, second])
    hashes = []
    tokens = []
    for selected in (first, second):
        reader = ClickHouseSignalSourceReadStorage(client, batch.catalogs, source_context=selected)
        dispatch = ContextSignalSourceInsertDispatch(Keeper(), source_context=selected)
        storage = RegisteredSignalSourceStorage(reader, client, dispatch, session_key=selected.session_key, configuration_revision=selected.configuration_revision, source_context=selected)
        storage.initialize_new_session()
        storage.begin_batch(batch)
        logical_hash = _publish_one(storage, batch)
        storage.finish_batch(batch, logical_hash)
        hashes.append(logical_hash)
        tokens.append([sql for sql in client.sql if sql.startswith('INSERT') and selected.context_id in sql])
    assert hashes[0] == hashes[1]
    assert all(tokens)
    assert all(first.context_id in sql for sql in tokens[0])
    assert all(second.context_id in sql for sql in tokens[1])
    assert all(len(rows) % 2 == 0 for rows in client.rows.values())


def test_context_seal_publication_is_once_and_ambiguous_write_blocks_retry(monkeypatch):
    from src.backend.signal_context_transport import publish_context_seal
    from src.trading_runtime.keeper_ownership import KeeperUnavailable
    from types import SimpleNamespace
    class NodeExistsError(Exception): pass
    class Keeper:
        def __init__(self): self.rows = {}
        def ensure_path(self, path): pass
        def create(self, path, value):
            if path in self.rows: raise NodeExistsError()
            self.rows[path] = (value, 0)
        def get(self, path):
            value, version = self.rows[path]
            return value, SimpleNamespace(version=version)
        def set(self, path, value, *, version):
            assert self.rows[path][1] == version
            self.rows[path] = (value, version + 1)
    selected = context()
    calls = []
    client = ContextMemoryClient([])
    def insert(writer, table, rows):
        calls.append(rows)
        client.contexts.append(selected)
    monkeypatch.setattr('src.market_engine.typed_product_insert.insert_product_rows', insert)
    keeper = Keeper()
    publish_context_seal(object(), client, keeper, selected)
    publish_context_seal(object(), client, keeper, selected)
    assert len(calls) == 1
    def uncertain(*args):
        calls.append('uncertain')
        raise TimeoutError('lost response')
    monkeypatch.setattr('src.market_engine.typed_product_insert.insert_product_rows', uncertain)
    other = replace(selected, run_id='actual-other')
    with pytest.raises(TimeoutError): publish_context_seal(object(), client, keeper, other)
    with pytest.raises(KeeperUnavailable, match='unresolved'): publish_context_seal(object(), client, keeper, other)
    assert calls.count('uncertain') == 1


def test_native_timestamp_wire_preserves_logical_hash_and_utc_instant():
    from datetime import datetime, timezone
    from src.backend.signal_typed_json_wire import typed_json_wire_row
    from src.backend.signal_stream_typed_readback import canonical_row
    selected = context()
    original = row()
    original['last_emitted_at'] = '2026-10-15T09:30:00.123456-04:00'
    from hashlib import sha256
    from src.trading_runtime.journal_contract import canonical_json
    original = canonical_row(STATE_DELTA, original)
    original['content_hash'] = sha256(canonical_json({key: value for key, value in original.items() if key != 'content_hash'}).encode()).hexdigest()
    envelope = project_context_row(selected, STATE_DELTA, original)
    wire = typed_json_wire_row(STATE_DELTA, envelope)
    assert wire['last_emitted_at'] == '2026-10-15 13:30:00.123456'
    assert envelope['last_emitted_at'] == '2026-10-15T13:30:00.123456+00:00'
    assert wire['content_hash'] == envelope['content_hash']
    assert wire['context_content_hash'] == envelope['context_content_hash']
    restored = {**wire, **canonical_row(STATE_DELTA, {key: wire[key] for key, _ in STATE_DELTA.columns})}
    from src.backend.signal_execution_context import restore_context_row
    assert restore_context_row(selected, STATE_DELTA, restored) == original
    with pytest.raises(ValueError, match='aware'):
        typed_json_wire_row(STATE_DELTA, {**original, 'last_emitted_at': '2026-10-15T13:30:00'})

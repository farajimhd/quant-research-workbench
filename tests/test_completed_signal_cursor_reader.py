from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import re

import pytest

from src.backend.completed_signal_cursor_reader import CompletedSignalCursorReadAhead
from src.backend.signal_execution_context import SignalExecutionContext, project_context_row, seal_context
from src.backend.signal_context_schema import LOGICAL_TABLES, context_table
from src.backend.signal_source_clickhouse_storage import ClickHouseSignalSourceReadStorage
from src.backend.signal_stream_typed_cursor import STATE_DELTA
from src.backend.signal_stream_typed_readback import recover_committed_head
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.journal_contract import canonical_json
from tests.test_signal_execution_context import context, row
from tests.test_signal_stream_typed_readback import _publish
from tests.test_signal_stream_typed_publication import FakeStorage


class Client:
    def __init__(self, selected, families):
        self.selected, self.families, self.queries = selected, families, []
    def execute(self, sql):
        self.queries.append(sql)
        if sql == "SELECT getSetting('readonly')":return '1'
        if 'FROM arte.signal_execution_context_v2' in sql:return json.dumps(seal_context(self.selected))
        name = re.search(r'FROM arte\.([a-z0-9_]+)', sql)[1]
        rows = deepcopy(self.families.get(name, []))
        span = re.search(r'batch_sequence>=(\d+) AND batch_sequence<=(\d+)', sql)
        exact = re.search(r'batch_sequence=(\d+)', sql)
        event = re.search(r"event_id='([^']+)'", sql)
        if span:rows = [r for r in rows if int(span[1]) <= r['batch_sequence'] <= int(span[2])]
        elif exact:rows = [r for r in rows if r['batch_sequence'] == int(exact[1])]
        if event:rows = [r for r in rows if r['event_id'] == event[1]]
        limit = int(re.search(r'LIMIT (\d+)', sql)[1])
        return '\n'.join(json.dumps(v) for v in rows[:limit])


def prefix(selected):
    return V4CommittedPrefix(selected.run_id, 10, '00000000-0000-0000-0000-000000000001',
        selected.session_key+':1000', 'completed', ('00000000-0000-0000-0000-000000000001',))


def rows_fixture(count=4):
    selected = context()
    values = []
    for sequence in range(1, count+1):
        value = {k:v for k,v in row().items() if k != 'content_hash'}
        value['batch_sequence'] = sequence
        value['content_hash'] = sha256(canonical_json(value).encode()).hexdigest()
        values.append(project_context_row(selected, STATE_DELTA, value))
    return selected, {context_table(STATE_DELTA).name: values}


def test_existing_full_cursor_and_occurrence_verifier_gets_identical_head():
    memory = FakeStorage()
    batch, _ = _publish(memory)
    selected = SignalExecutionContext('run', 'backtest', batch.session_key,
        batch.configuration_revision, batch.source_revision)
    specs = {table.name: table for table in LOGICAL_TABLES}
    families = {context_table(name).name: [project_context_row(selected, specs[name], v) for v in values]
                for name,values in memory.rows.items()}
    serial = ClickHouseSignalSourceReadStorage(Client(selected, families), batch.catalogs, source_context=selected)
    reader = CompletedSignalCursorReadAhead(Client(selected, families), batch.catalogs,
        source_context=selected, prefix=prefix(selected), batch_span=8)
    kwargs = dict(session_key=batch.session_key, configuration_revision=batch.configuration_revision,
        source_revision=batch.source_revision, catalogs=batch.catalogs)
    assert recover_committed_head(reader, **kwargs) == recover_committed_head(serial, **kwargs)
    reader.verify_unchanged()
    assert reader.read_ahead_counts()['family_packet_queries'] == 4


def test_read_ahead_reuses_bounded_family_packet_without_exposing_mutable_cache():
    selected, families = rows_fixture()
    reader = CompletedSignalCursorReadAhead(Client(selected, families), {}, source_context=selected,
        prefix=prefix(selected), batch_span=4)
    observed = []
    for sequence in range(1,5):
        observed.extend(reader.read_cursor_rows(STATE_DELTA.name, session_key=selected.session_key, batch_sequence=sequence))
    assert [v['batch_sequence'] for v in observed] == [1,2,3,4]
    observed[0]['ticker'] = 'changed'
    assert reader.read_cursor_rows(STATE_DELTA.name, session_key=selected.session_key, batch_sequence=1)[0]['ticker'] == 'ABC'
    assert reader.read_ahead_counts()['family_packet_queries'] == 1


def test_over_budget_packet_splits_and_preserves_every_original_row():
    selected, families = rows_fixture(8)
    reader = CompletedSignalCursorReadAhead(Client(selected, families), {}, source_context=selected,
        prefix=prefix(selected), batch_span=4, maximum_family_rows=2)
    observed = [reader.read_cursor_rows(STATE_DELTA.name, session_key=selected.session_key, batch_sequence=i)[0]['batch_sequence'] for i in range(1,9)]
    assert observed == list(range(1,9))
    counts = reader.read_ahead_counts()
    assert counts['capacity_retries'] == 1 and counts['retained_rows'] <= 2
    assert counts['family_packet_queries'] == 5 and counts['family_page_evictions'] == 3


def test_context_integrity_error_is_not_retried_or_hidden():
    selected, families = rows_fixture()
    families[context_table(STATE_DELTA).name][0]['ticker'] = 'tampered'
    reader = CompletedSignalCursorReadAhead(Client(selected, families), {}, source_context=selected,
        prefix=prefix(selected))
    with pytest.raises(ValueError):
        reader.read_cursor_rows(STATE_DELTA.name, session_key=selected.session_key, batch_sequence=1)
    assert reader.read_ahead_counts()['family_packet_queries'] == 1


def test_single_batch_overflow_still_fails_without_truncating_rows():
    selected, families = rows_fixture(1)
    extra = {k:v for k,v in row().items() if k != 'content_hash'}
    extra['ticker'] = 'XYZ'
    extra['content_hash'] = sha256(canonical_json(extra).encode()).hexdigest()
    families[context_table(STATE_DELTA).name].append(project_context_row(selected, STATE_DELTA, extra))
    reader = CompletedSignalCursorReadAhead(Client(selected, families), {}, source_context=selected,
        prefix=prefix(selected), batch_span=8, maximum_family_rows=1)
    with pytest.raises(RuntimeError, match='explicit read bound'):
        reader.read_cursor_rows(STATE_DELTA.name, session_key=selected.session_key, batch_sequence=1)
    assert reader.read_ahead_counts()['retained_rows'] == 0


@pytest.mark.parametrize('change', (dict(status='running'),dict(run_id='foreign')))
def test_read_ahead_rejects_nonterminal_or_foreign_native_authority(change):
    selected, families = rows_fixture()
    with pytest.raises(ValueError):
        CompletedSignalCursorReadAhead(Client(selected, families), {}, source_context=selected,
            prefix=replace(prefix(selected), **change))


def test_inventory_change_is_detected_after_cold_verification():
    selected, families = rows_fixture()
    reader = CompletedSignalCursorReadAhead(Client(selected, families), {}, source_context=selected,
        prefix=prefix(selected))
    reader.list_cursor_commits(session_key=selected.session_key)
    reader._base.list_cursor_commits = lambda **_: [{'batch_sequence': 1}]
    with pytest.raises(ValueError, match='inventory changed'):
        reader.verify_unchanged()


def test_commit_inventory_fence_uses_complete_sequence_order_not_http_response_order():
    selected, families = rows_fixture()
    reader = CompletedSignalCursorReadAhead(Client(selected, families), {}, source_context=selected,
        prefix=prefix(selected))
    first = [{'batch_sequence': 2, 'content_hash': 'b'}, {'batch_sequence': 1, 'content_hash': 'a'}]
    second = list(reversed(first))
    reader._base.list_cursor_commits = lambda **_: deepcopy(first)
    assert [v['batch_sequence'] for v in reader.list_cursor_commits(session_key=selected.session_key)] == [1,2]
    reader._base.list_cursor_commits = lambda **_: deepcopy(second)
    reader.verify_unchanged()
    second[0]['content_hash'] = 'changed'
    with pytest.raises(ValueError, match='inventory changed'):
        reader.verify_unchanged()

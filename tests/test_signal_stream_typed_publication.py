from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
import threading
from unittest.mock import patch

import pytest

from src.backend.signal_stream_runtime_service import _occurrence
from src.backend.signal_stream_typed_catalog import build_signal_field_catalog
from src.backend.signal_stream_typed_publication import (
    PublicationBatch, SourceCatalog, TypedSignalPublicationQueue,
)
from tests.test_signal_stream_typed_catalog import _source


class FakeStorage:
    def __init__(self):
        self.rows = defaultdict(list)
        self.writes = []
        self.fail_after_table = None
        self.block = None
        self.entered = Event()

    def insert_rows(self, table_name, rows):
        if self.block is not None:
            self.entered.set()
            assert self.block.wait(2)
        self.rows[table_name].extend(deepcopy(rows))
        self.writes.append(table_name)
        if table_name == self.fail_after_table:
            raise RuntimeError("ambiguous insert")

    def read_occurrence_rows(self, table_name, *, event_id):
        return deepcopy([row for row in self.rows[table_name] if row["event_id"] == event_id])

    def read_cursor_rows(self, table_name, *, session_key, batch_sequence):
        return deepcopy([row for row in self.rows[table_name]
                         if row["session_key"] == session_key
                         and row["batch_sequence"] == batch_sequence])

    def read_exact_prior_occurrence(self, event_id):
        return None

    def list_cursor_commits(self, *, session_key):
        return deepcopy([row for row in self.rows["signal_stream_cursor_commit_typed_v1"]
                         if row["session_key"] == session_key])


def _publisher(storage, *, capacity=64):
    publisher = TypedSignalPublicationQueue(storage, capacity=capacity)
    sample = _batch()
    publisher.bootstrap_session(session_key=sample.session_key,
                                configuration_revision=sample.configuration_revision,
                                source_revision=sample.source_revision,
                                catalogs=sample.catalogs)
    return publisher


def _batch():
    stream, columns, row = _source()
    catalog = build_signal_field_catalog(stream, columns,
                                         configuration_revision="configuration-1")
    occurrence = _occurrence(stream, row, columns,
                             as_of=datetime(2026, 9, 24, 14, 0, tzinfo=UTC),
                             definition_revision="definition-1", typed_catalog=catalog)
    return PublicationBatch(
        session_key="2026-09-24", batch_sequence=1,
        cutoff_at="2026-09-24T14:00:00+00:00",
        configuration_revision="configuration-1", source_revision="source-1",
        previous_commit_hash="0" * 64,
        before_states={}, after_states={"stream-1": {"ABC": {
            "matching": True, "definition_revision": "definition-1"}}},
        before_admissions={}, after_admissions={}, occurrences=(occurrence,),
        catalogs={"stream-1": SourceCatalog(catalog, stream, columns)},
    )


def test_clock_only_commit_is_explicit_monotonic_and_cold_recoverable():
    from dataclasses import replace
    from datetime import datetime, timedelta
    from src.backend.signal_stream_typed_publication import _publish_one
    from src.backend.signal_stream_typed_readback import recover_committed_head
    batch = replace(_batch(), before_states={}, after_states={}, before_admissions={},
        after_admissions={}, occurrences=())
    untouched = FakeStorage()
    with pytest.raises(ValueError, match="no-op"):
        _publish_one(untouched, batch)
    assert not untouched.writes
    storage = FakeStorage()
    publisher = _publisher(storage, capacity=1)
    try:
        first = replace(batch, allow_clock_only=True)
        head = publisher.submit(first).result(timeout=3)
        assert storage.writes == ["signal_stream_cursor_commit_typed_v1"]
        with pytest.raises(ValueError, match="must advance"):
            publisher.submit(replace(first, batch_sequence=2, previous_commit_hash=head))
        cutoff = datetime.fromisoformat(first.cutoff_at) + timedelta(seconds=1)
        second = replace(first, batch_sequence=2, previous_commit_hash=head, cutoff_at=cutoff.isoformat())
        publisher.submit(second).result(timeout=3)
        recovered = recover_committed_head(storage, session_key=batch.session_key,
            configuration_revision=batch.configuration_revision, source_revision=batch.source_revision,
            catalogs=batch.catalogs)
        assert recovered.sequence == 2 and recovered.states == {} and recovered.admissions == {}
    finally:
        publisher.close()


def test_bounded_fake_publication_fences_last_after_exact_readback() -> None:
    storage = FakeStorage()
    publisher = _publisher(storage, capacity=1)
    try:
        head = publisher.submit(_batch()).result(timeout=3)
        assert len(head) == 64
        assert storage.writes[-1] == "signal_stream_cursor_commit_typed_v1"
        assert storage.rows["signal_stream_cursor_commit_typed_v1"][0]["content_hash"] == head
        with pytest.raises(ValueError, match="sequence"):
            publisher.submit(_batch())
    finally:
        publisher.close()


def test_submit_is_local_only_and_queue_is_bounded() -> None:
    storage = FakeStorage()
    storage.block = Event()
    publisher = _publisher(storage, capacity=1)
    publisher.bootstrap_session(session_key="2026-09-25",
                                configuration_revision="configuration-1",
                                source_revision="source-1", catalogs=_batch().catalogs)
    try:
        receipt = publisher.submit(_batch())
        assert storage.entered.wait(1)
        assert not receipt.done()
        with pytest.raises(ValueError, match="pending"):
            publisher.submit(_batch())
        with pytest.raises(RuntimeError, match="capacity"):
            publisher.submit(replace(_batch(), session_key="2026-09-25"))
        storage.block.set()
        assert len(receipt.result(timeout=3)) == 64
    finally:
        storage.block.set()
        publisher.close()


def test_ambiguous_child_insert_fails_closed_without_retry_or_commit() -> None:
    storage = FakeStorage()
    storage.fail_after_table = "signal_stream_python_column_evidence_v1"
    publisher = _publisher(storage)
    try:
        with pytest.raises(RuntimeError, match="ambiguous insert"):
            publisher.submit(_batch()).result(timeout=3)
        assert "signal_stream_cursor_commit_typed_v1" not in storage.writes
        with pytest.raises(RuntimeError, match="unavailable"):
            publisher.submit(_batch())
    finally:
        publisher.close()


def test_cancelled_receipt_does_not_cancel_durable_worker() -> None:
    storage = FakeStorage()
    storage.block = Event()
    publisher = _publisher(storage)
    try:
        receipt = publisher.submit(_batch())
        assert storage.entered.wait(1)
        assert receipt.cancel()
        storage.block.set()
        publisher.close()
        assert len(storage.rows["signal_stream_cursor_commit_typed_v1"]) == 1
    finally:
        storage.block.set()
        publisher.close()


def test_full_batch_copy_runs_on_worker_not_submitter() -> None:
    import copy
    storage = FakeStorage()
    publisher = _publisher(storage)
    copied_on = []

    def tracked_copy(value):
        copied_on.append(threading.current_thread().name)
        return copy.deepcopy(value)

    try:
        with patch("src.backend.signal_stream_typed_publication.deepcopy", side_effect=tracked_copy):
            assert len(publisher.submit(_batch()).result(timeout=3)) == 64
        assert copied_on == ["typed-signal-publication"]
    finally:
        publisher.close()


def test_zero_event_state_delta_commits_and_cold_recovers() -> None:
    storage = FakeStorage()
    publisher = _publisher(storage)
    try:
        batch = replace(_batch(), occurrences=(), catalogs={})
        head = publisher.submit(batch).result(timeout=3)
        assert len(head) == 64
        assert storage.rows["signal_stream_occurrence_ref_typed_v1"] == []
        assert len(storage.rows["signal_stream_state_delta_typed_v1"]) == 1
        assert len(storage.rows["signal_stream_cursor_commit_typed_v1"]) == 1
    finally:
        publisher.close()

@pytest.mark.parametrize('mismatch', ['state', 'cutoff', 'source_revision'])
def test_existing_cursor_mismatch_reports_only_scalar_commit_and_family_hashes(mismatch):
    import json
    from src.backend.signal_stream_typed_publication import _publish_one
    from src.backend.signal_stream_typed_cursor import COMMIT, _hash
    batch=replace(_batch(),before_states={},after_states={},occurrences=(),allow_clock_only=True)
    storage=FakeStorage();_publish_one(storage,batch);before=deepcopy(storage.rows);writes=list(storage.writes)
    if mismatch=='state':
        staged=replace(batch,after_states=_batch().after_states)
        field='state_delta_count'
    elif mismatch=='cutoff':
        staged=replace(batch,cutoff_at='2026-09-24T14:00:01+00:00')
        field='cutoff_at'
    else:
        staged=replace(batch,source_revision='another-reviewed-source')
        field='source_revision'
    with pytest.raises(ValueError,match='typed cursor cold readback is incomplete or duplicated: ') as failure:
        _publish_one(storage,staged,verify_existing=True)
    diagnostics=json.loads(str(failure.value).split(': ',1)[1])
    assert set(diagnostics)=={'families','commit_differences','batch_sequence','session_key','cutoff_at'}
    assert diagnostics['batch_sequence']==staged.batch_sequence and diagnostics['session_key']==staged.session_key
    assert diagnostics['cutoff_at'].startswith(staged.cutoff_at[:19])
    assert field in diagnostics['commit_differences']
    assert set(diagnostics['commit_differences'])<={key for key,_ in COMMIT.columns}
    assert set(diagnostics['families'])=={'state_delta','occurrence_ref','admission_delta'}
    for family,values in diagnostics['families'].items():
        assert set(values)=={'expected_count','actual_count','expected_hash','actual_hash'}
        assert values['actual_count']==0 and values['actual_hash']==_hash([])
        assert len(values['expected_hash'])==64
    if mismatch=='state':
        assert diagnostics['families']['state_delta']['expected_count']==1
        assert diagnostics['commit_differences']['state_delta_count']=={'expected':1,'actual':0}
        assert 'stream-1' not in str(failure.value) and 'ABC' not in str(failure.value)
    assert storage.rows==before and storage.writes==writes

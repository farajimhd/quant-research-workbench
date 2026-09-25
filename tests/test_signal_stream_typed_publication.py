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

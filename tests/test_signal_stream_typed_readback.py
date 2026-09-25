from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from threading import Event, Thread
from unittest.mock import patch

import pytest

from src.backend.signal_stream_typed_cursor import COMMIT, STATE_DELTA
from src.backend.signal_stream_typed_readback import (
    CommittedCursorHead, canonical_row, recover_committed_head,
)
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from tests.test_signal_stream_typed_publication import FakeStorage, _batch


class DriverStorage(FakeStorage):
    def _driver(self, rows):
        result = deepcopy(rows)
        for row in result:
            for key, value in row.items():
                if key in {"matching"} and value is not None:
                    row[key] = int(value)
                elif key.endswith("hash") or key == "event_id":
                    if isinstance(value, str) and len(value) == 64:
                        row[key] = value.encode("ascii")
                elif key in {"event_time", "effective_at", "available_at", "cutoff_at",
                             "last_emitted_at", "expires_at", "value_time"}:
                    if isinstance(value, str):
                        row[key] = datetime.fromisoformat(value).replace(tzinfo=None)
        return result

    def read_occurrence_rows(self, table_name, *, event_id):
        return self._driver(super().read_occurrence_rows(table_name, event_id=event_id))

    def read_cursor_rows(self, table_name, *, session_key, batch_sequence):
        return self._driver(super().read_cursor_rows(
            table_name, session_key=session_key, batch_sequence=batch_sequence))

    def list_cursor_commits(self, *, session_key):
        return self._driver(super().list_cursor_commits(session_key=session_key))


def _publish(storage):
    batch = _batch()
    queue = TypedSignalPublicationQueue(storage)
    queue.bootstrap_session(session_key=batch.session_key,
                            configuration_revision=batch.configuration_revision,
                            source_revision=batch.source_revision, catalogs=batch.catalogs)
    try:
        head = queue.submit(batch).result(timeout=3)
    finally:
        queue.close()
    return batch, head


def test_driver_forms_publish_and_cold_head() -> None:
    storage = DriverStorage()
    batch, head = _publish(storage)
    recovered = recover_committed_head(
        storage, session_key=batch.session_key,
        configuration_revision=batch.configuration_revision,
        source_revision=batch.source_revision, catalogs=batch.catalogs)
    assert (recovered.sequence, recovered.content_hash, recovered.states,
            recovered.occurrence_count) == (1, head, batch.after_states, 1)
    restarted = TypedSignalPublicationQueue(storage)
    try:
        restarted.bootstrap_session(session_key=batch.session_key,
                                    configuration_revision=batch.configuration_revision,
                                    source_revision=batch.source_revision,
                                    catalogs=batch.catalogs)
        with pytest.raises(ValueError, match="sequence"):
            restarted.submit(batch)
    finally:
        restarted.close()


def test_cold_head_rejects_duplicate_and_missing_commit() -> None:
    storage = DriverStorage()
    batch, _ = _publish(storage)
    commit = storage.rows[COMMIT.name][0]
    storage.rows[COMMIT.name].append(deepcopy(commit))
    with pytest.raises(ValueError, match="duplicate or gapped"):
        recover_committed_head(storage, session_key=batch.session_key,
                               configuration_revision=batch.configuration_revision,
                               source_revision=batch.source_revision, catalogs=batch.catalogs)
    storage.rows[COMMIT.name].clear()
    with pytest.raises(ValueError, match="non-nullable"):
        canonical_row(COMMIT, {**commit, "content_hash": None})


def test_driver_corruption_rejects_bool_and_fixed_string() -> None:
    row = {name: None for name, _ in STATE_DELTA.columns}
    row.update(schema_version=1, session_key="2026-09-24", batch_sequence=1,
               signal_stream_id="stream", ticker="ABC", operation="upsert",
               matching=2, definition_revision="revision", last_emitted_at=None,
               content_hash=b"a" * 64)
    with pytest.raises(ValueError, match="Boolean"):
        canonical_row(STATE_DELTA, row)
    row["matching"] = 1
    row["content_hash"] = b"a" * 63
    with pytest.raises(ValueError, match="FixedString"):
        canonical_row(STATE_DELTA, row)


def test_concurrent_bootstrap_of_same_session_is_rejected() -> None:
    batch = _batch()
    publisher = TypedSignalPublicationQueue(FakeStorage())
    entered, release = Event(), Event()
    errors = []

    def slow_recovery(*_args, **_kwargs):
        entered.set()
        assert release.wait(2)
        return CommittedCursorHead(batch.session_key, 0, "0" * 64, {}, {}, 0)

    def first_bootstrap():
        try:
            publisher.bootstrap_session(
                session_key=batch.session_key,
                configuration_revision=batch.configuration_revision,
                source_revision=batch.source_revision, catalogs=batch.catalogs)
        except BaseException as exc:
            errors.append(exc)

    with patch("src.backend.signal_stream_typed_publication.recover_committed_head",
               side_effect=slow_recovery):
        worker = Thread(target=first_bootstrap)
        worker.start()
        assert entered.wait(1)
        with pytest.raises(ValueError, match="already bootstrapped"):
            publisher.bootstrap_session(
                session_key=batch.session_key,
                configuration_revision=batch.configuration_revision,
                source_revision=batch.source_revision, catalogs=batch.catalogs)
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert not errors
    publisher.close()

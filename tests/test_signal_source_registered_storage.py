"""The typed source worker publishes only through registered INSERTs."""
import json
import re

import pytest

from src.backend.signal_source_insert_dispatch import (
    SignalSourceInsertDispatch, source_insert_run_id,
)
from src.backend.signal_source_registered_storage import RegisteredSignalSourceStorage
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from tests.test_arte_typed_insert_dispatch import Keeper
from tests.test_signal_stream_typed_publication import FakeStorage, _batch


class _InsertClient:
    def __init__(self, storage):
        self.storage = storage
        self.query_ids = []

    def execute(self, sql, *, query_id=None):
        table = re.match(r"INSERT INTO arte\.([a-z0-9_]+) \(", sql).group(1)
        self.query_ids.append(query_id)
        self.storage.insert_rows(
            table, [json.loads(line) for line in sql.partition("\n")[2].splitlines()])


class _Attestor:
    def __init__(self):
        self.seen = []

    def bootstrap(self, recovered, *, configuration_revision, source_revision):
        assert recovered.sequence == 0
        self.seen.append("bootstrap")

    def attest(self, batch, cursor_hash, storage):
        self.seen.append(cursor_hash)
        return cursor_hash


def _registered():
    batch = _batch()
    raw = FakeStorage()
    client = _InsertClient(raw)
    dispatch = SignalSourceInsertDispatch(Keeper())
    run_id = source_insert_run_id(batch.session_key, batch.configuration_revision)
    dispatch.initialize_new_session(run_id, has_ch_rows=False)
    storage = RegisteredSignalSourceStorage(
        raw, client, dispatch, session_key=batch.session_key,
        configuration_revision=batch.configuration_revision)
    attestor = _Attestor()
    publisher = TypedSignalPublicationQueue(storage, attestor=attestor)
    publisher.bootstrap_session(
        session_key=batch.session_key,
        configuration_revision=batch.configuration_revision,
        source_revision=batch.source_revision, catalogs=batch.catalogs)
    return batch, raw, client, dispatch, storage, attestor, publisher


def test_registered_source_worker_seals_exact_batch_before_cold_close():
    batch, raw, client, dispatch, storage, attestor, publisher = _registered()
    try:
        head = publisher.submit(batch).result(timeout=3)
        assert attestor.seen == ["bootstrap", head]
        assert raw.writes[-1] == "signal_stream_cursor_commit_typed_v1"
        assert len(client.query_ids) == len(raw.writes)
        assert all(query_id.startswith("arte_signal_source_")
                   for query_id in client.query_ids)
        assert dispatch.acquire_cold_barrier(storage.run_id) == (1, head)
    finally:
        publisher.close()


def test_registered_source_lost_response_keeps_cold_barrier_closed():
    batch, raw, _client, dispatch, storage, _attestor, publisher = _registered()
    raw.fail_after_table = "signal_stream_python_column_evidence_v1"
    try:
        with pytest.raises(RuntimeError, match="ambiguous insert"):
            publisher.submit(batch).result(timeout=3)
        assert not raw.rows["signal_stream_cursor_commit_typed_v1"]
        with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
            dispatch.acquire_cold_barrier(storage.run_id)
    finally:
        publisher.close()


def test_registered_source_cannot_publish_without_attestor():
    batch = _batch()
    storage = RegisteredSignalSourceStorage(
        FakeStorage(), _InsertClient(FakeStorage()),
        SignalSourceInsertDispatch(Keeper()),
        session_key=batch.session_key,
        configuration_revision=batch.configuration_revision)
    with pytest.raises(ValueError, match="Keeper head attestation"):
        TypedSignalPublicationQueue(storage)


def test_registered_source_new_session_checks_all_clickhouse_rows():
    batch = _batch()
    raw = FakeStorage()
    raw.has_any_source_rows = lambda *, session_key: True
    dispatch = SignalSourceInsertDispatch(Keeper())
    storage = RegisteredSignalSourceStorage(
        raw, _InsertClient(raw), dispatch, session_key=batch.session_key,
        configuration_revision=batch.configuration_revision)
    with pytest.raises(KeeperUnavailable, match="unregistered ClickHouse rows"):
        storage.initialize_new_session()
    raw.has_any_source_rows = lambda *, session_key: False
    storage.initialize_new_session()
    assert dispatch.acquire_cold_barrier(storage.run_id) == (0, "0" * 64)

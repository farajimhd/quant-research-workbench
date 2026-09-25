"""Fake-only proof that typed cursor ACK follows exact Keeper CAS."""
import pytest

from src.backend.signal_stream_session_head import SignalSessionHeadKeeper
from src.backend.signal_stream_typed_attestation import SignalCursorAttestor
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_signal_stream_typed_publication import FakeStorage, _batch


def _publisher(storage, keeper, *, owner="worker-1", epoch=1):
    batch = _batch()
    publisher = TypedSignalPublicationQueue(
        storage, attestor=SignalCursorAttestor(
            keeper, owner_id=owner, epoch=epoch))
    publisher.bootstrap_session(
        session_key=batch.session_key,
        configuration_revision=batch.configuration_revision,
        source_revision=batch.source_revision, catalogs=batch.catalogs)
    return publisher


def test_receipt_requires_exact_keeper_attested_cold_head() -> None:
    storage = FakeStorage()
    keeper = SignalSessionHeadKeeper(FakeKazoo(), endpoint="127.0.0.1:9181")
    batch = _batch()
    assert keeper.acquire(batch.session_key, owner_id="worker-1") == 1
    publisher = _publisher(storage, keeper)
    try:
        digest = publisher.submit(batch).result(timeout=3)
        head = keeper.read_head(batch.session_key)
        assert head.batch_sequence == 1
        assert head.cursor_commit_hash == digest
    finally:
        publisher.close()
    # A fresh owner can cold-verify the exact historical prefix.
    assert keeper.release(batch.session_key, owner_id="worker-1", epoch=1)
    assert keeper.acquire(batch.session_key, owner_id="worker-2") == 2
    second = _publisher(storage, keeper, owner="worker-2", epoch=2)
    second.close()


def test_lost_owner_after_clickhouse_readback_never_receives_ack() -> None:
    storage = FakeStorage()
    keeper = SignalSessionHeadKeeper(FakeKazoo(), endpoint="127.0.0.1:9181")
    batch = _batch()
    keeper.acquire(batch.session_key, owner_id="worker-1")
    publisher = _publisher(storage, keeper)
    original = storage.read_cursor_rows
    lost = False
    def lose_at_commit(table_name, *, session_key, batch_sequence):
        nonlocal lost
        rows = original(table_name, session_key=session_key,
                        batch_sequence=batch_sequence)
        if table_name == "signal_stream_cursor_commit_typed_v1" and rows and not lost:
            lost = True
            keeper.release(batch.session_key, owner_id="worker-1", epoch=1)
            keeper.acquire(batch.session_key, owner_id="worker-2")
        return rows
    storage.read_cursor_rows = lose_at_commit
    try:
        with pytest.raises(RuntimeError, match="owner|lost"):
            publisher.submit(batch).result(timeout=3)
        assert keeper.read_head(batch.session_key).batch_sequence == 0
    finally:
        publisher.close()
    # The fully persisted but unattested CH fence blocks cold bootstrap.
    with pytest.raises(ValueError, match="differs from Keeper"):
        _publisher(storage, keeper, owner="worker-2", epoch=2)


def test_duplicate_committed_fence_cannot_be_attested() -> None:
    storage = FakeStorage()
    keeper = SignalSessionHeadKeeper(FakeKazoo(), endpoint="127.0.0.1:9181")
    batch = _batch()
    keeper.acquire(batch.session_key, owner_id="worker-1")
    publisher = _publisher(storage, keeper)
    original = storage.read_cursor_rows
    def duplicate_commit(table_name, *, session_key, batch_sequence):
        rows = original(table_name, session_key=session_key,
                        batch_sequence=batch_sequence)
        if table_name == "signal_stream_cursor_commit_typed_v1" and rows:
            return rows + rows
        return rows
    storage.read_cursor_rows = duplicate_commit
    try:
        with pytest.raises(ValueError, match="duplicated"):
            publisher.submit(batch).result(timeout=3)
        assert keeper.read_head(batch.session_key).batch_sequence == 0
    finally:
        publisher.close()


def test_wrong_session_commit_fails_exact_readback_before_attestation() -> None:
    storage = FakeStorage()
    keeper = SignalSessionHeadKeeper(FakeKazoo(), endpoint="127.0.0.1:9181")
    batch = _batch()
    keeper.acquire(batch.session_key, owner_id="worker-1")
    publisher = _publisher(storage, keeper)
    original = storage.read_cursor_rows

    def wrong_session(table_name, *, session_key, batch_sequence):
        rows = original(table_name, session_key=session_key,
                        batch_sequence=batch_sequence)
        if table_name == "signal_stream_cursor_commit_typed_v1" and rows:
            return [{**rows[0], "session_key": "2026-01-02"}]
        return rows

    storage.read_cursor_rows = wrong_session
    try:
        with pytest.raises(ValueError, match="cold readback is incomplete"):
            publisher.submit(batch).result(timeout=3)
        assert keeper.read_head(batch.session_key).batch_sequence == 0
    finally:
        publisher.close()

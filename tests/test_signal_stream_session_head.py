from __future__ import annotations

import pytest

from src.backend.signal_stream_session_head import (
    GENESIS_HASH, SignalSessionHeadKeeper,
)
from tests.test_live_signal_completion_keeper import FakeKazoo


SESSION = "2026-09-24"


def test_keeper_head_contiguous_attestation_and_stale_owner_rejection() -> None:
    keeper = SignalSessionHeadKeeper(FakeKazoo(), endpoint="127.0.0.1:9181")
    assert keeper.acquire(SESSION, owner_id="first") == 1
    assert keeper.acquire(SESSION, owner_id="second") is None
    genesis = keeper.read_head(SESSION)
    assert genesis.batch_sequence == 0 and genesis.cursor_commit_hash == GENESIS_HASH
    first = keeper.attest_next(
        SESSION, owner_id="first", epoch=1, previous=genesis,
        cursor_commit_hash="a" * 64, configuration_revision="config-1",
        source_revision="source-1")
    assert first.batch_sequence == 1 and first.keeper_epoch == 1
    with pytest.raises(RuntimeError, match="head changed"):
        keeper.attest_next(SESSION, owner_id="first", epoch=1, previous=genesis,
                           cursor_commit_hash="b" * 64,
                           configuration_revision="config-1", source_revision="source-1")
    assert keeper.release(SESSION, owner_id="first", epoch=1)
    assert keeper.acquire(SESSION, owner_id="second") == 2
    with pytest.raises(RuntimeError, match="lost"):
        keeper.attest_next(SESSION, owner_id="first", epoch=1, previous=first,
                           cursor_commit_hash="b" * 64,
                           configuration_revision="config-1", source_revision="source-1")
    second = keeper.attest_next(SESSION, owner_id="second", epoch=2, previous=first,
                                cursor_commit_hash="b" * 64,
                                configuration_revision="config-1", source_revision="source-1")
    assert second.batch_sequence == 2 and second.keeper_epoch == 2
    with pytest.raises(ValueError, match="revision changed"):
        keeper.attest_next(SESSION, owner_id="second", epoch=2, previous=second,
                           cursor_commit_hash="c" * 64,
                           configuration_revision="config-2", source_revision="source-1")


def test_keeper_head_cas_rejects_holder_replacement_during_transaction() -> None:
    client = FakeKazoo()
    keeper = SignalSessionHeadKeeper(client, endpoint="127.0.0.1:9181")
    keeper.acquire(SESSION, owner_id="first")
    genesis = keeper.read_head(SESSION)
    holder_path = f"{keeper._base(SESSION)}/holder"

    def replace_holder():
        value, version, owner = client.nodes[holder_path]
        client.nodes[holder_path] = (value, version + 1, owner)

    client.before_commit = replace_holder
    with pytest.raises(RuntimeError, match="CAS failed"):
        keeper.attest_next(SESSION, owner_id="first", epoch=1, previous=genesis,
                           cursor_commit_hash="a" * 64,
                           configuration_revision="config-1", source_revision="source-1")
    assert keeper.read_head(SESSION) == genesis


def test_keeper_head_rejects_remote_endpoint_and_invalid_identity() -> None:
    with pytest.raises(ValueError, match="loopback"):
        SignalSessionHeadKeeper(FakeKazoo(), endpoint="192.0.2.1:9181")
    keeper = SignalSessionHeadKeeper(FakeKazoo(), endpoint="127.0.0.1:9181")
    with pytest.raises(ValueError, match="owner"):
        keeper.acquire(SESSION, owner_id="bad\nowner")

from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from src.backend.live_signal_source_cursor_proof import cold_attested_signal_source_cursor
from src.backend.signal_stream_session_head import SessionHead
from src.backend.signal_stream_typed_readback import CommittedCursorHead


SESSION = "2026-09-24"
HASH = "a" * 64


class Keeper:
    def __init__(self):
        self.head = SessionHead(SESSION, 1, HASH, "config-1", "source-1", "owner", 2, 3)

    def read_head(self, session_key):
        assert session_key == SESSION
        return self.head


class Client:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return "\n".join(json.dumps(row) for row in self.rows)


def _recover(monkeypatch, *, hash=HASH, sequence=1, after=None,
             cutoff="2026-09-24T14:00:00+00:00"):
    def recover(storage, **kwargs):
        assert kwargs["session_key"] == SESSION
        assert kwargs["configuration_revision"] == "config-1"
        assert kwargs["source_revision"] == "source-1"
        assert storage.list_cursor_commits(session_key=SESSION) == []
        if after:
            after()
        return CommittedCursorHead(SESSION, sequence, hash, {}, {}, 0, cutoff)
    monkeypatch.setattr(
        "src.backend.live_signal_source_cursor_proof.recover_committed_head", recover)


def test_source_proof_bounded_cold_verification_and_current_head(monkeypatch):
    _recover(monkeypatch)
    keeper, client = Keeper(), Client()
    proof = cold_attested_signal_source_cursor(
        object(), client, keeper, session_key=SESSION,
        configuration_revision="config-1", source_revision="source-1",
        catalogs={}, max_batches=2)
    assert (proof.identity, proof.content_hash, proof.sequence) == (SESSION, HASH, 1)
    assert len(client.queries) == 1
    assert "LIMIT 3 FORMAT JSONEachRow" in client.queries[0]
    proof.assert_current()
    keeper.head = replace(keeper.head, keeper_epoch=3)
    with pytest.raises(RuntimeError, match="head changed"):
        proof.assert_current()


def test_source_proof_rejects_mismatch_and_concurrent_head_change(monkeypatch):
    keeper = Keeper()
    _recover(monkeypatch, hash="b" * 64)
    with pytest.raises(ValueError, match="differs from Keeper"):
        cold_attested_signal_source_cursor(
            object(), Client(), keeper, session_key=SESSION,
            configuration_revision="config-1", source_revision="source-1",
            catalogs={})
    _recover(monkeypatch, after=lambda: setattr(
        keeper, "head", replace(keeper.head, keeper_version=4)))
    with pytest.raises(RuntimeError, match="head changed"):
        cold_attested_signal_source_cursor(
            object(), Client(), keeper, session_key=SESSION,
            configuration_revision="config-1", source_revision="source-1",
            catalogs={})


def test_source_proof_rejects_scope_and_commit_overflow(monkeypatch):
    keeper = Keeper()
    with pytest.raises(ValueError, match="scope"):
        cold_attested_signal_source_cursor(
            object(), Client(), keeper, session_key=SESSION,
            configuration_revision="wrong", source_revision="source-1", catalogs={})
    _recover(monkeypatch)
    with pytest.raises(ValueError, match="exceeds recovery bound"):
        cold_attested_signal_source_cursor(
            object(), Client([{}, {}, {}]), keeper, session_key=SESSION,
            configuration_revision="config-1", source_revision="source-1",
            catalogs={}, max_batches=2)


def test_strategy_one_source_proof_requires_isolated_storage_and_keeper(monkeypatch):
    _recover(monkeypatch)
    keeper, client = Keeper(), Client()
    keeper.strategy_one = True
    storage = type("IsolatedStorage", (), {"strategy_one": True})()
    proof = cold_attested_signal_source_cursor(
        storage, client, keeper, session_key=SESSION,
        configuration_revision="config-1", source_revision="source-1",
        catalogs={}, max_batches=2, strategy_one=True,
        required_through_at=datetime(2026, 9, 24, 14, tzinfo=timezone.utc))
    assert proof.content_hash == HASH
    assert "arte.trading_strategy_one_" in client.queries[0]
    with pytest.raises(ValueError, match="scope differ"):
        cold_attested_signal_source_cursor(
            object(), Client(), keeper, session_key=SESSION,
            configuration_revision="config-1", source_revision="source-1",
            catalogs={}, strategy_one=True,
            required_through_at=datetime(2026, 9, 24, 14, tzinfo=timezone.utc))


def test_numbered_source_proof_rejects_partial_cutoff(monkeypatch):
    keeper, client = Keeper(), Client()
    keeper.strategy_one = True
    storage = type("IsolatedStorage", (), {"strategy_one": True})()
    _recover(monkeypatch, cutoff="2026-09-24T13:59:59.999999+00:00")
    required = datetime(2026, 9, 24, 14, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="before the required cutoff"):
        cold_attested_signal_source_cursor(
            storage, client, keeper, session_key=SESSION,
            configuration_revision="config-1", source_revision="source-1",
            catalogs={}, strategy_one=True, required_through_at=required)
    with pytest.raises(ValueError, match="required cutoff"):
        cold_attested_signal_source_cursor(
            storage, client, keeper, session_key=SESSION,
            configuration_revision="config-1", source_revision="source-1",
            catalogs={}, strategy_one=True)

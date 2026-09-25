"""Ambiguous terminal V3 writes are acknowledged only from exact cold rows."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.backend import backtest_terminal_v3_reconcile as subject
from src.trading_runtime.arte_journal_writer import (
    _CONTRACTS, _datetime_wire, typed_row,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from src.trading_runtime.arte_typed_insert_dispatch import typed_insert_query_id
from src.backend.backtest_terminal_v3_dispatch import _root
from tests.test_backtest_terminal_v3_dispatch import _client, RUN, BATCH
from tests.test_backtest_terminal_v3_fence import _fixture
from src.backend.backtest_terminal_v3_fence import project_terminal_v3_commit


def _sql_and_stored():
    event = typed_row("trading_event_v1", {
        "run_id": RUN, "event_month": "2026-08-01",
        "attempt_id": "00000000-0000-0000-0000-000000000101",
        "batch_id": BATCH,
        "record_id": "00000000-0000-0000-0000-000000000102",
        "sequence": 4,
        "event_time": "2026-08-18T14:00:00.100000+00:00",
        "recorded_at": "2026-08-18T14:00:00.100000+00:00",
        "category": "lifecycle", "entity_type": "run",
        "entity_id": "terminal", "account_id": "DU1",
        "correlation_id": "", "causation_id": "",
    })
    token = f"terminal-v2:{BATCH}:trading_event_v1:{'a' * 64}"
    sql = ("INSERT INTO arte.trading_event_v1 ("
           + ",".join(name for name, _ in _CONTRACTS["trading_event_v1"].columns)
           + ") SETTINGS async_insert=1,wait_for_async_insert=1,"
           "insert_deduplicate=1,"
           f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n"
           + canonical_json(event))
    stored = dict(event)
    stored["event_time"] = _datetime_wire(event["event_time"], 9)
    stored["recorded_at"] = _datetime_wire(event["recorded_at"], 6)
    return sql, stored


def _setup(monkeypatch, rows):
    client, fence, writer = _client(fail=True)
    sql, stored = _sql_and_stored()
    with pytest.raises(TimeoutError, match="lost response"):
        client.execute(sql)
    dispatch = client._dispatch
    keeper = dispatch.keeper

    class Authority:
        def __init__(self):
            self.keeper = SimpleNamespace(_client=keeper)
            self.client = SimpleNamespace(_client=object())
            self.checked = 0
        def assert_current(self, run_id, account_ids):
            assert run_id == RUN and account_ids == ("DU1",)
            self.checked += 1

    class ReadClient:
        def execute(self, query):
            assert query.startswith("SELECT * FROM arte.trading_event_v1")
            return "\n".join(json.dumps(row) for row in rows)

    monkeypatch.setattr(subject, "FixedTerminalKeeperAuthority", Authority)
    monkeypatch.setattr(subject, "read_v3_preflight", lambda *_: None)
    monkeypatch.setattr(subject, "verify_retained_v3_cold_gate",
                        lambda *_, **__: SimpleNamespace(
                            prefix=SimpleNamespace(last_sequence=3,
                                last_batch_id="00000000-0000-0000-0000-000000000e02"),
                            barrier=SimpleNamespace(assert_fenced=lambda run_id: None)))
    return Authority(), ReadClient(), dispatch, sql, stored, writer


def _ack(authority, reader, dispatch, sql):
    return subject.acknowledge_visible_terminal_v3_operation(
        reader, authority, dispatch, run_id=RUN, account_ids=("DU1",),
        batch_id=BATCH, last_sequence=4,
        expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64, deterministic_sql=sql)


def test_visible_exact_terminal_row_acknowledges_without_retry(monkeypatch):
    _, stored = _sql_and_stored()
    authority, reader, dispatch, sql, _, writer = _setup(monkeypatch, (stored,))
    query_id = _ack(authority, reader, dispatch, sql)
    assert query_id.startswith("arte_typed_")
    assert len(writer.calls) == 1  # No second INSERT during reconciliation.
    assert authority.checked >= 2
    assert _ack(authority, reader, dispatch, sql) == query_id


@pytest.mark.parametrize("rows", [(), ({"run_id": RUN},)])
def test_missing_or_partial_row_remains_started_and_gate_closed(monkeypatch, rows):
    authority, reader, dispatch, sql, _, writer = _setup(monkeypatch, rows)
    with pytest.raises(KeeperUnavailable):
        _ack(authority, reader, dispatch, sql)
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        # Inventory is still unresolved and no retry occurred.
        from src.backend.backtest_terminal_v3_dispatch import TerminalV3DispatchClient
        TerminalV3DispatchClient(
            writer, dispatch, SimpleNamespace(barrier=SimpleNamespace(
                authority=dispatch, run_id=RUN,
                assert_fenced=lambda run_id: None)),
            run_id=RUN, batch_id=BATCH, last_sequence=4).acknowledged_operations()
    assert dispatch._read_gate(RUN)[0].mode == "closed"
    assert len(writer.calls) == 1


def test_duplicate_visible_terminal_row_cannot_be_acknowledged(monkeypatch):
    _, stored = _sql_and_stored()
    authority, reader, dispatch, sql, _, writer = _setup(
        monkeypatch, (stored, stored))
    with pytest.raises(KeeperUnavailable, match="absent, partial, or conflicting"):
        _ack(authority, reader, dispatch, sql)
    assert dispatch._read_gate(RUN)[0].mode == "closed"
    assert len(writer.calls) == 1


def test_changed_deterministic_sql_hash_cannot_acknowledge(monkeypatch):
    _, stored = _sql_and_stored()
    authority, reader, dispatch, sql, _, writer = _setup(monkeypatch, (stored,))
    changed = sql.replace("terminal\"", "changed\"")
    with pytest.raises(KeeperUnavailable, match="identity differs"):
        _ack(authority, reader, dispatch, changed)
    assert len(writer.calls) == 1


def test_tampered_keeper_query_identity_cannot_acknowledge(monkeypatch):
    _, stored = _sql_and_stored()
    authority, reader, dispatch, sql, _, writer = _setup(monkeypatch, (stored,))
    token = sql.split("insert_deduplication_token='", 1)[1].split("'", 1)[0]
    query_id = typed_insert_query_id(RUN, "trading_event_v1", token)
    path = f"{_root(RUN, BATCH)}/{query_id}"
    value, stat = dispatch.keeper.get(path)
    dispatch.keeper.set(path, value.replace(query_id.encode(), b"arte_typed_" + b"0" * 64),
                        version=stat.version)
    with pytest.raises(KeeperUnavailable, match="identity"):
        _ack(authority, reader, dispatch, sql)
    assert dispatch._read_gate(RUN)[0].mode == "closed"
    assert len(writer.calls) == 1


def test_terminal_v3_seal_row_normalizes_stored_utc_exactly():
    prefix, kwargs = _fixture()
    seal = project_terminal_v3_commit(prefix, **kwargs)
    stored = {**seal, "committed_at": seal["committed_at"].replace(
        "T", " ").replace("+00:00", "")}
    class Reader:
        def execute(self, query):
            assert "FROM arte.trading_backtest_terminal_commit_v3" in query
            return json.dumps(stored)
    subject._exact_visible_rows(
        Reader(), table="trading_backtest_terminal_commit_v3",
        run_id=prefix.run_id, batch_id=str(seal["batch_id"]),
        last_sequence=int(seal["last_sequence"]), expected=(seal,))
    with pytest.raises(KeeperUnavailable, match="conflicting"):
        subject._exact_visible_rows(
            Reader(), table="trading_backtest_terminal_commit_v3",
            run_id=prefix.run_id, batch_id=str(seal["batch_id"]),
            last_sequence=int(seal["last_sequence"]),
            expected=({**seal, "content_hash": "a" * 64},))

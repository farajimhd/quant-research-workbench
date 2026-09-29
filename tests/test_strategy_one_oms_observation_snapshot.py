"""OMS checkpoint observations preserve the last processed state, not broker now."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from src.trading_runtime.strategy_one_oms_observation_snapshot import (
    OBSERVATION, ROOT, OmsObservationHead, OmsObservationSnapshotRows,
    load_attested_oms_observation_snapshot,
    load_unattested_oms_observation_snapshot,
    project_oms_observation_snapshot, verify_oms_observation_snapshot,
)


def _snapshot(fingerprints=None):
    group = SimpleNamespace(
        broker_order_ids=["broker-1"],
        broker_order_state_fingerprints=fingerprints or {
            "broker-1": ("working", "Submitted", 0.0, 5.0, 0.0,
                         10.0, 0.0, "", "", ""),
        })
    return project_oms_observation_snapshot(
        run_id="backtest:one", session_date=date(2026, 8, 18),
        checkpoint_sequence=42, boundary_ms=30_000,
        groups={"group-1": group})


def test_snapshot_is_normalized_deterministic_and_canonical():
    rows = verify_oms_observation_snapshot(_snapshot())
    assert rows == _snapshot()
    assert rows.root["observation_count"] == 1
    assert rows.observations[0]["remaining_quantity"] == "5.000000000000000000"
    assert rows.observations[0]["broker_status"] == "Submitted"
    assert all("JSON" not in kind and "Map" not in kind
               for table in (ROOT, OBSERVATION) for _, kind in table.columns)


def test_snapshot_rejects_unbound_and_live_shape():
    with pytest.raises(ValueError, match="unbound"):
        _snapshot({"other": ("working", "Submitted", 0, 5, 0, 10, 0, "", "", "")})
    with pytest.raises(ValueError, match="canonical"):
        _snapshot({"broker-1": ("Submitted", 0, 5, 0, 10, 0, "")})


def test_snapshot_rejects_tampered_child_and_missing_row():
    rows = _snapshot()
    with pytest.raises(RuntimeError, match="child"):
        verify_oms_observation_snapshot(replace(
            rows, observations=({**rows.observations[0], "broker_status": "Filled"},)))
    with pytest.raises(RuntimeError, match="set"):
        verify_oms_observation_snapshot(OmsObservationSnapshotRows(rows.root, ()))


def test_oms_observation_rows_cannot_use_unfenced_journal_insert():
    from src.trading_runtime.arte_journal_writer import _insert

    rows = _snapshot()
    with pytest.raises(RuntimeError, match="typed snapshot fence"):
        _insert(object(), ROOT.name, (rows.root,), "unfenced")


def test_cold_rows_require_one_root_and_exact_children(monkeypatch):
    from src.trading_runtime import arte_journal_writer

    rows = _snapshot()
    queries = []

    def read(_client, sql):
        queries.append(sql)
        return [rows.root] if ROOT.name in sql else list(rows.observations)

    monkeypatch.setattr(arte_journal_writer, "_rows", read)
    assert load_unattested_oms_observation_snapshot(
        object(), run_id="backtest:one", checkpoint_sequence=42) == rows
    assert len(queries) == 2
    assert all("LIMIT " in query and "FORMAT JSONEachRow" in query
               for query in queries)
    monkeypatch.setattr(arte_journal_writer, "_rows",
                        lambda _client, sql: [] if ROOT.name in sql else list(rows.observations))
    with pytest.raises(RuntimeError, match="unique checkpoint root"):
        load_unattested_oms_observation_snapshot(
            object(), run_id="backtest:one", checkpoint_sequence=42)


def test_cold_attestation_rejects_changed_keeper_head(monkeypatch):
    from src.trading_runtime import (
        arte_journal_commit_v4, arte_journal_projection,
        strategy_one_oms_observation_snapshot as snapshot,
    )

    rows = _snapshot()
    batch = "00000000-0000-0000-0000-000000000001"
    prefix = type("Prefix", (), {
        "status": "running", "last_sequence": 42,
        "last_batch_id": batch,
    })()
    monkeypatch.setattr(arte_journal_commit_v4, "load_verified_v4_prefix",
                        lambda *_: prefix)
    monkeypatch.setattr(arte_journal_projection, "load_latest_backtest_cursor",
                        lambda *_: {"run_id": "backtest:one",
                                    "event_sequence": 42, "batch_id": batch,
                                    "boundary_ms": 30_000,
                                    "session_date": "2026-08-18"})
    monkeypatch.setattr(snapshot, "load_unattested_oms_observation_snapshot",
                        lambda *_args, **_kwargs: rows)
    head = OmsObservationHead("backtest:one", 42, batch,
                              rows.root["content_hash"], 0)

    class Keeper:
        def __init__(self):
            self.calls = 0

        def read_head(self, *, run_id):
            assert run_id == "backtest:one"
            self.calls += 1
            return head

    assert load_attested_oms_observation_snapshot(
        object(), Keeper(), run_id="backtest:one",
        checkpoint_sequence=42) == rows

    class MovingKeeper(Keeper):
        def read_head(self, *, run_id):
            result = super().read_head(run_id=run_id)
            return replace(result, keeper_version=1) if self.calls == 2 else result

    with pytest.raises(RuntimeError, match="seal differs"):
        load_attested_oms_observation_snapshot(
            object(), MovingKeeper(), run_id="backtest:one",
            checkpoint_sequence=42)

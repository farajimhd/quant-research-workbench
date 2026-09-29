"""OMS checkpoint observations preserve the last processed state, not broker now."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from src.trading_runtime.strategy_one_oms_observation_snapshot import (
    OBSERVATION, ROOT, OmsObservationSnapshotRows,
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

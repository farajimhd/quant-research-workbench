"""Evidence checkpoints are normalized scalar rows, not opaque payloads."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
from src.trading_runtime.strategy_one_activation_state import FrozenActivation
from src.trading_runtime.strategy_one_evidence_snapshot import (
    TABLES, EvidenceSnapshotRows, ManagedEvidenceSnapshotHeadReader,
    project_evidence_snapshot,
    restore_evidence_snapshot,
)
from src.trading_runtime.strategy_one_resistance import (
    KnownResistance, ResistanceObservation,
)


RUN = str(UUID(int=17))
DAY = date(2026, 8, 18)


def _rows():
    state = StrategyOneEvidenceState(
        330_000,
        (("AAA", ResistanceObservation(
            330_000, 102_000,
            (KnownResistance("R1", 10.0, 10.2),), frozenset({"R1"}))),),
        (FrozenActivation("AAA", 301_000, 100_000, 0.2, ("R1", "R2")),),
        (("AAA", 330_000, 99_000),))
    return state, project_evidence_snapshot(
        run_id=RUN, session_date=DAY, checkpoint_sequence=1046, state=state)


def test_normalized_evidence_round_trip_and_layout():
    state, rows = _rows()
    assert restore_evidence_snapshot(rows) == state
    assert len(TABLES) == 6
    assert all("live_market_ssd" in table.ddl() for table in TABLES)
    assert all(not any(term in kind for term in ("JSON", "Array", "Map", "Object"))
               for table in TABLES for _, kind in table.columns)
    assert rows.known[0]["accepted"] == 1
    assert tuple(row["unified_level_id"] for row in rows.activation_levels) == (
        "R1", "R2")


def test_normalized_evidence_rejects_missing_child_or_semantic_change():
    state, rows = _rows()
    with pytest.raises(RuntimeError, match="children differ"):
        restore_evidence_snapshot(replace(rows, known=()))
    changed = (dict(rows.known[0], accepted=0),)
    with pytest.raises(RuntimeError, match="children differ"):
        restore_evidence_snapshot(replace(rows, known=changed))
    with pytest.raises(ValueError, match="known resistance identities"):
        project_evidence_snapshot(
            run_id=RUN, session_date=DAY, checkpoint_sequence=1046,
            state=replace(state, resistance=(("AAA", ResistanceObservation(
                330_000, 102_000, (), frozenset({"R1"}))),)))


def test_evidence_keeper_head_is_run_scoped_and_requires_exact_wire():
    assert ManagedEvidenceSnapshotHeadReader.path(RUN) != (
        ManagedEvidenceSnapshotHeadReader.path(str(UUID(int=18))))
    with pytest.raises(ValueError, match="run is invalid"):
        ManagedEvidenceSnapshotHeadReader.path("bad\nrun")

    class Client:
        client_id = "reader-1"

        def __init__(self, wire):
            self.wire = wire

        def get(self, _path):
            return self.wire, SimpleNamespace(version=2)

    reader = object.__new__(ManagedEvidenceSnapshotHeadReader)
    client = Client(f"1\n{RUN}\n1046\n{str(UUID(int=19))}\n{'a' * 64}".encode())
    reader._session = SimpleNamespace(
        writable=True, client=client, _generation=1)
    head = reader.read_head(run_id=RUN)
    assert (head.checkpoint_sequence, head.snapshot_hash, head.keeper_version) == (
        1046, "a" * 64, 2)
    client.wire = b"not-a-head"
    with pytest.raises(ValueError, match="missing or corrupt"):
        reader.read_head(run_id=RUN)

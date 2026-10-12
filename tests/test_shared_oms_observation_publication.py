"""Central publisher against synthetic SQL and real typed-dispatch Keeper logic."""
import json
from datetime import date
from types import SimpleNamespace
import pytest

from src.trading_runtime import arte_oms_observation_snapshot as shared
from src.trading_runtime import arte_journal_schema, arte_journal_commit_v4 as commits, arte_journal_projection as projection
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from tests.test_arte_typed_insert_dispatch import Keeper, TypedInsertDispatch, compact_running_prefix, BATCH_ID


@pytest.mark.parametrize("failure", [None, "lost_child", "lease"])
def test_shared_publication_requires_readback_and_current_ownership(monkeypatch, failure):
    rows = shared.project_shared_oms_observations(run_id="run-1", session_date=date(2026, 8, 18),
        checkpoint_sequence=1, boundary_ms=30000, groups={"group": SimpleNamespace(
            broker_order_ids=("broker",), broker_order_state_fingerprints={
                "broker": ("working", "Submitted", 0., 5., 0.001, 10., 0., "", "", "")})})
    keeper = Keeper()
    keeper.connected, keeper.client_id = True, (101, b"synthetic")
    keeper.add_listener = lambda listener: None
    keeper.exists = lambda path: keeper.rows.get(path)
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    dispatch = TypedInsertDispatch(keeper)
    dispatch.initialize_new_run("run-1")
    compact_running_prefix(dispatch)
    current = [True]
    # Physical admission is mocked; real lease assertion and dispatch CAS run.
    lease = BacktestV4KeeperLease(SimpleNamespace(is_current=lambda *a, **k: current[0]),
        "run-1", "synthetic-owner", 1)
    prefix = V4CommittedPrefix("run-1", 1, BATCH_ID, "cursor", "running", (BATCH_ID,))
    monkeypatch.setattr(commits, "load_writer_v4_snapshot_prefix", lambda *args: prefix)
    monkeypatch.setattr(commits, "load_verified_v4_prefix", lambda *args: prefix)
    monkeypatch.setattr(projection, "load_latest_backtest_cursor", lambda *args: dict(
        run_id="run-1", event_sequence=1, batch_id=BATCH_ID, boundary_ms=30000, session_date="2026-08-18"))
    monkeypatch.setattr(arte_journal_schema, "storage_preflight", lambda *args, **kwargs: None)
    tables, inserted = {}, []
    class Client:
        typed_insert_strict = True
        typed_insert_dispatch = dispatch
        backtest_v4_lease = lease
        def execute(self, sql, **kwargs):
            table = sql.split("arte.", 1)[1].split(" ", 1)[0]
            if sql.startswith("INSERT INTO "):
                inserted.append(table)
                tables.setdefault(table, []).extend(json.loads(line)
                    for line in sql.split("JSONEachRow\n", 1)[1].splitlines())
                if table == shared.OBSERVATION.name:
                    if failure == "lost_child":
                        raise TimeoutError("Synthetic lost INSERT acknowledgement")
                    if failure == "lease":
                        current[0] = False
                return ""
            return "\n".join(json.dumps(row) for row in tables.get(table, ()))
    client = Client()
    path = shared.ManagedSharedOmsObservationHeadReader.path("run-1")
    if failure:
        with pytest.raises((TimeoutError, RuntimeError)):
            shared.publish_shared_oms_observations(client, session, rows, journal_batch_id=BATCH_ID)
        assert path not in keeper.rows
        assert shared.ROOT.name not in inserted
        return
    head = shared.publish_shared_oms_observations(client, session, rows, journal_batch_id=BATCH_ID)
    assert head.snapshot_hash == rows.root["content_hash"]
    assert inserted == [shared.OBSERVATION.name, shared.ROOT.name]
    assert dispatch._read_gate("run-1")[0].registered == 0
    assert shared.publish_shared_oms_observations(client, session, rows, journal_batch_id=BATCH_ID) == head
    assert len(inserted) == 2

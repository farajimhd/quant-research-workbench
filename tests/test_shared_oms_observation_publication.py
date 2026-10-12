"""Central publisher against synthetic SQL and real typed-dispatch Keeper logic."""
import json
from datetime import date
from types import SimpleNamespace
from threading import Event
import pytest

from src.trading_runtime import arte_oms_observation_snapshot as shared
from src.trading_runtime import arte_journal_schema, arte_journal_commit_v4 as commits, arte_journal_projection as projection
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.trading_runtime import arte_journal_writer as writer
from tests.test_arte_typed_insert_dispatch import Keeper, TypedInsertDispatch, compact_running_prefix, BATCH_ID


@pytest.mark.parametrize("failure", [None, "lost_child", "lease"])
@pytest.mark.parametrize("queued", [False, True])
def test_shared_publication_requires_readback_and_current_ownership(monkeypatch, failure, queued):
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
        manager_keeper_session = session
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
    lane = None
    entered, release = Event(), Event()
    if queued:
        monkeypatch.setattr(writer, "_v4_preflight", lambda *args: None)
        monkeypatch.setattr(writer, "_verify_run_identity", lambda *args: dict(mode="backtest", account_ids=("account",)))
        compact = dispatch.compact_verified_shared_oms_observation_snapshot
        def held_compact(**kwargs):
            entered.set()
            assert release.wait(5)
            return compact(**kwargs)
        monkeypatch.setattr(dispatch, "compact_verified_shared_oms_observation_snapshot", held_compact)
        lane = writer.ArteJournalWriter(client, run_id="run-1", journal_profile="backtest_v4", coalesce_batches=False)
        lane._last_commit_id = BATCH_ID
    def publish():
        if lane is None:
            return shared.publish_shared_oms_observations(client, session, rows, journal_batch_id=BATCH_ID)
        receipt = lane.submit_shared_oms_observation_snapshot(rows, journal_batch_id=BATCH_ID)
        if not failure and not release.is_set():
            assert entered.wait(5)
            assert not receipt.done() and path not in keeper.rows
            release.set()
        return receipt.result(timeout=5)
    try:
        if failure:
            with pytest.raises((TimeoutError, RuntimeError)):
                publish()
            assert path not in keeper.rows
            assert shared.ROOT.name not in inserted
            return
        receipt = publish()
        head = shared.ManagedSharedOmsObservationHeadReader(session).read_head(run_id="run-1")
        assert receipt == (BATCH_ID if queued else head)
        assert head.snapshot_hash == rows.root["content_hash"]
        assert inserted == [shared.OBSERVATION.name, shared.ROOT.name]
        assert dispatch._read_gate("run-1")[0].registered == 0
        assert publish() == receipt
        assert len(inserted) == 2
    finally:
        release.set()
        if lane is not None:
            if failure:
                with pytest.raises(RuntimeError, match="did not drain durably"):
                    lane.close()
            else:
                lane.close()

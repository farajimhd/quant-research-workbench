"""Real queued central snapshot publication over synthetic SQL/Keeper stores."""
import json
import re
from threading import Event
from types import SimpleNamespace

import pytest

from tests.test_shared_broker_match_snapshot import snapshot, Store
from tests.test_arte_typed_insert_dispatch import Keeper, TypedInsertDispatch, compact_running_prefix, BATCH_ID
from src.trading_runtime import arte_broker_match_snapshot as shared
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime import arte_journal_commit_v4 as commits, arte_journal_projection as projection
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


@pytest.mark.parametrize("failure", [None, "lost_child", "lease"])
def test_queue_receipt_waits_for_central_readback_and_head_selection(monkeypatch, failure):
    rows, contract = snapshot(1000, run_id="run-1", checkpoint_sequence=1)
    store = Store(rows)
    store.rows = {name: () for name in store.rows}
    store.require_overlap = False
    keeper = Keeper()
    keeper.connected, keeper.client_id = True, (101, b"test")
    keeper.add_listener = lambda listener: None
    keeper.exists = lambda path: keeper.rows.get(path)
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    dispatch = TypedInsertDispatch(keeper)
    dispatch.initialize_new_run("run-1")
    compact_running_prefix(dispatch)
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    # Physical run admission is a separate bootstrap qualification. This
    # fixture uses the real lease method with an explicitly controlled owner.
    owner_current = [True]
    lease = BacktestV4KeeperLease(SimpleNamespace(is_current=lambda *args, **kwargs: owner_current[0]), "run-1", "test-owner", 1)
    inserted = []
    def client():
        result = store.client()
        result.typed_insert_strict, result.typed_insert_dispatch = True, dispatch
        result.manager_keeper_session = session
        result.backtest_v4_lease = lease
        execute = result.execute
        def sql(statement, **kwargs):
            if statement.startswith("INSERT INTO arte."):
                table = re.search(r"INSERT INTO arte\.([a-z0-9_]+)", statement).group(1)
                values = tuple(json.loads(line) for line in statement.split("JSONEachRow\n", 1)[1].splitlines())
                with store.lock:
                    store.rows[table] += values
                    inserted.append(table)
                if failure == "lost_child" and table == shared.ACCOUNT.name:
                    raise OSError("synthetic lost INSERT acknowledgement")
                if failure == "lease" and table == shared.MARK.name:
                    owner_current[0] = False
                return ""
            return execute(statement)
        result.execute = sql
        return result
    root_client = client()
    prefix = V4CommittedPrefix("run-1", 1, BATCH_ID, "cursor", "running", (BATCH_ID,))
    monkeypatch.setattr(commits, "load_writer_v4_snapshot_prefix", lambda *args: prefix)
    monkeypatch.setattr(commits, "load_verified_v4_prefix", lambda *args: prefix)
    monkeypatch.setattr(projection, "load_latest_backtest_cursor", lambda *args: dict(run_id="run-1",
        event_sequence=1, batch_id=BATCH_ID, boundary_ms=rows.snapshot["boundary_ms"],
        session_date=rows.snapshot["session_date"]))
    monkeypatch.setattr(writer, "_v4_preflight", lambda client: None)
    monkeypatch.setattr(writer, "_verify_run_identity", lambda *args: dict(mode="backtest", account_ids=("account",)))
    entered, release = Event(), Event()
    native_compact = dispatch.compact_verified_shared_broker_match_snapshot
    def held_compact(**kwargs):
        assert inserted[-1] == shared.ROOT.name
        assert shared.ManagedSharedBrokerMatchHeadReader.path("run-1") not in keeper.rows
        entered.set()
        assert release.wait(timeout=5)
        return native_compact(**kwargs)
    monkeypatch.setattr(dispatch, "compact_verified_shared_broker_match_snapshot", held_compact)
    lane = writer.ArteJournalWriter(root_client, run_id="run-1", journal_profile="backtest_v4", coalesce_batches=False)
    # This fixture separately established a real compacted dispatch prefix;
    # its admitted run context is the explicitly mocked external seam above.
    lane._last_commit_id = BATCH_ID
    try:
        receipt = lane.submit_shared_broker_match_snapshot(rows, broker_contract=contract,
            journal_batch_id=BATCH_ID, connection_factory=client, concurrency=2)
        if failure:
            with pytest.raises((OSError, RuntimeError, ExceptionGroup)):
                receipt.result(timeout=5)
            assert shared.ManagedSharedBrokerMatchHeadReader.path("run-1") not in keeper.rows
            assert not store.rows[shared.ROOT.name]
            if failure == "lost_child":
                assert dispatch._read_gate("run-1")[0].inflight > 0
            return
        assert entered.wait(timeout=5)
        assert not receipt.done()
        release.set()
        assert receipt.result(timeout=5) == BATCH_ID
        head = shared.ManagedSharedBrokerMatchHeadReader(session).read_head(run_id="run-1")
        assert head.snapshot_hash == rows.snapshot["content_hash"]
        assert dispatch._read_gate("run-1")[0].registered == 0
        before = tuple(inserted)
        repeated = lane.submit_shared_broker_match_snapshot(rows, broker_contract=contract,
            journal_batch_id=BATCH_ID, connection_factory=client, concurrency=2)
        assert repeated.result(timeout=5) == BATCH_ID and tuple(inserted) == before
    finally:
        release.set()
        if failure:
            with pytest.raises(RuntimeError, match="did not drain durably"):
                lane.close()
        else:
            lane.close()
        root_client.close()

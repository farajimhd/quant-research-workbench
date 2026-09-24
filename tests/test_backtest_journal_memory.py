import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.trading_runtime.journal_evidence import REFERENCE
from src.trading_runtime.journal import TradingJournal


RUN_ID = "00000000-0000-4000-8000-000000000001"
AT = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _entry(entity_id: str, *, run_id: str = RUN_ID) -> dict:
    return dict(run_id=run_id, category="strategy", entity_type="signal",
                entity_id=entity_id, payload={"ticker": "AAPL"}, event_time=AT)


def test_invalid_batch_does_not_publish_partial_prefix():
    journal = BacktestMemoryJournal(run_id=RUN_ID)
    with pytest.raises(ValueError, match="mix runs"):
        journal.append_many([_entry("one"), _entry("two", run_id="other")])
    assert journal.latest_sequence(RUN_ID) == 0
    assert journal.records(RUN_ID) == []
    with pytest.raises(ValueError, match="mix runs"):
        journal.append_once_many([_entry("one"), _entry("two", run_id="other")])
    assert journal.latest_sequence(RUN_ID) == 0


def test_idempotent_batch_preserves_order_and_first_occurrence():
    journal = BacktestMemoryJournal(run_id=RUN_ID)
    result = journal.append_once_many([_entry("one"), _entry("one"), _entry("two")])
    assert [inserted for _, inserted in result] == [True, False, True]
    assert [record.sequence for record, _ in result] == [1, 1, 2]
    assert journal.append_once_many([_entry("one")])[0] == (result[0][0], False)
    assert [record.sequence for record in journal.records(RUN_ID, after_sequence=1)] == [2]


def test_buffer_fails_closed_and_evidence_uses_live_reference_contract():
    journal = BacktestMemoryJournal(run_id=RUN_ID, max_pending_records=1)
    assert set(journal.reference_json({"foo": 1})) == {REFERENCE}
    encoded = journal.reference_evidence({"levels": [{"price": 12.0}]})
    assert set(encoded["levels"]) == {REFERENCE}
    journal.append_many([_entry("one")])
    with pytest.raises(RuntimeError, match="buffer is full"):
        journal.append_many([_entry("two")])
    assert journal.latest_sequence(RUN_ID) == 1
    with pytest.raises(RuntimeError, match="async fence"):
        journal.flush()
    assert [record.sequence for record in journal.unfenced_records()] == [1]
    journal.mark_fenced(1)
    assert journal.unfenced_records() == []
    assert journal.append_many([_entry("two")])[0].sequence == 2
    with pytest.raises(ValueError, match="outside the unfenced prefix"):
        journal.unfenced_records(after_sequence=0)


def test_acknowledged_batches_release_memory_without_reusing_sequences():
    journal = BacktestMemoryJournal(run_id=RUN_ID, max_pending_records=2)
    for index in range(20):
        record = journal.append_many([_entry(str(index))])[0]
        assert record.sequence == index + 1
        journal.mark_fenced(record.sequence)
        assert journal.pending_record_count == 0
        assert journal.unfenced_records() == []
        assert journal._records == []
        with pytest.raises(ValueError, match="ClickHouse"):
            journal.records(RUN_ID)
    assert journal.latest_sequence(RUN_ID) == 20
    assert journal._by_identity == {}


def test_signal_idempotence_survives_fence_without_retaining_all_decisions():
    journal = BacktestMemoryJournal(run_id=RUN_ID)
    signal = dict(_entry("signal"), category="market_discovery_signal")
    original = journal.append_many([signal])[0]
    journal.mark_fenced(original.sequence)
    replayed, inserted = journal.append_once_many([signal])[0]
    assert not inserted and replayed.record_id == original.record_id
    assert journal._records == []
    assert len(journal._by_identity) == 1


def test_campaign_ownership_matches_live_reserve_confirm_release_contract():
    journal = BacktestMemoryJournal(run_id=RUN_ID)
    key = dict(resource_id="book:AAPL", session_key="2026-08-18")
    first = journal.acquire_campaign_session_ownership(**key, owner_id="strategy-a",
                                                        state="reserved")
    assert first == {**key, "owner_id": "strategy-a", "state": "reserved", "epoch": 1}
    assert journal.acquire_campaign_session_ownership(**key, owner_id="strategy-b",
                                                       state="reserved") is None
    assert not journal.release_campaign_session_reservation(**key, owner_id="strategy-b")
    confirmed = journal.acquire_campaign_session_ownership(**key, owner_id="strategy-a",
                                                            state="confirmed")
    assert confirmed["state"] == "confirmed" and confirmed["epoch"] == 2
    assert not journal.release_campaign_session_reservation(**key, owner_id="strategy-a")
    assert journal.campaign_session_ownership(**key)["owner_id"] == "strategy-a"

    second = dict(resource_id="book:MSFT", session_key="2026-08-18")
    journal.acquire_campaign_session_ownership(**second, owner_id="strategy-a", state="reserved")
    assert journal.release_campaign_session_reservation(**second, owner_id="strategy-a")
    assert journal.campaign_session_ownership(**second) is None


def test_assignment_upsert_preserves_identity_and_detaches_mutable_state():
    journal = BacktestMemoryJournal(run_id=RUN_ID)
    payload = {"assignment_id": "a", "strategy_id": "s", "strategy_revision": 3,
               "account_id": "paper", "ticker": "aapl", "conid": 42,
               "status": "watching", "state": {"legs": [1]}}
    saved = journal.save_strategy_assignment(payload)
    payload["state"]["legs"].append(2)
    assert saved["ticker"] == "AAPL"
    assert journal.strategy_assignment("a")["state"] == {"legs": [1]}
    journal.save_strategy_assignments([{"assignment_id": "a", "strategy_id": "other",
                                        "status": "managing", "state": {"legs": [3]}}],
                                      return_rows=False)
    updated = journal.strategy_assignments(account_id="paper", ticker="AAPL", active_only=True)[0]
    assert updated["strategy_id"] == "s"
    assert updated["status"] == "managing"
    assert updated["state"] == {"legs": [3]}


def test_operational_assignment_and_campaign_contract_matches_live_journal(tmp_path):
    live = TradingJournal(tmp_path / "parity.sqlite3")
    memory = BacktestMemoryJournal(run_id=RUN_ID)
    try:
        payload = {"assignment_id": "a", "strategy_id": "s", "strategy_revision": 3,
                   "account_id": "paper", "ticker": "aapl", "conid": 42,
                   "status": "watching", "state": {"legs": [1]},
                   "created_at": "2026-08-18T12:00:00+00:00",
                   "updated_at": "2026-08-18T12:00:00+00:00"}
        left = live.save_strategy_assignment(payload)
        right = memory.save_strategy_assignment(payload)
        assert {key: value for key, value in left.items() if key != "updated_at"} == {
            key: value for key, value in right.items() if key != "updated_at"}
        key = dict(resource_id="book:AAPL", session_key="2026-08-18")
        for state in ("reserved", "confirmed", "reserved"):
            assert live.acquire_campaign_session_ownership(**key, owner_id="s", state=state) == (
                memory.acquire_campaign_session_ownership(**key, owner_id="s", state=state))
        assert not live.release_campaign_session_reservation(**key, owner_id="s")
        assert not memory.release_campaign_session_reservation(**key, owner_id="s")
    finally:
        live.close()


def test_controller_checkpoint_becomes_resumable_only_after_clickhouse_fence():
    journal = BacktestMemoryJournal(run_id=RUN_ID, max_pending_records=1)
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller.run_id = RUN_ID
    controller.status = "running"
    controller._journal = journal
    controller._checkpoint_projection_cache = None
    controller._checkpoint_io_task = None
    controller.stream_snapshot = lambda: {"status": "running"}
    controller._restart_checkpoint_state = lambda **kwargs: {
        "schema_version": 1, "controller": {
            "source_cursor": {"session_date": "2026-08-18", "boundary_ms": 100},
            "frame_cursor": {}, "processed_events": 1,
        },
    }
    controller._record_stage_time = lambda *_args: None
    controller._restart_checkpoint_interval_events = lambda: 100

    class Publisher:
        def __init__(self, *, fail=False):
            self.fail = fail
            self.calls = []

        async def fence_checkpoint(self, **kwargs):
            assert journal.load_checkpoint(RUN_ID) is None
            self.calls.append(kwargs)
            if self.fail:
                raise OSError("commit unavailable")
            return "fence"

    at = AT
    failed = Publisher(fail=True)
    controller._journal_publisher = failed
    with pytest.raises(OSError, match="commit unavailable"):
        asyncio.run(controller._save_restart_checkpoint_responsive(at))
    assert journal.load_checkpoint(RUN_ID) is None
    assert controller._checkpoint_projection_cache is None
    assert controller._checkpoint_io_task is None

    succeeded = Publisher()
    controller._journal_publisher = succeeded
    asyncio.run(controller._save_restart_checkpoint_responsive(
        at, checkpoint_status="completed"))
    assert succeeded.calls[0]["source_cursor"] == journal.load_checkpoint(RUN_ID)["cursor"]
    assert succeeded.calls[0]["status"] == "completed"
    assert controller._checkpoint_projection_cache["resume_supported"] is True
    assert controller._checkpoint_io_task is None


def test_controller_opens_clickhouse_journal_after_contract_preflight(monkeypatch):
    import src.backend.backtest_journal_clickhouse as clickhouse_journal

    calls = []

    class Client:
        def close(self):
            calls.append("closed")

    client = Client()
    monkeypatch.setattr(clickhouse_journal, "journal_clickhouse_client", lambda: client)
    monkeypatch.setattr(clickhouse_journal, "storage_preflight",
                        lambda actual: calls.append(("preflight", actual)))
    monkeypatch.setattr(clickhouse_journal, "backtest_code_hash", lambda _root: "b" * 64)
    monkeypatch.setattr(clickhouse_journal, "publish_run",
                        lambda actual, **kwargs: calls.append(("run", actual, kwargs)))
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": "a" * 64},
        market_data_plan={"token": "market"},
        causal_v7_plan={"token": "v7"},
        payload=lambda: {"mode": "backtest"},
    )
    controller.run_id = RUN_ID
    controller.created_at = AT
    controller._journal = None
    controller._journal_writer = None
    controller._journal_publisher = None

    async def exercise():
        await controller._open_fixed_journal()
        assert isinstance(controller._journal, BacktestMemoryJournal)
        assert controller._journal_publisher.journal is controller._journal
        assert calls[0] == ("preflight", client)
        assert calls[1][2]["market_plan_token"] == "market"
        await controller._close_fixed_journal()

    asyncio.run(exercise())
    assert calls[-1] == "closed"

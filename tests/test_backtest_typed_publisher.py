import asyncio
from concurrent.futures import Future
from datetime import date, timezone
from threading import Event
from types import SimpleNamespace

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.arte_journal_writer import JournalQueueFull
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot
from src.trading_runtime.runtime import RunMode


RUN = "00000000-0000-0000-0000-000000000a01"
ATTEMPT = "00000000-0000-0000-0000-000000000a02"
DAY = date(2026, 8, 18)
AT = market_day_boundary(DAY, 300_000).astimezone(timezone.utc)


class FakeWriter:
    run_id = RUN
    run_mode = "backtest"
    journal_profile = "backtest_v2"
    coalesce_batches = False
    max_events_per_commit = 512

    def __init__(self, *, automatic=True):
        self.automatic = automatic
        self.submitted = []
        self.receipts = []
        self.error = None

    def submit(self, batch):
        if self.error is not None:
            raise self.error
        receipt = Future()
        self.submitted.append(batch)
        self.receipts.append(receipt)
        if self.automatic:
            receipt.set_result(batch.batch_id)
        return receipt


def _journal():
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                   entity_id=RUN, event_time=AT,
                   payload={"status": "running", "config": {"mode": "backtest"}})
    journal.append(run_id=RUN, category="checkpoint",
                   entity_type="market_boundary",
                   entity_id=f"{DAY.isoformat()}:300000", event_time=AT,
                   payload={"session_date": DAY.isoformat(), "boundary_ms": 300_000,
                            "market_sequence": 2, "frame_as_of": None,
                            "frame_ticker": None, "frame_timeframe": None,
                            "frame_sequence": None})
    return journal


def _publisher(journal, writer, *, batch_size=512):
    return BacktestTypedJournalPublisher(
        journal, writer, attempt_id=ATTEMPT, run_month=DAY.replace(day=1),
        batch_size=batch_size, expected_config={"mode": "backtest"})


def test_fixed_publisher_rejects_legacy_v1_writer():
    writer = FakeWriter()
    writer.journal_profile = "v1"
    with pytest.raises(ValueError, match="exclusive bounded prefix"):
        _publisher(_journal(), writer)


def test_v4_publisher_routes_running_prefix_only_to_v4_queue():
    class V4Writer(FakeWriter):
        journal_profile = "backtest_v4"

        def submit(self, batch):
            pytest.fail("V4 publication used legacy typed queue")

        def submit_base_v4(self, batch):
            return FakeWriter.submit(self, batch)

    async def exercise():
        journal = _journal()
        writer = V4Writer()
        publisher = _publisher(journal, writer)
        receipt = await publisher.enqueue_pending()
        assert receipt.last_sequence == 2
        assert len(writer.submitted) == 1
        assert writer.submitted[0].status == "running"
        assert journal.pending_record_count == 0

    asyncio.run(exercise())


def test_v4_publisher_routes_broker_and_protection_in_sequence():
    class V4Writer(FakeWriter):
        journal_profile = "backtest_v4"

        def submit_base_v4(self, batch):
            self.kinds.append("base")
            return FakeWriter.submit(self, batch)

        def submit_broker_acknowledgement_v4(self, unit):
            self.kinds.append("broker")
            return FakeWriter.submit(self, unit.base)

        def submit_protection_change_v4(self, unit):
            self.kinds.append("protection")
            return FakeWriter.submit(self, unit.base)

    async def exercise():
        journal = _journal()
        journal.append(
            run_id=RUN, category="broker", entity_type="order_acknowledgement",
            entity_id="1001", account_id="DU1", event_time=AT,
            payload={"order_id": "1001", "order_status": "Submitted",
                     "local_order_id": "coid-1", "order_group_id": "group-1",
                     "decision_to_submit_ms": 1.25, "ticker": "AAA",
                     "action": "enter_long", "intent_id": "intent-1",
                     "correlation_id": "correlation-1",
                     "causation_id": "causation-1",
                     "strategy_id": "early-squeeze-strategy", "strategy_revision": 1})
        journal.append(
            run_id=RUN, category="protection", entity_type="protection_change",
            entity_id="1002", account_id="DU1", event_time=AT,
            payload={"schema_version": 1, "order_group_id": "group-1",
                     "entry_order_ids": ["coid-1"], "order_id": "1002",
                     "client_order_id": "coid-2", "kind": "stop",
                     "phase": "effective", "price": 9.89, "active": True,
                     "ticker": "AAA", "source_intent_id": "intent-1",
                     "strategy_id": "early-squeeze-strategy",
                     "strategy_revision": 1, "action": "enter_long",
                     "intent_id": "intent-1",
                     "correlation_id": "correlation-1",
                     "causation_id": "causation-1"})
        writer = V4Writer()
        writer.kinds = []
        publisher = _publisher(journal, writer)
        receipt = await publisher.enqueue_pending()
        assert receipt.last_sequence == 4
        assert writer.kinds == ["base", "broker", "protection"]
        assert [(batch.first_sequence, batch.last_sequence)
                for batch in writer.submitted] == [(1, 2), (3, 3), (4, 4)]
        assert writer.submitted[1].prior_batch_id == writer.submitted[0].batch_id
        assert writer.submitted[2].prior_batch_id == writer.submitted[1].batch_id
        assert journal.pending_record_count == 0

    asyncio.run(exercise())


def test_v4_publisher_routes_numbered_entry_with_exact_child_off_hot_path():
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent

    class V4Writer(FakeWriter):
        journal_profile = "backtest_v4"

        def submit_base_v4(self, _batch):
            pytest.fail("Numbered entry lost its normalized child")

        def submit_strategy_one_entry_v4(self, unit):
            self.entry_unit = unit
            return FakeWriter.submit(self, unit.base)

    async def exercise():
        proposal = StrategyOneEntryProposal(
            "assignment-1", "DU1", "AAA", 31_000, 30_000,
            10.01, 9.89, 12., "R4", .5, 30_000, "S1")
        intent = strategy_one_entry_intent(proposal, session_date=DAY)
        journal = BacktestMemoryJournal(run_id=RUN)
        record = journal.append_strategy_one_intent(
            intent=intent, proposal=proposal, session_date=DAY,
            account_id="DU1", strategy_id="early-squeeze-strategy",
            strategy_revision=0)
        writer = V4Writer(automatic=False)
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=ATTEMPT, run_month=DAY.replace(day=1),
            expected_config={"mode": "backtest",
                             "strategy_id": "early-squeeze-strategy",
                             "strategy_revision": 0})
        task = publisher.enqueue_pending()
        for _ in range(100):
            if writer.receipts:
                break
            await asyncio.sleep(.01)
        assert writer.receipts and not task.done()
        assert journal.pending_record_count == 1
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        receipt = await task
        assert receipt.last_sequence == 1
        assert writer.entry_unit.entry_evidence[0]["parent_record_id"] == record.record_id
        assert writer.entry_unit.entry_evidence[0]["target_level_id"] == "R4"
        assert journal.strategy_one_entry_for_record(record.record_id) is None
        assert journal.pending_record_count == 0

    asyncio.run(exercise())


def test_v4_publisher_refuses_numbered_intent_without_atomic_sidecar():
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent

    class V4Writer(FakeWriter):
        journal_profile = "backtest_v4"

        def submit_base_v4(self, _batch):
            pytest.fail("Unpaired Strategy 1 intent reached the writer")

    async def exercise():
        proposal = StrategyOneEntryProposal(
            "assignment-1", "DU1", "AAA", 31_000, 30_000,
            10.01, 9.89, 12., "R4", .5, 30_000, "S1")
        intent = strategy_one_entry_intent(proposal, session_date=DAY)
        journal = BacktestMemoryJournal(run_id=RUN)
        journal.append(
            run_id=RUN, category="strategy", entity_type="strategy_intent",
            entity_id=intent.intent_id, account_id="DU1", event_time=intent.event_time,
            payload={**intent.payload(), "strategy_id": "early-squeeze-strategy",
                     "strategy_revision": 0})
        writer = V4Writer()
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=ATTEMPT, run_month=DAY.replace(day=1),
            expected_config={"mode": "backtest",
                             "strategy_id": "early-squeeze-strategy",
                             "strategy_revision": 0})
        with pytest.raises(RuntimeError, match="lacks normalized evidence"):
            await publisher.enqueue_pending()
        assert journal.pending_record_count == 1
        assert not writer.submitted

    asyncio.run(exercise())


def test_v4_terminal_queues_after_predecessor_and_fences_only_after_receipt(monkeypatch):
    import src.backend.backtest_typed_publisher as publisher_module

    def legacy_projection_forbidden(*_args, **_kwargs):
        raise AssertionError("V4 terminal used the legacy typed projector")

    monkeypatch.setattr(publisher_module, "project_pending_backtest_prefix",
                        legacy_projection_forbidden)

    class V4Writer(FakeWriter):
        journal_profile = "backtest_v4"

        def submit_base_v4(self, batch):
            return FakeWriter.submit(self, batch)

        def submit_terminal_backtest(self, batch, captures):
            assert len(captures) == 1 and captures[0].run_id == RUN
            return FakeWriter.submit(self, batch)

    async def exercise():
        journal = _journal()
        writer = V4Writer(automatic=False)
        publisher = _publisher(journal, writer)
        running = publisher.enqueue_pending()
        await _wait_for_submission(writer)
        journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                       entity_id=RUN, event_time=AT,
                       payload={"status": "completed", "processed_events": 2})
        capture = CapturedPortfolioSnapshot(
            RUN, "DU1", 1, AT, "primary", "enabled", "synchronized",
            "broker-snapshot-1", AT, "", 1000.0, None, None,
            (), (), (), (), (), (),
        )
        terminal = publisher.enqueue_terminal((capture,))
        assert not terminal.done() and publisher.fenced_sequence == 0
        with pytest.raises(RuntimeError, match="terminal publication owns"):
            publisher.enqueue_pending()
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        await running
        for _ in range(100):
            if len(writer.submitted) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(writer.submitted) == 2
        assert writer.submitted[1].status == "completed"
        assert publisher.fenced_sequence == 2 and journal.pending_record_count == 1
        writer.receipts[1].set_result(writer.submitted[1].batch_id)
        assert (await terminal).last_sequence == 3
        assert journal.pending_record_count == 0

    asyncio.run(exercise())


def test_v4_terminal_appended_before_running_task_starts_stays_out_of_base_queue():
    class V4Writer(FakeWriter):
        journal_profile = "backtest_v4"

        def submit_base_v4(self, batch):
            assert batch.status == "running"
            return FakeWriter.submit(self, batch)

        def submit_terminal_backtest(self, batch, captures):
            assert batch.status == "completed"
            return FakeWriter.submit(self, batch)

    async def exercise():
        journal = _journal()
        writer = V4Writer()
        publisher = _publisher(journal, writer)
        running = publisher.enqueue_pending()
        journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                       entity_id=RUN, event_time=AT,
                       payload={"status": "completed", "processed_events": 2})
        capture = CapturedPortfolioSnapshot(
            RUN, "DU1", 1, AT, "primary", "enabled", "synchronized",
            "broker-snapshot-1", AT, "", 1000.0, None, None,
            (), (), (), (), (), (),
        )
        terminal = publisher.enqueue_terminal((capture,))
        assert (await running).last_sequence == 2
        assert (await terminal).last_sequence == 3
        assert [batch.status for batch in writer.submitted] == ["running", "completed"]

    asyncio.run(exercise())


def test_fixed_controller_v4_finish_captures_exact_terminal_actor_state():
    class V4Writer(FakeWriter):
        journal_profile = "backtest_v4"

        def submit_base_v4(self, batch):
            return FakeWriter.submit(self, batch)

        def submit_terminal_backtest(self, batch, captures):
            assert batch.last_sequence == captures[0].state_revision
            assert captures[0].snapshot_at == AT
            return FakeWriter.submit(self, batch)

    class Portfolio:
        def capture_recovery_snapshot(self, account_id, *, state_revision, snapshot_at):
            assert account_id == "DU1" and state_revision == 3
            return CapturedPortfolioSnapshot(
                RUN, account_id, state_revision, snapshot_at, "primary",
                "enabled", "synchronized", "broker-snapshot-1", AT,
                "", 1000.0, None, None, (), (), (), (), (), (),
            )

    async def exercise():
        journal = _journal()
        writer = V4Writer()
        publisher = _publisher(journal, writer)
        controller = object.__new__(ReplayRunController)
        controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
        controller.run_id = RUN
        controller._account_map = {"primary": "DU1"}
        controller._journal = journal
        controller._journal_publisher = publisher
        controller._runtime_finished = False

        async def finish(*, status):
            journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                           entity_id=RUN, event_time=AT,
                           payload={"status": status, "processed_events": 2})

        controller._runtime = SimpleNamespace(
            finish=finish, portfolio=Portfolio())
        await controller._finish_fixed_v4("completed")
        assert controller._runtime_finished
        assert publisher.fenced_sequence == 3
        assert journal.pending_record_count == 0
        assert [batch.status for batch in writer.submitted] == ["running", "completed"]

    asyncio.run(exercise())


def test_invalid_evidence_fails_projection_before_writer_submission():
    async def exercise():
        journal = BacktestMemoryJournal(run_id=RUN)
        journal.append(
            run_id=RUN, category="lifecycle", entity_type="run",
            entity_id=RUN, event_time=AT,
            payload={"status": "running", "config": {"mode": "backtest",
                                                      "invalid": float("nan")}},
        )
        writer = FakeWriter()
        publisher = _publisher(journal, writer)
        with pytest.raises(ValueError, match="Out of range float"):
            await publisher.enqueue_pending()
        assert writer.submitted == []
        assert journal.pending_record_count == 1

    asyncio.run(exercise())


async def _wait_for_submission(writer):
    for _ in range(100):
        if writer.submitted:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("Fake writer did not receive the projected batch")


def test_delayed_writer_does_not_stall_engine_and_one_receipt_fences_batch():
    async def exercise():
        journal = _journal()
        writer = FakeWriter(automatic=False)
        publisher = _publisher(journal, writer)
        task = publisher.enqueue_pending()
        assert not task.done()
        engine_ticks = 0
        for _ in range(3):
            engine_ticks += 1
            await asyncio.sleep(0)
        await _wait_for_submission(writer)
        assert engine_ticks == 3
        assert len(writer.submitted) == 1
        assert len(writer.submitted[0].events) == 2
        assert publisher.fenced_sequence == 0
        assert journal.pending_record_count == 2
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        result = await publisher.await_fence()
        assert result.last_sequence == 2
        assert result.source_cursor == f"{DAY.isoformat()}:300000"
        assert journal.pending_record_count == 0
        assert (await publisher.enqueue_pending()).last_sequence == 2

    asyncio.run(exercise())


def test_large_pending_prefix_projects_only_one_commit_sized_slice_at_a_time():
    async def exercise():
        journal = BacktestMemoryJournal(run_id=RUN)
        for ordinal in range(5):
            journal.append(
                run_id=RUN, category="risk", entity_type="risk_snapshot",
                entity_id=RUN, event_time=AT,
                payload={"status": "stale", "error": f"fault-{ordinal}",
                         "entries_frozen": True})
        writer = FakeWriter(automatic=False)
        publisher = _publisher(journal, writer, batch_size=2)
        task = publisher.enqueue_pending()
        for index, expected_count in enumerate((2, 2, 1)):
            for _ in range(100):
                if len(writer.submitted) > index:
                    break
                await asyncio.sleep(.001)
            assert len(writer.submitted) == index + 1
            assert len(writer.submitted[index].events) == expected_count
            assert journal.pending_record_count == 5 - 2 * index
            writer.receipts[index].set_result(writer.submitted[index].batch_id)
        receipt = await task
        assert receipt.last_sequence == 5
        assert journal.pending_record_count == 0
        assert [batch.first_sequence for batch in writer.submitted] == [1, 3, 5]

    asyncio.run(exercise())


def test_checkpoint_enqueue_does_not_wait_for_clickhouse_receipt():
    async def exercise():
        journal = _journal()
        writer = FakeWriter(automatic=False)
        publisher = _publisher(journal, writer)
        receipt = publisher.enqueue_checkpoint(
            boundary_id=f"{DAY.isoformat()}:300000")
        assert not receipt.done()
        assert publisher.checkpoint_pending
        await _wait_for_submission(writer)
        assert publisher.fenced_sequence == 0
        with pytest.raises(RuntimeError, match="already pending"):
            publisher.enqueue_checkpoint(boundary_id=f"{DAY.isoformat()}:300000")
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        committed = await receipt
        assert committed.source_cursor == f"{DAY.isoformat()}:300000"
        assert committed.last_sequence == 2
        assert not publisher.checkpoint_pending
        assert journal.pending_record_count == 0

    asyncio.run(exercise())


def test_checkpoint_queues_behind_inflight_prefix_without_blocking_engine():
    async def exercise():
        journal = BacktestMemoryJournal(run_id=RUN)
        journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                       entity_id=RUN, event_time=AT,
                       payload={"status": "running", "config": {"mode": "backtest"}})
        writer = FakeWriter(automatic=False)
        publisher = _publisher(journal, writer)
        publisher.enqueue_pending()
        await _wait_for_submission(writer)
        journal.append(run_id=RUN, category="checkpoint",
                       entity_type="market_boundary",
                       entity_id=f"{DAY.isoformat()}:300000", event_time=AT,
                       payload={"session_date": DAY.isoformat(), "boundary_ms": 300_000,
                                "market_sequence": 2, "frame_as_of": None,
                                "frame_ticker": None, "frame_timeframe": None,
                                "frame_sequence": None})
        receipt = publisher.enqueue_checkpoint(
            boundary_id=f"{DAY.isoformat()}:300000")
        assert not receipt.done()
        assert len(writer.submitted) == 1
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        for _ in range(100):
            if len(writer.submitted) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(writer.submitted) == 2
        assert not receipt.done()
        assert publisher.fenced_sequence == 1
        writer.receipts[1].set_result(writer.submitted[1].batch_id)
        committed = await receipt
        assert committed.last_sequence == 2
        assert committed.source_cursor == f"{DAY.isoformat()}:300000"
        assert journal.pending_record_count == 0

    asyncio.run(exercise())


def test_queued_checkpoint_fails_if_prior_clickhouse_receipt_fails():
    async def exercise():
        journal = BacktestMemoryJournal(run_id=RUN)
        journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                       entity_id=RUN, event_time=AT,
                       payload={"status": "running", "config": {"mode": "backtest"}})
        writer = FakeWriter(automatic=False)
        publisher = _publisher(journal, writer)
        publisher.enqueue_pending()
        await _wait_for_submission(writer)
        journal.append(run_id=RUN, category="checkpoint",
                       entity_type="market_boundary",
                       entity_id=f"{DAY.isoformat()}:300000", event_time=AT,
                       payload={"session_date": DAY.isoformat(), "boundary_ms": 300_000,
                                "market_sequence": 2, "frame_as_of": None,
                                "frame_ticker": None, "frame_timeframe": None,
                                "frame_sequence": None})
        receipt = publisher.enqueue_checkpoint(
            boundary_id=f"{DAY.isoformat()}:300000")
        writer.receipts[0].set_exception(RuntimeError("prior insert failed"))
        with pytest.raises(RuntimeError, match="prior insert failed"):
            await receipt
        assert publisher.fenced_sequence == 0
        assert journal.pending_record_count == 2
        assert len(writer.submitted) == 1

    asyncio.run(exercise())


def test_async_checkpoint_does_not_absorb_later_unfenced_records(monkeypatch):
    import src.backend.backtest_typed_publisher as publisher_module

    real_project = publisher_module.project_pending_backtest_prefix
    started = Event()
    release = Event()

    def delayed_project(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return real_project(*args, **kwargs)

    monkeypatch.setattr(publisher_module, "project_pending_backtest_prefix", delayed_project)

    async def exercise():
        journal = _journal()
        writer = FakeWriter()
        publisher = _publisher(journal, writer)
        receipt = publisher.enqueue_checkpoint(
            boundary_id=f"{DAY.isoformat()}:300000")
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        journal.append(run_id=RUN, category="test", entity_type="later",
                       entity_id="later", event_time=AT, payload={})
        release.set()
        assert (await receipt).last_sequence == 2
        assert journal.pending_record_count == 1
        assert len(writer.submitted[0].events) == 2

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_unsupported_record_rejects_entire_prefix_before_submit():
    journal = _journal()
    journal.append(run_id=RUN, category="watchlist_membership",
                   entity_type="historical_watchlist_member", entity_id="ABCD",
                   event_time=AT, payload={"event": "added"})
    writer = FakeWriter()
    publisher = _publisher(journal, writer)
    async def exercise():
        publisher.enqueue_pending()
        with pytest.raises(ValueError, match="lacks a typed projection"):
            await publisher.await_fence()

    asyncio.run(exercise())
    assert not writer.submitted
    assert journal.pending_record_count == 3


def test_failed_receipt_and_full_queue_do_not_advance_uncommitted_batch():
    journal = _journal()
    writer = FakeWriter(automatic=False)
    publisher = _publisher(journal, writer)

    async def fail_receipt():
        publisher.enqueue_pending()
        await _wait_for_submission(writer)
        writer.receipts[0].set_exception(RuntimeError("fake insert failed"))
        with pytest.raises(RuntimeError, match="fake insert failed"):
            await publisher.await_fence()

    asyncio.run(fail_receipt())
    assert publisher.fenced_sequence == 0
    assert journal.pending_record_count == 2
    with pytest.raises(RuntimeError, match="publication failed"):
        publisher.enqueue_pending()

    writer = FakeWriter()
    writer.error = JournalQueueFull("fake bounded queue full")
    publisher = _publisher(journal, writer)

    async def fail_queue():
        publisher.enqueue_pending()
        with pytest.raises(JournalQueueFull, match="bounded queue full"):
            await publisher.await_fence()

    asyncio.run(fail_queue())
    assert publisher.fenced_sequence == 0


def test_writer_must_disable_recoalescing_and_allow_bounded_batch_size():
    writer = FakeWriter()
    writer.coalesce_batches = True
    with pytest.raises(ValueError, match="exclusive bounded prefix"):
        _publisher(_journal(), writer)
    writer.coalesce_batches = False
    writer.max_events_per_commit = 1
    with pytest.raises(ValueError, match="exclusive bounded prefix"):
        _publisher(_journal(), writer, batch_size=2)


def test_slow_projection_runs_off_event_loop(monkeypatch):
    import src.backend.backtest_typed_publisher as publisher_module

    real_project = publisher_module.project_pending_backtest_prefix
    started = Event()
    release = Event()

    def slow_project(*args, **kwargs):
        started.set()
        if not release.wait(2):
            raise AssertionError("Slow projector was never released")
        return real_project(*args, **kwargs)

    monkeypatch.setattr(publisher_module, "project_pending_backtest_prefix", slow_project)

    async def exercise():
        writer = FakeWriter()
        publisher = _publisher(_journal(), writer)
        task = publisher.enqueue_pending()
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        ticks = 0
        for _ in range(5):
            ticks += 1
            await asyncio.sleep(0)
        assert ticks == 5 and not task.done() and not writer.submitted
        release.set()
        assert (await publisher.await_fence()).last_sequence == 2

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_fixed_controller_fences_only_typed_cursor_without_opaque_checkpoint(monkeypatch):
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                   entity_id=RUN, event_time=AT,
                   payload={"status": "running", "config": {"mode": "backtest"}})
    writer = FakeWriter()
    publisher = _publisher(journal, writer)
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller.run_id = RUN
    controller.status = "running"
    controller.processed_events = 2
    controller._journal = journal
    controller._journal_publisher = publisher
    controller._source_cursor = {"session_date": DAY.isoformat(),
                                 "boundary_ms": 300_000, "sequence": 2}
    controller._frame_cursor = {}
    controller._checkpoint_projection_cache = None
    controller.stream_snapshot = lambda: {}
    controller._flush_passive_market_events = lambda: None
    controller._record_stage_time = lambda *_: None
    controller._restart_checkpoint_interval_events = lambda: None
    monkeypatch.setattr(journal, "save_checkpoint", lambda *_: pytest.fail("opaque checkpoint"))

    asyncio.run(controller._save_restart_checkpoint_responsive(AT))

    assert len(writer.submitted) == 1
    assert len(writer.submitted[0].events) == 2
    assert writer.submitted[0].source_cursor == f"{DAY.isoformat()}:300000"
    assert publisher.fenced_sequence == 2
    assert journal.pending_record_count == 0
    assert controller._checkpoint_projection_cache["resume_supported"] is False


def test_fixed_controller_queues_checkpoint_without_waiting_for_writer():
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                   entity_id=RUN, event_time=AT,
                   payload={"status": "running", "config": {"mode": "backtest"}})
    writer = FakeWriter(automatic=False)
    publisher = _publisher(journal, writer)
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller.run_id = RUN
    controller.status = "running"
    controller.processed_events = 2
    controller._journal = journal
    controller._journal_publisher = publisher
    controller._source_cursor = {"session_date": DAY.isoformat(),
                                 "boundary_ms": 300_000, "sequence": 2}
    controller._frame_cursor = {}
    controller._checkpoint_projection_cache = None
    controller._checkpoint_io_task = None
    controller.stream_snapshot = lambda: pytest.fail("nonblocking checkpoint serialized UI snapshot")
    controller._flush_passive_market_events = lambda: None
    controller._record_stage_time = lambda *_: None
    controller._restart_checkpoint_interval_events = lambda: None

    async def exercise():
        await controller._save_restart_checkpoint_responsive(
            AT, nonblocking_fixed=True)
        assert controller._checkpoint_io_task is not None
        assert not controller._checkpoint_io_task.done()
        await _wait_for_submission(writer)
        assert publisher.fenced_sequence == 0
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        await controller._checkpoint_io_task
        await asyncio.sleep(0)
        assert publisher.fenced_sequence == 2
        assert controller._checkpoint_io_task is None
        assert controller._checkpoint_projection_cache["status"] == "cursor_fenced"

    asyncio.run(exercise())


def test_fixed_controller_terminal_checkpoint_fails_before_journal_write():
    journal = BacktestMemoryJournal(run_id=RUN)
    writer = FakeWriter()
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller._journal = journal
    controller._journal_publisher = _publisher(journal, writer)
    controller.status = "completed"

    with pytest.raises(RuntimeError, match="lifecycle-last typed account captures"):
        asyncio.run(controller._save_restart_checkpoint_responsive(
            AT, checkpoint_status="completed"))
    assert not writer.submitted
    assert journal.pending_record_count == 0

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
from src.trading_runtime.runtime import RunMode


RUN = "00000000-0000-0000-0000-000000000a01"
ATTEMPT = "00000000-0000-0000-0000-000000000a02"
DAY = date(2026, 8, 18)
AT = market_day_boundary(DAY, 300_000).astimezone(timezone.utc)


class FakeWriter:
    run_id = RUN
    run_mode = "backtest"
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
    controller.stream_snapshot = lambda: {}
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

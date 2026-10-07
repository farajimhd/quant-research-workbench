"""Forced checkpoints return completed publisher evidence, never queue tokens.

These tests exercise the controller and actual typed publisher with an in-memory
journal and controllable writer receipts. They do not attest a database snapshot.
"""
import asyncio
from types import SimpleNamespace

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.runtime import RunMode
from test_backtest_typed_publisher import (
    AT, DAY, RUN, FakeWriter, _publisher, _wait_for_submission,
)


def controller_fixture():
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
    return controller, writer, publisher


def test_forced_checkpoint_returns_only_after_actual_publisher_fence():
    async def exercise():
        controller, writer, publisher = controller_fixture()
        task = asyncio.create_task(controller._save_restart_checkpoint_responsive(AT))
        await _wait_for_submission(writer)
        assert not task.done() and publisher.fenced_sequence == 0
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        receipt = await task
        assert type(receipt) is TypedBacktestReceipt
        assert receipt == TypedBacktestReceipt(
            2, writer.submitted[0].batch_id, f"{DAY.isoformat()}:300000")
        assert controller._checkpoint_io_task is None
        assert controller._checkpoint_work_snapshot is None
        assert controller._journal.pending_record_count == 0
    asyncio.run(exercise())


@pytest.mark.parametrize('nonblocking', [False, True])
def test_controller_forwards_selected_capture_owner(monkeypatch, nonblocking):
    """Exercise both controller routes; native owner validation is tested separately."""
    async def exercise():
        controller, writer, publisher = controller_fixture()
        writer.journal_profile = 'backtest_v4'
        captured = object()
        owner = SimpleNamespace(capture=lambda manager, **kwargs: captured)
        controller._strategy_one_manager = SimpleNamespace(
            _fixed_lot_owner=owner,
            evidence=SimpleNamespace(capture_recovery_state=lambda:
                SimpleNamespace(boundary_ms=300_000)))
        controller._account_map = {'assignment': 'account'}
        controller.warmup_events = 0
        controller._processed_frames = 0
        controller._runtime = SimpleNamespace(
            processed_events=2, last_event_time=AT,
            broker=SimpleNamespace(broker_match_snapshot_state=lambda: ()),
            order_manager=SimpleNamespace(capture_observed_broker_states=lambda: ()),
            portfolio=SimpleNamespace(capture_recovery_snapshot=lambda *a, **k: object()))
        calls = []
        def enqueue(**kwargs):
            calls.append(kwargs)
            sequence = controller._journal.unfenced_records()[-1].sequence
            publisher._sequence = sequence
            result = asyncio.get_running_loop().create_future()
            result.set_result(TypedBacktestReceipt(sequence, 'batch', kwargs['boundary_id']))
            return result
        monkeypatch.setattr(publisher, 'enqueue_checkpoint', enqueue)
        await controller._save_restart_checkpoint_responsive(AT, nonblocking_fixed=nonblocking)
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert calls[0]['manager_state'] is captured
        assert calls[0]['fixed_lot_owner'] is owner
        assert calls[0]['portfolio_captures']
    asyncio.run(exercise())


def test_failed_publication_cannot_return_an_arming_receipt():
    async def exercise():
        controller, writer, publisher = controller_fixture()
        task = asyncio.create_task(controller._save_restart_checkpoint_responsive(AT))
        await _wait_for_submission(writer)
        writer.receipts[0].set_exception(RuntimeError("publication failed"))
        with pytest.raises(RuntimeError, match="publication failed"):
            await task
        assert publisher.fenced_sequence == 0
        assert controller._checkpoint_projection_cache is None
        assert controller._checkpoint_io_task is None
        assert controller._journal.pending_record_count == 2
    asyncio.run(exercise())


@pytest.mark.parametrize('receipt', [object(),
    TypedBacktestReceipt(2, 'batch', 'wrong-cursor'),
    TypedBacktestReceipt(99, 'batch', f'{DAY.isoformat()}:300000')])
def test_invalid_publisher_receipt_is_rejected_before_cache_update(monkeypatch, receipt):
    from unittest.mock import AsyncMock
    controller, _, publisher = controller_fixture()
    monkeypatch.setattr(publisher, 'fence_checkpoint', AsyncMock(return_value=receipt))
    with pytest.raises(RuntimeError, match='receipt differs from its fence'):
        asyncio.run(controller._save_restart_checkpoint_responsive(AT))
    assert controller._checkpoint_projection_cache is None
    assert controller._checkpoint_io_task is None


def test_cancellation_waits_for_publication_but_does_not_return_receipt():
    async def exercise():
        controller, writer, publisher = controller_fixture()
        task = asyncio.create_task(controller._save_restart_checkpoint_responsive(AT))
        await _wait_for_submission(writer)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not writer.receipts[0].cancelled()
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert publisher.fenced_sequence == 2
        assert controller._checkpoint_io_task is None
        assert controller._checkpoint_work_snapshot is None
    asyncio.run(exercise())


def test_forced_checkpoint_awaits_pending_periodic_checkpoint_then_its_own():
    async def exercise():
        controller, writer, publisher = controller_fixture()
        assert await controller._save_restart_checkpoint_responsive(
            AT, nonblocking_fixed=True) is None
        await _wait_for_submission(writer)
        controller._source_cursor = {**controller._source_cursor,
                                     "boundary_ms": 300_100, "sequence": 3}
        from src.backend.backtest_market_data import market_day_boundary
        task = asyncio.create_task(controller._save_restart_checkpoint_responsive(
            market_day_boundary(DAY, 300_100)))
        await asyncio.sleep(0)
        assert not task.done() and len(writer.submitted) == 1
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        for _ in range(100):
            if len(writer.submitted) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(writer.submitted) == 2 and not task.done()
        writer.receipts[1].set_result(writer.submitted[1].batch_id)
        receipt = await task
        assert receipt.source_cursor == f"{DAY.isoformat()}:300100"
        assert receipt.last_batch_id == writer.submitted[1].batch_id
        assert receipt.last_sequence == publisher.fenced_sequence == 3
        assert controller._checkpoint_io_task is None
    asyncio.run(exercise())

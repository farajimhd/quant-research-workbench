import asyncio
from datetime import date, time
from threading import Event
from unittest.mock import AsyncMock, patch

import pytest

from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, RunMode
from src.trading_runtime.journal import TradingJournal
from tests.test_replay_run_service import approved_configuration


def test_checkpoint_keeps_status_responsive_and_preserves_state(tmp_path):
    async def check():
        controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 7, 28),
            start_time=time(9, 45), mode=RunMode.BACKTEST, tickers=('AAPL',),
            configuration_revision=approved_configuration()), runtime_root=tmp_path)
        controller.run_dir.mkdir(parents=True)
        controller._journal = TradingJournal(controller.run_dir / 'journal.sqlite3')
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        controller.status = 'running'
        controller.current_time = controller.definition.session_start
        controller._runtime_inputs_ready = True
        expected = controller._restart_checkpoint_state()
        captured, release_capture = Event(), Event()
        persisting, release_persist = Event(), Event()
        original_capture = controller._restart_checkpoint_state
        original_save = controller._journal.save_checkpoint

        def capture(**kwargs):
            captured.set()
            assert release_capture.wait(5)
            return original_capture(**kwargs)

        def persist(*args):
            persisting.set()
            assert release_persist.wait(5)
            return original_save(*args)

        try:
            with (patch.object(controller, '_restart_checkpoint_state', side_effect=capture),
                  patch.object(controller._journal, 'save_checkpoint', side_effect=persist)):
                task = asyncio.create_task(controller._save_restart_checkpoint_responsive(controller.current_time))
                try:
                    assert await asyncio.to_thread(captured.wait, 2)
                    from src.backend.app import backtest_run_service, trading_backtest_run
                    with patch.object(backtest_run_service, 'get', return_value=controller):
                        for phase, event in [('checkpoint_capture', release_capture), ('checkpoint_persist', release_persist)]:
                            if phase == 'checkpoint_persist':
                                assert await asyncio.to_thread(persisting.wait, 2)
                            # The real compact endpoint must not read mutable state
                            # or wait on the checkpoint's journal transaction.
                            with patch.object(controller, 'snapshot', side_effect=AssertionError('uncached status')):
                                result = await asyncio.wait_for(trading_backtest_run(controller.run_id, compact=True), .2)
                            assert result['work_progress']['phase'] == phase
                            assert result['work_progress']['active']
                            assert result['current_time'] == expected['controller']['current_time']
                            with pytest.raises(ValueError, match='Checkpoint'):
                                await controller.add_assignment({})
                            response = await asyncio.wait_for(controller.command('pause' if phase == 'checkpoint_capture' else 'stop'), .2)
                            assert response['work_progress']['active']
                            assert response['status'] == response['lifecycle']['source_status'] == 'paused'
                            if phase == 'checkpoint_persist':
                                assert response['work_progress']['stop_requested']
                            event.set()
                        await task
                finally:
                    release_capture.set(); release_persist.set()
                    await task
            actual = controller._journal.load_checkpoint(controller.run_id)['state']
            assert actual == expected
            assert controller.stream_snapshot()['work_progress']['phase'] == 'playback'
        finally:
            controller._journal.close()
    asyncio.run(check())


@pytest.mark.parametrize('mode, expected_calls', [(RunMode.BACKTEST, 0), (RunMode.REPLAY, 1)])
def test_checkpoint_cadence_preserves_replay_and_removes_backtest_playback_work(tmp_path, mode, expected_calls):
    async def check():
        controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 7, 28),
            start_time=time(9, 45), mode=mode, tickers=('AAPL',),
            configuration_revision=approved_configuration()), runtime_root=tmp_path)
        controller.status = 'running'
        controller.processed_events = 1_000_000
        controller._processed_frames = 1_000_000
        controller._source_cursor = {'sequence': 1_000_000}
        controller._frame_cursor = {'sequence': 1_000_000}
        with (patch.object(controller, '_publish', new_callable=AsyncMock),
              patch.object(controller, '_save_restart_checkpoint_responsive', new_callable=AsyncMock) as save):
            await controller._after_event(controller.definition.requested_start)
            await controller._after_event(controller.definition.requested_start)
            assert save.await_count == expected_calls
        assert controller._checkpoint_projection()['interval_events'] == (None if mode == RunMode.BACKTEST else 25_000)
    asyncio.run(check())


@pytest.mark.parametrize('cursor_kind', ['market', 'frame'])
def test_pause_saves_exact_state_once_at_engine_boundary_and_play_continues(tmp_path, cursor_kind):
    async def check():
        controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 7, 28),
            start_time=time(9, 45), mode=RunMode.BACKTEST, tickers=('AAPL',),
            configuration_revision=approved_configuration()), runtime_root=tmp_path)
        controller.run_dir.mkdir(parents=True)
        controller._journal = TradingJournal(controller.run_dir / 'journal.sqlite3')
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        controller.status = 'running'
        controller.current_time = controller.definition.requested_start
        controller.processed_events = 123
        controller._source_cursor = {'sequence': 123, 'ts': controller.current_time.isoformat(), 'ticker': 'AAPL', 'kind': 'quote'}
        if cursor_kind == 'frame':
            controller._frame_cursor = {'sequence': 123, 'as_of': controller.current_time.isoformat(), 'ticker': 'AAPL', 'timeframe': '1s'}
            controller._source_cursor = {}
        controller._runtime_inputs_ready = True
        saved = asyncio.Event()
        original_save = controller._save_restart_checkpoint_responsive
        async def save(at):
            await original_save(at)
            saved.set()
        gate = None
        try:
            with (patch.object(controller, '_publish', new_callable=AsyncMock),
                  patch.object(controller, '_schedule_manifest_write'),
                  patch.object(controller, '_save_restart_checkpoint_responsive', side_effect=save) as capture):
                await controller.command('pause')
                capture.assert_not_called()  # HTTP/control task cannot capture mid-event.
                expected = controller._restart_checkpoint_state()
                gate = asyncio.create_task(controller._wait_until_active())
                await asyncio.wait_for(saved.wait(), 5)
                assert not gate.done()
                assert controller._journal.load_checkpoint(controller.run_id)['state'] == expected
                restored = ReplayRunController(controller.definition, run_id=controller.run_id,
                    runtime_root=tmp_path, resume_state=expected)
                restored._journal = TradingJournal(tmp_path / 'restored.sqlite3')
                try:
                    await restored._initialize_runtime(record_configuration=False, record_lifecycle=False)
                    assert restored.processed_events == controller.processed_events
                    assert restored._source_cursor == controller._source_cursor
                    assert restored._frame_cursor == controller._frame_cursor
                    assert restored._runtime.broker.checkpoint_state() == controller._runtime.broker.checkpoint_state()
                finally:
                    restored._journal.close()
                await controller.command('pause')
                await asyncio.sleep(0)
                assert capture.await_count == 1
                await controller.command('play')
                await asyncio.wait_for(gate, 2)
                assert controller.status == 'running'
                assert controller.processed_events == 123
                assert not controller._pause_checkpoint_requested
                # A later pause must save its new boundary too.
                saved.clear()
                controller.processed_events = 124
                (controller._source_cursor if cursor_kind == 'market' else controller._frame_cursor)['sequence'] = 124
                await controller.command('pause')
                gate = asyncio.create_task(controller._wait_until_active())
                await asyncio.wait_for(saved.wait(), 5)
                assert capture.await_count == 2
                assert controller._journal.load_checkpoint(controller.run_id)['state']['controller']['processed_events'] == 124
                await controller.command('stop')
                await asyncio.wait_for(gate, 2)
        finally:
            if gate is not None and not gate.done():
                gate.cancel()
                await asyncio.gather(gate, return_exceptions=True)
            controller._journal.close()
    asyncio.run(check())

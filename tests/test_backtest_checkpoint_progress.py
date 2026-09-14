import asyncio
from datetime import date, time
from threading import Event
from unittest.mock import patch

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

import asyncio
import json
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, ReplayRunService, RunMode
from src.trading_runtime.journal import TradingJournal
from tests.test_replay_run_service import approved_configuration


@pytest.mark.parametrize('kind', ['market', 'frame', 'external_signal'])
@pytest.mark.parametrize('cancelled', [False, True])
def test_interrupted_unit_cannot_be_saved_as_resumable(tmp_path, kind, cancelled):
    async def check():
        controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 7, 28),
            start_time=time(9, 45), mode=RunMode.BACKTEST, tickers=('AAPL',),
            configuration_revision=approved_configuration()), runtime_root=tmp_path)
        controller.run_dir.mkdir(parents=True)
        controller._journal = TradingJournal(controller.run_dir / 'journal.sqlite3')
        try:
            await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
            at = controller.definition.requested_start
            controller.current_time = at
            unit = SimpleNamespace(ticker='AAPL', ts=at, as_of=at, available_at=at)
            error = asyncio.CancelledError if cancelled else RuntimeError
            if kind == 'market':
                async def broker_mutation(*args, **kwargs):
                    controller._runtime.processed_events += 1
                    controller._source_cursor = dict(ts=at.isoformat(), ticker='AAPL', sequence=1, kind='trade')
                controller._process_market_event = broker_mutation
                controller._process_strategy_market_event = AsyncMock(side_effect=error('interrupted'))
                operation = controller._process_replay_market_event
            else:
                setattr(controller, '_apply_strategy_frame' if kind == 'frame' else '_apply_external_signal_event',
                    AsyncMock(side_effect=error('interrupted')))
                operation = controller._process_strategy_frame if kind == 'frame' else controller._process_external_signal_event
            with pytest.raises(error):
                await operation(unit)
            controller._save_restart_checkpoint(at)
            saved = controller._journal.load_checkpoint(controller.run_id)['state']
            assert saved['complete'] is False
            assert saved['incomplete_processing_unit'] == dict(kind=kind, ticker='AAPL', at=at.isoformat())
            (controller.run_dir / 'manifest.json').write_text(json.dumps({'run':{'status':'failed'}}))
            with pytest.raises(ValueError, match='complete restart-safe checkpoint'):
                await ReplayRunService(runtime_root=tmp_path).resume(controller.run_id)
            # Older failed runs claimed completeness without tracking partial work.
            saved['complete'] = True
            saved.pop('processing_boundary_version')
            controller._journal.save_checkpoint(controller.run_id, '', saved, at)
            with pytest.raises(ValueError, match='certified processing boundary'):
                await ReplayRunService(runtime_root=tmp_path).resume(controller.run_id)
        finally:
            controller._journal.close()
    asyncio.run(check())


def test_successful_unit_clears_marker_but_failed_unit_cannot_continue():
    controller = object.__new__(ReplayRunController)
    from datetime import datetime, timezone
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with controller._processing_unit('frame', 'AAPL', at):
        assert controller._incomplete_processing_unit is not None
    assert controller._incomplete_processing_unit is None
    with pytest.raises(RuntimeError, match='failure'):
        with controller._processing_unit('market', 'AAPL', at):
            raise RuntimeError('failure')
    with pytest.raises(RuntimeError, match='incomplete processing unit'):
        with controller._processing_unit('market', 'AAPL', at):
            pass

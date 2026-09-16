import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.run_strategy_222_refinement import request_stop


@pytest.mark.parametrize('status,requested',[('running',True),('stopped',False),('failed',False)])
def test_stop_is_not_repeated_while_worker_finishes_cleanup(status,requested):
    async def check():
        worker=asyncio.get_running_loop().create_future()
        controller=SimpleNamespace(_task=worker,status=status,_stop_requested=requested,command=AsyncMock())
        await request_stop(controller)
        controller.command.assert_not_awaited()
        assert not worker.done()  # The caller still needs to await cleanup.
        worker.set_result(None)
    asyncio.run(check())


@pytest.mark.parametrize('error', ['', 'checkpoint write failed'])
def test_requested_stop_finishes_cleanup_without_starting_next_trial(tmp_path, monkeypatch, capsys, error):
    import json
    from datetime import date
    from pathlib import Path
    from unittest.mock import Mock
    from scripts import run_strategy_222_refinement as runner
    from src.backend import replay_run_service, trading_configuration_service, historical_signal_occurrence_service

    # Keep the launcher's runtime-root restriction while isolating all artifacts.
    def runtime_path(value):
        prefix = 'D:/TradingML/runtimes'
        return tmp_path / str(value)[len(prefix):].lstrip('/') if str(value).startswith(prefix) else Path(value)

    monkeypatch.setattr(runner, 'Path', runtime_path)
    monkeypatch.setattr(runner, 'source_identity', lambda: {})
    prepare = Mock(return_value=dict(candidate_id='candidate', candidate_revision=1))
    monkeypatch.setattr(runner, 'prepare', prepare)
    monkeypatch.setattr(historical_signal_occurrence_service, '_load_repository_env', lambda: None)
    monkeypatch.setattr(trading_configuration_service, 'candidate_runtime_configuration_snapshot', lambda *a, **k: {})
    monkeypatch.setattr(replay_run_service, 'ReplayRunDefinition', lambda **k: SimpleNamespace(**k))

    async def check():
        worker = asyncio.get_running_loop().create_future()
        controller = SimpleNamespace(run_id='run', status='running', error='', current_time=None,
            processed_events=0, _preparation_stage='ready', _preparation_completed_units=1,
            _preparation_total_units=1, _stop_requested=False, _task=worker,
            _monitoring=SimpleNamespace(close=AsyncMock()), _journal=SimpleNamespace(close=Mock()),
            start=AsyncMock())
        journal = controller._journal
        async def command(action):
            assert action == 'stop'
            controller._stop_requested = True
            controller.status = 'stopped'
            controller.error = error
            worker.set_result(None)
        controller.command = command
        monkeypatch.setattr(replay_run_service, 'ReplayRunController', lambda *a, **k: controller)
        monkeypatch.setattr(runner.asyncio, 'wait', AsyncMock())
        stop = tmp_path / 'stop'
        async def start():
            stop.write_text('operator requested stop')
        controller.start = start
        args = SimpleNamespace(runtime=tmp_path/'trial', symbol='TEST', end='04:10:00',
            session_date=date(2026, 8, 19), variants=['first', 'second'], portfolio_symbols=None,
            recipe_parameters=None, restart_at=None, stop_request_file=stop)
        if error:
            with pytest.raises(RuntimeError, match='checkpoint write failed'):
                await runner.run_locked(args)
        else:
            await runner.run_locked(args)
        assert worker.done()
        controller._monitoring.close.assert_awaited_once()
        journal.close.assert_called_once()
        prepare.assert_called_once()
        manifest = json.loads((args.runtime/'manifest.json').read_text())
        assert len(manifest['trials']) == 1
        assert manifest['trials'][0]['stop_requested'] is True
        assert manifest['trials'][0]['status'] == 'stopped'
        assert manifest['trials'][0]['error'] == error
    asyncio.run(check())
    output = capsys.readouterr().out
    assert ('Stopped comparisons on request after cleanup:' in output) == (not error)
    assert 'Completed comparisons:' not in output


@pytest.mark.parametrize('terminal_race',[True,False])
def test_only_terminal_race_is_accepted(terminal_race):
    async def check():
        worker=asyncio.get_running_loop().create_future()
        controller=SimpleNamespace(_task=worker,status='running',_stop_requested=False)
        async def command(value):
            assert value=='stop'
            if terminal_race:controller.status='stopped'
            raise ValueError('command failed')
        controller.command=command
        if terminal_race:await request_stop(controller)
        else:
            with pytest.raises(ValueError,match='command failed'):await request_stop(controller)
        worker.set_result(None)
    asyncio.run(check())

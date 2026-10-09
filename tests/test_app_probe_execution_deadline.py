"""Exercise execution deadlines without replacing the native stop lifecycle."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.clickhouse import smoke_strategy_one_app_route as probe


def test_execution_deadline_stops_and_drains_original_task(monkeypatch, capsys):
    async def exercise():
        task = asyncio.get_running_loop().create_future()
        clock = iter((0.0, 2.0))
        monkeypatch.setattr(probe, 'perf_counter', lambda: next(clock))

        async def stop(command):
            assert command == 'stop'
            assert not task.cancelled()
            task.set_result(None)

        controller = SimpleNamespace(run_id='deadline-run', _task=task,
                                     command=AsyncMock(side_effect=stop))
        with pytest.raises(TimeoutError, match='incomplete financial result'):
            await probe._await_run_with_progress(controller, max_execution_s=1)
        controller.command.assert_awaited_once_with('stop')
        assert task.done() and not task.cancelled()

    asyncio.run(exercise())
    assert 'financial_result_valid=false' in capsys.readouterr().out


def test_completed_task_is_not_stopped_at_deadline():
    async def exercise():
        task = asyncio.get_running_loop().create_future()
        task.set_result(None)
        controller = SimpleNamespace(_task=task, command=AsyncMock())
        await probe._await_run_with_progress(controller, max_execution_s=1)
        controller.command.assert_not_awaited()
    asyncio.run(exercise())


def test_cleanup_failure_is_not_hidden_by_timeout(monkeypatch):
    async def exercise():
        task = asyncio.get_running_loop().create_future()
        clock = iter((0.0, 2.0))
        monkeypatch.setattr(probe, 'perf_counter', lambda: next(clock))

        async def stop(command):
            task.set_exception(RuntimeError('journal cleanup failed'))

        controller = SimpleNamespace(run_id='deadline-run', _task=task,
                                     command=AsyncMock(side_effect=stop))
        with pytest.raises(RuntimeError, match='journal cleanup failed'):
            await probe._await_run_with_progress(controller, max_execution_s=1)
    asyncio.run(exercise())

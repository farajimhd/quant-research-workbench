import asyncio
import os
import subprocess
import sys
import threading
from unittest.mock import AsyncMock

import pytest

from src.backend.filtered_v7_preparation import preparation_process, _reap


def run_on_backend_loop(coro):
    factory = asyncio.SelectorEventLoop if sys.platform == 'win32' else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(coro)


@pytest.mark.parametrize('outcome', ['complete', 'failed', 'cancelled', 'startup_cancelled'])
def test_real_child_on_backend_event_loop(tmp_path, monkeypatch, outcome):
    from src.backend import filtered_v7_preparation as module
    started = threading.Event()
    release = threading.Event()
    children = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        started.set()
        if outcome == 'startup_cancelled':
            assert release.wait(10)
        return child

    monkeypatch.setattr(module.subprocess, 'Popen', spawn)

    async def check():
        code = 'import time; time.sleep(60)' if 'cancelled' in outcome else 'raise SystemExit(7)' if outcome == 'failed' else 'print("ready")'
        with (tmp_path / 'child.log').open('wb') as log:
            entered = asyncio.Event()

            async def worker():
                async with preparation_process(sys.executable, '-B', '-c', code,
                        stdout=log, stderr=log, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as child:
                    entered.set()
                    while child.poll() is None:
                        await asyncio.sleep(.01)
                    return child.returncode

            task = asyncio.create_task(worker())
            try:
                if 'cancelled' in outcome:
                    assert await asyncio.to_thread(started.wait, 10)
                    if outcome == 'cancelled':
                        await asyncio.wait_for(entered.wait(), 10)
                    task.cancel()
                    release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 10)
                else:
                    assert await asyncio.wait_for(task, 10) == (7 if outcome == 'failed' else 0)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        assert children and all(child.poll() is not None for child in children)

    run_on_backend_loop(check())


def test_worker_cleanup_escalates_after_termination_timeout():
    from unittest.mock import Mock
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired('worker', 5), 1]
    _reap(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_filtered_worker_entrypoint_on_backend_loop(tmp_path):
    async def check():
        with (tmp_path / 'entrypoint.log').open('wb') as log:
            async with preparation_process(sys.executable, '-B', '-m',
                    'research.level_book.v7.filtered_worker', '--help',
                    stdout=log, stderr=log,
                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as process:
                async with asyncio.timeout(20):
                    while process.poll() is None:
                        await asyncio.sleep(.01)
                assert process.returncode == 0
        assert '--ticker' in (tmp_path / 'entrypoint.log').read_text()
    run_on_backend_loop(check())


def test_empty_initialization_error_reaches_failed_run_and_canvas(tmp_path):
    from datetime import date, time
    from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, RunMode
    from tests.test_replay_run_service import approved_configuration

    async def check():
        controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 8, 18),
            start_time=time(4), mode=RunMode.BACKTEST, tickers=('TEST',),
            configuration_revision=approved_configuration()), runtime_root=tmp_path)
        controller._publish = AsyncMock(side_effect=NotImplementedError())
        controller._finish = AsyncMock()
        await controller._run_engine()
        controller._finish.assert_awaited_once_with('failed')
        assert controller.error == 'NotImplementedError'
        controller.status = 'failed'
        controller._preparation_stage = 'Checking filtered V7 histories'
        with pytest.raises(ValueError, match='Backtest initialization failed during Checking filtered V7 histories: NotImplementedError'):
            await controller._canvas_payload_unlocked('TEST')

    run_on_backend_loop(check())

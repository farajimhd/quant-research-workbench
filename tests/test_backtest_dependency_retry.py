import asyncio
from unittest.mock import patch

import pytest

from src.backend.qmd_gateway_client import (
    QmdServiceError, backtest_history_retries, qmd_history_get_json,
)
from src.backend.replay_run_service import ReplayRunController, RunMode
from types import SimpleNamespace


def failure(retryable=True):
    return QmdServiceError(service='QMD History', operation='GET',
        path='/snapshot/chart-bars/AGNC', code='qmd_upstream_timeout',
        message='timed out', retryable=retryable)


def test_retry_same_frozen_request_only_and_scope_restored():
    params = dict(as_of='2026-08-21T13:30:00.100Z', timeframe='1s')
    events = []
    def observe(key, event):
        events.append(event)
        params['as_of'] = '2099-01-01T00:00:00Z'
    with patch('src.backend.qmd_gateway_client._qmd_service_get_json', side_effect=[failure(), {'bars': [1]}]) as get, patch('src.backend.qmd_gateway_client.time.sleep') as sleep:
        with backtest_history_retries(observe):
            assert qmd_history_get_json('/snapshot/chart-bars/AGNC', params, timeout=60) == {'bars': [1]}
        assert get.call_args_list[0] == get.call_args_list[1]
        assert get.call_args.args[2]['as_of'] == '2026-08-21T13:30:00.100Z'
        sleep.assert_called_once_with(2)
        assert events[0]['attempt'] == 2 and events[-1] is None
    with patch('src.backend.qmd_gateway_client._qmd_service_get_json', side_effect=failure()) as get:
        with pytest.raises(QmdServiceError): qmd_history_get_json('/snapshot/chart-bars/AGNC')
        assert get.call_count == 1


@pytest.mark.parametrize('retryable,attempts', [(True, 3), (False, 1)])
def test_bounded_failure_never_returns_partial_data(retryable, attempts):
    events = []
    with backtest_history_retries(lambda key, event: events.append(event)), patch('src.backend.qmd_gateway_client._qmd_service_get_json', side_effect=failure(retryable)) as get, patch('src.backend.qmd_gateway_client.time.sleep') as sleep:
        with pytest.raises(QmdServiceError): qmd_history_get_json('/snapshot/chart-bars/AGNC')
        assert get.call_count == attempts
        assert sleep.call_count == attempts - 1
    if retryable: assert events[-1] is None


def test_controller_threaded_retry_reports_wait_without_replaying_engine():
    async def check():
        controller = object.__new__(ReplayRunController)
        controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
        controller.status = 'running'
        controller.processed_events = 123
        controller._runtime_inputs_ready = True
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def engine():
            calls.append('before')
            task = asyncio.create_task(asyncio.to_thread(qmd_history_get_json, '/snapshot/chart-bars/AGNC'))
            await entered.wait()
            work = controller._work_progress_payload()
            assert work['phase'] == 'waiting_for_data'
            assert work['dependencies'][0]['attempt'] == 2
            assert controller.processed_events == 123
            release.set()
            assert await task == {'bars': [1]}
            calls.append('after')
        controller._run_engine = engine
        loop = asyncio.get_running_loop()
        def wait(delay):
            loop.call_soon_threadsafe(entered.set)
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=5)
        with patch('src.backend.qmd_gateway_client._qmd_service_get_json', side_effect=[failure(), {'bars': [1]}]), patch('src.backend.qmd_gateway_client.time.sleep', side_effect=wait):
            await asyncio.wait_for(controller._run(), 10)
        assert calls == ['before', 'after']
        assert controller._dependency_retries == {}
    asyncio.run(check())

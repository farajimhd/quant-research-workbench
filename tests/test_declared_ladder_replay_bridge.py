"""Declared source batching and real controller pre-entry capture ordering."""
import asyncio
from types import SimpleNamespace

import pytest

from src.backend.backtest_declared_ladder_plan import _geometry_scopes, automatic_policy
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.backend.replay_run_service import ReplayRunController
from test_backtest_forced_checkpoint_receipt import controller_fixture
from test_backtest_typed_publisher import AT, DAY
from test_strategy_forty_nine_release import envelope


def test_geometry_certifies_whole_frozen_population_in_bounded_scopes():
    symbols = tuple(f'T{i:04}' for i in range(25))
    market = SimpleNamespace(tickers=symbols, sessions=('2026-08-18',), token='market')
    calls = []
    reader = object()
    def certify(kind):
        def run(market, *args, candidate_tickers, client, **kwargs):
            assert client is not reader and client.reader is reader
            assert len(candidate_tickers) <= 8
            calls.append((kind, candidate_tickers))
            return SimpleNamespace(token=kind+','.join(candidate_tickers),
                coverage=candidate_tickers,
                intervals=tuple((ticker, (1, 2)) for ticker in candidate_tickers))
        return run
    pivot, v7 = _geometry_scopes(market, object(), reader, certify('p'), certify('v'))
    assert tuple(t for scope in pivot.scopes for t in scope[0]) == symbols
    assert tuple(t for scope in v7.scopes for t in scope[0]) == symbols
    assert pivot.ticker_count == v7.ticker_count == 25
    assert len(calls) == 8
    assert not hasattr(v7, 'intervals')
    again = _geometry_scopes(market, object(), reader, certify('p'), certify('v'))
    assert again == (pivot, v7)
    with pytest.raises(RuntimeError, match='missing'):
        _geometry_scopes(market, object(), reader,
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('missing')), certify('v'))


def test_policy_selects_only_declared_complete_policy():
    assert automatic_policy({}) is None
    configuration = envelope()['payload']
    assert automatic_policy(configuration) is not None
    configuration['strategy']['numbered_release']['automatic_entry_policy']['contract'] = 'wrong'
    with pytest.raises(ValueError, match='Unknown declared'):
        automatic_policy(configuration)


def ladder_controller():
    controller, _, _ = controller_fixture()
    controller.definition.configuration_revision = envelope()
    controller.warmup_events = controller._processed_frames = 0
    controller._account_map = {'source':'DU1'}
    controller._strategy_one_manager = None
    captures = []
    controller._runtime = SimpleNamespace(last_event_time=AT, processed_events=2,
        broker=SimpleNamespace(broker_match_snapshot_state=lambda: {'actual': 'broker'}),
        order_manager=SimpleNamespace(capture_observed_broker_states=lambda: ('actual-oms',)),
        portfolio=SimpleNamespace(capture_recovery_snapshot=lambda account, **kwargs:
            captures.append((account, kwargs)) or ('actual-portfolio', account, kwargs)))
    calls, receipts = [], []
    def enqueue(**kwargs):
        calls.append(kwargs)
        future = asyncio.get_running_loop().create_future()
        receipts.append(future)
        return future
    controller._journal_publisher = SimpleNamespace(
        writer=SimpleNamespace(journal_profile='backtest_v4'), enqueue_checkpoint=enqueue)
    return controller, calls, receipts, captures


def test_pre_entry_fresh_capture_waits_previous_then_queues_distinct_actual_state():
    async def run():
        controller, calls, receipts, captures = ladder_controller()
        await controller._save_restart_checkpoint_responsive(AT, nonblocking_fixed=True)
        task = asyncio.create_task(controller._save_restart_checkpoint_responsive(
            AT, nonblocking_fixed=True, require_fresh_capture=True))
        await asyncio.sleep(0)
        assert not task.done() and len(calls) == 1
        controller._journal.mark_fenced(2)
        receipts[0].set_result(TypedBacktestReceipt(2, 'b1', f'{DAY}:300000'))
        await task
        assert len(calls) == 2 and not receipts[1].done()
        assert calls[1]['manager_state'] is calls[1]['evidence_state'] is None
        assert calls[1]['broker_state'] == (300_000, {'actual': 'broker'})
        assert calls[1]['oms_observations'] == ('actual-oms',)
        assert len(captures) == 2
        assert captures[-1][1] == {'state_revision':3, 'snapshot_at':AT}
        receipts[1].set_result(TypedBacktestReceipt(3, 'b2', f'{DAY}:300000'))
        await asyncio.sleep(0)
        assert controller._checkpoint_io_task is None
    asyncio.run(run())


@pytest.mark.parametrize('drift', ['clock', 'manager'])
def test_declared_capture_rejects_stale_clock_or_historical_manager(drift):
    async def run():
        controller, calls, _, _ = ladder_controller()
        if drift == 'clock':
            controller._runtime.last_event_time = None
        else:
            controller._strategy_one_manager = object()
        with pytest.raises(RuntimeError, match='boundary|manager'):
            await controller._save_restart_checkpoint_responsive(AT, nonblocking_fixed=True)
        assert not calls
    asyncio.run(run())


def test_declared_fixed_runner_dispatches_without_historical_plans():
    async def run():
        controller = object.__new__(ReplayRunController)
        controller.definition = SimpleNamespace(configuration_revision=envelope())
        calls = []
        async def declared():
            calls.append('declared')
        controller._run_declared_ladder_fixed_days = declared
        await controller._run_fixed_market_days()
        assert calls == ['declared']
    asyncio.run(run())


@pytest.mark.parametrize('declared', [True, False])
def test_native_resume_blocks_declared_policy_before_historical_restore(tmp_path, monkeypatch, declared):
    from unittest.mock import AsyncMock
    from src.backend.replay_run_service import ReplayRunService
    from test_backtest_typed_publisher import RUN
    async def run():
        service = ReplayRunService(runtime_root=tmp_path, allow_typed_backtest_resume=True)
        definition = SimpleNamespace(configuration_revision=(envelope() if declared else
            {'payload':{'strategy':{'strategy_number':1}}}))
        monkeypatch.setattr(service, '_load_typed_backtest_resume_definition', lambda _:definition)
        prepare = AsyncMock(side_effect=RuntimeError('historical restore reached'))
        monkeypatch.setattr(service, '_prepare_typed_v4_resume', prepare)
        with pytest.raises(RuntimeError, match='fresh-only' if declared else 'historical restore reached'):
            await service.resume(RUN)
        assert prepare.await_count == (0 if declared else 1)
    asyncio.run(run())

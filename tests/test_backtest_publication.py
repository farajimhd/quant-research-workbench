import asyncio
import json
import os
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from src.backend.backtest_publication import BacktestPublication, render_publication


def slow_render(packet):
    time.sleep(0.15)
    payloads = {symbol: {'run': packet['run'], 'pid': os.getpid()} for symbol in packet['symbols']}
    return {'payloads': payloads, 'wire': {k: json.dumps(v).encode() for k, v in payloads.items()}}


class PublicationTests(IsolatedAsyncioTestCase):
    async def test_historical_bootstrap_and_round_trip_are_independent_of_ui(self):
        from dataclasses import replace
        from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
        from src.trading_runtime.domain import TradingMode
        from src.trading_runtime.journal import TradingJournal
        from src.trading_runtime.ibkr_schema import OrderRequest
        from tests.test_trading_runtime import TS, quote, _NoopStrategy
        async def run(watched):
            with TemporaryDirectory() as directory:
                journal = TradingJournal(Path(directory) / 'journal.sqlite3')
                broker = SimulatedBrokerAdapter(['SIM'], SimulationConfig(liquidity_participation=1),
                    mode=TradingMode.BACKTEST, initial_time=TS)
                runtime = TradingRuntime(RunConfig(RunMode.BACKTEST, 'noop', 1, ('SIM',), TS.date(),
                    run_id='00000000-0000-0000-0000-000000000004'), broker, _NoopStrategy(), journal)
                publication = BacktestPublication() if watched else None
                reader = None
                try:
                    await runtime.initialize()
                    assert runtime.projected_snapshot().as_of == TS
                    assert (await broker.shortability(265598)).observed_at == TS
                    restored = SimulatedBrokerAdapter(['SIM'], broker.config, mode=TradingMode.BACKTEST)
                    restored.restore_checkpoint_state(broker.checkpoint_state())
                    assert restored._latest_event_time() == TS
                    for i in range(12):
                        event = replace(quote(bid=99+i, ask=100+i, sequence=i+1), ts=TS+timedelta(seconds=i))
                        await runtime.process_event(event)
                        if i in (0, 5):
                            await broker.place_orders('SIM', [OrderRequest(acctId='SIM', conid=265598,
                                cOID=f'order-{i}', ticker='AAPL', orderType='MKT', side='BUY' if i == 0 else 'SELL', quantity=10)])
                        if publication:
                            publication.publish(dict(run=dict(run_id=runtime.run_id, mode='backtest', status='running',
                                current_time=event.ts.isoformat(), updated_at=str(i)), snapshot=runtime.projected_snapshot(),
                                sequence=journal.latest_sequence(runtime.run_id), journal_path=str(journal.path),
                                assignments=(), configuration={}, automatic=False, performance_extrema=broker.performance_extrema()))
                            if reader is None:
                                reader = asyncio.create_task(publication.get('AAPL'))
                            await asyncio.sleep(0)
                    await runtime.finish()
                    result = broker.checkpoint_state()
                    assert len(result['executions']) == 2
                    assert result['positions']['SIM'][0]['quantity'] == 0
                    if reader:
                        await asyncio.wait_for(reader, 30)
                    return result
                finally:
                    if publication:
                        await publication.close()
                    journal.close()
        assert await run(False) == await run(True)

    async def test_slow_process_latest_boundary_and_cancelled_reader(self):
        publication = BacktestPublication(slow_render)
        def boundary(i, status='running'):
            return dict(run=dict(status=status, updated_at=str(i)), assignments=())
        try:
            publication.publish(boundary(0))
            reader = asyncio.create_task(publication.get('AAA'))
            await asyncio.sleep(0)
            for i in range(1, 101):
                publication.publish(boundary(i))
                await asyncio.sleep(0)
            # The engine has advanced 100 boundaries while the first render is
            # still outstanding. There is only one replacement, not 100 jobs.
            assert publication.pending['run']['updated_at'] == '100'
            assert not reader.done()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            publication.publish(boundary(101, 'completed'))
            result = await asyncio.wait_for(publication.get('AAA'), 20)
            assert result['pid'] != os.getpid()
            assert result['run']['updated_at'] == '101'
            assert json.loads(await publication.encoded('AAA')) == result
            # A terminal symbol requested during pool shutdown must not strand.
            assert (await asyncio.wait_for(publication.get('BBB'), 20))['run'] == result['run']
        finally:
            await publication.close()
        assert publication.pool is None

    async def test_real_projection_matches_canvas_and_fences_same_time_appends(self):
        from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition
        from src.trading_runtime.runtime import TradingRuntime, RunMode
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
        from src.trading_runtime.canonical_session import CanonicalBrokerSession
        from src.trading_runtime.domain import TradingMode, BrokerProvider
        from src.trading_runtime.journal import TradingJournal
        from tests.test_replay_run_service import approved_configuration
        broker = SimulatedBrokerAdapter(['SIM'], mode=TradingMode.BACKTEST)
        session = CanonicalBrokerSession(broker, mode=TradingMode.BACKTEST, provider=BrokerProvider.SIMULATED)
        await session.bootstrap()
        runtime = object.__new__(TradingRuntime)
        runtime._canonical_session = session
        runtime.broker = broker
        with TemporaryDirectory() as directory:
            controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 8, 21),
                start_time=clock_time(4), end_time=clock_time(4, 30), mode=RunMode.BACKTEST,
                tickers=('SUGP',), configuration_revision=approved_configuration()), runtime_root=Path(directory))
            controller._runtime = runtime
            controller._journal = TradingJournal(Path(directory) / 'journal.sqlite3')
            runtime.journal = controller._journal
            runtime.run_id = controller.run_id
            controller.status = 'running'
            controller.current_time = datetime(2026, 8, 21, 8, tzinfo=timezone.utc)
            def append(identity, at):
                controller._journal.append(run_id=controller.run_id, category='strategy_decision',
                    entity_type='signal', entity_id=identity, event_time=at,
                    payload=dict(ticker='SUGP', strategy_id='strategy', action='enter_long'))
            try:
                append('before', controller.current_time)
                expected = await controller.canvas_payload('SUGP')
                # Hold transport timing counters at the same boundary too.
                with patch.object(controller, 'stream_snapshot', return_value=expected['run']):
                    await controller._publish(force=True)
                packet = dict(controller._monitoring.boundary, symbols=('SUGP',))
                count = packet['sequence']
                append('same-time-later-sequence', controller.current_time)
                append('future', controller.current_time + timedelta(seconds=1))
                result = render_publication(packet)['payloads']['SUGP']
                assert result['trading'].pop('presentation_sequence') == count
                assert result == expected
                # Exercise the real subprocess with frozen canonical dataclasses.
                observed = await asyncio.wait_for(controller.canvas_payload('SUGP'), 30)
                assert observed['trading']['strategy_activity'] == expected['trading']['strategy_activity']
                assert controller._journal.latest_sequence(controller.run_id) == count + 2
                with patch.object(controller, '_capture_monitoring', side_effect=ValueError('presentation failed')):
                    await controller._publish(force=True)
                assert controller.status == 'running'
                assert controller.stream_snapshot()['monitoring']['error'] == 'presentation failed'
            finally:
                if controller._monitoring:
                    await controller._monitoring.close()
                controller._journal.close()

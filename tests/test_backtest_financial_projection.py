import asyncio
from dataclasses import replace
from datetime import timedelta, date, time
from time import perf_counter
from unittest.mock import patch

import pytest

from src.backend.canonical_trading_service import trading_state_payload
from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, RunMode as ReplayMode
from src.trading_runtime.domain import TradingMode
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from tests.test_trading_runtime import TS, quote, _NoopStrategy
from tests.test_replay_run_service import approved_configuration


def test_financial_projection_marks_without_fills_is_pure_and_matches_reconciliation(tmp_path):
    async def check():
        journal = TradingJournal(tmp_path / 'journal.sqlite3')
        broker = SimulatedBrokerAdapter(['SIM'], SimulationConfig(commission_per_share=0,
            minimum_commission=0, liquidity_participation=1), mode=TradingMode.BACKTEST, initial_time=TS)
        runtime = TradingRuntime(RunConfig(RunMode.BACKTEST, 'noop', 1, ('SIM',), TS.date()), broker, _NoopStrategy(), journal)
        try:
            await runtime.initialize()
            async def mark(price, second):
                at = TS + timedelta(seconds=second)
                await runtime.process_event(replace(quote(bid=price, ask=price, sequence=second+1), ts=at))
                return at
            await mark(100, 0)
            await broker.place_orders('SIM', [OrderRequest(acctId='SIM', conid=265598, cOID='buy', ticker='AAPL', orderType='MKT', side='BUY', quantity=10)])
            await mark(100, 1)
            old = runtime.projected_snapshot()
            frozen = None
            for price, second, unrealized in [(110,2,100), (98,3,-20), (105,4,50)]:
                at = await mark(price, second)
                state = broker.checkpoint_state()
                sequence = journal.latest_sequence(runtime.run_id)
                canonical = runtime._canonical_session.projector.snapshot()
                with patch.object(runtime._canonical_session, 'reconcile', side_effect=AssertionError('projection reconciled')):
                    projected = runtime.projected_snapshot(as_of=at)
                    payload = trading_state_payload(projected, include_strategy_activity=False, performance_extrema=broker.performance_extrema())
                assert projected.as_of == at
                assert float(payload['performance_snapshot']['unrealized_pnl']) == unrealized
                assert payload['performance_snapshot']['as_of'] == at.isoformat()
                assert broker.checkpoint_state() == state
                assert journal.latest_sequence(runtime.run_id) == sequence
                assert runtime._canonical_session.projector.snapshot() == canonical
                reference = await runtime.canonical_snapshot(as_of=at)
                expected = trading_state_payload(reference, include_strategy_activity=False, performance_extrema=broker.performance_extrema())
                assert payload['performance_snapshot'] == expected['performance_snapshot']
                assert payload['performance_journal'] == expected['performance_journal']
                assert [(p.quantity,p.market_price,p.market_value,p.unrealized_pnl) for p in projected.positions] == [(p.quantity,p.market_price,p.market_value,p.unrealized_pnl) for p in reference.positions]
                if frozen is None: frozen = projected
            assert old.as_of < at
            assert float(frozen.positions[0].unrealized_pnl) == 100
            with pytest.raises(ValueError, match='precedes'):
                runtime.projected_snapshot(as_of=TS)
            await broker.place_orders('SIM', [OrderRequest(acctId='SIM', conid=265598, cOID='sell', ticker='AAPL', orderType='MKT', side='SELL', quantity=10)])
            await mark(105,5)
            at = await mark(120,6)
            projected = runtime.projected_snapshot(as_of=at)
            assert not projected.positions and projected.as_of == at
            assert float(trading_state_payload(projected, include_strategy_activity=False)['performance_snapshot']['unrealized_pnl']) == 0
            # A full-market quote cache must not be scanned by financial publication.
            broker._quotes_by_ticker.update({f'UNHELD{i}':quote(bid=99, ask=100) for i in range(5000)})
            with patch.object(broker, '_latest_event_time', side_effect=AssertionError('scanned universe')):
                template = broker._positions['SIM'][265598]
                for count in (0, 1, 50):
                    # Seed bounded position books only for the cost probe.
                    broker._positions['SIM'] = {265598+i: replace(template, conid=265598+i,
                        ticker=f'HELD{i}', quantity=10, avg_cost=100) for i in range(count)}
                    broker._marks.update({265598+i:110 for i in range(count)})
                    started = perf_counter()
                    for _ in range(200): runtime.projected_snapshot(as_of=at)
                    print(f'financial projection ({count} positions, 5000 cached tickers): {(perf_counter()-started)*1000/200:.3f} ms/publication')
        finally:
            journal.close()
    asyncio.run(check())


def test_monitoring_does_not_split_passive_engine_batches(tmp_path):
    async def check():
        controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026,7,28), start_time=time(9,45),
            mode=ReplayMode.BACKTEST, tickers=('AAPL',), configuration_revision=approved_configuration()), runtime_root=tmp_path)
        controller._runtime = object()
        controller._journal = object()
        controller._pending_passive_market_events = [quote(bid=99, ask=100)]
        with patch.object(controller, '_capture_monitoring') as capture:
            await controller._publish(force=True)
            capture.assert_not_called()
            assert len(controller._pending_passive_market_events) == 1
            controller._pending_passive_market_events.clear()
            await controller._publish(force=True)
            capture.assert_called_once()
    asyncio.run(check())

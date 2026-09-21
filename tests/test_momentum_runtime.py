import asyncio
import pytest
from dataclasses import replace
from datetime import time

from tests.test_early_squeeze_momentum import momentum_fixture
from tests.test_replay_run_service import approved_configuration
from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, _debug_market_events
from src.trading_runtime.journal import TradingJournal


@pytest.mark.parametrize('exit_kind', ['stop', 'partial_target'])
def test_engine_portfolio_oms_broker_roundtrip_and_recorded_stop_reason(tmp_path, exit_kind):
    async def run():
        _, prepared, trade, _, _ = momentum_fixture()
        configuration = approved_configuration(assignments=[dict(
            assignment_id=prepared.assignment_id, account_key='primary', ticker=prepared.ticker,
            conid=prepared.conid, status='watching', parameters=prepared.parameters,
            permissions=dict(observe=True, enter=True, add=True, reduce=True, exit=True, reenter=True))])
        configuration['payload']['strategy']['parameters'] = prepared.parameters
        configuration['payload']['portfolio']['policies'][0]['allow_outside_rth'] = True
        configuration['payload']['oms']['outside_rth'] = True
        controller = ReplayRunController(ReplayRunDefinition(
            session_date=trade().observed_at.date(), start_time=time(4), tickers=(prepared.ticker,),
            configuration_revision=configuration, initial_cash=9000), runtime_root=tmp_path)
        controller._journal = TradingJournal(tmp_path/'journal.sqlite3')
        try:
            await controller._initialize_runtime()
            runtime = controller._runtime
            assigned = controller._strategy.assignments()[0]
            key = (assigned.account_id, assigned.ticker.upper())
            controller._strategy._assignments[key] = replace(assigned, state=prepared.state)
            observation = trade(16.02, 10.44)
            async def quote(at, bid, ask):
                event = _debug_market_events((dict(kind='quote', ticker=assigned.ticker, ts=at.isoformat(),
                    bid_price=bid, ask_price=ask, bid_size=10000, ask_size=10000),))[0]
                await runtime.process_event(event, evaluate_strategy=False)
            await quote(observation.observed_at, observation.bid, observation.ask)
            await runtime.process_account_strategy_observation(observation, assigned.account_id)
            await quote(trade(16.3).observed_at, observation.bid, observation.ask)
            positions = await runtime.broker.positions(assigned.account_id)
            assert positions and positions[0].position > 0, [r.payload for r in controller._journal.records(controller.run_id)
                if r.category == 'portfolio']
            group = next(g for g in runtime.order_manager._groups.values() if g.intent.action == 'enter_long')
            assert group.intent.profit_target_price > group.intent.metadata['momentum_fill_average']
            assert controller._strategy.assignments()[0].state['squeeze_breakout']['momentum_requests'][group.intent.intent_id]['filled']
            stop = group.intent.invalidation_price
            expected_reason = group.intent.metadata['stop_exit_reason']
            if exit_kind == 'partial_target':
                target = group.intent.profit_target_price
                event = _debug_market_events((dict(kind='quote', ticker=assigned.ticker,
                    ts=trade(16.4).observed_at.isoformat(), bid_price=target, ask_price=target+.02,
                    bid_size=1, ask_size=1),))[0]
                await runtime.process_event(event, evaluate_strategy=False)
                held = sum(p.position for p in await runtime.broker.positions(assigned.account_id))
                assert 0 < held < group.filled_quantity
                current = controller._strategy.assignments()[0]
                assert current.state['entry_acquisition_exit_latched']
                expected_reason = 'momentum_target_5x'
                assert current.state['last_exit_reason'] == expected_reason
                # Price retreats below the target. The engine must still sell
                # all remaining shares through Portfolio and OMS, not wait.
                await quote(trade(16.5).observed_at, target-.10, target-.08)
                await runtime.process_account_strategy_observation(
                    replace(trade(16.5, target-.09, held), bid=target-.10, ask=target-.08), assigned.account_id)
                await quote(trade(16.6).observed_at, target-.10, target-.08)
            else:
                event = _debug_market_events((dict(kind='trade', ticker=assigned.ticker, ts=trade(16.4).observed_at.isoformat(),
                    price=stop-.01, size=10000),))[0]
                await runtime.process_event(event, evaluate_strategy=False)
                await quote(trade(16.5).observed_at, stop-.02, stop-.01)
            positions = await runtime.broker.positions(assigned.account_id)
            assert not any(p.position for p in positions)
            if exit_kind == 'partial_target':
                await runtime.process_account_strategy_observation(trade(16.7, 10.44), assigned.account_id)
                decisions = [r.payload for r in controller._journal.records(controller.run_id)
                    if r.category == 'strategy_decision']
                assert decisions[-1]['reason'] == 'target_hit_same_1s_candle'
                assert not any(p.position for p in await runtime.broker.positions(assigned.account_id))
            executions = await runtime.broker.trades()
            assert len(executions) >= 2
            sells = [e for e in executions if str(e.side).upper() in {'SELL', 'SLD', 'S'}]
            assert sells
            assert all(e.raw['canonical_metadata'].get('exit_reason') == expected_reason for e in sells)
        finally:
            if controller._runtime:
                await controller._runtime.finish()
            controller._journal.close()
    asyncio.run(run())

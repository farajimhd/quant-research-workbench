import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from src.backend.canonical_trading_service import trading_state_payload
from src.trading_runtime.canonical_session import CanonicalBrokerSession
from src.trading_runtime.domain import BrokerProvider, TradingMode
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from tests.test_trading_runtime import quote, TS


async def performance_scenario():
    broker = SimulatedBrokerAdapter(['SIM'], SimulationConfig(commission_per_share=0,
        minimum_commission=0,liquidity_participation=1),mode=TradingMode.BACKTEST)
    session = CanonicalBrokerSession(broker,mode=TradingMode.BACKTEST,provider=BrokerProvider.SIMULATED)
    await session.bootstrap()

    async def mark(price, second):
        await broker.on_market_event(replace(quote(bid=price,ask=price,sequence=second+1),ts=TS+timedelta(seconds=second)))

    async def order(side,second):
        await broker.place_orders('SIM',[OrderRequest(acctId='SIM',conid=265598,cOID=f'{side}-{second}',
            ticker='AAPL',orderType='MKT',side=side,quantity=10)])
        await broker.match_current_orders('AAPL',TS+timedelta(seconds=second))

    async def payload():
        await session.reconcile()
        return trading_state_payload(session.projector.snapshot(),include_strategy_activity=False,
            performance_extrema=broker.performance_extrema())

    await mark(100,0)
    await order('BUY',0)
    await mark(110,1)
    opened=await payload()
    assert opened['performance_snapshot']['max_unrealized_pnl']==pytest.approx(100)
    await mark(105,2)
    assert broker.performance_extrema()['maximum_drawdown']==pytest.approx(50)
    await mark(98,3)
    assert broker.performance_extrema()['worst_unrealized']==pytest.approx(-20)
    assert broker.performance_extrema()['maximum_drawdown']==pytest.approx(120)
    await mark(100,4)
    await order('SELL',4)
    closed=await payload()
    assert closed['performance_snapshot']['unrealized_pnl']=='0'
    assert closed['performance_snapshot']['max_unrealized_pnl']==pytest.approx(100)
    assert closed['performance_snapshot']['minimum_unrealized_pnl']==pytest.approx(-20)
    assert closed['performance_snapshot']['maximum_drawdown']==pytest.approx(120)
    await order('BUY',4)
    await mark(105,5)
    await order('SELL',5)
    await mark(100,6)
    await order('BUY',6)
    await mark(98,7)
    await order('SELL',7)
    closed=await payload()
    assert float(closed['performance_journal']['summary']['gross_profit'])==50
    assert float(closed['performance_journal']['summary']['gross_loss'])==20
    saved=broker.checkpoint_state()
    restored=SimulatedBrokerAdapter(['SIM'],broker.config,mode=TradingMode.BACKTEST)
    restored.restore_checkpoint_state(saved)
    await restored.initialize()
    assert restored.performance_extrema()==broker.performance_extrema()
    await restored.on_market_event(replace(quote(bid=115,ask=115),ts=TS+timedelta(seconds=8)))
    assert restored.performance_extrema()['peak_unrealized']==100
    # Old checkpoints cannot recover a market path that they never stored.
    old=dict(saved,schema_version=3);old.pop('performance_extrema');old.pop('performance_marks')
    legacy=SimulatedBrokerAdapter(['SIM'],broker.config,mode=TradingMode.BACKTEST)
    legacy.restore_checkpoint_state(old)
    unavailable=trading_state_payload(session.projector.snapshot(),include_strategy_activity=False,
        performance_extrema=legacy.performance_extrema())
    assert unavailable['performance_snapshot']['max_unrealized_pnl'] is None
    return opened,closed


def test_run_extrema_survive_close_and_checkpoint_restore():
    asyncio.run(performance_scenario())

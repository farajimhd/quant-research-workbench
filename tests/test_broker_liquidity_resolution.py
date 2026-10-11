from datetime import timedelta
import asyncio

import pytest

from src.trading_runtime.runtime import RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.ibkr_schema import OrderRequest
from tests.test_simulated_broker_liquidity_bar import START, bar


def second_bar(at):
    row = bar(at)
    row.update(resolution_ms=1000, bucket_index=(row["bucket_index"] + 1) // 10 - 1)
    return row


@pytest.mark.parametrize("resolution", [0, 1, 500, 1000.0, True])
def test_resolution_is_explicit_and_bounded(resolution):
    with pytest.raises(ValueError, match="resolution"):
        SimulationConfig(liquidity_resolution_ms=resolution)


def test_second_bar_fills_real_order_and_charges_commission():
    asyncio.run(second_bar_fills_real_order_and_charges_commission())


async def second_bar_fills_real_order_and_charges_commission():
    broker = SimulatedBrokerAdapter(["TEST"], SimulationConfig(liquidity_resolution_ms=1000,
        marketable_liquidity_participation=1), mode=RunMode.BACKTEST,
        initial_time=START, fixed_bar_mode=True)
    await broker.initialize()
    await broker.place_orders("TEST", [OrderRequest(acctId="TEST", conid=1,
        cOID="one-second", ticker="AAPL", orderType="LMT", side="BUY", quantity=10, price=10)])
    at = START + timedelta(seconds=1)
    fills = await broker.on_liquidity_bar(second_bar(at), at=at)
    assert len(fills) == 1
    assert fills[0].size == 10
    assert fills[0].commission == 1
    assert broker.position_quantity("TEST", 1, "AAPL") == 10


def test_second_bar_does_not_fill_order_placed_inside_bucket():
    asyncio.run(second_bar_does_not_fill_order_placed_inside_bucket())


async def second_bar_does_not_fill_order_placed_inside_bucket():
    broker = SimulatedBrokerAdapter(["TEST"], SimulationConfig(liquidity_resolution_ms=1000),
        mode=RunMode.BACKTEST, initial_time=START + timedelta(milliseconds=100), fixed_bar_mode=True)
    await broker.initialize()
    await broker.place_orders("TEST", [OrderRequest(acctId="TEST", conid=1,
        cOID="inside-second", ticker="AAPL", orderType="MKT", side="BUY", quantity=10)])
    at = START + timedelta(seconds=1)
    assert await broker.on_liquidity_bar(second_bar(at), at=at) == []
    assert broker.position_quantity("TEST", 1, "AAPL") == 0


def test_resolution_and_future_event_rejected_before_mutation():
    broker = SimulatedBrokerAdapter(["TEST"], SimulationConfig(liquidity_resolution_ms=1000))
    at = START + timedelta(seconds=1)
    with pytest.raises(ValueError, match="configured"):
        broker.validate_liquidity_bar(bar(at), at=at)
    row = second_bar(at)
    row["last_event_us"] = int(at.timestamp() * 1_000_000)
    with pytest.raises(ValueError, match="completed bucket"):
        broker.validate_liquidity_bar(row, at=at)
    assert not broker._bar_boundaries


def test_one_second_activation_delay_cannot_spend_pre_activation_liquidity():
    async def exercise():
        broker = SimulatedBrokerAdapter(["TEST"], SimulationConfig(liquidity_resolution_ms=1000,
            new_order_activation_delay_ms=500, marketable_liquidity_participation=1),
            mode=RunMode.BACKTEST, initial_time=START, fixed_bar_mode=True)
        await broker.initialize()
        await broker.place_orders("TEST", [OrderRequest(acctId="TEST", conid=1,
            cOID="delayed-second", ticker="AAPL", orderType="MKT", side="BUY", quantity=10)])
        first = START + timedelta(seconds=1)
        assert await broker.on_liquidity_bar(second_bar(first), at=first) == []
        second = START + timedelta(seconds=2)
        assert len(await broker.on_liquidity_bar(second_bar(second), at=second)) == 1
    asyncio.run(exercise())

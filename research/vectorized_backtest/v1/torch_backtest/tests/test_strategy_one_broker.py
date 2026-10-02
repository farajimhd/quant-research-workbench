"""Real app broker oracles for the device-side fill/fee/order contracts."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import torch

from research.vectorized_backtest.v1.torch_backtest.strategy_one_broker import (
    Book,
    match_listing,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.simulated_broker import (
    SimulatedBrokerAdapter,
    SimulationConfig,
)

DEVICES = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])
START = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)


def scalar(value, device, dtype=torch.float64):
    return torch.tensor([value], device=device, dtype=dtype)


def source_row(at, *, bid=9.99, ask=10, size=80, low=9.98):
    return {
        "ticker": "TEST",
        "resolution_ms": 100,
        "bucket_index": 144000 + round((at - START).total_seconds() * 10) - 1,
        "event_count": 3,
        "last_event_us": round(at.timestamp() * 1_000_000) - 1,
        "quote_valid": 1,
        "quote_timestamp_us": round(at.timestamp() * 1_000_000) - 10000,
        "bid_int": round(bid * 10000),
        "ask_int": round(ask * 10000),
        "bid_size": size,
        "ask_size": size,
        "price_valid": 1,
        "extremes_valid": 1,
        "close_int": round(ask * 10000),
        "low_int": round(low * 10000),
        "high_int": round(ask * 10000),
        "execution_volume": 150,
        "execution_price_levels": (
            {"price_int": 99800, "volume": 120},
            {"price_int": 100000, "volume": 30},
        ),
    }


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("variant", ["partial", "passive", "stop", "grid_stop"])
@pytest.mark.parametrize("marketable_participation", [0.25, 1.0])
@pytest.mark.parametrize("stop_slippage", [0.0, 5.0])
def test_native_broker_fill_and_commission_parity(
    device, variant, marketable_participation, stop_slippage
):
    asyncio.run(
        _check_native_broker(device, variant, marketable_participation, stop_slippage)
    )


async def _check_native_broker(
    device, variant, marketable_participation, stop_slippage
):
    broker = SimulatedBrokerAdapter(
        ["TEST"],
        SimulationConfig(
            initial_cash=10000,
            marketable_liquidity_participation=marketable_participation,
            market_slippage_bps=stop_slippage,
        ),
        mode=RunMode.BACKTEST,
        initial_time=START,
        fixed_bar_mode=True,
    )
    await broker.initialize()
    quantity = 10 if variant in ("stop", "grid_stop") else 100
    stop = 3.96 if variant == "grid_stop" else 9
    limit = 9.99 if variant == "passive" else 10
    orders = [
        OrderRequest(
            acctId="TEST",
            conid=1,
            cOID="entry",
            ticker="TEST",
            orderType="LMT",
            side="BUY",
            quantity=quantity,
            price=limit,
            tif="DAY",
            outsideRTH=True,
        ),
        OrderRequest(
            acctId="TEST",
            conid=1,
            cOID="target",
            parentId="entry",
            ticker="TEST",
            orderType="LMT",
            side="SELL",
            quantity=quantity,
            price=12,
            tif="DAY",
            outsideRTH=True,
            isSingleGroup=True,
        ),
        OrderRequest(
            acctId="TEST",
            conid=1,
            cOID="stop",
            parentId="entry",
            ticker="TEST",
            orderType="STP",
            side="SELL",
            quantity=quantity,
            auxPrice=stop,
            tif="DAY",
            outsideRTH=True,
            isSingleGroup=True,
        ),
    ]
    await broker.place_orders("TEST", orders)
    book = Book.empty(1, device)
    book.remaining[0, :3] = quantity
    book.price[0, :5] = torch.tensor(
        [limit, 12, stop, 12, stop], dtype=torch.float64, device=device
    )
    book.active[0, 0] = True
    book.reference[0, 0] = limit
    book.reserved_risk_per_share[0, 0] = limit - stop
    kernel = (
        torch.compile(match_listing, fullgraph=True)
        if device == "cuda" and variant == "grid_stop"
        else match_listing
    )
    cash, held, avg, risk, allocated = [
        scalar(value, device) for value in (10000, 0, 0, 0, 0)
    ]
    for tick in range(1, 7):
        at = START + timedelta(milliseconds=tick * 100)
        bid, ask, low = (
            (3.97, 3.98, 3.96)
            if variant == "grid_stop" and tick > 1
            else (
                (8.9, 9, 8.8)
                if variant == "stop" and tick > 1
                else (9.99, 10.01 if variant == "passive" else 10, 9.98)
            )
        )
        row = source_row(
            at, bid=bid, ask=ask, low=low, size=40 if variant == "stop" else 80
        )
        expected = await broker.on_liquidity_bar(row, at=at)
        x = {
            "boundary_ms": scalar(tick * 100, device, torch.int64),
            "interval_start_ms": scalar((tick - 1) * 100, device, torch.int64),
            "present": scalar(True, device, torch.bool),
            "quote_valid": scalar(True, device, torch.bool),
            "bid": scalar(bid, device),
            "ask": scalar(ask, device),
            "low": scalar(row["low_int"], device) / 10000,
            "bid_size": scalar(row["bid_size"], device),
            "ask_size": scalar(row["ask_size"], device),
            "price_int": torch.tensor(
                [[99800, 100000]], device=device, dtype=torch.float64
            ),
            "price_volume": torch.tensor(
                [[120, 30]], device=device, dtype=torch.float64
            ),
        }
        cash, held, avg, risk, allocated, _, _, events = kernel(
            book,
            x,
            cash,
            held,
            avg,
            risk,
            allocated,
            marketable_participation,
            0.25,
            stop_slippage,
        )
        observed = [
            (float(event[0]), float(event[1]), float(event[2]))
            for event in events[0].cpu()
            if event[0] != 0
        ]
        oracle = [
            (fill.size if fill.side == "B" else -fill.size, fill.price, fill.commission)
            for fill in expected
        ]
        assert len(observed) == len(oracle)
        for actual_fill, expected_fill in zip(observed, oracle):
            assert actual_fill == pytest.approx(expected_fill, rel=0, abs=1e-10)
        positions = await broker.positions("TEST")
        assert held.item() == (positions[0].position if positions else 0)
        assert cash.item() == pytest.approx(
            (await broker.account_summary("TEST")).totalcashvalue
        )

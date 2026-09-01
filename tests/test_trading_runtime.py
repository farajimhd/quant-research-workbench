from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.backend.real_live_trading_service import ibkr_order_payload
from src.backend.qmd_gateway_client import ENRICHED_QMD_TIMEFRAMES, normalize_qmd_family_bar_snapshot
from src.backend.trading_runtime_service import (
    _bounded_historical_chart_window,
    historical_bar_history_before,
    strategy_activity_payload,
)
from src.market_engine.events import QuoteEvent, TradeEvent
from src.market_engine.historical_source import _validate_health, event_from_qmd_payload
from src.trading_runtime.ibkr_schema import OrderRequest, OrderStatus
from src.trading_runtime.clickhouse import TRADING_TABLE_DDL, _specialized_rows
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.orchestrator import historical_run_window
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.signals import MarketSignal, StrategyEvaluation, StrategyIntent, StrategySignal
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig


TS = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


def quote(*, bid: float, ask: float, bid_size: float = 100, ask_size: float = 100) -> QuoteEvent:
    return QuoteEvent(
        ask_exchange=11,
        ask_price=ask,
        ask_size=ask_size,
        bid_exchange=12,
        bid_price=bid,
        bid_size=bid_size,
        conditions=(),
        indicators=(),
        ingest_ts=TS,
        raw={"conid": 265598},
        sequence=1,
        source="test",
        tape=3,
        ticker="AAPL",
        ts=TS,
    )


def trade(*, price: float, size: float = 100) -> TradeEvent:
    return TradeEvent(
        conditions=(),
        event_id=f"trade-{price}-{size}",
        exchange=11,
        ingest_ts=TS,
        participant_ts=TS,
        price=price,
        raw={"conid": 265598},
        sequence=2,
        size=size,
        source="test",
        tape=3,
        ticker="AAPL",
        ts=TS,
    )


class SimulatedBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.broker = SimulatedBrokerAdapter(
            ["DU123"],
            SimulationConfig(initial_cash=10_000, commission_per_share=0.0, minimum_commission=0.0, liquidity_participation=0.5),
        )
        await self.broker.initialize()
        await self.broker.on_market_event(quote(bid=99, ask=100))

    async def test_partial_market_fills_use_quote_liquidity_and_ibkr_statuses(self) -> None:
        order = OrderRequest(acctId="DU123", conid=265598, cOID="parent", ticker="AAPL", orderType="MKT", side="BUY", quantity=100)
        response = await self.broker.place_orders("DU123", [order])
        self.assertEqual(response[0]["order_status"], "Submitted")

        fills = await self.broker.on_market_event(quote(bid=99, ask=100, ask_size=80))
        self.assertEqual(fills[0].size, 40)
        orders = await self.broker.live_orders()
        self.assertEqual(orders[0].order_status, OrderStatus.SUBMITTED)
        self.assertEqual(orders[0].remainingQuantity, 60)

        await self.broker.on_market_event(quote(bid=100, ask=101, ask_size=120))
        orders = await self.broker.live_orders()
        self.assertEqual(orders[0].order_status, OrderStatus.FILLED)
        self.assertEqual(orders[0].avgPrice, 100.6)
        positions = await self.broker.positions("DU123")
        self.assertEqual(positions[0].position, 100)
        self.assertEqual(positions[0].avgCost, 100.6)

    async def test_marketable_sweep_uses_full_causal_displayed_liquidity(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-SWEEP"],
            SimulationConfig(
                initial_cash=20_000,
                commission_per_share=0.0,
                minimum_commission=0.0,
                liquidity_participation=0.25,
                marketable_liquidity_participation=1.0,
            ),
        )
        await broker.initialize()
        await broker.on_market_event(quote(bid=99, ask=100, ask_size=80))
        order = OrderRequest(
            acctId="DU-SWEEP",
            conid=265598,
            cOID="marketable-sweep",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=100,
            price=100,
        )
        await broker.place_orders("DU-SWEEP", [order])

        fills = await broker.on_market_event(
            quote(bid=99, ask=100, ask_size=80)
        )

        self.assertEqual(fills[0].size, 80)
        live = await broker.live_orders()
        self.assertEqual(live[0].remainingQuantity, 20)

    async def test_marketable_limit_remains_aggressive_on_causal_trade_events(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-SWEEP"],
            SimulationConfig(
                initial_cash=20_000,
                commission_per_share=0.0,
                minimum_commission=0.0,
                liquidity_participation=0.25,
                marketable_liquidity_participation=1.0,
            ),
        )
        await broker.initialize()
        await broker.on_market_event(quote(bid=99, ask=100, ask_size=80))
        await broker.place_orders("DU-SWEEP", [OrderRequest(
            acctId="DU-SWEEP",
            conid=265598,
            cOID="marketable-trade-continuation",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=100,
            price=100,
        )])
        await broker.on_market_event(quote(bid=99, ask=100, ask_size=80))

        fills = await broker.on_market_event(trade(price=100, size=20))

        self.assertEqual(fills[0].price, 100)
        self.assertEqual(fills[0].size, 20)
        live = await broker.live_orders()
        self.assertEqual(live[0].remainingQuantity, 0)

    async def test_order_submission_time_uses_ticker_quote_without_conid(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-TIME"],
            SimulationConfig(
                initial_cash=20_000,
                commission_per_share=0.0,
                minimum_commission=0.0,
            ),
        )
        await broker.initialize()
        broker.observe_market_event(replace(quote(bid=99, ask=100), raw={}))
        await broker.place_orders("DU-TIME", [OrderRequest(
            acctId="DU-TIME",
            conid=265598,
            cOID="causal-submission-time",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=10,
            price=100,
        )])

        live = await broker.live_orders()

        self.assertEqual(live[0].lastExecutionTime, TS)

    async def test_resting_sell_limit_fills_from_trade_through_with_passive_participation(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-TARGET"],
            SimulationConfig(
                initial_cash=20_000,
                commission_per_share=0.0,
                minimum_commission=0.0,
                liquidity_participation=0.25,
                marketable_liquidity_participation=1.0,
            ),
        )
        await broker.initialize()
        await broker.on_market_event(quote(bid=99, ask=100, bid_size=100, ask_size=100))
        await broker.place_orders("DU-TARGET", [OrderRequest(
            acctId="DU-TARGET",
            conid=265598,
            cOID="entry-for-target",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=100,
            price=100,
        )])
        await broker.on_market_event(quote(bid=99, ask=100, bid_size=100, ask_size=100))
        await broker.place_orders("DU-TARGET", [OrderRequest(
            acctId="DU-TARGET",
            conid=265598,
            cOID="resting-target",
            ticker="AAPL",
            orderType="LMT",
            side="SELL",
            quantity=100,
            price=105,
        )])

        fills = await broker.on_market_event(trade(price=105.25, size=200))

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, 105)
        self.assertEqual(fills[0].size, 50)
        live = await broker.live_orders()
        target = next(item for item in live if item.cOID == "resting-target")
        self.assertEqual(target.remainingQuantity, 50)

    async def test_crossed_target_uses_aggressive_trade_participation(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-TARGET"],
            SimulationConfig(
                initial_cash=20_000,
                commission_per_share=0.0,
                minimum_commission=0.0,
                liquidity_participation=0.25,
                marketable_liquidity_participation=1.0,
            ),
        )
        await broker.initialize()
        await broker.on_market_event(
            quote(bid=99, ask=100, bid_size=100, ask_size=100)
        )
        await broker.place_orders("DU-TARGET", [OrderRequest(
            acctId="DU-TARGET",
            conid=265598,
            cOID="entry-for-crossed-target",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=100,
            price=100,
        )])
        await broker.on_market_event(
            quote(bid=99, ask=100, bid_size=100, ask_size=100)
        )
        await broker.place_orders("DU-TARGET", [OrderRequest(
            acctId="DU-TARGET",
            conid=265598,
            cOID="crossed-target",
            ticker="AAPL",
            orderType="LMT",
            side="SELL",
            quantity=100,
            price=105,
        )])
        broker.observe_market_event(
            quote(bid=105.10, ask=105.20, bid_size=100, ask_size=100)
        )

        fills = await broker.on_market_event(trade(price=105.25, size=100))

        self.assertEqual(fills[0].price, 105.10)
        self.assertEqual(fills[0].size, 100)

    async def test_resting_sell_limit_does_not_fill_from_trade_below_limit(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-TARGET"],
            SimulationConfig(
                initial_cash=20_000,
                commission_per_share=0.0,
                minimum_commission=0.0,
                liquidity_participation=0.25,
                marketable_liquidity_participation=1.0,
            ),
        )
        await broker.initialize()
        await broker.on_market_event(quote(bid=99, ask=100, bid_size=100, ask_size=100))
        await broker.place_orders("DU-TARGET", [OrderRequest(
            acctId="DU-TARGET",
            conid=265598,
            cOID="entry-for-target",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=100,
            price=100,
        )])
        await broker.on_market_event(quote(bid=99, ask=100, bid_size=100, ask_size=100))
        await broker.place_orders("DU-TARGET", [OrderRequest(
            acctId="DU-TARGET",
            conid=265598,
            cOID="resting-target",
            ticker="AAPL",
            orderType="LMT",
            side="SELL",
            quantity=100,
            price=105,
        )])

        fills = await broker.on_market_event(trade(price=104.99, size=1_000))

        self.assertEqual(fills, [])

    async def test_stock_participation_fill_is_whole_shares(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-WHOLE"],
            SimulationConfig(
                initial_cash=20_000,
                commission_per_share=0,
                minimum_commission=0,
                liquidity_participation=0.5,
            ),
        )
        await broker.initialize()
        await broker.on_market_event(quote(bid=99, ask=100))
        order = OrderRequest(
            acctId="DU-WHOLE",
            conid=265598,
            cOID="whole-share-parent",
            ticker="AAPL",
            orderType="MKT",
            side="BUY",
            quantity=200,
        )
        await broker.place_orders("DU-WHOLE", [order])

        fills = await broker.on_market_event(
            quote(bid=99, ask=100, ask_size=287.02)
        )

        self.assertEqual(fills[0].size, 143)
        self.assertTrue(float(fills[0].size).is_integer())

    async def test_minimum_commission_is_cumulative_per_order_not_per_partial_fill(self) -> None:
        broker = SimulatedBrokerAdapter(
            ["DU-COMMISSION"],
            SimulationConfig(
                initial_cash=10_000,
                commission_per_share=0.005,
                minimum_commission=1.0,
                liquidity_participation=0.5,
            ),
        )
        await broker.initialize()
        await broker.on_market_event(quote(bid=99, ask=100, ask_size=4))
        await broker.place_orders("DU-COMMISSION", [OrderRequest(
            acctId="DU-COMMISSION", conid=265598, cOID="commission-order",
            ticker="AAPL", orderType="LMT", side="BUY", quantity=10, price=100,
        )])

        fills = []
        for _ in range(5):
            fills.extend(
                await broker.on_market_event(quote(bid=99, ask=100, ask_size=4))
            )

        self.assertEqual(sum(fill.size for fill in fills), 10)
        self.assertEqual(sum(fill.commission for fill in fills), 1.0)

    async def test_partially_filled_sell_modification_checks_remaining_quantity(self) -> None:
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="entry", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=10, price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=20, ask_size=20)
        )
        stop = OrderRequest(
            acctId="DU123", conid=265598, cOID="stop", ticker="AAPL",
            orderType="STP", side="SELL", quantity=10, auxPrice=100,
        )
        response = await self.broker.place_orders("DU123", [stop])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=10, ask_size=10)
        )

        modified = await self.broker.modify_order(
            "DU123",
            response[0]["order_id"],
            replace(stop, auxPrice=98),
        )

        self.assertEqual(modified[0]["order_status"], "Submitted")

    async def test_partially_filled_whole_account_buy_can_reprice_remaining_quantity(self) -> None:
        entry = OrderRequest(
            acctId="DU123",
            conid=265598,
            cOID="whole-account-entry",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=99,
            price=100,
        )
        response = await self.broker.place_orders("DU123", [entry])
        fills = await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=10, ask_size=10)
        )
        self.assertEqual(sum(fill.size for fill in fills), 5)

        modified = await self.broker.modify_order(
            "DU123",
            response[0]["order_id"],
            replace(entry, price=101),
        )

        self.assertEqual(modified[0]["order_status"], "Submitted")
        orders = await self.broker.live_orders()
        self.assertEqual(orders[0].remainingQuantity, 94)
        self.assertEqual(orders[0].price, 101)

    async def test_partially_filled_buy_reprice_still_enforces_remaining_cash(self) -> None:
        entry = OrderRequest(
            acctId="DU123",
            conid=265598,
            cOID="bounded-reprice-entry",
            ticker="AAPL",
            orderType="LMT",
            side="BUY",
            quantity=100,
            price=100,
        )
        response = await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=10, ask_size=10)
        )

        with self.assertRaisesRegex(ValueError, "Order exceeds available cash"):
            await self.broker.modify_order(
                "DU123",
                response[0]["order_id"],
                replace(entry, price=106),
            )

    async def test_sell_capacity_tolerates_only_floating_point_roundoff(self) -> None:
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="fractional-entry", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=0.3, price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=1, ask_size=1)
        )

        first = OrderRequest(
            acctId="DU123", conid=265598, cOID="fractional-stop-1", ticker="AAPL",
            orderType="STP", side="SELL", quantity=0.1, auxPrice=95,
        )
        second = OrderRequest(
            acctId="DU123", conid=265598, cOID="fractional-stop-2", ticker="AAPL",
            orderType="STP", side="SELL", quantity=0.2, auxPrice=94,
        )
        await self.broker.place_orders("DU123", [first])
        response = await self.broker.place_orders("DU123", [second])

        self.assertEqual(response[0]["order_status"], "Submitted")
        with self.assertRaisesRegex(ValueError, "unconfigured short"):
            await self.broker.place_orders("DU123", [
                OrderRequest(
                    acctId="DU123", conid=265598, cOID="fractional-excess", ticker="AAPL",
                    orderType="STP", side="SELL", quantity=0.0001, auxPrice=93,
                )
            ])

    async def test_oca_alternatives_share_capacity_with_residual_backstop(self) -> None:
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="oca-residual-entry", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=10.000001, price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=100, ask_size=100)
        )
        alternatives = [
            OrderRequest(
                acctId="DU123", conid=265598, cOID=f"oca-alternative-{index}",
                ticker="AAPL", orderType="STP", side="SELL", quantity=10,
                auxPrice=95 - index, isSingleGroup=True,
            )
            for index in range(3)
        ]
        await self.broker.place_orders("DU123", alternatives)

        response = await self.broker.place_orders("DU123", [OrderRequest(
            acctId="DU123", conid=265598, cOID="residual-backstop", ticker="AAPL",
            orderType="STP", side="SELL", quantity=0.000001, auxPrice=91,
        )])

        self.assertEqual(response[0]["order_status"], "Submitted")
        with self.assertRaisesRegex(ValueError, "unconfigured short"):
            await self.broker.place_orders("DU123", [OrderRequest(
                acctId="DU123", conid=265598, cOID="excess-after-residual", ticker="AAPL",
                orderType="STP", side="SELL", quantity=0.0001, auxPrice=90,
            )])

    async def test_bracket_children_activate_and_oca_sibling_cancels(self) -> None:
        orders = [
            OrderRequest(acctId="DU123", conid=265598, cOID="entry", ticker="AAPL", orderType="LMT", side="BUY", quantity=10, price=100),
            OrderRequest(acctId="DU123", conid=265598, cOID="target", parentId="entry", ticker="AAPL", orderType="LMT", side="SELL", quantity=10, price=105, isSingleGroup=True),
            OrderRequest(acctId="DU123", conid=265598, cOID="stop", parentId="entry", ticker="AAPL", orderType="STP", side="SELL", quantity=10, auxPrice=95, isSingleGroup=True),
        ]
        await self.broker.place_orders("DU123", orders)
        snapshots = await self.broker.live_orders()
        self.assertEqual([row.order_status for row in snapshots], [OrderStatus.SUBMITTED, OrderStatus.INACTIVE, OrderStatus.INACTIVE])

        await self.broker.on_market_event(quote(bid=99, ask=100, ask_size=20))
        snapshots = await self.broker.live_orders()
        self.assertEqual([row.order_status for row in snapshots], [OrderStatus.FILLED, OrderStatus.SUBMITTED, OrderStatus.SUBMITTED])

        await self.broker.on_market_event(quote(bid=106, ask=107, bid_size=20))
        snapshots = await self.broker.live_orders()
        self.assertEqual(snapshots[1].order_status, OrderStatus.FILLED)
        self.assertEqual(snapshots[2].order_status, OrderStatus.CANCELLED)
        self.assertEqual((await self.broker.positions("DU123")), [])

    async def test_standalone_exit_oca_cancellation_applies_within_same_event(self) -> None:
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="entry", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=10, price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(quote(bid=99, ask=100, ask_size=20))
        exits = [
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-limit", ticker="AAPL",
                orderType="LMT", side="SELL", quantity=10, price=98,
                isSingleGroup=True,
            ),
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-stop", ticker="AAPL",
                orderType="STP", side="SELL", quantity=10, auxPrice=100,
                isSingleGroup=True,
            ),
        ]
        await self.broker.place_orders("DU123", exits)

        fills = await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=20, ask_size=20)
        )

        self.assertEqual(len(fills), 1)
        snapshots = await self.broker.live_orders()
        self.assertEqual(snapshots[-2].order_status, OrderStatus.FILLED)
        self.assertEqual(snapshots[-1].order_status, OrderStatus.CANCELLED)
        self.assertEqual(await self.broker.positions("DU123"), [])

    async def test_partial_oca_fills_reduce_sibling_capacity_without_overfill(self) -> None:
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="entry-large", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=100, price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=200, ask_size=200)
        )
        exits = [
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-limit-large", ticker="AAPL",
                orderType="LMT", side="SELL", quantity=100, price=98,
                isSingleGroup=True,
            ),
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-stop-large", ticker="AAPL",
                orderType="STP", side="SELL", quantity=100, auxPrice=100,
                isSingleGroup=True,
            ),
        ]
        await self.broker.place_orders("DU123", exits)

        first = await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=80, ask_size=80)
        )
        second = await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=80, ask_size=80)
        )

        sold = sum(fill.size for fill in [*first, *second] if fill.side == "S")
        self.assertEqual(sold, 100)
        self.assertEqual(await self.broker.positions("DU123"), [])

    async def test_partial_bracket_fills_share_capacity_without_overfill(self) -> None:
        orders = [
            OrderRequest(
                acctId="DU123", conid=265598, cOID="entry-partial-bracket",
                ticker="AAPL", orderType="LMT", side="BUY", quantity=100,
                price=100,
            ),
            OrderRequest(
                acctId="DU123", conid=265598, cOID="target-partial-bracket",
                parentId="entry-partial-bracket", ticker="AAPL",
                orderType="LMT", side="SELL", quantity=100, price=98,
                isSingleGroup=True,
            ),
            OrderRequest(
                acctId="DU123", conid=265598, cOID="stop-partial-bracket",
                parentId="entry-partial-bracket", ticker="AAPL",
                orderType="STP", side="SELL", quantity=100, auxPrice=100,
                isSingleGroup=True,
            ),
        ]
        await self.broker.place_orders("DU123", orders)
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=200, ask_size=200)
        )

        first = await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=80, ask_size=80)
        )
        second = await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=80, ask_size=80)
        )

        sold = sum(fill.size for fill in [*first, *second] if fill.side == "S")
        self.assertEqual(sold, 100)
        self.assertEqual(await self.broker.positions("DU123"), [])
        snapshots = await self.broker.live_orders()
        self.assertEqual(snapshots[1].filledQuantity + snapshots[2].filledQuantity, 100)
        self.assertTrue(
            all(
                row.order_status in {OrderStatus.FILLED, OrderStatus.CANCELLED}
                for row in snapshots[1:]
            )
        )

    async def test_execution_clamps_stale_sell_capacity_to_long_position(self) -> None:
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="entry-stale-exit",
            ticker="AAPL", orderType="LMT", side="BUY", quantity=100,
            price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=200, ask_size=200)
        )
        exit_order = OrderRequest(
            acctId="DU123", conid=265598, cOID="stale-exit",
            ticker="AAPL", orderType="LMT", side="SELL", quantity=100,
            price=98,
        )
        await self.broker.place_orders("DU123", [exit_order])

        # Emulate a stale restored/reconciled request whose requested size no
        # longer matches the broker-held position. The execution boundary must
        # remain authoritative even when upstream state is temporarily stale.
        self.broker._orders["2"].request = replace(
            self.broker._orders["2"].request,
            quantity=175,
        )
        fills = await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=400, ask_size=400)
        )

        self.assertEqual(sum(fill.size for fill in fills), 100)
        self.assertEqual(await self.broker.positions("DU123"), [])
        snapshot = (await self.broker.live_orders())[-1]
        self.assertEqual(snapshot.totalSize, 100)
        self.assertEqual(snapshot.remainingQuantity, 0)

    async def test_full_exit_oca_can_atomically_replace_same_strategy_protection(self) -> None:
        strategy_raw = {
            "canonical_strategy_id": "momentum",
            "canonical_strategy_revision": 5,
        }
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="entry", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=10, price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(quote(bid=99, ask=100, ask_size=20))
        protection = OrderRequest(
            acctId="DU123", conid=265598, cOID="repair-stop", ticker="AAPL",
            orderType="STP", side="SELL", quantity=10, auxPrice=95,
            raw={
                **strategy_raw,
                "canonical_metadata": {"action": "enter_long"},
            },
        )
        await self.broker.place_orders("DU123", [protection])
        exits = [
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-limit", ticker="AAPL",
                orderType="LMT", side="SELL", quantity=10, price=101,
                isSingleGroup=True,
                raw={**strategy_raw, "canonical_metadata": {"action": "exit"}},
            ),
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-stop", ticker="AAPL",
                orderType="STP", side="SELL", quantity=10, auxPrice=98,
                isSingleGroup=True,
                raw={**strategy_raw, "canonical_metadata": {"action": "exit"}},
            ),
        ]

        response = await self.broker.place_orders("DU123", exits)

        self.assertEqual([row["order_status"] for row in response], ["Submitted", "Submitted"])

    async def test_full_exit_oca_cannot_replace_unrelated_strategy_sell(self) -> None:
        entry = OrderRequest(
            acctId="DU123", conid=265598, cOID="entry", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=10, price=100,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(quote(bid=99, ask=100, ask_size=20))
        await self.broker.place_orders("DU123", [OrderRequest(
            acctId="DU123", conid=265598, cOID="other-stop", ticker="AAPL",
            orderType="STP", side="SELL", quantity=10, auxPrice=95,
            raw={
                "canonical_strategy_id": "other",
                "canonical_strategy_revision": 1,
                "canonical_metadata": {"action": "enter_long"},
            },
        )])
        exits = [
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-limit", ticker="AAPL",
                orderType="LMT", side="SELL", quantity=10, price=101,
                isSingleGroup=True,
                raw={
                    "canonical_strategy_id": "momentum",
                    "canonical_strategy_revision": 5,
                    "canonical_metadata": {"action": "exit"},
                },
            ),
            OrderRequest(
                acctId="DU123", conid=265598, cOID="exit-stop", ticker="AAPL",
                orderType="STP", side="SELL", quantity=10, auxPrice=98,
                isSingleGroup=True,
                raw={
                    "canonical_strategy_id": "momentum",
                    "canonical_strategy_revision": 5,
                    "canonical_metadata": {"action": "exit"},
                },
            ),
        ]

        with self.assertRaisesRegex(ValueError, "unconfigured short"):
            await self.broker.place_orders("DU123", exits)

    async def test_newly_activated_child_cannot_fill_on_parent_market_event(self) -> None:
        orders = [
            OrderRequest(acctId="DU123", conid=265598, cOID="entry", ticker="AAPL", orderType="LMT", side="BUY", quantity=10, price=100),
            OrderRequest(acctId="DU123", conid=265598, cOID="stop", parentId="entry", ticker="AAPL", orderType="STP", side="SELL", quantity=10, auxPrice=95, isSingleGroup=True),
        ]
        await self.broker.place_orders("DU123", orders)

        parent_event = await self.broker.on_market_event(
            quote(bid=94, ask=100, bid_size=20, ask_size=20)
        )

        self.assertEqual([fill.side for fill in parent_event], ["B"])
        snapshots = await self.broker.live_orders()
        self.assertEqual(snapshots[1].order_status, OrderStatus.SUBMITTED)
        stop_event = await self.broker.on_market_event(
            quote(bid=94, ask=95, bid_size=20, ask_size=20)
        )
        self.assertEqual([fill.side for fill in stop_event], ["S"])
        self.assertEqual((await self.broker.positions("DU123")), [])

    async def test_partial_bracket_fill_allows_active_backstop_while_children_inactive(self) -> None:
        orders = [
            OrderRequest(acctId="DU123", conid=265598, cOID="entry", ticker="AAPL", orderType="LMT", side="BUY", quantity=10, price=100),
            OrderRequest(acctId="DU123", conid=265598, cOID="target", parentId="entry", ticker="AAPL", orderType="LMT", side="SELL", quantity=10, price=105, isSingleGroup=True),
            OrderRequest(acctId="DU123", conid=265598, cOID="stop", parentId="entry", ticker="AAPL", orderType="STP", side="SELL", quantity=10, auxPrice=95, isSingleGroup=True),
        ]
        bracket_response = await self.broker.place_orders("DU123", orders)
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=10, ask_size=10)
        )

        response = await self.broker.place_orders("DU123", [
            OrderRequest(
                acctId="DU123",
                conid=265598,
                cOID="partial-fill-backstop",
                ticker="AAPL",
                orderType="STP",
                side="SELL",
                quantity=5,
                auxPrice=95,
            )
        ])

        self.assertEqual(response[0]["order_status"], "Submitted")
        inactive_child = OrderRequest(
            acctId="DU123",
            conid=265598,
            cOID="stop",
            parentId="entry",
            ticker="AAPL",
            orderType="STP",
            side="SELL",
            quantity=10,
            auxPrice=96,
            isSingleGroup=True,
        )
        child_modified = await self.broker.modify_order(
            "DU123", bracket_response[2]["order_id"], inactive_child
        )
        self.assertEqual(child_modified[0]["order_status"], "Inactive")
        with self.assertRaisesRegex(ValueError, "exceeds its parent"):
            await self.broker.modify_order(
                "DU123",
                bracket_response[2]["order_id"],
                replace(inactive_child, quantity=11),
            )
        replacement = OrderRequest(
            acctId="DU123",
            conid=265598,
            cOID="partial-fill-backstop",
            ticker="AAPL",
            orderType="STP",
            side="SELL",
            quantity=5,
            auxPrice=96,
        )
        modified = await self.broker.modify_order(
            "DU123", response[0]["order_id"], replacement
        )
        self.assertEqual(modified[0]["order_status"], "Submitted")
        with self.assertRaisesRegex(ValueError, "unconfigured short"):
            await self.broker.place_orders("DU123", [
                OrderRequest(
                    acctId="DU123",
                    conid=265598,
                    cOID="excess-sell",
                    ticker="AAPL",
                    orderType="STP",
                    side="SELL",
                    quantity=1,
                    auxPrice=94,
                )
            ])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=20, ask_size=20)
        )
        reduced_child = await self.broker.modify_order(
            "DU123",
            bracket_response[2]["order_id"],
            replace(inactive_child, quantity=5),
        )
        self.assertEqual(reduced_child[0]["order_status"], "Submitted")

    async def test_trailing_stop_tracks_favorable_price_before_triggering(self) -> None:
        entry = OrderRequest(
            acctId="DU123",
            conid=265598,
            cOID="trail-entry",
            ticker="AAPL",
            orderType="MKT",
            side="BUY",
            quantity=10,
        )
        await self.broker.place_orders("DU123", [entry])
        await self.broker.on_market_event(
            quote(bid=99, ask=100, bid_size=20, ask_size=20)
        )
        trailing = OrderRequest(
            acctId="DU123",
            conid=265598,
            cOID="trail-exit",
            ticker="AAPL",
            orderType="TRAIL",
            side="SELL",
            quantity=10,
            trailingAmt=2,
            trailingType="amt",
        )
        await self.broker.place_orders("DU123", [trailing])

        self.assertEqual(
            await self.broker.on_market_event(quote(bid=101, ask=102, bid_size=20)),
            [],
        )
        self.assertEqual(
            await self.broker.on_market_event(quote(bid=105, ask=106, bid_size=20)),
            [],
        )
        checkpoint = self.broker.checkpoint_state()
        restored = SimulatedBrokerAdapter(
            ["DU123"],
            SimulationConfig(
                initial_cash=10_000,
                commission_per_share=0.0,
                minimum_commission=0.0,
                liquidity_participation=0.5,
            ),
        )
        await restored.initialize()
        restored.restore_checkpoint_state(checkpoint)
        self.assertEqual(
            await restored.on_market_event(quote(bid=104, ask=105, bid_size=20)),
            [],
        )
        fills = await restored.on_market_event(
            quote(bid=103, ask=104, bid_size=20)
        )

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, 103)
        self.assertEqual(await restored.positions("DU123"), [])


class JournalTests(unittest.TestCase):
    def test_indexed_navigation_queries_find_latest_and_next_causal_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            first = journal.append(
                run_id="replay-a",
                category="strategy_decision",
                entity_type="signal",
                entity_id="decision-1",
                event_time=TS,
                payload={"action": "wait", "ticker": "AAPL"},
            )
            journal.append(
                run_id="replay-a",
                category="broker",
                entity_type="order",
                entity_id="order-1",
                event_time=TS + timedelta(seconds=1),
                payload={"ticker": "AAPL"},
            )
            final = journal.append(
                run_id="replay-a",
                category="strategy",
                entity_type="strategy_intent",
                entity_id="intent-1",
                event_time=TS + timedelta(seconds=2),
                payload={"action": "enter_long", "ticker": "AAPL"},
            )

            next_record = journal.next_record_after_time(
                "replay-a",
                TS - timedelta(microseconds=1),
                categories=("strategy_decision", "strategy"),
            )

            assert next_record is not None
            self.assertEqual(next_record.record_id, first.record_id)
            self.assertEqual(journal.latest_sequence("replay-a"), final.sequence)
            self.assertEqual(journal.latest_sequence("missing"), 0)
            journal.close()

    def test_historical_signal_stream_records_are_scoped_to_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            for run_id, entity_id in (("replay-a", "a"), ("replay-b", "b")):
                journal.append(
                    run_id=run_id,
                    category="market_discovery_signal",
                    entity_type="signal_occurrence",
                    entity_id=entity_id,
                    event_time=TS,
                    payload={"signal_stream_id": "squeeze", "ticker": "AAPL"},
                )

            rows = journal.signal_stream_records(
                run_id="replay-b", signal_stream_id="squeeze"
            )

            self.assertEqual([row.entity_id for row in rows], ["b"])
            journal.close()

    def test_shared_connection_reads_wait_for_active_journal_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            journal.append(
                run_id="signal-stream",
                category="market_discovery_signal",
                entity_type="signal_occurrence",
                entity_id="occurrence-1",
                event_time=TS,
                payload={"signal_stream_id": "squeeze", "ticker": "AAPL"},
            )

            with ThreadPoolExecutor(max_workers=1) as executor:
                with journal._lock:
                    future = executor.submit(
                        journal.signal_stream_records,
                        signal_stream_id="squeeze",
                    )
                    with self.assertRaises(FutureTimeoutError):
                        future.result(timeout=0.05)

                rows = future.result(timeout=1.0)

            self.assertEqual([row.entity_id for row in rows], ["occurrence-1"])
            journal.close()

    def test_strategy_activity_is_complete_causal_log_and_excludes_broker_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            journal.append(
                run_id="run-a", category="market_discovery_signal", entity_type="signal_occurrence", entity_id="occurrence-1",
                event_time=TS - timedelta(seconds=2), payload={"signal_stream_id": "squeeze", "signal_stream_name": "Exact 5% Squeeze", "ticker": "AAPL", "squeeze_move_pct": 5.25, "squeeze_anchor_price": 10.0, "last_price": 10.525, "liquidity_score": 88.0, "event_quote_bid_price": 10.52, "event_quote_ask_price": 10.53},
            )
            journal.append(
                run_id="run-a", category="watchlist_membership", entity_type="historical_watchlist_member", entity_id="AAPL",
                event_time=TS - timedelta(seconds=1), payload={"event": "added", "ticker": "AAPL", "source": "causal_historical_watchlist"},
            )
            journal.append(
                run_id="run-a", category="strategy_decision", entity_type="signal", entity_id="signal-1",
                event_time=TS, payload={
                    "strategy_id": "momentum",
                    "ticker": "AAPL",
                    "action": "wait",
                    "metadata": {
                        "entry_rules": {
                            "trigger": {"passed": True},
                            "confirmation": {"passed": False},
                            "veto": {"passed": False},
                        },
                        "execution_quality": {
                            "checks": {"spread": True, "trade_rate": True},
                            "failed": [],
                        }
                    },
                },
            )
            journal.append(
                run_id="run-a", category="strategy", entity_type="strategy_intent", entity_id="intent-1",
                event_time=TS + timedelta(seconds=1), payload={"strategy_id": "momentum", "ticker": "AAPL", "action": "enter_long"},
            )
            journal.append(
                run_id="run-a", category="broker", entity_type="order", entity_id="order-1",
                event_time=TS + timedelta(seconds=2), payload={"strategy_id": "momentum", "ticker": "AAPL"},
            )
            journal.append(
                run_id="run-a", category="order_management", entity_type="order_state", entity_id="order-state-1",
                event_time=TS + timedelta(seconds=3), payload={"strategy_id": "momentum", "ticker": "AAPL", "state": "submitted"},
            )

            rows = journal.strategy_activity_records(strategy_id="momentum", ticker="AAPL")
            self.assertEqual([row.entity_type for row in rows], ["order_state", "strategy_intent", "signal"])
            all_rows = journal.strategy_activity_records(ticker="AAPL")
            self.assertEqual(
                [row.entity_type for row in all_rows],
                ["order_state", "strategy_intent", "signal", "historical_watchlist_member", "signal_occurrence"],
            )
            activity = strategy_activity_payload(journal=journal, run_id="run-a", ticker="AAPL", as_of=TS + timedelta(seconds=4))
            self.assertEqual(
                [row["event_type"] for row in activity["rows"]],
                ["order", "decision", "decision", "watchlist", "signal"],
            )
            self.assertEqual(activity["rows"][2]["action"], "wait")
            self.assertEqual(
                activity["rows"][2]["gates"],
                "trigger:pass · confirmation:fail · veto:clear · execution:pass",
            )
            self.assertEqual(
                activity["rows"][0]["reason"],
                "Order is submitted.",
            )
            occurrence = activity["rows"][-1]
            self.assertEqual(occurrence["action"], "Exact 5% Squeeze")
            self.assertIn("+5.25% from squeeze anchor", occurrence["reason"])
            self.assertEqual(occurrence["reference_price"], 10.525)
            self.assertEqual(journal.strategy_activity_records(run_id="missing"), [])
            journal.close()

    def test_journal_sequence_checkpoint_strategy_and_outbox_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            journal = TradingJournal(path)
            first = journal.append(run_id="run", category="command", entity_type="order", entity_id="1", payload={"a": 1})
            second = journal.append(run_id="run", category="broker", entity_type="order", entity_id="1", payload={"status": "Submitted"})
            self.assertEqual(first.payload["correlation_id"], "run:run")
            self.assertTrue(first.payload["causation_id"].startswith("event:"))
            self.assertNotEqual(first.payload["causation_id"], second.payload["causation_id"])
            journal.save_checkpoint("run", "cursor", {"events": 2}, TS)
            journal.save_strategy(strategy_id="s", revision=1, name="Strategy", implementation="module:Class", automatic=True, config={"x": 2})
            journal.close()

            reopened = TradingJournal(path)
            self.assertEqual([first.sequence, second.sequence], [1, 2])
            self.assertEqual(len(reopened.pending_outbox()), 2)
            self.assertEqual(reopened.load_checkpoint("run")["state"], {"events": 2})
            self.assertTrue(reopened.strategy("s")["automatic"])
            reopened.close()

            read_only = TradingJournal(path, read_only=True)
            self.assertEqual(read_only.load_checkpoint("run")["state"], {"events": 2})
            with self.assertRaises(sqlite3.OperationalError):
                read_only.append(
                    run_id="run",
                    category="command",
                    entity_type="order",
                    entity_id="blocked",
                    payload={},
                )
            read_only.close()

    def test_journal_batch_is_atomic_and_preserves_per_run_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            journal = TradingJournal(path)
            journal.append(
                run_id="run-a",
                category="command",
                entity_type="order",
                entity_id="before",
                payload={"position": 0},
            )

            records = journal.append_many(
                [
                    {"run_id": "run-a", "category": "command", "entity_type": "order", "entity_id": "a-1", "payload": {"position": 1}},
                    {"run_id": "run-b", "category": "command", "entity_type": "order", "entity_id": "b-1", "payload": {"position": 1}},
                    {"run_id": "run-a", "category": "command", "entity_type": "order", "entity_id": "a-2", "payload": {"position": 2}},
                ]
            )

            self.assertEqual([(row.run_id, row.sequence) for row in records], [("run-a", 2), ("run-b", 1), ("run-a", 3)])
            self.assertEqual(len(journal.pending_outbox()), 4)
            journal.close()

    def test_journal_idempotent_batch_preserves_order_and_duplicate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            existing, inserted = journal.append_once(
                run_id="run",
                category="signal",
                entity_type="occurrence",
                entity_id="existing",
                payload={"position": 0},
                event_time=TS,
            )
            self.assertTrue(inserted)

            entries = [
                {"run_id": "run", "category": "signal", "entity_type": "occurrence", "entity_id": "new-a", "payload": {"position": 1}, "event_time": TS},
                {"run_id": "run", "category": "signal", "entity_type": "occurrence", "entity_id": "existing", "payload": {"position": 99}, "event_time": TS},
                {"run_id": "run", "category": "signal", "entity_type": "occurrence", "entity_id": "new-a", "payload": {"position": 2}, "event_time": TS},
                {"run_id": "run", "category": "signal", "entity_type": "occurrence", "entity_id": "new-b", "payload": {"position": 3}, "event_time": TS},
            ]
            results = journal.append_once_many(entries)

            self.assertEqual(
                [(record.entity_id, was_inserted) for record, was_inserted in results],
                [("new-a", True), ("existing", False), ("new-a", False), ("new-b", True)],
            )
            self.assertEqual(results[1][0].record_id, existing.record_id)
            self.assertEqual(results[2][0].payload["position"], 1)
            self.assertEqual([results[0][0].sequence, results[3][0].sequence], [2, 3])
            self.assertEqual(len(journal.pending_outbox()), 3)
            journal.close()

    def test_journal_preserves_explicit_autonomous_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            record = journal.append(
                run_id="run",
                category="portfolio",
                entity_type="decision",
                entity_id="decision-1",
                payload={
                    "correlation_id": "strategy:assignment-7",
                    "causation_id": "intent:intent-9",
                },
                event_time=TS,
            )
            self.assertEqual(record.payload["correlation_id"], "strategy:assignment-7")
            self.assertEqual(record.payload["causation_id"], "intent:intent-9")
            journal.close()

    def test_trade_annotations_are_durable_and_replace_by_episode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            journal = TradingJournal(path)
            saved = journal.save_trade_annotation(
                "episode-1",
                note="Waited for confirmation.",
                tags=["A+", "followed-plan", "A+"],
                review_status="reviewed",
                setup_override="Opening drive",
            )
            journal.close()

            reopened = TradingJournal(path)
            self.assertEqual(saved["tags"], ["A+", "followed-plan"])
            self.assertEqual(reopened.trade_annotation("episode-1")["note"], "Waited for confirmation.")
            reopened.save_trade_annotation("episode-1", note="Updated", tags=[], review_status="follow_up")
            self.assertEqual(reopened.trade_annotation("episode-1")["review_status"], "follow_up")
            reopened.close()

    def test_clickhouse_contract_uses_fixed_prefix_and_typed_order_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            record = journal.append(
                run_id="00000000-0000-0000-0000-000000000001", category="broker", entity_type="order",
                entity_id="7", account_id="DU123", event_time=TS,
                payload={"order_id": "7", "local_order_id": "coid", "order_status": "Submitted"},
            )
            typed = _specialized_rows([record])["tr_order_event_v1"]
            self.assertEqual(typed[0]["client_order_id"], "coid")
            self.assertTrue(all("q_live.tr_" in statement for statement in TRADING_TABLE_DDL))
            journal.close()

    def test_clickhouse_contract_projects_typed_strategy_signal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            record = journal.append(
                run_id="00000000-0000-0000-0000-000000000001",
                category="strategy_decision",
                entity_type="signal",
                entity_id="strategy-signal-1",
                account_id="DU123",
                event_time=TS,
                payload={
                    "strategy_id": "breakout",
                    "strategy_revision": 3,
                    "ticker": "AAPL",
                    "signal_type": "entry_decision",
                    "action": "enter_long",
                    "direction": "bullish",
                    "working_timeframe": "1s",
                    "score": 0.74,
                    "confidence": 0.82,
                    "source_signal_ids": ["qmd-1"],
                    "invalidation_price": 313.5,
                    "reason": "QMD flow confirmed the structural break.",
                },
            )

            typed = _specialized_rows([record])["tr_signal_v2"][0]
            self.assertEqual(typed["signal_id"], "strategy-signal-1")
            self.assertEqual(typed["strategy_revision"], 3)
            self.assertEqual(typed["action"], "enter_long")
            self.assertEqual(typed["source_signal_ids"], ["qmd-1"])
            self.assertEqual(typed["invalidation_price"], 313.5)
            journal.close()


class HistoricalContractTests(unittest.TestCase):
    @staticmethod
    def gateway_call(gateway_get, expected_path: str):
        return next(
            call.args[:2]
            for call in reversed(gateway_get.call_args_list)
            if call.args and call.args[0] == expected_path
        )

    def test_subsecond_and_five_second_charts_use_enriched_indicator_contract(self) -> None:
        self.assertIn("100ms", ENRICHED_QMD_TIMEFRAMES)
        self.assertIn("5s", ENRICHED_QMD_TIMEFRAMES)

    def test_five_minute_first_paint_bounds_raw_event_work(self) -> None:
        session_start = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
        page_start, page_end, has_earlier = _bounded_historical_chart_window(
            session_start=session_start,
            session_end=datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc),
            as_of=datetime(2026, 8, 25, 22, 35, tzinfo=timezone.utc),
            before_bar=None,
            timeframe="5m",
            row_limit=5_000,
        )

        self.assertEqual(page_end, datetime(2026, 8, 25, 22, 35, tzinfo=timezone.utc))
        self.assertEqual(page_start, datetime(2026, 8, 25, 20, 35, tzinfo=timezone.utc))
        self.assertTrue(has_earlier)

    def test_completed_five_second_review_loads_the_full_premarket_session(self) -> None:
        session_start = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
        page_start, page_end, has_earlier = _bounded_historical_chart_window(
            session_start=session_start,
            session_end=datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc),
            as_of=datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc),
            before_bar=None,
            timeframe="5s",
            row_limit=5_000,
            full_session=True,
        )

        self.assertEqual(page_start, session_start)
        self.assertEqual(page_end, datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc))
        self.assertFalse(has_earlier)

    def test_completed_subsecond_review_remains_bounded_when_session_exceeds_capacity(self) -> None:
        session_start = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
        page_start, page_end, has_earlier = _bounded_historical_chart_window(
            session_start=session_start,
            session_end=datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc),
            as_of=datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc),
            before_bar=None,
            timeframe="100ms",
            row_limit=25_000,
            full_session=True,
        )

        self.assertEqual(page_end, datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc))
        self.assertEqual(page_start, datetime(2026, 8, 21, 13, 19, 35, tzinfo=timezone.utc))
        self.assertTrue(has_earlier)

    def test_python_runtime_consumes_the_rust_market_event_contract(self) -> None:
        event = event_from_qmd_payload(
            {
                "kind": "quote", "ticker": "AAPL", "sequence": 42, "tape": 3,
                "ts": TS.isoformat(), "ingest_ts": TS.isoformat(), "conditions": [3], "indicators": [],
                "ask_exchange": 11, "ask_price": 100.1234, "ask_size": 20,
                "bid_exchange": 12, "bid_price": 100.12, "bid_size": 25,
                "raw": {"schema_version": 4, "arrival_sequence": 42},
            }
        )
        self.assertIsInstance(event, QuoteEvent)
        self.assertEqual(event.sequence, 42)
        self.assertAlmostEqual(event.ask_price, 100.1234)
        self.assertAlmostEqual(event.bid_price, 100.12)

    def test_historical_stream_errors_are_not_treated_as_market_events(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ClickHouse unavailable"):
            event_from_qmd_payload(
                {
                    "error": "ClickHouse unavailable",
                    "source": "historical_clickhouse",
                    "terminal": True,
                }
            )

    def test_historical_health_rejects_another_service_on_the_same_port(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "different service"):
            _validate_health({"service": "news_gateway", "status": "ready"})
        payload = {
            "service": "qmd_history_gateway",
            "host_role": "historical",
            "status": "ready",
            "running": True,
        }
        self.assertIs(_validate_health(payload), payload)

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_chart_history_preserves_exact_session_as_of_and_intra_session_cursor(self, gateway_get) -> None:
        gateway_get.return_value = {
            "as_of": "2026-07-10T13:45:00+00:00",
            "bars": [
                {
                    "bar_start": "2026-07-10T13:44:00+00:00",
                    "bar_end": "2026-07-10T13:45:00+00:00",
                    "close": 315.0,
                }
            ],
            "has_more": True,
            "indicators": [{"bar_start": "2026-07-10T13:44:00+00:00", "ema_20": 314.8}],
            "indicators_available": True,
            "indicator_provenance": {
                "engine_version": "qmd-derived-v28",
                "indicator_schema_version": 18,
                "source": {"source_plan_hash": "plan-123", "tiers": ["archive", "recent"]},
                "warm_up": {"status": "satisfied_in_response"},
            },
            "next_before": "2026-07-10T13:44:00+00:00",
            "structure_events": [
                {
                    "event_id": 91,
                    "event_kind": "level_promoted",
                    "timeframe": "100ms",
                },
                {
                    "event_id": 92,
                    "event_kind": "level_promoted",
                    "timeframe": "1s",
                },
            ],
            "structure_level_history": [
                {
                    "created_at_ms": 1_752_155_940_000,
                    "side": 1,
                    "price": 314.75,
                    "footprint": [{"price": 314.75, "total_volume": 2_400.0}],
                }
            ],
            "market_signal_events": [
                {
                    "event_id": "event-1",
                    "signal_id": "signal-1",
                    "signal_key": "vwap_transition",
                    "state": "triggered",
                    "effective_at": "2026-07-10T13:44:59.900+00:00",
                }
            ],
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2026, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            before_bar="2026-07-10T13:44:00+00:00",
            ticker="AAPL",
            timeframe="100ms",
            row_limit=5_000,
            indicator_columns=["bar_start", "ema_20", "ema_20"],
        )

        path, params = self.gateway_call(gateway_get, "/snapshot/chart-bars/AAPL")
        self.assertEqual(path, "/snapshot/chart-bars/AAPL")
        self.assertEqual(params["timeframe"], "100ms")
        self.assertEqual(params["as_of"], "2026-07-10T13:44:00+00:00")
        self.assertIsNone(params["before"])
        self.assertEqual(params["end"], "2026-07-10T13:44:00+00:00")
        self.assertEqual(params["start"], "2026-07-10T13:33:35+00:00")
        self.assertEqual(params["indicator_columns"], "bar_start,ema_20")
        self.assertEqual(params["allow_persisted_bars"], "true")
        self.assertEqual(params["include_market_signals"], "true")
        self.assertEqual(params["include_structure"], "true")
        self.assertEqual(params["mode"], "live")
        self.assertEqual(params["stage"], "full")
        self.assertEqual(result["next_before"], "2026-07-10T13:44:00+00:00")
        self.assertTrue(result["has_more_in_session"])
        self.assertEqual(len(result["indicators"]), 1)
        self.assertEqual(
            result["indicator_provenance"]["source"]["source_plan_hash"],
            "plan-123",
        )
        self.assertEqual(
            [(row["event_id"], row["timeframe"]) for row in result["structure_events"]],
            [(91, "100ms"), (92, "1s")],
        )
        self.assertEqual(result["structure_level_history"][0]["price"], 314.75)
        self.assertEqual(result["market_signal_events"][0]["signal_id"], "signal-1")

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_chart_bar_stage_is_forwarded_without_indicator_projection(self, gateway_get) -> None:
        gateway_get.return_value = {
            "bars": [{"bar_start": "2026-07-10T13:44:00+00:00", "close": 315.0}],
            "has_more": False,
            "indicators": [],
            "indicators_available": False,
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2026, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            ticker="AAPL",
            timeframe="1m",
            stage="bars",
        )

        _, params = gateway_get.call_args_list[0].args[:2]
        self.assertEqual(params["stage"], "bars")
        self.assertIsNone(params["indicator_columns"])
        self.assertEqual(result["stage"], "bars")
        self.assertEqual(len(result["history"]), 1)

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_chart_history_orders_fractional_rfc3339_timestamps_chronologically(self, gateway_get) -> None:
        gateway_get.return_value = {
            "as_of": "2026-07-14T13:45:00Z",
            "bars": [
                {"bar_start": "2026-07-14T12:54:14.400Z", "close": 315.4},
                {"bar_start": "2026-07-14T12:54:14Z", "close": 315.0},
            ],
            "has_more": False,
            "indicators": [
                {"bar_start": "2026-07-14T12:54:14.400Z", "ema_20": 315.2},
                {"bar_start": "2026-07-14T12:54:14Z", "ema_20": 315.0},
            ],
            "indicators_available": True,
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 15),
            session_date=date(2026, 7, 14),
            as_of="2026-07-14T13:45:00Z",
            before_bar=None,
            ticker="AAPL",
            timeframe="100ms",
            row_limit=5_000,
        )

        self.assertEqual(
            [row["bar_start"] for row in result["history"]],
            ["2026-07-14T12:54:14Z", "2026-07-14T12:54:14.400Z"],
        )
        self.assertEqual(
            [row["bar_start"] for row in result["indicators"]],
            ["2026-07-14T12:54:14Z", "2026-07-14T12:54:14.400Z"],
        )

    @patch("src.backend.trading_runtime_service._is_recent_live_chart_session", return_value=True)
    @patch("src.backend.trading_runtime_service.qmd_intraday_bar_history")
    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_recent_live_chart_defers_older_coverage_lookup_until_requested(
        self, gateway_get, live_history, _is_recent
    ) -> None:
        live_history.return_value = {
            "bars": [{"bar_start": "2026-07-14T13:44:00+00:00", "close": 315.0}],
            "has_more": False,
        }
        result = historical_bar_history_before(
            before=date(2026, 7, 15),
            session_date=date(2026, 7, 14),
            ticker="AAPL",
            timeframe="1m",
            stage="bars",
        )

        self.assertTrue(result["has_more"])
        self.assertFalse(result["has_more_in_session"])
        self.assertEqual(result["previous_session_before"], "2026-07-14")
        gateway_get.assert_not_called()

    @patch("src.backend.trading_runtime_service._is_recent_live_chart_session", return_value=True)
    @patch("src.backend.trading_runtime_service.qmd_intraday_bar_history")
    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_replay_chart_never_reads_live_materializations(
        self, gateway_get, live_history, _is_recent
    ) -> None:
        gateway_get.return_value = {
            "bars": [{"bar_start": "2026-07-14T13:44:00+00:00", "close": 315.0}],
            "has_more": False,
            "indicators": [],
            "indicators_available": False,
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 15),
            session_date=date(2026, 7, 14),
            ticker="AAPL",
            timeframe="1m",
            stage="bars",
            mode="replay",
        )

        live_history.assert_not_called()
        _, params = gateway_get.call_args_list[0].args[:2]
        self.assertEqual(params["mode"], "replay")
        self.assertEqual(len(result["history"]), 1)

    @patch("src.backend.trading_runtime_service._is_recent_live_chart_session", return_value=True)
    @patch("src.backend.trading_runtime_service.qmd_persisted_indicators")
    @patch("src.backend.trading_runtime_service.qmd_intraday_bar_history")
    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_recent_live_full_chart_uses_matching_durable_indicators(
        self, gateway_get, live_history, persisted_indicators, _is_recent
    ) -> None:
        live_history.return_value = {
            "bars": [{"bar_start": "2026-07-14T13:44:00+00:00", "close": 315.0}],
            "has_more": False,
            "source": "qmd_live_intraday_family_bars_v3",
        }
        persisted_indicators.return_value = {
            "history": [{"bar_start": "2026-07-14T13:44:00+00:00", "ema_20": 314.5}],
            "source": "qmd_live_qmd_indicator_rows_v1",
            "calculation_revision": "qmd-indicators-v21",
            "complete": True,
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 15),
            session_date=date(2026, 7, 14),
            ticker="AAPL",
            timeframe="1m",
            stage="full",
            mode="live",
        )

        self.assertEqual(result["source"], "qmd_live_intraday_family_bars_v3")
        self.assertEqual(result["indicators"][0]["ema_20"], 314.5)
        self.assertTrue(result["indicators_available"])
        gateway_get.assert_not_called()

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_monthly_chart_history_honors_visible_page_budget(self, gateway_get) -> None:
        gateway_get.return_value = {
            "bars": [
                {
                    "bar_family": "trade",
                    "bar_start": "2023-08-01T04:00:00+00:00",
                    "bar_end": "2023-09-01T04:00:00+00:00",
                    "close": 190.0,
                    "high": 198.0,
                    "is_closed": True,
                    "low": 175.0,
                    "open": 178.0,
                    "session_date": "2023-08-01",
                    "size_sum": 10_000.0,
                }
            ],
            "coverage_status": "stale",
            "latest_session_date": "2023-08-01",
            "source": "market_sip_compact.daily_session_bars_by_symbol_time_v1",
            "split_adjusted": True,
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2026, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            before_bar=None,
            ticker="AAPL",
            timeframe="1mo",
            row_limit=36,
        )

        path, params = self.gateway_call(
            gateway_get, "/snapshot/chart-macro-bars/AAPL"
        )
        self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
        self.assertEqual(params["timeframe"], "1mo")
        self.assertEqual(params["start"], "2023-07-01T00:00:00+00:00")
        self.assertEqual(params["limit"], 37)
        self.assertEqual(result["history"][0]["volume"], 10_000.0)
        self.assertEqual(result["coverage_status"], "stale")
        self.assertEqual(result["latest_session_date"], "2023-08-01")
        self.assertTrue(result["split_adjusted"])
        self.assertFalse(result["indicators_available"])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["next_before"], "")

    @patch("src.backend.trading_runtime_service.qmd_product_request")
    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_live_monthly_chart_reads_completed_history_without_source_plan(
        self, gateway_get, product_request
    ) -> None:
        gateway_get.return_value = {"bars": [], "source": "qmd_history"}

        historical_bar_history_before(
            before=date(2026, 8, 25),
            session_date=date(2026, 8, 24),
            as_of="2026-08-25T02:15:00+00:00",
            ticker="AMIX",
            timeframe="1mo",
            row_limit=36,
            mode="live",
        )

        product_request.assert_not_called()
        path, params = self.gateway_call(
            gateway_get, "/snapshot/chart-macro-bars/AMIX"
        )
        self.assertEqual(path, "/snapshot/chart-macro-bars/AMIX")
        self.assertEqual(params["mode"], "live")
        self.assertEqual(params["limit"], 37)

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_daily_chart_history_honors_visible_page_budget(self, gateway_get) -> None:
        gateway_get.return_value = {"bars": [], "source": "market_sip_compact.daily_session_bars_by_symbol_time_v1"}

        historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2026, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            before_bar=None,
            ticker="AAPL",
            timeframe="1d",
            row_limit=240,
        )

        path, params = self.gateway_call(
            gateway_get, "/snapshot/chart-macro-bars/AAPL"
        )
        self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
        self.assertEqual(params["timeframe"], "1d")
        expected_start = (datetime(2026, 7, 10, 13, 45, tzinfo=timezone.utc) - timedelta(days=408)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(params["start"], expected_start.isoformat())
        self.assertEqual(params["limit"], 241)

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_daily_chart_history_pages_backward_from_earliest_bar(self, gateway_get) -> None:
        gateway_get.return_value = {
            "bars": [
                {
                    "bar_family": "trade",
                    "bar_start": "2020-07-09T08:00:00+00:00",
                    "bar_end": "2020-07-10T00:00:00+00:00",
                    "session_date": "2020-07-09",
                }
            ],
            "source": "market_sip_compact.daily_session_bars_by_symbol_time_v1",
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2023, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            before_bar="2023-07-10T08:00:00+00:00",
            ticker="AAPL",
            timeframe="1d",
            row_limit=240,
        )

        path, params = self.gateway_call(gateway_get, "/snapshot/chart-macro-bars/AAPL")
        self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
        expected_start = (datetime(2023, 7, 10, 8, 0, tzinfo=timezone.utc) - timedelta(days=408)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(params["start"], expected_start.isoformat())
        self.assertEqual(params["end"], "2023-07-10T08:00:00+00:00")
        self.assertEqual(params["as_of"], "2026-07-10T13:45:00+00:00")
        self.assertFalse(result["has_more"])
        self.assertEqual(result["next_before"], "")

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_weekly_and_yearly_chart_history_use_daily_macro_authority(self, gateway_get) -> None:
        gateway_get.return_value = {"bars": [], "source": "market_sip_compact.daily_session_bars_by_symbol_time_v1"}

        for timeframe, row_limit, expected_start in (
            ("1w", 156, (datetime(2026, 7, 10, 13, 45, tzinfo=timezone.utc) - timedelta(days=1365)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()),
            ("1y", 20, "2006-01-01T00:00:00+00:00"),
        ):
            with self.subTest(timeframe=timeframe):
                historical_bar_history_before(
                    before=date(2026, 7, 11),
                    session_date=date(2026, 7, 10),
                    as_of="2026-07-10T13:45:00+00:00",
                    before_bar=None,
                    ticker="AAPL",
                    timeframe=timeframe,
                    row_limit=row_limit,
                )
                path, params = self.gateway_call(
                    gateway_get, "/snapshot/chart-macro-bars/AAPL"
                )
                self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
                self.assertEqual(params["timeframe"], timeframe)
                self.assertEqual(params["start"], expected_start)
                self.assertEqual(params["limit"], row_limit + 1)

    def test_live_family_bars_use_the_chart_bar_contract(self) -> None:
        payload = normalize_qmd_family_bar_snapshot(
            {
                "rows": [
                    {
                        "bar_start": "2026-07-10T13:44:59.900000+00:00",
                        "bar_end": "2026-07-10T13:45:00+00:00",
                        "bar_family": "trade",
                        "close": 0,
                        "high": 0,
                        "local_date": "2026-07-10",
                        "low": 0,
                        "open": 0,
                        "schema_version": 1,
                        "size_sum": 50,
                        "state": "closed",
                        "ticker": "AAPL",
                    },
                    {
                        "bar_start": "2026-07-10T13:45:00+00:00",
                        "bar_end": "2026-07-10T13:45:00.100000+00:00",
                        "bar_family": "trade",
                        "close": 315.0,
                        "high": 315.1,
                        "local_date": "2026-07-10",
                        "low": 314.9,
                        "open": 314.95,
                        "schema_version": 1,
                        "size_sum": 200,
                        "state": "partial",
                        "ticker": "AAPL",
                    }
                ]
            },
            symbol="AAPL",
            timeframe="100ms",
        )
        self.assertEqual(payload["history"], [])
        self.assertEqual(payload["current"]["timeframe"], "100ms")
        self.assertEqual(payload["current"]["volume"], 200)


class LiveOrderContractTests(unittest.TestCase):
    def test_live_payload_uses_ibkr_field_names_for_stop_limit(self) -> None:
        payload = ibkr_order_payload(
            {
                "symbol": "AAPL", "side": "SELL", "quantity": 10, "order_type": "STOP_LIMIT",
                "client_order_id": "exit-1", "conid": 265598, "limit_price": 94.5,
                "stop_price": 95, "time_in_force": "GTC", "outside_rth": False,
            },
            "DU123",
        )
        self.assertEqual(payload["acctId"], "DU123")
        self.assertEqual(payload["secType"], "265598:STK")
        self.assertEqual(payload["orderType"], "STOP_LIMIT")
        self.assertEqual(payload["price"], 94.5)
        self.assertEqual(payload["auxPrice"], 95)


class _NoopStrategy:
    strategy_id = "noop"
    revision = 1
    automatic = True

    async def on_event(self, event, account_id):
        return StrategyEvaluation()


class _SignalAwareStrategy(_NoopStrategy):
    strategy_id = "signal-aware"
    revision = 2

    async def on_market_signal(self, signal, account_id):
        return StrategyEvaluation(
            signals=(
                StrategySignal(
                    signal_id="strategy-decision-1",
                    signal_type="market_signal_assessment",
                    ticker=signal.ticker,
                    event_time=signal.effective_at,
                    action="hold",
                    direction=signal.direction,
                    score=signal.score,
                    confidence=signal.confidence,
                    reason="Observed reusable QMD evidence; no order condition yet.",
                    source_signal_ids=(signal.signal_id,),
                    working_timeframe=signal.working_timeframe,
                ),
            )
        )


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_journals_wait_reason_transitions_not_duplicate_market_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            runtime = TradingRuntime.__new__(TradingRuntime)
            runtime.config = RunConfig(
                RunMode.BACKTEST,
                "signal-aware",
                2,
                ("DU123",),
                date(2026, 7, 14),
                run_id="00000000-0000-0000-0000-000000000099",
            )
            runtime.run_id = runtime.config.run_id
            runtime.journal = journal
            runtime._last_wait_decision_signatures = {}

            def wait_signal(
                signal_id: str,
                *,
                detail: str,
                swing_passed: bool,
                event_time: datetime,
            ) -> StrategySignal:
                return StrategySignal(
                    signal_id=signal_id,
                    signal_type="entry_confirmation_incomplete",
                    ticker="CLSK",
                    event_time=event_time,
                    action="wait",
                    direction="neutral",
                    score=0.2,
                    confidence=0.7,
                    reason="entry_confirmation_incomplete",
                    metadata={
                        "reason_code": "entry_confirmation_incomplete",
                        "reason_detail": detail,
                        "status": "watching",
                        "entry_rules": {
                            "trigger": {
                                "condition_evidence": {
                                    "swing-high-break": [{
                                        "condition_id": "price-above-swing-high",
                                        "left_source_id": "market.last_price",
                                        "passed": swing_passed,
                                    }]
                                }
                            }
                        },
                    },
                )

            try:
                runtime._record_strategy_signals(
                    StrategyEvaluation(signals=(wait_signal(
                        "wait-1",
                        detail="Wait: price is 4.10; requires 4.20.",
                        swing_passed=False,
                        event_time=TS,
                    ),)),
                    "DU123",
                )
                runtime._record_strategy_signals(
                    StrategyEvaluation(signals=(wait_signal(
                        "wait-2",
                        detail="Wait: price is 4.11; requires 4.20.",
                        swing_passed=False,
                        event_time=TS + timedelta(seconds=10),
                    ),)),
                    "DU123",
                )
                runtime._record_strategy_signals(
                    StrategyEvaluation(signals=(wait_signal(
                        "wait-3",
                        detail="Wait: swing high passed; VWAP remains incomplete.",
                        swing_passed=True,
                        event_time=TS + timedelta(seconds=11),
                    ),)),
                    "DU123",
                )

                decisions = [
                    row for row in journal.records(runtime.run_id)
                    if row.category == "strategy_decision"
                ]
                self.assertEqual([row.entity_id for row in decisions], ["wait-1", "wait-3"])
                self.assertEqual(
                    decisions[0].payload["metadata"]["reason_detail"],
                    "Wait: price is 4.10; requires 4.20.",
                )
            finally:
                journal.close()

    async def test_confirmed_external_proposal_is_journaled_before_portfolio_and_oms(self) -> None:
        for index, authority in enumerate(("manual", "semi_automatic"), start=3):
            with self.subTest(authority=authority), tempfile.TemporaryDirectory() as directory:
                journal = TradingJournal(Path(directory) / "journal.sqlite3")
                try:
                    runtime = TradingRuntime.__new__(TradingRuntime)
                    runtime.config = RunConfig(
                        RunMode.REPLAY,
                        "interactive-proposal",
                        1,
                        ("SIM-01",),
                        date(2026, 7, 14),
                        run_id=f"00000000-0000-0000-0000-{index:012d}",
                    )
                    runtime.run_id = runtime.config.run_id
                    runtime.journal = journal
                    runtime.last_event_time = TS
                    runtime._execute_intents = AsyncMock(return_value=[{
                        "decision": {"status": "approved", "reservation_id": "reservation-1"},
                        "order_group": {"state": "submitted"},
                    }])
                    intent = StrategyIntent(
                        intent_id=f"proposal:proposal-{index}",
                        ticker="AAPL",
                        event_time=TS,
                        action="enter_long",
                        quantity=10,
                        reference_price=100,
                        invalidation_price=95,
                    )

                    result = await runtime.submit_external_intent(
                        intent,
                        account_id="SIM-01",
                        proposal_id=f"proposal-{index}",
                        proposal_authority=authority,
                    )

                    records = journal.records(runtime.run_id)
                    self.assertEqual(
                        [row.entity_type for row in records],
                        ["trade_proposal_confirmed", "trade_proposal_result"],
                    )
                    self.assertEqual(records[0].payload["authority"], authority)
                    self.assertEqual(result["decision"]["status"], "approved")
                    runtime._execute_intents.assert_awaited_once()
                finally:
                    journal.close()

    async def test_market_signal_validates_qmd_payload_at_strategy_boundary(self) -> None:
        payload = {
            "signal_id": "qmd-signal-1",
            "event_id": "qmd-event-1",
            "signal_key": "directional_flow_acceleration",
            "schema_version": 3,
            "signal_version": 1,
            "engine_version": "qmd-market-signal-v2",
            "producer": "qmd",
            "ticker": "aapl",
            "working_timeframe": "100ms",
            "observed_at": TS.isoformat(),
            "effective_at": TS.isoformat(),
            "state": "triggered",
            "direction": "bearish",
            "score": -0.7,
            "rank_score": 0.66,
            "confidence": 0.8,
            "reference_price": 314.5,
            "evidence": {"tape_imbalance": -0.4},
            "clock": {
                "input_basis": "event_native",
                "calculation_window": "100ms",
                "evaluation_mode": "closed_only",
                "update_trigger": "bar_close",
                "publication_cadence": "interval",
                "publication_interval_ms": 100,
            },
        }
        signal = MarketSignal.from_qmd_payload(payload)
        self.assertEqual(signal.ticker, "AAPL")
        self.assertEqual(signal.score, -0.7)
        self.assertEqual(signal.rank_score, 0.66)
        self.assertEqual(signal.effective_at, TS)
        self.assertEqual(signal.domain.value, "market")
        self.assertEqual(signal.clock.calculation_window, "100ms")
        self.assertEqual(signal.clock.publication_cadence.value, "interval")

        with self.assertRaisesRegex(ValueError, "must include a timezone"):
            MarketSignal.from_qmd_payload(
                {
                    **payload,
                    "observed_at": "2026-07-13T14:00:00",
                    "effective_at": "2026-07-13T14:00:00",
                }
            )
        with self.assertRaisesRegex(ValueError, "missing: score"):
            MarketSignal.from_qmd_payload({key: value for key, value in payload.items() if key != "score"})
        with self.assertRaisesRegex(ValueError, "missing: rank_score"):
            MarketSignal.from_qmd_payload(
                {key: value for key, value in payload.items() if key != "rank_score"}
            )

    async def test_market_signal_is_interpreted_by_strategy_without_implicit_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            try:
                broker = SimulatedBrokerAdapter(["DU123"])
                runtime = TradingRuntime(
                    RunConfig(
                        RunMode.BACKTEST,
                        "signal-aware",
                        2,
                        ("DU123",),
                        date(2026, 7, 14),
                        run_id="00000000-0000-0000-0000-000000000002",
                    ),
                    broker,
                    _SignalAwareStrategy(),
                    journal,
                )
                await runtime.initialize()
                await runtime.process_market_signal(
                    MarketSignal(
                        signal_id="qmd-signal-1",
                        event_id="qmd-event-1",
                        signal_key="vwap_transition",
                        schema_version=3,
                        signal_version=1,
                        engine_version="qmd-market-signal-v2",
                        producer="qmd-gateway",
                        ticker="AAPL",
                        working_timeframe="1s",
                        observed_at=TS,
                        effective_at=TS,
                        state="triggered",
                        direction="bullish",
                        score=0.64,
                        rank_score=0.60,
                        confidence=0.71,
                        trigger_reason="Price reclaimed VWAP with positive flow.",
                        reference_price=315.0,
                    )
                )

                records = journal.records(runtime.run_id)
                strategy_records = [row for row in records if row.entity_type == "signal"]
                order_records = [row for row in records if row.entity_type == "order"]
                self.assertEqual(len(strategy_records), 1)
                self.assertEqual(strategy_records[0].payload["action"], "hold")
                self.assertEqual(strategy_records[0].payload["source_signal_ids"], ["qmd-signal-1"])
                self.assertEqual(order_records, [])
            finally:
                journal.close()

    async def test_runtime_rejects_out_of_order_events_and_persists_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            broker = SimulatedBrokerAdapter(["DU123"])
            runtime = TradingRuntime(
                RunConfig(RunMode.BACKTEST, "noop", 1, ("DU123",), date(2026, 7, 14), run_id="00000000-0000-0000-0000-000000000001"),
                broker,
                _NoopStrategy(),
                journal,
            )
            await runtime.initialize()
            await runtime.process_event(quote(bid=99, ask=100))
            self.assertEqual(journal.load_checkpoint(runtime.run_id)["state"]["processed_events"], 1)
            older = quote(bid=98, ask=99)
            object.__setattr__(older, "ts", datetime(2026, 7, 13, 13, 59, tzinfo=timezone.utc))
            with self.assertRaisesRegex(ValueError, "non-decreasing"):
                await runtime.process_event(older)
            journal.close()

    async def test_passive_market_event_updates_causal_state_without_order_matching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            broker = SimulatedBrokerAdapter(["DU123"])
            runtime = TradingRuntime(
                RunConfig(
                    RunMode.REPLAY,
                    "noop",
                    1,
                    ("DU123",),
                    date(2026, 7, 14),
                    run_id="00000000-0000-0000-0000-000000000003",
                    checkpoint_interval_events=10_000,
                ),
                broker,
                _NoopStrategy(),
                journal,
            )
            try:
                await runtime.initialize()
                runtime.process_passive_market_event(quote(bid=99, ask=100))

                self.assertEqual(runtime.processed_events, 1)
                self.assertEqual(runtime.last_event_time, TS)
                self.assertTrue(runtime._latest_checkpoint_cursor.endswith("|1|quote"))
                self.assertFalse(broker.has_orders)
                self.assertEqual(broker._quotes[265598].ask_price, 100)
            finally:
                journal.close()

    async def test_backtest_anchor_is_exclusive_and_replay_anchor_is_inclusive(self) -> None:
        backtest = historical_run_window(RunMode.BACKTEST, date(2026, 7, 13), session_count=1)
        replay = historical_run_window(RunMode.REPLAY, date(2026, 7, 13))
        self.assertEqual(backtest.sessions, (date(2026, 7, 10),))
        self.assertEqual(replay.sessions, (date(2026, 7, 13),))


if __name__ == "__main__":
    unittest.main()

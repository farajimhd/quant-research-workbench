"""Native completed-liquidity-bucket execution, without synthetic tape events."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig


START = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


def bar(at, *, bid=9.99, ask=10.0, bid_size=100, ask_size=100,
        low=9.98, high=10.02, execution_volume=100, quote_age_us=10_000):
    last_us = int(at.timestamp() * 1_000_000) - 1
    return {
        "ticker": "AAPL", "resolution_ms": 100,
        "bucket_index": int((at - START).total_seconds() * 10),
        "event_count": 3, "last_event_us": last_us,
        "quote_valid": 1, "quote_timestamp_us": last_us - quote_age_us,
        "bid_int": round(bid * 10_000), "ask_int": round(ask * 10_000),
        "bid_size": bid_size, "ask_size": ask_size,
        "price_valid": 1, "extremes_valid": 1,
        "close_int": round(ask * 10_000),
        "low_int": round(low * 10_000), "high_int": round(high * 10_000),
        "execution_volume": execution_volume,
    }


class LiquidityBarBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = SimulatedBrokerAdapter(
            ["TEST"], SimulationConfig(initial_cash=100_000,
                commission_per_share=0, minimum_commission=0,
                liquidity_participation=0.25,
                marketable_liquidity_participation=1),
            mode=RunMode.BACKTEST, initial_time=START,
        )
        await self.broker.initialize()

    async def order(self, kind, *, side="BUY", quantity=10, price=None, stop=None,
                    oid=None):
        await self.broker.place_orders("TEST", [OrderRequest(
            acctId="TEST", conid=265598, cOID=oid or f"{kind}-{side}", ticker="AAPL",
            orderType=kind, side=side, quantity=quantity, price=price, auxPrice=stop,
        )])

    async def test_market_order_uses_completed_quote_and_displayed_size(self):
        await self.order("MKT", quantity=30)
        at = START + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(bar(at, ask_size=8), at=at)
        self.assertEqual([(fill.price, fill.size) for fill in fills], [(10.0, 8.0)])
        self.assertEqual(await self.broker.match_current_orders("AAPL", at), [])

    async def test_order_from_current_bucket_waits_until_next_bucket(self):
        first = START + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(bar(first), at=first), [])
        await self.order("MKT")
        self.assertEqual(await self.broker.match_current_orders("AAPL", first), [])
        second = first + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(bar(second), at=second)
        self.assertEqual(sum(fill.size for fill in fills), 10)

    async def test_intrabar_stop_trigger_defers_fill(self):
        await self.order("STP", stop=10.1)
        first = START + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(
            bar(first, high=10.2), at=first), [])
        second = first + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(bar(second), at=second)
        self.assertEqual([(fill.price, fill.size) for fill in fills], [(10.0, 10.0)])

    async def test_resting_limit_uses_execution_volume_not_displayed_quote_size(self):
        await self.order("LMT", price=9.9, quantity=30)
        at = START + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(
            bar(at, low=9.8, execution_volume=40), at=at)
        self.assertEqual([(fill.price, fill.size) for fill in fills], [(9.9, 10.0)])

    async def test_stale_quote_cannot_fill_market_order(self):
        await self.order("MKT")
        at = START + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(
            bar(at, quote_age_us=2_000_000), at=at), [])

    async def test_quote_only_bucket_can_fill_and_no_quote_clock_survives_restore(self):
        await self.order("MKT", quantity=5)
        first = START + timedelta(milliseconds=100)
        quote_only = bar(first)
        quote_only.update(price_valid=0, extremes_valid=0, close_int=0,
                          low_int=0, high_int=0, execution_volume=0)
        fills = await self.broker.on_liquidity_bar(quote_only, at=first)
        self.assertEqual(sum(fill.size for fill in fills), 5)
        second = first + timedelta(milliseconds=100)
        no_quote = bar(second)
        no_quote.update(quote_valid=0, quote_timestamp_us=0)
        await self.broker.on_liquidity_bar(no_quote, at=second)
        await self.order("MKT", quantity=3, oid="next")
        restored = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST, initial_time=START)
        await restored.initialize()
        restored.restore_checkpoint_state(self.broker.checkpoint_state())
        self.assertEqual(await restored.match_current_orders("AAPL", second), [])
        third = second + timedelta(milliseconds=100)
        third_bar = bar(third)
        fills = await restored.on_liquidity_bar(third_bar, at=third)
        self.assertEqual(sum(fill.size for fill in fills), 3)

    async def test_runtime_records_bar_fill_without_market_event(self):
        class NoopStrategy:
            strategy_id = "bar-test"
            revision = 1
            automatic = True

        with TemporaryDirectory(dir=Path("D:/TradingML/runtimes")) as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            try:
                runtime = TradingRuntime(
                    RunConfig(RunMode.BACKTEST, "bar-test", 1, ("TEST",), START.date(),
                              safety_supervisor_enabled=False,
                              write_progress_checkpoints=False),
                    self.broker, NoopStrategy(), journal,
                )
                await runtime.initialize()
                await self.order("MKT", quantity=5)
                at = START + timedelta(milliseconds=100)
                await runtime.process_liquidity_bar(bar(at), at=at)
                self.assertEqual(runtime.processed_events, 1)
                self.assertEqual(sum(record.category == "execution" and
                    record.entity_type == "fill" for record in
                    journal.records(runtime.run_id)), 1)
            finally:
                journal.close()

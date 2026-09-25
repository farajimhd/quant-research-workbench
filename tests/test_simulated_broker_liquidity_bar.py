"""Native completed-liquidity-bucket execution, without synthetic tape events."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock

from src.trading_runtime.ibkr_schema import OrderRequest
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from tests.test_trading_runtime import quote, trade


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

    async def test_empty_book_preserves_completed_quote_mark_and_boundary(self):
        at = START + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(bar(at), at=at), [])
        self.assertEqual(self.broker.completed_liquidity_quote("AAPL").ask_price, 10.0)
        self.assertEqual(self.broker._bar_marks_by_ticker["AAPL"], 10.0)
        self.assertEqual(self.broker._bar_boundaries["AAPL"], at)
        with self.assertRaisesRegex(ValueError, "must advance"):
            await self.broker.on_liquidity_bar(bar(at), at=at)
        stale_at = at + timedelta(milliseconds=1100)
        stale = bar(stale_at, quote_age_us=1_100_000)
        stale.update(price_valid=0, extremes_valid=0, close_int=0)
        self.assertEqual(await self.broker.on_liquidity_bar(stale, at=stale_at), [])
        self.assertIsNone(self.broker.completed_liquidity_quote("AAPL"))
        self.assertNotIn("AAPL", self.broker._bar_marks_by_ticker)

    async def test_other_ticker_bucket_does_not_scan_global_order_book(self):
        await self.order("MKT", quantity=5)
        at = START + timedelta(milliseconds=100)
        other = {**bar(at), "ticker": "MSFT"}
        self.broker._sorted_orders = Mock(side_effect=AssertionError("global order scan"))
        self.assertEqual(await self.broker.on_liquidity_bar(other, at=at), [])
        self.assertEqual(len(await self.broker.on_liquidity_bar(bar(at), at=at)), 1)

    async def test_completed_orders_and_flat_positions_leave_hot_ticker_index(self):
        await self.order("MKT", quantity=5, oid="entry")
        first = START + timedelta(milliseconds=100)
        self.assertEqual(len(await self.broker.on_liquidity_bar(bar(first), at=first)), 1)
        await self.order("MKT", side="SELL", quantity=5, oid="exit")
        second = first + timedelta(milliseconds=100)
        self.assertEqual(len(await self.broker.on_liquidity_bar(bar(second), at=second)), 1)
        self.assertNotIn("AAPL", self.broker._position_conids_by_ticker)
        third = second + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(bar(third), at=third), [])
        self.assertNotIn("AAPL", self.broker._orders_by_ticker)
        self.assertEqual(len(self.broker._orders), 2)

        restored = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST,
            initial_time=START)
        await restored.initialize()
        restored.restore_checkpoint_state(self.broker.checkpoint_state())
        self.assertNotIn("AAPL", restored._orders_by_ticker)
        self.assertNotIn("AAPL", restored._position_conids_by_ticker)
        self.assertEqual(len(restored._orders), 2)

    async def test_restored_ticker_index_keeps_fill_priority(self):
        await self.order("MKT", quantity=5, oid="first")
        await self.order("MKT", quantity=5, oid="second")
        restored = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST,
            initial_time=START)
        await restored.initialize()
        restored.restore_checkpoint_state(self.broker.checkpoint_state())
        at = START + timedelta(milliseconds=100)
        fills = await restored.on_liquidity_bar(bar(at, ask_size=6), at=at)
        self.assertEqual([(fill.order_ref, fill.size) for fill in fills],
                         [("first", 5.0), ("second", 1.0)])

    async def test_ticker_quote_checkpoint_is_not_derivable_from_conid_index(self):
        observed = replace(quote(bid=9.99, ask=10.0),
                           raw={}, ingest_ts=START, ts=START)
        self.assertEqual(self.broker.observe_market_event(observed), 0)
        state = self.broker.checkpoint_state()
        self.assertEqual(state["quotes"], {})
        self.assertIn("AAPL", state["quotes_by_ticker"])
        restored = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST,
            initial_time=START)
        await restored.initialize()
        restored.restore_checkpoint_state(state)
        self.assertEqual(restored.checkpoint_state(), state)

    async def test_checkpoint_restores_canonical_stop_trigger_metadata(self):
        request = OrderRequest(
            acctId="TEST", conid=265598, cOID="stop-with-lineage",
            ticker="AAPL", orderType="STP", side="BUY", quantity=5,
            price=10.5, auxPrice=10.5, raw={
                "canonical_run_id": "backtest-run",
                "canonical_strategy_id": "strategy-1",
                "canonical_strategy_revision": 7,
                "canonical_metadata": {"stop_trigger_source": "eligible_trade"},
            })
        await self.broker.place_orders("TEST", [request])
        self.assertNotIn("canonical_metadata", request.to_cpapi())
        checkpoint = self.broker.checkpoint_state()
        self.assertEqual(checkpoint["orders"][0]["request"]["canonical_metadata"],
                         {"stop_trigger_source": "eligible_trade"})
        restored = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST, initial_time=START)
        await restored.initialize()
        restored.restore_checkpoint_state(checkpoint)
        self.assertEqual(restored._orders["1"].request.raw, request.raw)
        self.assertEqual(restored.checkpoint_state(), checkpoint)

    async def test_market_order_uses_completed_quote_and_displayed_size(self):
        await self.order("MKT", quantity=30)
        at = START + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(bar(at, ask_size=8), at=at)
        self.assertEqual([(fill.price, fill.size) for fill in fills], [(10.0, 8.0)])
        self.assertEqual(await self.broker.match_current_orders("AAPL", at), [])

    async def test_unambiguous_quote_only_bucket_matches_event_fill(self):
        at = START + timedelta(milliseconds=100)
        snapshot = bar(at, ask_size=8, quote_age_us=0)
        snapshot.update(event_count=1, first_event_us=snapshot["last_event_us"],
                        price_valid=0, extremes_valid=0, close_int=0,
                        low_int=0, high_int=0, execution_volume=0)
        await self.order("MKT", quantity=5)
        bar_fills = await self.broker.on_liquidity_bar(snapshot, at=at)

        event_broker = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST, initial_time=START)
        await event_broker.initialize()
        await event_broker.place_orders("TEST", [OrderRequest(
            acctId="TEST", conid=265598, cOID="event-order", ticker="AAPL",
            orderType="MKT", side="BUY", quantity=5)])
        event_at = at - timedelta(microseconds=1)
        event_fills = await event_broker.on_market_event(replace(
            quote(bid=9.99, ask=10.0, ask_size=8),
            ts=event_at, ingest_ts=event_at))
        self.assertEqual([(fill.price, fill.size) for fill in bar_fills],
                         [(fill.price, fill.size) for fill in event_fills])

    async def test_event_at_boundary_belongs_to_next_bucket(self):
        at = START + timedelta(milliseconds=100)
        invalid = bar(at)
        invalid["last_event_us"] = int(at.timestamp() * 1_000_000)
        with self.assertRaisesRegex(ValueError, "completed bucket"):
            await self.broker.on_liquidity_bar(invalid, at=at)

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

    async def test_resting_limit_matches_unambiguous_quote_then_trade_reference(self):
        await self.order("LMT", price=9.9, quantity=30)
        at = START + timedelta(milliseconds=100)
        fixed = await self.broker.on_liquidity_bar(
            bar(at, low=9.8, execution_volume=40), at=at)
        reference = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST, initial_time=START)
        await reference.initialize()
        await reference.on_market_event(replace(
            quote(bid=9.99, ask=10.0), ts=START, ingest_ts=START))
        await reference.place_orders("TEST", [OrderRequest(
            acctId="TEST", conid=265598, cOID="reference-limit", ticker="AAPL",
            orderType="LMT", side="BUY", quantity=30, price=9.9)])
        tape_at = at - timedelta(microseconds=1)
        event = await reference.on_market_event(replace(
            trade(price=9.8, size=40), ts=tape_at, ingest_ts=tape_at,
            participant_ts=tape_at))
        self.assertEqual([(row.price, row.size) for row in fixed],
                         [(row.price, row.size) for row in event])

    async def test_stop_limit_waits_for_next_bucket_and_its_limit(self):
        await self.order("STOP_LIMIT", price=10.05, stop=10.1)
        first = START + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(
            bar(first, high=10.2), at=first), [])
        second = first + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(
            bar(second, bid=10.19, ask=10.2, low=10.06, high=10.2), at=second), [])
        third = second + timedelta(milliseconds=100)
        filled = await self.broker.on_liquidity_bar(
            bar(third, bid=10.03, ask=10.04, low=10.03, high=10.04), at=third)
        self.assertEqual([(row.price, row.size) for row in filled], [(10.04, 10.0)])

    async def test_quote_age_cutoff_is_completed_boundary_not_last_event(self):
        await self.order("MKT", quantity=2)
        first = START + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(
            bar(first, quote_age_us=1_000_000), at=first), [])
        second = first + timedelta(milliseconds=100)
        filled = await self.broker.on_liquidity_bar(
            bar(second, quote_age_us=999_999), at=second)
        self.assertEqual(sum(row.size for row in filled), 2)

    async def test_shared_displayed_liquidity_is_partial_and_deterministic(self):
        await self.order("MKT", quantity=10, oid="first")
        await self.order("MKT", quantity=10, oid="second")
        # Simulate a worker reconstructing the order map in a different
        # insertion order; broker priority remains immutable order ID order.
        self.broker._orders = dict(reversed(tuple(self.broker._orders.items())))
        first = START + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(
            bar(first, ask_size=7), at=first)
        self.assertEqual([(row.order_id, row.size) for row in fills], [("1", 7.0)])
        second = first + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(
            bar(second, ask_size=7), at=second)
        self.assertEqual([(row.order_id, row.size) for row in fills],
                         [("1", 3.0), ("2", 4.0)])

    async def test_child_activated_by_parent_fill_waits_for_next_bucket(self):
        parent = OrderRequest(
            acctId="TEST", conid=265598, cOID="entry", ticker="AAPL",
            orderType="MKT", side="BUY", quantity=5)
        child = OrderRequest(
            acctId="TEST", conid=265598, cOID="exit", parentId="entry",
            ticker="AAPL", orderType="LMT", side="SELL", quantity=5,
            price=9.9)
        await self.broker.place_orders("TEST", [parent, child])
        first = START + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(bar(first), at=first)
        self.assertEqual([row.order_id for row in fills], ["1"])
        second = first + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(bar(second), at=second)
        self.assertEqual([row.order_id for row in fills], ["2"])

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
        completed = self.broker.completed_liquidity_quote("AAPL")
        self.assertIsNotNone(completed)
        self.assertEqual(completed.source, "arte.liquidity_100ms_v1")
        self.assertEqual(completed.ts, first - timedelta(microseconds=10_001))
        second = first + timedelta(milliseconds=100)
        no_quote = bar(second)
        no_quote.update(quote_valid=0, quote_timestamp_us=0)
        await self.broker.on_liquidity_bar(no_quote, at=second)
        self.assertIsNone(self.broker.completed_liquidity_quote("AAPL"))
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
                quote = await runtime.process_liquidity_bar(bar(at), at=at)
                self.assertIsNotNone(quote)
                self.assertEqual(quote.ts, at - timedelta(microseconds=10_001))
                self.assertEqual(runtime.processed_events, 1)
                self.assertEqual(sum(record.category == "execution" and
                    record.entity_type == "fill" for record in
                    journal.records(runtime.run_id)), 1)
            finally:
                journal.close()

    async def test_runtime_records_bar_fill_with_clickhouse_only_buffer(self):
        class NoopStrategy:
            strategy_id = "bar-test"
            revision = 1
            automatic = True

        run_id = "00000000-0000-0000-0000-000000000123"
        journal = BacktestMemoryJournal(run_id=run_id)
        runtime = TradingRuntime(
            RunConfig(RunMode.BACKTEST, "bar-test", 1, ("TEST",), START.date(),
                      run_id=run_id, safety_supervisor_enabled=False,
                      write_progress_checkpoints=False),
            self.broker, NoopStrategy(), journal,
        )
        await runtime.initialize()
        await self.order("MKT", quantity=5)
        at = START + timedelta(milliseconds=100)
        await runtime.process_liquidity_bar(bar(at), at=at)
        assert sum(record.category == "execution" and record.entity_type == "fill"
                   for record in journal.records(run_id)) == 1
        assert journal.latest_sequence(run_id) > 0
        journal.close()

    async def test_order_free_bucket_skips_oms_scans_but_keeps_quote(self):
        class NoopStrategy:
            strategy_id = "bar-test"
            revision = 1
            automatic = True

        run_id = "00000000-0000-0000-0000-000000000124"
        journal = BacktestMemoryJournal(run_id=run_id)
        runtime = TradingRuntime(
            RunConfig(RunMode.BACKTEST, "bar-test", 1, ("TEST",), START.date(),
                      run_id=run_id, safety_supervisor_enabled=False,
                      write_progress_checkpoints=False),
            self.broker, NoopStrategy(), journal,
        )
        await runtime.initialize()
        from types import SimpleNamespace
        runtime.order_manager = SimpleNamespace(has_managed_groups=False)
        runtime.order_manager.on_market_snapshot = Mock()
        runtime.order_manager.enforce_entry_body_triggers = AsyncMock(
            side_effect=AssertionError("OMS scan"))
        runtime.order_manager.advance_adaptive_execution = AsyncMock(
            side_effect=AssertionError("OMS scan"))
        runtime.order_manager.expire_entry_deadlines = AsyncMock(
            side_effect=AssertionError("OMS scan"))
        self.broker.validate_liquidity_bar = Mock(
            wraps=self.broker.validate_liquidity_bar)
        at = START + timedelta(milliseconds=100)
        completed = await runtime.process_liquidity_bar(bar(at), at=at)
        assert self.broker.validate_liquidity_bar.call_count == 1
        assert completed is not None and completed.ticker == "AAPL"
        runtime.order_manager.on_market_snapshot.assert_called_once()
        first = runtime.execution_market_data.snapshot("AAPL")
        assert first is not None
        assert first.observed_at == at - timedelta(microseconds=10_001)
        assert runtime.processed_events == 1
        later = START + timedelta(seconds=2)
        carried = await runtime.process_liquidity_bar(
            bar(later, quote_age_us=750_000), at=later)
        assert carried is not None
        expected_source_time = later - timedelta(microseconds=750_001)
        assert carried.ts == expected_source_time
        assert runtime.execution_market_data.snapshot("AAPL").observed_at == expected_source_time
        assert runtime.order_manager.on_market_snapshot.call_count == 2
        next_boundary = later + timedelta(milliseconds=100)
        same_source = bar(next_boundary, quote_age_us=850_000)
        await runtime.process_liquidity_bar(same_source, at=next_boundary)
        assert runtime.order_manager.on_market_snapshot.call_count == 2
        assert runtime.execution_market_data.snapshot("AAPL").observed_at == expected_source_time
        invalid_at = next_boundary + timedelta(milliseconds=100)
        invalid = bar(invalid_at)
        invalid["quote_timestamp_us"] = invalid["last_event_us"] + 1
        with self.assertRaisesRegex(ValueError, "invalid quote provenance"):
            await runtime.process_liquidity_bar(invalid, at=invalid_at)
        assert self.broker.validate_liquidity_bar.call_count == 4
        assert runtime.order_manager.on_market_snapshot.call_count == 2
        assert runtime.order_manager.enforce_entry_body_triggers.await_count == 0
        assert runtime.processed_events == 3
        assert self.broker._bar_boundaries["AAPL"] == next_boundary
        journal.close()

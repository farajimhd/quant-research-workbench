"""Native completed-liquidity-bucket execution, without synthetic tape events."""
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.ibkr_normalizer import normalize_order
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    TABLES as BROKER_MATCH_TABLES, project_broker_match_snapshot,
    BrokerMatchHead, verify_broker_match_snapshot,
    load_attested_broker_match_snapshot,
    load_unattested_broker_match_snapshot,
)
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_v4_broker_quote_restore import load_completed_broker_quotes
from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
from src.backend.backtest_v4_execution_restore import reconstruct_broker_executions
from tests.test_trading_runtime import quote, trade


START = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


def test_incremental_commission_is_exact_on_journal_decimal_grid():
    broker = SimulatedBrokerAdapter(["DU1"])
    order = SimpleNamespace(filled=203.0, commission_paid=1.01)
    incremental = broker._incremental_order_commission(order)
    assert Decimal(str(incremental)) == Decimal("0.005")
    assert Decimal(str(order.commission_paid)) == Decimal("1.015")


def bar(at, *, bid=9.99, ask=10.0, bid_size=100, ask_size=100,
        low=9.98, high=10.02, execution_volume=100, quote_age_us=10_000,
        execution_price_levels=None):
    last_us = int(at.timestamp() * 1_000_000) - 1
    local = at.astimezone(ZoneInfo("America/New_York"))
    bucket_index = ((local.hour * 3_600 + local.minute * 60 + local.second)
                    * 1_000 + local.microsecond // 1_000) // 100 - 1
    row = {
        "ticker": "AAPL", "resolution_ms": 100,
        "bucket_index": bucket_index,
        "event_count": 3, "last_event_us": last_us,
        "quote_valid": 1, "quote_timestamp_us": last_us - quote_age_us,
        "bid_int": round(bid * 10_000), "ask_int": round(ask * 10_000),
        "bid_size": bid_size, "ask_size": ask_size,
        "price_valid": 1, "extremes_valid": 1,
        "close_int": round(ask * 10_000),
        "low_int": round(low * 10_000), "high_int": round(high * 10_000),
        "execution_volume": execution_volume,
    }
    if execution_price_levels is not None:
        row["execution_price_levels"] = tuple(
            {"price_int": round(price * 10_000), "volume": volume}
            for price, volume in execution_price_levels)
    return row


class LiquidityBarBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_bar_canonical_orders_reuse_only_unchanged_projections(self):
        broker = SimulatedBrokerAdapter(
            ["TEST"], mode=RunMode.BACKTEST, initial_time=START,
            fixed_bar_mode=True,
        )
        await broker.initialize()
        request = OrderRequest(
            acctId="TEST", conid=1, orderType="LMT", side="BUY",
            quantity=10, ticker="AAPL", price=10, cOID="cache-test",
            raw={"canonical_strategy_id": "strategy-one"},
        )
        placed = await broker.place_orders("TEST", [request])
        order_id = placed[0]["order_id"]
        with patch("src.trading_runtime.simulated_broker.normalize_order",
                   wraps=normalize_order) as projection:
            first = await broker.canonical_orders("TEST")
            second = await broker.canonical_orders("TEST")
            self.assertEqual(projection.call_count, 1)
            self.assertIs(first[0], second[0])
            request.raw["canonical_strategy_id"] = "external-mutation"
            self.assertEqual((await broker.canonical_orders("TEST"))[0], first[0])

            await broker.modify_order("TEST", order_id,
                                      replace(request, price=10.5))
            modified = await broker.canonical_orders("TEST")
            self.assertEqual(projection.call_count, 2)
            expected_modified = normalize_order(
                broker._orders[order_id].snapshot().to_cpapi(), "TEST")
            self.assertEqual(replace(modified[0], received_at=expected_modified.received_at),
                             expected_modified)
            self.assertNotEqual(modified[0], first[0])

            await broker.modify_order(
                "TEST", order_id, replace(request, price=10.5,
                                          raw={"canonical_strategy_id": "new-lineage"}))
            replaced_request = await broker.canonical_orders("TEST")
            self.assertEqual(projection.call_count, 3)
            self.assertEqual(replaced_request[0].limit_price, modified[0].limit_price)
            self.assertEqual(replaced_request[0].raw["canonical_strategy_id"],
                             "new-lineage")

            restored = SimulatedBrokerAdapter(
                ["TEST"], mode=RunMode.BACKTEST, initial_time=START,
                fixed_bar_mode=True,
            )
            await restored.initialize()
            restored.restore_checkpoint_state(broker.broker_match_snapshot_state())
            self.assertEqual(restored._canonical_order_cache, {})
            self.assertEqual((await restored.canonical_orders("TEST"))[0].limit_price,
                             replaced_request[0].limit_price)

            before_cancel_calls = projection.call_count
            await broker.cancel_order("TEST", order_id)
            cancelled = await broker.canonical_orders("TEST")
            self.assertEqual(projection.call_count, before_cancel_calls + 1)
            self.assertTrue(cancelled[0].terminal)
            expected_cancelled = normalize_order(
                broker._orders[order_id].snapshot().to_cpapi(), "TEST")
            self.assertEqual(replace(cancelled[0], received_at=expected_cancelled.received_at),
                             expected_cancelled)

    async def test_empty_fixed_bar_horizon_has_checkpoint_without_market_rows(self):
        broker = SimulatedBrokerAdapter(
            ["TEST"], mode=RunMode.BACKTEST, initial_time=START,
            fixed_bar_mode=True,
        )
        await broker.initialize()
        snapshot = broker.broker_match_snapshot_state()
        self.assertTrue(snapshot["bar_mode"])
        self.assertEqual(snapshot["bar_boundaries"], {})
        self.assertEqual(snapshot["orders"], [])
        restored = SimulatedBrokerAdapter(
            ["TEST"], mode=RunMode.BACKTEST, initial_time=START,
        )
        restored.restore_checkpoint_state(snapshot)
        self.assertEqual(restored.broker_match_snapshot_state(), snapshot)

    async def test_fixed_bar_mode_cannot_enable_replay(self):
        with self.assertRaisesRegex(ValueError, "requires Backtest"):
            SimulatedBrokerAdapter(["TEST"], fixed_bar_mode=True)

    async def test_fixed_bar_mode_rejects_direct_event_observation(self):
        broker = SimulatedBrokerAdapter(
            ["TEST"], mode=RunMode.BACKTEST, initial_time=START,
            fixed_bar_mode=True,
        )
        await broker.initialize()
        with self.assertRaisesRegex(RuntimeError, "cannot mix with market events"):
            broker.observe_market_event(trade(price=10.0, size=100))
        self.assertEqual(broker._trades_by_ticker, {})

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

    async def test_bucket_identity_is_checked_before_broker_mutation(self):
        at = START + timedelta(milliseconds=100)
        wrong = bar(at)
        wrong["bucket_index"] += 1
        with self.assertRaisesRegex(ValueError, "bucket identity"):
            await self.broker.on_liquidity_bar(wrong, at=at)
        with self.assertRaisesRegex(ValueError, "bucket identity"):
            await self.broker.on_liquidity_bar(bar(at), at=at + timedelta(microseconds=1))
        self.assertFalse(self.broker._bar_mode)
        self.assertNotIn("AAPL", self.broker._bar_boundaries)

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

    async def test_completed_bucket_consumption_is_not_needed_in_next_bucket(self):
        await self.order("MKT", quantity=10, oid="partial")
        first = START + timedelta(milliseconds=100)
        self.assertEqual(len(await self.broker.on_liquidity_bar(
            bar(first, ask_size=4), at=first)), 1)
        checkpoint = self.broker.checkpoint_state()
        self.assertTrue(checkpoint["liquidity_consumed"])
        # The bucket is complete and a resumed fixed run starts at its next
        # boundary. Consumption is scoped by bucket identity, not carried
        # capacity; persisting it would duplicate the fill journal.
        compact = {**checkpoint, "liquidity_consumed": {}}
        restored = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST,
            initial_time=START)
        await restored.initialize()
        restored.restore_checkpoint_state(compact)
        second = first + timedelta(milliseconds=100)
        next_row = bar(second, ask_size=3)
        original_fills = await self.broker.on_liquidity_bar(next_row, at=second)
        resumed_fills = await restored.on_liquidity_bar(next_row, at=second)
        self.assertEqual(original_fills, resumed_fills)
        self.assertEqual(self.broker._cash, restored._cash)
        self.assertEqual(self.broker._positions, restored._positions)
        self.assertEqual(self.broker._orders, restored._orders)

    async def test_open_order_match_state_projects_to_normalized_rows(self):
        await self.order("MKT", quantity=10, oid="partial")
        at = START + timedelta(milliseconds=100)
        await self.broker.on_liquidity_bar(bar(at, ask_size=4), at=at)
        day = date(2026, 8, 18)
        boundary_ms = round((at - market_day_boundary(day, 0)).total_seconds() * 1000)
        rows = project_broker_match_snapshot(
            run_id="backtest:one", session_date=day,
            checkpoint_sequence=42, boundary_ms=boundary_ms,
            state=self.broker.checkpoint_state())
        compact_rows = project_broker_match_snapshot(
            run_id="backtest:one", session_date=day,
            checkpoint_sequence=42, boundary_ms=boundary_ms,
            state=self.broker.broker_match_snapshot_state())
        self.assertEqual(compact_rows, rows)
        self.assertNotIn("executions", self.broker.broker_match_snapshot_state())
        self.assertEqual(rows.snapshot["account_count"], 1)
        self.assertEqual(rows.snapshot["position_count"], 1)
        self.assertEqual(rows.snapshot["open_order_count"], 1)
        self.assertEqual(rows.snapshot["ticker_count"], 1)
        self.assertEqual(rows.open_orders[0]["filled"], 4.0)
        self.assertEqual(rows.tickers[0]["last_boundary_ms"], boundary_ms)
        self.assertEqual(verify_broker_match_snapshot(rows), rows)
        class Reader:
            def execute(self, sql):
                assert sql.lstrip().startswith("SELECT ")
                assert "arte.liquidity_100ms_v1" in sql
                assert "toUUID('00000000-0000-0000-0000-000000000001')" in sql
                return json.dumps({
                    "session_date": day.isoformat(), "ticker": "AAPL",
                    "bucket_index": bar(at)["bucket_index"],
                    "event_count": 3, "last_event_us": bar(at)["last_event_us"],
                    "quote_timestamp_us": bar(at)["quote_timestamp_us"],
                    "quote_valid": 1, "bid_int": 99900, "ask_int": 100000,
                    "bid_size": 100, "ask_size": 4,
                })

        plan = CertifiedMarketDayPlan(
            ExecutionInterval.fixed(100), "a" * 64, "b" * 64,
            (day.isoformat(),), ("AAPL",),
            (MarketDayUnit("a" * 64, day.isoformat(), "AAPL",
                           "broker_100ms",
                           "00000000-0000-0000-0000-000000000001",
                           "c" * 64, 1, "d" * 64),), (100,), "e" * 64)
        quote = load_completed_broker_quotes(Reader(), plan=plan, broker=rows)
        self.assertEqual(quote["AAPL"].ask, 10.0)
        self.assertEqual(quote["AAPL"].quote_timestamp_us,
                         rows.tickers[0]["quote_timestamp_us"])
        recovered_state = reconstruct_broker_match_state(
            rows, requests_by_broker_id={
                order.order_id: order.request
                for order in self.broker._orders.values()
                if order.status.value == rows.open_orders[0]["status"]},
            quotes=quote)
        original_execution = (await self.broker.trades())[0]
        original_request = self.broker._orders[original_execution.order_id].request
        recovered_state["executions"] = reconstruct_broker_executions(
            (dict(sequence=7, execution_id=original_execution.execution_id,
                  account_id=original_execution.account,
                  client_order_id=original_execution.order_ref,
                  broker_order_id=original_execution.order_id,
                  conid=original_execution.conid, ticker=original_execution.symbol,
                  side=original_execution.side, quantity=original_execution.size,
                  price=original_execution.price, currency=original_execution.currency,
                  source_event_time=original_execution.trade_time.isoformat()),),
            (dict(sequence=8, execution_id=original_execution.execution_id,
                  account_id=original_execution.account,
                  commission=original_execution.commission,
                  currency=original_execution.currency, status="final"),),
            requests_by_coid={original_request.cOID: original_request},
            coid_by_broker_id={original_execution.order_id: original_request.cOID},
            next_execution_id=recovered_state["next_execution_id"])
        restored = SimulatedBrokerAdapter(
            ["TEST"], self.broker.config, mode=RunMode.BACKTEST,
            initial_time=START)
        await restored.initialize()
        restored.restore_checkpoint_state(recovered_state)
        self.assertEqual(await restored.trades(), await self.broker.trades())
        self.assertEqual(restored.broker_match_snapshot_state(),
                         self.broker.broker_match_snapshot_state())
        # OCA partial fills can shrink the broker's resting sibling without
        # changing the OMS request. Recovery must honor the broker quantity.
        reduced_state = self.broker.broker_match_snapshot_state()
        reduced_state["orders"][0]["request"]["quantity"] = 8.0
        reduced_rows = project_broker_match_snapshot(
            run_id="backtest:one", session_date=day,
            checkpoint_sequence=43, boundary_ms=boundary_ms,
            state=reduced_state)
        reduced = reconstruct_broker_match_state(
            reduced_rows,
            requests_by_broker_id={
                order.order_id: order.request for order in self.broker._orders.values()
                if order.status.value == rows.open_orders[0]["status"]},
            quotes=quote)
        self.assertEqual(reduced["orders"][0]["request"]["quantity"], 8.0)
        next_at = at + timedelta(milliseconds=100)
        next_row = bar(next_at, ask_size=20)
        original_fills = await self.broker.on_liquidity_bar(next_row, at=next_at)
        recovered_fills = await restored.on_liquidity_bar(next_row, at=next_at)
        self.assertEqual(original_fills, recovered_fills)
        with self.assertRaisesRegex(RuntimeError, "exact quote identities"):
            reconstruct_broker_match_state(
                rows, requests_by_broker_id={
                    row["broker_order_id"]: self.broker._orders[
                        row["broker_order_id"]].request
                    for row in rows.open_orders}, quotes={})
        class MissingReader(Reader):
            def execute(self, sql):
                super().execute(sql)
                return ""

        with self.assertRaisesRegex(RuntimeError, "lacks exact liquidity rows"):
            load_completed_broker_quotes(MissingReader(), plan=plan, broker=rows)

        class ChangedReader(Reader):
            def execute(self, sql):
                value = json.loads(super().execute(sql))
                value["quote_timestamp_us"] -= 1
                return json.dumps(value)

        with self.assertRaisesRegex(RuntimeError, "differs from completed bucket"):
            load_completed_broker_quotes(ChangedReader(), plan=plan, broker=rows)
        stored = replace(
            rows,
            snapshot={**rows.snapshot, "initial_time": rows.snapshot[
                "initial_time"].replace("T", " ").removesuffix("+00:00")},
            open_orders=({**rows.open_orders[0], "submitted_at": rows.open_orders[0][
                "submitted_at"].replace("T", " ").removesuffix("+00:00")},),
        )
        self.assertEqual(verify_broker_match_snapshot(stored), rows)
        with self.assertRaisesRegex(ValueError, "child hash differs"):
            verify_broker_match_snapshot(replace(
                stored, open_orders=({**stored.open_orders[0], "filled": 5.0},)))
        with self.assertRaisesRegex(ValueError, "family seal"):
            verify_broker_match_snapshot(replace(stored, open_orders=()))
        class Reader:
            def execute(self, sql):
                self.last_sql = sql
                family = next(table.name for table in BROKER_MATCH_TABLES
                              if f"arte.{table.name} " in sql)
                values = {
                    BROKER_MATCH_TABLES[0].name: (stored.snapshot,),
                    BROKER_MATCH_TABLES[1].name: stored.accounts,
                    BROKER_MATCH_TABLES[2].name: stored.positions,
                    BROKER_MATCH_TABLES[3].name: stored.open_orders,
                    BROKER_MATCH_TABLES[4].name: stored.tickers,
                    BROKER_MATCH_TABLES[5].name: stored.marks,
                }[family]
                from decimal import Decimal
                columns = dict(next(table for table in BROKER_MATCH_TABLES
                                    if table.name == family).columns)
                return "\n".join(json.dumps({
                    **row,
                    **{name: format(Decimal(str(row[name])), ".18f")
                       for name, kind in columns.items()
                       if kind == "Decimal(38, 18)"}
                }) for row in values)

        reader = Reader()
        self.assertEqual(load_unattested_broker_match_snapshot(
            reader, run_id="backtest:one", checkpoint_sequence=42), rows)
        self.assertTrue(reader.last_sql.startswith("SELECT "))
        batch = "00000000-0000-0000-0000-000000000042"
        prefix = SimpleNamespace(status="running", last_sequence=42,
                                 batch_ids=(batch,), last_batch_id=batch)
        selected = BrokerMatchHead("backtest:one", 42, batch,
                                   rows.snapshot["content_hash"], 0)
        keeper = SimpleNamespace(read_head=lambda **_kwargs: selected)
        cursor = {"run_id": "backtest:one", "event_sequence": 42,
                  "batch_id": batch, "boundary_ms": boundary_ms,
                  "session_date": day.isoformat()}
        with patch("src.trading_runtime.arte_journal_commit_v4.load_verified_v4_prefix",
                   return_value=prefix), patch(
                "src.trading_runtime.arte_journal_projection.load_latest_backtest_cursor",
                return_value=cursor):
            self.assertEqual(load_attested_broker_match_snapshot(
                reader, keeper, run_id="backtest:one",
                checkpoint_sequence=42), rows)
            changed = SimpleNamespace(read_head=lambda **_kwargs: replace(
                selected, snapshot_hash="0" * 64))
            with self.assertRaisesRegex(RuntimeError, "differs from selected cursor"):
                load_attested_broker_match_snapshot(
                    reader, changed, run_id="backtest:one",
                    checkpoint_sequence=42)
        self.assertTrue(all("live_market_ssd" in table.ddl()
                            for table in BROKER_MATCH_TABLES))
        self.assertTrue(all("json" not in name and "blob" not in name
                            for table in BROKER_MATCH_TABLES
                            for name, _ in table.columns))

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

    async def test_fixed_bar_slippage_remains_exactly_journal_representable(self):
        from src.trading_runtime.arte_journal_projection import _exact_decimal

        broker = SimulatedBrokerAdapter(
            ["TEST"], replace(self.broker.config, market_slippage_bps=5.0),
            mode=RunMode.BACKTEST, initial_time=START)
        await broker.initialize()
        await broker.place_orders("TEST", [OrderRequest(
            acctId="TEST", conid=265598, cOID="slipped-entry", ticker="AAPL",
            orderType="MKT", side="BUY", quantity=1)])
        at = START + timedelta(milliseconds=100)
        fill, = await broker.on_liquidity_bar(bar(at, ask=10.01), at=at)
        self.assertEqual(_exact_decimal(fill.price, field="fill.price"),
                         "10.0150050000")

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
            bar(at, low=9.8, execution_volume=40,
                execution_price_levels=((9.8, 40),)), at=at)
        self.assertEqual([(fill.price, fill.size) for fill in fills], [(9.9, 10.0)])

    async def test_resting_limit_matches_unambiguous_quote_then_trade_reference(self):
        await self.order("LMT", price=9.9, quantity=30)
        at = START + timedelta(milliseconds=100)
        fixed = await self.broker.on_liquidity_bar(
            bar(at, low=9.8, execution_volume=40,
                execution_price_levels=((9.8, 40),)), at=at)
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

    async def test_passive_limit_refuses_uncoupled_low_and_eligible_volume(self):
        await self.order("LMT", price=9.9, quantity=30)
        at = START + timedelta(milliseconds=100)
        with self.assertRaisesRegex(RuntimeError, "certified eligible price-volume"):
            await self.broker.on_liquidity_bar(
                bar(at, low=9.8, execution_volume=40), at=at)
        self.assertNotIn("AAPL", self.broker._bar_boundaries)
        self.assertNotIn("AAPL", self.broker._quotes_by_ticker)

    async def test_passive_limit_uses_only_eligible_price_volume_at_limit(self):
        await self.order("LMT", price=9.9, quantity=30)
        at = START + timedelta(milliseconds=100)
        fills = await self.broker.on_liquidity_bar(
            bar(at, low=9.8, execution_volume=40,
                execution_price_levels=((9.8, 5), (9.95, 35))), at=at)
        self.assertEqual([(fill.price, fill.size) for fill in fills], [(9.9, 1.0)])

    async def test_price_levels_must_reconcile_before_broker_state_changes(self):
        at = START + timedelta(milliseconds=100)
        malformed = bar(at, execution_volume=40,
                        execution_price_levels=((9.8, 5), (9.95, 30)))
        with self.assertRaisesRegex(ValueError, "differ from eligible volume"):
            await self.broker.on_liquidity_bar(malformed, at=at)
        self.assertNotIn("AAPL", self.broker._bar_boundaries)

    async def test_stop_limit_waits_for_next_bucket_and_its_limit(self):
        await self.order("STOP_LIMIT", price=10.05, stop=10.1)
        first = START + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(
            bar(first, high=10.2), at=first), [])
        second = first + timedelta(milliseconds=100)
        self.assertEqual(await self.broker.on_liquidity_bar(
            bar(second, bid=10.19, ask=10.2, low=10.06, high=10.2,
                execution_price_levels=((10.1, 100),)), at=second), [])
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

    async def test_boundary_validates_all_tickers_and_wakes_oms_once(self):
        class NoopStrategy:
            strategy_id = "bar-test"
            revision = 1
            automatic = True

        run_id = "00000000-0000-0000-0000-000000000125"
        journal = BacktestMemoryJournal(run_id=run_id)
        runtime = TradingRuntime(
            RunConfig(RunMode.BACKTEST, "bar-test", 1, ("TEST",), START.date(),
                      run_id=run_id, safety_supervisor_enabled=False,
                      write_progress_checkpoints=False),
            self.broker, NoopStrategy(), journal,
        )
        await runtime.initialize()
        from types import SimpleNamespace
        runtime.order_manager = SimpleNamespace(
            has_managed_groups=True, on_market_snapshot=Mock(),
            enforce_entry_body_triggers=AsyncMock(),
            advance_adaptive_execution=AsyncMock(),
            expire_entry_deadlines=AsyncMock(),
        )
        at = START + timedelta(milliseconds=100)
        first, second = bar(at), {**bar(at), "ticker": "MSFT"}
        invalid = {**second, "quote_timestamp_us": second["last_event_us"] + 1}
        with self.assertRaisesRegex(ValueError, "invalid quote provenance"):
            await runtime.process_liquidity_boundary((first, invalid), at=at)
        self.assertEqual(runtime.processed_events, 0)
        self.assertIsNone(runtime.execution_market_data.snapshot("AAPL"))
        self.assertEqual(runtime.order_manager.on_market_snapshot.call_count, 0)
        self.assertNotIn("AAPL", self.broker._bar_boundaries)
        quotes = await runtime.process_liquidity_boundary((first, second), at=at)
        self.assertEqual(set(quotes), {"AAPL", "MSFT"})
        self.assertEqual(runtime.processed_events, 2)
        self.assertEqual(runtime.order_manager.on_market_snapshot.call_count, 2)
        runtime.order_manager.enforce_entry_body_triggers.assert_awaited_once_with(at)
        runtime.order_manager.advance_adaptive_execution.assert_awaited_once_with(at)
        runtime.order_manager.expire_entry_deadlines.assert_awaited_once_with(at)
        journal.close()

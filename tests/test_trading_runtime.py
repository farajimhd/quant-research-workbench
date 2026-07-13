from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from services.qmd_history_gateway.store import historical_to_live_compact, row_to_market_event
from src.backend.real_live_trading_service import ibkr_order_payload
from src.market_engine.events import QuoteEvent
from src.trading_runtime.ibkr_schema import OrderRequest, OrderStatus
from src.trading_runtime.clickhouse import TRADING_TABLE_DDL, _specialized_rows
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.orchestrator import historical_run_window
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
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


class JournalTests(unittest.TestCase):
    def test_journal_sequence_checkpoint_strategy_and_outbox_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            journal = TradingJournal(path)
            first = journal.append(run_id="run", category="command", entity_type="order", entity_id="1", payload={"a": 1})
            second = journal.append(run_id="run", category="broker", entity_type="order", entity_id="1", payload={"status": "Submitted"})
            journal.save_checkpoint("run", "cursor", {"events": 2}, TS)
            journal.save_strategy(strategy_id="s", revision=1, name="Strategy", implementation="module:Class", automatic=True, config={"x": 2})
            journal.close()

            reopened = TradingJournal(path)
            self.assertEqual([first.sequence, second.sequence], [1, 2])
            self.assertEqual(len(reopened.pending_outbox()), 2)
            self.assertEqual(reopened.load_checkpoint("run")["state"], {"events": 2})
            self.assertTrue(reopened.strategy("s")["automatic"])
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


class HistoricalContractTests(unittest.TestCase):
    def test_historical_event_mirrors_live_compact_and_decodes_quote(self) -> None:
        row = {
            "ticker": "AAPL", "ordinal": 42, "event_meta": 6, "sip_timestamp_us": int(TS.timestamp() * 1_000_000),
            "price_primary_int": 1001234, "price_secondary_int": 1001200, "size_primary": 20, "size_secondary": 25,
            "exchange_primary": 11, "exchange_secondary": 12, "condition_token_1": 3,
            "condition_token_2": 0, "condition_token_3": 0, "condition_token_4": 0, "condition_token_5": 0,
            "event_date": "2026-07-13",
        }
        compact = historical_to_live_compact(row)
        event = row_to_market_event(row)
        self.assertEqual(compact["schema_version"], 4)
        self.assertEqual(compact["source_sequence"], 42)
        self.assertIsInstance(event, QuoteEvent)
        self.assertAlmostEqual(event.ask_price, 100.1234)
        self.assertAlmostEqual(event.bid_price, 100.12)


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
        return []


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_backtest_anchor_is_exclusive_and_replay_anchor_is_inclusive(self) -> None:
        backtest = historical_run_window(RunMode.BACKTEST, date(2026, 7, 13), session_count=1)
        replay = historical_run_window(RunMode.REPLAY, date(2026, 7, 13))
        self.assertEqual(backtest.sessions, (date(2026, 7, 10),))
        self.assertEqual(replay.sessions, (date(2026, 7, 13),))


if __name__ == "__main__":
    unittest.main()

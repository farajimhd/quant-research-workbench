from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.backend.real_live_trading_service import ibkr_order_payload
from src.backend.qmd_gateway_client import ENRICHED_QMD_TIMEFRAMES, normalize_qmd_family_bar_snapshot
from src.backend.trading_runtime_service import historical_bar_history_before
from src.market_engine.events import QuoteEvent
from src.market_engine.historical_source import _validate_health, event_from_qmd_payload
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
    def test_subsecond_and_five_second_charts_use_enriched_indicator_contract(self) -> None:
        self.assertIn("100ms", ENRICHED_QMD_TIMEFRAMES)
        self.assertIn("5s", ENRICHED_QMD_TIMEFRAMES)

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
            "next_before": "2026-07-10T13:44:00+00:00",
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

        path, params = gateway_get.call_args.args[:2]
        self.assertEqual(path, "/snapshot/chart-bars/AAPL")
        self.assertEqual(params["timeframe"], "100ms")
        self.assertEqual(params["as_of"], "2026-07-10T13:45:00+00:00")
        self.assertEqual(params["before"], "2026-07-10T13:44:00+00:00")
        self.assertEqual(params["indicator_columns"], "bar_start,ema_20")
        self.assertEqual(result["next_before"], "2026-07-10T13:44:00+00:00")
        self.assertTrue(result["has_more_in_session"])
        self.assertEqual(len(result["indicators"]), 1)

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

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_monthly_chart_history_requests_exact_24_month_macro_window(self, gateway_get) -> None:
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
            "source": "market_sip_compact.macro_bars_by_time_symbol",
        }

        result = historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2026, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            before_bar=None,
            ticker="AAPL",
            timeframe="1mo",
            row_limit=5_000,
        )

        path, params = gateway_get.call_args.args[:2]
        self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
        self.assertEqual(params["timeframe"], "1mo")
        self.assertEqual(params["start"], "2024-08-01T00:00:00+00:00")
        self.assertEqual(result["history"][0]["volume"], 10_000.0)
        self.assertFalse(result["indicators_available"])

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_daily_chart_history_requests_exact_180_day_macro_window(self, gateway_get) -> None:
        gateway_get.return_value = {"bars": [], "source": "market_sip_compact.macro_bars_by_time_symbol"}

        historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2026, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            before_bar=None,
            ticker="AAPL",
            timeframe="1d",
            row_limit=5_000,
        )

        path, params = gateway_get.call_args.args[:2]
        self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
        self.assertEqual(params["timeframe"], "1d")
        self.assertEqual(params["start"], "2026-01-12T00:00:00+00:00")

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

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
    def test_strategy_activity_is_newest_first_and_excludes_broker_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            journal.append(
                run_id="run-a", category="strategy_decision", entity_type="signal", entity_id="signal-1",
                event_time=TS, payload={"strategy_id": "momentum", "ticker": "AAPL", "action": "wait"},
            )
            journal.append(
                run_id="run-a", category="strategy", entity_type="strategy_intent", entity_id="intent-1",
                event_time=TS + timedelta(seconds=1), payload={"strategy_id": "momentum", "ticker": "AAPL", "action": "enter_long"},
            )
            journal.append(
                run_id="run-a", category="broker", entity_type="order", entity_id="order-1",
                event_time=TS + timedelta(seconds=2), payload={"strategy_id": "momentum", "ticker": "AAPL"},
            )

            rows = journal.strategy_activity_records(strategy_id="momentum", ticker="AAPL")
            self.assertEqual([row.entity_type for row in rows], ["strategy_intent", "signal"])
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
        self.assertEqual(params["as_of"], "2026-07-10T13:45:00+00:00")
        self.assertEqual(params["before"], "2026-07-10T13:44:00+00:00")
        self.assertEqual(params["indicator_columns"], "bar_start,ema_20")
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
            "source": "market_sip_compact.daily_session_bars_by_symbol_time_v1",
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

        path, params = self.gateway_call(
            gateway_get, "/snapshot/chart-macro-bars/AAPL"
        )
        self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
        self.assertEqual(params["timeframe"], "1mo")
        self.assertEqual(params["start"], "2024-08-01T00:00:00+00:00")
        self.assertEqual(result["history"][0]["volume"], 10_000.0)
        self.assertFalse(result["indicators_available"])

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_daily_chart_history_requests_exact_180_day_macro_window(self, gateway_get) -> None:
        gateway_get.return_value = {"bars": [], "source": "market_sip_compact.daily_session_bars_by_symbol_time_v1"}

        historical_bar_history_before(
            before=date(2026, 7, 11),
            session_date=date(2026, 7, 10),
            as_of="2026-07-10T13:45:00+00:00",
            before_bar=None,
            ticker="AAPL",
            timeframe="1d",
            row_limit=5_000,
        )

        path, params = self.gateway_call(
            gateway_get, "/snapshot/chart-macro-bars/AAPL"
        )
        self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
        self.assertEqual(params["timeframe"], "1d")
        self.assertEqual(params["start"], "2026-01-12T00:00:00+00:00")

    @patch("src.backend.trading_runtime_service._historical_gateway_get")
    def test_weekly_and_yearly_chart_history_use_daily_macro_authority(self, gateway_get) -> None:
        gateway_get.return_value = {"bars": [], "source": "market_sip_compact.daily_session_bars_by_symbol_time_v1"}

        for timeframe, expected_start in (
            ("1w", "2023-07-21T00:00:00+00:00"),
            ("1y", "2007-01-01T00:00:00+00:00"),
        ):
            with self.subTest(timeframe=timeframe):
                historical_bar_history_before(
                    before=date(2026, 7, 11),
                    session_date=date(2026, 7, 10),
                    as_of="2026-07-10T13:45:00+00:00",
                    before_bar=None,
                    ticker="AAPL",
                    timeframe=timeframe,
                    row_limit=5_000,
                )
                path, params = self.gateway_call(
                    gateway_get, "/snapshot/chart-macro-bars/AAPL"
                )
                self.assertEqual(path, "/snapshot/chart-macro-bars/AAPL")
                self.assertEqual(params["timeframe"], timeframe)
                self.assertEqual(params["start"], expected_start)

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
    async def test_confirmed_external_proposal_is_journaled_before_portfolio_and_oms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            try:
                runtime = TradingRuntime.__new__(TradingRuntime)
                runtime.config = RunConfig(
                    RunMode.REPLAY,
                    "manual-proposal",
                    1,
                    ("SIM-01",),
                    date(2026, 7, 14),
                    run_id="00000000-0000-0000-0000-000000000003",
                )
                runtime.run_id = runtime.config.run_id
                runtime.journal = journal
                runtime.last_event_time = TS
                runtime._execute_intents = AsyncMock(return_value=[{
                    "decision": {"status": "approved", "reservation_id": "reservation-1"},
                    "order_group": {"state": "submitted"},
                }])
                intent = StrategyIntent(
                    intent_id="proposal:proposal-1",
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
                    proposal_id="proposal-1",
                    proposal_authority="manual",
                )

                records = journal.records(runtime.run_id)
                self.assertEqual(
                    [row.entity_type for row in records],
                    ["trade_proposal_confirmed", "trade_proposal_result"],
                )
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

    async def test_backtest_anchor_is_exclusive_and_replay_anchor_is_inclusive(self) -> None:
        backtest = historical_run_window(RunMode.BACKTEST, date(2026, 7, 13), session_count=1)
        replay = historical_run_window(RunMode.REPLAY, date(2026, 7, 13))
        self.assertEqual(backtest.sessions, (date(2026, 7, 10),))
        self.assertEqual(replay.sessions, (date(2026, 7, 13),))


if __name__ == "__main__":
    unittest.main()

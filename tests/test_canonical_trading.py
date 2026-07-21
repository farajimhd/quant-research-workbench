from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl

from src.backend.canonical_backtest_service import canonical_backtest_state
from src.trading_runtime.canonical_commands import intent_to_ibkr_request
from src.trading_runtime.canonical_session import CanonicalBrokerSession
from src.trading_runtime.domain import (
    BrokerAccount,
    BrokerEventType,
    BrokerProvider,
    Execution,
    InstrumentContract,
    OrderIntent,
    OrderLifecycleState,
    TradingMode,
)
from src.trading_runtime.ibkr_normalizer import normalize_accounts, normalize_ledger, normalize_order, normalize_position_snapshot
from src.trading_runtime.projector import TradingStateProjector
from src.trading_runtime.round_trips import derive_round_trip_trades
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter


NOW = datetime(2026, 7, 17, 14, 0, tzinfo=UTC)


def instrument(symbol: str = "AAPL", conid: int = 265598) -> InstrumentContract:
    return InstrumentContract(instrument_id=f"ibkr:{conid}", conid=conid, symbol=symbol, security_type="STK", currency="USD")


class CanonicalNormalizationTests(unittest.TestCase):
    def test_account_discovery_preserves_view_and_trade_permissions_for_string_account_lists(self) -> None:
        accounts = normalize_accounts(
            [{"accountId": "DU_VIEW", "currency": "CAD"}, {"accountId": "DU_BOTH", "currency": "USD"}],
            {"accounts": ["DU_BOTH", "DU_TRADE"], "acctProps": {"DU_TRADE": {"currency": "EUR"}}},
        )
        by_id = {row.account_id: row for row in accounts}
        self.assertEqual(set(by_id), {"DU_VIEW", "DU_BOTH", "DU_TRADE"})
        self.assertTrue(by_id["DU_VIEW"].can_view)
        self.assertFalse(by_id["DU_VIEW"].can_trade)
        self.assertTrue(by_id["DU_TRADE"].can_trade)
        self.assertFalse(by_id["DU_TRADE"].can_view)
        self.assertEqual(by_id["DU_TRADE"].base_currency, "EUR")

    def test_unknown_order_status_is_not_converted_to_inactive(self) -> None:
        order = normalize_order({"acctId": "DU1", "orderId": "1", "conid": 265598, "ticker": "AAPL", "status": "FutureBrokerState"})
        self.assertEqual(order.lifecycle_state, OrderLifecycleState.UNKNOWN)
        self.assertEqual(order.broker_status_raw, "FutureBrokerState")

    def test_ledger_delta_retains_other_currencies(self) -> None:
        projector = TradingStateProjector(TradingMode.PAPER, BrokerProvider.IBKR_CPAPI)
        projector.merge_ledger(normalize_ledger({"BASE": {"cashbalance": 100}, "CAD": {"cashbalance": 20}}, "DU1"))
        projector.merge_ledger(normalize_ledger({"CAD": {"settledcash": 18}}, "DU1"))
        by_currency = {row.currency: row for row in projector.ledger.values()}
        self.assertEqual(set(by_currency), {"BASE", "CAD"})
        self.assertEqual(by_currency["CAD"].values["cashbalance"], Decimal("20"))
        self.assertEqual(by_currency["CAD"].values["settledcash"], Decimal("18"))


class CanonicalProjectionTests(unittest.TestCase):
    def test_complete_empty_position_snapshot_clears_state_but_incomplete_does_not(self) -> None:
        projector = TradingStateProjector(TradingMode.PAPER, BrokerProvider.IBKR_CPAPI)
        projector.set_accounts([BrokerAccount(provider=BrokerProvider.IBKR_CPAPI, account_id="DU1", base_currency="USD", can_view=True, can_trade=True, valid_at=NOW)])
        first_manifest, first_rows = normalize_position_snapshot([{"conid": 265598, "ticker": "AAPL", "position": 10, "avgCost": 100, "mktPrice": 101, "timestamp": NOW.isoformat()}], "DU1")
        projector.apply_position_snapshot("DU1", first_manifest.snapshot_id, True, first_rows)
        projector.apply_position_snapshot("DU1", "partial", False, [])
        self.assertEqual(len(projector.positions), 1)
        self.assertTrue(projector.stale)
        projector.apply_position_snapshot("DU1", "complete-empty", True, [])
        self.assertEqual(len(projector.positions), 0)
        self.assertTrue(projector.complete)
        self.assertFalse(projector.stale)

    def test_historical_projection_uses_source_time_not_load_time(self) -> None:
        projector = TradingStateProjector(TradingMode.BACKTEST, BrokerProvider.SIMULATED)
        projector.set_accounts([BrokerAccount(provider=BrokerProvider.SIMULATED, account_id="BT", base_currency="USD", valid_at=NOW)])
        self.assertEqual(projector.snapshot().as_of, NOW)

    def test_round_trip_ids_and_values_are_deterministic(self) -> None:
        executions = [
            Execution("open", "DU1", instrument(), "BUY", Decimal("10"), Decimal("100"), NOW, commission=Decimal("1"), commission_status="final"),
            Execution("close", "DU1", instrument(), "SELL", Decimal("10"), Decimal("102"), NOW + timedelta(minutes=1), commission=Decimal("1"), commission_status="final"),
        ]
        first = derive_round_trip_trades(executions)
        second = derive_round_trip_trades(executions)
        self.assertEqual(first, second)
        self.assertEqual(first[0].gross_pnl, Decimal("20"))
        self.assertEqual(first[0].net_pnl, Decimal("18"))


class CanonicalAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulated_adapter_accepts_canonical_intent_and_returns_audit_events(self) -> None:
        broker = SimulatedBrokerAdapter(["SIM"], mode=TradingMode.BACKTEST)
        await broker.initialize()
        intent = OrderIntent(
            command_id="command-1",
            account_id="SIM",
            instrument=instrument(),
            client_order_id="client-1",
            side="BUY",
            order_type="LMT",
            time_in_force="DAY",
            quantity=Decimal("5"),
            limit_price=Decimal("100"),
            created_at=NOW,
        )
        request = intent_to_ibkr_request(intent)
        self.assertEqual(request.acctId, "SIM")
        self.assertEqual(request.price, 100.0)
        events = await broker.submit_intents("SIM", [intent])
        self.assertEqual([row.event_type for row in events], [BrokerEventType.ORDER_COMMAND, BrokerEventType.ORDER_ACKNOWLEDGED])
        self.assertEqual(events[-1].mode, TradingMode.BACKTEST)

    async def test_stream_topic_preserves_case_sensitive_account_id(self) -> None:
        broker = SimulatedBrokerAdapter(["DUAbC"])
        session = CanonicalBrokerSession(broker, mode=TradingMode.REPLAY, provider=BrokerProvider.SIMULATED)
        await session.bootstrap()
        events = session.apply_websocket_message({"topic": "ssd+DUAbC", "result": [{"key": "NetLiquidation", "amount": 100, "currency": "USD", "timestamp": int(NOW.timestamp() * 1000)}]})
        self.assertEqual(events[0].account_id, "DUAbC")


class CanonicalBacktestTests(unittest.TestCase):
    def test_completed_backtest_adapts_to_v2_state_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-1"
            run_dir.mkdir()
            (run_dir / "metadata.json").write_text(json.dumps({"run_id": "run-1", "run_name": "Run 1", "status": "completed", "created_at": NOW.isoformat(), "created_by_app": True, "config": {"base_currency": "USD"}}), encoding="utf-8")
            pl.DataFrame([{"timestamp": NOW, "cash": 90_000.0, "equity": 100_000.0, "realized_pnl": 10.0, "open_unrealized_pnl": 20.0, "gross_exposure": 10_000.0}]).write_parquet(run_dir / "portfolio.parquet")
            pl.DataFrame([{"timestamp": NOW, "symbol": "AAPL", "quantity": 10, "entry_price": 100.0, "mark_price": 101.0, "market_value": 1010.0, "unrealized_pnl": 10.0}]).write_parquet(run_dir / "positions.parquet")
            pl.DataFrame([{"order_id": 1, "symbol": "AAPL", "side": "BUY", "quantity": 10, "order_type": "MARKET", "status": "FILLED", "created_at": NOW, "filled_at": NOW, "fill_price": 100.0}]).write_parquet(run_dir / "orders.parquet")
            pl.DataFrame([{"fill_id": 1, "order_id": 1, "symbol": "AAPL", "side": "BUY", "quantity": 10, "fill_price": 100.0, "filled_at": NOW, "total_fee": 1.0}]).write_parquet(run_dir / "fills.parquet")
            pl.DataFrame().write_parquet(run_dir / "trades.parquet")
            state = canonical_backtest_state(run_dir)
            self.assertEqual(state["schema_version"], 2)
            self.assertEqual(state["mode"], "backtest")
            self.assertTrue(state["complete"])
            self.assertEqual(len(state["positions"]), 1)
            self.assertEqual(len(state["executions"]), 1)
            self.assertEqual(state["portfolio"]["metrics"]["net_liquidation"], "100000.0")


if __name__ == "__main__":
    unittest.main()

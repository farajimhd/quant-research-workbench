from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.backend.query_plans.market_tradable_universe_v1 import (
    full_tradable_universe,
    tradable_symbol_lookup,
)
from src.backend.real_live_market_data.universe import default_universe_sql
from src.backend.real_live_trading_service import tradable_symbol_map


class MarketTradableUniverseQueryPlanTests(unittest.TestCase):
    def test_full_plan_joins_latest_registered_reference_sources(self) -> None:
        sql = full_tradable_universe(database="q_live")
        self.assertIn("FROM `q_live`.feature_tradable_universe_v1 FINAL", sql)
        self.assertIn("FROM `q_live`.feature_scanner_static_v1 FINAL", sql)
        self.assertIn("LEFT JOIN (SELECT * FROM `q_live`.id_issuer_v1 FINAL)", sql)
        self.assertIn("u.is_tradable = 1", sql)
        self.assertIn("u.ibkr_conid AS ibkr_conid", sql)

    def test_symbol_lookup_is_normalized_deduplicated_and_bounded_by_caller_input(self) -> None:
        sql = tradable_symbol_lookup(
            database="q_live",
            symbols=[" msft ", "AAPL", "MSFT"],
        )
        self.assertIn("upper(ticker) IN ('AAPL', 'MSFT')", sql)
        self.assertIn("toUInt8(is_tradable) AS is_tradable", sql)

    def test_symbol_lookup_rejects_empty_set(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one symbol"):
            tradable_symbol_lookup(database="q_live", symbols=[])

    @patch.dict("os.environ", {"REAL_LIVE_TRADABLE_UNIVERSE_DATABASE": "q_live"})
    def test_live_market_data_compatibility_builder_uses_registered_plan(self) -> None:
        config = SimpleNamespace(
            read_clickhouse=SimpleNamespace(database="ignored_read"),
            write_clickhouse=SimpleNamespace(database="ignored_write"),
        )
        self.assertEqual(
            default_universe_sql(config),
            full_tradable_universe(database="q_live"),
        )

    @patch("src.backend.real_live_trading_service.ClickHouseHttpClient")
    @patch("src.backend.real_live_trading_service.market_gateway_config")
    @patch.dict("os.environ", {"REAL_LIVE_TRADABLE_UNIVERSE_DATABASE": "q_live"})
    def test_live_trading_lookup_calls_registered_plan(
        self,
        config_mock,
        client_class_mock,
    ) -> None:
        config_mock.return_value = SimpleNamespace(
            read_clickhouse=SimpleNamespace(database="ignored_read"),
            write_clickhouse=SimpleNamespace(database="ignored_write"),
        )
        client_class_mock.return_value.query_json.return_value = [
            {
                "universe_date_text": "2026-08-11",
                "ticker": "AAPL",
                "is_tradable": 1,
                "exclusion_reason": "",
                "ibkr_conid": 265598,
            }
        ]
        rows = tradable_symbol_map(["aapl"])
        self.assertTrue(rows["AAPL"]["is_tradable"])
        query = client_class_mock.return_value.query_json.call_args.args[0]
        self.assertEqual(
            query,
            tradable_symbol_lookup(database="q_live", symbols=["AAPL"]),
        )


if __name__ == "__main__":
    unittest.main()

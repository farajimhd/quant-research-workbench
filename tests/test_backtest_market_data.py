from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.backend.backtest_market_data import (
    ExecutionInterval,
    MarketDayLedger,
    assert_select_only,
    market_day_rows_sql,
    verify_market_day_plan,
)


class _ReadClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, sql: str) -> str:
        self.queries.append(sql)
        return json.dumps({
            "session_date": "2026-08-18", "ticker": "SUGP",
            "attempt_id": "00000000-0000-0000-0000-000000000001",
        }) + "\n"


class BacktestMarketDataTests(unittest.TestCase):
    def _ledger(self, root: Path) -> MarketDayLedger:
        path = root / "build-ledger-v2.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript("""
          CREATE TABLE builds (build_id TEXT PRIMARY KEY, definition_hash TEXT,
            version TEXT, calculation_source TEXT, rules_hash TEXT,
            database_name TEXT, status TEXT, updated_at TEXT);
          CREATE TABLE units (build_id TEXT, session_date TEXT, ticker TEXT,
            stage TEXT, attempt_id TEXT, source_hash TEXT, output_rows INTEGER,
            output_hash TEXT, status TEXT, updated_at TEXT);
        """)
        connection.execute(
            "INSERT INTO builds VALUES (?,?,?,?,?,?,?,?)",
            ("build-1", "definition", "market-day-core-v5", "events", "rules",
             "arte", "core_complete", "2026-09-23T00:00:00Z"),
        )
        for stage in ("bars", "technical", "broker_100ms"):
            connection.execute(
                "INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("build-1", "2026-08-18", "SUGP", stage,
                 "00000000-0000-0000-0000-000000000001", "source", 10,
                 "output", "complete", "2026-09-23T00:00:00Z"),
            )
        connection.commit()
        connection.close()
        return MarketDayLedger(path)

    def test_execution_interval_is_events_or_100ms_multiple(self) -> None:
        self.assertEqual(ExecutionInterval.parse("realtime").label, "events")
        self.assertEqual(ExecutionInterval.parse("200ms").milliseconds, 200)
        self.assertEqual(ExecutionInterval.parse("1s").milliseconds, 1_000)
        with self.assertRaises(ValueError):
            ExecutionInterval.parse("250ms")

    def test_catalogue_pins_all_three_read_only_products(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        client = _ReadClient()
        verify_market_day_plan(plan, client)
        self.assertEqual(len(client.queries), 3)
        self.assertTrue(all(query.lstrip().startswith("SELECT") for query in client.queries))
        sql = market_day_rows_sql(plan)
        self.assertIn("arte.bars_v1", sql)
        self.assertIn("arte.indicators_v1", sql)
        self.assertIn("arte.liquidity_100ms_v1", sql)
        self.assertNotIn("market_day_events", sql)

    def test_missing_stage_fails_catalogue_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = self._ledger(Path(directory))
            connection = sqlite3.connect(ledger.path)
            connection.execute("DELETE FROM units WHERE stage='broker_100ms'")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(ValueError, "product gaps"):
                ledger.certified_plan(
                    sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                    configuration={"strategy": {"execution_interval": "100ms"}},
                )

    def test_select_only_guard_rejects_mutation(self) -> None:
        self.assertEqual(assert_select_only("SELECT 1"), "SELECT 1")
        with self.assertRaisesRegex(ValueError, "SELECT-only"):
            assert_select_only("INSERT INTO arte.bars_v1 VALUES")


if __name__ == "__main__":
    unittest.main()

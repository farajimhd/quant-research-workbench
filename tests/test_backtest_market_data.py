from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from src.backend.backtest_market_data import (
    ExecutionInterval,
    MarketDayLedger,
    assert_select_only,
    market_day_rows_sql,
    verify_market_day_plan,
    _stable_hash,
)


class _ReadClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, sql: str) -> str:
        self.queries.append(sql)
        return json.dumps({
            "ticker": "SUGP", "n": 10, "unique_keys": 10,
            "hash": "42", "resolutions": [100, 1000],
            "attempt_id": "00000000-0000-0000-0000-000000000001",
        }) + "\n"


class _CorruptReadClient(_ReadClient):
    def execute(self, sql: str) -> str:
        row = json.loads(super().execute(sql))
        row["hash"] = "43"
        return json.dumps(row) + "\n"


class BacktestMarketDataTests(unittest.TestCase):
    def _ledger(self, root: Path) -> MarketDayLedger:
        definition = {"plan": {"requested": ["2026-08-18"], "units": [
            {"source_date": "2026-08-18", "ticker": "SUGP"},
        ]}}
        manifest_dir = root / "market-day"
        manifest_dir.mkdir()
        (manifest_dir / "build-1.json").write_text(json.dumps({
            "build_id": "build-1", "definition": definition,
        }), encoding="utf-8")
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
            ("build-1", _stable_hash(definition), "market-day-core-v5", "events", "rules",
             "arte", "core_complete", "2026-09-23T00:00:00Z"),
        )
        for stage in ("bars", "technical", "broker_100ms"):
            connection.execute(
                "INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("build-1", "2026-08-18", "SUGP", stage,
                 "00000000-0000-0000-0000-000000000001", "source", 10,
                 "42", "complete", "2026-09-23T00:00:00Z"),
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

    def test_nested_unsupported_fixed_interval_fails_before_data_access(self) -> None:
        from src.backend.backtest_market_data import compile_required_resolutions

        with self.assertRaisesRegex(ValueError, "200"):
            compile_required_resolutions(
                {"signal_activation": {"signal_streams": [
                    {"execution_interval": {"value": 200, "unit": "milliseconds"}},
                ]}},
                ExecutionInterval.parse("100ms"),
            )

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
        self.assertNotIn("WITH scopes", sql)
        self.assertIn("b.session_date AS session_date", sql)
        self.assertIn("l.quote_timestamp_us AS quote_timestamp_us", sql)

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

    def test_day_can_certify_before_entire_campaign_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = self._ledger(Path(directory))
            connection = sqlite3.connect(ledger.path)
            connection.execute("UPDATE builds SET status='building'")
            connection.commit()
            connection.close()
            plan = ledger.certified_plan(
                sessions=[date(2026, 8, 18)], tickers=[],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
            self.assertEqual(plan.tickers, ("SUGP",))

    def test_changed_persisted_hash_fails_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
            with self.assertRaisesRegex(ValueError, "integrity changed"):
                verify_market_day_plan(plan, _CorruptReadClient())

    def test_incomplete_full_population_fails_instead_of_shrinking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = self._ledger(Path(directory))
            manifest = ledger.path.parent / "market-day" / "build-1.json"
            report = json.loads(manifest.read_text(encoding="utf-8"))
            report["definition"]["plan"]["units"].append(
                {"source_date": "2026-08-18", "ticker": "OTHER"}
            )
            manifest.write_text(json.dumps(report), encoding="utf-8")
            connection = sqlite3.connect(ledger.path)
            connection.execute("UPDATE builds SET definition_hash=?", (_stable_hash(report["definition"]),))
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(ValueError, "product gaps"):
                ledger.certified_plan(
                    sessions=[date(2026, 8, 18)], tickers=[],
                    configuration={"strategy": {"execution_interval": "100ms"}},
                )

    def test_select_only_guard_rejects_mutation(self) -> None:
        self.assertEqual(assert_select_only("SELECT 1"), "SELECT 1")
        with self.assertRaisesRegex(ValueError, "SELECT-only"):
            assert_select_only("INSERT INTO arte.bars_v1 VALUES")

    def test_fixed_definition_remains_readable_for_archived_review(self) -> None:
        from src.backend.replay_run_service import ReplayRunDefinition, RunMode

        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 18), start_time=time(4),
            mode=RunMode.BACKTEST, execution_interval="100ms",
            market_data_plan={"token": "test", "execution_interval": {"milliseconds": 100}},
            configuration_revision={"revision_id": "approved-test", "payload": {}},
        )
        self.assertEqual(definition.execution_interval, "100ms")


if __name__ == "__main__":
    unittest.main()

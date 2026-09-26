from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import patch

from src.backend.backtest_market_data import (
    ExecutionInterval,
    MarketDayLedger,
    assert_select_only,
    market_day_boundary,
    market_day_source_sqls,
    project_market_day_plan,
    iter_market_boundary_groups,
    iter_market_day_rows,
    iter_market_time_groups,
    iter_persisted_v7_seconds,
    readonly_clickhouse_client,
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
            "eligible_keys": 10, "key_hash": "123",
            "attempt_id": "00000000-0000-0000-0000-000000000001",
        }) + "\n"

    def iter_json_each_row(self, sql: str):
        for line in self.execute(sql).splitlines():
            if line.strip():
                yield json.loads(line)


class _CorruptReadClient(_ReadClient):
    def execute(self, sql: str) -> str:
        row = json.loads(super().execute(sql))
        row["hash"] = "43"
        return json.dumps(row) + "\n"


class _MisalignedIndicatorClient(_ReadClient):
    def execute(self, sql: str) -> str:
        row = json.loads(super().execute(sql))
        if "arte.indicators_v1" in sql:
            row["key_hash"] = "124"
        return json.dumps(row) + "\n"


class _QuoteOnlyReadClient(_ReadClient):
    def execute(self, sql: str) -> str:
        if "arte.indicators_v1" in sql:
            self.queries.append(sql)
            return ""
        row = json.loads(super().execute(sql))
        if "arte.bars_v1" in sql:
            row["eligible_keys"] = 0
            row["key_hash"] = "0"
        return json.dumps(row) + "\n"


class BacktestMarketDataTests(unittest.TestCase):
    def test_projection_preserves_parent_attempts_and_changes_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        projected = project_market_day_plan(plan, ["SUGP"])
        self.assertEqual(projected.units, plan.units)
        self.assertNotEqual(projected.token, plan.token)
        with self.assertRaisesRegex(ValueError, "nonempty subset"):
            project_market_day_plan(plan, ["OTHER"])

    def test_full_universe_stream_has_bounded_large_query_settings(self) -> None:
        with patch.dict("os.environ", {
            "BACKTEST_CLICKHOUSE_URL": "http://localhost:8123",
            "BACKTEST_CLICKHOUSE_USER": "readonly-test",
            "BACKTEST_CLICKHOUSE_PASSWORD": "",
        }):
            ordinary = readonly_clickhouse_client()
            stream = readonly_clickhouse_client(market_stream=True)
        self.assertEqual(ordinary.default_query_params["readonly"], "1")
        self.assertEqual(ordinary.default_query_params["max_execution_time"], "60")
        self.assertEqual(stream.default_query_params["max_query_size"], str(16 * 1024 * 1024))
        self.assertEqual(stream.default_query_params["max_ast_elements"], "500000")
        self.assertEqual(stream.default_query_params["max_execution_time"], "21600")

    def test_persisted_boundary_uses_0400_new_york_clock(self) -> None:
        self.assertEqual(
            market_day_boundary("2026-08-18", 300_100).isoformat(),
            "2026-08-18T04:05:00.100000-04:00",
        )
        with self.assertRaisesRegex(ValueError, "04:00-20:00"):
            market_day_boundary("2026-08-18", 57_600_100)

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

    def test_fixed_plan_uses_clickhouse_certificate_and_keeper_only(self) -> None:
        from unittest.mock import Mock, patch
        from src.backend.backtest_market_data import certified_market_plan_from_arte

        class Closed:
            def __init__(self):
                self.closed = False
            def close(self):
                self.closed = True
        reader = Closed()
        session = Closed()
        session.client = object()
        with (patch("src.backend.backtest_market_data.readonly_clickhouse_client",
                    return_value=reader) as open_reader,
              patch("src.trading_runtime.arte_market_day_cold_preflight.market_day_fence_build_ids",
                    return_value=("a" * 64,)) as fence_ids,
              patch("src.trading_runtime.keeper_session.open_workstation_keeper_session",
                    return_value=session),
              patch("src.trading_runtime.arte_market_day_keeper.MarketDayKeeperReader",
                    return_value=Mock(load=Mock(return_value="attested"))) as keeper_reader,
              patch("src.trading_runtime.arte_market_day_cold_preflight.discover_cold_certified_market_day_plan",
                    return_value="certified") as discover):
            result = certified_market_plan_from_arte(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"], configuration={})
        self.assertEqual(result, "certified")
        open_reader.assert_called_once_with(v3_read_principal=True)
        self.assertEqual(discover.call_args.kwargs["sessions"], ("2026-08-18",))
        self.assertEqual(discover.call_args.kwargs["expected_build_ids"], ("a" * 64,))
        self.assertTrue(session.closed)
        self.assertEqual(discover.call_args.args[1].load("a" * 64), "attested")
        keeper_reader.return_value.load.assert_called_once_with("a" * 64)
        fence_ids.assert_called_once()
        self.assertTrue(reader.closed and session.closed)

    def test_certificate_reader_retries_only_lost_select_response(self) -> None:
        from http.client import RemoteDisconnected
        from unittest.mock import Mock
        from src.backend.backtest_market_data import _MarketCertificateReader

        raw = Mock()
        raw.execute.side_effect = [RemoteDisconnected("closed"), "certified"]
        reader = _MarketCertificateReader(raw)
        self.assertEqual(reader.execute("SELECT 1 FORMAT TabSeparated"), "certified")
        self.assertEqual(raw.execute.call_count, 2)
        raw.execute.side_effect = None
        raw.execute.return_value = "system metadata"
        self.assertEqual(reader.execute("SELECT name FROM system.tables"),
                         "system metadata")
        with self.assertRaisesRegex(ValueError, "SELECT-only"):
            reader.execute("INSERT INTO arte.bars_v1 VALUES (1)")
        self.assertEqual(raw.execute.call_count, 3)
        with self.assertRaisesRegex(ValueError, "SELECT-only"):
            reader.execute("SELECT 1; INSERT INTO arte.bars_v1 VALUES (1)")
        self.assertEqual(raw.execute.call_count, 3)
        raw.execute.side_effect = [RemoteDisconnected("closed"),
                                   RemoteDisconnected("closed again")]
        with self.assertRaises(RemoteDisconnected):
            reader.execute("SELECT 1 FORMAT TabSeparated")
        self.assertEqual(raw.execute.call_count, 5)

    def test_nested_unsupported_fixed_interval_fails_before_data_access(self) -> None:
        from src.backend.backtest_market_data import compile_required_resolutions

        with self.assertRaisesRegex(ValueError, "200"):
            compile_required_resolutions(
                {"signal_activation": {"signal_streams": [
                    {"execution_interval": {"value": 200, "unit": "milliseconds"}},
                ]}},
                ExecutionInterval.parse("100ms"),
            )

    def test_strategy_one_pins_completed_macd_and_stop_bar_resolutions(self) -> None:
        from src.backend.backtest_market_data import compile_required_resolutions

        required = compile_required_resolutions(
            {"strategy": {"strategy_number": 1}},
            ExecutionInterval.parse("100ms"),
        )
        self.assertEqual(required, (100, 1_000, 5_000, 10_000, 30_000))
        self.assertEqual(compile_required_resolutions(
            {"strategy": {"strategy_number": True}},
            ExecutionInterval.parse("100ms")), (100, 1_000))

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
        sources = market_day_source_sqls(plan)
        self.assertEqual(len(sources), 2)
        sql = "\n".join(sources)
        self.assertIn("arte.bars_v1", sql)
        self.assertIn("arte.indicators_v1", sql)
        self.assertIn("i.resolution_ms AS indicator_resolution_ms", sql)
        self.assertIn("arte.liquidity_100ms_v1", sql)
        self.assertNotIn("market_day_events", sql)
        self.assertNotIn("WITH scopes", sql)
        self.assertIn("SELECT l.session_date AS session_date,l.ticker AS ticker", sql)
        self.assertIn("l.bucket_index AS bucket_index", sql)
        self.assertIn("l.quote_timestamp_us AS quote_timestamp_us", sql)
        self.assertIn("FROM (SELECT * FROM arte.liquidity_100ms_v1", sql)
        self.assertIn("WHERE resolution_ms=100", sql)
        self.assertIn("WHERE resolution_ms IN (1000)", sql)
        self.assertNotIn("UNION ALL", sql)
        self.assertTrue(all("ORDER BY m.session_date,m.boundary_ms,m.ticker,m.resolution_ms" in
                            source for source in sources))
        premarket_sql = "\n".join(market_day_source_sqls(plan, through_boundary_ms=19_800_000))
        self.assertIn("bucket_index<342000", premarket_sql)
        self.assertIn("(resolution_ms=1000 AND bucket_index<34200)", premarket_sql)
        self.assertIn("*100-14400000 AS boundary_ms", premarket_sql)
        with self.assertRaisesRegex(ValueError, "positive 100ms"):
            market_day_source_sqls(plan, through_boundary_ms=19_800_001)

    def test_liquidity_bucket_upper_bound_is_exact_completed_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        for boundary_ms in (100, 200, 300_000, 57_600_000):
            source = market_day_source_sqls(
                plan, through_boundary_ms=boundary_ms)[0]
            upper = (boundary_ms + 14_400_000) // 100
            self.assertIn(f"bucket_index<{upper}", source)
            for bucket_index in (upper - 2, upper - 1, upper):
                self.assertEqual(bucket_index < upper,
                                 (bucket_index + 1) * 100 <=
                                 boundary_ms + 14_400_000)

    def test_bar_resolution_bounds_are_exact_completed_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        for boundary_ms in (100, 900, 1_000, 300_000, 57_600_000):
            source = market_day_source_sqls(
                plan, through_boundary_ms=boundary_ms)[1]
            for resolution in (100, 1_000):
                upper = (boundary_ms + 14_400_000) // resolution
                self.assertIn(
                    f"(resolution_ms={resolution} AND bucket_index<{upper})",
                    source)
                for bucket_index in (upper - 2, upper - 1, upper):
                    self.assertEqual(bucket_index < upper,
                                     (bucket_index + 1) * resolution <=
                                     boundary_ms + 14_400_000)

    def test_window_excludes_completed_start_and_includes_completed_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        for start, end in ((0, 100), (100, 1_000), (1_000, 1_100),
                           (29_900, 30_000), (30_000, 30_100)):
            sources = market_day_source_sqls(
                plan, after_boundary_ms=start, through_boundary_ms=end)
            for resolution in (100, 1_000):
                lower = (start + 14_400_000) // resolution
                upper = (end + 14_400_000) // resolution
                self.assertIn(f"bucket_index>={lower}", sources[0] if resolution == 100 else sources[1])
                self.assertIn(f"bucket_index<{upper}", sources[0] if resolution == 100 else sources[1])
                for bucket in range(lower - 1, upper + 1):
                    self.assertEqual(lower <= bucket < upper,
                        start + 14_400_000 < (bucket + 1) * resolution
                        <= end + 14_400_000)
        with self.assertRaisesRegex(ValueError, "start boundary"):
            market_day_source_sqls(plan, after_boundary_ms=1_000,
                                   through_boundary_ms=1_000)
        with self.assertRaisesRegex(ValueError, "start boundary"):
            market_day_source_sqls(plan, after_boundary_ms=101)

    def test_window_prunes_certified_fill_price_children_too(self) -> None:
        from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit

        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        unit = next(unit for unit in plan.units if unit.stage == "broker_100ms")
        prices = PriceLevelPlan(plan.build_id, (PriceLevelUnit(
            unit.session_date, unit.ticker, unit.attempt_id,
            "00000000-0000-0000-0000-000000000001", 0, 0, 0., "0"),), "token")
        source = market_day_source_sqls(
            plan, after_boundary_ms=100, through_boundary_ms=1_000,
            price_plan=prices)[0]
        self.assertIn("AND bucket_index>=144001", source)
        self.assertIn("AND bucket_index<144010", source)
        self.assertIn("FROM arte.liquidity_execution_price_100ms_v1", source)

    def test_boundary_groups_keep_sparse_quote_buckets_and_completed_seconds(self) -> None:
        rows = [
            {"session_date": "2026-08-18", "boundary_ms": 100, "ticker": "AAPL", "resolution_ms": 100},
            {"session_date": "2026-08-18", "boundary_ms": 1000, "ticker": "AAPL", "resolution_ms": 100},
            {"session_date": "2026-08-18", "boundary_ms": 1000, "ticker": "AAPL", "resolution_ms": 1000},
        ]
        groups = list(iter_market_boundary_groups(iter(rows)))
        self.assertEqual([set(group[3]) for group in groups], [{100}, {100, 1000}])
        with self.assertRaisesRegex(ValueError, "Duplicate persisted resolution"):
            list(iter_market_boundary_groups([rows[0], rows[0]]))
        with self.assertRaisesRegex(ValueError, "not in causal order"):
            list(iter_market_boundary_groups([rows[1], rows[0]]))

    def test_separate_market_sources_merge_at_completed_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        class Sources:
            def iter_json_each_row(self, sql):
                if "SELECT l.session_date" in sql:
                    return iter([
                        {"session_date": "2026-08-18", "boundary_ms": 100,
                         "ticker": "SUGP", "resolution_ms": 100},
                        {"session_date": "2026-08-18", "boundary_ms": 1000,
                         "ticker": "SUGP", "resolution_ms": 100},
                    ])
                return iter([{"session_date": "2026-08-18", "boundary_ms": 1000,
                              "ticker": "SUGP", "resolution_ms": 1000}])
        rows = list(iter_market_day_rows(plan, client=Sources(), through_boundary_ms=1000))
        self.assertEqual([(row["boundary_ms"], row["resolution_ms"]) for row in rows],
                         [(100, 100), (1000, 100), (1000, 1000)])

    def test_time_group_waits_for_all_tickers_at_completed_boundary(self) -> None:
        rows = [
            {"session_date": "2026-08-18", "boundary_ms": 100, "ticker": "AAPL", "resolution_ms": 100},
            {"session_date": "2026-08-18", "boundary_ms": 100, "ticker": "MSFT", "resolution_ms": 100},
            {"session_date": "2026-08-18", "boundary_ms": 200, "ticker": "AAPL", "resolution_ms": 100},
        ]
        time_groups = list(iter_market_time_groups(iter_market_boundary_groups(rows)))
        self.assertEqual([group[1] for group in time_groups], [100, 200])
        self.assertEqual([ticker for ticker, _ in time_groups[0][2]], ["AAPL", "MSFT"])
        with self.assertRaisesRegex(ValueError, "not in causal order"):
            list(iter_market_time_groups([
                ("2026-08-18", 200, "AAPL", {}),
                ("2026-08-18", 100, "AAPL", {}),
            ]))
        with self.assertRaisesRegex(ValueError, "Duplicate ticker"):
            list(iter_market_time_groups([
                ("2026-08-18", 100, "AAPL", {}),
                ("2026-08-18", 100, "AAPL", {}),
            ]))

    def test_v7_catch_up_reads_only_completed_pinned_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        client = _ReadClient()
        rows = list(iter_persisted_v7_seconds(plan, session_date="2026-08-18",
                                              ticker="SUGP", through_boundary_ms=300_100,
                                              client=client))
        assert len(rows) == 1
        assert "arte.bars_v1" in client.queries[0]
        assert "AND resolution_ms=1000 AND bucket_index>=14400" in client.queries[0]
        assert "AND bucket_index<14700" in client.queries[0]
        assert "attempt_id=toUUID('00000000-0000-0000-0000-000000000001')" in client.queries[0]
        with self.assertRaisesRegex(ValueError, "outside the certified"):
            list(iter_persisted_v7_seconds(plan, session_date="2026-08-18",
                                            ticker="OTHER", through_boundary_ms=300_100,
                                            client=client))

    def test_v7_catch_up_closes_stream_when_consumer_stops_early(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
        closed = []

        class StreamingClient(_ReadClient):
            def iter_json_each_row(self, sql):
                self.queries.append(sql)
                try:
                    yield {"ticker": "SUGP", "bucket_index": 14400}
                    yield {"ticker": "SUGP", "bucket_index": 14401}
                finally:
                    closed.append(True)

        source = iter_persisted_v7_seconds(
            plan, session_date="2026-08-18", ticker="SUGP",
            through_boundary_ms=300_100, client=StreamingClient(),
        )
        self.assertEqual(next(source)["bucket_index"], 14400)
        source.close()
        self.assertEqual(closed, [True])

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

    def test_replaced_indicator_key_fails_preflight_even_with_matching_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._ledger(Path(directory)).certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
            with self.assertRaisesRegex(ValueError, "indicator.*key coverage"):
                verify_market_day_plan(plan, _MisalignedIndicatorClient())

    def test_quote_only_session_allows_zero_indicators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = self._ledger(Path(directory))
            connection = sqlite3.connect(ledger.path)
            connection.execute(
                "UPDATE units SET output_rows=0,output_hash='0' WHERE stage='technical'")
            connection.commit()
            connection.close()
            plan = ledger.certified_plan(
                sessions=[date(2026, 8, 18)], tickers=["SUGP"],
                configuration={"strategy": {"execution_interval": "100ms"}},
            )
            verify_market_day_plan(plan, _QuoteOnlyReadClient())

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

    def test_fixed_v7_definition_pins_persisted_catalog_without_legacy_resolve(self) -> None:
        from unittest.mock import patch
        from src.backend.replay_run_service import ReplayRunDefinition, RunMode

        with patch("src.backend.experimental_structure_book.resolve",
                   side_effect=AssertionError("fixed Backtest must not resolve retrospective V7")):
            definition = ReplayRunDefinition(
                session_date=date(2026, 8, 18), start_time=time(4),
                mode=RunMode.BACKTEST, execution_interval="100ms",
                market_data_plan={"token": "market", "build_id": "build",
                                  "execution_interval": {"milliseconds": 100}},
                causal_v7_plan={"token": "v7", "build_id": "build",
                                "catalog_hash": "a" * 64},
                configuration_revision={"revision_id": "approved-test", "payload": {}},
            )
        self.assertEqual(definition.experimental_structure_fingerprint, "a" * 64)


if __name__ == "__main__":
    unittest.main()

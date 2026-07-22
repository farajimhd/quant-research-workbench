from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipelines.reference_data.market_publications_historical_gap_fill import (
    SEC_FTD_PAGE,
    is_us_equity_market_holiday,
    parse_sec_ftd_links,
)
from services.reference_gateway.main import publication_schedule_details
from services.reference_gateway.publication_maintenance import PublicationMaintenanceResult, run_recent_publication_gap_fill
from services.reference_gateway.source_schedule import record_source_schedule
from services.reference_gateway.state import coverage_status, latest_publication_coverage


class FakeClickHouseClient:
    def __init__(self) -> None:
        self.coverage_sql = ""

    def execute(self, sql: str) -> str:
        if "FROM system.tables" in sql:
            return "1\n"
        self.coverage_sql = sql
        return (
            '{"coverage_kind":"finra_short_volume:CNMS","min_start":"2025-01-01",'
            '"max_end":"2026-07-03","windows":2,"rows_written":12240,'
            '"unresolved_windows":0,"latest_status":"completed"}\n'
        )


class MarketPublicationReliabilityTests(unittest.TestCase):
    def test_coverage_query_collapses_retries_by_logical_window(self) -> None:
        client = FakeClickHouseClient()

        rows = latest_publication_coverage(client, database="q_live")

        self.assertEqual(rows["finra_short_volume:CNMS"]["unresolved_windows"], 0)
        self.assertIn("latest_window_results", client.coverage_sql)
        self.assertIn("source_object", client.coverage_sql)
        self.assertIn("countIf(status IN ('failed', 'error')) AS unresolved_windows", client.coverage_sql)
        self.assertIn("argMax(status, tuple(coverage_end_date, coverage_start_date, window_latest_update))", client.coverage_sql)

    def test_unresolved_old_window_is_warning_not_frontier_failure(self) -> None:
        self.assertEqual(coverage_status("completed", date.today(), date.today(), 1), "warning")
        self.assertEqual(coverage_status("failed", date.today(), date.today(), 0), "failed")

    def test_sec_page_parser_preserves_nonstandard_authoritative_urls(self) -> None:
        html = """
        <a href="/files/data/other/fails-deliver-data/cnsfails202605a.zip">May 2026 A</a>
        <a href="/files/node/add/data_distribution/cnsfails202002b.zip">Feb 2020 B</a>
        <a href="/files/data/fails-deliver-data/cnsfails201910a_0.zip">Oct 2019 A</a>
        """

        links = parse_sec_ftd_links(html)

        self.assertEqual(len(links), 3)
        self.assertEqual(links[0]["url"], "https://www.sec.gov/files/data/fails-deliver-data/cnsfails201910a_0.zip")
        self.assertTrue(all(item["link_mode"] == "html" for item in links))

    def test_sec_page_parser_does_not_invent_missing_links(self) -> None:
        self.assertEqual(parse_sec_ftd_links("<html><body>No archives</body></html>"), [])

    def test_finra_special_full_day_closure_is_nonpublication_day(self) -> None:
        self.assertTrue(is_us_equity_market_holiday(date(2025, 1, 9)))
        self.assertFalse(is_us_equity_market_holiday(date(2025, 1, 8)))

    def test_gateway_maintenance_explicitly_uses_html_sec_links(self) -> None:
        class FakeProcess:
            stdout = ["done\n"]

            @staticmethod
            def wait() -> int:
                return 0

        config = SimpleNamespace(
            market_publication_gap_fill_enabled=True,
            market_publication_deep_backfill_enabled=False,
            market_publication_gap_fill_days=5,
            clickhouse_read_database="q_live",
            clickhouse_write_database="q_live",
            prepared_root_win=Path("D:/market-data/prepared"),
        )
        with patch("services.reference_gateway.publication_maintenance.subprocess.Popen", return_value=FakeProcess()) as popen:
            result = run_recent_publication_gap_fill(config)

        command = popen.call_args.args[0]
        self.assertTrue(result.attempted)
        self.assertEqual(command[command.index("--sec-ftd-link-mode") + 1], "html")

    def test_source_schedule_checkpoint_retries_same_idempotent_insert(self) -> None:
        class ResetOnceClient:
            def __init__(self) -> None:
                self.sql: list[str] = []

            def execute(self, sql: str) -> str:
                self.sql.append(sql)
                if len(self.sql) == 1:
                    raise ConnectionResetError(10054, "connection reset")
                return ""

        client = ResetOnceClient()
        config = SimpleNamespace(clickhouse_write_database="q_live")
        with patch("services.reference_gateway.source_schedule.time.sleep") as sleep:
            record_source_schedule(
                client,
                config,
                source_name="market_publication_gap_fill",
                status="completed",
                rows_written=186_755,
                details={"windows": 36},
                source_run_id="reference_publications_test",
            )

        self.assertEqual(len(client.sql), 2)
        self.assertEqual(client.sql[0], client.sql[1])
        sleep.assert_called_once_with(1.0)

    def test_publication_schedule_details_exclude_runtime_output(self) -> None:
        result = PublicationMaintenanceResult(
            attempted=True,
            returncode=0,
            start_date="2026-07-08",
            end_date="2026-07-22",
            reason="deep_reference_publication_gap_fill",
            command=["python", "gap_fill.py"],
            stdout_tail="large runtime output",
            stderr_tail="diagnostic traceback",
        )

        details = publication_schedule_details(result)

        self.assertEqual(details["returncode"], 0)
        self.assertNotIn("command", details)
        self.assertNotIn("stdout_tail", details)
        self.assertNotIn("stderr_tail", details)


if __name__ == "__main__":
    unittest.main()

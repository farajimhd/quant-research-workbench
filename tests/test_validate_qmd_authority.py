from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.validate_qmd_authority import (
    collect_evidence,
    compare_scanner_parity,
    direct_scanner_rows,
    validate_event_page,
    validate_source_plan,
)


class QmdAuthorityValidationTests(unittest.TestCase):
    @patch("scripts.validate_qmd_authority._clickhouse_json_rows")
    def test_direct_parity_uses_only_plan_declared_bounded_sources(self, query) -> None:
        query.return_value = [{"ticker": "AAPL", "trade_count": 1}]
        plan = {
            "end": "2026-01-01T00:01:00Z",
            "segments": [
                {
                    "start": "2025-12-31T23:59:00Z",
                    "end": "2026-01-01T00:01:00Z",
                    "queryable_by_history": True,
                    "source": "market_sip_compact.events_YYYY",
                    "tier": "archive",
                }
            ],
        }
        rows = direct_scanner_rows(
            plan=plan,
            tickers="AAPL",
            clickhouse_url="http://clickhouse",
            clickhouse_user="default",
            clickhouse_password="secret",
        )
        self.assertEqual(rows[0]["ticker"], "AAPL")
        sql = query.call_args.kwargs["sql"]
        self.assertIn("market_sip_compact.events_2025", sql)
        self.assertIn("market_sip_compact.events_2026", sql)
        self.assertIn("ticker IN ('AAPL')", sql)

    def test_scanner_parity_reports_metric_drift(self) -> None:
        failures = compare_scanner_parity(
            {
                "AAPL": {
                    "first": 100.0,
                    "first_5m": 101.0,
                    "last": 102.0,
                    "quote_count": 2,
                    "trade_count": 3,
                    "volume": 40.0,
                }
            },
            [
                {
                    "ticker": "AAPL",
                    "first": 100.0,
                    "first_5m": 101.0,
                    "last": 102.0,
                    "quote_count": 2,
                    "trade_count": 4,
                    "volume": 40.0,
                }
            ],
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("trade_count", failures[0])

    def test_direct_parity_rejects_live_or_unapproved_sources(self) -> None:
        for source, tier, queryable in (
            ("http://127.0.0.1:8800", "current_live", False),
            ("other.events_2026", "archive", True),
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                RuntimeError, "fully durable|unapproved"
            ):
                direct_scanner_rows(
                    plan={
                        "end": "2026-08-01T00:01:00Z",
                        "segments": [
                            {
                                "start": "2026-08-01T00:00:00Z",
                                "end": "2026-08-01T00:01:00Z",
                                "queryable_by_history": queryable,
                                "source": source,
                                "tier": tier,
                            }
                        ],
                    },
                    tickers="AAPL",
                    clickhouse_url="http://clickhouse",
                    clickhouse_user="default",
                    clickhouse_password="",
                )

    def test_source_plan_requires_exact_non_overlapping_tiling(self) -> None:
        plan = {
            "plan_hash": "plan-1",
            "event_schema_version": 4,
            "segments": [
                {"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "tier": "archive"},
                {"start": "2026-08-02T00:00:00Z", "end": "2026-08-03T00:00:00Z", "tier": "recent"},
            ],
        }
        self.assertEqual(
            validate_source_plan(plan, start="2026-08-01T00:00:00Z", end="2026-08-03T00:00:00Z"),
            [],
        )
        plan["segments"][1]["start"] = "2026-08-01T23:59:00Z"
        self.assertRegex(validate_source_plan(plan, start="2026-08-01T00:00:00Z", end="2026-08-03T00:00:00Z")[0], "expected")

    def test_event_validation_rejects_order_regression_and_missing_lineage(self) -> None:
        failures, _, lineage = validate_event_page(
            [
                {"ts": "2026-08-01T00:00:02Z", "ticker": "AAPL", "raw": {"correlation_id": "c", "causation_id": "a"}},
                {"ts": "2026-08-01T00:00:01Z", "ticker": "AAPL", "raw": {}},
            ],
            None,
        )
        self.assertEqual(lineage, 1)
        self.assertTrue(any("regressed" in failure for failure in failures))
        self.assertTrue(any("lacks" in failure for failure in failures))

    @patch("scripts.validate_qmd_authority._get_json")
    def test_collection_pins_revision_across_pages(self, get_json) -> None:
        plan = {
            "plan_hash": "plan-1",
            "event_schema_version": 4,
            "segments": [{"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "tier": "archive"}],
        }
        revision = {"source_plan_hash": "plan-1", "token": "revision-1"}
        event = {"ts": "2026-08-01T15:00:00Z", "ticker": "AAPL", "raw": {"correlation_id": "c", "causation_id": "a"}}
        responses = [
            {"service": "qmd_history_gateway", "status": "ready"},
            {"header": {"service": "qmd_history_gateway"}},
            plan,
            {"complete": True},
            {"running": True, "status": "closed"},
            {"header": {"service": "qmd_gateway"}},
            {"complete": True, "events": [event], "next_cursor": None, "source_revision": revision},
        ]
        get_json.side_effect = responses
        report = collect_evidence(
            live_url="http://live",
            history_url="http://history",
            start="2026-08-01T00:00:00Z",
            end="2026-08-02T00:00:00Z",
            tickers="AAPL",
            page_size=100,
            max_events=100,
        )
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["event_page_proof"]["lineage_count"], 1)

    @patch("scripts.validate_qmd_authority._get_json")
    def test_history_only_requires_and_records_a_durable_plan(self, get_json) -> None:
        plan = {
            "plan_hash": "plan-1",
            "event_schema_version": 4,
            "segments": [
                {
                    "start": "2026-08-01T00:00:00Z",
                    "end": "2026-08-02T00:00:00Z",
                    "queryable_by_history": True,
                    "source": "market_sip_compact.events_YYYY",
                    "tier": "archive",
                }
            ],
        }
        revision = {"source_plan_hash": "plan-1", "token": "revision-1"}
        get_json.side_effect = [
            {"service": "qmd_history_gateway", "status": "ready"},
            {"header": {"service": "qmd_history_gateway"}},
            plan,
            {"complete": True},
            {"complete": True, "events": [], "next_cursor": None, "source_revision": revision},
        ]
        report = collect_evidence(
            live_url="http://occupied",
            history_url="http://history",
            start="2026-08-01T00:00:00Z",
            end="2026-08-02T00:00:00Z",
            tickers="AAPL",
            page_size=100,
            max_events=100,
            allow_history_only=True,
        )
        self.assertEqual(report["validation_scope"], "durable_history_only")
        self.assertTrue(report["services"]["live_health"]["skipped"])

    @patch("scripts.validate_qmd_authority._get_json")
    def test_history_only_rejects_a_live_continuation(self, get_json) -> None:
        get_json.side_effect = [
            {"service": "qmd_history_gateway", "status": "ready"},
            {"header": {"service": "qmd_history_gateway"}},
            {
                "plan_hash": "plan-1",
                "event_schema_version": 4,
                "segments": [
                    {
                        "start": "2026-08-01T00:00:00Z",
                        "end": "2026-08-02T00:00:00Z",
                        "queryable_by_history": False,
                        "source": "http://127.0.0.1:8800",
                        "tier": "current_live",
                    }
                ],
            },
            {"complete": False},
        ]
        with self.assertRaisesRegex(RuntimeError, "durable and queryable"):
            collect_evidence(
                live_url="http://occupied",
                history_url="http://history",
                start="2026-08-01T00:00:00Z",
                end="2026-08-02T00:00:00Z",
                tickers="AAPL",
                page_size=100,
                max_events=100,
                allow_history_only=True,
            )


if __name__ == "__main__":
    unittest.main()

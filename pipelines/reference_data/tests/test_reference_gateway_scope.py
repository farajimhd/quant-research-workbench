from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipelines.reference_data.migration.step_02c_report_weak_reference_candidates import weak_candidate_report_sql
from pipelines.reference_data.migration.step_06_build_q_live_bridge_features import build_specs
from services.reference_gateway.active_tickers import MissingTickerCandidate, run_active_ticker_plan
from services.reference_gateway.audit import active_stock_base_query
from services.reference_gateway.canonical_graph_writer import ExistingGraph, build_candidate_rows, stable_key
from services.reference_gateway.tradability import is_otc_venue, otc_venue_predicate_sql


class ReferenceGatewayScopeTests(unittest.TestCase):
    def test_otc_venue_authority_covers_provider_and_canonical_codes(self) -> None:
        for value in ("OTCM", "XOTC", "OTCLNKECN", "OTCQX", "PINK", "ARCAEDGE"):
            with self.subTest(value=value):
                self.assertTrue(is_otc_venue(value))
        for value in ("XNAS", "XNYS", "ARCX", "NYSEARCA", "XASE", "IEX"):
            with self.subTest(value=value):
                self.assertFalse(is_otc_venue(value))

    def test_sql_scope_is_shared_by_audit_reports_and_publication(self) -> None:
        predicate = otc_venue_predicate_sql("exchange_code")
        audit_sql = active_stock_base_query("q_live")
        weak_report_sql = weak_candidate_report_sql("q_live", "sec_core")
        tradable_sql = next(spec.insert_sql for spec in build_specs("q_live", "run", "2026-08-07 00:00:00.000", date(2026, 8, 7)) if spec.name == "tradable_universe")

        self.assertIn("ARCAEDGE", predicate)
        self.assertIn("NOT", audit_sql)
        self.assertIn("ARCAEDGE", audit_sql)
        self.assertIn("ARCAEDGE", weak_report_sql)
        self.assertIn("unsupported_otc_venue", tradable_sql)
        self.assertIn("AND NOT", tradable_sql)

    @patch("services.reference_gateway.active_tickers.load_open_active_ticker_issue_tickers", return_value=[])
    @patch("services.reference_gateway.active_tickers.load_current_active_symbols", return_value=[])
    @patch("services.reference_gateway.active_tickers.MassiveReferenceClient")
    def test_active_ticker_discovery_excludes_otc_before_detail_requests(
        self,
        massive_type: Mock,
        _current: Mock,
        _issues: Mock,
    ) -> None:
        massive = massive_type.return_value
        massive.fetch_active_us_stock_tickers.return_value = SimpleNamespace(
            tickers=[
                {"ticker": "OTCX", "primary_exchange": "OTCM"},
                {"ticker": "LIST", "primary_exchange": "XNAS"},
            ],
            pages=1,
            saturated=False,
        )
        config = SimpleNamespace(
            massive_base_url="https://example.invalid",
            active_ticker_page_limit=1000,
            active_ticker_max_pages=10,
            active_ticker_new_candidate_limit=0,
            ibkr_base_url="https://example.invalid",
        )

        plan = run_active_ticker_plan(config)

        self.assertEqual(plan.excluded_otc_tickers, 1)
        self.assertEqual(plan.missing_tickers, 1)
        massive.fetch_ticker_overview.assert_not_called()

    def test_graph_writer_refuses_otc_candidate_even_with_complete_identity(self) -> None:
        candidate = MissingTickerCandidate(
            ticker="OTCX",
            name="OTC Example",
            market="stocks",
            locale="us",
            primary_exchange="OTCM",
            currency_symbol="USD",
            cik="0000000001",
            composite_figi="BBG000OTC001",
            share_class_figi="BBG000OTC002",
            ticker_type="CS",
            missing_reason="missing",
            overview={"primary_exchange": "OTCM"},
            ibkr_candidates=[{"symbol": "OTCX", "conid": "1", "sec_type": "STK", "currency": "USD", "exact_symbol": True}],
            proposed_action="candidate_ready_for_dry_run_graph_resolution",
        )
        graph = ExistingGraph(
            exchanges={"OTCM": {"exchange_code": "OTCM", "iso_country_code": "US"}},
            ticker_type_id_by_provider_code={"CS": "ticker-type:cs"},
            issuer_by_cik={},
            duplicate_ciks=set(),
            security_by_figi={},
            duplicate_figis=set(),
            listing_by_key={},
            massive_listing_ids_by_ticker={},
        )

        rows, issues = build_candidate_rows(candidate, graph, "test", datetime(2026, 8, 7, tzinfo=UTC))

        self.assertTrue(all(not table_rows for table_rows in rows.values()))
        self.assertEqual([item.issue_type for item in issues], ["out_of_scope_otc"])

    def test_graph_writer_allows_massive_listing_beside_same_ticker_foreign_rows(self) -> None:
        candidate = complete_candidate()
        graph = complete_graph()

        rows, issues = build_candidate_rows(candidate, graph, "test", datetime(2026, 9, 3, tzinfo=UTC))

        self.assertEqual(issues, [])
        self.assertEqual(rows["id_listing_v1"][0]["ibkr_conid"], "123456789")
        self.assertEqual(rows["id_symbol_v1"][0]["source_system"], "massive")
        self.assertEqual(rows["id_symbol_v1"][0]["ticker"], "ZZZT")

    def test_graph_writer_replaces_wrong_conid_on_same_security_primary_listing(self) -> None:
        candidate = complete_candidate()
        graph = complete_graph()
        security_id = "security:figi:" + stable_key("BBG000TEST02")
        key = (security_id, "NASDAQ", "USD")
        graph.listing_by_key[key] = {
            "listing_id": "listing:existing",
            "security_id": security_id,
            "exchange_code": "NASDAQ",
            "currency_code": "USD",
            "ibkr_conid": "999",
            "board_code": None,
            "segment_name": None,
            "listing_status": "active",
            "is_primary_listing": 1,
            "list_date": None,
            "delisted_date": None,
            "first_seen_at_utc": "2026-01-01 00:00:00.000",
            "last_seen_at_utc": "2026-01-01 00:00:00.000",
            "source_run_id": "old",
            "source_content_sha256": "old",
            "inserted_at": "2026-01-01 00:00:00.000",
        }

        rows, issues = build_candidate_rows(candidate, graph, "test", datetime(2026, 9, 3, tzinfo=UTC))

        self.assertEqual(issues, [])
        self.assertEqual(len(rows["id_listing_v1"]), 1)
        self.assertEqual(rows["id_listing_v1"][0]["listing_id"], "listing:existing")
        self.assertEqual(rows["id_listing_v1"][0]["ibkr_conid"], "123456789")


def complete_candidate() -> MissingTickerCandidate:
    return MissingTickerCandidate(
        ticker="ZZZT",
        name="ZZZ Test Corp",
        market="stocks",
        locale="us",
        primary_exchange="XNAS",
        currency_symbol="USD",
        cik="1234567890",
        composite_figi="BBG000TEST01",
        share_class_figi="BBG000TEST02",
        ticker_type="CS",
        missing_reason="missing",
        overview={"primary_exchange": "XNAS"},
        ibkr_candidates=[
            {
                "symbol": "ZZZT",
                "conid": "123456789",
                "sec_type": "STK",
                "security_type": "COMMON",
                "listing_exchange": "NASDAQ",
                "currency": "USD",
                "country_code": "US",
                "is_us": True,
                "company_name": "ZZZ Test Corp",
            }
        ],
        proposed_action="candidate_ready_for_dry_run_graph_resolution",
    )


def complete_graph() -> ExistingGraph:
    return ExistingGraph(
        exchanges={"NASDAQ": {"exchange_code": "NASDAQ", "iso_country_code": "US", "mic": "XNAS"}},
        ticker_type_id_by_provider_code={"CS": "ticker-type:cs"},
        issuer_by_cik={},
        duplicate_ciks=set(),
        security_by_figi={},
        duplicate_figis=set(),
        listing_by_key={},
        massive_listing_ids_by_ticker={},
    )


if __name__ == "__main__":
    unittest.main()

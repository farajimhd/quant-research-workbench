from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipelines.reference_data.migration.step_02c_report_weak_reference_candidates import weak_candidate_report_sql
from pipelines.reference_data.migration.step_06_build_q_live_bridge_features import build_specs
from services.reference_gateway.active_tickers import MissingTickerCandidate, run_active_ticker_plan
from services.reference_gateway.audit import active_stock_base_query
from services.reference_gateway.canonical_graph_writer import ExistingGraph, build_candidate_rows
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
            listing_keys=set(),
            symbol_tickers=set(),
        )

        rows, issues = build_candidate_rows(candidate, graph, "test", datetime(2026, 8, 7, tzinfo=UTC))

        self.assertTrue(all(not table_rows for table_rows in rows.values()))
        self.assertEqual([item.issue_type for item in issues], ["out_of_scope_otc"])


if __name__ == "__main__":
    unittest.main()

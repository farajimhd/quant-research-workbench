from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from services.reference_gateway.active_tickers import MissingTickerCandidate
from services.reference_gateway.canonical_graph_writer import ExistingGraph, build_candidate_rows
from services.reference_gateway.table_groups import table_group_by_id


def main() -> None:
    candidate = MissingTickerCandidate(
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
        missing_reason="massive_active_ticker_not_in_id_symbol_v1",
        overview={"active": True, "ticker": "ZZZT", "name": "ZZZ Test Corp", "primary_exchange": "XNAS", "currency_name": "usd", "cik": "1234567890"},
        ibkr_candidates=[{"symbol": "ZZZT", "conid": "123456789", "sec_type": "STK", "exchange": "SMART", "listing_exchange": "NASDAQ", "currency": "USD", "company_name": "ZZZ Test Corp", "exact_symbol": True}],
        proposed_action="candidate_ready_for_dry_run_graph_resolution",
    )
    graph = ExistingGraph(
        exchanges={"NASDAQ": {"exchange_code": "NASDAQ", "acronym": "NASDAQ", "mic": "XNAS", "operating_mic": "XNAS", "iso_country_code": "US", "status": "active"}},
        ticker_type_id_by_provider_code={"CS": "ticker-type:stocks:cs"},
        issuer_by_cik={},
        duplicate_ciks=set(),
        security_by_figi={},
        duplicate_figis=set(),
        listing_keys=set(),
        symbol_tickers=set(),
    )
    rows, issues = build_candidate_rows(candidate, graph, "smoke", datetime.now(UTC))
    assert not issues, issues
    assert len(rows["id_issuer_v1"]) == 1
    assert len(rows["id_security_v1"]) == 1
    assert len(rows["id_listing_v1"]) == 1
    assert len(rows["id_symbol_v1"]) == 1
    assert rows["id_listing_v1"][0]["ibkr_conid"] == "123456789"
    assert rows["id_symbol_v1"][0]["ticker"] == "ZZZT"
    bad_candidate = replace(candidate, ticker="BADT", cik="", share_class_figi="", composite_figi="", overview={}, ibkr_candidates=[])
    _, bad_issues = build_candidate_rows(bad_candidate, graph, "smoke", datetime.now(UTC))
    assert {issue.issue_type for issue in bad_issues} >= {"missing_durable_issuer_identifier", "missing_figi_security_identifier", "missing_unique_ibkr_conid"}
    mapping_group = table_group_by_id("source_mapping_and_issues")
    assert mapping_group is not None
    assert "id_sec_market_bridge_v3" in mapping_group.tables
    assert "id_issuer_relationship_v1" in mapping_group.tables
    assert "id_sec_market_bridge_v1" not in mapping_group.tables
    schedule_group = table_group_by_id("source_schedule")
    assert schedule_group is not None
    assert schedule_group.tables == ("market_reference_source_schedule_v1",)
    assert table_group_by_id("reference_alerts") is None
    assert table_group_by_id("canonical_security_facts") is None
    print("reference_gateway_smoke_test=passed")


if __name__ == "__main__":
    main()

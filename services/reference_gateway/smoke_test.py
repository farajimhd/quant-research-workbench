from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from services.reference_gateway.active_tickers import ActiveTickerPlan, MissingTickerCandidate
from services.reference_gateway.alerts import alert_row, build_active_ticker_alerts, build_audit_alerts
from services.reference_gateway.audit import AuditCheck, ReferenceAuditReport
from services.reference_gateway.canonical_graph_writer import ExistingGraph, build_candidate_rows


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
    plan = ActiveTickerPlan(
        checked_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        provider_rows=1,
        provider_pages=1,
        provider_saturated=False,
        known_active_symbols=0,
        missing_tickers=1,
        overview_fetched=0,
        ibkr_searched=0,
        candidate_limit=1,
        candidates=[replace(bad_candidate, proposed_action="open_mapping_issue_missing_unique_ibkr_conid")],
        wall_seconds=0.01,
    )
    mapping_alerts = build_active_ticker_alerts(plan)
    assert len(mapping_alerts) == 1
    mapping_row = alert_row(mapping_alerts[0])
    assert mapping_row["alert_family"] == "tradability_guardrail"
    assert mapping_row["affects_tradability"] == 1
    audit_report = ReferenceAuditReport(
        status="failed",
        checked_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        database="q_live",
        read_database="q_live",
        write_database="q_live",
        wall_seconds=0.01,
        checks=[AuditCheck("required_tables", "error", "failed", 1, "Missing required reference tables.")],
    )
    audit_alerts = build_audit_alerts(audit_report, report_path="smoke.json")
    assert len(audit_alerts) == 1
    assert alert_row(audit_alerts[0])["alert_group"] == "reference_audit"
    print("reference_gateway_smoke_test=passed")


if __name__ == "__main__":
    main()

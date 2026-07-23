from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from src.backend.real_live_market_data.startup import logo_asset_url
from src.backend.ticker_facts_service import (
    FUNDAMENTAL_TAGS,
    XBRL_HISTORY_START,
    analyze_fundamentals,
    financial_card_and_scores,
    select_fundamentals,
    share_base_card,
    valuation_card_from_facts,
)


SCANNER_SCHEMA_VERSION = "canvas_historical_scanner_v1"
SCANNER_TABLE = "q_live.canvas_historical_scanner_v1"
SCANNER_TECHNICAL_SCHEMA_VERSION = "canvas_scanner_technical_v3"
SCANNER_TECHNICAL_TABLE = "q_live.canvas_scanner_technical_v3"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NEW_YORK = ZoneInfo("America/New_York")
EXTENDED_SESSION_START_MINUTE = 4 * 60
EXTENDED_SESSION_END_MINUTE = 20 * 60
EXTENDED_SESSION_DURATION_US = 16 * 60 * 60 * 1_000_000
SCANNER_TECHNICAL_WINDOWS: dict[str, int | None] = {
    "100ms": 100_000,
    "1s": 1_000_000,
    "5s": 5_000_000,
    "10s": 10_000_000,
    "30s": 30_000_000,
    "1m": 60_000_000,
    "5m": 5 * 60_000_000,
    "15m": 15 * 60_000_000,
    "30m": 30 * 60_000_000,
    "1h": 60 * 60_000_000,
    "1d": None,
    "extended_session": None,
    "regular_session": None,
}
SCANNER_TECHNICAL_METRICS = (
    "open",
    "high",
    "low",
    "change_pct",
    "volume",
    "dollar_volume",
    "trade_count",
    "quote_count",
    "vwap",
    "vwap_distance_pct",
    "vwap_trade",
    "vwap_trade_distance_pct",
    "relative_volume",
    "range_pct",
)
SCANNER_REFERENCE_FIELDS = (
    "company_name",
    "exchange",
    "country",
    "sector",
    "market_cap",
    "shares_outstanding",
    "float_shares",
    "short_interest",
    "short_crowding_pct",
    "days_to_cover",
)
SCANNER_FUNDAMENTAL_FIELDS = (
    "xbrl_quality_score", "xbrl_quality_label", "xbrl_quality_coverage_pct",
    "xbrl_profitability_score", "xbrl_growth_score", "xbrl_cash_quality_score",
    "xbrl_balance_sheet_score", "xbrl_capital_discipline_score",
    "financial_trajectory_score", "financial_trajectory_label",
    "financial_profitability_score", "financial_cash_generation_score", "financial_balance_sheet_score",
    "share_base_pressure_pct", "share_base_discipline_score", "valuation_pe", "valuation_label",
    "fundamental_free_cash_flow", "fundamental_gross_margin_pct", "fundamental_operating_margin_pct",
    "fundamental_net_margin_pct", "fundamental_free_cash_flow_margin_pct", "fundamental_return_on_assets_pct",
    "fundamental_return_on_equity_pct", "fundamental_working_capital", "fundamental_current_ratio",
    "fundamental_debt_to_equity", "fundamental_net_debt", "fundamental_interest_coverage",
    "fundamental_revenue_growth_pct", "fundamental_earnings_growth_pct", "fundamental_share_growth_pct",
    "fundamental_dilution_pct", "fundamental_cash_conversion", "fundamental_research_intensity_pct",
    "fundamental_sga_intensity_pct", "fundamental_latest_filing_at",
    "fundamental_revenue", "fundamental_gross_profit", "fundamental_operating_income",
    "fundamental_net_income", "fundamental_diluted_eps", "fundamental_operating_cash_flow",
    "fundamental_capital_expenditure", "fundamental_cash", "fundamental_current_assets",
    "fundamental_current_liabilities", "fundamental_accounts_receivable", "fundamental_accounts_payable",
    "fundamental_inventory", "fundamental_assets", "fundamental_liabilities", "fundamental_stockholders_equity",
    "fundamental_long_term_debt", "fundamental_current_debt", "fundamental_research_development",
    "fundamental_sga_expense", "fundamental_stock_based_compensation", "fundamental_interest_expense",
    "fundamental_income_tax_expense", "fundamental_effective_tax_rate_pct", "fundamental_goodwill",
    "fundamental_intangible_assets", "fundamental_deferred_revenue", "fundamental_debt_issued",
    "fundamental_debt_repaid", "fundamental_common_stock_issuance", "fundamental_common_shares_outstanding",
    "fundamental_weighted_average_basic_shares", "fundamental_weighted_average_diluted_shares",
    "fundamental_sec_public_float_value", "fundamental_dividends_per_share", "fundamental_share_repurchases",
    "fundamental_repurchased_shares",
)

_REPORTED_FUNDAMENTAL_KEYS = {
    "Revenue": "fundamental_revenue", "Gross profit": "fundamental_gross_profit",
    "Operating income": "fundamental_operating_income", "Net income": "fundamental_net_income",
    "Diluted EPS": "fundamental_diluted_eps", "Operating cash flow": "fundamental_operating_cash_flow",
    "Capital expenditure": "fundamental_capital_expenditure", "Cash": "fundamental_cash",
    "Current assets": "fundamental_current_assets", "Current liabilities": "fundamental_current_liabilities",
    "Accounts receivable": "fundamental_accounts_receivable", "Accounts payable": "fundamental_accounts_payable",
    "Inventory": "fundamental_inventory", "Assets": "fundamental_assets", "Liabilities": "fundamental_liabilities",
    "Stockholders' equity": "fundamental_stockholders_equity", "Long-term debt": "fundamental_long_term_debt",
    "Current debt": "fundamental_current_debt", "Research & development": "fundamental_research_development",
    "SG&A expense": "fundamental_sga_expense", "Stock-based compensation": "fundamental_stock_based_compensation",
    "Interest expense": "fundamental_interest_expense", "Income tax expense": "fundamental_income_tax_expense",
    "Effective tax rate": "fundamental_effective_tax_rate_pct", "Goodwill": "fundamental_goodwill",
    "Intangible assets": "fundamental_intangible_assets", "Deferred revenue": "fundamental_deferred_revenue",
    "Debt issued": "fundamental_debt_issued", "Debt repaid": "fundamental_debt_repaid",
    "Common-stock issuance": "fundamental_common_stock_issuance",
    "Common shares outstanding": "fundamental_common_shares_outstanding",
    "Weighted average basic shares": "fundamental_weighted_average_basic_shares",
    "Weighted average diluted shares": "fundamental_weighted_average_diluted_shares",
    "SEC public float value": "fundamental_sec_public_float_value",
    "Dividends per share": "fundamental_dividends_per_share", "Share repurchases": "fundamental_share_repurchases",
    "Repurchased shares": "fundamental_repurchased_shares",
}

_DERIVED_FUNDAMENTAL_KEYS = {
    "free_cash_flow": "fundamental_free_cash_flow", "gross_margin": "fundamental_gross_margin_pct",
    "operating_margin": "fundamental_operating_margin_pct", "net_margin": "fundamental_net_margin_pct",
    "free_cash_flow_margin": "fundamental_free_cash_flow_margin_pct",
    "return_on_assets": "fundamental_return_on_assets_pct", "return_on_equity": "fundamental_return_on_equity_pct",
    "working_capital": "fundamental_working_capital", "current_ratio": "fundamental_current_ratio",
    "debt_to_equity": "fundamental_debt_to_equity", "net_debt": "fundamental_net_debt",
    "interest_coverage": "fundamental_interest_coverage", "revenue_growth": "fundamental_revenue_growth_pct",
    "earnings_growth": "fundamental_earnings_growth_pct", "share_growth": "fundamental_share_growth_pct",
    "dilution": "fundamental_dilution_pct", "cash_conversion": "fundamental_cash_conversion",
    "research_intensity": "fundamental_research_intensity_pct", "sga_intensity": "fundamental_sga_intensity_pct",
}


def historical_scanner_snapshot(as_of: datetime, *, lookback_minutes: int = 15) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a causal full-universe scanner snapshot, materializing it once per source revision."""
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    lookback_minutes = max(5, min(int(lookback_minutes), 120))
    snapshot_at = as_of.astimezone(UTC).replace(second=0, microsecond=0)
    window_start = snapshot_at - timedelta(minutes=lookback_minutes)
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    source_database = os.environ.get("QMD_HISTORY_CLICKHOUSE_DATABASE", "market_sip_compact")
    table_prefix = os.environ.get("QMD_HISTORY_TABLE_PREFIX", "events_")
    if not IDENTIFIER.fullmatch(source_database) or not IDENTIFIER.fullmatch(table_prefix):
        raise ValueError("Historical scanner source identifiers are invalid.")
    source_revision = _source_revision(client, source_database, snapshot_at)
    _ensure_snapshot_table(client)
    rows = _cached_rows(client, snapshot_at, lookback_minutes, source_revision)
    materialized = False
    if not rows:
        _materialize_snapshot(
            client,
            source_database=source_database,
            table_prefix=table_prefix,
            snapshot_at=snapshot_at,
            window_start=window_start,
            lookback_minutes=lookback_minutes,
            source_revision=source_revision,
        )
        rows = _cached_rows(client, snapshot_at, lookback_minutes, source_revision)
        materialized = True
    return rows, {
        "complete_universe": True,
        "lookback_minutes": lookback_minutes,
        "materialized": materialized,
        "row_count": len(rows),
        "schema_version": SCANNER_SCHEMA_VERSION,
        "snapshot_at_utc": snapshot_at.isoformat(),
        "source_revision": source_revision,
        "window_start_utc": window_start.isoformat(),
    }


def historical_scanner_technical_projection(
    as_of: datetime,
    *,
    calculation_windows: list[str] | tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Project cached cross-sectional technical fields for each requested window.

    Interval metrics use exchange-session buckets. Anchored metrics use explicit
    session windows and do not acquire a synthetic bar timeframe. Each distinct
    calculation window is computed once for the full market and stored by source
    revision, avoiding per-symbol QMD calls on the interactive scanner path.
    """
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    requested = list(dict.fromkeys(str(value).strip() for value in calculation_windows if str(value).strip()))
    invalid = [value for value in requested if value not in SCANNER_TECHNICAL_WINDOWS]
    if invalid:
        raise ValueError(
            "Unsupported scanner technical calculation window(s): "
            f"{', '.join(invalid)}. Expected one of {', '.join(SCANNER_TECHNICAL_WINDOWS)}."
        )
    if not requested:
        return {}, {"technical_calculation_windows": [], "technical_materialized": []}
    cutoff = as_of.astimezone(UTC)
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    source_database = os.environ.get("QMD_HISTORY_CLICKHOUSE_DATABASE", "market_sip_compact")
    table_prefix = os.environ.get("QMD_HISTORY_TABLE_PREFIX", "events_")
    if not IDENTIFIER.fullmatch(source_database) or not IDENTIFIER.fullmatch(table_prefix):
        raise ValueError("Historical scanner source identifiers are invalid.")
    source_revision = _source_revision(client, source_database, cutoff)
    _ensure_technical_snapshot_table(client)
    projection: dict[str, dict[str, Any]] = defaultdict(dict)
    materialized: list[str] = []
    windows: dict[str, dict[str, str]] = {}
    for calculation_window in requested:
        window_start, window_end = scanner_technical_window(cutoff, calculation_window)
        windows[calculation_window] = {
            "window_start_utc": window_start.isoformat(),
            "window_end_utc": window_end.isoformat(),
        }
        rows = _cached_technical_rows(client, window_end, calculation_window, source_revision)
        if not rows:
            _materialize_technical_snapshot(
                client,
                source_database=source_database,
                table_prefix=table_prefix,
                snapshot_at=window_end,
                window_start=window_start,
                calculation_window=calculation_window,
                source_revision=source_revision,
            )
            rows = _cached_technical_rows(client, window_end, calculation_window, source_revision)
            materialized.append(calculation_window)
        for row in rows:
            ticker = str(row.pop("symbol", "")).upper()
            if not ticker:
                continue
            for metric in SCANNER_TECHNICAL_METRICS:
                value = row.get(metric)
                if value in (None, ""):
                    continue
                if metric == "vwap":
                    projection[ticker][_technical_field_key("vwap", calculation_window, "hlc3")] = value
                elif metric == "vwap_distance_pct":
                    projection[ticker][_technical_field_key("vwap_distance_pct", calculation_window, "hlc3")] = value
                elif metric == "vwap_trade":
                    projection[ticker][_technical_field_key("vwap", calculation_window, "trade_price")] = value
                elif metric == "vwap_trade_distance_pct":
                    projection[ticker][_technical_field_key("vwap_distance_pct", calculation_window, "trade_price")] = value
                else:
                    projection[ticker][_technical_field_key(metric, calculation_window)] = value
    return dict(projection), {
        "technical_materialized": materialized,
        "technical_schema_version": SCANNER_TECHNICAL_SCHEMA_VERSION,
        "technical_calculation_windows": requested,
        "technical_windows": windows,
    }


def scanner_technical_window(as_of: datetime, calculation_window: str) -> tuple[datetime, datetime]:
    """Return the causal calculation window ending no later than ``as_of``.

    Interval calculations use the latest bucket on the 04:00-20:00 New York
    grid. Anchored calculations use either the complete extended session or
    regular trading session to date; an anchor is not a bar timeframe.
    """
    if calculation_window not in SCANNER_TECHNICAL_WINDOWS:
        raise ValueError(f"Unsupported scanner technical calculation window: {calculation_window}")
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    local = as_of.astimezone(NEW_YORK)
    anchored = calculation_window in {"1d", "extended_session", "regular_session"}
    session_start_minute = 9 * 60 + 30 if calculation_window == "regular_session" else EXTENDED_SESSION_START_MINUTE
    session_end_minute = 16 * 60 if calculation_window == "regular_session" else EXTENDED_SESSION_END_MINUTE
    minute_of_day = local.hour * 60 + local.minute
    if minute_of_day < session_start_minute:
        session_date = _previous_weekday(local.date())
        local_end = datetime.combine(
            session_date,
            datetime.min.time(),
            NEW_YORK,
        ) + timedelta(minutes=session_end_minute)
    else:
        session_date = local.date()
        session_close = datetime.combine(session_date, datetime.min.time(), NEW_YORK) + timedelta(
            minutes=session_end_minute
        )
        local_end = min(local, session_close)
    session_open = datetime.combine(session_date, datetime.min.time(), NEW_YORK) + timedelta(minutes=session_start_minute)
    if anchored:
        local_start = session_open
    else:
        resolution_us = int(SCANNER_TECHNICAL_WINDOWS[calculation_window] or 0)
        elapsed_us = max(1, int((local_end - session_open).total_seconds() * 1_000_000))
        bucket_index = max(0, (elapsed_us - 1) // resolution_us)
        local_start = session_open + timedelta(microseconds=bucket_index * resolution_us)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def historical_scanner_reference_projection(as_of: datetime) -> dict[str, dict[str, Any]]:
    """Batch-project point-in-time identity, supply, market, and short facts for the scanner universe."""
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    cutoff = as_of.astimezone(UTC)
    cutoff_sql = sql_string(_clock(cutoff))
    date_sql = sql_string(cutoff.date().isoformat())
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    rows = _json_rows(
        client.execute(
            f"""
            WITH
                parseDateTime64BestEffort({cutoff_sql}) AS cutoff,
                toDate({date_sql}) AS cutoff_date,
                (
                    SELECT max(universe_date)
                    FROM q_live.feature_tradable_universe_v1 FINAL
                    WHERE universe_date <= cutoff_date AND inserted_at <= cutoff
                ) AS latest_universe_date,
                (
                    SELECT max(feature_date)
                    FROM q_live.feature_scanner_static_v1 FINAL
                ) AS latest_scanner_date
            SELECT
                u.ticker AS ticker,
                u.exchange_code AS exchange,
                coalesce(nullIf(s.security_name, ''), nullIf(i.legal_name, ''), nullIf(i.issuer_name, '')) AS company_name,
                coalesce(nullIf(c.effective_country_code, ''), nullIf(i.domicile_country_code, '')) AS country,
                coalesce(nullIf(i.sector, ''), nullIf(i.industry, ''), nullIf(i.sic_description, '')) AS sector,
                m.market_cap AS market_cap,
                coalesce(f.shares_outstanding, m.shares_outstanding) AS shares_outstanding,
                f.free_float AS float_shares,
                si.short_interest AS short_interest,
                if(f.free_float > 0 AND si.short_interest IS NOT NULL,
                   toFloat64(si.short_interest) / toFloat64(f.free_float) * 100, NULL) AS short_crowding_pct,
                si.days_to_cover AS days_to_cover,
                ifNull(a.relative_path, '') AS logo_relative_path
            FROM
            (
                SELECT
                    upper(ticker) AS ticker,
                    argMax(symbol_id, inserted_at) AS symbol_id,
                    argMax(security_id, inserted_at) AS security_id,
                    argMax(issuer_id, inserted_at) AS issuer_id,
                    argMax(listing_id, inserted_at) AS listing_id,
                    argMax(exchange_code, inserted_at) AS exchange_code
                FROM q_live.feature_tradable_universe_v1 FINAL
                WHERE universe_date = latest_universe_date AND inserted_at <= cutoff AND is_tradable = 1
                GROUP BY ticker
            ) AS u
            LEFT JOIN
            (
                SELECT security_id, argMax(security_name, inserted_at) AS security_name
                FROM q_live.id_security_v1 FINAL
                WHERE inserted_at <= cutoff
                GROUP BY security_id
            ) AS s ON s.security_id = u.security_id
            LEFT JOIN
            (
                SELECT
                    issuer_id,
                    argMax(legal_name, inserted_at) AS legal_name,
                    argMax(issuer_name, inserted_at) AS issuer_name,
                    argMax(domicile_country_code, inserted_at) AS domicile_country_code,
                    argMax(sector, inserted_at) AS sector,
                    argMax(industry, inserted_at) AS industry,
                    argMax(sic_description, inserted_at) AS sic_description,
                    argMax(logo_asset_id, inserted_at) AS logo_asset_id
                FROM q_live.id_issuer_v1 FINAL
                WHERE inserted_at <= cutoff
                GROUP BY issuer_id
            ) AS i ON i.issuer_id = u.issuer_id
            LEFT JOIN
            (
                SELECT symbol_id, listing_id, argMax(logo_asset_id, inserted_at) AS logo_asset_id
                FROM q_live.feature_scanner_static_v1 FINAL
                WHERE feature_date = latest_scanner_date
                GROUP BY symbol_id, listing_id
            ) AS scanner ON scanner.symbol_id = u.symbol_id AND scanner.listing_id = u.listing_id
            LEFT JOIN
            (
                SELECT issuer_id, argMax(logo_asset_id, inserted_at) AS logo_asset_id
                FROM q_live.id_issuer_v1 FINAL
                GROUP BY issuer_id
            ) AS current_branding ON current_branding.issuer_id = u.issuer_id
            LEFT JOIN
            (
                SELECT asset_id, argMax(relative_path, inserted_at) AS relative_path
                FROM q_live.market_presentation_asset_v1 FINAL
                WHERE asset_kind = 'logo' AND status = 'active'
                GROUP BY asset_id
            ) AS a ON a.asset_id = coalesce(scanner.logo_asset_id, current_branding.logo_asset_id, i.logo_asset_id)
            LEFT JOIN
            (
                SELECT symbol_id,
                    argMax(effective_country_code, tuple(assertion_date, inserted_at)) AS effective_country_code
                FROM q_live.market_security_country_v1 FINAL
                WHERE assertion_date <= cutoff_date AND inserted_at <= cutoff AND symbol_id IS NOT NULL
                GROUP BY symbol_id
            ) AS c ON c.symbol_id = u.symbol_id
            LEFT JOIN
            (
                SELECT symbol_id,
                    argMax(market_cap, tuple(observed_at_utc, inserted_at)) AS market_cap,
                    argMax(share_class_shares_outstanding, tuple(observed_at_utc, inserted_at)) AS shares_outstanding
                FROM q_live.market_security_market_snapshot_v1 FINAL
                WHERE observed_at_utc <= cutoff AND inserted_at <= cutoff
                GROUP BY symbol_id
            ) AS m ON m.symbol_id = u.symbol_id
            LEFT JOIN
            (
                SELECT symbol_id,
                    argMax(free_float, tuple(effective_date, inserted_at)) AS free_float,
                    argMax(shares_outstanding, tuple(effective_date, inserted_at)) AS shares_outstanding
                FROM q_live.market_security_float_v1 FINAL
                WHERE effective_date <= cutoff_date AND inserted_at <= cutoff
                GROUP BY symbol_id
            ) AS f ON f.symbol_id = u.symbol_id
            LEFT JOIN
            (
                SELECT symbol_id,
                    argMax(short_interest, tuple(coalesce(published_at_utc, toDateTime64(publication_date, 3, 'UTC'), toDateTime64(settlement_date, 3, 'UTC')), inserted_at)) AS short_interest,
                    argMax(days_to_cover, tuple(coalesce(published_at_utc, toDateTime64(publication_date, 3, 'UTC'), toDateTime64(settlement_date, 3, 'UTC')), inserted_at)) AS days_to_cover
                FROM q_live.market_short_interest_v1 FINAL
                WHERE settlement_date <= cutoff_date AND inserted_at <= cutoff
                  AND coalesce(published_at_utc, toDateTime64(publication_date, 3, 'UTC'), toDateTime64(settlement_date, 3, 'UTC')) <= cutoff
                GROUP BY symbol_id
            ) AS si ON si.symbol_id = u.symbol_id
            FORMAT JSONEachRow
            """
        )
    )
    projection: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        values = {
            field: row.get(field)
            for field in SCANNER_REFERENCE_FIELDS
            if row.get(field) not in (None, "")
        }
        logo_url = logo_asset_url(str(row.get("logo_relative_path") or ""))
        if logo_url:
            values["logo_url"] = logo_url
        projection[ticker] = values
    return projection


def historical_scanner_fundamental_projection(
    as_of: datetime,
    *,
    prices_by_ticker: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Calculate the Stock Facts and XBRL financial fields in one causal, set-based read."""
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    cutoff = as_of.astimezone(UTC)
    cutoff_sql = sql_string(_clock(cutoff))
    cutoff_date_sql = sql_string(cutoff.date().isoformat())
    tags = sorted({tag for _, alternatives in FUNDAMENTAL_TAGS for tag in alternatives})
    tag_clause = ", ".join(sql_string(tag) for tag in tags)
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    rows = _json_rows(
        client.execute(
            f"""
            WITH
                parseDateTime64BestEffort({cutoff_sql}) AS cutoff,
                toDate({cutoff_date_sql}) AS cutoff_date,
                (
                    SELECT max(universe_date)
                    FROM q_live.feature_tradable_universe_v1 FINAL
                    WHERE universe_date <= cutoff_date AND inserted_at <= cutoff
                ) AS latest_universe_date,
                universe AS
                (
                    SELECT
                        upper(u.ticker) AS ticker,
                        argMax(
                            replaceOne(u.issuer_id, 'issuer:cik:', ''),
                            tuple(u.is_tradable, u.currency_code = 'USD', u.product_type = 'STK', u.inserted_at)
                        ) AS cik
                    FROM q_live.feature_tradable_universe_v1 AS u FINAL
                    WHERE u.universe_date = latest_universe_date
                      AND u.inserted_at <= cutoff
                      AND notEmpty(u.ticker)
                      AND startsWith(u.issuer_id, 'issuer:cik:')
                    GROUP BY upper(u.ticker)
                )
            SELECT *
            FROM
            (
                SELECT
                    universe.ticker AS ticker,
                    f.tag AS tag,
                    f.taxonomy AS taxonomy,
                    f.unit_code AS unit_code,
                    f.value AS value,
                    f.fiscal_year AS fiscal_year,
                    f.fiscal_period AS fiscal_period,
                    f.period_end_date AS period_end_date,
                    f.filed_at_utc AS filed_at_utc,
                    f.form_type AS form_type,
                    f.accession_number AS accession_number,
                    f.recorded_at_utc AS recorded_at_utc
                FROM q_live.sec_xbrl_company_fact_v3 AS f FINAL
                INNER JOIN universe ON universe.cik = toString(f.cik)
                WHERE f.tag IN ({tag_clause})
                  AND f.filed_at_utc >= parseDateTime64BestEffort({sql_string(_clock(XBRL_HISTORY_START))})
                  AND f.filed_at_utc <= cutoff
                  AND f.recorded_at_utc <= cutoff
                ORDER BY ticker, tag, period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
                LIMIT 1 BY ticker, tag, period_end_date, fiscal_period, unit_code
            )
            ORDER BY ticker, tag, period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
            LIMIT 8 BY ticker, tag
            FORMAT JSONEachRow
            """
        )
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            grouped[ticker].append(row)

    prices = prices_by_ticker or {}
    projection: dict[str, dict[str, Any]] = {}
    for ticker, fundamental_rows in grouped.items():
        analysis = analyze_fundamentals(fundamental_rows)
        financial_card, financial_scores = financial_card_and_scores(fundamental_rows)
        share_card, share_score = share_base_card(fundamental_rows, [], [])
        valuation_card = valuation_card_from_facts(fundamental_rows, prices.get(ticker), None)
        facets = {str(facet.get("id") or ""): facet for facet in analysis.get("facets", [])}
        values: dict[str, Any] = {
            "xbrl_quality_score": analysis.get("score"),
            "xbrl_quality_label": analysis.get("label"),
            "xbrl_quality_coverage_pct": analysis.get("coverage_percent"),
            "financial_trajectory_score": financial_card.get("value"),
            "financial_trajectory_label": financial_card.get("label"),
            "financial_profitability_score": financial_scores.get("profitability"),
            "financial_cash_generation_score": financial_scores.get("cash_generation"),
            "financial_balance_sheet_score": financial_scores.get("balance_sheet"),
            "share_base_pressure_pct": share_card.get("value"),
            "share_base_discipline_score": share_score,
            "valuation_pe": valuation_card.get("value"),
            "valuation_label": valuation_card.get("label"),
            "fundamental_latest_filing_at": _utc_iso(max(
                (str(row.get("filed_at_utc") or "") for row in fundamental_rows), default="",
            )),
        }
        for facet_id, field in {
            "profitability": "xbrl_profitability_score", "growth": "xbrl_growth_score",
            "cash_quality": "xbrl_cash_quality_score", "balance_sheet": "xbrl_balance_sheet_score",
            "capital_discipline": "xbrl_capital_discipline_score",
        }.items():
            values[field] = facets.get(facet_id, {}).get("score")
        for metric in analysis.get("metrics", []):
            field = _DERIVED_FUNDAMENTAL_KEYS.get(str(metric.get("id") or ""))
            if field:
                values[field] = metric.get("value")
        for fact in select_fundamentals(fundamental_rows, cutoff):
            field = _REPORTED_FUNDAMENTAL_KEYS.get(str(fact.get("label") or ""))
            if field:
                values[field] = fact.get("value")
        projection[ticker] = {key: value for key, value in values.items() if value not in (None, "")}
    return projection


def _ensure_snapshot_table(client: ClickHouseHttpClient) -> None:
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCANNER_TABLE}
        (
            snapshot_at_utc DateTime64(6, 'UTC'),
            lookback_minutes UInt16,
            schema_version LowCardinality(String),
            source_revision String,
            symbol LowCardinality(String),
            last Float64,
            change_pct Float64,
            change_5m_pct Float64,
            volume Float64,
            trade_count UInt64,
            quote_count UInt64,
            materialized_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
        )
        ENGINE = ReplacingMergeTree(materialized_at_utc)
        PARTITION BY toYYYYMM(snapshot_at_utc)
        ORDER BY (snapshot_at_utc, lookback_minutes, source_revision, symbol)
        """
    )
    client.execute(f"ALTER TABLE {SCANNER_TABLE} ADD COLUMN IF NOT EXISTS schema_version LowCardinality(String) DEFAULT '' AFTER lookback_minutes")


def _ensure_technical_snapshot_table(client: ClickHouseHttpClient) -> None:
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCANNER_TECHNICAL_TABLE}
        (
            snapshot_at_utc DateTime64(6, 'UTC'),
            calculation_window LowCardinality(String),
            schema_version LowCardinality(String),
            source_revision String,
            symbol LowCardinality(String),
            open Float64,
            high Float64,
            low Float64,
            change_pct Float64,
            volume Float64,
            dollar_volume Float64,
            trade_count UInt64,
            quote_count UInt64,
            vwap Float64,
            vwap_distance_pct Float64,
            vwap_trade Float64,
            vwap_trade_distance_pct Float64,
            relative_volume Nullable(Float64),
            range_pct Float64,
            average_daily_volume Nullable(Float64),
            materialized_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
        )
        ENGINE = ReplacingMergeTree(materialized_at_utc)
        PARTITION BY toYYYYMM(snapshot_at_utc)
        ORDER BY (snapshot_at_utc, calculation_window, source_revision, symbol)
        """
    )


def _cached_technical_rows(
    client: ClickHouseHttpClient,
    snapshot_at: datetime,
    calculation_window: str,
    source_revision: str,
) -> list[dict[str, Any]]:
    return _json_rows(
        client.execute(
            f"""
            SELECT
                symbol, open, high, low, change_pct, volume, dollar_volume,
                trade_count, quote_count, vwap, vwap_distance_pct,
                vwap_trade, vwap_trade_distance_pct, relative_volume, range_pct
            FROM {SCANNER_TECHNICAL_TABLE} FINAL
            WHERE snapshot_at_utc = parseDateTime64BestEffort({sql_string(_clock(snapshot_at))})
              AND calculation_window = {sql_string(calculation_window)}
              AND schema_version = {sql_string(SCANNER_TECHNICAL_SCHEMA_VERSION)}
              AND source_revision = {sql_string(source_revision)}
            ORDER BY abs(change_pct) DESC, symbol ASC
            LIMIT 20000
            FORMAT JSONEachRow
            """
        )
    )


def _materialize_technical_snapshot(
    client: ClickHouseHttpClient,
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    calculation_window: str,
    source_revision: str,
) -> None:
    start_us = int(window_start.timestamp() * 1_000_000)
    end_us = int(snapshot_at.timestamp() * 1_000_000)
    elapsed_us = max(1, end_us - start_us)
    start_date = window_start.date().isoformat()
    end_date = snapshot_at.date().isoformat()
    selects = []
    for year in range(window_start.year, snapshot_at.year + 1):
        selects.append(
            f"""
            SELECT ticker, ordinal, event_meta, sip_timestamp_us, price_primary_int, size_primary
            FROM {source_database}.{table_prefix}{year}
            PREWHERE event_date >= toDate({sql_string(start_date)})
              AND event_date <= toDate({sql_string(end_date)})
            WHERE sip_timestamp_us >= {start_us} AND sip_timestamp_us < {end_us}
            """
        )
    source = " UNION ALL ".join(selects)
    session_date = snapshot_at.astimezone(NEW_YORK).date().isoformat()
    client.execute(
        f"""
        INSERT INTO {SCANNER_TECHNICAL_TABLE}
        (
            snapshot_at_utc, calculation_window, schema_version, source_revision, symbol,
            open, high, low, change_pct, volume, dollar_volume, trade_count, quote_count,
            vwap, vwap_distance_pct, vwap_trade, vwap_trade_distance_pct,
            relative_volume, range_pct, average_daily_volume
        )
        WITH
            {elapsed_us} AS elapsed_us,
            {EXTENDED_SESSION_DURATION_US} AS session_us
        SELECT
            parseDateTime64BestEffort({sql_string(_clock(snapshot_at))}),
            {sql_string(calculation_window)},
            {sql_string(SCANNER_TECHNICAL_SCHEMA_VERSION)},
            {sql_string(source_revision)},
            current.symbol,
            current.open,
            current.high,
            current.low,
            if(current.open = 0, 0, (current.last / current.open - 1) * 100),
            current.volume,
            current.dollar_volume,
            current.trade_count,
            current.quote_count,
            current.vwap,
            if(current.vwap = 0, 0, (current.last / current.vwap - 1) * 100),
            current.vwap_trade,
            if(current.vwap_trade = 0, 0, (current.last / current.vwap_trade - 1) * 100),
            if(prior.average_daily_volume > 0,
               current.volume / (prior.average_daily_volume * elapsed_us / session_us),
               NULL),
            if(current.low = 0, 0, (current.high / current.low - 1) * 100),
            prior.average_daily_volume
        FROM
        (
            SELECT
                ticker AS symbol,
                argMinIf(bar_open, minute_index, bar_trade_count > 0) AS open,
                maxIf(bar_high, bar_trade_count > 0) AS high,
                minIf(bar_low, bar_trade_count > 0) AS low,
                argMaxIf(bar_close, minute_index, bar_trade_count > 0) AS last,
                sum(bar_volume) AS volume,
                sum(bar_dollar_volume) AS dollar_volume,
                sum(bar_trade_count) AS trade_count,
                sum(bar_quote_count) AS quote_count,
                if(volume = 0, 0, sum(((bar_high + bar_low + bar_close) / 3) * bar_volume) / volume) AS vwap,
                if(volume = 0, 0, dollar_volume / volume) AS vwap_trade
            FROM
            (
                SELECT
                    ticker,
                    intDiv(sip_timestamp_us - {start_us}, 60000000) AS minute_index,
                    argMinIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS bar_open,
                    maxIf(price, is_trade) AS bar_high,
                    minIf(price, is_trade) AS bar_low,
                    argMaxIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS bar_close,
                    sumIf(toFloat64(size_primary), is_trade) AS bar_volume,
                    sumIf(price * toFloat64(size_primary), is_trade) AS bar_dollar_volume,
                    countIf(is_trade) AS bar_trade_count,
                    countIf(is_quote) AS bar_quote_count
                FROM
                (
                    SELECT
                        ticker,
                        ordinal,
                        sip_timestamp_us,
                        bitAnd(event_meta, 1) = 1 AND price_primary_int > 0 AND size_primary > 0 AS is_trade,
                        bitAnd(event_meta, 1) = 0 AS is_quote,
                        toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
                        size_primary
                    FROM ({source})
                )
                GROUP BY ticker, minute_index
            )
            GROUP BY ticker
            HAVING trade_count > 0
        ) AS current
        LEFT JOIN
        (
            SELECT sym, avg(size_sum) AS average_daily_volume
            FROM
            (
                SELECT sym, session_date, size_sum
                FROM {source_database}.macro_bars_by_time_symbol FINAL
                WHERE timeframe = '1d'
                  AND bar_family = 'trade'
                  AND session_date < toDate({sql_string(session_date)})
                  AND session_date >= toDate({sql_string(session_date)}) - INTERVAL 35 DAY
                ORDER BY session_date DESC
                LIMIT 20 BY sym
            )
            GROUP BY sym
        ) AS prior ON prior.sym = current.symbol
        """
    )


def _source_revision(client: ClickHouseHttpClient, database: str, snapshot_at: datetime) -> str:
    source_date = snapshot_at.date().isoformat()
    rows = _json_rows(
        client.execute(
            f"""
            SELECT
                sum(canonical_event_count) AS event_count,
                max(latest_build_step) AS build_step,
                toString(max(latest_updated_at)) AS updated_at
            FROM
            (
                SELECT
                    ticker,
                    argMax(event_count, tuple(build_step, updated_at)) AS canonical_event_count,
                    argMax(build_step, tuple(build_step, updated_at)) AS latest_build_step,
                    max(updated_at) AS latest_updated_at
                FROM {database}.events_ordinal_continuity
                WHERE source_date = toDate({sql_string(source_date)})
                GROUP BY ticker
            )
            FORMAT JSONEachRow
            """
        )
    )
    row = rows[0] if rows else {}
    return f"{int(row.get('build_step') or 0)}:{int(row.get('event_count') or 0)}:{row.get('updated_at') or ''}"


def _cached_rows(client: ClickHouseHttpClient, snapshot_at: datetime, lookback_minutes: int, source_revision: str) -> list[dict[str, Any]]:
    rows = _json_rows(
        client.execute(
            f"""
            SELECT symbol, last, change_pct, change_5m_pct, volume, trade_count, quote_count
            FROM {SCANNER_TABLE} FINAL
            WHERE snapshot_at_utc = parseDateTime64BestEffort({sql_string(_clock(snapshot_at))})
              AND lookback_minutes = {lookback_minutes}
              AND schema_version = {sql_string(SCANNER_SCHEMA_VERSION)}
              AND source_revision = {sql_string(source_revision)}
            ORDER BY abs(change_5m_pct) DESC, symbol ASC
            LIMIT 20000
            FORMAT JSONEachRow
            """
        )
    )
    return [{**row, "ticker": str(row.get("symbol") or "")} for row in rows]


def _materialize_snapshot(
    client: ClickHouseHttpClient,
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    lookback_minutes: int,
    source_revision: str,
) -> None:
    start_us = int(window_start.timestamp() * 1_000_000)
    end_us = int(snapshot_at.timestamp() * 1_000_000)
    five_minute_us = int((snapshot_at - timedelta(minutes=5)).timestamp() * 1_000_000)
    selects = []
    for year in range(window_start.year, snapshot_at.year + 1):
        selects.append(
            f"""
            SELECT ticker, ordinal, event_meta, sip_timestamp_us, price_primary_int, size_primary
            FROM {source_database}.{table_prefix}{year}
            PREWHERE sip_timestamp_us >= {start_us} AND sip_timestamp_us < {end_us}
            """
        )
    source = " UNION ALL ".join(selects)
    client.execute(
        f"""
        INSERT INTO {SCANNER_TABLE}
            (snapshot_at_utc, lookback_minutes, schema_version, source_revision, symbol, last, change_pct,
             change_5m_pct, volume, trade_count, quote_count)
        SELECT
            parseDateTime64BestEffort({sql_string(_clock(snapshot_at))}),
            {lookback_minutes},
            {sql_string(SCANNER_SCHEMA_VERSION)},
            {sql_string(source_revision)},
            ticker,
            last_price,
            if(first_price = 0, 0, (last_price / first_price - 1) * 100),
            if(first_5m_price = 0, 0, (last_price / first_5m_price - 1) * 100),
            volume,
            trade_count,
            quote_count
        FROM
        (
            SELECT
                ticker,
                argMaxIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS last_price,
                argMinIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS first_price,
                argMinIf(price, tuple(sip_timestamp_us, ordinal), is_trade AND sip_timestamp_us >= {five_minute_us}) AS first_5m_price,
                sumIf(toFloat64(size_primary), is_trade) AS volume,
                countIf(is_trade) AS trade_count,
                countIf(is_quote) AS quote_count
            FROM
            (
                SELECT
                    ticker,
                    ordinal,
                    sip_timestamp_us,
                    bitAnd(event_meta, 1) = 1 AND price_primary_int > 0 AND size_primary > 0 AS is_trade,
                    bitAnd(event_meta, 1) = 0 AS is_quote,
                    toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
                    size_primary
                FROM ({source})
            )
            GROUP BY ticker
        )
        WHERE trade_count > 0
        """
    )


def _clock(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _technical_field_key(
    metric: str,
    calculation_window: str,
    source: str | None = None,
) -> str:
    source_suffix = f"__{source}" if source else ""
    return f"technical__{metric}__{calculation_window}{source_suffix}"


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _utc_iso(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _json_rows(payload: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in payload.splitlines() if line.strip()]

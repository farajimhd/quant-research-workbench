from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import quote_ident, sql_string


QUERY_PLAN_ID = "reference.scanner_asof.v1"
QUERY_PLAN_VERSION = 2
NEW_YORK = ZoneInfo("America/New_York")


def scanner_reference_projection(cutoff: datetime, database: str = "q_live", *, tickers: Iterable[str] = ()) -> str:
    """Build the causal, set-based reference projection for Historical Scanner."""
    if cutoff.tzinfo is None:
        raise ValueError("scanner reference cutoff must include a timezone")
    db = quote_ident(database)
    instant = sql_string(
        cutoff.astimezone(UTC).isoformat(timespec="milliseconds")
    )
    cutoff_date = sql_string(cutoff.astimezone(NEW_YORK).date().isoformat())
    ticker_catalog = tuple(sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}))
    ticker_filter = f"\n              AND upper(ticker) IN ({', '.join(sql_string(ticker) for ticker in ticker_catalog)})" if ticker_catalog else ""
    return f"""
        WITH
            parseDateTime64BestEffort({instant}) AS cutoff,
            toDate({cutoff_date}) AS cutoff_date,
            (
                SELECT max(universe_date)
                FROM {db}.feature_tradable_universe_v1 FINAL
                WHERE universe_date <= cutoff_date AND inserted_at <= cutoff
            ) AS latest_universe_date,
            (
                SELECT max(feature_date)
                FROM {db}.feature_scanner_static_v1 FINAL
                WHERE feature_date <= cutoff_date AND inserted_at <= cutoff
            ) AS latest_scanner_date
        SELECT
            u.ticker AS ticker,
            u.symbol_id AS symbol_id,
            u.security_id AS security_id,
            u.issuer_id AS issuer_id,
            u.listing_id AS listing_id,
            u.exchange_code AS exchange,
            coalesce(nullIf(s.security_name, ''), nullIf(i.legal_name, ''), nullIf(i.issuer_name, '')) AS company_name,
            coalesce(nullIf(c.effective_country_code, ''), nullIf(i.domicile_country_code, '')) AS country,
            coalesce(nullIf(i.sector, ''), nullIf(i.industry, ''), nullIf(i.sic_description, '')) AS sector,
            nullIf(i.industry, '') AS industry,
            coalesce(m.market_cap, scanner.market_cap) AS market_cap,
            coalesce(f.shares_outstanding, m.shares_outstanding) AS shares_outstanding,
            coalesce(f.free_float, scanner.free_float) AS float_shares,
            nullIf(f.float_source_tag, '') AS float_source,
            multiIf(
                coalesce(f.free_float, scanner.free_float) IS NOT NULL, 'reported',
                coalesce(f.shares_outstanding, m.shares_outstanding) IS NOT NULL, 'shares_outstanding_only',
                'unavailable'
            ) AS float_quality,
            scanner.short_pressure_label AS short_pressure,
            coalesce(si.short_interest, scanner.short_interest) AS short_interest,
            if(coalesce(f.free_float, scanner.free_float) > 0 AND coalesce(si.short_interest, scanner.short_interest) IS NOT NULL,
               toFloat64(coalesce(si.short_interest, scanner.short_interest)) / toFloat64(coalesce(f.free_float, scanner.free_float)) * 100, NULL) AS short_crowding_pct,
            if(coalesce(f.free_float, scanner.free_float) > 0 AND coalesce(si.short_interest, scanner.short_interest) IS NOT NULL,
               toFloat64(coalesce(si.short_interest, scanner.short_interest)) / toFloat64(coalesce(f.free_float, scanner.free_float)) * 100, NULL) AS short_interest_pct,
            coalesce(si.days_to_cover, scanner.days_to_cover) AS days_to_cover,
            sv.short_volume AS short_volume,
            if(coalesce(sv.short_volume_ratio, scanner.short_volume_ratio) IS NULL, NULL,
               toFloat64(coalesce(sv.short_volume_ratio, scanner.short_volume_ratio)) * 100) AS short_volume_pct,
            if(empty(ftd.symbol_id), NULL, ftd.fails_quantity) AS fails_to_deliver,
            if(empty(ftd.symbol_id) OR ftd.fails_quantity IS NULL OR ftd.previous_close_price IS NULL, NULL,
               toFloat64(ftd.fails_quantity) * ftd.previous_close_price) AS ftd_value,
            ifNull(notEmpty(reg.symbol_id), false) AS reg_sho_threshold,
            nullIf(borrow.borrow_status, '') AS borrow_status,
            borrow.shortable_shares AS borrow_shares,
            coalesce(borrow.fee_rate, borrow.indicative_borrow_rate) AS borrow_fee,
            if(empty(ipo.symbol_id), NULL, ipo.listing_date) AS ipo_date,
            if(empty(ipo.symbol_id), NULL, dateDiff('day', cutoff_date, ipo.listing_date)) AS ipo_days_to_event,
            if(empty(split.symbol_id), NULL, split.execution_date) AS split_execution_date,
            if(empty(split.symbol_id), NULL, dateDiff('day', cutoff_date, split.execution_date)) AS split_days_to_event,
            u.ibkr_conid AS ibkr_conid,
            ifNull(a.relative_path, '') AS logo_relative_path
        FROM
        (
            SELECT
                upper(ticker) AS ticker,
                argMax(symbol_id, inserted_at) AS symbol_id,
                argMax(security_id, inserted_at) AS security_id,
                argMax(issuer_id, inserted_at) AS issuer_id,
                argMax(listing_id, inserted_at) AS listing_id,
                argMax(exchange_code, inserted_at) AS exchange_code,
                argMax(ibkr_conid, inserted_at) AS ibkr_conid
            FROM {db}.feature_tradable_universe_v1 FINAL
            WHERE universe_date = latest_universe_date
              AND inserted_at <= cutoff
              AND is_tradable = 1
              {ticker_filter}
            GROUP BY ticker
        ) AS u
        LEFT JOIN
        (
            SELECT security_id, argMax(security_name, inserted_at) AS security_name
            FROM {db}.id_security_v1 FINAL
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
            FROM {db}.id_issuer_v1 FINAL
            WHERE inserted_at <= cutoff
            GROUP BY issuer_id
        ) AS i ON i.issuer_id = u.issuer_id
        LEFT JOIN
        (
            SELECT symbol_id, listing_id,
                argMax(logo_asset_id, inserted_at) AS logo_asset_id,
                argMax(free_float, inserted_at) AS free_float,
                argMax(market_cap, inserted_at) AS market_cap,
                argMax(short_interest, inserted_at) AS short_interest,
                argMax(days_to_cover, inserted_at) AS days_to_cover,
                argMax(short_volume_ratio, inserted_at) AS short_volume_ratio,
                argMax(short_pressure_label, inserted_at) AS short_pressure_label
            FROM {db}.feature_scanner_static_v1 FINAL
            WHERE feature_date = latest_scanner_date AND inserted_at <= cutoff
            GROUP BY symbol_id, listing_id
        ) AS scanner ON scanner.symbol_id = u.symbol_id AND scanner.listing_id = u.listing_id
        LEFT JOIN
        (
            SELECT issuer_id, argMax(logo_asset_id, inserted_at) AS logo_asset_id
            FROM {db}.id_issuer_v1 FINAL
            WHERE inserted_at <= cutoff
            GROUP BY issuer_id
        ) AS current_branding ON current_branding.issuer_id = u.issuer_id
        LEFT JOIN
        (
            SELECT asset_id, argMax(relative_path, inserted_at) AS relative_path
            FROM {db}.market_presentation_asset_v1 FINAL
            WHERE asset_kind = 'logo' AND status = 'active' AND inserted_at <= cutoff
            GROUP BY asset_id
        ) AS a ON a.asset_id = coalesce(scanner.logo_asset_id, current_branding.logo_asset_id, i.logo_asset_id)
        LEFT JOIN
        (
            SELECT symbol_id,
                argMax(effective_country_code, tuple(assertion_date, inserted_at)) AS effective_country_code
            FROM {db}.market_security_country_v1 FINAL
            WHERE assertion_date <= cutoff_date
              AND inserted_at <= cutoff
              AND symbol_id IS NOT NULL
            GROUP BY symbol_id
        ) AS c ON c.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT symbol_id,
                argMax(market_cap, tuple(observed_at_utc, inserted_at)) AS market_cap,
                argMax(share_class_shares_outstanding, tuple(observed_at_utc, inserted_at)) AS shares_outstanding
            FROM {db}.market_security_market_snapshot_v1 FINAL
            WHERE observed_at_utc <= cutoff AND inserted_at <= cutoff
            GROUP BY symbol_id
        ) AS m ON m.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT symbol_id,
                argMax(free_float, tuple(effective_date, inserted_at)) AS free_float,
                argMax(shares_outstanding, tuple(effective_date, inserted_at)) AS shares_outstanding,
                argMax(float_source_tag, tuple(effective_date, inserted_at)) AS float_source_tag
            FROM {db}.market_security_float_v1 FINAL
            WHERE effective_date <= cutoff_date AND inserted_at <= cutoff
            GROUP BY symbol_id
        ) AS f ON f.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT symbol_id,
                argMax(short_interest, tuple(coalesce(published_at_utc, toDateTime64(publication_date, 3, 'UTC'), toDateTime64(settlement_date, 3, 'UTC')), inserted_at)) AS short_interest,
                argMax(days_to_cover, tuple(coalesce(published_at_utc, toDateTime64(publication_date, 3, 'UTC'), toDateTime64(settlement_date, 3, 'UTC')), inserted_at)) AS days_to_cover
            FROM {db}.market_short_interest_v1 FINAL
            WHERE settlement_date <= cutoff_date
              AND inserted_at <= cutoff
              AND coalesce(published_at_utc, toDateTime64(publication_date, 3, 'UTC'), toDateTime64(settlement_date, 3, 'UTC')) <= cutoff
            GROUP BY symbol_id
        ) AS si ON si.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT symbol_id,
                argMax(short_volume, tuple(coalesce(published_at_utc, toDateTime64(trade_date, 3, 'UTC')), inserted_at)) AS short_volume,
                argMax(short_volume_ratio, tuple(coalesce(published_at_utc, toDateTime64(trade_date, 3, 'UTC')), inserted_at)) AS short_volume_ratio
            FROM {db}.market_short_volume_v1 FINAL
            WHERE trade_date <= cutoff_date AND inserted_at <= cutoff
              AND coalesce(published_at_utc, toDateTime64(trade_date, 3, 'UTC')) <= cutoff
            GROUP BY symbol_id
        ) AS sv ON sv.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT symbol_id,
                argMax(fails_quantity, tuple(settlement_date, inserted_at)) AS fails_quantity,
                argMax(previous_close_price, tuple(settlement_date, inserted_at)) AS previous_close_price
            FROM {db}.market_fails_to_deliver_v1 FINAL
            WHERE settlement_date <= cutoff_date AND inserted_at <= cutoff AND symbol_id IS NOT NULL
            GROUP BY symbol_id
        ) AS ftd ON ftd.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT symbol_id, argMax(threshold_status, tuple(threshold_date, inserted_at)) AS threshold_status
            FROM {db}.market_reg_sho_threshold_v1 FINAL
            WHERE threshold_date <= cutoff_date AND inserted_at <= cutoff AND symbol_id IS NOT NULL
            GROUP BY symbol_id
        ) AS reg ON reg.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT symbol_id,
                argMax(borrow_status, tuple(observed_at_utc, inserted_at)) AS borrow_status,
                argMax(shortable_shares, tuple(observed_at_utc, inserted_at)) AS shortable_shares,
                argMax(indicative_borrow_rate, tuple(observed_at_utc, inserted_at)) AS indicative_borrow_rate,
                argMax(fee_rate, tuple(observed_at_utc, inserted_at)) AS fee_rate
            FROM {db}.market_security_borrow_v1 FINAL
            WHERE observed_at_utc <= cutoff AND inserted_at <= cutoff AND symbol_id IS NOT NULL
            GROUP BY symbol_id
        ) AS borrow ON borrow.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT
                symbol_id,
                argMin(
                    listing_date,
                    tuple(abs(dateDiff('day', cutoff_date, listing_date)), listing_date, inserted_at)
                ) AS listing_date
            FROM {db}.market_ipo_v1 FINAL
            WHERE inserted_at <= cutoff
            GROUP BY symbol_id
        ) AS ipo ON ipo.symbol_id = u.symbol_id
        LEFT JOIN
        (
            SELECT
                symbol_id,
                argMin(
                    execution_date,
                    tuple(abs(dateDiff('day', cutoff_date, execution_date)), execution_date, inserted_at)
                ) AS execution_date
            FROM {db}.market_stock_split_v1 FINAL
            WHERE inserted_at <= cutoff
            GROUP BY symbol_id
        ) AS split ON split.symbol_id = u.symbol_id
        SETTINGS join_use_nulls = 1
        FORMAT JSONEachRow
    """

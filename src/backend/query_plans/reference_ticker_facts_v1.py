from __future__ import annotations

from datetime import UTC, datetime

from research.mlops.clickhouse import quote_ident, sql_string


def identity_anchor(ticker: str, cutoff: datetime, database: str) -> str:
    """Version-1 point-in-time identity anchor for one validated ticker."""
    if cutoff.tzinfo is None:
        raise ValueError("identity cutoff must include a timezone")
    db = quote_ident(database)
    symbol = sql_string(ticker)
    instant = sql_string(clickhouse_timestamp(cutoff))
    day = sql_string(cutoff.date().isoformat())
    return f"""
        WITH (SELECT max(universe_date) FROM {db}.feature_tradable_universe_v1 FINAL
              WHERE universe_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS latest_date
        SELECT
            u.symbol_id AS symbol_id, u.listing_id AS listing_id, u.security_id AS security_id,
            u.issuer_id AS issuer_id, u.ticker AS ticker, u.exchange_code AS exchange_code,
            u.currency_code AS currency_code, u.ibkr_conid AS ibkr_conid, u.product_type AS product_type,
            u.asset_class AS asset_class, u.is_tradable AS is_tradable, u.exclusion_reason AS exclusion_reason,
            u.universe_date AS universe_date, s.display_name AS display_name,
            s.instrument_type AS instrument_type, s.security_type AS security_type,
            listing.list_date AS list_date, sec.security_name AS security_name, sec.has_options AS has_options,
            issuer.issuer_name AS issuer_name, issuer.legal_name AS legal_name,
            issuer.branding_name AS branding_name, issuer.entity_type AS entity_type,
            issuer.domicile_country_code AS domicile_country_code,
            issuer.state_of_incorporation AS state_of_incorporation, issuer.sic_code AS sic_code,
            issuer.sic_description AS sic_description, issuer.sector AS sector,
            issuer.industry AS industry, issuer.industry_group AS industry_group,
            issuer.website_url AS website_url, issuer.investor_website_url AS investor_website_url,
            issuer.last_verified_at_utc AS last_verified_at_utc
        FROM {db}.feature_tradable_universe_v1 AS u FINAL
        LEFT JOIN {db}.id_symbol_v1 AS s FINAL
            ON s.symbol_id = u.symbol_id AND s.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_listing_v1 AS listing FINAL
            ON listing.listing_id = u.listing_id AND listing.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_security_v1 AS sec FINAL
            ON sec.security_id = u.security_id AND sec.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL
            ON issuer.issuer_id = u.issuer_id AND issuer.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        WHERE u.universe_date = latest_date AND upper(u.ticker) = {symbol}
        ORDER BY u.is_tradable DESC, u.currency_code = 'USD' DESC, u.product_type = 'STK' DESC, u.exchange_code ASC
        LIMIT 1
        FORMAT JSONEachRow
    """


def clickhouse_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")

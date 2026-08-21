from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from research.mlops.clickhouse import quote_ident, sql_string
from src.backend.query_plans.market_daily_bars_v1 import daily_session_trade_bars_relation_sql


HISTORY_LIMIT = 10_000
MAIN_HISTORY_DAYS = 520


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
            issuer.last_verified_at_utc AS last_verified_at_utc,
            profile.latest_issuer_name AS sec_issuer_name,
            profile.latest_incorporation_jurisdiction AS sec_incorporation_jurisdiction,
            profile.latest_issuer_legal_country_code AS issuer_legal_country_code,
            profile.latest_issuer_business_country_code AS issuer_business_country_code,
            profile.latest_business_address_line1 AS business_address_line1,
            profile.latest_business_address_line2 AS business_address_line2,
            profile.latest_business_address_line3 AS business_address_line3,
            profile.latest_business_address_city AS business_address_city,
            profile.latest_business_address_state_or_province AS business_address_state_or_province,
            profile.latest_business_address_postal_code AS business_address_postal_code,
            profile.latest_source_kind AS company_profile_source_kind,
            profile.latest_source_accession_number AS company_profile_accession_number,
            profile.profile_available_at_utc AS company_profile_available_at_utc,
            profile.business_address_source_kind AS business_address_source_kind,
            profile.business_address_accession_number AS business_address_accession_number,
            profile.business_address_available_at_utc AS business_address_available_at_utc,
            profile.business_country_source_kind AS business_country_source_kind,
            profile.business_country_accession_number AS business_country_accession_number,
            profile.business_country_available_at_utc AS business_country_available_at_utc,
            profile.legal_country_source_kind AS legal_country_source_kind,
            profile.legal_country_accession_number AS legal_country_accession_number,
            profile.legal_country_available_at_utc AS legal_country_available_at_utc,
            country.latest_listing_country_code AS listing_country_code,
            country.latest_effective_country_code AS effective_country_code
        FROM {db}.feature_tradable_universe_v1 AS u FINAL
        LEFT JOIN {db}.id_symbol_v1 AS s FINAL
            ON s.symbol_id = u.symbol_id AND s.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_listing_v1 AS listing FINAL
            ON listing.listing_id = u.listing_id AND listing.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_security_v1 AS sec FINAL
            ON sec.security_id = u.security_id AND sec.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL
            ON issuer.issuer_id = u.issuer_id AND issuer.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN
        (
            SELECT
                issuer_id,
                argMaxIf(issuer_name, tuple(available_at_utc, inserted_at), ifNull(issuer_name, '') != '') AS latest_issuer_name,
                argMaxIf(incorporation_jurisdiction, tuple(available_at_utc, inserted_at), ifNull(incorporation_jurisdiction, '') != '') AS latest_incorporation_jurisdiction,
                argMaxIf(issuer_legal_country_code, tuple(available_at_utc, inserted_at), ifNull(issuer_legal_country_code, '') != '') AS latest_issuer_legal_country_code,
                argMaxIf(issuer_business_country_code, tuple(available_at_utc, inserted_at), ifNull(issuer_business_country_code, '') != '') AS latest_issuer_business_country_code,
                argMaxIf(business_address_line1, tuple(available_at_utc, inserted_at), ifNull(business_address_line1, '') != '') AS latest_business_address_line1,
                argMaxIf(business_address_line2, tuple(available_at_utc, inserted_at), ifNull(business_address_line2, '') != '') AS latest_business_address_line2,
                argMaxIf(business_address_line3, tuple(available_at_utc, inserted_at), ifNull(business_address_line3, '') != '') AS latest_business_address_line3,
                argMaxIf(business_address_city, tuple(available_at_utc, inserted_at), ifNull(business_address_city, '') != '') AS latest_business_address_city,
                argMaxIf(business_address_state_or_province, tuple(available_at_utc, inserted_at), ifNull(business_address_state_or_province, '') != '') AS latest_business_address_state_or_province,
                argMaxIf(business_address_postal_code, tuple(available_at_utc, inserted_at), ifNull(business_address_postal_code, '') != '') AS latest_business_address_postal_code,
                argMax(source_kind, tuple(available_at_utc, inserted_at)) AS latest_source_kind,
                argMax(source_accession_number, tuple(available_at_utc, inserted_at)) AS latest_source_accession_number,
                max(available_at_utc) AS profile_available_at_utc,
                argMaxIf(source_kind, tuple(available_at_utc, inserted_at), ifNull(business_address_line1, '') != '' OR ifNull(business_address_city, '') != '' OR ifNull(issuer_business_country_code, '') != '') AS business_address_source_kind,
                argMaxIf(source_accession_number, tuple(available_at_utc, inserted_at), ifNull(business_address_line1, '') != '' OR ifNull(business_address_city, '') != '' OR ifNull(issuer_business_country_code, '') != '') AS business_address_accession_number,
                maxIf(available_at_utc, ifNull(business_address_line1, '') != '' OR ifNull(business_address_city, '') != '' OR ifNull(issuer_business_country_code, '') != '') AS business_address_available_at_utc,
                argMaxIf(source_kind, tuple(available_at_utc, inserted_at), ifNull(issuer_business_country_code, '') != '') AS business_country_source_kind,
                argMaxIf(source_accession_number, tuple(available_at_utc, inserted_at), ifNull(issuer_business_country_code, '') != '') AS business_country_accession_number,
                maxIf(available_at_utc, ifNull(issuer_business_country_code, '') != '') AS business_country_available_at_utc,
                argMaxIf(source_kind, tuple(available_at_utc, inserted_at), ifNull(issuer_legal_country_code, '') != '') AS legal_country_source_kind,
                argMaxIf(source_accession_number, tuple(available_at_utc, inserted_at), ifNull(issuer_legal_country_code, '') != '') AS legal_country_accession_number,
                maxIf(available_at_utc, ifNull(issuer_legal_country_code, '') != '') AS legal_country_available_at_utc
            FROM {db}.market_issuer_company_profile_v1 FINAL
            WHERE available_at_utc <= parseDateTime64BestEffort({instant})
            GROUP BY issuer_id
        ) AS profile ON profile.issuer_id = u.issuer_id
        LEFT JOIN
        (
            SELECT
                symbol_id,
                argMax(listing_country_code, tuple(available_at_utc, inserted_at)) AS latest_listing_country_code,
                argMax(effective_country_code, tuple(available_at_utc, inserted_at)) AS latest_effective_country_code
            FROM {db}.market_security_country_v1 FINAL
            WHERE available_at_utc <= parseDateTime64BestEffort({instant})
              AND startsWith(source_evidence_ref, 'id_listing_v1/ref_exchange_v1:')
            GROUP BY symbol_id
        ) AS country ON country.symbol_id = u.symbol_id
        WHERE u.universe_date = latest_date AND upper(u.ticker) = {symbol}
        ORDER BY u.is_tradable DESC, u.currency_code = 'USD' DESC, u.product_type = 'STK' DESC, u.exchange_code ASC
        LIMIT 1
        FORMAT JSONEachRow
    """


def clickhouse_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def reference_fact_queries(
    *,
    ticker: str,
    context: dict[str, Any],
    cutoff: datetime,
    database: str,
    historical_database: str,
) -> dict[str, str]:
    """Build the independent reference queries for one resolved identity."""
    return {
        "borrow": borrow(ticker, cutoff, database),
        "classifications": classifications(context["security_id"], cutoff, database),
        "corporate": corporate_events(context["symbol_id"], cutoff, database),
        "fails_to_deliver": fails_to_deliver(ticker, cutoff, database),
        "float": float_history(context["symbol_id"], cutoff, database),
        "identifiers": identifiers(
            context["issuer_id"], context["security_id"], cutoff, database
        ),
        "market": market_snapshot(context["symbol_id"], cutoff, database),
        "reg_sho": reg_sho(ticker, cutoff, database),
        "short_interest": short_interest(context["symbol_id"], cutoff, database),
        "short_volume": short_volume(context["symbol_id"], cutoff, database),
        "splits": splits(context["symbol_id"], cutoff, database),
        "volume": daily_volume(ticker, cutoff, historical_database),
    }


def market_snapshot(symbol_id: str, cutoff: datetime, database: str) -> str:
    return latest_rows(
        database,
        "market_security_market_snapshot_v1",
        "symbol_id",
        symbol_id,
        "observed_at_utc",
        cutoff,
        limit=20,
    )


def float_history(symbol_id: str, cutoff: datetime, database: str) -> str:
    # New provider rows can contain shares outstanding while omitting float.
    return latest_rows(
        database,
        "market_security_float_v1",
        "symbol_id",
        symbol_id,
        "effective_date",
        cutoff,
        date_column=True,
        limit=40,
    )


def borrow(ticker: str, cutoff: datetime, database: str) -> str:
    return latest_rows(
        database,
        "market_security_borrow_v1",
        "provider_ticker",
        ticker,
        "observed_at_utc",
        cutoff,
        limit=20,
    )


def short_interest(
    symbol_id: str,
    cutoff: datetime,
    database: str,
    *,
    limit: int = 30,
) -> str:
    db = quote_ident(database)
    return f"""
        SELECT settlement_date, publication_date, published_at_utc, short_interest, avg_daily_volume,
               days_to_cover, source_system, source_venue, inserted_at
        FROM
        (
            SELECT settlement_date, publication_date, published_at_utc, short_interest, avg_daily_volume,
                   days_to_cover, source_system, source_venue, inserted_at
            FROM {db}.market_short_interest_v1 FINAL
            WHERE symbol_id = {sql_string(symbol_id)}
              AND settlement_date <= toDate({sql_string(cutoff.date().isoformat())})
              AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
            ORDER BY settlement_date DESC, inserted_at DESC
            LIMIT 1 BY settlement_date
        )
        ORDER BY settlement_date DESC
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def short_volume(symbol_id: str, cutoff: datetime, database: str) -> str:
    return short_volume_history(symbol_id, cutoff, database, limit=21)


def short_volume_history(
    symbol_id: str,
    cutoff: datetime,
    database: str,
    *,
    limit: int = HISTORY_LIMIT,
) -> str:
    db = quote_ident(database)
    return f"""
        SELECT trade_date, short_volume, total_volume, exempt_volume, short_volume_ratio,
               source_system, source_venue, inserted_at
        FROM
        (
            SELECT * FROM {db}.market_short_volume_v1 FINAL
            WHERE symbol_id = {sql_string(symbol_id)}
              AND trade_date <= toDate({sql_string(cutoff.date().isoformat())})
              AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
            ORDER BY trade_date DESC, inserted_at DESC
            LIMIT 1 BY trade_date
        )
        ORDER BY trade_date DESC, inserted_at DESC
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def daily_volume(ticker: str, cutoff: datetime, database: str) -> str:
    return daily_volume_history(ticker, cutoff, database, limit=MAIN_HISTORY_DAYS)


def daily_volume_history(
    ticker: str,
    cutoff: datetime,
    database: str,
    *,
    limit: int = HISTORY_LIMIT,
) -> str:
    daily_bars = daily_session_trade_bars_relation_sql(
        database=database,
        start_date=date(1970, 1, 1),
        end_date=cutoff.date() + timedelta(days=1),
        as_of=cutoff,
        ticker=ticker,
    )
    return f"""
        SELECT session_date, bar_end, close, size_sum
        FROM ({daily_bars})
        ORDER BY bar_end DESC
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def identifiers(
    issuer_id: str,
    security_id: str,
    cutoff: datetime,
    database: str,
) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT entity, identifier_kind, identifier_value, source_system, is_primary, last_seen_at_utc
        FROM
        (
            SELECT 'issuer' AS entity, identifier_kind, identifier_value, source_system, is_primary, last_seen_at_utc
            FROM {db}.id_issuer_identifier_v1 FINAL WHERE issuer_id = {sql_string(issuer_id)}
            UNION ALL
            SELECT 'security' AS entity, identifier_kind, identifier_value, source_system, is_primary, last_seen_at_utc
            FROM {db}.id_security_identifier_v1 FINAL WHERE security_id = {sql_string(security_id)}
        )
        WHERE last_seen_at_utc <= parseDateTime64BestEffort({instant})
        ORDER BY is_primary DESC, entity ASC, identifier_kind ASC
        FORMAT JSONEachRow
    """


def classifications(security_id: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    return f"""
        SELECT classification_source, classification_scheme, classification_level, classification_value, last_seen_at_utc
        FROM {db}.market_security_classification_v1 FINAL
        WHERE security_id = {sql_string(security_id)}
          AND last_seen_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY classification_source ASC, classification_scheme ASC, classification_level ASC
        LIMIT 30
        FORMAT JSONEachRow
    """


def corporate_events(symbol_id: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    day = sql_string(cutoff.date().isoformat())
    return f"""
        SELECT
            (SELECT max(execution_date) FROM {db}.market_stock_split_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND execution_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_split_date,
            (SELECT argMax(split_from, tuple(execution_date, inserted_at)) FROM {db}.market_stock_split_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND execution_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_split_from,
            (SELECT argMax(split_to, tuple(execution_date, inserted_at)) FROM {db}.market_stock_split_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND execution_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_split_to,
            (SELECT max(ex_dividend_date) FROM {db}.market_cash_dividend_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND ex_dividend_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_ex_dividend_date,
            (SELECT argMax(cash_amount, tuple(ex_dividend_date, inserted_at)) FROM {db}.market_cash_dividend_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND ex_dividend_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_dividend_amount,
            (SELECT argMax(currency_code, tuple(ex_dividend_date, inserted_at)) FROM {db}.market_cash_dividend_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND ex_dividend_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS dividend_currency
        FORMAT JSONEachRow
    """


def fails_to_deliver(
    ticker: str,
    cutoff: datetime,
    database: str,
    *,
    limit: int = 30,
) -> str:
    return latest_rows(
        database,
        "market_fails_to_deliver_v1",
        "provider_ticker",
        ticker,
        "settlement_date",
        cutoff,
        date_column=True,
        limit=limit,
    )


def reg_sho(
    ticker: str,
    cutoff: datetime,
    database: str,
    *,
    limit: int = 30,
) -> str:
    return latest_rows(
        database,
        "market_reg_sho_threshold_v1",
        "provider_ticker",
        ticker,
        "threshold_date",
        cutoff,
        date_column=True,
        limit=limit,
    )


def splits(
    symbol_id: str,
    cutoff: datetime,
    database: str,
    *,
    limit: int = 100,
) -> str:
    return latest_rows(
        database,
        "market_stock_split_v1",
        "symbol_id",
        symbol_id,
        "execution_date",
        cutoff,
        date_column=True,
        limit=limit,
    )


def latest(
    database: str,
    table: str,
    key: str,
    value: str,
    order: str,
    cutoff: datetime,
    *,
    date_column: bool = False,
) -> str:
    return latest_rows(
        database,
        table,
        key,
        value,
        order,
        cutoff,
        date_column=date_column,
        limit=1,
    )


def latest_rows(
    database: str,
    table: str,
    key: str,
    value: str,
    order: str,
    cutoff: datetime,
    *,
    date_column: bool = False,
    limit: int = 2,
) -> str:
    db = quote_ident(database)
    relation = f"{db}.{quote_ident(table)}"
    cutoff_clause = (
        f"toDate({sql_string(cutoff.date().isoformat())})"
        if date_column
        else f"parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})"
    )
    return f"""
        SELECT * FROM
        (
            SELECT * FROM {relation} FINAL
            WHERE {quote_ident(key)} = {sql_string(value)} AND {quote_ident(order)} <= {cutoff_clause}
              AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
            ORDER BY {quote_ident(order)} DESC, inserted_at DESC
            LIMIT 1 BY {quote_ident(order)}
        )
        ORDER BY {quote_ident(order)} DESC, inserted_at DESC
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)


TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
DATABASE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FUNDAMENTAL_TAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")),
    ("Net income", ("NetIncomeLoss", "ProfitLoss")),
    ("Diluted EPS", ("EarningsPerShareDiluted",)),
    ("Operating income", ("OperatingIncomeLoss",)),
    ("Cash", ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")),
    ("Assets", ("Assets",)),
    ("Liabilities", ("Liabilities", "LiabilitiesCurrent")),
    ("Stockholders' equity", ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")),
)
LOGGER = logging.getLogger(__name__)


def ticker_facts_payload(symbol: str, *, as_of: str | None = None, database: str = "q_live") -> dict[str, Any]:
    ticker = normalize_ticker(symbol)
    cutoff = parse_as_of(as_of)
    if not DATABASE_PATTERN.fullmatch(database):
        raise ValueError("Ticker facts database is not a valid identifier.")
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    try:
        anchor_rows = clickhouse_rows(client, identity_anchor_sql(ticker, cutoff, database))
    except Exception as error:
        raise RuntimeError("Canonical stock-reference storage is unavailable.") from error
    if not anchor_rows:
        return {
            "as_of": cutoff.isoformat(),
            "errors": {},
            "facts": {},
            "fundamentals": [],
            "identifiers": [],
            "sources": [],
            "status": "not_found",
            "symbol": ticker,
            "warnings": ["No canonical US stock listing was available for this ticker at the selected clock."],
        }
    anchor = anchor_rows[0]
    context = {
        "cik": issuer_cik(str(anchor.get("issuer_id") or "")),
        "issuer_id": str(anchor.get("issuer_id") or ""),
        "listing_id": str(anchor.get("listing_id") or ""),
        "security_id": str(anchor.get("security_id") or ""),
        "symbol_id": str(anchor.get("symbol_id") or ""),
    }
    queries: dict[str, str] = {
        "borrow": borrow_sql(ticker, cutoff, database),
        "classifications": classifications_sql(context["security_id"], cutoff, database),
        "corporate": corporate_events_sql(context["symbol_id"], cutoff, database),
        "float": float_sql(context["symbol_id"], cutoff, database),
        "identifiers": identifiers_sql(context["issuer_id"], context["security_id"], cutoff, database),
        "market": market_snapshot_sql(context["symbol_id"], cutoff, database),
        "short_interest": short_interest_sql(context["symbol_id"], cutoff, database),
        "short_volume": short_volume_sql(context["symbol_id"], cutoff, database),
        "volume": volume_sql(ticker, cutoff, historical_database()),
    }
    if context["cik"]:
        queries["fundamentals"] = fundamentals_sql(context["cik"], cutoff, database)
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
        futures = {pool.submit(clickhouse_rows, client, query): name for name, query in queries.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as error:  # The response remains useful when one independent source table is unavailable.
                results[name] = []
                errors[name] = f"{name.replace('_', ' ').title()} source is unavailable."
                LOGGER.warning("Ticker facts source %s failed: %s", name, error)

    market = first(results.get("market"))
    float_row = first(results.get("float"))
    short_rows = results.get("short_interest", [])
    short_interest = short_rows[0] if short_rows else {}
    previous_short_interest = short_rows[1] if len(short_rows) > 1 else {}
    short_volume = first(results.get("short_volume"))
    borrow = first(results.get("borrow"))
    volume = first(results.get("volume"))
    shares_outstanding = first_number(float_row, "shares_outstanding") or first_number(market, "share_class_shares_outstanding", "weighted_shares_outstanding")
    free_float = first_number(float_row, "free_float")
    short_shares = first_number(short_interest, "short_interest")
    fundamentals = select_fundamentals(results.get("fundamentals", []))
    warnings: list[str] = []
    if not free_float:
        warnings.append("Free float is unavailable for this security; short interest as a percent of float is not inferred.")
    if borrow and not any(borrow.get(field) is not None for field in ("shortable_shares", "indicative_borrow_rate", "fee_rate")):
        warnings.append("IBKR returned a borrow snapshot but did not publish availability or rate fields.")
    if not fundamentals:
        warnings.append("No selected SEC-reported fundamental facts were available by the selected clock.")
    return {
        "as_of": cutoff.isoformat(),
        "errors": errors,
        "facts": {
            "borrow": borrow,
            "classifications": results.get("classifications", []),
            "corporate": first(results.get("corporate")),
            "float": float_row,
            "identity": {**anchor, "cik": context["cik"] or None},
            "market": market,
            "short_interest": {
                **short_interest,
                "change_from_previous": difference(short_interest, previous_short_interest, "short_interest"),
                "percent_of_float": ratio_percent(short_shares, free_float),
                "percent_of_outstanding": ratio_percent(short_shares, shares_outstanding),
                "previous_settlement_date": previous_short_interest.get("settlement_date"),
            },
            "short_volume": short_volume,
            "volume": volume,
        },
        "fundamentals": fundamentals,
        "identifiers": results.get("identifiers", []),
        "sources": source_inventory(results),
        "status": "partial" if errors else "ready",
        "symbol": ticker,
        "warnings": warnings,
    }


def normalize_ticker(value: str) -> str:
    ticker = str(value or "").strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("Ticker must contain 1-16 letters, numbers, dots, or hyphens.")
    return ticker


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("as_of must be an ISO-8601 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError("as_of must include an explicit timezone.")
    return parsed.astimezone(UTC)


def clickhouse_rows(client: ClickHouseHttpClient, query: str) -> list[dict[str, Any]]:
    payload = client.execute(query)
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def identity_anchor_sql(ticker: str, cutoff: datetime, database: str) -> str:
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


def market_snapshot_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
    return latest_sql(database, "market_security_market_snapshot_v1", "symbol_id", symbol_id, "observed_at_utc", cutoff)


def float_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
    return latest_sql(database, "market_security_float_v1", "symbol_id", symbol_id, "effective_date", cutoff, date_column=True)


def borrow_sql(ticker: str, cutoff: datetime, database: str) -> str:
    return latest_sql(database, "market_security_borrow_v1", "provider_ticker", ticker, "observed_at_utc", cutoff)


def short_interest_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
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
        LIMIT 2
        FORMAT JSONEachRow
    """


def short_volume_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    return f"""
        SELECT
            max(trade_date) AS latest_trade_date,
            argMax(short_volume, tuple(trade_date, inserted_at)) AS latest_short_volume,
            argMax(total_volume, tuple(trade_date, inserted_at)) AS latest_total_volume,
            argMax(exempt_volume, tuple(trade_date, inserted_at)) AS latest_exempt_volume,
            argMax(short_volume_ratio, tuple(trade_date, inserted_at)) AS latest_short_volume_ratio,
            sum(short_volume) / nullIf(sum(total_volume), 0) AS ratio_20d,
            sum(short_volume) AS short_volume_20d,
            sum(total_volume) AS total_volume_20d,
            countDistinct(trade_date) AS sessions,
            argMax(source_system, tuple(trade_date, inserted_at)) AS source_system,
            argMax(source_venue, tuple(trade_date, inserted_at)) AS source_venue
        FROM
        (
            SELECT *
            FROM
            (
                SELECT * FROM {db}.market_short_volume_v1 FINAL
                WHERE symbol_id = {sql_string(symbol_id)}
                  AND trade_date <= toDate({sql_string(cutoff.date().isoformat())})
                  AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
                ORDER BY trade_date DESC, inserted_at DESC
                LIMIT 1 BY trade_date
            )
            ORDER BY trade_date DESC
            LIMIT 20
        )
        FORMAT JSONEachRow
    """


def volume_sql(ticker: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    return f"""
        SELECT
            argMax(session_date, bar_end) AS session_date,
            argMax(close, bar_end) AS latest_close,
            argMax(size_sum, bar_end) AS latest_volume,
            avg(size_sum) AS average_volume_20d,
            argMax(size_sum, bar_end) / nullIf(avg(size_sum), 0) AS relative_volume_20d,
            argMax(size_sum * close, bar_end) AS latest_dollar_volume,
            count() AS sessions
        FROM
        (
            SELECT session_date, bar_end, close, size_sum
            FROM {db}.macro_bars_by_time_symbol FINAL
            WHERE sym = {sql_string(ticker)} AND timeframe = '1d' AND bar_family = 'trade'
              AND bar_end <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
            ORDER BY bar_end DESC
            LIMIT 20
        )
        FORMAT JSONEachRow
    """


def historical_database() -> str:
    value = os.environ.get("QMD_HISTORY_DATABASE") or os.environ.get("QMD_HISTORICAL_CLICKHOUSE_DATABASE") or "market_sip_compact"
    if not DATABASE_PATTERN.fullmatch(value):
        raise ValueError("QMD history database is not a valid identifier.")
    return value


def fundamentals_sql(cik: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    tags = sorted({tag for _, alternatives in FUNDAMENTAL_TAGS for tag in alternatives})
    tag_clause = ", ".join(sql_string(tag) for tag in tags)
    return f"""
        SELECT tag, taxonomy, unit_code, value, fiscal_year, fiscal_period, period_end_date,
               filed_at_utc, form_type, accession_number, recorded_at_utc
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE cik = {sql_string(cik)} AND tag IN ({tag_clause})
          AND filed_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
          AND recorded_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY tag ASC, period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
        LIMIT 1 BY tag
        FORMAT JSONEachRow
    """


def identifiers_sql(issuer_id: str, security_id: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT entity, identifier_kind, identifier_value, source_system, is_primary
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


def classifications_sql(security_id: str, cutoff: datetime, database: str) -> str:
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


def corporate_events_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
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


def latest_sql(database: str, table: str, key: str, value: str, order: str, cutoff: datetime, *, date_column: bool = False) -> str:
    db = quote_ident(database)
    relation = f"{db}.{quote_ident(table)}"
    cutoff_clause = f"toDate({sql_string(cutoff.date().isoformat())})" if date_column else f"parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})"
    return f"""
        SELECT * FROM {relation} FINAL
        WHERE {quote_ident(key)} = {sql_string(value)} AND {quote_ident(order)} <= {cutoff_clause}
          AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY {quote_ident(order)} DESC, inserted_at DESC LIMIT 1
        FORMAT JSONEachRow
    """


def select_fundamentals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_tag = {str(row.get("tag") or ""): row for row in rows}
    selected: list[dict[str, Any]] = []
    for label, alternatives in FUNDAMENTAL_TAGS:
        row = next((by_tag[tag] for tag in alternatives if tag in by_tag), None)
        if row:
            selected.append({"label": label, **row})
    return selected


def source_inventory(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    authorities = {
        "borrow": ("IBKR borrow", "q_live.market_security_borrow_v1"),
        "classifications": ("Reference classification", "q_live.market_security_classification_v1"),
        "corporate": ("Corporate actions", "q_live.market_stock_split_v1 / market_cash_dividend_v1"),
        "float": ("Massive float", "q_live.market_security_float_v1"),
        "fundamentals": ("SEC XBRL", "q_live.sec_xbrl_company_fact_v3"),
        "identifiers": ("Canonical identifiers", "q_live.id_*_identifier_v1"),
        "market": ("Massive market snapshot", "q_live.market_security_market_snapshot_v1"),
        "short_interest": ("Massive short interest", "q_live.market_short_interest_v1"),
        "short_volume": ("FINRA short volume", "q_live.market_short_volume_v1"),
        "volume": ("QMD daily bars", f"{historical_database()}.macro_bars_by_time_symbol"),
    }
    return [
        {"available": bool(results.get(key)), "label": label, "table": table}
        for key, (label, table) in authorities.items()
    ]


def issuer_cik(issuer_id: str) -> str:
    match = re.fullmatch(r"issuer:cik:(\d{1,10})", issuer_id)
    return match.group(1).zfill(10) if match else ""


def clickhouse_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def first(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    return rows[0] if rows else {}


def first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def ratio_percent(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator * 100.0 if numerator is not None and denominator else None


def difference(current: dict[str, Any], previous: dict[str, Any], key: str) -> float | None:
    try:
        return float(current[key]) - float(previous[key])
    except (KeyError, TypeError, ValueError):
        return None

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from src.backend.real_live_market_data.startup import logo_asset_url


MAX_PRESENTATION_TICKERS = 200
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")


def ticker_presentation_payload(tickers: Iterable[str], *, database: str = "q_live") -> dict[str, Any]:
    normalized = normalize_tickers(tickers)
    if not normalized:
        return {"presentations": {}, "source": f"{database}.market_presentation_asset_v1", "status": "ready"}
    try:
        rows = _clickhouse_rows(ticker_presentation_sql(normalized, database=database))
    except RuntimeError:
        # Branding is optional presentation data. Database pressure must not make
        # the ticker identity or its containing Canvas surface unavailable.
        return {"presentations": {}, "source": f"{database}.market_presentation_asset_v1", "status": "unavailable"}
    presentations: dict[str, dict[str, str]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker not in normalized:
            continue
        relative_path = str(row.get("logo_relative_path") or "").strip()
        presentations[ticker] = {
            "issuer_name": str(row.get("issuer_name") or "").strip(),
            "logo_url": logo_asset_url(relative_path),
            "ticker": ticker,
        }
    return {"presentations": presentations, "source": f"{database}.market_presentation_asset_v1", "status": "ready"}


def normalize_tickers(tickers: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for value in tickers:
        ticker = str(value or "").strip().upper()
        if not TICKER_PATTERN.fullmatch(ticker) or ticker in normalized:
            continue
        normalized.append(ticker)
        if len(normalized) >= MAX_PRESENTATION_TICKERS:
            break
    return normalized


def ticker_presentation_sql(tickers: list[str], *, database: str = "q_live") -> str:
    database_name = quote_ident(database)
    ticker_clause = ", ".join(sql_string(ticker) for ticker in tickers)
    asset_match_by_ticker = {
        ticker: (
            f"lowerUTF8(display_name) = {sql_string(f'{ticker.lower()} logo')}"
            f" OR positionCaseInsensitiveUTF8(relative_path, {sql_string(f'/logo/{ticker.lower()}-')}) > 0"
            f" OR positionCaseInsensitiveUTF8(relative_path, {sql_string(f'ticker-overview-{ticker.lower()}-logo-')}) > 0"
        )
        for ticker in tickers
    }
    asset_ticker_expression = "multiIf(" + ", ".join(
        f"({condition}), {sql_string(ticker)}" for ticker, condition in asset_match_by_ticker.items()
    ) + ", '')"
    asset_filter = " OR ".join(f"({condition})" for condition in asset_match_by_ticker.values())
    return f"""
        WITH
            (SELECT max(universe_date) FROM {database_name}.feature_tradable_universe_v1) AS latest_universe_date,
            (SELECT max(feature_date) FROM {database_name}.feature_scanner_static_v1) AS latest_scanner_date,
            fallback_assets AS
            (
                SELECT
                    {asset_ticker_expression} AS ticker,
                    relative_path
                FROM {database_name}.market_presentation_asset_v1 FINAL
                WHERE asset_kind = 'logo'
                  AND status = 'active'
                  AND ({asset_filter})
                ORDER BY ticker ASC, last_seen_at_utc DESC, inserted_at DESC
                LIMIT 1 BY ticker
            )
        SELECT
            base.ticker AS ticker,
            base.issuer_name AS issuer_name,
            if(notEmpty(base.linked_logo_relative_path), base.linked_logo_relative_path, ifNull(fallback.relative_path, '')) AS logo_relative_path
        FROM
        (
            SELECT
                upper(u.ticker) AS ticker,
                coalesce(nullIf(issuer.branding_name, ''), issuer.issuer_name, '') AS issuer_name,
                ifNull(asset.relative_path, '') AS linked_logo_relative_path
            FROM {database_name}.feature_tradable_universe_v1 AS u FINAL
            LEFT JOIN {database_name}.id_issuer_v1 AS issuer FINAL
                ON issuer.issuer_id = u.issuer_id
            LEFT JOIN {database_name}.feature_scanner_static_v1 AS scanner FINAL
                ON scanner.feature_date = latest_scanner_date
               AND scanner.symbol_id = u.symbol_id
               AND scanner.listing_id = u.listing_id
            LEFT JOIN
            (
                SELECT *
                FROM {database_name}.market_presentation_asset_v1 FINAL
                WHERE asset_kind = 'logo' AND status = 'active'
            ) AS asset
                ON asset.asset_id = coalesce(scanner.logo_asset_id, issuer.logo_asset_id)
            WHERE u.universe_date = latest_universe_date
              AND u.ticker IN ({ticker_clause})
            ORDER BY ticker ASC, notEmpty(linked_logo_relative_path) DESC, u.is_tradable DESC
            LIMIT 1 BY ticker
        ) AS base
        LEFT JOIN fallback_assets AS fallback ON fallback.ticker = base.ticker
        ORDER BY base.ticker ASC
        FORMAT JSONEachRow
    """


def _clickhouse_rows(query: str) -> list[dict[str, Any]]:
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    payload = client.execute(query)
    return [json.loads(line) for line in payload.splitlines() if line.strip()]

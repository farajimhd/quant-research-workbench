from __future__ import annotations

from research.mlops.clickhouse import quote_ident, sql_string


def ticker_presentation(
    tickers: list[str],
    *,
    database: str = "q_live",
) -> str:
    """Version-1 bounded ticker branding and issuer-name query plan.

    The caller owns ticker syntax validation and the 200-symbol request bound.
    This plan owns the latest published reference snapshots, linked-logo
    precedence, deterministic fallback matching, and one-row-per-ticker rule.
    """
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
        f"({condition}), {sql_string(ticker)}"
        for ticker, condition in asset_match_by_ticker.items()
    ) + ", '')"
    asset_filter = " OR ".join(
        f"({condition})" for condition in asset_match_by_ticker.values()
    )
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

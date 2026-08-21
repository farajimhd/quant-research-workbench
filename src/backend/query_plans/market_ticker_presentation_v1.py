from __future__ import annotations

from research.mlops.clickhouse import quote_ident, sql_string


def ticker_presentation(
    tickers: list[str],
    *,
    database: str = "q_live",
) -> str:
    """Version-1 bounded ticker branding and issuer-name query plan.

    The caller owns ticker syntax validation and the 200-symbol request bound.
    This plan owns the latest published reference snapshots, resolved issuer
    presentation precedence, migration fallbacks, and one-row-per-ticker rule.
    """
    database_name = quote_ident(database)
    ticker_clause = ", ".join(sql_string(ticker) for ticker in tickers)
    asset_match_by_ticker = {
        ticker: (
            f"lowerUTF8(display_name) IN ({sql_string(f'{ticker.lower()} icon')}, {sql_string(f'{ticker.lower()} logo')})"
            f" OR positionCaseInsensitiveUTF8(relative_path, {sql_string(f'/logo/{ticker.lower()}-')}) > 0"
            f" OR positionCaseInsensitiveUTF8(relative_path, {sql_string(f'/icon/{ticker.lower()}-')}) > 0"
            f" OR positionCaseInsensitiveUTF8(relative_path, {sql_string(f'ticker-overview-{ticker.lower()}-logo-')}) > 0"
            f" OR positionCaseInsensitiveUTF8(relative_path, {sql_string(f'ticker-overview-{ticker.lower()}-icon-')}) > 0"
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
                    relative_path,
                    source_system,
                    asset_kind
                FROM {database_name}.market_presentation_asset_v1 FINAL
                WHERE asset_kind IN ('icon', 'logo')
                  AND status = 'active'
                  AND ({asset_filter})
                ORDER BY ticker ASC, if(asset_kind = 'icon', 0, 1), last_seen_at_utc DESC, inserted_at DESC
                LIMIT 1 BY ticker
            )
        SELECT
            base.ticker AS ticker,
            base.issuer_name AS issuer_name,
            base.country AS country,
            if(notEmpty(base.resolved_relative_path), base.resolved_relative_path,
               if(notEmpty(base.linked_logo_relative_path), base.linked_logo_relative_path, ifNull(fallback.relative_path, ''))) AS logo_relative_path,
            if(notEmpty(base.resolved_relative_path), base.resolved_source_system,
               if(notEmpty(base.linked_logo_relative_path), base.linked_source_system, ifNull(fallback.source_system, ''))) AS logo_source,
            if(notEmpty(base.resolved_relative_path), base.resolved_source_kind,
               if(notEmpty(base.linked_logo_relative_path), base.linked_asset_kind, ifNull(fallback.asset_kind, ''))) AS logo_kind,
            base.selection_revision AS logo_selection_revision,
            base.selection_quality_class AS logo_quality_class
        FROM
        (
            SELECT
                upper(u.ticker) AS ticker,
                coalesce(nullIf(issuer.branding_name, ''), nullIf(profile.issuer_name, ''), issuer.issuer_name, '') AS issuer_name,
                coalesce(nullIf(profile.issuer_business_country_code, ''), nullIf(profile.issuer_legal_country_code, ''), nullIf(country.effective_country_code, ''), nullIf(issuer.domicile_country_code, ''), nullIf(country.listing_country_code, ''), '') AS country,
                ifNull(resolved_asset.relative_path, '') AS resolved_relative_path,
                ifNull(selection.source_system, '') AS resolved_source_system,
                ifNull(selection.source_kind, '') AS resolved_source_kind,
                ifNull(selection.selected_selection_id, '') AS selection_revision,
                ifNull(selection.quality_class, '') AS selection_quality_class,
                ifNull(linked_asset.relative_path, '') AS linked_logo_relative_path,
                ifNull(linked_asset.source_system, '') AS linked_source_system,
                ifNull(linked_asset.asset_kind, '') AS linked_asset_kind
            FROM {database_name}.feature_tradable_universe_v1 AS u FINAL
            LEFT JOIN {database_name}.id_issuer_v1 AS issuer FINAL
                ON issuer.issuer_id = u.issuer_id
            LEFT JOIN
            (
                SELECT
                    issuer_id,
                    argMaxIf(issuer_name, tuple(available_at_utc, inserted_at), ifNull(issuer_name, '') != '') AS issuer_name,
                    argMaxIf(issuer_legal_country_code, tuple(available_at_utc, inserted_at), ifNull(issuer_legal_country_code, '') != '') AS issuer_legal_country_code,
                    argMaxIf(issuer_business_country_code, tuple(available_at_utc, inserted_at), ifNull(issuer_business_country_code, '') != '') AS issuer_business_country_code
                FROM {database_name}.market_issuer_company_profile_v1 FINAL
                GROUP BY issuer_id
            ) AS profile ON profile.issuer_id = u.issuer_id
            LEFT JOIN
            (
                SELECT
                    symbol_id,
                    argMax(effective_country_code, tuple(available_at_utc, inserted_at)) AS effective_country_code,
                    argMax(listing_country_code, tuple(available_at_utc, inserted_at)) AS listing_country_code
                FROM {database_name}.market_security_country_v1 FINAL
                WHERE symbol_id IN
                (
                    SELECT symbol_id
                    FROM {database_name}.feature_tradable_universe_v1 FINAL
                    WHERE universe_date = latest_universe_date
                      AND ticker IN ({ticker_clause})
                )
                  AND startsWith(source_evidence_ref, 'id_listing_v1/ref_exchange_v1:')
                GROUP BY symbol_id
            ) AS country
                ON country.symbol_id = u.symbol_id
            LEFT JOIN {database_name}.feature_scanner_static_v1 AS scanner FINAL
                ON scanner.feature_date = latest_scanner_date
               AND scanner.symbol_id = u.symbol_id
               AND scanner.listing_id = u.listing_id
            LEFT JOIN
            (
                SELECT
                    issuer_id,
                    argMax(asset_id, tuple(selected_at_utc, inserted_at, selection_id)) AS asset_id,
                    argMax(source_system, tuple(selected_at_utc, inserted_at, selection_id)) AS source_system,
                    argMax(source_kind, tuple(selected_at_utc, inserted_at, selection_id)) AS source_kind,
                    argMax(quality_class, tuple(selected_at_utc, inserted_at, selection_id)) AS quality_class,
                    argMax(selection_id, tuple(selected_at_utc, inserted_at, selection_id)) AS selected_selection_id
                FROM {database_name}.market_issuer_presentation_selection_v1
                WHERE issuer_id IN
                (
                    SELECT issuer_id
                    FROM {database_name}.feature_tradable_universe_v1 FINAL
                    WHERE universe_date = latest_universe_date
                      AND ticker IN ({ticker_clause})
                )
                GROUP BY issuer_id
            ) AS selection ON selection.issuer_id = u.issuer_id
            LEFT JOIN
            (
                SELECT *
                FROM {database_name}.market_presentation_asset_v1 FINAL
                WHERE status = 'active'
            ) AS resolved_asset ON resolved_asset.asset_id = selection.asset_id
            LEFT JOIN
            (
                SELECT *
                FROM {database_name}.market_presentation_asset_v1 FINAL
                WHERE asset_kind = 'logo' AND status = 'active'
            ) AS linked_asset
                ON linked_asset.asset_id = coalesce(scanner.logo_asset_id, issuer.logo_asset_id)
            WHERE u.universe_date = latest_universe_date
              AND u.ticker IN ({ticker_clause})
            ORDER BY ticker ASC, notEmpty(resolved_relative_path) DESC, notEmpty(linked_logo_relative_path) DESC, u.is_tradable DESC
            LIMIT 1 BY ticker
        ) AS base
        LEFT JOIN fallback_assets AS fallback ON fallback.ticker = base.ticker
        ORDER BY base.ticker ASC
        FORMAT JSONEachRow
    """

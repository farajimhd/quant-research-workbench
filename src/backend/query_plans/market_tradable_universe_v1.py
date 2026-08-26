from __future__ import annotations

from collections.abc import Iterable

from research.mlops.clickhouse import quote_ident, sql_string


def full_tradable_universe(*, database: str) -> str:
    """Version-1 latest tradable universe used by the Live market-data lane."""
    db = quote_ident(database)
    return f"""
    WITH
        latest_universe AS
        (
            SELECT max(universe_date) AS universe_date
            FROM {db}.feature_tradable_universe_v1 FINAL
        ),
        latest_scanner AS
        (
            SELECT max(feature_date) AS feature_date
            FROM {db}.feature_scanner_static_v1 FINAL
        )
    SELECT
        u.ticker AS candidate_massive_ticker,
        u.symbol_id AS symbol_id,
        u.symbol_status AS symbol_status,
        1 AS primary_symbol_flag,
        '' AS ticker_type_id,
        'CS' AS ticker_type_provider_code,
        'Common Stock' AS ticker_type_name,
        '' AS ticker_type_description,
        u.product_type AS security_product_type,
        u.asset_class AS security_asset_class,
        u.product_type AS security_instrument_type,
        u.product_type AS security_type,
        u.listing_id AS listing_id,
        u.listing_status AS listing_status,
        u.ibkr_conid AS ibkr_conid,
        u.exchange_code AS exchange_code,
        u.currency_code AS currency_code,
        u.issuer_id AS issuer_id,
        issuer.issuer_name AS issuer_name,
        coalesce(resolved_asset.asset_id, asset.asset_id) AS logo_asset_id,
        coalesce(resolved_asset.relative_path, asset.relative_path) AS logo_relative_path,
        coalesce(resolved_asset.mime_type, asset.mime_type) AS logo_mime_type,
        coalesce(resolved_asset.source_reference, asset.source_reference) AS logo_source_reference,
        scanner.free_float AS massive_float,
        scanner.short_interest AS massive_short_interest,
        scanner.days_to_cover AS massive_days_to_cover,
        scanner.short_volume_ratio AS massive_short_volume_ratio,
        scanner.float_bucket AS float_profile,
        scanner.short_pressure_label AS short_setup,
        u.is_tradable AS is_tradable,
        u.exclusion_reason AS exclusion_reason
    FROM (SELECT * FROM {db}.feature_tradable_universe_v1 FINAL) AS u
    LEFT JOIN (SELECT * FROM {db}.id_issuer_v1 FINAL) AS issuer
        ON issuer.issuer_id = u.issuer_id
    LEFT JOIN (SELECT * FROM {db}.feature_scanner_static_v1 FINAL) AS scanner
        ON scanner.feature_date = (SELECT feature_date FROM latest_scanner)
       AND scanner.symbol_id = u.symbol_id
       AND scanner.listing_id = u.listing_id
    LEFT JOIN
    (
        SELECT issuer_id, argMax(asset_id, tuple(selected_at_utc, inserted_at, selection_id)) AS asset_id
        FROM {db}.market_issuer_presentation_selection_v1
        GROUP BY issuer_id
    ) AS presentation_selection ON presentation_selection.issuer_id = u.issuer_id
    LEFT JOIN (SELECT * FROM {db}.market_presentation_asset_v1 FINAL WHERE status = 'active') AS resolved_asset
        ON resolved_asset.asset_id = presentation_selection.asset_id
    LEFT JOIN (SELECT * FROM {db}.market_presentation_asset_v1 FINAL) AS asset
        ON asset.asset_id = coalesce(scanner.logo_asset_id, issuer.logo_asset_id)
    WHERE u.universe_date = (SELECT universe_date FROM latest_universe)
      AND u.is_tradable = 1
    ORDER BY upper(candidate_massive_ticker)
    """


def tradable_symbol_lookup(*, database: str, symbols: Iterable[str]) -> str:
    """Return current tradability evidence for a caller-bounded symbol set."""
    db = quote_ident(database)
    normalized = sorted(
        {str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()}
    )
    if not normalized:
        raise ValueError("tradable symbol lookup requires at least one symbol")
    symbol_list = ", ".join(sql_string(symbol) for symbol in normalized)
    return f"""
        WITH latest AS
        (
            SELECT max(universe_date) AS universe_date
            FROM {db}.feature_tradable_universe_v1 FINAL
        )
        SELECT
            toString(universe_date) AS universe_date_text,
            upper(ticker) AS ticker,
            symbol_id,
            listing_id,
            exchange_code,
            currency_code,
            toUInt8(is_tradable) AS is_tradable,
            ifNull(exclusion_reason, '') AS exclusion_reason,
            toUInt64OrZero(ifNull(ibkr_conid, '')) AS ibkr_conid
        FROM {db}.feature_tradable_universe_v1 FINAL
        WHERE universe_date = (SELECT universe_date FROM latest)
          AND upper(ticker) IN ({symbol_list})
        """

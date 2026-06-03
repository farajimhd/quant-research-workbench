from __future__ import annotations

from typing import Any

import polars as pl

from src.backend.real_live_market_data.clickhouse import ClickHouseHttpClient, quote_identifier
from src.backend.real_live_market_data.config import MarketGatewayConfig
from src.backend.real_live_market_data.models import UniverseRecord


REQUIRED_UNIVERSE_COLUMNS = {"ticker", "conid"}


def load_universe_frame(client: ClickHouseHttpClient, config: MarketGatewayConfig, *, timeout: int = 20) -> pl.DataFrame:
    sql = config.universe_sql or default_universe_sql(config)
    frame = client.query_frame(sql, timeout=timeout)
    if frame.is_empty():
        return frame
    source_columns = set(frame.columns)
    frame = normalize_universe_frame(frame)
    if config.min_price > 0 and source_columns.intersection({"last_price", "latest_price"}):
        frame = frame.filter((pl.col("last_price").fill_null(0) >= config.min_price) | (pl.col("last_price").is_null()))
    if config.min_avg_daily_volume > 0 and "avg_daily_volume" in source_columns:
        frame = frame.filter((pl.col("avg_daily_volume").fill_null(0) >= config.min_avg_daily_volume) | (pl.col("avg_daily_volume").is_null()))
    if config.max_universe_symbols > 0 and frame.height > config.max_universe_symbols:
        sort_column = "avg_daily_volume" if "avg_daily_volume" in frame.columns else "ticker"
        frame = frame.sort(sort_column, descending=sort_column != "ticker").head(config.max_universe_symbols)
    return frame


def default_universe_sql(config: MarketGatewayConfig) -> str:
    database = quote_identifier(config.read_clickhouse.database or "default")
    return f"""
    SELECT
        s.ticker AS candidate_massive_ticker,
        s.symbol_id AS symbol_id,
        s.status AS symbol_status,
        s.primary_symbol_flag AS primary_symbol_flag,
        s.ticker_type_id AS ticker_type_id,
        tt.provider_code AS ticker_type_provider_code,
        tt.name AS ticker_type_name,
        tt.description AS ticker_type_description,
        sec.product_type AS security_product_type,
        sec.asset_class AS security_asset_class,
        sec.instrument_type AS security_instrument_type,
        sec.security_type AS security_type,
        l.listing_id AS listing_id,
        l.listing_status AS listing_status,
        l.ibkr_conid AS ibkr_conid,
        l.exchange_code AS exchange_code,
        l.currency_code AS currency_code,
        issuer.issuer_id AS issuer_id,
        issuer.issuer_name AS issuer_name,
        logo.logo_asset_id AS logo_asset_id,
        logo.logo_relative_path AS logo_relative_path,
        logo.logo_mime_type AS logo_mime_type,
        logo.logo_source_reference AS logo_source_reference
    FROM (SELECT * FROM {database}.market_symbol_v1 FINAL) AS s
    INNER JOIN (SELECT * FROM {database}.market_listing_v1 FINAL) AS l
        ON l.listing_id = s.listing_id
    INNER JOIN (SELECT * FROM {database}.market_security_v1 FINAL) AS sec
        ON sec.security_id = l.security_id
    INNER JOIN (SELECT * FROM {database}.market_exchange_v1 FINAL) AS ex
        ON ex.exchange_code = l.exchange_code
    LEFT JOIN (SELECT * FROM {database}.market_issuer_v1 FINAL) AS issuer
        ON issuer.issuer_id = sec.issuer_id
    LEFT JOIN (SELECT * FROM {database}.market_ticker_type_v1 FINAL) AS tt
        ON tt.ticker_type_id = s.ticker_type_id
    LEFT JOIN (
        SELECT
            logo_ticker,
            argMax(asset_id, logo_seen_at) AS logo_asset_id,
            argMax(relative_path, logo_seen_at) AS logo_relative_path,
            argMax(mime_type, logo_seen_at) AS logo_mime_type,
            argMax(source_reference, logo_seen_at) AS logo_source_reference
        FROM (
            SELECT
                upper(extract(relative_path, 'ticker-overview-([^/]+)-logo-')) AS logo_ticker,
                asset_id,
                relative_path,
                mime_type,
                source_reference,
                coalesce(last_verified_at_utc, last_seen_at_utc, first_seen_at_utc, toDateTime64('1970-01-01 00:00:00', 3)) AS logo_seen_at
            FROM {database}.market_presentation_asset_v1 FINAL
            WHERE source_system = 'massive'
              AND asset_kind = 'logo'
        ) AS extracted_logo
        WHERE logo_ticker != ''
        GROUP BY logo_ticker
    ) AS logo
        ON logo.logo_ticker = upper(s.ticker)
    WHERE s.status = 'active'
      AND s.primary_symbol_flag = 1
      AND l.listing_status = 'active'
      AND match(ifNull(l.ibkr_conid, ''), '^[1-9][0-9]*$')
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
    ORDER BY upper(candidate_massive_ticker)
    """


def normalize_universe_frame(frame: pl.DataFrame) -> pl.DataFrame:
    rename_map = {
        "candidate_massive_ticker": "ticker",
        "currency_code": "currency",
        "symbol": "ticker",
        "exchange_code": "primary_exchange",
        "ibkr_conid": "conid",
        "ib_conid": "conid",
        "float_shares": "float",
        "latest_price": "last_price",
        "security_product_type": "sec_type",
    }
    frame = frame.rename({old: new for old, new in rename_map.items() if old in frame.columns and new not in frame.columns})
    missing = REQUIRED_UNIVERSE_COLUMNS - set(frame.columns)
    if missing:
        raise RuntimeError(f"ClickHouse universe query is missing required column(s): {', '.join(sorted(missing))}")
    defaults: dict[str, Any] = {
        "avg_daily_volume": 0.0,
        "currency": "USD",
        "float": 0.0,
        "last_price": 0.0,
        "primary_exchange": "",
        "sec_type": "STK",
        "short_interest": 0.0,
        "short_interest_date": "",
        "short_volume": 0.0,
        "short_volume_date": "",
    }
    for column, value in defaults.items():
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(value).alias(column))
    return frame.with_columns(
        pl.col("ticker").cast(pl.Utf8).str.to_uppercase(),
        pl.col("conid").cast(pl.Int64, strict=False).fill_null(0),
        pl.col("avg_daily_volume").cast(pl.Float64, strict=False).fill_null(0.0),
        pl.col("float").cast(pl.Float64, strict=False).fill_null(0.0),
        pl.col("last_price").cast(pl.Float64, strict=False).fill_null(0.0),
        pl.col("short_interest").cast(pl.Float64, strict=False).fill_null(0.0),
        pl.col("short_volume").cast(pl.Float64, strict=False).fill_null(0.0),
    ).filter((pl.col("ticker") != "") & (pl.col("conid") > 0))


def universe_records(frame: pl.DataFrame) -> dict[str, UniverseRecord]:
    records: dict[str, UniverseRecord] = {}
    for row in frame.to_dicts():
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        records[ticker] = UniverseRecord(
            ticker=ticker,
            conid=int(row.get("conid") or 0),
            avg_daily_volume=float(row.get("avg_daily_volume") or 0),
            currency=str(row.get("currency") or "USD"),
            float_shares=float(row.get("float") or 0),
            last_price=float(row.get("last_price") or 0),
            primary_exchange=str(row.get("primary_exchange") or ""),
            sec_type=str(row.get("sec_type") or "STK"),
            short_interest=float(row.get("short_interest") or 0),
            short_interest_date=str(row.get("short_interest_date") or ""),
            short_volume=float(row.get("short_volume") or 0),
            short_volume_date=str(row.get("short_volume_date") or ""),
        )
    return records

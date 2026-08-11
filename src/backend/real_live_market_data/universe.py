from __future__ import annotations

import os
from typing import Any

import polars as pl

from src.backend.query_plans.market_tradable_universe_v1 import full_tradable_universe
from src.backend.real_live_market_data.clickhouse import ClickHouseHttpClient
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
    feature_database = os.environ.get("REAL_LIVE_TRADABLE_UNIVERSE_DATABASE", "").strip() or config.write_clickhouse.database or config.read_clickhouse.database or "q_live"
    return full_tradable_universe(database=feature_database)


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
        "massive_float": "float",
        "massive_short_interest": "short_interest",
        "massive_short_interest_date": "short_interest_date",
        "massive_short_volume": "short_volume",
        "massive_short_volume_date": "short_volume_date",
        "security_product_type": "sec_type",
        "snapshot_last_price": "last_price",
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

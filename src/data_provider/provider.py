from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import polars as pl

from src.data_provider.config import DataProviderConfig
from src.data_provider.calendar import market_sessions
from src.data_provider.manifest import read_manifest
from src.data_provider.raw_loader import date_range
from src.data_provider.store import existing_dates, partition_path


DEFAULT_MAX_ENTRY_PARTICIPATION_RATE = 0.05
DEFAULT_MAX_ENTRY_TRADE_MULTIPLE = 3.0


class MarketDataProvider:
    def __init__(self, config: DataProviderConfig | None = None):
        self.config = config or DataProviderConfig()

    @property
    def processed_root(self) -> Path:
        return self.config.processed_root

    def available_dates(self, timeframe: str = "1m") -> list[str]:
        return existing_dates(self.processed_root, "bars", timeframe)

    def missing_dates(self, start: date, end: date, timeframe: str = "1m") -> list[str]:
        available = set(self.available_dates(timeframe))
        return [session.isoformat() for session in market_sessions(start, end) if session.isoformat() not in available]

    def status_rows(self, start: date, end: date, timeframes: Iterable[str]) -> list[dict]:
        manifest = read_manifest(self.processed_root)
        artifacts = manifest.get("artifacts", {})
        rows = []
        for session in date_range(start, end):
            session_key = session.isoformat()
            row = {"session_date": session_key}
            for timeframe in timeframes:
                artifact = artifacts.get(f"bars|{timeframe}|{session_key}")
                row[f"{timeframe}_status"] = "ready" if artifact else "missing"
                row[f"{timeframe}_rows"] = artifact.get("rows", 0) if artifact else 0
            rows.append(row)
        return rows

    def _paths(self, group: str, timeframe: str, start: date, end: date) -> list[Path]:
        paths = []
        for session in date_range(start, end):
            path = partition_path(self.processed_root, group, timeframe, session)
            if path.exists():
                paths.append(path)
        return paths

    def load_bars(
        self,
        *,
        start_date: date,
        end_date: date,
        timeframe: str = "1m",
        tickers: list[str] | None = None,
        feature_groups: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        paths = self._paths("bars", timeframe, start_date, end_date)
        if not paths:
            return pl.DataFrame()
        scan = pl.scan_parquet([str(path) for path in paths], missing_columns="insert", extra_columns="ignore")
        if tickers:
            scan = scan.filter(pl.col("ticker").is_in(tickers))
        base = scan.collect()
        for group in feature_groups or []:
            feature_paths = self._paths(f"features_{group}", timeframe, start_date, end_date)
            if not feature_paths:
                continue
            feature_scan = pl.scan_parquet([str(path) for path in feature_paths], missing_columns="insert", extra_columns="ignore")
            if tickers and "ticker" in feature_scan.collect_schema().names():
                feature_scan = feature_scan.filter(pl.col("ticker").is_in(tickers))
            features = feature_scan.collect()
            if not features.is_empty() and "bar_id" in features.columns:
                duplicate_columns = [column for column in features.columns if column != "bar_id" and column in base.columns]
                if duplicate_columns:
                    features = features.drop(duplicate_columns)
                base = base.join(features, on="bar_id", how="left", coalesce=True)
        base = add_liquidity_capacity_columns(base)
        if columns:
            selected = [column for column in columns if column in base.columns]
            if selected:
                base = base.select(selected)
        return base.sort(["ticker", "bar_time_utc"]) if not base.is_empty() else base

    def load_supervision(
        self,
        *,
        start_date: date,
        end_date: date,
        timeframe: str = "1m",
        supervision_type: str = "bar",
        tickers: list[str] | None = None,
    ) -> pl.DataFrame:
        group = f"supervision_{supervision_type}"
        paths = self._paths(group, timeframe, start_date, end_date)
        if not paths:
            return pl.DataFrame()
        scan = pl.scan_parquet([str(path) for path in paths])
        if tickers:
            scan = scan.filter(pl.col("ticker").is_in(tickers))
        return scan.collect()


def add_liquidity_capacity_columns(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    names = frame.columns
    exprs = liquidity_capacity_expressions(names)
    if not exprs:
        return frame
    order_columns = [column for column in ["ticker", "bar_time_utc", "bar_time_market"] if column in names]
    sorted_frame = frame.sort(order_columns) if order_columns else frame
    return sorted_frame.with_columns(exprs)


def liquidity_capacity_expressions(names: list[str]) -> list[pl.Expr]:
    if "volume" not in names:
        return []
    volume = pl.col("volume").cast(pl.Float64, strict=False)
    participation_capacity = volume * DEFAULT_MAX_ENTRY_PARTICIPATION_RATE
    if "transactions" in names:
        transactions = pl.col("transactions").cast(pl.Float64, strict=False)
        average_trade_size = pl.when(transactions > 0).then(volume / transactions).otherwise(None)
        last_capacity = pl.min_horizontal(participation_capacity, average_trade_size * DEFAULT_MAX_ENTRY_TRADE_MULTIPLE)
    else:
        average_trade_size = pl.lit(None, dtype=pl.Float64)
        last_capacity = participation_capacity

    group_columns = ["ticker"] if "ticker" in names else []
    rolling_volume = volume.rolling_mean(3, min_samples=1).over(group_columns) if group_columns else volume.rolling_mean(3, min_samples=1)
    rolling_participation_capacity = rolling_volume * DEFAULT_MAX_ENTRY_PARTICIPATION_RATE
    if "transactions" in names:
        rolling_transactions = transactions.rolling_sum(3, min_samples=1).over(group_columns) if group_columns else transactions.rolling_sum(3, min_samples=1)
        rolling_average_trade_size = pl.when(rolling_transactions > 0).then(
            (volume.rolling_sum(3, min_samples=1).over(group_columns) if group_columns else volume.rolling_sum(3, min_samples=1))
            / rolling_transactions
        ).otherwise(None)
        rolling_capacity = pl.min_horizontal(rolling_participation_capacity, rolling_average_trade_size * DEFAULT_MAX_ENTRY_TRADE_MULTIPLE)
    else:
        rolling_capacity = rolling_participation_capacity

    exprs = [
        average_trade_size.alias("avg_trade_size"),
        last_capacity.floor().clip(0).cast(pl.Int64).alias("max_fill_qty_last_bar"),
        rolling_capacity.floor().clip(0).cast(pl.Int64).alias("max_fill_qty_3bar"),
        rolling_capacity.fill_null(last_capacity).floor().clip(0).cast(pl.Int64).alias("max_fill_qty"),
    ]
    if "close" in names:
        close = pl.col("close").cast(pl.Float64, strict=False)
        exprs.extend(
            [
                (last_capacity * close).alias("max_fill_notional_last_bar"),
                (rolling_capacity * close).alias("max_fill_notional_3bar"),
                (rolling_capacity.fill_null(last_capacity) * close).alias("max_fill_notional"),
            ]
        )
    return exprs

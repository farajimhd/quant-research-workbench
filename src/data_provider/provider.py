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
        scan = pl.scan_parquet([str(path) for path in paths])
        if tickers:
            scan = scan.filter(pl.col("ticker").is_in(tickers))
        base = scan.collect()
        for group in feature_groups or []:
            feature_paths = self._paths(f"features_{group}", timeframe, start_date, end_date)
            if not feature_paths:
                continue
            feature_scan = pl.scan_parquet([str(path) for path in feature_paths])
            if tickers and "ticker" in feature_scan.collect_schema().names():
                feature_scan = feature_scan.filter(pl.col("ticker").is_in(tickers))
            features = feature_scan.collect()
            if not features.is_empty() and "bar_id" in features.columns:
                duplicate_columns = [column for column in features.columns if column != "bar_id" and column in base.columns]
                if duplicate_columns:
                    features = features.drop(duplicate_columns)
                base = base.join(features, on="bar_id", how="left", coalesce=True)
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

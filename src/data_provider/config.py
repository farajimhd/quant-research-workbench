from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_RAW_ROOT = Path("D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1")
DEFAULT_PROCESSED_ROOT = Path("D:/TradingData/quant-research-workbench/market_data")
EXCHANGE_TIME_ZONE = "America/New_York"
SCHEMA_VERSION = 1
FEATURE_VERSION = 2
SUPERVISION_VERSION = 2

TIMEFRAMES: dict[str, int | str] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "1d": "1d",
    "1mo": "1mo",
}

FEATURE_GROUPS = [
    "core",
    "session",
    "momentum",
    "volatility",
    "volume_liquidity",
    "price_action",
    "shock",
    "fvg",
    "market_structure",
    "order_blocks",
]

SUPERVISION_GROUPS = [
    "bar",
    "method",
    "scanner",
]

REBUILD_MODES = ["force_rebuild"]


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(slots=True)
class DataProviderConfig:
    raw_root: Path = DEFAULT_RAW_ROOT
    processed_root: Path = DEFAULT_PROCESSED_ROOT
    exchange_timezone: str = EXCHANGE_TIME_ZONE
    schema_version: int = SCHEMA_VERSION
    feature_version: int = FEATURE_VERSION
    supervision_version: int = SUPERVISION_VERSION

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "DataProviderConfig":
        raw = raw or {}
        return cls(
            raw_root=Path(raw.get("raw_root") or raw.get("data_root") or DEFAULT_RAW_ROOT),
            processed_root=Path(raw.get("processed_root") or raw.get("processed_data_root") or DEFAULT_PROCESSED_ROOT),
            exchange_timezone=str(raw.get("exchange_timezone") or EXCHANGE_TIME_ZONE),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_root": str(self.raw_root),
            "processed_root": str(self.processed_root),
            "exchange_timezone": self.exchange_timezone,
            "schema_version": self.schema_version,
            "feature_version": self.feature_version,
            "supervision_version": self.supervision_version,
        }


@dataclass(slots=True)
class BuildRequest:
    raw_root: Path = DEFAULT_RAW_ROOT
    processed_root: Path = DEFAULT_PROCESSED_ROOT
    start_date: date = date(2024, 5, 1)
    end_date: date = date(2024, 5, 1)
    exchange_timezone: str = EXCHANGE_TIME_ZONE
    timeframes: list[str] = field(default_factory=lambda: ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1mo"])
    feature_groups: list[str] = field(default_factory=lambda: list(FEATURE_GROUPS))
    supervision_groups: list[str] = field(default_factory=lambda: list(SUPERVISION_GROUPS))
    rebuild_mode: str = "force_rebuild"
    tickers: list[str] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BuildRequest":
        return cls(
            raw_root=Path(raw.get("raw_root") or raw.get("data_root") or DEFAULT_RAW_ROOT),
            processed_root=Path(raw.get("processed_root") or raw.get("processed_data_root") or DEFAULT_PROCESSED_ROOT),
            start_date=parse_date(raw["start_date"]),
            end_date=parse_date(raw["end_date"]),
            exchange_timezone=str(raw.get("exchange_timezone") or EXCHANGE_TIME_ZONE),
            timeframes=list(raw.get("timeframes") or TIMEFRAMES.keys()),
            feature_groups=list(raw.get("feature_groups") or FEATURE_GROUPS),
            supervision_groups=list(raw.get("supervision_groups") or SUPERVISION_GROUPS),
            rebuild_mode="force_rebuild",
            tickers=list(raw["tickers"]) if raw.get("tickers") else None,
        )

    def to_config(self) -> DataProviderConfig:
        return DataProviderConfig(
            raw_root=self.raw_root,
            processed_root=self.processed_root,
            exchange_timezone=self.exchange_timezone,
        )

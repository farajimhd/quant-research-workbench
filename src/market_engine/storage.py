from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HistoricalClickHouseConfig:
    database: str
    endpoint_url: str
    password: str
    user: str


def historical_clickhouse_config() -> HistoricalClickHouseConfig:
    """Read the historical quote/trade database config without importing app services."""

    return HistoricalClickHouseConfig(
        database=os.environ.get("HISTORICAL_CLICKHOUSE_DATABASE", "market_sip_raw").strip() or "market_sip_raw",
        endpoint_url=os.environ.get("HISTORICAL_CLICKHOUSE_URL", os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")).strip().rstrip("/"),
        password=os.environ.get("HISTORICAL_CLICKHOUSE_PASSWORD", os.environ.get("CLICKHOUSE_PASSWORD", "")).strip(),
        user=os.environ.get("HISTORICAL_CLICKHOUSE_USER", os.environ.get("CLICKHOUSE_USER", "default")).strip() or "default",
    )

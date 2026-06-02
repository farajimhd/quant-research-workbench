from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CLICKHOUSE_URL = "http://localhost:8123"
DEFAULT_WRITE_DATABASE = "quant_research_workbench"
DEFAULT_MASSIVE_WS_URL = "wss://socket.massive.com/stocks"


def load_real_live_market_env() -> None:
    for env_path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


@dataclass(frozen=True)
class ClickHouseConfig:
    database: str
    endpoint_url: str
    password: str
    user: str


@dataclass(frozen=True)
class MassiveWebSocketConfig:
    api_key: str
    subscribe_batch_size: int
    url: str


@dataclass(frozen=True)
class MarketGatewayConfig:
    enable_clickhouse_writes: bool
    massive: MassiveWebSocketConfig
    max_universe_symbols: int
    min_avg_daily_volume: float
    min_price: float
    read_clickhouse: ClickHouseConfig
    scanner_row_limit: int
    signal_row_limit: int
    subscribe_quotes: bool
    subscribe_trades: bool
    universe_sql: str
    websocket_enabled: bool
    write_clickhouse: ClickHouseConfig

    @property
    def clickhouse(self) -> ClickHouseConfig:
        return self.read_clickhouse


def market_gateway_config() -> MarketGatewayConfig:
    load_real_live_market_env()
    read_clickhouse = ClickHouseConfig(
        database=env_first("REAL_LIVE_CLICKHOUSE_READ_DATABASE", "REAL_LIVE_UNIVERSE_CLICKHOUSE_DATABASE", "REAL_LIVE_CLICKHOUSE_DATABASE", "CLICKHOUSE_DATABASE", "TD__DATABASE__CLICKHOUSE__DATABASE", default="default"),
        endpoint_url=env_first("REAL_LIVE_CLICKHOUSE_READ_URL", "REAL_LIVE_UNIVERSE_CLICKHOUSE_URL", "REAL_LIVE_CLICKHOUSE_URL", "CLICKHOUSE_URL", "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL", default=DEFAULT_CLICKHOUSE_URL).rstrip("/"),
        password=env_first("REAL_LIVE_CLICKHOUSE_READ_PASSWORD", "REAL_LIVE_UNIVERSE_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_PASSWORD", "CLICKHOUSE_PASSWORD", "TD__DATABASE__CLICKHOUSE__PASSWORD", default=""),
        user=env_first("REAL_LIVE_CLICKHOUSE_READ_USER", "REAL_LIVE_UNIVERSE_CLICKHOUSE_USER", "REAL_LIVE_CLICKHOUSE_USER", "CLICKHOUSE_USER", "TD__DATABASE__CLICKHOUSE__USER", default="default"),
    )
    write_clickhouse = ClickHouseConfig(
        database=env_first("REAL_LIVE_CLICKHOUSE_WRITE_DATABASE", "REAL_LIVE_APP_CLICKHOUSE_DATABASE", "REAL_LIVE_REPLAY_CLICKHOUSE_DATABASE", default=DEFAULT_WRITE_DATABASE),
        endpoint_url=env_first("REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_APP_CLICKHOUSE_URL", "REAL_LIVE_REPLAY_CLICKHOUSE_URL", "REAL_LIVE_CLICKHOUSE_URL", "CLICKHOUSE_URL", "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL", default=read_clickhouse.endpoint_url).rstrip("/"),
        password=env_first("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD", "REAL_LIVE_APP_CLICKHOUSE_PASSWORD", "REAL_LIVE_REPLAY_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_PASSWORD", "CLICKHOUSE_PASSWORD", "TD__DATABASE__CLICKHOUSE__PASSWORD", default=read_clickhouse.password),
        user=env_first("REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_APP_CLICKHOUSE_USER", "REAL_LIVE_REPLAY_CLICKHOUSE_USER", "REAL_LIVE_CLICKHOUSE_USER", "CLICKHOUSE_USER", "TD__DATABASE__CLICKHOUSE__USER", default=read_clickhouse.user),
    )
    massive = MassiveWebSocketConfig(
        api_key=env_first("MASSIVE_API_KEY", "MASSIVE_STOCK_API_KEY", "POLYGON_API_KEY", default=""),
        subscribe_batch_size=positive_int_env("REAL_LIVE_MASSIVE_SUBSCRIBE_BATCH_SIZE", 400),
        url=env_first("REAL_LIVE_MASSIVE_WS_URL", "MASSIVE_WS_URL", default=DEFAULT_MASSIVE_WS_URL).rstrip("/"),
    )
    return MarketGatewayConfig(
        enable_clickhouse_writes=bool_env("REAL_LIVE_CLICKHOUSE_WRITES", True),
        massive=massive,
        max_universe_symbols=positive_int_env("REAL_LIVE_MAX_UNIVERSE_SYMBOLS", 6000),
        min_avg_daily_volume=float_env("REAL_LIVE_MIN_AVG_DAILY_VOLUME", 100_000),
        min_price=float_env("REAL_LIVE_MIN_PRICE", 1.0),
        read_clickhouse=read_clickhouse,
        scanner_row_limit=positive_int_env("REAL_LIVE_SCANNER_ROW_LIMIT", 500),
        signal_row_limit=positive_int_env("REAL_LIVE_SIGNAL_ROW_LIMIT", 500),
        subscribe_quotes=bool_env("REAL_LIVE_SUBSCRIBE_QUOTES", True),
        subscribe_trades=bool_env("REAL_LIVE_SUBSCRIBE_TRADES", True),
        universe_sql=os.environ.get("REAL_LIVE_UNIVERSE_SQL", "").strip(),
        websocket_enabled=bool_env("REAL_LIVE_MARKET_WEBSOCKET_ENABLED", True),
        write_clickhouse=write_clickhouse,
    )


def env_first(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return max(1, value)

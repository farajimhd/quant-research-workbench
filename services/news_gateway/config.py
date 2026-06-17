from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class NewsGatewayConfig:
    bind: str
    host: str
    port: int
    data_root_win: Path
    raw_root_win: Path
    prepared_root_win: Path
    is_workstation: bool
    massive_api_key_present: bool
    benzinga_url: str
    market_poll_seconds: float
    premarket_poll_seconds: float
    afterhours_poll_seconds: float
    closed_poll_seconds: float
    lookback_minutes: int
    restart_gap_max_days: int
    poll_overlap_seconds: int
    page_limit: int
    max_pages: int
    execute: bool
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password_present: bool
    clickhouse_database: str
    normalized_table: str
    ticker_table: str
    recent_history_limit: int
    write_batch_size: int
    policy_json: str
    text_limit_chars: int

    @classmethod
    def from_env(cls) -> "NewsGatewayConfig":
        bind = env_string("NEWS_GATEWAY_BIND", "127.0.0.1:8796")
        host, port = parse_bind(bind)
        data_root = resolve_data_root()
        raw_root = Path(env_string("NEWS_BENZINGA_RAW_ROOT_WIN", str(data_root / "news-benzinga" / "raw")))
        prepared_root = Path(env_string("NEWS_BENZINGA_PREPARED_ROOT_WIN", str(data_root / "prepared")))
        clickhouse_password = env_string("NEWS_CLICKHOUSE_PASSWORD", "") or env_string("QMD_CLICKHOUSE_PASSWORD", "") or env_string("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD", "")
        return cls(
            bind=bind,
            host=host,
            port=port,
            data_root_win=data_root,
            raw_root_win=raw_root,
            prepared_root_win=prepared_root,
            is_workstation=is_workstation_host(),
            massive_api_key_present=bool(env_string("MASSIVE_API_KEY", "")),
            benzinga_url=env_string("NEWS_BENZINGA_URL", env_string("NEWS_MASSIVE_BENZINGA_URL", "https://api.massive.com/benzinga/v2/news")),
            market_poll_seconds=env_float("NEWS_BENZINGA_MARKET_POLL_SECONDS", 5.0),
            premarket_poll_seconds=env_float("NEWS_BENZINGA_PREMARKET_POLL_SECONDS", 10.0),
            afterhours_poll_seconds=env_float("NEWS_BENZINGA_AFTERHOURS_POLL_SECONDS", 15.0),
            closed_poll_seconds=env_float("NEWS_BENZINGA_CLOSED_POLL_SECONDS", 60.0),
            lookback_minutes=env_int("NEWS_BENZINGA_LOOKBACK_MINUTES", 15),
            restart_gap_max_days=env_int("NEWS_BENZINGA_RESTART_GAP_MAX_DAYS", 3),
            poll_overlap_seconds=env_int("NEWS_BENZINGA_POLL_OVERLAP_SECONDS", 120),
            page_limit=env_int("NEWS_BENZINGA_PAGE_LIMIT", 1_000),
            max_pages=env_int("NEWS_BENZINGA_MAX_PAGES", 1_000),
            execute=env_bool("NEWS_BENZINGA_EXECUTE", True),
            clickhouse_url=env_string("NEWS_CLICKHOUSE_URL", env_string("QMD_CLICKHOUSE_URL", env_string("REAL_LIVE_CLICKHOUSE_WRITE_URL", "http://localhost:8123"))).rstrip("/"),
            clickhouse_user=env_string("NEWS_CLICKHOUSE_USER", env_string("QMD_CLICKHOUSE_USER", env_string("REAL_LIVE_CLICKHOUSE_WRITE_USER", "default"))),
            clickhouse_password_present=bool(clickhouse_password),
            clickhouse_database=env_string("NEWS_BENZINGA_CLICKHOUSE_DATABASE", env_string("NEWS_CLICKHOUSE_DATABASE", "q_live")),
            normalized_table=env_string("NEWS_BENZINGA_NORMALIZED_TABLE", "benzinga_news_normalized_v1"),
            ticker_table=env_string("NEWS_BENZINGA_TICKER_TABLE", "benzinga_news_ticker_v1"),
            recent_history_limit=env_int("NEWS_RECENT_HISTORY_LIMIT", 5_000),
            write_batch_size=env_int("NEWS_CLICKHOUSE_MAX_BATCH", 1_000),
            policy_json=env_string("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON", ""),
            text_limit_chars=env_int("NEWS_BENZINGA_TEXT_LIMIT_CHARS", 50_000),
        )

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["data_root_win"] = str(self.data_root_win)
        payload["raw_root_win"] = str(self.raw_root_win)
        payload["prepared_root_win"] = str(self.prepared_root_win)
        return payload


def resolve_data_root() -> Path:
    explicit = os.environ.get("NEWS_GATEWAY_DATA_ROOT_WIN", "").strip()
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise RuntimeError(f"NEWS_GATEWAY_DATA_ROOT_WIN does not exist: {path}")
    if is_workstation_host():
        if WORKSTATION_DATA_ROOT_WIN.exists():
            return WORKSTATION_DATA_ROOT_WIN
        raise RuntimeError("Workstation data root D:/market-data is not available. Create it or set NEWS_GATEWAY_DATA_ROOT_WIN.")
    if WORKSTATION_SHARE_DATA_ROOT_WIN.exists():
        return WORKSTATION_SHARE_DATA_ROOT_WIN
    raise RuntimeError(
        "Workstation market-data root is not available. Start the service on the workstation, "
        "mount \\\\DESKTOP-SAAI85T\\Workstation-D\\market-data, or set NEWS_GATEWAY_DATA_ROOT_WIN."
    )


def is_workstation_host() -> bool:
    return os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_COMPUTER_NAME


def parse_bind(value: str) -> tuple[str, int]:
    text = value.strip()
    if ":" not in text:
        return text, 8796
    host, port_text = text.rsplit(":", 1)
    return host, int(port_text)


def env_string(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_string(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_string(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

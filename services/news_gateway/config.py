from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")
WORKSTATION_CODE_ROOT_WIN = Path("D:/TradingML/codes/quant_research_workbench_pipelines")
WORKSTATION_SHARE_CODE_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines")


@dataclass(frozen=True, slots=True)
class NewsGatewayConfig:
    bind: str
    host: str
    port: int
    data_root_win: Path
    raw_root_win: Path
    prepared_root_win: Path
    manual_gap_manifest_root_win: Path
    manual_gap_script_root_win: Path
    workstation_code_root_win: Path
    workstation_conda_env: str
    is_workstation: bool
    massive_api_key_present: bool
    benzinga_url: str
    market_poll_seconds: float
    premarket_poll_seconds: float
    afterhours_poll_seconds: float
    closed_poll_seconds: float
    lookback_minutes: int
    startup_auto_fill_max_gap_days: int
    coverage_discovery_chunk_seconds: int
    gap_fill_chunk_minutes: int
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
    coverage_table: str
    recent_history_limit: int
    write_batch_size: int
    policy_json: str
    text_limit_chars: int
    terminal_rich_enabled: bool
    terminal_refresh_seconds: float
    terminal_news_limit: int

    @classmethod
    def from_env(cls) -> "NewsGatewayConfig":
        bind = env_string("NEWS_GATEWAY_BIND", "127.0.0.1:8796")
        host, port = parse_bind(bind)
        data_root = resolve_data_root()
        raw_root = Path(env_string("NEWS_BENZINGA_RAW_ROOT_WIN", str(data_root / "news-benzinga" / "raw")))
        prepared_root = Path(env_string("NEWS_BENZINGA_PREPARED_ROOT_WIN", str(data_root / "prepared")))
        manual_gap_manifest_root = Path(
            env_string(
                "NEWS_BENZINGA_MANUAL_GAP_MANIFEST_ROOT_WIN",
                str(prepared_root / "news_gateway_manual_gap_fill"),
            )
        )
        workstation_code_root = Path(env_string("NEWS_GATEWAY_WORKSTATION_CODE_ROOT_WIN", str(WORKSTATION_CODE_ROOT_WIN)))
        manual_gap_script_root = Path(
            env_string(
                "NEWS_BENZINGA_MANUAL_GAP_SCRIPT_ROOT_WIN",
                str(workstation_code_root / "generated" / "news_gateway_manual_gap_fill"),
            )
        )
        clickhouse_password = default_clickhouse_password()
        return cls(
            bind=bind,
            host=host,
            port=port,
            data_root_win=data_root,
            raw_root_win=raw_root,
            prepared_root_win=prepared_root,
            manual_gap_manifest_root_win=manual_gap_manifest_root,
            manual_gap_script_root_win=manual_gap_script_root,
            workstation_code_root_win=workstation_code_root,
            workstation_conda_env=env_string("NEWS_GATEWAY_WORKSTATION_CONDA_ENV", "ml4t"),
            is_workstation=is_workstation_host(),
            massive_api_key_present=bool(env_string("MASSIVE_API_KEY", "")),
            benzinga_url=env_string("NEWS_BENZINGA_URL", env_string("NEWS_MASSIVE_BENZINGA_URL", "https://api.massive.com/benzinga/v2/news")),
            market_poll_seconds=env_float("NEWS_BENZINGA_MARKET_POLL_SECONDS", 5.0),
            premarket_poll_seconds=env_float("NEWS_BENZINGA_PREMARKET_POLL_SECONDS", 10.0),
            afterhours_poll_seconds=env_float("NEWS_BENZINGA_AFTERHOURS_POLL_SECONDS", 15.0),
            closed_poll_seconds=env_float("NEWS_BENZINGA_CLOSED_POLL_SECONDS", 60.0),
            lookback_minutes=env_int("NEWS_BENZINGA_LOOKBACK_MINUTES", 15),
            startup_auto_fill_max_gap_days=env_int("NEWS_BENZINGA_STARTUP_AUTO_FILL_MAX_GAP_DAYS", 30),
            coverage_discovery_chunk_seconds=env_int("NEWS_BENZINGA_COVERAGE_DISCOVERY_CHUNK_SECONDS", 300),
            gap_fill_chunk_minutes=env_int("NEWS_BENZINGA_GAP_FILL_CHUNK_MINUTES", 90),
            poll_overlap_seconds=env_int("NEWS_BENZINGA_POLL_OVERLAP_SECONDS", 120),
            page_limit=env_int("NEWS_BENZINGA_PAGE_LIMIT", 1_000),
            max_pages=env_int("NEWS_BENZINGA_MAX_PAGES", 1_000),
            execute=env_bool("NEWS_BENZINGA_EXECUTE", True),
            clickhouse_url=default_clickhouse_url(),
            clickhouse_user=default_clickhouse_user(),
            clickhouse_password_present=bool(clickhouse_password),
            clickhouse_database=env_string("NEWS_BENZINGA_CLICKHOUSE_DATABASE", env_string("NEWS_CLICKHOUSE_DATABASE", "q_live")),
            normalized_table=env_string("NEWS_BENZINGA_NORMALIZED_TABLE", "benzinga_news_normalized_v1"),
            ticker_table=env_string("NEWS_BENZINGA_TICKER_TABLE", "benzinga_news_ticker_v1"),
            coverage_table=env_string("NEWS_BENZINGA_COVERAGE_TABLE", "benzinga_news_coverage_manifest_v1"),
            recent_history_limit=env_int("NEWS_RECENT_HISTORY_LIMIT", 5_000),
            write_batch_size=env_int("NEWS_CLICKHOUSE_MAX_BATCH", 1_000),
            policy_json=env_string("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON", ""),
            text_limit_chars=env_int("NEWS_BENZINGA_TEXT_LIMIT_CHARS", 50_000),
            terminal_rich_enabled=env_bool_auto("NEWS_TERMINAL_RICH_ENABLED", sys.stdout.isatty()),
            terminal_refresh_seconds=env_float("NEWS_TERMINAL_REFRESH_SECONDS", 1.0),
            terminal_news_limit=env_int("NEWS_TERMINAL_NEWS_LIMIT", 12),
        )

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["data_root_win"] = str(self.data_root_win)
        payload["raw_root_win"] = str(self.raw_root_win)
        payload["prepared_root_win"] = str(self.prepared_root_win)
        payload["manual_gap_manifest_root_win"] = str(self.manual_gap_manifest_root_win)
        payload["manual_gap_script_root_win"] = str(self.manual_gap_script_root_win)
        payload["workstation_code_root_win"] = str(self.workstation_code_root_win)
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


def env_bool_auto(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip() or value.strip().lower() == "auto":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default


def default_clickhouse_url() -> str:
    return first_env(
        "NEWS_CLICKHOUSE_URL",
        "QLIVE_MIGRATION_CLICKHOUSE_URL",
        "QMD_CLICKHOUSE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "CLICKHOUSE_URL",
        "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL",
        default="http://localhost:8123",
    ).rstrip("/")


def default_clickhouse_user() -> str:
    return first_env(
        "NEWS_CLICKHOUSE_USER",
        "QLIVE_MIGRATION_CLICKHOUSE_USER",
        "QMD_CLICKHOUSE_USER",
        "REAL_LIVE_CLICKHOUSE_WRITE_USER",
        "CLICKHOUSE_WORKSTATION_USER",
        "CLICKHOUSE_USER",
        "TD__DATABASE__CLICKHOUSE__USER",
        default="default",
    )


def default_clickhouse_password() -> str:
    return first_env(
        "NEWS_CLICKHOUSE_PASSWORD",
        "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
        "QMD_CLICKHOUSE_PASSWORD",
        "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
        "CLICKHOUSE_WORKSTATION_PASSWORD",
        "CLICKHOUSE_PASSWORD",
        "TD__DATABASE__CLICKHOUSE__PASSWORD",
        default="",
    )

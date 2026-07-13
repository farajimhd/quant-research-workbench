from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pipelines.sec.edgar.sec_pipeline.config import SecPipelineConfig, env_bool, env_float, env_int, env_string
from pipelines.sec.edgar.sec_pipeline.xbrl_context import XbrlContextSyncConfig


WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")
WORKSTATION_CODE_ROOT_WIN = Path("D:/TradingML/codes/quant_research_workbench_pipelines")
WORKSTATION_SHARE_CODE_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines")


@dataclass(frozen=True, slots=True)
class SecGatewayConfig:
    bind: str
    host: str
    port: int
    pipeline: SecPipelineConfig
    is_workstation: bool
    execute: bool
    poll_seconds: float
    closed_poll_seconds: float
    market_status_url: str
    market_holidays_url: str
    market_status_enabled: bool
    market_status_refresh_seconds: float
    current_feed_count: int
    startup_auto_fill_max_gap_days: int
    auto_run_historical_on_workstation: bool
    live_workers: int
    live_queue_max_items: int
    submissions_cache_entries: int
    submissions_cache_max_age_seconds: float
    xbrl_payload_cache_entries: int
    xbrl_payload_cache_max_age_seconds: float
    xbrl_missing_cik_cache_entries: int
    xbrl_context_sync_enabled: bool
    xbrl_context_database: str
    xbrl_context_table: str
    xbrl_context_manifest_table: str
    xbrl_context_reconcile_limit: int
    xbrl_context_max_threads: int
    xbrl_context_max_memory_usage: str
    xbrl_context_insert_batch_rows: int
    recent_metadata_retention_hours: float
    full_audit_on_startup: bool
    full_audit_after_write_batches: int
    terminal_rich_enabled: bool
    terminal_screen_enabled: bool
    terminal_refresh_seconds: float
    graceful_shutdown_seconds: float
    run_log_enabled: bool
    run_log_queue_size: int

    @classmethod
    def from_env(cls) -> "SecGatewayConfig":
        bind = env_string("SEC_GATEWAY_BIND", "127.0.0.1:8797")
        host, port = parse_bind(bind)
        pipeline = SecPipelineConfig.from_env()
        return cls(
            bind=bind,
            host=host,
            port=port,
            pipeline=pipeline,
            is_workstation=is_workstation_host(),
            execute=env_bool("SEC_GATEWAY_EXECUTE", True),
            poll_seconds=env_float("SEC_GATEWAY_POLL_SECONDS", env_float("SEC_GATEWAY_MARKET_POLL_SECONDS", 30.0)),
            closed_poll_seconds=env_float("SEC_GATEWAY_CLOSED_POLL_SECONDS", 300.0),
            market_status_url=env_string("SEC_MARKET_STATUS_URL", env_string("NEWS_MARKET_STATUS_URL", "https://api.massive.com/v1/marketstatus/now")),
            market_holidays_url=env_string("SEC_MARKET_HOLIDAYS_URL", env_string("NEWS_MARKET_HOLIDAYS_URL", "https://api.massive.com/v1/marketstatus/upcoming")),
            market_status_enabled=env_bool("SEC_MARKET_STATUS_ENABLED", True),
            market_status_refresh_seconds=env_float("SEC_MARKET_STATUS_REFRESH_SECONDS", 10.0),
            current_feed_count=env_int("SEC_GATEWAY_CURRENT_FEED_COUNT", 100),
            startup_auto_fill_max_gap_days=env_int("SEC_GATEWAY_STARTUP_AUTO_FILL_MAX_GAP_DAYS", 3),
            auto_run_historical_on_workstation=env_bool("SEC_GATEWAY_AUTO_RUN_HISTORICAL_ON_WORKSTATION", is_workstation_host()),
            live_workers=env_int("SEC_GATEWAY_LIVE_WORKERS", 4),
            live_queue_max_items=env_int("SEC_GATEWAY_LIVE_QUEUE_MAX_ITEMS", 500),
            submissions_cache_entries=env_int("SEC_GATEWAY_SUBMISSIONS_CACHE_ENTRIES", 512),
            submissions_cache_max_age_seconds=env_float("SEC_GATEWAY_SUBMISSIONS_CACHE_MAX_AGE_SECONDS", 3600.0),
            xbrl_payload_cache_entries=env_int("SEC_GATEWAY_XBRL_PAYLOAD_CACHE_ENTRIES", 32),
            xbrl_payload_cache_max_age_seconds=env_float("SEC_GATEWAY_XBRL_PAYLOAD_CACHE_MAX_AGE_SECONDS", 3600.0),
            xbrl_missing_cik_cache_entries=env_int("SEC_GATEWAY_XBRL_MISSING_CIK_CACHE_ENTRIES", 5_000),
            xbrl_context_sync_enabled=env_bool("SEC_GATEWAY_XBRL_CONTEXT_SYNC_ENABLED", True),
            xbrl_context_database=env_string("SEC_GATEWAY_XBRL_CONTEXT_DATABASE", "market_sip_compact"),
            xbrl_context_table=env_string("SEC_GATEWAY_XBRL_CONTEXT_TABLE", "sec_xbrl_context_v3"),
            xbrl_context_manifest_table=env_string(
                "SEC_GATEWAY_XBRL_CONTEXT_MANIFEST_TABLE",
                "sec_xbrl_context_sync_manifest_v3",
            ),
            xbrl_context_reconcile_limit=env_int("SEC_GATEWAY_XBRL_CONTEXT_RECONCILE_LIMIT", 100),
            xbrl_context_max_threads=env_int("SEC_GATEWAY_XBRL_CONTEXT_MAX_THREADS", 8),
            xbrl_context_max_memory_usage=env_string("SEC_GATEWAY_XBRL_CONTEXT_MAX_MEMORY", "16G"),
            xbrl_context_insert_batch_rows=env_int("SEC_GATEWAY_XBRL_CONTEXT_INSERT_BATCH_ROWS", 10_000),
            recent_metadata_retention_hours=env_float("SEC_GATEWAY_RECENT_METADATA_RETENTION_HOURS", 24.0),
            full_audit_on_startup=env_bool("SEC_GATEWAY_FULL_AUDIT_ON_STARTUP", True),
            full_audit_after_write_batches=env_int("SEC_GATEWAY_FULL_AUDIT_AFTER_WRITE_BATCHES", 0),
            terminal_rich_enabled=env_bool_auto("SEC_GATEWAY_TERMINAL_RICH_ENABLED", sys.stdout.isatty()),
            terminal_screen_enabled=env_bool("SEC_GATEWAY_TERMINAL_SCREEN_ENABLED", True),
            terminal_refresh_seconds=env_float("SEC_GATEWAY_TERMINAL_REFRESH_SECONDS", 1.0),
            graceful_shutdown_seconds=env_float("SEC_GATEWAY_GRACEFUL_SHUTDOWN_SECONDS", 300.0),
            run_log_enabled=env_bool("SEC_GATEWAY_RUN_LOG_ENABLED", True),
            run_log_queue_size=env_int("SEC_GATEWAY_RUN_LOG_QUEUE_SIZE", 10_000),
        )

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["pipeline"] = self.pipeline.public_dict()
        return payload


def resolve_data_root() -> Path:
    explicit = os.environ.get("SEC_GATEWAY_DATA_ROOT_WIN", "").strip()
    if explicit:
        path = Path(explicit)
        if path_exists(path):
            return path
        raise RuntimeError(f"SEC_GATEWAY_DATA_ROOT_WIN does not exist: {path}")
    if is_workstation_host():
        if path_exists(WORKSTATION_DATA_ROOT_WIN):
            return WORKSTATION_DATA_ROOT_WIN
        raise RuntimeError("Workstation data root D:/market-data is not available.")
    if path_exists(WORKSTATION_SHARE_DATA_ROOT_WIN):
        return WORKSTATION_SHARE_DATA_ROOT_WIN
    if path_exists(WORKSTATION_DATA_ROOT_WIN):
        return WORKSTATION_DATA_ROOT_WIN
    raise RuntimeError("Workstation market-data root is not available. Start on workstation or mount the share.")


def is_workstation_host() -> bool:
    return os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_COMPUTER_NAME


def parse_bind(value: str) -> tuple[str, int]:
    text = value.strip()
    if ":" not in text:
        return text, 8797
    host, port_text = text.rsplit(":", 1)
    return host, int(port_text)


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def env_bool_auto(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip() or value.strip().lower() == "auto":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def xbrl_context_sync_config(config: SecGatewayConfig) -> XbrlContextSyncConfig:
    return XbrlContextSyncConfig(
        source_database=config.pipeline.clickhouse.write_database,
        bridge_database=config.pipeline.clickhouse.read_database,
        context_database=config.xbrl_context_database,
        bridge_table="id_sec_market_bridge_v3",
        context_table=config.xbrl_context_table,
        manifest_table=config.xbrl_context_manifest_table,
        storage_policy=os.environ.get("CLICKHOUSE_HISTORICAL_STORAGE_POLICY") or "",
        max_threads=max(1, int(config.xbrl_context_max_threads)),
        max_memory_usage=config.xbrl_context_max_memory_usage,
        insert_batch_rows=max(1, int(config.xbrl_context_insert_batch_rows)),
    )

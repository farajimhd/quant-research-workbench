from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from research.mlops.clickhouse import (
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
)
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback


WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class TextEmbedGatewayConfig:
    bind: str
    host: str
    port: int
    data_root_win: Path
    log_root_win: Path
    is_workstation: bool
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password_present: bool
    source_database: str
    context_database: str
    target_database: str
    news_token_table: str
    sec_token_table: str
    news_embedding_table: str
    sec_embedding_table: str
    coverage_table: str
    sec_context_filing_table: str
    sec_context_text_table: str
    sec_live_filing_table: str
    sec_live_text_table: str
    sec_bridge_table: str
    sec_max_text_rows_per_filing: int
    sec_context_refresh_chunk_hours: float
    sec_context_historical_max_chunks_per_cycle: int
    storage_policy: str
    tokenizer_model: str
    embedding_model: str
    embedding_device: str
    embedding_torch_dtype: str
    embedding_pooling: str
    local_files_only: bool
    news_max_tokens: int
    news_max_chunks: int
    sec_chunk_tokens: int
    sec_max_chunks: int
    news_body_prefix_chars: int
    news_external_prefix_chars: int
    news_pdf_prefix_chars: int
    sec_text_prefix_chars: int
    source_batch_size: int
    token_batch_size: int
    embedding_batch_size: int
    embedding_insert_batch_size: int
    live_poll_seconds: float
    closed_poll_seconds: float
    weekend_poll_seconds: float
    live_lookback_minutes: int
    historical_lookback_days: int
    historical_batch_limit: int
    max_threads: int
    max_memory_usage: str
    market_status_url: str
    market_holidays_url: str
    market_status_enabled: bool
    market_status_refresh_seconds: float
    terminal_rich_enabled: bool
    terminal_screen_enabled: bool
    terminal_refresh_seconds: float
    graceful_shutdown_seconds: float
    recent_status_limit: int
    recent_status_retention_hours: float
    run_log_enabled: bool
    run_log_queue_size: int

    @classmethod
    def from_env(cls) -> "TextEmbedGatewayConfig":
        bind = env_string("TEXT_EMBED_GATEWAY_BIND", "127.0.0.1:8798")
        host, port = parse_bind(bind)
        data_root = resolve_data_root()
        clickhouse_password = default_clickhouse_password()
        return cls(
            bind=bind,
            host=host,
            port=port,
            data_root_win=data_root,
            log_root_win=Path(env_string("TEXT_EMBED_GATEWAY_LOG_ROOT_WIN", str(data_root / "prepared" / "text_embed_gateway" / "logs"))),
            is_workstation=is_workstation_host(),
            clickhouse_url=default_clickhouse_url_with_network_fallback(),
            clickhouse_user=default_clickhouse_user(),
            clickhouse_password_present=bool(clickhouse_password),
            source_database=env_string("TEXT_EMBED_SOURCE_DATABASE", "q_live"),
            context_database=env_string("TEXT_EMBED_CONTEXT_DATABASE", "market_sip_compact"),
            target_database=env_string("TEXT_EMBED_TARGET_DATABASE", "market_sip_compact"),
            news_token_table=env_string("TEXT_EMBED_NEWS_TOKEN_TABLE", "news_text_tokens"),
            sec_token_table=env_string("TEXT_EMBED_SEC_TOKEN_TABLE", "sec_filing_text_tokens"),
            news_embedding_table=env_string("TEXT_EMBED_NEWS_EMBEDDING_TABLE", "news_text_embeddings"),
            sec_embedding_table=env_string("TEXT_EMBED_SEC_EMBEDDING_TABLE", "sec_filing_text_embeddings"),
            coverage_table=env_string("TEXT_EMBED_COVERAGE_TABLE", "text_embedding_coverage_v1"),
            sec_context_filing_table=env_string("TEXT_EMBED_SEC_CONTEXT_FILING_TABLE", "sec_filing_context"),
            sec_context_text_table=env_string("TEXT_EMBED_SEC_CONTEXT_TEXT_TABLE", "sec_filing_text_context"),
            sec_live_filing_table=env_string("TEXT_EMBED_SEC_LIVE_FILING_TABLE", "sec_filing_v2"),
            sec_live_text_table=env_string("TEXT_EMBED_SEC_LIVE_TEXT_TABLE", "sec_filing_text_v1"),
            sec_bridge_table=env_string("TEXT_EMBED_SEC_BRIDGE_TABLE", "id_sec_market_bridge_v1"),
            sec_max_text_rows_per_filing=env_int("TEXT_EMBED_SEC_MAX_TEXT_ROWS_PER_FILING", 0),
            sec_context_refresh_chunk_hours=env_float("TEXT_EMBED_SEC_CONTEXT_REFRESH_CHUNK_HOURS", 24.0),
            sec_context_historical_max_chunks_per_cycle=env_int("TEXT_EMBED_SEC_CONTEXT_HISTORICAL_MAX_CHUNKS_PER_CYCLE", 7),
            storage_policy=env_string("TEXT_EMBED_STORAGE_POLICY", default_storage_policy()),
            tokenizer_model=env_string("TEXT_EMBED_TOKENIZER_MODEL", "Qwen/Qwen3-0.6B"),
            embedding_model=env_string("TEXT_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
            embedding_device=env_string("TEXT_EMBED_DEVICE", "auto"),
            embedding_torch_dtype=env_string("TEXT_EMBED_TORCH_DTYPE", "bfloat16"),
            embedding_pooling=env_string("TEXT_EMBED_POOLING", "last_token"),
            local_files_only=env_bool("TEXT_EMBED_LOCAL_FILES_ONLY", True),
            news_max_tokens=env_int("TEXT_EMBED_NEWS_MAX_TOKENS", 1024),
            news_max_chunks=env_int("TEXT_EMBED_NEWS_MAX_CHUNKS", 2),
            sec_chunk_tokens=env_int("TEXT_EMBED_SEC_CHUNK_TOKENS", 1024),
            sec_max_chunks=env_int("TEXT_EMBED_SEC_MAX_CHUNKS", 8),
            news_body_prefix_chars=env_int("TEXT_EMBED_NEWS_BODY_PREFIX_CHARS", 12_000),
            news_external_prefix_chars=env_int("TEXT_EMBED_NEWS_EXTERNAL_PREFIX_CHARS", 12_000),
            news_pdf_prefix_chars=env_int("TEXT_EMBED_NEWS_PDF_PREFIX_CHARS", 12_000),
            sec_text_prefix_chars=env_int("TEXT_EMBED_SEC_TEXT_PREFIX_CHARS", 0),
            source_batch_size=env_int("TEXT_EMBED_SOURCE_BATCH_SIZE", 64),
            token_batch_size=env_int("TEXT_EMBED_TOKEN_BATCH_SIZE", 256),
            embedding_batch_size=env_int("TEXT_EMBED_BATCH_SIZE", 16),
            embedding_insert_batch_size=env_int("TEXT_EMBED_INSERT_BATCH_SIZE", 64),
            live_poll_seconds=env_float("TEXT_EMBED_LIVE_POLL_SECONDS", 2.0),
            closed_poll_seconds=env_float("TEXT_EMBED_CLOSED_POLL_SECONDS", 60.0),
            weekend_poll_seconds=env_float("TEXT_EMBED_WEEKEND_POLL_SECONDS", 300.0),
            live_lookback_minutes=env_int("TEXT_EMBED_LIVE_LOOKBACK_MINUTES", 180),
            historical_lookback_days=max(60, env_int("TEXT_EMBED_HISTORICAL_LOOKBACK_DAYS", 60)),
            historical_batch_limit=env_int("TEXT_EMBED_HISTORICAL_BATCH_LIMIT", 512),
            max_threads=env_int("TEXT_EMBED_CLICKHOUSE_MAX_THREADS", 8),
            max_memory_usage=env_string("TEXT_EMBED_CLICKHOUSE_MAX_MEMORY_USAGE", "16G"),
            market_status_url=env_string("TEXT_EMBED_MARKET_STATUS_URL", env_string("NEWS_MARKET_STATUS_URL", "https://api.massive.com/v1/marketstatus/now")),
            market_holidays_url=env_string("TEXT_EMBED_MARKET_HOLIDAYS_URL", env_string("NEWS_MARKET_HOLIDAYS_URL", "https://api.massive.com/v1/marketstatus/upcoming")),
            market_status_enabled=env_bool("TEXT_EMBED_MARKET_STATUS_ENABLED", True),
            market_status_refresh_seconds=env_float("TEXT_EMBED_MARKET_STATUS_REFRESH_SECONDS", 10.0),
            terminal_rich_enabled=env_bool_auto("TEXT_EMBED_TERMINAL_RICH_ENABLED", sys.stdout.isatty()),
            terminal_screen_enabled=env_bool("TEXT_EMBED_TERMINAL_SCREEN_ENABLED", True),
            terminal_refresh_seconds=env_float("TEXT_EMBED_TERMINAL_REFRESH_SECONDS", 1.0),
            graceful_shutdown_seconds=env_float("TEXT_EMBED_GRACEFUL_SHUTDOWN_SECONDS", 180.0),
            recent_status_limit=env_int("TEXT_EMBED_RECENT_STATUS_LIMIT", 100),
            recent_status_retention_hours=env_float("TEXT_EMBED_RECENT_STATUS_RETENTION_HOURS", 2.0),
            run_log_enabled=env_bool("TEXT_EMBED_RUN_LOG_ENABLED", True),
            run_log_queue_size=env_int("TEXT_EMBED_RUN_LOG_QUEUE_SIZE", 10_000),
        )

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["data_root_win"] = str(self.data_root_win)
        payload["log_root_win"] = str(self.log_root_win)
        return payload


def resolve_data_root() -> Path:
    explicit = os.environ.get("TEXT_EMBED_GATEWAY_DATA_ROOT_WIN", "").strip()
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise RuntimeError(f"TEXT_EMBED_GATEWAY_DATA_ROOT_WIN does not exist: {path}")
    if is_workstation_host():
        if path_exists(WORKSTATION_DATA_ROOT_WIN):
            return WORKSTATION_DATA_ROOT_WIN
        raise RuntimeError("Workstation data root D:/market-data is not available.")
    if path_exists(WORKSTATION_SHARE_DATA_ROOT_WIN):
        return WORKSTATION_SHARE_DATA_ROOT_WIN
    raise RuntimeError("Workstation market-data root is not available; set TEXT_EMBED_GATEWAY_DATA_ROOT_WIN.")


def is_workstation_host() -> bool:
    return os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_COMPUTER_NAME


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def parse_bind(value: str) -> tuple[str, int]:
    text = value.strip()
    if ":" not in text:
        return text, 8798
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

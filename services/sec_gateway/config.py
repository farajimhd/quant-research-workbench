from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pipelines.sec.edgar.sec_pipeline.config import SecPipelineConfig, env_bool, env_float, env_int, env_string


WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")
WORKSTATION_CODE_ROOT_WIN = Path("D:/TradingML/codes/quant_research_workbench_pipelines")


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
    current_feed_count: int
    startup_auto_fill_max_gap_days: int
    auto_run_historical_on_workstation: bool
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
            poll_seconds=env_float("SEC_GATEWAY_POLL_SECONDS", 30.0),
            closed_poll_seconds=env_float("SEC_GATEWAY_CLOSED_POLL_SECONDS", 300.0),
            current_feed_count=env_int("SEC_GATEWAY_CURRENT_FEED_COUNT", 100),
            startup_auto_fill_max_gap_days=env_int("SEC_GATEWAY_STARTUP_AUTO_FILL_MAX_GAP_DAYS", 3),
            auto_run_historical_on_workstation=env_bool("SEC_GATEWAY_AUTO_RUN_HISTORICAL_ON_WORKSTATION", is_workstation_host()),
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
        if path.exists():
            return path
        raise RuntimeError(f"SEC_GATEWAY_DATA_ROOT_WIN does not exist: {path}")
    if is_workstation_host():
        if WORKSTATION_DATA_ROOT_WIN.exists():
            return WORKSTATION_DATA_ROOT_WIN
        raise RuntimeError("Workstation data root D:/market-data is not available.")
    if WORKSTATION_SHARE_DATA_ROOT_WIN.exists():
        return WORKSTATION_SHARE_DATA_ROOT_WIN
    raise RuntimeError("Workstation market-data root is not available. Start on workstation or mount the share.")


def is_workstation_host() -> bool:
    return os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_COMPUTER_NAME


def parse_bind(value: str) -> tuple[str, int]:
    text = value.strip()
    if ":" not in text:
        return text, 8797
    host, port_text = text.rsplit(":", 1)
    return host, int(port_text)


def env_bool_auto(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip() or value.strip().lower() == "auto":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

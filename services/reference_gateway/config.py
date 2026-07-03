from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_user


WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class ReferenceGatewayConfigOverrides:
    operator_mode: str | None = None
    run_mode: str | None = None
    integrity_mode: str | None = None
    maintenance_mode: str | None = None
    diagnostics_mode: str | None = None
    clickhouse_read_database: str | None = None
    clickhouse_write_database: str | None = None
    market_hours_write_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceGatewayConfig:
    operator_mode: str
    run_mode: str
    integrity_mode: str
    maintenance_mode: str
    diagnostics_mode: str
    bind: str
    host: str
    port: int
    data_root_win: Path
    prepared_root_win: Path
    report_root_win: Path
    is_workstation: bool
    execute: bool
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password_present: bool
    clickhouse_read_database: str
    clickhouse_write_database: str
    massive_base_url: str
    massive_api_key_present: bool
    ibkr_base_url: str
    preflight_enabled: bool
    active_ticker_max_pages: int
    active_ticker_page_limit: int
    active_ticker_new_candidate_limit: int
    after_hours_writes_only: bool
    market_hours_write_override: bool
    market_hours_write_reason: str
    write_discovered_issues: bool
    write_canonical_graph: bool
    immediate_tradability_block_enabled: bool
    resolve_stale_issues: bool
    rebuild_tradable_on_execute: bool
    rebuild_tradable_in_test_mode: bool
    daemon_loop_enabled: bool
    daemon_active_interval_seconds: float
    daemon_after_hours_interval_seconds: float
    market_publication_gap_fill_enabled: bool
    market_publication_gap_fill_days: int
    current_ticker_detail_insert_batch_size: int
    current_ticker_detail_request_min_interval_seconds: float
    current_ticker_detail_request_timeout_seconds: int
    current_ticker_detail_request_max_retries: int
    current_ticker_detail_request_retry_base_seconds: float
    current_ticker_detail_request_retry_max_seconds: float
    ibkr_borrow_snapshot_batch_size: int
    ibkr_borrow_insert_batch_size: int
    ibkr_borrow_request_min_interval_seconds: float
    ibkr_borrow_request_timeout_seconds: int
    terminal_rich_enabled: bool
    terminal_screen_enabled: bool
    terminal_refresh_seconds: float

    @classmethod
    def from_env(cls, overrides: ReferenceGatewayConfigOverrides | None = None) -> "ReferenceGatewayConfig":
        overrides = overrides or ReferenceGatewayConfigOverrides()
        bind = env_string("REFERENCE_GATEWAY_BIND", "127.0.0.1:8798")
        host, port = parse_bind(bind)
        data_root = resolve_data_root()
        prepared_root = Path(env_string("REFERENCE_GATEWAY_PREPARED_ROOT_WIN", str(data_root / "prepared")))
        password = default_clickhouse_password()
        legacy_database = env_string("REFERENCE_GATEWAY_CLICKHOUSE_DATABASE", "q_live")
        operator_mode = normalized_choice(overrides.operator_mode, env_string("REFERENCE_GATEWAY_MODE", "prod"), {"prod", "temp"}, "prod")
        run_mode = normalized_choice(overrides.run_mode, env_string("REFERENCE_GATEWAY_RUN", ""), {"daemon", "once"}, "daemon" if operator_mode == "prod" else "once")
        integrity_mode = normalized_choice(overrides.integrity_mode, env_string("REFERENCE_GATEWAY_INTEGRITY", "strict"), {"strict", "report-only"}, "strict")
        maintenance_mode = normalized_choice(overrides.maintenance_mode, env_string("REFERENCE_GATEWAY_MAINTENANCE", "auto"), {"auto", "skip", "force"}, "auto")
        diagnostics_mode = normalized_choice(overrides.diagnostics_mode, env_string("REFERENCE_GATEWAY_DIAGNOSTICS", "none"), {"none", "rules", "table-groups", "config"}, "none")
        default_read_database = "q_live"
        default_write_database = "q_reference_tmp" if operator_mode == "temp" else "q_live"
        if overrides.operator_mode is not None:
            read_database = string_override(overrides.clickhouse_read_database, default_read_database)
            write_database = string_override(overrides.clickhouse_write_database, default_write_database)
        else:
            read_database = string_override(
                overrides.clickhouse_read_database,
                env_string("REFERENCE_CLICKHOUSE_READ_DATABASE", env_string("REFERENCE_GATEWAY_READ_DATABASE", default_read_database or legacy_database)),
            )
            write_database = string_override(
                overrides.clickhouse_write_database,
                env_string(
                    "REFERENCE_CLICKHOUSE_WRITE_DATABASE",
                    env_string("REFERENCE_GATEWAY_WRITE_DATABASE", default_write_database or legacy_database),
                ),
            )
        maintenance_reason = string_override(overrides.market_hours_write_reason, env_string("REFERENCE_GATEWAY_MAINTENANCE_REASON", env_string("REFERENCE_GATEWAY_MARKET_HOURS_WRITE_REASON", "")))
        maintenance_force = maintenance_mode == "force"
        maintenance_skip = maintenance_mode == "skip"
        integrity_report_only = integrity_mode == "report-only"
        return cls(
            operator_mode=operator_mode,
            run_mode=run_mode,
            integrity_mode=integrity_mode,
            maintenance_mode=maintenance_mode,
            diagnostics_mode=diagnostics_mode,
            bind=bind,
            host=host,
            port=port,
            data_root_win=data_root,
            prepared_root_win=prepared_root,
            report_root_win=Path(env_string("REFERENCE_GATEWAY_REPORT_ROOT_WIN", str(prepared_root / "reference_gateway" / "reports"))),
            is_workstation=is_workstation_host(),
            execute=diagnostics_mode == "none",
            clickhouse_url=default_clickhouse_url(),
            clickhouse_user=default_clickhouse_user(),
            clickhouse_password_present=bool(password),
            clickhouse_read_database=read_database,
            clickhouse_write_database=write_database,
            massive_base_url=env_string("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/"),
            massive_api_key_present=bool(env_string("MASSIVE_API_KEY", "")),
            ibkr_base_url=env_string("IBKR_CPAPI_BASE_URL", "https://localhost:5000/v1/api").rstrip("/"),
            preflight_enabled=env_bool("REFERENCE_GATEWAY_PREFLIGHT_ENABLED", True),
            active_ticker_max_pages=env_int("REFERENCE_GATEWAY_ACTIVE_TICKER_MAX_PAGES", 1_000),
            active_ticker_page_limit=env_int("REFERENCE_GATEWAY_ACTIVE_TICKER_PAGE_LIMIT", 1_000),
            active_ticker_new_candidate_limit=env_int("REFERENCE_GATEWAY_ACTIVE_TICKER_NEW_CANDIDATE_LIMIT", 250),
            after_hours_writes_only=env_bool("REFERENCE_GATEWAY_AFTER_HOURS_WRITES_ONLY", True),
            market_hours_write_override=maintenance_force,
            market_hours_write_reason=maintenance_reason,
            write_discovered_issues=not integrity_report_only,
            write_canonical_graph=not integrity_report_only,
            immediate_tradability_block_enabled=not integrity_report_only,
            resolve_stale_issues=not integrity_report_only,
            rebuild_tradable_on_execute=not maintenance_skip,
            rebuild_tradable_in_test_mode=operator_mode == "temp" and maintenance_force,
            daemon_loop_enabled=run_mode == "daemon" and diagnostics_mode == "none",
            daemon_active_interval_seconds=env_float("REFERENCE_GATEWAY_DAEMON_ACTIVE_INTERVAL_SECONDS", 900.0),
            daemon_after_hours_interval_seconds=env_float("REFERENCE_GATEWAY_DAEMON_AFTER_HOURS_INTERVAL_SECONDS", 3600.0),
            market_publication_gap_fill_enabled=not maintenance_skip,
            market_publication_gap_fill_days=env_int("REFERENCE_GATEWAY_MARKET_PUBLICATION_GAP_FILL_DAYS", 14),
            current_ticker_detail_insert_batch_size=env_int("REFERENCE_GATEWAY_CURRENT_TICKER_DETAIL_INSERT_BATCH_SIZE", 50_000),
            current_ticker_detail_request_min_interval_seconds=env_float("REFERENCE_GATEWAY_CURRENT_TICKER_DETAIL_REQUEST_MIN_INTERVAL_SECONDS", 0.12),
            current_ticker_detail_request_timeout_seconds=env_int("REFERENCE_GATEWAY_CURRENT_TICKER_DETAIL_REQUEST_TIMEOUT_SECONDS", 60),
            current_ticker_detail_request_max_retries=env_int("REFERENCE_GATEWAY_CURRENT_TICKER_DETAIL_REQUEST_MAX_RETRIES", 5),
            current_ticker_detail_request_retry_base_seconds=env_float("REFERENCE_GATEWAY_CURRENT_TICKER_DETAIL_REQUEST_RETRY_BASE_SECONDS", 2.0),
            current_ticker_detail_request_retry_max_seconds=env_float("REFERENCE_GATEWAY_CURRENT_TICKER_DETAIL_REQUEST_RETRY_MAX_SECONDS", 120.0),
            ibkr_borrow_snapshot_batch_size=env_int("REFERENCE_GATEWAY_IBKR_BORROW_SNAPSHOT_BATCH_SIZE", 100),
            ibkr_borrow_insert_batch_size=env_int("REFERENCE_GATEWAY_IBKR_BORROW_INSERT_BATCH_SIZE", 50_000),
            ibkr_borrow_request_min_interval_seconds=env_float("REFERENCE_GATEWAY_IBKR_BORROW_REQUEST_MIN_INTERVAL_SECONDS", 0.12),
            ibkr_borrow_request_timeout_seconds=env_int("REFERENCE_GATEWAY_IBKR_BORROW_REQUEST_TIMEOUT_SECONDS", 60),
            terminal_rich_enabled=env_bool_auto("REFERENCE_GATEWAY_TERMINAL_RICH_ENABLED", sys.stdout.isatty()),
            terminal_screen_enabled=env_bool("REFERENCE_GATEWAY_TERMINAL_SCREEN_ENABLED", True),
            terminal_refresh_seconds=env_float("REFERENCE_GATEWAY_TERMINAL_REFRESH_SECONDS", 1.0),
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "service": {
                "operator_mode": self.operator_mode,
                "run_mode": self.run_mode,
                "bind": self.bind,
                "host": self.host,
                "port": self.port,
                "is_workstation": self.is_workstation,
                "data_root_win": str(self.data_root_win),
                "prepared_root_win": str(self.prepared_root_win),
                "report_root_win": str(self.report_root_win),
            },
            "database": {
                "clickhouse_url": self.clickhouse_url,
                "clickhouse_user": self.clickhouse_user,
                "clickhouse_password_present": self.clickhouse_password_present,
                "read_database": self.clickhouse_read_database,
                "write_database": self.clickhouse_write_database,
                "test_write_mode": self.test_write_mode,
            },
            "providers": {
                "massive_base_url": self.massive_base_url,
                "massive_api_key_present": self.massive_api_key_present,
                "ibkr_base_url": self.ibkr_base_url,
                "ibkr_required_for_source_sync": True,
            },
            "execution": {
                "execute": self.execute,
                "daemon_loop_enabled": self.daemon_loop_enabled,
                "diagnostics_mode": self.diagnostics_mode,
                "daemon_active_interval_seconds": self.daemon_active_interval_seconds,
                "daemon_after_hours_interval_seconds": self.daemon_after_hours_interval_seconds,
                "preflight_enabled": self.preflight_enabled,
            },
            "source_sync": {
                "enabled_for_operational_runs": True,
                "active_ticker_max_pages": self.active_ticker_max_pages,
                "active_ticker_page_limit": self.active_ticker_page_limit,
                "active_ticker_new_candidate_limit": self.active_ticker_new_candidate_limit,
                "write_canonical_graph": self.write_canonical_graph,
                "current_ticker_detail_insert_batch_size": self.current_ticker_detail_insert_batch_size,
                "current_ticker_detail_request_min_interval_seconds": self.current_ticker_detail_request_min_interval_seconds,
                "current_ticker_detail_request_timeout_seconds": self.current_ticker_detail_request_timeout_seconds,
                "current_ticker_detail_request_max_retries": self.current_ticker_detail_request_max_retries,
                "current_ticker_detail_request_retry_base_seconds": self.current_ticker_detail_request_retry_base_seconds,
                "current_ticker_detail_request_retry_max_seconds": self.current_ticker_detail_request_retry_max_seconds,
                "ibkr_borrow_snapshot_batch_size": self.ibkr_borrow_snapshot_batch_size,
                "ibkr_borrow_insert_batch_size": self.ibkr_borrow_insert_batch_size,
                "ibkr_borrow_request_min_interval_seconds": self.ibkr_borrow_request_min_interval_seconds,
                "ibkr_borrow_request_timeout_seconds": self.ibkr_borrow_request_timeout_seconds,
            },
            "integrity": {
                "mode": self.integrity_mode,
                "write_discovered_issues": self.write_discovered_issues,
                "immediate_tradability_block_enabled": self.immediate_tradability_block_enabled,
                "resolve_stale_issues": self.resolve_stale_issues,
            },
            "maintenance": {
                "mode": self.maintenance_mode,
                "after_hours_writes_only": self.after_hours_writes_only,
                "market_hours_write_override": self.market_hours_write_override,
                "market_hours_write_reason": self.market_hours_write_reason,
                "rebuild_tradable_on_execute": self.rebuild_tradable_on_execute,
                "rebuild_tradable_in_test_mode": self.rebuild_tradable_in_test_mode,
                "market_publication_gap_fill_enabled": self.market_publication_gap_fill_enabled,
                "market_publication_gap_fill_days": self.market_publication_gap_fill_days,
            },
            "terminal": {
                "rich_enabled": self.terminal_rich_enabled,
                "screen_enabled": self.terminal_screen_enabled,
                "refresh_seconds": self.terminal_refresh_seconds,
            },
        }

    @property
    def clickhouse_database(self) -> str:
        return self.clickhouse_read_database

    @property
    def test_write_mode(self) -> bool:
        return self.clickhouse_read_database != self.clickhouse_write_database


def resolve_data_root() -> Path:
    explicit = os.environ.get("REFERENCE_GATEWAY_DATA_ROOT_WIN", "").strip()
    if explicit:
        path = Path(explicit)
        if path_exists(path):
            return path
        raise RuntimeError(f"REFERENCE_GATEWAY_DATA_ROOT_WIN does not exist: {path}")
    if is_workstation_host():
        if path_exists(WORKSTATION_DATA_ROOT_WIN):
            return WORKSTATION_DATA_ROOT_WIN
        raise RuntimeError("Workstation data root D:/market-data is not available.")
    if path_exists(WORKSTATION_SHARE_DATA_ROOT_WIN):
        return WORKSTATION_SHARE_DATA_ROOT_WIN
    raise RuntimeError("Workstation market-data root is not available. Start on workstation or mount the share.")


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


def default_clickhouse_url() -> str:
    return first_env(
        "REFERENCE_CLICKHOUSE_URL",
        "QLIVE_MIGRATION_CLICKHOUSE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "QMD_CLICKHOUSE_URL",
        "CLICKHOUSE_URL",
        "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL",
        default="http://localhost:8123",
    ).rstrip("/")


def env_string(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def string_override(value: str | None, fallback: str) -> str:
    return value.strip() if value is not None and value.strip() else fallback


def normalized_choice(value: str | None, fallback: str, choices: set[str], default: str) -> str:
    candidate = (value or fallback or default).strip().lower().replace("_", "-")
    return candidate if candidate in choices else default


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

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_user


WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class ReferenceGatewayConfig:
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
    clickhouse_database: str
    source_massive_enabled: bool
    ibkr_resolution_enabled: bool
    after_hours_writes_only: bool
    market_hours_write_override: bool
    market_hours_write_reason: str
    terminal_rich_enabled: bool
    terminal_refresh_seconds: float

    @classmethod
    def from_env(cls) -> "ReferenceGatewayConfig":
        bind = env_string("REFERENCE_GATEWAY_BIND", "127.0.0.1:8798")
        host, port = parse_bind(bind)
        data_root = resolve_data_root()
        prepared_root = Path(env_string("REFERENCE_GATEWAY_PREPARED_ROOT_WIN", str(data_root / "prepared")))
        password = default_clickhouse_password()
        return cls(
            bind=bind,
            host=host,
            port=port,
            data_root_win=data_root,
            prepared_root_win=prepared_root,
            report_root_win=Path(env_string("REFERENCE_GATEWAY_REPORT_ROOT_WIN", str(prepared_root / "reference_gateway" / "reports"))),
            is_workstation=is_workstation_host(),
            execute=env_bool("REFERENCE_GATEWAY_EXECUTE", False),
            clickhouse_url=default_clickhouse_url(),
            clickhouse_user=default_clickhouse_user(),
            clickhouse_password_present=bool(password),
            clickhouse_database=env_string("REFERENCE_GATEWAY_CLICKHOUSE_DATABASE", "q_live"),
            source_massive_enabled=env_bool("REFERENCE_GATEWAY_MASSIVE_ENABLED", True),
            ibkr_resolution_enabled=env_bool("REFERENCE_GATEWAY_IBKR_RESOLUTION_ENABLED", False),
            after_hours_writes_only=env_bool("REFERENCE_GATEWAY_AFTER_HOURS_WRITES_ONLY", True),
            market_hours_write_override=env_bool("REFERENCE_GATEWAY_MARKET_HOURS_WRITE_OVERRIDE", False),
            market_hours_write_reason=env_string("REFERENCE_GATEWAY_MARKET_HOURS_WRITE_REASON", ""),
            terminal_rich_enabled=env_bool_auto("REFERENCE_GATEWAY_TERMINAL_RICH_ENABLED", sys.stdout.isatty()),
            terminal_refresh_seconds=env_float("REFERENCE_GATEWAY_TERMINAL_REFRESH_SECONDS", 1.0),
        )

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["data_root_win"] = str(self.data_root_win)
        payload["prepared_root_win"] = str(self.prepared_root_win)
        payload["report_root_win"] = str(self.report_root_win)
        return payload


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

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user


DEFAULT_READ_DATABASE = "q_live"
DEFAULT_WRITE_DATABASE = "q_live"
DEFAULT_COVERAGE_TABLE = "sec_coverage_manifest_v1"
DEFAULT_FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom&count=100"
DEFAULT_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class SecClickHouseConfig:
    url: str
    user: str
    password: str
    read_database: str = DEFAULT_READ_DATABASE
    write_database: str = DEFAULT_WRITE_DATABASE
    coverage_table: str = DEFAULT_COVERAGE_TABLE

    @classmethod
    def from_env(cls) -> "SecClickHouseConfig":
        legacy_database = env_string("SEC_CLICKHOUSE_DATABASE", DEFAULT_READ_DATABASE)
        return cls(
            url=default_clickhouse_url(),
            user=default_clickhouse_user(),
            password=default_clickhouse_password(),
            read_database=env_string("SEC_CLICKHOUSE_READ_DATABASE", legacy_database),
            write_database=env_string("SEC_CLICKHOUSE_WRITE_DATABASE", env_string("SEC_GATEWAY_WRITE_DATABASE", DEFAULT_WRITE_DATABASE)),
            coverage_table=env_string("SEC_COVERAGE_TABLE", DEFAULT_COVERAGE_TABLE),
        )


@dataclass(frozen=True, slots=True)
class SecPipelineConfig:
    clickhouse: SecClickHouseConfig
    sec_user_agent: str
    feed_url: str
    data_root_win: Path
    artifact_root_win: Path
    prepared_root_win: Path
    raw_live_root_win: Path
    historical_output_root_win: Path
    workstation_code_root_win: Path
    workstation_conda_env: str
    request_min_interval_seconds: float = 0.12
    request_timeout_seconds: float = 30.0
    request_transient_error_cooldown_seconds: float = 60.0
    request_rate_limit_cooldown_seconds: float = 300.0

    @classmethod
    def from_env(cls) -> "SecPipelineConfig":
        data_root = resolve_data_root()
        prepared_root = Path(env_string("SEC_PREPARED_ROOT_WIN", str(data_root / "prepared")))
        artifact_root = Path(env_string("SEC_CORE_ARTIFACT_ROOT_WIN", str(data_root / "sec_core")))
        return cls(
            clickhouse=SecClickHouseConfig.from_env(),
            sec_user_agent=sec_user_agent(),
            feed_url=env_string("SEC_CURRENT_FEED_URL", DEFAULT_FEED_URL),
            data_root_win=data_root,
            artifact_root_win=artifact_root,
            prepared_root_win=prepared_root,
            raw_live_root_win=Path(env_string("SEC_LIVE_RAW_ROOT_WIN", str(data_root / "sec-edgar" / "live-raw"))),
            historical_output_root_win=Path(
                env_string("SEC_HISTORICAL_ORCHESTRATOR_OUTPUT_ROOT_WIN", str(prepared_root / "sec_historical_backfill_orchestrator"))
            ),
            workstation_code_root_win=Path(env_string("SEC_GATEWAY_WORKSTATION_CODE_ROOT_WIN", "D:/TradingML/codes/quant_research_workbench_pipelines")),
            workstation_conda_env=env_string("SEC_GATEWAY_WORKSTATION_CONDA_ENV", "ml4t"),
            request_min_interval_seconds=env_float("SEC_REQUEST_MIN_INTERVAL_SECONDS", 0.12),
            request_timeout_seconds=env_float("SEC_REQUEST_TIMEOUT_SECONDS", 30.0),
            request_transient_error_cooldown_seconds=env_float("SEC_REQUEST_TRANSIENT_ERROR_COOLDOWN_SECONDS", 60.0),
            request_rate_limit_cooldown_seconds=env_float("SEC_REQUEST_RATE_LIMIT_COOLDOWN_SECONDS", 300.0),
        )

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["clickhouse"]["password"] = "present" if self.clickhouse.password else "missing"
        for key in ("data_root_win", "artifact_root_win", "prepared_root_win", "raw_live_root_win", "historical_output_root_win", "workstation_code_root_win"):
            payload[key] = str(getattr(self, key))
        return payload


def env_string(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_string(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_string(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def sec_user_agent() -> str:
    return (
        env_string("SEC_USER_AGENT", "")
        or env_string("SEC_EDGAR_USER_AGENT", "")
        or env_string("NEWS_SEC_USER_AGENT", "")
        or "QuantResearchWorkbench SEC gateway contact@example.com"
    )


def resolve_data_root() -> Path:
    explicit = env_string("SEC_GATEWAY_DATA_ROOT_WIN", env_string("SEC_DATA_ROOT_WIN", ""))
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise RuntimeError(f"configured SEC data root does not exist: {path}")
    if os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_COMPUTER_NAME:
        if DEFAULT_DATA_ROOT_WIN.exists():
            return DEFAULT_DATA_ROOT_WIN
        raise RuntimeError("Workstation SEC data root D:/market-data is not available.")
    if WORKSTATION_SHARE_DATA_ROOT_WIN.exists():
        return WORKSTATION_SHARE_DATA_ROOT_WIN
    raise RuntimeError(
        "Workstation market-data root is not available. Start on the workstation, mount "
        "\\\\DESKTOP-SAAI85T\\Workstation-D\\market-data, or set SEC_GATEWAY_DATA_ROOT_WIN."
    )

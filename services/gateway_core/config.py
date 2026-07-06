"""Shared configuration shapes for service gateways.

These dataclasses are intentionally small. Existing services keep their
service-specific config classes, but can project values into these structures
when rendering standard dashboards, logs, or run manifests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServiceIdentityConfig:
    service_name: str
    bind: str = "127.0.0.1:0"
    mode: str = "prod"
    execute: bool = True
    test_write_mode: bool = False


@dataclass(frozen=True)
class ClickHouseConfig:
    url: str
    read_database: str
    write_database: str
    user_present: bool = False
    password_present: bool = False


@dataclass(frozen=True)
class StorageConfig:
    artifact_root: str
    raw_subdir: str = "raw"
    prepared_subdir: str = "prepared"
    runtime_subdir: str = "runtime"


@dataclass(frozen=True)
class ScheduleConfig:
    poll_seconds_market_open: float
    poll_seconds_market_closed: float
    active_collection_window: str = "04:00-20:00 ET"
    maintenance_window: str = "outside active collection window"


@dataclass(frozen=True)
class CoverageConfig:
    enabled: bool = True
    manifest_table: str = ""
    bootstrap_chunk_seconds: int = 3600
    manual_backfill_threshold_days: float = 30.0


@dataclass(frozen=True)
class BackfillConfig:
    enabled: bool = True
    max_inline_gap_days: float = 30.0
    workstation_required_after_days: float = 30.0
    generated_script_root: str = ""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    endpoint: str = ""
    enabled: bool = True
    rate_limit_per_second: float | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool = True
    refresh_seconds: float = 1.0
    recent_rows: int = 12
    compact_height_rows: int = 42


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool = True
    fail_on_error: bool = True
    warn_on_stale: bool = True


@dataclass(frozen=True)
class ErrorPolicyConfig:
    max_retry_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    critical_error_labels: tuple[str, ...] = ("schema", "auth", "preflight")


@dataclass(frozen=True)
class GroupedGatewayConfig:
    identity: ServiceIdentityConfig
    clickhouse: ClickHouseConfig | None = None
    storage: StorageConfig | None = None
    schedule: ScheduleConfig | None = None
    coverage: CoverageConfig | None = None
    backfill: BackfillConfig | None = None
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    error_policy: ErrorPolicyConfig = field(default_factory=ErrorPolicyConfig)
    providers: tuple[ProviderConfig, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict with secrets represented only by presence."""

        return asdict(self)

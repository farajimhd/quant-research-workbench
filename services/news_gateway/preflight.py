from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from pipelines.news.benzinga.core.coverage_manifest import CoverageManifestConfig, ensure_coverage_manifest_table
from pipelines.news.benzinga.core.clickhouse_writer import NewsWriteConfig, validate_target_tables
from pipelines.news.benzinga.news_pipeline.provider import BenzingaProviderClient, BenzingaProviderConfig
from research.mlops.clickhouse import ClickHouseHttpClient
from services.news_gateway.config import NewsGatewayConfig
from services.gateway_core.market_calendar import MassiveMarketHoursClient


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: str
    wall_seconds: float
    message: str = ""


@dataclass(frozen=True, slots=True)
class PreflightReport:
    status: str
    checked_at_utc: str
    checks: list[PreflightCheck] = field(default_factory=list)

    def public_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checked_at_utc": self.checked_at_utc,
            "checks": [asdict(check) for check in self.checks],
        }


class PreflightError(RuntimeError):
    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        failed = [check for check in report.checks if check.status != "ok"]
        summary = "; ".join(f"{check.name}: {check.message}" for check in failed)
        super().__init__(f"News gateway preflight failed: {summary}")


def run_preflight(config: NewsGatewayConfig, *, clickhouse_password: str, api_key: str) -> PreflightReport:
    checks: list[PreflightCheck] = []
    checks.append(timed_check("configuration", lambda: check_configuration(config, clickhouse_password, api_key)))
    checks.append(timed_check("artifact_storage", lambda: check_artifact_storage(config)))
    checks.append(timed_check("clickhouse", lambda: check_clickhouse(config, clickhouse_password)))
    checks.append(timed_check("benzinga_provider", lambda: check_benzinga_provider(config, api_key)))
    if config.market_status_enabled:
        checks.append(timed_check("market_status", lambda: check_market_status(config, api_key)))
    status = "ok" if all(check.status == "ok" for check in checks) else "failed"
    report = PreflightReport(
        status=status,
        checked_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        checks=checks,
    )
    if status != "ok":
        raise PreflightError(report)
    return report


def timed_check(name: str, fn: Callable[[], str]) -> PreflightCheck:
    started = time.perf_counter()
    try:
        message = fn()
        return PreflightCheck(name=name, status="ok", wall_seconds=time.perf_counter() - started, message=message)
    except Exception as exc:  # noqa: BLE001
        return PreflightCheck(name=name, status="failed", wall_seconds=time.perf_counter() - started, message=repr(exc))


def check_configuration(config: NewsGatewayConfig, clickhouse_password: str, api_key: str) -> str:
    missing: list[str] = []
    if not api_key:
        missing.append("MASSIVE_API_KEY")
    if not config.clickhouse_url:
        missing.append("ClickHouse URL")
    if not config.clickhouse_user:
        missing.append("ClickHouse user")
    if not clickhouse_password:
        missing.append("ClickHouse password")
    if missing:
        raise RuntimeError(f"missing required configuration: {missing}")
    return (
        f"bind={config.bind} execute={config.execute} "
        f"clickhouse={config.clickhouse_url} user={config.clickhouse_user}"
    )


def check_artifact_storage(config: NewsGatewayConfig) -> str:
    checked_paths = [config.raw_root_win, config.prepared_root_win]
    for path in checked_paths:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise RuntimeError(f"path is not a directory: {path}")
        probe = path / f".news_gateway_preflight_{os.getpid()}.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    return "writable=" + ",".join(str(path) for path in checked_paths)


def check_clickhouse(config: NewsGatewayConfig, clickhouse_password: str) -> str:
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, clickhouse_password)
    client.execute("SELECT 1")
    ensure_coverage_manifest_table(
        client,
        CoverageManifestConfig(
            database=config.clickhouse_database,
            coverage_table=config.coverage_table,
            normalized_table=config.normalized_table,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        ),
    )
    validate_target_tables(
        client,
        NewsWriteConfig(
            database=config.clickhouse_database,
            normalized_table=config.normalized_table,
            ticker_table=config.ticker_table,
        ),
    )
    return (
        f"tables={config.clickhouse_database}.{config.normalized_table},"
        f"{config.clickhouse_database}.{config.ticker_table},"
        f"{config.clickhouse_database}.{config.coverage_table}"
    )


def check_benzinga_provider(config: NewsGatewayConfig, api_key: str) -> str:
    provider = BenzingaProviderClient(
        BenzingaProviderConfig(
            endpoint_url=config.benzinga_url,
            api_key=api_key,
            page_limit=1,
            max_pages=1,
        )
    )
    end_utc = datetime.now(UTC)
    start_utc = end_utc - timedelta(seconds=1)
    result = provider.fetch_window(start_utc, end_utc)
    return f"reachable pages={result.pages} rows={len(result.items)}"


def check_market_status(config: NewsGatewayConfig, api_key: str) -> str:
    result = MassiveMarketHoursClient.from_env(
        service_prefix="NEWS",
        api_key=api_key,
        status_url=config.market_status_url,
        holidays_url=config.market_holidays_url,
        enabled=config.market_status_enabled,
        refresh_seconds=config.market_status_refresh_seconds,
    ).snapshot(force=True)
    return (
        f"session={result.session} active={result.active_collection_window} "
        f"source={result.source} reason={result.reason} market={result.market or '-'} "
        f"earlyHours={result.early_hours} afterHours={result.after_hours} "
        f"holiday={result.holiday_status or '-'} serverTime={result.server_time or '-'}"
    )

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Callable

from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import SecClickHouseWriter
from pipelines.sec.edgar.sec_pipeline.coverage import SecCoverageConfig, ensure_coverage_table
from pipelines.sec.edgar.sec_pipeline.feed import SecCurrentFeedClient
from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient
from pipelines.sec.edgar.sec_pipeline.rate_limit import SecRateLimiter
from research.mlops.clickhouse import ClickHouseHttpClient
from services.sec_gateway.config import SecGatewayConfig


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
        return {"status": self.status, "checked_at_utc": self.checked_at_utc, "checks": [asdict(check) for check in self.checks]}


class PreflightError(RuntimeError):
    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        failed = [check for check in report.checks if check.status != "ok"]
        super().__init__("SEC gateway preflight failed: " + "; ".join(f"{check.name}: {check.message}" for check in failed))


def run_preflight(config: SecGatewayConfig) -> PreflightReport:
    checks = [
        timed_check("configuration", lambda: check_configuration(config)),
        timed_check("artifact_storage", lambda: check_artifact_storage(config)),
        timed_check("clickhouse", lambda: check_clickhouse(config)),
        timed_check("sec_feed", lambda: check_sec_feed(config)),
    ]
    status = "ok" if all(check.status == "ok" for check in checks) else "failed"
    report = PreflightReport(status=status, checked_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"), checks=checks)
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


def check_configuration(config: SecGatewayConfig) -> str:
    missing = []
    if not config.pipeline.sec_user_agent:
        missing.append("SEC_USER_AGENT")
    if not config.pipeline.clickhouse.url:
        missing.append("ClickHouse URL")
    if not config.pipeline.clickhouse.user:
        missing.append("ClickHouse user")
    if not config.pipeline.clickhouse.password:
        missing.append("ClickHouse password")
    if missing:
        raise RuntimeError(f"missing required configuration: {missing}")
    return f"bind={config.bind} execute={config.execute} db={config.pipeline.clickhouse.database}"


def check_artifact_storage(config: SecGatewayConfig) -> str:
    paths = [config.pipeline.raw_live_root_win, config.pipeline.prepared_root_win]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".sec_gateway_preflight_{os.getpid()}.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    return "writable=" + ",".join(str(path) for path in paths)


def check_clickhouse(config: SecGatewayConfig) -> str:
    ch = config.pipeline.clickhouse
    client = ClickHouseHttpClient(ch.url, ch.user, ch.password)
    client.execute("SELECT 1")
    ensure_coverage_table(
        client,
        SecCoverageConfig(database=ch.database, coverage_table=ch.coverage_table, storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or ""),
    )
    SecClickHouseWriter(client, database=ch.database).validate_tables()
    return f"tables={ch.database}.sec_filing_v2,sec_filing_document_v2,sec_filing_text_v2,{ch.coverage_table}"


def check_sec_feed(config: SecGatewayConfig) -> str:
    limiter = SecRateLimiter(config.pipeline.request_min_interval_seconds)
    http = SecHttpClient(user_agent=config.pipeline.sec_user_agent, rate_limiter=limiter, timeout_seconds=config.pipeline.request_timeout_seconds)
    feed = SecCurrentFeedClient(feed_url=config.pipeline.feed_url, http=http)
    rows = feed.fetch()
    return f"feed_reachable rows={len(rows)}"

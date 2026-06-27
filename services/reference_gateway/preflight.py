from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.providers import IbkrReferenceClient, MassiveReferenceClient
from services.reference_gateway.runtime_log import RuntimeLogger


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: str
    seconds: float
    details: str


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    checks: list[PreflightCheck] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_preflight(config: ReferenceGatewayConfig, *, require_source_sync_dependencies: bool, logger: RuntimeLogger | None = None) -> PreflightResult:
    checks = [
        check_artifact_storage(config),
        check_clickhouse(config),
    ]
    if require_source_sync_dependencies:
        checks.append(check_massive(config))
        checks.append(check_ibkr(config))
    status = "ok" if all(check.status == "ok" for check in checks) else "failed"
    result = PreflightResult(status=status, checks=checks)
    if logger is not None:
        logger.event("preflight_completed", **result.public_dict())
    return result


def check_artifact_storage(config: ReferenceGatewayConfig) -> PreflightCheck:
    started = time.perf_counter()
    try:
        root = Path(config.report_root_win)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".reference_gateway_write_probe"
        probe.write_text("ok", encoding="ascii")
        probe.unlink(missing_ok=True)
        return PreflightCheck("artifact_storage", "ok", time.perf_counter() - started, str(root))
    except Exception as exc:  # noqa: BLE001
        return PreflightCheck("artifact_storage", "failed", time.perf_counter() - started, repr(exc))


def check_clickhouse(config: ReferenceGatewayConfig) -> PreflightCheck:
    started = time.perf_counter()
    try:
        client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
        value = client.query_tsv("SELECT 1").strip()
        if value != "1":
            raise RuntimeError(f"unexpected SELECT 1 response: {value!r}")
        return PreflightCheck("clickhouse", "ok", time.perf_counter() - started, f"{config.clickhouse_url} read={config.clickhouse_read_database} write={config.clickhouse_write_database}")
    except Exception as exc:  # noqa: BLE001
        return PreflightCheck("clickhouse", "failed", time.perf_counter() - started, repr(exc))


def check_massive(config: ReferenceGatewayConfig) -> PreflightCheck:
    started = time.perf_counter()
    try:
        client = MassiveReferenceClient(base_url=config.massive_base_url, api_key=_massive_api_key(), page_limit=1, max_pages=1)
        result = client.fetch_active_us_stock_tickers()
        return PreflightCheck("massive", "ok", time.perf_counter() - started, f"reachable pages={result.pages} rows={len(result.tickers)}")
    except Exception as exc:  # noqa: BLE001
        return PreflightCheck("massive", "failed", time.perf_counter() - started, repr(exc))


def check_ibkr(config: ReferenceGatewayConfig) -> PreflightCheck:
    started = time.perf_counter()
    try:
        status = IbkrReferenceClient(base_url=config.ibkr_base_url).auth_status()
        authenticated = bool(status.get("authenticated")) if "authenticated" in status else bool(status.get("connected"))
        details = json.dumps(status, sort_keys=True, default=str)[:800]
        if not authenticated:
            raise RuntimeError("IBKR Client Portal is reachable but not authenticated: " + details)
        return PreflightCheck("ibkr_client_portal", "ok", time.perf_counter() - started, details)
    except Exception as exc:  # noqa: BLE001
        return PreflightCheck("ibkr_client_portal", "failed", time.perf_counter() - started, repr(exc))


def _massive_api_key() -> str:
    import os

    return os.environ.get("MASSIVE_API_KEY", "").strip()

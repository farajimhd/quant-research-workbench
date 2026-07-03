from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import ensure_market_publication_schema

from pipelines.reference_data.market_publications_historical_gap_fill import (
    load_symbol_refs,
    run_ibkr_borrow_availability,
)


ProgressCallback = Callable[[str, str, str, int | None], None]


@dataclass(frozen=True, slots=True)
class IbkrBorrowSyncResult:
    attempted: bool
    status: str
    eligible: int = 0
    written: int = 0
    failed: int = 0
    wall_seconds: float = 0.0
    details: dict[str, object] = field(default_factory=dict)


def run_startup_ibkr_borrow_sync(
    config: ReferenceGatewayConfig,
    *,
    on_progress: ProgressCallback | None = None,
) -> IbkrBorrowSyncResult:
    started = time.perf_counter()

    def progress(status: str, message: str, rows: int | None = None) -> None:
        if on_progress is not None:
            on_progress("ibkr_borrow_availability", status, message, rows)

    if not config.execute:
        return IbkrBorrowSyncResult(False, "skipped", wall_seconds=0.0, details={"reason": "diagnostic_mode"})

    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    progress("running", "Ensuring borrow target table exists.", None)
    ensure_market_publication_schema(
        client,
        database=config.clickhouse_write_database,
        read_database=config.clickhouse_read_database,
        storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
    )

    progress("running", "Loading active US stock listings with IBKR conids.", None)
    symbols = load_symbol_refs(client, config.clickhouse_read_database)
    eligible = sum(1 for ref in symbols.values() if ref.ibkr_conid.strip().isdigit())
    if eligible == 0:
        return IbkrBorrowSyncResult(
            True,
            "covered_empty",
            eligible=0,
            written=0,
            failed=0,
            wall_seconds=time.perf_counter() - started,
            details={"reason": "no_active_us_stock_ibkr_conids"},
        )

    today = datetime.now(UTC).date()
    run_id = "reference_gateway_ibkr_borrow_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    args = argparse.Namespace(
        write_database=config.clickhouse_write_database,
        execute=True,
        batch_size=config.ibkr_borrow_insert_batch_size,
        resume_from_coverage=False,
        request_min_interval_seconds=config.ibkr_borrow_request_min_interval_seconds,
        request_timeout_seconds=config.ibkr_borrow_request_timeout_seconds,
        ibkr_borrow_batch_size=config.ibkr_borrow_snapshot_batch_size,
    )

    progress("running", f"Requesting IBKR borrow snapshot for {eligible:,} active US stock conids.", eligible)
    results = run_ibkr_borrow_availability(
        client,
        args,
        run_id,
        today,
        today + timedelta(days=1),
        symbols,
        on_progress=lambda line: progress("running", line, None),
    )
    result = results[0] if results else None
    if result is None:
        return IbkrBorrowSyncResult(
            True,
            "completed",
            eligible=eligible,
            written=0,
            failed=0,
            wall_seconds=time.perf_counter() - started,
            details={"reason": "no_ibkr_borrow_gap_reported"},
        )
    return IbkrBorrowSyncResult(
        True,
        result.status,
        eligible=result.rows_read,
        written=result.rows_written,
        failed=result.rows_failed,
        wall_seconds=time.perf_counter() - started,
        details=result.details,
    )

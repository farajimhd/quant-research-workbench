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
    run_massive_ticker_details,
)


ProgressCallback = Callable[[str, str, str, int | None], None]


@dataclass(frozen=True, slots=True)
class CurrentTickerDetailSyncResult:
    attempted: bool
    status: str
    requested: int = 0
    matched: int = 0
    written: int = 0
    failed: int = 0
    wall_seconds: float = 0.0
    details: dict[str, object] = field(default_factory=dict)


def run_current_ticker_detail_sync(
    config: ReferenceGatewayConfig,
    *,
    tickers: list[str],
    on_progress: ProgressCallback | None = None,
) -> CurrentTickerDetailSyncResult:
    started = time.perf_counter()

    def progress(status: str, message: str, rows: int | None = None) -> None:
        if on_progress is not None:
            on_progress("massive_ticker_details", status, message, rows)

    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not normalized_tickers:
        return CurrentTickerDetailSyncResult(False, "skipped", wall_seconds=0.0, details={"reason": "no_new_accepted_tickers"})
    if not config.execute:
        return CurrentTickerDetailSyncResult(False, "skipped", requested=len(normalized_tickers), wall_seconds=0.0, details={"reason": "diagnostic_mode"})

    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    progress("running", "Ensuring market snapshot and float target tables exist.", None)
    ensure_market_publication_schema(
        client,
        database=config.clickhouse_write_database,
        read_database=config.clickhouse_read_database,
        storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
    )

    refs_by_ticker = load_symbol_refs(client, config.clickhouse_write_database)
    selected_refs = {ticker: refs_by_ticker[ticker] for ticker in normalized_tickers if ticker in refs_by_ticker}
    if not selected_refs:
        return CurrentTickerDetailSyncResult(
            True,
            "failed",
            requested=len(normalized_tickers),
            matched=0,
            wall_seconds=time.perf_counter() - started,
            details={"reason": "accepted_tickers_not_visible_in_write_database", "tickers": normalized_tickers[:50]},
        )

    today = datetime.now(UTC).date()
    run_id = "reference_gateway_current_ticker_details_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    args = argparse.Namespace(
        write_database=config.clickhouse_write_database,
        execute=True,
        batch_size=config.current_ticker_detail_insert_batch_size,
        resume_from_coverage=False,
        request_min_interval_seconds=config.current_ticker_detail_request_min_interval_seconds,
        request_timeout_seconds=config.current_ticker_detail_request_timeout_seconds,
        request_max_retries=config.current_ticker_detail_request_max_retries,
        request_retry_base_seconds=config.current_ticker_detail_request_retry_base_seconds,
        request_retry_max_seconds=config.current_ticker_detail_request_retry_max_seconds,
        user_agent="quant-reference-gateway-current-details/1.0",
        write_coverage=False,
    )

    progress("running", f"Refreshing Massive ticker details for {len(selected_refs):,} newly accepted ticker(s).", len(selected_refs))
    results = run_massive_ticker_details(
        client,
        args,
        run_id,
        today,
        today + timedelta(days=1),
        selected_refs,
        on_progress=lambda line: progress("running", line, None),
    )
    result = results[0] if results else None
    if result is None:
        return CurrentTickerDetailSyncResult(
            True,
            "completed",
            requested=len(normalized_tickers),
            matched=len(selected_refs),
            wall_seconds=time.perf_counter() - started,
            details={"reason": "no_current_detail_result"},
        )
    return CurrentTickerDetailSyncResult(
        True,
        result.status,
        requested=len(normalized_tickers),
        matched=len(selected_refs),
        written=result.rows_written,
        failed=result.rows_failed,
        wall_seconds=time.perf_counter() - started,
        details=result.details,
    )

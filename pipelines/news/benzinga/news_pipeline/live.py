from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig
from pipelines.news.benzinga.news_pipeline.pipeline import BenzingaNewsPipeline, ProcessedNewsItem
from pipelines.news.benzinga.news_pipeline.provider import BenzingaFetchResult, BenzingaProviderClient


@dataclass(frozen=True, slots=True)
class LiveIngestCycleSummary:
    status: str
    start_utc: str
    end_utc: str
    provider_rows: int
    processed_rows: int
    failed_rows: int
    ticker_rows: int
    fetch_tasks: int
    pages: int
    saturated: bool
    execute: bool
    write_summary: dict[str, Any]


def run_live_ingest_cycle(
    *,
    lookback_minutes: int,
    execute: bool,
    limit_items: int | None,
    pipeline_config: BenzingaPipelineConfig,
    clickhouse_target: ClickHouseTargetConfig,
    provider: BenzingaProviderClient,
    skip_existing: bool = True,
) -> LiveIngestCycleSummary:
    end_utc = datetime.now(UTC)
    start_utc = end_utc - timedelta(minutes=max(1, lookback_minutes))
    if limit_items == 0:
        fetch_result = BenzingaFetchResult(items=[], pages=0, saturated=False, next_url="")
    else:
        fetch_result = provider.fetch_window(start_utc, end_utc)
    items = fetch_result.items[:limit_items] if limit_items is not None else fetch_result.items
    pipeline = BenzingaNewsPipeline(pipeline_config)
    processed: list[ProcessedNewsItem] = []
    failed = 0
    ticker_rows = 0
    fetch_tasks = 0
    for item in items:
        try:
            value = pipeline.process_payload(item, downloaded_at_utc=end_utc)
            processed.append(value)
            ticker_rows += len(value.result.ticker_links)
            fetch_tasks += len(value.result.url_resolution.fetch_tasks)
        except Exception:  # noqa: BLE001
            failed += 1
    write_summary = pipeline.write_many(
        processed,
        target=clickhouse_target,
        execute=execute,
        skip_existing=skip_existing,
    )
    return LiveIngestCycleSummary(
        status="ok" if failed == 0 else "completed_with_errors",
        start_utc=start_utc.isoformat().replace("+00:00", "Z"),
        end_utc=end_utc.isoformat().replace("+00:00", "Z"),
        provider_rows=len(fetch_result.items),
        processed_rows=len(processed),
        failed_rows=failed,
        ticker_rows=ticker_rows,
        fetch_tasks=fetch_tasks,
        pages=fetch_result.pages,
        saturated=fetch_result.saturated,
        execute=execute,
        write_summary=asdict(write_summary),
    )

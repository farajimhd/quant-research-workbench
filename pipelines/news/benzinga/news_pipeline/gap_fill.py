from __future__ import annotations

import concurrent.futures
import json
import traceback
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from pipelines.news.benzinga.core.contracts import NewsPipelineResult, UrlResolution
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig
from pipelines.news.benzinga.news_pipeline.pipeline import BenzingaNewsPipeline, ProcessedNewsItem


@dataclass(frozen=True, slots=True)
class GapFillFileResult:
    status: str
    raw_json_path: str
    provider_article_id: str = ""
    canonical_news_id: str = ""
    ticker_count: int = 0
    fetch_task_count: int = 0
    warning_count: int = 0
    exception: str = ""
    traceback: str = ""
    result: NewsPipelineResult | None = None


@dataclass(frozen=True, slots=True)
class GapFillSummary:
    status: str
    files_seen: int
    files_processed: int
    files_failed: int
    normalized_rows: int
    ticker_rows: int
    fetch_tasks: int
    batches_written: int
    skipped_existing: int
    execute: bool
    output_jsonl: str
    error_jsonl: str


def discover_raw_files(raw_root: Path, start_utc: datetime | None = None, end_utc: datetime | None = None) -> list[Path]:
    candidates: list[Path] = []
    if start_utc and end_utc:
        for day in date_range_days(start_utc, end_utc):
            day_root = raw_root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
            if day_root.exists():
                candidates.extend(day_root.glob("*.json"))
    else:
        candidates.extend(raw_root.rglob("*.json"))
    return sorted(path for path in candidates if path.is_file())


def run_raw_file_gap_fill(
    *,
    raw_files: list[Path],
    pipeline_config: BenzingaPipelineConfig,
    clickhouse_target: ClickHouseTargetConfig,
    output_jsonl: Path,
    error_jsonl: Path,
    processes: int,
    batch_size: int,
    execute: bool,
    skip_existing: bool,
    skip_table_validation: bool,
    progress_interval: int,
) -> GapFillSummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    error_jsonl.parent.mkdir(parents=True, exist_ok=True)
    total = len(raw_files)
    processed = 0
    failed = 0
    normalized_rows = 0
    ticker_rows = 0
    fetch_tasks = 0
    batches_written = 0
    skipped_existing = 0
    pending_batch: list[ProcessedNewsItem] = []
    writer_pipeline = BenzingaNewsPipeline(pipeline_config)

    with output_jsonl.open("w", encoding="utf-8") as result_handle, error_jsonl.open("w", encoding="utf-8") as error_handle:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, processes)) as pool:
            future_to_path = {
                pool.submit(process_raw_file_worker, str(path), asdict(pipeline_config)): path
                for path in raw_files
            }
            for future in concurrent.futures.as_completed(future_to_path):
                outcome = future.result()
                if outcome.status == "ok" and outcome.result is not None:
                    processed += 1
                    normalized_rows += 1
                    ticker_rows += outcome.ticker_count
                    fetch_tasks += outcome.fetch_task_count
                    pending_batch.append(ProcessedNewsItem(result=outcome.result, raw_json_path=outcome.raw_json_path))
                    result_handle.write(json.dumps(summary_row(outcome), ensure_ascii=False, default=str) + "\n")
                    if len(pending_batch) >= batch_size:
                        batch_summary = writer_pipeline.write_many(
                            pending_batch,
                            target=clickhouse_target,
                            execute=execute,
                            skip_existing=skip_existing,
                            skip_table_validation=skip_table_validation,
                        )
                        batches_written += 1
                        skipped_existing += batch_summary.skipped_existing
                        result_handle.write(json.dumps({"type": "batch_write", **asdict(batch_summary)}, ensure_ascii=False, default=str) + "\n")
                        pending_batch.clear()
                else:
                    failed += 1
                    error_handle.write(json.dumps(summary_row(outcome), ensure_ascii=False, default=str) + "\n")
                if progress_interval and (processed + failed) % progress_interval == 0:
                    print(
                        f"progress={processed + failed:,}/{total:,} ok={processed:,} failed={failed:,} "
                        f"batch_pending={len(pending_batch):,} execute={execute}",
                        flush=True,
                    )
        if pending_batch:
            batch_summary = writer_pipeline.write_many(
                pending_batch,
                target=clickhouse_target,
                execute=execute,
                skip_existing=skip_existing,
                skip_table_validation=skip_table_validation,
            )
            batches_written += 1
            skipped_existing += batch_summary.skipped_existing
            result_handle.write(json.dumps({"type": "batch_write", **asdict(batch_summary)}, ensure_ascii=False, default=str) + "\n")

    return GapFillSummary(
        status="ok" if failed == 0 else "completed_with_errors",
        files_seen=total,
        files_processed=processed,
        files_failed=failed,
        normalized_rows=normalized_rows,
        ticker_rows=ticker_rows,
        fetch_tasks=fetch_tasks,
        batches_written=batches_written,
        skipped_existing=skipped_existing,
        execute=execute,
        output_jsonl=str(output_jsonl),
        error_jsonl=str(error_jsonl),
    )


def process_raw_file_worker(raw_json_path: str, config_values: dict[str, Any]) -> GapFillFileResult:
    try:
        cfg = BenzingaPipelineConfig(
            policy_json=str(config_values.get("policy_json") or ""),
            text_limit_chars=int(config_values.get("text_limit_chars") or 50_000),
            raw_root_win=Path(str(config_values.get("raw_root_win") or "D:/market-data/news-benzinga/raw")),
            output_root_win=Path(str(config_values.get("output_root_win") or "D:/market-data/prepared/benzinga_news_package")),
            max_enriched_text_chars_per_url=int(config_values.get("max_enriched_text_chars_per_url") or 24_000),
            max_enriched_urls_per_article=int(config_values.get("max_enriched_urls_per_article") or 5),
        )
        pipeline = BenzingaNewsPipeline(cfg)
        processed = pipeline.process_raw_file(raw_json_path)
        result = processed.result
        return GapFillFileResult(
            status="ok",
            raw_json_path=raw_json_path,
            provider_article_id=result.provider_article_id,
            canonical_news_id=result.canonical_news_id,
            ticker_count=len(result.ticker_links),
            fetch_task_count=len(result.url_resolution.fetch_tasks),
            warning_count=len(result.warnings),
            result=result,
        )
    except Exception as exc:  # noqa: BLE001
        return GapFillFileResult(
            status="failed",
            raw_json_path=raw_json_path,
            exception=repr(exc),
            traceback=traceback.format_exc(),
        )


def summary_row(outcome: GapFillFileResult) -> dict[str, Any]:
    return {
        "type": "item",
        "status": outcome.status,
        "raw_json_path": outcome.raw_json_path,
        "provider_article_id": outcome.provider_article_id,
        "canonical_news_id": outcome.canonical_news_id,
        "ticker_count": outcome.ticker_count,
        "fetch_task_count": outcome.fetch_task_count,
        "warning_count": outcome.warning_count,
        "exception": outcome.exception,
    }


def date_range_days(start_utc: datetime, end_utc: datetime) -> Iterable[datetime]:
    current = datetime(start_utc.year, start_utc.month, start_utc.day, tzinfo=UTC)
    last = datetime(end_utc.year, end_utc.month, end_utc.day, tzinfo=UTC)
    while current <= last:
        yield current
        current += timedelta(days=1)

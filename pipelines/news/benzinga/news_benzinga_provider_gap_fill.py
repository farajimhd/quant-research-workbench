from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.news_benzinga_normalize import artifact_path_for_payload, parse_provider_datetime, write_raw_payload  # noqa: E402
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig  # noqa: E402
from pipelines.news.benzinga.news_pipeline.pipeline import BenzingaNewsPipeline, ProcessedNewsItem  # noqa: E402
from pipelines.news.benzinga.news_pipeline.provider import BenzingaProviderClient, BenzingaProviderConfig  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


@dataclass(frozen=True, slots=True)
class BucketJob:
    bucket_index: int
    start_utc: str
    end_utc: str
    raw_root_win: str
    policy_json: str
    text_limit_chars: int
    endpoint_url: str
    api_key: str
    page_limit: int
    max_pages: int


@dataclass(frozen=True, slots=True)
class BucketResult:
    bucket_index: int
    start_utc: str
    end_utc: str
    status: str
    provider_rows: int = 0
    processed_rows: int = 0
    failed_rows: int = 0
    pages: int = 0
    saturated: bool = False
    exception: str = ""
    traceback: str = ""
    processed: list[ProcessedNewsItem] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download, normalize, and write Benzinga news for a historical date range.")
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--raw-root-win", default=os.environ.get("NEWS_BENZINGA_RAW_ROOT_WIN") or r"\\DESKTOP-SAAI85T\Workstation-D\market-data\news-benzinga\raw")
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_PROVIDER_GAP_OUTPUT_ROOT_WIN") or r"\\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\benzinga_news_provider_gap_fill")
    parser.add_argument("--bucket-minutes", type=int, default=90)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--progress-interval", type=int, default=10)
    parser.add_argument("--policy-json", default=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "")
    parser.add_argument("--text-limit-chars", type=int, default=int(os.environ.get("NEWS_BENZINGA_TEXT_LIMIT_CHARS") or "50000"))
    parser.add_argument("--endpoint-url", default=os.environ.get("NEWS_BENZINGA_URL") or os.environ.get("NEWS_MASSIVE_BENZINGA_URL") or "https://api.massive.com/benzinga/v2/news")
    parser.add_argument("--api-key", default=os.environ.get("MASSIVE_API_KEY") or "")
    parser.add_argument("--page-limit", type=int, default=int(os.environ.get("NEWS_BENZINGA_PAGE_LIMIT") or "1000"))
    parser.add_argument("--max-pages", type=int, default=int(os.environ.get("NEWS_BENZINGA_MAX_PAGES") or "1000"))
    parser.add_argument("--clickhouse-url", default=ClickHouseTargetConfig.from_env().url)
    parser.add_argument("--user", default=ClickHouseTargetConfig.from_env().user)
    parser.add_argument("--password", default=ClickHouseTargetConfig.from_env().password)
    parser.add_argument("--database", default=ClickHouseTargetConfig.from_env().database)
    parser.add_argument("--normalized-table", default=ClickHouseTargetConfig.from_env().normalized_table)
    parser.add_argument("--ticker-table", default=ClickHouseTargetConfig.from_env().ticker_table)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    if not args.api_key:
        raise RuntimeError("MASSIVE_API_KEY is required")
    start = parse_utc(args.start_utc)
    end = parse_utc(args.end_utc)
    jobs = build_jobs(args, start, end)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    result_path = run_root / "benzinga_provider_gap_fill_results.jsonl"
    error_path = run_root / "benzinga_provider_gap_fill_errors.jsonl"
    summary_path = run_root / "benzinga_provider_gap_fill_summary.json"
    run_root.mkdir(parents=True, exist_ok=True)
    target = ClickHouseTargetConfig(
        url=args.clickhouse_url,
        user=args.user,
        password=args.password,
        database=args.database,
        normalized_table=args.normalized_table,
        ticker_table=args.ticker_table,
    )
    writer = BenzingaNewsPipeline(BenzingaPipelineConfig(policy_json=args.policy_json, text_limit_chars=args.text_limit_chars, raw_root_win=Path(args.raw_root_win), output_root_win=run_root))
    print("=" * 96, flush=True)
    print("Benzinga provider gap fill", flush=True)
    print(f"range={args.start_utc} -> {args.end_utc} buckets={len(jobs):,} workers={args.workers}", flush=True)
    print(f"raw_root={args.raw_root_win}", flush=True)
    print(f"target={target.database}.{target.normalized_table} + {target.database}.{target.ticker_table}", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["MASSIVE_API_KEY", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 96, flush=True)
    completed = failed = provider_rows = processed_rows = failed_rows = skipped_existing = written_rows = 0
    pending: list[ProcessedNewsItem] = []
    with result_path.open("w", encoding="utf-8") as results, error_path.open("w", encoding="utf-8") as errors:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(process_bucket, job) for job in jobs]
            for future in concurrent.futures.as_completed(futures):
                outcome = future.result()
                public = {key: value for key, value in asdict(outcome).items() if key != "processed"}
                if outcome.status == "ok":
                    completed += 1
                    provider_rows += outcome.provider_rows
                    processed_rows += outcome.processed_rows
                    failed_rows += outcome.failed_rows
                    pending.extend(outcome.processed or [])
                    results.write(json.dumps(public, ensure_ascii=False, default=str) + "\n")
                    while len(pending) >= args.batch_size:
                        batch = pending[: args.batch_size]
                        del pending[: args.batch_size]
                        summary = writer.write_many(batch, target=target, execute=args.execute, skip_existing=True)
                        skipped_existing += summary.skipped_existing
                        written_rows += summary.normalized_rows_inserted
                else:
                    failed += 1
                    errors.write(json.dumps(public, ensure_ascii=False, default=str) + "\n")
                if (completed + failed) % max(1, args.progress_interval) == 0:
                    print(
                        f"progress={completed + failed:,}/{len(jobs):,} completed={completed:,} failed={failed:,} "
                        f"provider_rows={provider_rows:,} processed={processed_rows:,} write_pending={len(pending):,}",
                        flush=True,
                    )
        if pending:
            summary = writer.write_many(pending, target=target, execute=args.execute, skip_existing=True)
            skipped_existing += summary.skipped_existing
            written_rows += summary.normalized_rows_inserted
    summary_payload = {
        "status": "ok" if failed == 0 else "completed_with_errors",
        "buckets": len(jobs),
        "completed_buckets": completed,
        "failed_buckets": failed,
        "provider_rows": provider_rows,
        "processed_rows": processed_rows,
        "failed_rows": failed_rows,
        "written_rows": written_rows,
        "skipped_existing": skipped_existing,
        "execute": args.execute,
        "result_path": str(result_path),
        "error_path": str(error_path),
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2, sort_keys=True), encoding="utf-8")
    print("summary=" + json.dumps(summary_payload, sort_keys=True), flush=True)
    print(f"summary_json={summary_path}", flush=True)


def process_bucket(job: BucketJob) -> BucketResult:
    try:
        provider = BenzingaProviderClient(
            BenzingaProviderConfig(endpoint_url=job.endpoint_url, api_key=job.api_key, page_limit=job.page_limit, max_pages=job.max_pages)
        )
        pipeline = BenzingaNewsPipeline(
            BenzingaPipelineConfig(policy_json=job.policy_json, text_limit_chars=job.text_limit_chars, raw_root_win=Path(job.raw_root_win))
        )
        start = parse_utc(job.start_utc)
        end = parse_utc(job.end_utc)
        fetched = provider.fetch_window(start, end)
        processed: list[ProcessedNewsItem] = []
        failed = 0
        for payload in fetched.items:
            try:
                raw_path, raw_hash = save_raw_payload(Path(job.raw_root_win), payload)
                processed.append(pipeline.process_payload(payload, raw_artifact_path=str(raw_path), raw_payload_hash=raw_hash, downloaded_at_utc=datetime.now(UTC)))
            except Exception:
                failed += 1
        return BucketResult(
            bucket_index=job.bucket_index,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            status="ok",
            provider_rows=len(fetched.items),
            processed_rows=len(processed),
            failed_rows=failed,
            pages=fetched.pages,
            saturated=fetched.saturated,
            processed=processed,
        )
    except Exception as exc:  # noqa: BLE001
        return BucketResult(
            bucket_index=job.bucket_index,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            status="failed",
            exception=repr(exc),
            traceback=traceback.format_exc(),
        )


def save_raw_payload(raw_root: Path, payload: dict[str, Any]) -> tuple[Path, str]:
    try:
        published = parse_provider_datetime(str(payload.get("published") or ""))
    except Exception:
        published = datetime.now(UTC)
    raw_path = artifact_path_for_payload(raw_root.parent, payload, published)
    return raw_path, write_raw_payload(raw_path, payload)


def build_jobs(args: argparse.Namespace, start: datetime, end: datetime) -> list[BucketJob]:
    jobs: list[BucketJob] = []
    current = start
    index = 0
    while current < end:
        bucket_end = min(current + timedelta(minutes=max(1, args.bucket_minutes)), end)
        jobs.append(
            BucketJob(
                bucket_index=index,
                start_utc=current.isoformat().replace("+00:00", "Z"),
                end_utc=bucket_end.isoformat().replace("+00:00", "Z"),
                raw_root_win=args.raw_root_win,
                policy_json=args.policy_json,
                text_limit_chars=args.text_limit_chars,
                endpoint_url=args.endpoint_url,
                api_key=args.api_key,
                page_limit=args.page_limit,
                max_pages=args.max_pages,
            )
        )
        index += 1
        current = bucket_end
    return jobs


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    main()

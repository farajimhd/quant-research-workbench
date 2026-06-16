from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from pipelines.sec.edgar.sec_historical_feed_download import (  # noqa: E402
    RateLimiter,
    discover_available_archive_days,
    parse_date,
    sec_user_agent,
)
from pipelines.sec.edgar.sec_initial_fill_download import (  # noqa: E402
    DownloadResult,
    SourceSpec,
    build_summary,
    daily_archive_spec,
    download_all,
    is_g_drive_path,
    print_header,
    write_manifest,
)


DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_daily_feed_archives")
DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v2"
DEFAULT_START_DATE = date(2019, 1, 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download SEC EDGAR daily Feed .nc.tar.gz archives only. This script does not "
            "decompress archives, parse filings, fetch headers, or write to ClickHouse."
        )
    )
    parser.add_argument(
        "--artifact-root-win",
        default=os.environ.get("SEC_DAILY_FEED_ARTIFACT_ROOT_WIN") or os.environ.get("SEC_CORE_ARTIFACT_ROOT_WIN") or str(DEFAULT_ARTIFACT_ROOT_WIN),
        help="Root where compressed daily feed archives are retained.",
    )
    parser.add_argument(
        "--output-root-win",
        default=os.environ.get("SEC_DAILY_FEED_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN),
        help="Root where manifests and summaries are written.",
    )
    parser.add_argument("--start-date", help="Inclusive archive date, YYYY-MM-DD. Defaults to 2019-01-01.")
    parser.add_argument("--end-date", help="Exclusive archive date, YYYY-MM-DD. Defaults to tomorrow in UTC.")
    parser.add_argument("--infer-from-clickhouse", action="store_true", help="Infer date range from q_live.sec_filing_v2 instead of using the default 2019-to-now range.")
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--target-table", default=os.environ.get("QLIVE_MIGRATION_SEC_FILING_TABLE", DEFAULT_TARGET_TABLE))
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--limit-days", type=int, default=0, help="Optional smoke-test cap after archive discovery.")
    parser.add_argument("--download-concurrency", type=int, default=int(os.environ.get("SEC_DAILY_FEED_DOWNLOAD_CONCURRENCY", "1")))
    parser.add_argument(
        "--sec-request-min-interval-seconds",
        type=float,
        default=float(os.environ.get("SEC_DAILY_FEED_REQUEST_MIN_INTERVAL_SECONDS", os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", "1.0"))),
        help="Global minimum delay between SEC requests. Daily archives are large, so the default is conservative.",
    )
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_DAILY_FEED_REQUEST_TIMEOUT_SECONDS", os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "30"))))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_DAILY_FEED_MAX_RETRIES", os.environ.get("SEC_MAX_RETRIES", "8"))))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_DAILY_FEED_RETRY_BASE_SECONDS", os.environ.get("SEC_RETRY_BASE_SECONDS", "30"))))
    parser.add_argument("--max-429-before-stop", type=int, default=int(os.environ.get("SEC_DAILY_FEED_MAX_429_BEFORE_STOP", os.environ.get("SEC_MAX_429_BEFORE_STOP", "20"))))
    parser.add_argument("--stop-on-429", dest="stop_on_429", action="store_true", default=False)
    parser.add_argument("--continue-on-429", dest="stop_on_429", action="store_false")
    parser.add_argument("--allow-g-drive", action="store_true", help="Allow artifact/output roots on G:. Disabled by default.")
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    parser.add_argument("--progress-layout", choices=["auto", "rich", "text"], default=os.environ.get("SEC_DAILY_FEED_PROGRESS_LAYOUT", "auto"))
    parser.add_argument("--progress-log-lines", type=int, default=18)
    parser.add_argument("--progress-refresh-per-second", type=float, default=4.0)
    parser.add_argument("--progress-screen", dest="progress_screen", action="store_true", default=True)
    parser.add_argument("--no-progress-screen", dest="progress_screen", action="store_false")
    parser.add_argument("--force", action="store_true", help="Redownload even when an archive already exists.")
    parser.add_argument("--dry-run", action="store_true", help="Discover and report planned archive downloads only.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    validate_args(args)

    artifact_root = Path(args.artifact_root_win)
    output_root = Path(args.output_root_win)
    artifact_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    user_agent = sec_user_agent()
    start_date, end_date, date_source = resolve_date_range(args)
    limiter = RateLimiter(max(0.0, args.sec_request_min_interval_seconds))
    days = discover_available_archive_days(
        start_date,
        end_date,
        user_agent,
        max(1.0, args.request_timeout_seconds),
        max(0, args.max_retries),
        max(0.1, args.retry_base_seconds),
        limiter,
    )
    if args.limit_days:
        days = days[: max(0, args.limit_days)]
    specs = [daily_archive_spec(day, artifact_root) for day in days]

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    manifest_path = output_root / f"sec_daily_feed_archives_{run_id}.jsonl"
    summary_path = output_root / f"sec_daily_feed_archives_summary_{run_id}.json"

    print_header(
        {
            "run_id": run_id,
            "mode": "daily_feed_archive_download_only",
            "artifact_root": str(artifact_root),
            "output_root": str(output_root),
            "date_source": date_source,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "available_archive_days": len(days),
            "planned_downloads": len(specs),
            "download_concurrency": max(1, args.download_concurrency),
            "request_min_interval_seconds": max(0.0, args.sec_request_min_interval_seconds),
            "stop_on_429": args.stop_on_429,
            "max_429_before_stop": max(1, args.max_429_before_stop),
            "dry_run": args.dry_run,
            "loaded_env_files": [str(path) for path in loaded_env_files],
            "secret_status": secret_status(
                [
                    "SEC_USER_AGENT",
                    "SEC_EDGAR_USER_AGENT",
                    "NEWS_SEC_USER_AGENT",
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                ]
            ),
        }
    )

    started = time.perf_counter()
    if args.dry_run:
        results = planned_results(specs)
        write_manifest(manifest_path, results)
    else:
        results = download_all(specs, args, user_agent, limiter)
        write_manifest(manifest_path, results)

    summary = build_summary(run_id, results, time.perf_counter() - started, manifest_path)
    summary.update(
        {
            "mode": "daily_feed_archive_download_only",
            "date_source": date_source,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "available_archive_days": len(days),
            "artifact_root": str(artifact_root),
            "output_root": str(output_root),
        }
    )
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest_path=" + str(manifest_path), flush=True)
    print("summary_path=" + str(summary_path), flush=True)
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    if any(row.status == "failed" for row in results):
        raise SystemExit(1)
    if any(row.status.startswith("stopped") or row.status == "cancelled" for row in results):
        raise SystemExit(2)


def validate_args(args: argparse.Namespace) -> None:
    if not args.allow_g_drive:
        for label, raw_path in [("artifact root", args.artifact_root_win), ("output root", args.output_root_win)]:
            if is_g_drive_path(Path(raw_path)):
                raise SystemExit(
                    f"{label} points to G:, which is blocked for this downloader: {raw_path}. "
                    "Use an SSD path such as D:/market-data/sec_core, or pass --allow-g-drive only for an intentional exception."
                )
    if args.infer_from_clickhouse and (args.start_date or args.end_date):
        raise SystemExit("--infer-from-clickhouse cannot be combined with --start-date or --end-date.")
    if args.start_date and args.end_date and parse_date(args.end_date) <= parse_date(args.start_date):
        raise SystemExit("--end-date must be later than --start-date.")
    if args.start_date and not args.end_date and datetime.now(UTC).date() + timedelta(days=1) <= parse_date(args.start_date):
        raise SystemExit("--start-date must be earlier than tomorrow in UTC when --end-date is omitted.")
    if args.end_date and not args.start_date and parse_date(args.end_date) <= DEFAULT_START_DATE:
        raise SystemExit("--end-date must be later than 2019-01-01 when --start-date is omitted.")
    if args.download_concurrency < 1:
        raise SystemExit("--download-concurrency must be >= 1.")


def resolve_date_range(args: argparse.Namespace) -> tuple[date, date, str]:
    if args.start_date and args.end_date:
        return parse_date(args.start_date), parse_date(args.end_date), "explicit_args"
    if args.start_date:
        return parse_date(args.start_date), datetime.now(UTC).date() + timedelta(days=1), "explicit_start_to_today"
    if args.end_date:
        return DEFAULT_START_DATE, parse_date(args.end_date), "default_start_to_explicit_end"
    if not args.infer_from_clickhouse:
        return DEFAULT_START_DATE, datetime.now(UTC).date() + timedelta(days=1), "default_2019_to_today"

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    table = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    try:
        text = client.execute(
            f"""
            SELECT min(filing_date), max(filing_date)
            FROM {table} FINAL
            WHERE filing_date IS NOT NULL
            FORMAT TSV
            """
        ).strip()
    except Exception as exc:
        raise SystemExit(
            "Could not infer SEC daily archive date range from q_live because ClickHouse is not reachable. "
            "Start ClickHouse or pass explicit --start-date and --end-date. "
            f"Original error: {exc}"
        ) from exc
    if not text:
        raise SystemExit("Could not infer SEC daily archive date range from q_live; pass --start-date and --end-date.")
    min_text, max_text = text.split("\t")[:2]
    if not min_text or not max_text or min_text == "\\N" or max_text == "\\N":
        raise SystemExit("q_live filing_date range is empty; pass --start-date and --end-date.")
    return parse_date(min_text), parse_date(max_text) + timedelta(days=1), f"{args.target_database}.{args.target_table}"


def planned_results(specs: list[SourceSpec]) -> list[DownloadResult]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    rows = []
    for spec in specs:
        rows.append(
            DownloadResult(
                source_file_id=f"planned:{spec.source_kind}:{spec.source_date}:{spec.artifact_path}",
                source_kind=spec.source_kind,
                source_url=spec.source_url,
                artifact_path=spec.artifact_path,
                source_date=spec.source_date,
                downloaded_at_utc=now,
                byte_size=0,
                sha256="",
                etag="",
                last_modified="",
                elapsed_seconds=0.0,
                status="planned",
            )
        )
    return rows


def default_migration_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_migration_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_migration_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


if __name__ == "__main__":
    main()

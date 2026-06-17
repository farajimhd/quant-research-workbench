from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig  # noqa: E402
from pipelines.news.benzinga.news_pipeline.gap_fill import discover_raw_files, run_raw_file_gap_fill  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Concurrent Benzinga raw-file gap fill using the reusable item-level news package.")
    parser.add_argument("--raw-root-win", default=os.environ.get("NEWS_BENZINGA_RAW_ROOT_WIN") or "D:/market-data/news-benzinga/raw")
    parser.add_argument("--start-utc", default="", help="Optional inclusive UTC start. If omitted, scans all raw files under raw-root.")
    parser.add_argument("--end-utc", default="", help="Optional exclusive UTC end. If omitted, scans all raw files under raw-root.")
    parser.add_argument("--policy-json", default=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "")
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_PACKAGE_OUTPUT_ROOT_WIN") or "D:/market-data/prepared/benzinga_news_package_gap_fill")
    parser.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--limit-files", type=int, default=0)
    parser.add_argument("--progress-interval", type=int, default=500)
    parser.add_argument("--text-limit-chars", type=int, default=int(os.environ.get("NEWS_BENZINGA_TEXT_LIMIT_CHARS") or "50000"))
    parser.add_argument("--clickhouse-url", default=ClickHouseTargetConfig.from_env().url)
    parser.add_argument("--user", default=ClickHouseTargetConfig.from_env().user)
    parser.add_argument("--password", default=ClickHouseTargetConfig.from_env().password)
    parser.add_argument("--database", default=ClickHouseTargetConfig.from_env().database)
    parser.add_argument("--normalized-table", default=ClickHouseTargetConfig.from_env().normalized_table)
    parser.add_argument("--ticker-table", default=ClickHouseTargetConfig.from_env().ticker_table)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--insert-existing", action="store_true", help="Do not skip rows already present by canonical_news_id.")
    parser.add_argument("--skip-table-validation", action="store_true", help="Reserved for compatibility; validation is skipped only by writer internals when safe.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    start = parse_utc(args.start_utc) if args.start_utc else None
    end = parse_utc(args.end_utc) if args.end_utc else None
    if bool(start) != bool(end):
        raise SystemExit("--start-utc and --end-utc must be provided together")
    raw_root = Path(args.raw_root_win)
    raw_files = discover_raw_files(raw_root, start, end)
    if args.limit_files > 0:
        raw_files = raw_files[: args.limit_files]
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    output_jsonl = run_root / "benzinga_package_gap_fill_results.jsonl"
    error_jsonl = run_root / "benzinga_package_gap_fill_errors.jsonl"
    pipeline_config = BenzingaPipelineConfig(
        policy_json=args.policy_json,
        text_limit_chars=args.text_limit_chars,
        raw_root_win=raw_root,
        output_root_win=run_root,
    )
    target = ClickHouseTargetConfig(
        url=args.clickhouse_url,
        user=args.user,
        password=args.password,
        database=args.database,
        normalized_table=args.normalized_table,
        ticker_table=args.ticker_table,
    )
    print("=" * 96, flush=True)
    print("Benzinga package gap fill", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"raw_root={raw_root}", flush=True)
    print(f"files={len(raw_files):,} processes={args.processes} batch_size={args.batch_size}", flush=True)
    print(f"date_range={args.start_utc or '<all>'} -> {args.end_utc or '<all>'}", flush=True)
    print(f"target={target.database}.{target.normalized_table} + {target.database}.{target.ticker_table}", flush=True)
    print(f"execute={args.execute} skip_existing={not args.insert_existing}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 96, flush=True)
    summary = run_raw_file_gap_fill(
        raw_files=raw_files,
        pipeline_config=pipeline_config,
        clickhouse_target=target,
        output_jsonl=output_jsonl,
        error_jsonl=error_jsonl,
        processes=args.processes,
        batch_size=max(1, args.batch_size),
        execute=args.execute,
        skip_existing=not args.insert_existing,
        skip_table_validation=args.skip_table_validation,
        progress_interval=max(1, args.progress_interval),
    )
    summary_path = run_root / "benzinga_package_gap_fill_summary.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True), encoding="utf-8")
    print("summary=" + json.dumps(asdict(summary), sort_keys=True), flush=True)
    print(f"summary_json={summary_path}", flush=True)


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("empty datetime")
    if len(text) == 10:
        text = text + "T00:00:00Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    main()

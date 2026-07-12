from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
SCRIPT = REPO_ROOT / "pipelines" / "market_sip" / "events" / "clickhouse_build_sec_context.py"


DEFAULTS = {
    "source_database": "q_live",
    "target_database": "market_sip_compact",
    "filing_table": "sec_filing_context_v3",
    "text_table": "sec_filing_text_context_v3",
    "xbrl_table": "sec_xbrl_context_v3",
    "source_filing_table": "sec_filing_v3",
    "source_text_table": "sec_filing_text_v3",
    "source_bridge_table": "id_sec_market_bridge_v3",
    "source_xbrl_company_fact_table": "sec_xbrl_company_fact_v3",
    "source_xbrl_frame_observation_table": "sec_xbrl_frame_observation_v3",
    "start_date": "2019-01-01",
    "end_date": "2026-12-31",
    "max_threads": 32,
    "max_memory_usage": "300G",
    "output_root_win": r"D:\market-data\prepared\clickhouse_sip_ingest\sec_context",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for SEC context migration into market_sip_compact.")
    parser.add_argument("--source-database", default=DEFAULTS["source_database"])
    parser.add_argument("--target-database", default=DEFAULTS["target_database"])
    parser.add_argument("--filing-table", default=DEFAULTS["filing_table"])
    parser.add_argument("--text-table", default=DEFAULTS["text_table"])
    parser.add_argument("--xbrl-table", default=DEFAULTS["xbrl_table"])
    parser.add_argument("--source-filing-table", default=DEFAULTS["source_filing_table"])
    parser.add_argument("--source-text-table", default=DEFAULTS["source_text_table"])
    parser.add_argument("--source-bridge-table", default=DEFAULTS["source_bridge_table"])
    parser.add_argument("--source-xbrl-company-fact-table", default=DEFAULTS["source_xbrl_company_fact_table"])
    parser.add_argument("--source-xbrl-frame-observation-table", default=DEFAULTS["source_xbrl_frame_observation_table"])
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--output-root-win", default=DEFAULTS["output_root_win"])
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--no-replace-range", action="store_true")
    parser.add_argument("--no-wait-mutations", action="store_true")
    parser.add_argument("--mutation-timeout-seconds", type=int, default=7200)
    parser.add_argument("--text-prefix-chars", type=int, default=0, help="Deprecated no-op. SEC text context now stores full text.")
    parser.add_argument("--max-text-rows-per-filing", type=int, default=0, help="Deprecated no-op. SEC text context now stores every text row.")
    parser.add_argument("--sec-text-buckets", type=int, default=64)
    parser.add_argument("--render-batch-rows", type=int, default=256)
    parser.add_argument(
        "--skip-text",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip the legacy SEC text-context copy by default.",
    )
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--drop-target-tables", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    argv = [
        sys.executable,
        str(SCRIPT),
        "--source-database",
        args.source_database,
        "--target-database",
        args.target_database,
        "--filing-table",
        args.filing_table,
        "--text-table",
        args.text_table,
        "--xbrl-table",
        args.xbrl_table,
        "--source-filing-table",
        args.source_filing_table,
        "--source-text-table",
        args.source_text_table,
        "--source-bridge-table",
        args.source_bridge_table,
        "--source-xbrl-company-fact-table",
        args.source_xbrl_company_fact_table,
        "--source-xbrl-frame-observation-table",
        args.source_xbrl_frame_observation_table,
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        args.max_memory_usage,
        "--output-root-win",
        args.output_root_win,
        "--mutation-timeout-seconds",
        str(args.mutation_timeout_seconds),
        "--text-prefix-chars",
        str(args.text_prefix_chars),
        "--max-text-rows-per-filing",
        str(args.max_text_rows_per_filing),
        "--sec-text-buckets",
        str(args.sec_text_buckets),
        "--render-batch-rows",
        str(args.render_batch_rows),
    ]
    if args.storage_policy:
        argv.extend(["--storage-policy", args.storage_policy])
    if args.clickhouse_url:
        argv.extend(["--clickhouse-url", args.clickhouse_url])
    if args.user:
        argv.extend(["--user", args.user])
    if args.password:
        argv.extend(["--password", args.password])
    if args.no_replace_range:
        argv.append("--no-replace-range")
    if args.no_wait_mutations:
        argv.append("--no-wait-mutations")
    if args.skip_text:
        argv.append("--skip-text")
    else:
        argv.append("--no-skip-text")
    if args.skip_xbrl:
        argv.append("--skip-xbrl")
    if args.drop_target_tables:
        argv.append("--drop-target-tables")
    if args.dry_run:
        argv.append("--dry-run")

    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if args.print_only:
        return 0
    try:
        return subprocess.call(argv)
    except KeyboardInterrupt:
        print("Interrupted by user.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
SCRIPT = REPO_ROOT / "pipelines" / "market_sip" / "events" / "clickhouse_build_training_category_reference.py"


DEFAULTS = {
    "database": "market_sip_compact",
    "xbrl_table": "sec_xbrl_context",
    "news_token_table": "news_text_tokens",
    "sec_token_table": "sec_filing_text_tokens",
    "reference_table": "training_category_reference",
    "max_threads": 16,
    "max_memory_usage": "80G",
    "output_root_win": r"D:\market-data\prepared\clickhouse_sip_ingest\category_references",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for training categorical reference table build.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--xbrl-table", default=DEFAULTS["xbrl_table"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-token-table", default=DEFAULTS["sec_token_table"])
    parser.add_argument("--reference-table", default=DEFAULTS["reference_table"])
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--output-root-win", default=DEFAULTS["output_root_win"])
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    argv = [
        sys.executable,
        str(SCRIPT),
        "--database",
        args.database,
        "--xbrl-table",
        args.xbrl_table,
        "--news-token-table",
        args.news_token_table,
        "--sec-token-table",
        args.sec_token_table,
        "--reference-table",
        args.reference_table,
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        args.max_memory_usage,
        "--output-root-win",
        args.output_root_win,
    ]
    if args.storage_policy:
        argv.extend(["--storage-policy", args.storage_policy])
    if args.clickhouse_url:
        argv.extend(["--clickhouse-url", args.clickhouse_url])
    if args.user:
        argv.extend(["--user", args.user])
    if args.password:
        argv.extend(["--password", args.password])
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

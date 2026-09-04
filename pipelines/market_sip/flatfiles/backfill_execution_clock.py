from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events.clickhouse_build_unified_events import (  # noqa: E402
    DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE,
    DEFAULT_CONTINUITY_TABLE,
    DEFAULT_DROP_TRADE_CORRECTION_CODES,
    DEFAULT_EVENTS_TABLE,
    events_table_for_source_date,
)
from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import DownloadJob  # noqa: E402
from pipelines.market_sip.flatfiles.download_update_events import (  # noqa: E402
    DEFAULT_EXECUTION_CLOCK_COVERAGE_TABLE,
    DEFAULT_EXECUTION_CLOCK_DATABASE,
    DEFAULT_EXECUTION_CLOCK_TABLE,
    DayFiles,
    create_execution_clock_coverage_table_sql,
    create_execution_clock_table_sql,
    execution_clock_day_is_complete,
    rebuild_execution_clock_day,
)
from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import DEFAULT_DATABASE  # noqa: E402
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import (  # noqa: E402
    default_clickhouse_url_with_network_fallback,
)
from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_FLATFILES_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files  # noqa: E402


SAFE_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate the versioned execution-clock sidecar without altering immutable historical events."
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--source-date", required=True)
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--continuity-table", default=DEFAULT_CONTINUITY_TABLE)
    parser.add_argument("--condition-token-reference-table", default=DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE)
    parser.add_argument("--drop-trade-correction-codes", default=DEFAULT_DROP_TRADE_CORRECTION_CODES)
    parser.add_argument("--execution-clock-database", default=DEFAULT_EXECUTION_CLOCK_DATABASE)
    parser.add_argument("--execution-clock-table", default=DEFAULT_EXECUTION_CLOCK_TABLE)
    parser.add_argument("--execution-clock-coverage-table", default=DEFAULT_EXECUTION_CLOCK_COVERAGE_TABLE)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-threads", type=int, default=16)
    parser.add_argument("--max-memory-usage", default="64G")
    parser.add_argument("--max-partitions-per-insert-block", type=int, default=1024)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def source_files(root: Path, source_date: str) -> DayFiles:
    parsed = date.fromisoformat(source_date)
    month = f"{parsed.month:02d}"
    quote = root / "quotes_v1" / str(parsed.year) / month / f"{source_date}.csv.gz"
    trade = root / "trades_v1" / str(parsed.year) / month / f"{source_date}.csv.gz"
    for path in (quote, trade):
        if not path.is_file():
            raise FileNotFoundError(f"required SIP flatfile is missing: {path}")
    return DayFiles(
        source_date,
        DownloadJob("quotes", source_date, "", str(quote), quote.stat().st_size),
        DownloadJob("trades", source_date, "", str(trade), trade.stat().st_size),
    )


def main() -> None:
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    args.ticker = args.ticker.strip().upper()
    args.tickers = args.ticker
    if not SAFE_TICKER.fullmatch(args.ticker):
        raise ValueError(f"unsafe ticker {args.ticker!r}")
    date.fromisoformat(args.source_date)
    args.events_table = events_table_for_source_date(args.events_table, args.source_date)
    day = source_files(Path(args.flatfiles_root_win), args.source_date)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    client.execute(f"CREATE DATABASE IF NOT EXISTS `{args.execution_clock_database}`")
    client.execute(create_execution_clock_table_sql(args))
    client.execute(create_execution_clock_coverage_table_sql(args))
    if execution_clock_day_is_complete(client, args, day):
        print(f"[complete] ticker={args.ticker} source_date={args.source_date} status=already_covered")
        return
    if args.dry_run:
        print(f"[ready] ticker={args.ticker} source_date={args.source_date} immutable_archive=true")
        return
    started = time.perf_counter()
    print(f"[active] ticker={args.ticker} source_date={args.source_date} stage=execution_clock", flush=True)
    rebuild_execution_clock_day(client, args, day, int(args.source_date.replace("-", "")))
    print(
        f"[complete] ticker={args.ticker} source_date={args.source_date} "
        f"elapsed_seconds={time.perf_counter() - started:.1f}",
        flush=True,
    )


if __name__ == "__main__":
    main()

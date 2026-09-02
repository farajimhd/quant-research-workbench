from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path


REPO_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "research").exists() and (parent / "pipelines").exists()
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events.clickhouse_build_unified_events import (  # noqa: E402
    DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE,
    DEFAULT_DROP_TRADE_CORRECTION_CODES,
    events_table_for_source_date,
)
from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import DownloadJob  # noqa: E402
from pipelines.market_sip.flatfiles.download_update_events import (  # noqa: E402
    DayFiles,
    raw_event_union_sql,
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
    discover_clickhouse_env_files,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files  # noqa: E402


SAFE_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill the exact exchange execution clock for one ticker/day without "
            "rebuilding any other event population."
        )
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--source-date", required=True)
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--condition-token-reference-table", default=DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE)
    parser.add_argument("--drop-trade-correction-codes", default=DEFAULT_DROP_TRADE_CORRECTION_CODES)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def source_files(root: Path, source_date: str) -> DayFiles:
    parsed = date.fromisoformat(source_date)
    year = f"{parsed.year:04d}"
    month = f"{parsed.month:02d}"
    quote = root / "quotes_v1" / year / month / f"{source_date}.csv.gz"
    trade = root / "trades_v1" / year / month / f"{source_date}.csv.gz"
    for path in (quote, trade):
        if not path.is_file():
            raise FileNotFoundError(f"required SIP flatfile is missing: {path}")
    return DayFiles(
        source_date=source_date,
        quote_job=DownloadJob("quotes", source_date, "", str(quote), quote.stat().st_size),
        trade_job=DownloadJob("trades", source_date, "", str(trade), trade.stat().st_size),
    )


def scalar(client: ClickHouseHttpClient, sql: str) -> int:
    return int(client.query_tsv(sql).strip() or "0")


def main() -> None:
    started = time.perf_counter()
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    args.ticker = args.ticker.strip().upper()
    if not SAFE_TICKER.fullmatch(args.ticker):
        raise ValueError(f"unsafe ticker {args.ticker!r}")
    date.fromisoformat(args.source_date)
    args.tickers = args.ticker
    day = source_files(Path(args.flatfiles_root_win), args.source_date)
    args.events_table = events_table_for_source_date(args.events_table, args.source_date)
    stage = f"execution_clock_backfill_{args.source_date.replace('-', '')}_{args.ticker.replace('-', '_').replace('.', '_')}"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    db = quote_ident(args.database)
    table = quote_ident(args.events_table)
    stage_table = quote_ident(stage)
    ticker = sql_string(args.ticker)
    source_date_sql = sql_string(args.source_date)
    ordinal_offset = scalar(
        client,
        f"SELECT min(ordinal) FROM {db}.{table} WHERE ticker={ticker} AND event_date=toDate({source_date_sql})",
    )
    target_count = scalar(
        client,
        f"SELECT count() FROM {db}.{table} WHERE ticker={ticker} AND event_date=toDate({source_date_sql})",
    )
    if target_count <= 0:
        raise RuntimeError("the requested ticker/day has no compact events")
    union = raw_event_union_sql(args, day)
    statements = [
        f"ALTER TABLE {db}.{table} ADD COLUMN IF NOT EXISTS execution_timestamp_us UInt64 DEFAULT 0 AFTER event_meta",
        f"DROP TABLE IF EXISTS {db}.{stage_table}",
        f"CREATE TABLE {db}.{stage_table} (ticker String, ordinal UInt64, execution_timestamp_us UInt64) ENGINE=Join(ANY, LEFT, ticker, ordinal)",
        f"""INSERT INTO {db}.{stage_table}
SELECT e.ticker,
       toUInt64({ordinal_offset}) + toUInt64(row_number() OVER (ORDER BY e.sip_timestamp_us, e.sequence_number, bitAnd(e.event_meta, 1)) - 1) AS ordinal,
       e.execution_timestamp_us
FROM ({union}) AS e
ORDER BY ordinal""",
    ]
    mutation = f"""ALTER TABLE {db}.{table}
UPDATE execution_timestamp_us = joinGet('{args.database}.{stage}', 'execution_timestamp_us', toString(ticker), ordinal)
WHERE ticker={ticker} AND event_date=toDate({source_date_sql})
SETTINGS mutations_sync=2"""
    if args.dry_run:
        print(
            f"[ready] target={args.database}.{args.events_table} ticker={args.ticker} "
            f"date={args.source_date} rows={target_count}"
        )
        print("[complete] dry_run=true changes=0")
        return
    try:
        print(
            f"[active] stage=extract target={args.database}.{args.events_table} "
            f"ticker={args.ticker} date={args.source_date} rows={target_count}",
            flush=True,
        )
        for statement in statements:
            client.execute(statement)
        staged_count = scalar(client, f"SELECT count() FROM {db}.{stage_table}")
        if staged_count != target_count:
            raise RuntimeError(
                f"execution-clock staging mismatch: target={target_count} staged={staged_count}"
            )
        print(f"[active] stage=mutate staged={staged_count} target={target_count}", flush=True)
        client.execute(mutation)
        corrected = scalar(
            client,
            f"""SELECT count()
FROM {db}.{table}
WHERE ticker={ticker} AND event_date=toDate({source_date_sql})
  AND execution_timestamp_us > 0""",
        )
        if corrected != target_count:
            raise RuntimeError(
                f"execution-clock backfill did not cover every event: expected={target_count} corrected={corrected}"
            )
        print(
            f"[complete] execution_clock_backfill=ok table={args.database}.{args.events_table} "
            f"ticker={args.ticker} date={args.source_date} rows={corrected} "
            f"elapsed_seconds={time.perf_counter() - started:.1f}"
        )
    finally:
        client.execute(f"DROP TABLE IF EXISTS {db}.{stage_table}")


if __name__ == "__main__":
    main()

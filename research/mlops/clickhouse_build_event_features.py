from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_SOURCE_DATABASE = "market_sip_raw"
DEFAULT_TARGET_DATABASE = "market_sip_features"
DEFAULT_CLICKHOUSE_URL = "http://localhost:8123"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/clickhouse_event_features")
CLICKHOUSE_ENDPOINT_ENV = "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL"
CLICKHOUSE_PASSWORD_ENV = "TD__DATABASE__CLICKHOUSE__PASSWORD"
CLICKHOUSE_USER_ENV = "TD__DATABASE__CLICKHOUSE__USER"
CLICKHOUSE_STORAGE_POLICY_ENV = "TD__DATABASE__CLICKHOUSE__STORAGE_POLICY"


@dataclass(slots=True)
class QueryProfile:
    label: str
    query_id: str
    wall_seconds: float
    query_duration_ms: int | None = None
    memory_usage_bytes: int | None = None
    read_rows: int | None = None
    read_bytes: int | None = None
    written_rows: int | None = None
    written_bytes: int | None = None
    exception: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build normalized SIP event tables and lightweight sampling indexes in ClickHouse.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--source-database", default=DEFAULT_SOURCE_DATABASE)
    parser.add_argument("--target-database", default=DEFAULT_TARGET_DATABASE)
    parser.add_argument("--start-date", default="2025-01-02")
    parser.add_argument("--end-date", default="2025-01-10")
    parser.add_argument("--kinds", default="quotes,trades", help="Comma-separated subset of quotes,trades.")
    parser.add_argument("--storage-policy", default=default_storage_policy(), help="Optional MergeTree storage_policy for new feature tables, e.g. ssd_policy.")
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--context-events", type=int, default=128)
    parser.add_argument("--train-end-date", default="2025-12-31")
    parser.add_argument("--val-end-date", default="2026-02-28")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true")
    parser.add_argument("--skip-normalized", action="store_true")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def default_clickhouse_url() -> str:
    return os.environ.get("CLICKHOUSE_URL") or os.environ.get(CLICKHOUSE_ENDPOINT_ENV) or DEFAULT_CLICKHOUSE_URL


def default_clickhouse_user() -> str:
    return os.environ.get("CLICKHOUSE_USER") or os.environ.get(CLICKHOUSE_USER_ENV) or "default"


def default_clickhouse_password() -> str:
    return os.environ.get("CLICKHOUSE_PASSWORD") or os.environ.get(CLICKHOUSE_PASSWORD_ENV) or ""


def default_storage_policy() -> str:
    return os.environ.get("CLICKHOUSE_STORAGE_POLICY") or os.environ.get(CLICKHOUSE_STORAGE_POLICY_ENV) or ""


def discover_clickhouse_env_files() -> list[Path]:
    paths = discover_env_files(REPO_ROOT)
    for parent in REPO_ROOT.parents:
        if (parent / "codes").exists() and (parent / "secrets").exists():
            paths.extend([parent / ".env", parent / "secrets" / ".env"])
            break
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    kinds = parse_kinds(args.kinds)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    settings = query_settings(args)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_report_path = output_root / f"event_feature_build_{run_id}.jsonl"

    print("=" * 96, flush=True)
    print("ClickHouse SIP event feature build", flush=True)
    print(f"source_database={args.source_database} target_database={args.target_database}", flush=True)
    print(f"kinds={','.join(kinds)} start_date={args.start_date} end_date={args.end_date}", flush=True)
    print(f"context_events={args.context_events} train_end_date={args.train_end_date} val_end_date={args.val_end_date}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"settings={settings.strip()}", flush=True)
    print(f"dry_run={args.dry_run} skip_normalized={args.skip_normalized} skip_index={args.skip_index}", flush=True)
    print(f"output_report={run_report_path}", flush=True)
    print(f"secret_status={secret_status([CLICKHOUSE_ENDPOINT_ENV, CLICKHOUSE_USER_ENV, CLICKHOUSE_PASSWORD_ENV, CLICKHOUSE_STORAGE_POLICY_ENV])}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    dates_by_kind = discover_loaded_dates(client, args.source_database, kinds, args.start_date, args.end_date)
    total_tasks = sum(len(dates) for dates in dates_by_kind.values())
    print(f"Discovered normalized build tasks={total_tasks:,}", flush=True)
    for kind, dates in dates_by_kind.items():
        print(f"  {kind}: {len(dates):,} dates {dates[:3]}{' ... ' + dates[-1] if len(dates) > 3 else ''}", flush=True)

    if args.dry_run:
        return

    create_database_and_tables(client, args.target_database, args.storage_policy)
    started_at = time.perf_counter()
    completed = skipped = failed = 0

    if not args.skip_normalized:
        for kind in kinds:
            for date in dates_by_kind[kind]:
                latest_status = latest_manifest_status(client, args.target_database, kind, date)
                if should_skip(latest_status, args):
                    skipped += 1
                    print(f"SKIP normalized {kind}:{date} status={latest_status}", flush=True)
                    continue
                if latest_status in {"failed", "started"} and normalized_rows_present(client, args.target_database, kind, date):
                    insert_manifest(client, args.target_database, kind, date, status="ok", run_id=run_id, exception=f"Recovered from existing {latest_status} manifest; normalized rows already present.")
                    skipped += 1
                    print(f"RECOVER-SKIP normalized {kind}:{date} previous_status={latest_status} rows already present", flush=True)
                    continue
                print("=" * 96, flush=True)
                print(f"START normalized {kind}:{date}", flush=True)
                insert_manifest(client, args.target_database, kind, date, status="started", run_id=run_id)
                try:
                    profile = build_normalized_one_day(client, args.source_database, args.target_database, kind, date, settings)
                    insert_manifest(client, args.target_database, kind, date, status="ok", run_id=run_id, profile=profile)
                    append_jsonl(run_report_path, {"status": "ok", "kind": kind, "date": date, "profile": asdict(profile)})
                    print_profile_summary(profile)
                    completed += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    insert_manifest(client, args.target_database, kind, date, status="failed", run_id=run_id, exception=repr(exc))
                    append_jsonl(run_report_path, {"status": "failed", "kind": kind, "date": date, "exception": repr(exc)})
                    print(f"FAILED normalized {kind}:{date}: {exc!r}", flush=True)
                    raise
                print_progress(completed, skipped, failed, total_tasks, started_at)

    if not args.skip_index:
        print("=" * 96, flush=True)
        print("START ticker_month_index rebuild for requested period", flush=True)
        profile = rebuild_ticker_month_index(client, args.target_database, args.start_date, args.end_date, args.context_events, args.train_end_date, args.val_end_date, settings)
        append_jsonl(run_report_path, {"status": "ok", "operation": "rebuild_ticker_month_index", "profile": asdict(profile)})
        print_profile_summary(profile)

    print("=" * 96, flush=True)
    print(f"DONE completed={completed:,} skipped={skipped:,} failed={failed:,}", flush=True)
    print(f"report={run_report_path}", flush=True)
    print_table_stats(client, args.target_database)
    print("=" * 96, flush=True)


class ClickHouseHttpClient:
    def __init__(self, base_url: str, user: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        params = {}
        if query_id:
            params["query_id"] = query_id
        url = self.base_url + "/"
        if params:
            url += "?" + parse.urlencode(params)
        req = request.Request(url, data=sql.encode("utf-8"), method="POST")
        if self.user:
            req.add_header("X-ClickHouse-User", self.user)
        if self.password:
            req.add_header("X-ClickHouse-Key", self.password)
        try:
            with request.urlopen(req, timeout=None) as response:
                return response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc

    def query_tsv(self, sql: str) -> str:
        return self.execute(sql.rstrip(";") + " FORMAT TSV")


def parse_kinds(text: str) -> list[str]:
    kinds = [item.strip() for item in text.split(",") if item.strip()]
    invalid = [kind for kind in kinds if kind not in {"quotes", "trades"}]
    if invalid:
        raise ValueError(f"Invalid kinds: {invalid}; expected subset of quotes,trades")
    return kinds


def discover_loaded_dates(client: ClickHouseHttpClient, source_database: str, kinds: list[str], start_date: str, end_date: str) -> dict[str, list[str]]:
    dates_by_kind: dict[str, list[str]] = {}
    for kind in kinds:
        table = "quotes_raw" if kind == "quotes" else "trades_raw"
        text = client.query_tsv(
            "SELECT toString(source_date) "
            f"FROM {quote_ident(source_database)}.{quote_ident(table)} "
            f"WHERE source_date >= toDate({sql_string(start_date)}) AND source_date <= toDate({sql_string(end_date)}) "
            "GROUP BY source_date ORDER BY source_date"
        )
        dates_by_kind[kind] = [line.strip() for line in text.splitlines() if line.strip()]
    return dates_by_kind


def create_database_and_tables(client: ClickHouseHttpClient, target_database: str, storage_policy: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(target_database)}")
    client.execute(create_events_table_sql(target_database, storage_policy))
    client.execute(create_ticker_month_index_sql(target_database, storage_policy))
    client.execute(create_manifest_sql(target_database, storage_policy))


def create_events_table_sql(database: str, storage_policy: str) -> str:
    db = quote_ident(database)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.events_normalized
(
    ticker LowCardinality(String),
    source_kind LowCardinality(String),
    event_type UInt8,
    source_date Date,
    source_file LowCardinality(String),
    sip_timestamp UInt64,
    sequence_number UInt64,
    participant_timestamp UInt64,
    event_time DateTime64(9, 'UTC'),
    event_date Date,
    bid_price Float64,
    ask_price Float64,
    trade_price Float64,
    bid_size UInt32,
    ask_size UInt32,
    trade_size UInt32,
    bid_exchange UInt16,
    ask_exchange UInt16,
    trade_exchange UInt16,
    tape UInt8,
    conditions String,
    correction UInt8,
    id UInt64,
    trf_id UInt64,
    trf_timestamp UInt64,
    issue_flags UInt32,
    built_at DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp, sequence_number, event_type)
{mergetree_settings_sql(storage_policy)}
"""


def create_ticker_month_index_sql(database: str, storage_policy: str) -> str:
    db = quote_ident(database)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.event_ticker_month_index
(
    event_month UInt32,
    ticker LowCardinality(String),
    split LowCardinality(String),
    first_event_date Date,
    last_event_date Date,
    first_timestamp UInt64,
    last_timestamp UInt64,
    event_count UInt64,
    quote_count UInt64,
    trade_count UInt64,
    has_enough_context UInt8,
    context_events UInt32,
    built_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(built_at)
ORDER BY (event_month, ticker)
{mergetree_settings_sql(storage_policy)}
"""


def create_manifest_sql(database: str, storage_policy: str) -> str:
    db = quote_ident(database)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.feature_build_manifest
(
    kind LowCardinality(String),
    source_date Date,
    status LowCardinality(String),
    run_id String,
    query_id String,
    wall_seconds Float64,
    query_duration_ms UInt64,
    memory_usage_bytes UInt64,
    read_rows UInt64,
    read_bytes UInt64,
    written_rows UInt64,
    written_bytes UInt64,
    exception String,
    updated_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (kind, source_date, updated_at)
{mergetree_settings_sql(storage_policy)}
"""


def build_normalized_one_day(client: ClickHouseHttpClient, source_database: str, target_database: str, kind: str, date: str, settings: str) -> QueryProfile:
    if kind == "quotes":
        sql = insert_normalized_quotes_sql(source_database, target_database, date)
    elif kind == "trades":
        sql = insert_normalized_trades_sql(source_database, target_database, date)
    else:
        raise ValueError(f"Unsupported kind: {kind}")
    return run_profiled(client, f"normalize_{kind}_{date}", sql, settings)


def insert_normalized_quotes_sql(source_database: str, target_database: str, date: str) -> str:
    src = quote_ident(source_database)
    dst = quote_ident(target_database)
    return f"""
INSERT INTO {dst}.events_normalized
(
    ticker, source_kind, event_type, source_date, source_file,
    sip_timestamp, sequence_number, participant_timestamp, event_time, event_date,
    bid_price, ask_price, trade_price, bid_size, ask_size, trade_size,
    bid_exchange, ask_exchange, trade_exchange, tape, conditions, correction, id,
    trf_id, trf_timestamp, issue_flags
)
SELECT
    ticker,
    'quotes',
    toUInt8(1),
    source_date,
    source_file,
    sip_timestamp,
    sequence_number,
    participant_timestamp,
    event_time,
    event_date,
    bid_price,
    ask_price,
    toFloat64(0),
    bid_size,
    ask_size,
    toUInt32(0),
    bid_exchange,
    ask_exchange,
    toUInt16(0),
    tape,
    conditions,
    toUInt8(0),
    toUInt64(0),
    toUInt64(0),
    trf_timestamp,
    (
        toUInt32(ticker = '') +
        toUInt32(sip_timestamp = 0) * 2 +
        toUInt32(sequence_number = 0) * 4 +
        toUInt32(bid_price <= 0) * 8 +
        toUInt32(ask_price <= 0) * 16 +
        toUInt32(bid_size = 0) * 32 +
        toUInt32(ask_size = 0) * 64 +
        toUInt32(ask_price < bid_price) * 128
    )
FROM {src}.quotes_raw
WHERE source_date = toDate({sql_string(date)})
"""


def insert_normalized_trades_sql(source_database: str, target_database: str, date: str) -> str:
    src = quote_ident(source_database)
    dst = quote_ident(target_database)
    return f"""
INSERT INTO {dst}.events_normalized
(
    ticker, source_kind, event_type, source_date, source_file,
    sip_timestamp, sequence_number, participant_timestamp, event_time, event_date,
    bid_price, ask_price, trade_price, bid_size, ask_size, trade_size,
    bid_exchange, ask_exchange, trade_exchange, tape, conditions, correction, id,
    trf_id, trf_timestamp, issue_flags
)
SELECT
    ticker,
    'trades',
    toUInt8(2),
    source_date,
    source_file,
    sip_timestamp,
    sequence_number,
    participant_timestamp,
    event_time,
    event_date,
    toFloat64(0),
    toFloat64(0),
    price,
    toUInt32(0),
    toUInt32(0),
    size,
    toUInt16(0),
    toUInt16(0),
    exchange,
    tape,
    conditions,
    correction,
    id,
    trf_id,
    trf_timestamp,
    (
        toUInt32(ticker = '') +
        toUInt32(sip_timestamp = 0) * 2 +
        toUInt32(sequence_number = 0) * 4 +
        toUInt32(price <= 0) * 256 +
        toUInt32(size = 0) * 512
    )
FROM {src}.trades_raw
WHERE source_date = toDate({sql_string(date)})
"""


def rebuild_ticker_month_index(
    client: ClickHouseHttpClient,
    target_database: str,
    start_date: str,
    end_date: str,
    context_events: int,
    train_end_date: str,
    val_end_date: str,
    settings: str,
) -> QueryProfile:
    db = quote_ident(target_database)
    months = months_for_range(start_date, end_date)
    month_list = ", ".join(str(month) for month in months)
    if month_list:
        client.execute(f"ALTER TABLE {db}.event_ticker_month_index DELETE WHERE event_month IN ({month_list})")
    sql = f"""
INSERT INTO {db}.event_ticker_month_index
(
    event_month, ticker, split, first_event_date, last_event_date, first_timestamp,
    last_timestamp, event_count, quote_count, trade_count, has_enough_context, context_events
)
SELECT
    toYYYYMM(event_date) AS event_month,
    ticker,
    multiIf(max(event_date) <= toDate({sql_string(train_end_date)}), 'train',
            max(event_date) <= toDate({sql_string(val_end_date)}), 'validation',
            'test') AS split,
    min(event_date) AS first_event_date,
    max(event_date) AS last_event_date,
    min(sip_timestamp) AS first_timestamp,
    max(sip_timestamp) AS last_timestamp,
    count() AS event_count,
    countIf(event_type = 1) AS quote_count,
    countIf(event_type = 2) AS trade_count,
    toUInt8(count() >= {int(context_events)}) AS has_enough_context,
    toUInt32({int(context_events)}) AS context_events
FROM {db}.events_normalized
WHERE event_date >= toDate({sql_string(start_date)})
  AND event_date <= toDate({sql_string(end_date)})
GROUP BY event_month, ticker
"""
    return run_profiled(client, "rebuild_ticker_month_index", sql, settings)


def latest_manifest_status(client: ClickHouseHttpClient, database: str, kind: str, date: str) -> str:
    try:
        rows = client.query_tsv(
            "SELECT if(countIf(status = 'ok') > 0, 'ok', argMax(status, updated_at)) FROM "
            f"{quote_ident(database)}.feature_build_manifest "
            f"WHERE kind = {sql_string(kind)} "
            f"AND source_date = toDate({sql_string(date)})"
        ).strip().splitlines()
    except Exception:
        return ""
    return rows[0] if rows else ""


def should_skip(status: str, args: argparse.Namespace) -> bool:
    if status == "ok":
        return True
    if status == "failed" and not args.retry_failed:
        return True
    if status == "started" and not args.retry_started:
        return True
    return False


def normalized_rows_present(client: ClickHouseHttpClient, database: str, kind: str, date: str) -> bool:
    try:
        rows = client.query_tsv(
            "SELECT count() FROM "
            f"{quote_ident(database)}.events_normalized "
            f"WHERE source_kind = {sql_string(kind)} AND source_date = toDate({sql_string(date)})"
        ).strip()
    except Exception:
        return False
    return int(rows or "0") > 0


def insert_manifest(
    client: ClickHouseHttpClient,
    database: str,
    kind: str,
    date: str,
    *,
    status: str,
    run_id: str,
    profile: QueryProfile | None = None,
    exception: str = "",
) -> None:
    profile = profile or QueryProfile(label="", query_id="", wall_seconds=0.0)
    db = quote_ident(database)
    client.execute(
        f"""
INSERT INTO {db}.feature_build_manifest
(
    kind, source_date, status, run_id, query_id, wall_seconds, query_duration_ms,
    memory_usage_bytes, read_rows, read_bytes, written_rows, written_bytes, exception
)
VALUES
(
    {sql_string(kind)},
    toDate({sql_string(date)}),
    {sql_string(status)},
    {sql_string(run_id)},
    {sql_string(profile.query_id)},
    {float(profile.wall_seconds)},
    {profile.query_duration_ms or 0},
    {profile.memory_usage_bytes or 0},
    {profile.read_rows or 0},
    {profile.read_bytes or 0},
    {profile.written_rows or 0},
    {profile.written_bytes or 0},
    {sql_string(exception or profile.exception)}
)
"""
    )


def run_profiled(client: ClickHouseHttpClient, label: str, sql: str, settings: str = "") -> QueryProfile:
    query_id = f"features_{label}_{uuid.uuid4().hex}"
    full_sql = sql.rstrip(";") + settings
    print(f"QUERY START {label} query_id={query_id}", flush=True)
    started = time.perf_counter()
    exception = ""
    try:
        client.execute(full_sql, query_id=query_id)
    except Exception as exc:  # noqa: BLE001
        exception = repr(exc)
        print(f"QUERY FAILED {label}: {exception}", flush=True)
    wall_seconds = time.perf_counter() - started
    profile = QueryProfile(label=label, query_id=query_id, wall_seconds=wall_seconds, exception=exception)
    enrich_profile_from_query_log(client, profile)
    if exception:
        raise RuntimeError(f"{label} failed: {exception}")
    return profile


def enrich_profile_from_query_log(client: ClickHouseHttpClient, profile: QueryProfile) -> None:
    try:
        client.execute("SYSTEM FLUSH LOGS")
        rows = client.query_tsv(
            "SELECT query_duration_ms, memory_usage, read_rows, read_bytes, written_rows, written_bytes, exception "
            "FROM system.query_log "
            f"WHERE query_id = {sql_string(profile.query_id)} AND type = 'QueryFinish' "
            "ORDER BY event_time_microseconds DESC LIMIT 1"
        ).strip().splitlines()
        if not rows:
            return
        values = rows[0].split("\t")
        profile.query_duration_ms = parse_int(values[0])
        profile.memory_usage_bytes = parse_int(values[1])
        profile.read_rows = parse_int(values[2])
        profile.read_bytes = parse_int(values[3])
        profile.written_rows = parse_int(values[4])
        profile.written_bytes = parse_int(values[5])
        if len(values) > 6 and values[6]:
            profile.exception = values[6]
    except Exception as exc:  # noqa: BLE001
        print(f"WARN query_log profile unavailable for {profile.label}: {exc!r}", flush=True)


def print_profile_summary(profile: QueryProfile) -> None:
    memory_gib = None if profile.memory_usage_bytes is None else profile.memory_usage_bytes / (1024 ** 3)
    rows_per_second = None
    if profile.written_rows and profile.wall_seconds > 0:
        rows_per_second = profile.written_rows / profile.wall_seconds
    print(
        "QUERY OK "
        f"{profile.label} wall_seconds={profile.wall_seconds:.2f} query_ms={profile.query_duration_ms} "
        f"memory_gib={None if memory_gib is None else round(memory_gib, 3)} "
        f"read_rows={profile.read_rows} written_rows={profile.written_rows} "
        f"rows_per_sec={format_optional_int(None if rows_per_second is None else round(rows_per_second))}",
        flush=True,
    )


def print_progress(completed: int, skipped: int, failed: int, total_tasks: int, started_at: float) -> None:
    elapsed = time.perf_counter() - started_at
    done = completed + skipped + failed
    rate = done / elapsed if elapsed > 0 else 0.0
    remaining = total_tasks - done
    eta_seconds = remaining / rate if rate > 0 else 0.0
    print(f"PROGRESS completed={completed:,} skipped={skipped:,} failed={failed:,} remaining={remaining:,} elapsed_min={elapsed / 60:.1f} eta_min={eta_seconds / 60:.1f}", flush=True)


def print_table_stats(client: ClickHouseHttpClient, database: str) -> None:
    for table in ("events_normalized", "event_ticker_month_index"):
        stats = client.query_tsv(
            "SELECT count(), sum(rows), formatReadableSize(sum(bytes_on_disk)), countDistinct(partition) "
            "FROM system.parts "
            f"WHERE database = {sql_string(database)} AND table = {sql_string(table)} AND active"
        ).strip()
        print(f"TABLE {table}: {stats}", flush=True)


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def query_settings(args: argparse.Namespace) -> str:
    settings: list[str] = [
        "max_threads = " + str(int(args.max_threads)),
        "max_memory_usage = " + str(parse_size_bytes(str(args.max_memory_usage))),
    ]
    return "\nSETTINGS " + ", ".join(settings)


def months_for_range(start_date: str, end_date: str) -> list[int]:
    start_year, start_month = int(start_date[:4]), int(start_date[5:7])
    end_year, end_month = int(end_date[:4]), int(end_date[5:7])
    months: list[int] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(year * 100 + month)
        month += 1
        if month > 12:
            year += 1
            month = 1
    return months


def mergetree_settings_sql(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    policy = storage_policy.strip()
    if policy:
        settings.append(f"storage_policy = {sql_string(policy)}")
    return "SETTINGS " + ", ".join(settings)


def format_optional_int(value: int | None) -> str:
    return "unknown" if value is None else f"{value:,}"


def quote_ident(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def parse_size_bytes(value: str) -> int:
    text = value.strip().upper()
    if text.isdigit():
        return int(text)
    multipliers = {
        "K": 1024,
        "KB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
    }
    for suffix, multiplier in sorted(multipliers.items(), key=lambda item: len(item[0]), reverse=True):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)].strip()) * multiplier)
    raise ValueError(f"Unsupported size: {value}")


if __name__ == "__main__":
    main()

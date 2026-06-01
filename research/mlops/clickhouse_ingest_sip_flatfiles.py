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


DEFAULT_DATABASE = "market_sip_raw"
DEFAULT_CLICKHOUSE_URL = "http://localhost:8123"
CLICKHOUSE_ENDPOINT_ENV = "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL"
CLICKHOUSE_PASSWORD_ENV = "TD__DATABASE__CLICKHOUSE__PASSWORD"
CLICKHOUSE_USER_ENV = "TD__DATABASE__CLICKHOUSE__USER"
CLICKHOUSE_FILE_ROOT_ENV = "TD__DATABASE__CLICKHOUSE__FILE_ROOT"
DEFAULT_FLATFILES_ROOT_WIN = Path("D:/market-data/flatfiles/us_stocks_sip")
DEFAULT_FLATFILES_ROOT_CH = "market-data/flatfiles/us_stocks_sip"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/clickhouse_sip_ingest")

QUOTE_SCHEMA_STRING = (
    "ticker String, "
    "ask_exchange String, "
    "ask_price String, "
    "ask_size String, "
    "bid_exchange String, "
    "bid_price String, "
    "bid_size String, "
    "conditions String, "
    "indicators String, "
    "participant_timestamp String, "
    "sequence_number String, "
    "sip_timestamp String, "
    "tape String, "
    "trf_timestamp String"
)

TRADE_SCHEMA_STRING = (
    "ticker String, "
    "conditions String, "
    "correction String, "
    "exchange String, "
    "id String, "
    "participant_timestamp String, "
    "price String, "
    "sequence_number String, "
    "sip_timestamp String, "
    "size String, "
    "tape String, "
    "trf_id String, "
    "trf_timestamp String"
)

KIND_ROOTS = {
    "quotes": "quotes_v1",
    "trades": "trades_v1",
}


@dataclass(frozen=True, slots=True)
class SourceFile:
    kind: str
    date: str
    windows_path: Path
    clickhouse_path: str
    bytes: int


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
    parser = argparse.ArgumentParser(description="Production ClickHouse ingest for Massive SIP quote/trade flatfiles.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-12-31")
    parser.add_argument("--kinds", default="quotes,trades", help="Comma-separated subset of quotes,trades.")
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--limit-files", type=int, default=0, help="Debug limit after discovery. 0 means all files.")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true", help="Retry files whose latest manifest status is started.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--optimize-final", action="store_true", help="Run OPTIMIZE FINAL on raw tables after ingest. Usually leave off for full-year ingest.")
    return parser.parse_args()


def default_clickhouse_url() -> str:
    return os.environ.get("CLICKHOUSE_URL") or os.environ.get(CLICKHOUSE_ENDPOINT_ENV) or DEFAULT_CLICKHOUSE_URL


def default_clickhouse_user() -> str:
    return os.environ.get("CLICKHOUSE_USER") or os.environ.get(CLICKHOUSE_USER_ENV) or "default"


def default_clickhouse_password() -> str:
    return os.environ.get("CLICKHOUSE_PASSWORD") or os.environ.get(CLICKHOUSE_PASSWORD_ENV) or ""


def default_clickhouse_file_root() -> str:
    return os.environ.get("CLICKHOUSE_FLATFILES_ROOT") or os.environ.get(CLICKHOUSE_FILE_ROOT_ENV) or DEFAULT_FLATFILES_ROOT_CH


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
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_report_path = output_root / f"sip_flatfile_ingest_{run_id}.jsonl"

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    settings = query_settings(args)
    database = args.database.strip()
    flatfiles_root_win = Path(args.flatfiles_root_win)
    flatfiles_root_ch = normalize_clickhouse_file_path(args.flatfiles_root_ch)

    print("=" * 96, flush=True)
    print("Production ClickHouse SIP flatfile ingest", flush=True)
    print(f"database={database}", flush=True)
    print(f"kinds={','.join(kinds)} start_date={args.start_date} end_date={args.end_date}", flush=True)
    print(f"flatfiles_root_win={flatfiles_root_win}", flush=True)
    print(f"flatfiles_root_ch={flatfiles_root_ch}", flush=True)
    print(f"settings={settings.strip()}", flush=True)
    print(f"dry_run={args.dry_run} retry_failed={args.retry_failed} retry_started={args.retry_started}", flush=True)
    print(f"output_report={run_report_path}", flush=True)
    print(f"secret_status={secret_status([CLICKHOUSE_ENDPOINT_ENV, CLICKHOUSE_USER_ENV, CLICKHOUSE_PASSWORD_ENV, CLICKHOUSE_FILE_ROOT_ENV])}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    source_files = discover_source_files(flatfiles_root_win, flatfiles_root_ch, kinds, args.start_date, args.end_date)
    if args.limit_files > 0:
        source_files = source_files[: args.limit_files]
    print(f"Discovered {len(source_files):,} source files", flush=True)
    if not source_files:
        return
    for preview in source_files[:5]:
        print(f"  preview {preview.kind} {preview.date} {preview.windows_path.name} {preview.bytes / (1024 ** 3):.2f} GiB -> {preview.clickhouse_path}", flush=True)

    if args.dry_run:
        return

    create_database_and_tables(client, database)
    completed = 0
    skipped = 0
    failed = 0
    started_at = time.perf_counter()

    for index, source in enumerate(source_files, start=1):
        latest_status = latest_manifest_status(client, database, source)
        if should_skip(latest_status, args):
            skipped += 1
            print(f"[{index:,}/{len(source_files):,}] SKIP {source.kind}:{source.date} status={latest_status}", flush=True)
            continue

        print("=" * 96, flush=True)
        print(f"[{index:,}/{len(source_files):,}] START {source.kind}:{source.date} file={source.windows_path.name} size_gib={source.bytes / (1024 ** 3):.2f}", flush=True)
        insert_manifest(client, database, source, status="started", run_id=run_id)
        try:
            profile = ingest_one_file(client, database, source, settings)
            insert_manifest(client, database, source, status="ok", run_id=run_id, profile=profile)
            append_jsonl(run_report_path, {"source": source_to_json(source), "profile": asdict(profile), "status": "ok"})
            completed += 1
            print_profile_summary(profile)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            insert_manifest(client, database, source, status="failed", run_id=run_id, exception=repr(exc))
            append_jsonl(run_report_path, {"source": source_to_json(source), "status": "failed", "exception": repr(exc)})
            print(f"FAILED {source.kind}:{source.date}: {exc!r}", flush=True)
            raise

        elapsed = time.perf_counter() - started_at
        done = completed + skipped + failed
        rate = done / elapsed if elapsed > 0 else 0.0
        remaining = len(source_files) - done
        eta_seconds = remaining / rate if rate > 0 else 0.0
        print(f"PROGRESS completed={completed:,} skipped={skipped:,} failed={failed:,} remaining={remaining:,} elapsed_min={elapsed / 60:.1f} eta_min={eta_seconds / 60:.1f}", flush=True)

    if args.optimize_final:
        for kind in kinds:
            table = "quotes_raw" if kind == "quotes" else "trades_raw"
            profile = run_profiled(client, f"optimize_{table}", f"OPTIMIZE TABLE {quote_ident(database)}.{quote_ident(table)} FINAL")
            append_jsonl(run_report_path, {"status": "ok", "operation": f"optimize_{table}", "profile": asdict(profile)})
            print_profile_summary(profile)

    print("=" * 96, flush=True)
    print(f"DONE completed={completed:,} skipped={skipped:,} failed={failed:,}", flush=True)
    print(f"report={run_report_path}", flush=True)
    print_table_stats(client, database)
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
    invalid = [kind for kind in kinds if kind not in KIND_ROOTS]
    if invalid:
        raise ValueError(f"Invalid kinds: {invalid}; expected subset of {sorted(KIND_ROOTS)}")
    return kinds


def discover_source_files(root_win: Path, root_ch: str, kinds: list[str], start_date: str, end_date: str) -> list[SourceFile]:
    files: list[SourceFile] = []
    for kind in kinds:
        folder = root_win / KIND_ROOTS[kind]
        for path in sorted(folder.glob("*/*/*.csv.gz")):
            date = path.name.replace(".csv.gz", "")
            if start_date <= date <= end_date:
                files.append(
                    SourceFile(
                        kind=kind,
                        date=date,
                        windows_path=path,
                        clickhouse_path=windows_path_to_clickhouse_path(path, root_win, root_ch),
                        bytes=path.stat().st_size,
                    )
                )
    return sorted(files, key=lambda item: (item.date, item.kind, str(item.windows_path)))


def create_database_and_tables(client: ClickHouseHttpClient, database: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    client.execute(create_quotes_table_sql(database))
    client.execute(create_trades_table_sql(database))
    client.execute(create_manifest_table_sql(database))


def create_quotes_table_sql(database: str) -> str:
    db = quote_ident(database)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.quotes_raw
(
    ticker LowCardinality(String),
    ask_exchange UInt16,
    ask_price Float64,
    ask_size UInt32,
    bid_exchange UInt16,
    bid_price Float64,
    bid_size UInt32,
    conditions String,
    indicators String,
    participant_timestamp UInt64,
    sequence_number UInt64,
    sip_timestamp UInt64,
    tape UInt8,
    trf_timestamp UInt64,
    source_date Date,
    source_file LowCardinality(String),
    event_time DateTime64(9, 'UTC') MATERIALIZED fromUnixTimestamp64Nano(toInt64(sip_timestamp)),
    event_date Date MATERIALIZED toDate(event_time),
    ingested_at DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp, sequence_number)
SETTINGS index_granularity = 8192
"""


def create_trades_table_sql(database: str) -> str:
    db = quote_ident(database)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.trades_raw
(
    ticker LowCardinality(String),
    conditions String,
    correction UInt8,
    exchange UInt16,
    id UInt64,
    participant_timestamp UInt64,
    price Float64,
    sequence_number UInt64,
    sip_timestamp UInt64,
    size UInt32,
    tape UInt8,
    trf_id UInt64,
    trf_timestamp UInt64,
    source_date Date,
    source_file LowCardinality(String),
    event_time DateTime64(9, 'UTC') MATERIALIZED fromUnixTimestamp64Nano(toInt64(sip_timestamp)),
    event_date Date MATERIALIZED toDate(event_time),
    ingested_at DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp, sequence_number)
SETTINGS index_granularity = 8192
"""


def create_manifest_table_sql(database: str) -> str:
    db = quote_ident(database)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.ingest_manifest
(
    kind LowCardinality(String),
    source_date Date,
    source_file String,
    source_path_ch String,
    file_bytes UInt64,
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
ORDER BY (kind, source_date, source_file, updated_at)
SETTINGS index_granularity = 8192
"""


def ingest_one_file(client: ClickHouseHttpClient, database: str, source: SourceFile, settings: str) -> QueryProfile:
    if source.kind == "quotes":
        sql = insert_quotes_sql(database, source)
    elif source.kind == "trades":
        sql = insert_trades_sql(database, source)
    else:
        raise ValueError(f"Unsupported kind: {source.kind}")
    label = f"insert_{source.kind}_{source.date}"
    return run_profiled(client, label, sql, settings)


def insert_quotes_sql(database: str, source: SourceFile) -> str:
    db = quote_ident(database)
    return f"""
INSERT INTO {db}.quotes_raw
(
    ticker,
    ask_exchange,
    ask_price,
    ask_size,
    bid_exchange,
    bid_price,
    bid_size,
    conditions,
    indicators,
    participant_timestamp,
    sequence_number,
    sip_timestamp,
    tape,
    trf_timestamp,
    source_date,
    source_file
)
SELECT
    ticker,
    toUInt16OrZero(ask_exchange),
    toFloat64OrZero(ask_price),
    toUInt32OrZero(ask_size),
    toUInt16OrZero(bid_exchange),
    toFloat64OrZero(bid_price),
    toUInt32OrZero(bid_size),
    conditions,
    indicators,
    toUInt64OrZero(participant_timestamp),
    toUInt64OrZero(sequence_number),
    toUInt64OrZero(sip_timestamp),
    toUInt8OrZero(tape),
    toUInt64OrZero(trf_timestamp),
    toDate({sql_string(source.date)}),
    {sql_string(source.windows_path.name)}
FROM file({sql_string(source.clickhouse_path)}, 'CSVWithNames', {sql_string(QUOTE_SCHEMA_STRING)})
"""


def insert_trades_sql(database: str, source: SourceFile) -> str:
    db = quote_ident(database)
    return f"""
INSERT INTO {db}.trades_raw
(
    ticker,
    conditions,
    correction,
    exchange,
    id,
    participant_timestamp,
    price,
    sequence_number,
    sip_timestamp,
    size,
    tape,
    trf_id,
    trf_timestamp,
    source_date,
    source_file
)
SELECT
    ticker,
    conditions,
    toUInt8OrZero(correction),
    toUInt16OrZero(exchange),
    toUInt64OrZero(id),
    toUInt64OrZero(participant_timestamp),
    toFloat64OrZero(price),
    toUInt64OrZero(sequence_number),
    toUInt64OrZero(sip_timestamp),
    toUInt32OrZero(size),
    toUInt8OrZero(tape),
    toUInt64OrZero(trf_id),
    toUInt64OrZero(trf_timestamp),
    toDate({sql_string(source.date)}),
    {sql_string(source.windows_path.name)}
FROM file({sql_string(source.clickhouse_path)}, 'CSVWithNames', {sql_string(TRADE_SCHEMA_STRING)})
"""


def run_profiled(client: ClickHouseHttpClient, label: str, sql: str, settings: str = "") -> QueryProfile:
    query_id = f"sip_{label}_{uuid.uuid4().hex}"
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


def latest_manifest_status(client: ClickHouseHttpClient, database: str, source: SourceFile) -> str:
    try:
        rows = client.query_tsv(
            "SELECT status FROM "
            f"{quote_ident(database)}.ingest_manifest "
            f"WHERE kind = {sql_string(source.kind)} "
            f"AND source_date = toDate({sql_string(source.date)}) "
            f"AND source_file = {sql_string(source.windows_path.name)} "
            "ORDER BY updated_at DESC LIMIT 1"
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


def insert_manifest(
    client: ClickHouseHttpClient,
    database: str,
    source: SourceFile,
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
INSERT INTO {db}.ingest_manifest
(
    kind, source_date, source_file, source_path_ch, file_bytes, status, run_id, query_id,
    wall_seconds, query_duration_ms, memory_usage_bytes, read_rows, read_bytes,
    written_rows, written_bytes, exception
)
VALUES
(
    {sql_string(source.kind)},
    toDate({sql_string(source.date)}),
    {sql_string(source.windows_path.name)},
    {sql_string(source.clickhouse_path)},
    {int(source.bytes)},
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
        f"rows_per_sec={None if rows_per_second is None else round(rows_per_second):,}",
        flush=True,
    )


def print_table_stats(client: ClickHouseHttpClient, database: str) -> None:
    for table in ("quotes_raw", "trades_raw"):
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


def source_to_json(source: SourceFile) -> dict[str, Any]:
    return {
        "kind": source.kind,
        "date": source.date,
        "windows_path": str(source.windows_path),
        "clickhouse_path": source.clickhouse_path,
        "bytes": source.bytes,
    }


def query_settings(args: argparse.Namespace) -> str:
    settings: list[str] = [
        "input_format_csv_empty_as_default = 1",
        "input_format_skip_unknown_fields = 1",
        "date_time_input_format = 'best_effort'",
    ]
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


def windows_path_to_clickhouse_path(path: Path, flatfiles_root_win: Path, flatfiles_root_ch: str) -> str:
    root = flatfiles_root_win.resolve()
    relative = path.resolve().relative_to(root)
    return normalize_clickhouse_file_path(flatfiles_root_ch).rstrip("/") + "/" + relative.as_posix()


def normalize_clickhouse_file_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized == "/":
        return normalized
    return normalized.rstrip("/")


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

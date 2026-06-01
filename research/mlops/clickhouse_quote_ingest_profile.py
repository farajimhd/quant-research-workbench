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


DEFAULT_DATABASE_PREFIX = "qrw_quote_ingest_profile"
DEFAULT_CLICKHOUSE_URL = "http://localhost:8123"
CLICKHOUSE_ENDPOINT_ENV = "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL"
CLICKHOUSE_PASSWORD_ENV = "TD__DATABASE__CLICKHOUSE__PASSWORD"
CLICKHOUSE_USER_ENV = "TD__DATABASE__CLICKHOUSE__USER"
CLICKHOUSE_FILE_ROOT_ENV = "TD__DATABASE__CLICKHOUSE__FILE_ROOT"
DEFAULT_FLATFILES_ROOT_WIN = Path("D:/market-data/flatfiles/us_stocks_sip")
DEFAULT_FLATFILES_ROOT_CH = "/mnt/d/market-data/flatfiles/us_stocks_sip"
DEFAULT_USER_FILES_RELATIVE_FLATFILES_ROOT_CH = "market-data/flatfiles/us_stocks_sip"
USER_FILES_RELATIVE_SYMBOL_ROOT_CH = "us_stocks_sip"
USER_FILES_RELATIVE_FLATFILES_ALIAS_CH = "flatfiles/us_stocks_sip"
USER_FILES_RELATIVE_FLATFILE_ALIAS_CH = "flatfile/us_stocks_sip"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/clickhouse_ingest_profile")
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


@dataclass(frozen=True, slots=True)
class ClickHousePathMapping:
    name: str
    flatfiles_root_ch: str
    quote_files_ch: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile ClickHouse loading of Massive SIP quote CSV files.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default="", help="ClickHouse database name. Defaults to a unique qrw_quote_ingest_profile_<timestamp> database.")
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument(
        "--flatfiles-root-ch",
        default=default_clickhouse_file_root(),
        help=(
            "ClickHouse-visible flatfiles root. Use a relative path such as "
            "'market-data/flatfiles/us_stocks_sip' when ClickHouse user_files_path "
            "is D:/ or /mnt/d. Absolute /mnt/... paths are still supported."
        ),
    )
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--dates", default="", help="Comma-separated YYYY-MM-DD dates. Defaults to first three discovered 2025 quote files.")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--drop-database", action="store_true", help="Drop the target database before running. Requires DROP DATABASE permission.")
    parser.add_argument("--max-discovery-files", type=int, default=3)
    parser.add_argument("--max-memory-usage", default="0", help="Optional ClickHouse max_memory_usage setting, e.g. 64G or bytes. 0 leaves unlimited/default.")
    parser.add_argument("--max-threads", type=int, default=0, help="Optional per-query ClickHouse max_threads setting.")
    parser.add_argument("--path-preflight-only", action="store_true", help="Only test ClickHouse file() path access and exit before creating tables.")
    return parser.parse_args()


def default_clickhouse_url() -> str:
    return (
        os.environ.get("CLICKHOUSE_URL")
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or DEFAULT_CLICKHOUSE_URL
    )


def default_clickhouse_user() -> str:
    return os.environ.get("CLICKHOUSE_USER") or os.environ.get(CLICKHOUSE_USER_ENV) or "default"


def default_clickhouse_password() -> str:
    return os.environ.get("CLICKHOUSE_PASSWORD") or os.environ.get(CLICKHOUSE_PASSWORD_ENV) or ""


def default_clickhouse_file_root() -> str:
    return (
        os.environ.get("CLICKHOUSE_FLATFILES_ROOT")
        or os.environ.get(CLICKHOUSE_FILE_ROOT_ENV)
        or DEFAULT_USER_FILES_RELATIVE_FLATFILES_ROOT_CH
    )


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
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    database = args.database.strip() or f"{DEFAULT_DATABASE_PREFIX}_{run_stamp}"
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"quote_ingest_profile_{run_stamp}.json"
    preflight_log_path = output_root / f"clickhouse_path_preflight_{run_stamp}.log"

    dates = parse_dates(args)
    quote_files = [quote_file_for_date(Path(args.flatfiles_root_win), date) for date in dates]
    missing = [str(path) for path in quote_files if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing quote files:\n" + "\n".join(missing))

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    settings = query_settings(args)
    profiles: list[QueryProfile] = []
    snapshots: list[dict[str, Any]] = []
    path_mapping = select_readable_path_mapping(
        client,
        quote_files,
        args.flatfiles_root_win,
        args.flatfiles_root_ch,
        settings,
        preflight_log_path,
    )
    quote_files_ch = path_mapping.quote_files_ch
    if args.path_preflight_only:
        print("=" * 96, flush=True)
        print(f"PATH PREFLIGHT OK: {path_mapping.name}", flush=True)
        print(f"first_quote_file={quote_files_ch[0]}", flush=True)
        print(f"preflight_log={preflight_log_path}", flush=True)
        print("=" * 96, flush=True)
        return

    print("=" * 96, flush=True)
    print("ClickHouse quote ingest profile", flush=True)
    print(f"clickhouse_url={args.clickhouse_url}", flush=True)
    print(f"clickhouse_user={args.user}", flush=True)
    print(f"clickhouse_password_present={bool(args.password)}", flush=True)
    print(f"secret_status={secret_status([CLICKHOUSE_ENDPOINT_ENV, CLICKHOUSE_USER_ENV, CLICKHOUSE_PASSWORD_ENV, CLICKHOUSE_FILE_ROOT_ENV])}", flush=True)
    print(f"database={database}", flush=True)
    print(f"selected_file_path_mapping={path_mapping.name} flatfiles_root_ch={path_mapping.flatfiles_root_ch}", flush=True)
    for path_win, path_ch in zip(quote_files, quote_files_ch):
        print(f"quote_file={path_win} size_gb={path_win.stat().st_size / (1024 ** 3):.2f} clickhouse_path={path_ch}", flush=True)
    print("=" * 96, flush=True)

    snapshots.append({"label": "before", "memory": read_memory_snapshot(client)})
    if args.drop_database:
        profiles.append(run_profiled(client, "drop_database", f"DROP DATABASE IF EXISTS {quote_ident(database)}"))
    profiles.append(run_profiled(client, "create_database", f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}"))
    profiles.append(run_profiled(client, "create_table", create_table_sql(database)))
    snapshots.append({"label": "after_create", "memory": read_memory_snapshot(client)})

    for index, (path_win, path_ch) in enumerate(zip(quote_files, quote_files_ch), start=1):
        label = f"insert_quotes_{index}_{path_win.stem.replace('.', '_')}"
        profiles.append(run_profiled(client, label, insert_quotes_sql(database, path_ch, path_win.name), settings))
        print_table_stats(client, database, f"after {path_win.name}")
        snapshots.append({"label": label, "memory": read_memory_snapshot(client)})

    profiles.append(run_profiled(client, "optimize_final", f"OPTIMIZE TABLE {quote_ident(database)}.quotes_raw FINAL"))
    print_table_stats(client, database, "after optimize final")
    snapshots.append({"label": "after_optimize", "memory": read_memory_snapshot(client)})

    count_rows = client.query_tsv(f"SELECT count() FROM {quote_ident(database)}.quotes_raw").strip()
    active_parts = client.query_tsv(
        "SELECT partition, count(), sum(rows), formatReadableSize(sum(bytes_on_disk)) "
        "FROM system.parts "
        f"WHERE database = {sql_string(database)} AND table = 'quotes_raw' AND active "
        "GROUP BY partition ORDER BY partition"
    )
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "database": database,
        "clickhouse_url": args.clickhouse_url,
        "clickhouse_user": args.user,
        "clickhouse_password_present": bool(args.password),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "secret_status": secret_status([CLICKHOUSE_ENDPOINT_ENV, CLICKHOUSE_USER_ENV, CLICKHOUSE_PASSWORD_ENV, CLICKHOUSE_FILE_ROOT_ENV]),
        "selected_file_path_mapping": asdict(path_mapping),
        "quote_files": [{"windows_path": str(path), "clickhouse_path": path_ch, "bytes": path.stat().st_size} for path, path_ch in zip(quote_files, quote_files_ch)],
        "settings": settings,
        "total_rows": int(count_rows) if count_rows else 0,
        "profiles": [asdict(profile) for profile in profiles],
        "memory_snapshots": snapshots,
        "active_parts_tsv": active_parts,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("=" * 96, flush=True)
    print(f"Report written: {report_path}", flush=True)
    print(f"Total rows loaded: {report['total_rows']:,}", flush=True)
    print("Profiles:", flush=True)
    for profile in profiles:
        memory_gb = None if profile.memory_usage_bytes is None else profile.memory_usage_bytes / (1024 ** 3)
        print(
            f"  {profile.label}: wall={profile.wall_seconds:.2f}s "
            f"query_ms={profile.query_duration_ms} memory_gb={memory_gb if memory_gb is None else round(memory_gb, 3)} "
            f"read_rows={profile.read_rows} written_rows={profile.written_rows} exception={profile.exception[:120]}",
            flush=True,
        )
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
        data = sql.encode("utf-8")
        req = request.Request(url, data=data, method="POST")
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


def run_profiled(client: ClickHouseHttpClient, label: str, sql: str, settings: str = "") -> QueryProfile:
    query_id = f"qrw_{label}_{uuid.uuid4().hex}"
    full_sql = sql.rstrip(";") + settings
    print(f"START {label} query_id={query_id}", flush=True)
    started = time.perf_counter()
    exception = ""
    try:
        client.execute(full_sql, query_id=query_id)
    except Exception as exc:  # noqa: BLE001
        exception = repr(exc)
        print(f"FAILED {label}: {exception}", flush=True)
    wall_seconds = time.perf_counter() - started
    print(f"FINISH {label} wall_seconds={wall_seconds:.2f}", flush=True)
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


def create_table_sql(database: str) -> str:
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
    source_file LowCardinality(String),
    ingested_at DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY intDiv(sip_timestamp, 1000000000 * 86400 * 31)
ORDER BY (ticker, sip_timestamp, sequence_number)
SETTINGS index_granularity = 8192
"""


def insert_quotes_sql(database: str, clickhouse_path: str, source_file: str) -> str:
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
    {sql_string(source_file)}
FROM file({sql_string(clickhouse_path)}, 'CSVWithNames', {sql_string(QUOTE_SCHEMA_STRING)})
"""


def read_quotes_probe_sql(clickhouse_path: str) -> str:
    return f"""
SELECT ticker
FROM file({sql_string(clickhouse_path)}, 'CSVWithNames', {sql_string(QUOTE_SCHEMA_STRING)})
LIMIT 1
"""


def query_settings(args: argparse.Namespace) -> str:
    settings: list[str] = [
        "input_format_csv_empty_as_default = 1",
        "input_format_csv_skip_unknown_fields = 1",
        "date_time_input_format = 'best_effort'",
    ]
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


def read_memory_snapshot(client: ClickHouseHttpClient) -> list[dict[str, Any]]:
    try:
        text = client.query_tsv(
            "SELECT metric, value FROM system.asynchronous_metrics "
            "WHERE metric ILIKE '%memory%' OR metric ILIKE '%Memory%' ORDER BY metric"
        )
        rows = []
        for line in text.strip().splitlines():
            if not line:
                continue
            metric, value = line.split("\t", 1)
            rows.append({"metric": metric, "value": float(value)})
        return rows
    except Exception as exc:  # noqa: BLE001
        return [{"metric": "snapshot_error", "value": str(exc)}]


def print_table_stats(client: ClickHouseHttpClient, database: str, label: str) -> None:
    stats = client.query_tsv(
        "SELECT count(), sum(rows), formatReadableSize(sum(bytes_on_disk)), countDistinct(partition) "
        "FROM system.parts "
        f"WHERE database = {sql_string(database)} AND table = 'quotes_raw' AND active"
    ).strip()
    rows = client.query_tsv(f"SELECT count() FROM {quote_ident(database)}.quotes_raw").strip()
    print(f"TABLE {label}: rows={rows} parts_stats={stats}", flush=True)


def select_readable_path_mapping(
    client: ClickHouseHttpClient,
    quote_files_win: list[Path],
    flatfiles_root_win: str,
    configured_flatfiles_root_ch: str,
    settings: str,
    log_path: Path,
) -> ClickHousePathMapping:
    candidates = build_path_mappings(quote_files_win, flatfiles_root_win, configured_flatfiles_root_ch)
    errors: list[dict[str, str]] = []
    log_preflight("Testing ClickHouse server-side file() access with first real quote file.", log_path)
    log_preflight(f"windows_first_quote_file={quote_files_win[0]}", log_path)
    log_preflight(f"configured_flatfiles_root_ch={configured_flatfiles_root_ch}", log_path)
    log_preflight(f"candidate_count={len(candidates)}", log_path)
    for candidate in candidates:
        probe_path = candidate.quote_files_ch[0]
        sql = read_quotes_probe_sql(probe_path).rstrip(";") + settings
        log_preflight(f"trying {candidate.name}: {probe_path}", log_path)
        try:
            first_ticker = client.query_tsv(sql).strip()
            if first_ticker:
                log_preflight(f"ClickHouse file() path mapping works: {candidate.name} -> {probe_path}; first_ticker={first_ticker}", log_path)
                return candidate
            errors.append({"name": candidate.name, "path": probe_path, "error": "query_returned_no_rows"})
            log_preflight(f"failed {candidate.name}: query_returned_no_rows", log_path)
        except Exception as exc:  # noqa: BLE001
            errors.append({"name": candidate.name, "path": probe_path, "error": repr(exc)})
            log_preflight(f"failed {candidate.name}: {exc!r}", log_path)
    log_preflight("ClickHouse file() path preflight failed for all candidate paths:", log_path)
    for item in errors:
        log_preflight(f"  {item['name']}: {item['path']} -> {item['error'][:500]}", log_path)
    log_preflight(f"preflight_log={log_path}", log_path)
    print_server_context(client)
    raise RuntimeError(
        "ClickHouse cannot read the real quote CSV through file(). "
        "The server must see the file path itself, and file() may be restricted to user_files_path. "
        f"Either set {CLICKHOUSE_FILE_ROOT_ENV} / CLICKHOUSE_FLATFILES_ROOT to the path visible to the ClickHouse server, "
        f"or configure ClickHouse user_files_path / container volume so D:/market-data/flatfiles/us_stocks_sip is visible. "
        f"See path preflight log: {log_path}"
    )


def build_path_mappings(
    quote_files_win: list[Path],
    flatfiles_root_win: str,
    configured_flatfiles_root_ch: str,
) -> list[ClickHousePathMapping]:
    mappings: list[ClickHousePathMapping] = []
    seen: set[tuple[str, str]] = set()

    def append_mapping(name: str, root_ch: str, mapper) -> None:
        mapping = ClickHousePathMapping(
            name=name,
            flatfiles_root_ch=root_ch,
            quote_files_ch=[mapper(path) for path in quote_files_win],
        )
        key = (mapping.name, mapping.quote_files_ch[0] if mapping.quote_files_ch else "")
        if key not in seen:
            seen.add(key)
            mappings.append(mapping)

    configured_root = normalize_clickhouse_file_path(configured_flatfiles_root_ch)
    append_mapping(
        "configured_root",
        configured_root,
        lambda path: windows_path_to_clickhouse_path(path, flatfiles_root_win, configured_root),
    )

    default_relative_root = DEFAULT_USER_FILES_RELATIVE_FLATFILES_ROOT_CH
    if configured_root.rstrip("/") != default_relative_root.rstrip("/"):
        append_mapping(
            "default_user_files_relative_root",
            default_relative_root,
            lambda path: windows_path_to_clickhouse_path(path, flatfiles_root_win, default_relative_root),
        )

    if configured_root.rstrip("/") != USER_FILES_RELATIVE_FLATFILES_ALIAS_CH.rstrip("/"):
        append_mapping(
            "user_files_relative_flatfiles_alias",
            USER_FILES_RELATIVE_FLATFILES_ALIAS_CH,
            lambda path: windows_path_to_clickhouse_path(path, flatfiles_root_win, USER_FILES_RELATIVE_FLATFILES_ALIAS_CH),
        )

    if configured_root.rstrip("/") != USER_FILES_RELATIVE_FLATFILE_ALIAS_CH.rstrip("/"):
        append_mapping(
            "user_files_relative_flatfile_alias",
            USER_FILES_RELATIVE_FLATFILE_ALIAS_CH,
            lambda path: windows_path_to_clickhouse_path(path, flatfiles_root_win, USER_FILES_RELATIVE_FLATFILE_ALIAS_CH),
        )

    if configured_root.rstrip("/") != USER_FILES_RELATIVE_SYMBOL_ROOT_CH.rstrip("/"):
        append_mapping(
            "user_files_relative_symbol_root",
            USER_FILES_RELATIVE_SYMBOL_ROOT_CH,
            lambda path: windows_path_to_clickhouse_path(path, flatfiles_root_win, USER_FILES_RELATIVE_SYMBOL_ROOT_CH),
        )

    for name, root_win in (
        ("relative_from_flatfiles_parent", Path("D:/market-data/flatfiles")),
        ("relative_from_market_data_root", Path("D:/market-data")),
        ("relative_from_drive_root", Path("D:/")),
        ("relative_from_flatfiles_root", Path(flatfiles_root_win)),
    ):
        append_mapping(name, "", lambda path, root=root_win: relative_from_root_path(path, root))

    default_absolute_root = DEFAULT_FLATFILES_ROOT_CH
    if configured_root.rstrip("/") != default_absolute_root.rstrip("/"):
        append_mapping(
            "default_mnt_d_root",
            default_absolute_root,
            lambda path: windows_path_to_clickhouse_path(path, flatfiles_root_win, default_absolute_root),
        )

    append_mapping("windows_drive_path", "", windows_drive_clickhouse_path)
    append_mapping("relative_from_drive", "", relative_from_drive_path)

    return mappings


def log_preflight(message: str, log_path: Path) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def normalize_clickhouse_file_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized == "/":
        return normalized
    return normalized.rstrip("/")


def is_absolute_clickhouse_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("/") or (len(normalized) >= 3 and normalized[1:3] == ":/")


def print_server_context(client: ClickHouseHttpClient) -> None:
    diagnostics = {
        "version_user": "SELECT version(), currentUser()",
        "file_settings": (
            "SELECT name, value FROM system.settings "
            "WHERE name ILIKE '%file%' OR name ILIKE '%path%' "
            "ORDER BY name LIMIT 50"
        ),
    }
    for label, sql in diagnostics.items():
        try:
            print(f"ClickHouse diagnostic {label}:", flush=True)
            print(client.query_tsv(sql).strip(), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"ClickHouse diagnostic {label} failed: {exc!r}", flush=True)


def parse_dates(args: argparse.Namespace) -> list[str]:
    if args.dates.strip():
        return [date.strip() for date in args.dates.split(",") if date.strip()]
    root = Path(args.flatfiles_root_win) / "quotes_v1"
    paths = sorted(root.glob("*/*/*.csv.gz"))
    dates = []
    for path in paths:
        date = path.name.replace(".csv.gz", "")
        if args.start_date <= date <= args.end_date:
            dates.append(date)
        if len(dates) >= args.max_discovery_files:
            break
    if not dates:
        raise FileNotFoundError(f"No quote files discovered under {root} for {args.start_date} -> {args.end_date}")
    return dates


def quote_file_for_date(flatfiles_root: Path, date: str) -> Path:
    return flatfiles_root / "quotes_v1" / date[:4] / date[5:7] / f"{date}.csv.gz"


def windows_path_to_clickhouse_path(path: Path, flatfiles_root_win: str, flatfiles_root_ch: str) -> str:
    flatfiles_root_ch = normalize_clickhouse_file_path(flatfiles_root_ch)
    path = path.resolve()
    root = Path(flatfiles_root_win).resolve()
    try:
        relative = path.relative_to(root)
        if not flatfiles_root_ch:
            return relative.as_posix()
        return flatfiles_root_ch.rstrip("/") + "/" + relative.as_posix()
    except ValueError:
        drive = path.drive.rstrip(":").lower()
        if not drive:
            return path.as_posix()
        return f"/mnt/{drive}" + path.as_posix()[2:]


def windows_drive_clickhouse_path(path: Path) -> str:
    return path.resolve().as_posix()


def relative_from_drive_path(path: Path) -> str:
    resolved = path.resolve()
    if resolved.drive:
        return resolved.as_posix()[3:]
    return resolved.as_posix().lstrip("/")


def relative_from_root_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return relative_from_drive_path(path)


def quote_ident(value: str) -> str:
    escaped = value.replace("`", "``")
    return f"`{escaped}`"


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

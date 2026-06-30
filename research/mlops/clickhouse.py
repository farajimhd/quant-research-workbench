from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DATABASE = "market_sip_raw"

DEFAULT_CLICKHOUSE_URL = "http://localhost:8123"

CLICKHOUSE_URL_ENV = "CLICKHOUSE_URL"

CLICKHOUSE_WORKSTATION_PASSWORD_ENV = "CLICKHOUSE_WORKSTATION_PASSWORD"

CLICKHOUSE_WORKSTATION_USER_ENV = "CLICKHOUSE_WORKSTATION_USER"

CLICKHOUSE_PASSWORD_SIMPLE_ENV = "CLICKHOUSE_PASSWORD"

CLICKHOUSE_USER_SIMPLE_ENV = "CLICKHOUSE_USER"

CLICKHOUSE_ENDPOINT_ENV = "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL"

CLICKHOUSE_PASSWORD_ENV = "TD__DATABASE__CLICKHOUSE__PASSWORD"

CLICKHOUSE_USER_ENV = "TD__DATABASE__CLICKHOUSE__USER"

REAL_LIVE_CLICKHOUSE_WRITE_URL_ENV = "REAL_LIVE_CLICKHOUSE_WRITE_URL"

REAL_LIVE_CLICKHOUSE_WRITE_USER_ENV = "REAL_LIVE_CLICKHOUSE_WRITE_USER"

REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD_ENV = "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"

CLICKHOUSE_FILE_ROOT_ENV = "TD__DATABASE__CLICKHOUSE__FILE_ROOT"

CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV = "CLICKHOUSE_HISTORICAL_STORAGE_POLICY"

CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV = "CLICKHOUSE_STORAGE_POLICY"

CLICKHOUSE_STORAGE_POLICY_ENV = "TD__DATABASE__CLICKHOUSE__STORAGE_POLICY"

HISTORICAL_CLICKHOUSE_DATABASE_ENV = "HISTORICAL_CLICKHOUSE_DATABASE_HDD_STORAGE_POLICY"

DEFAULT_FLATFILES_ROOT_WIN = Path("D:/market-data/flatfiles/us_stocks_sip")

DEFAULT_CLICKHOUSE_FILE_ROOT = "/mnt/d/market-data"

CLICKHOUSE_FILE_ROOT_PREFIXES = (
    "/mnt/g/market-data/workstation-d/",
    "/mnt/g/market-data/",
    "market-data/workstation-d/",
    "market-data/",
    "workstation-d/",
)

DEFAULT_FLATFILES_ROOT_CH = "/mnt/d/market-data/flatfiles/us_stocks_sip"

DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/clickhouse_sip_ingest")

DEFAULT_PREFLIGHT_PROCESSES = 4

DEFAULT_MANIFEST_TABLE = "ingest_manifest"

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

@dataclass
class SourceFile:
    kind: str
    date: str
    windows_path: Path
    clickhouse_path: str
    bytes: int

@dataclass
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

@dataclass
class RowStats:
    rows: int
    min_sip_timestamp: int
    max_sip_timestamp: int

@dataclass
class SourcePreflight:
    source_key: str
    stats: RowStats
    wall_seconds: float

def default_clickhouse_url() -> str:
    return (
        os.environ.get(REAL_LIVE_CLICKHOUSE_WRITE_URL_ENV)
        or os.environ.get(CLICKHOUSE_URL_ENV)
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or DEFAULT_CLICKHOUSE_URL
    )

def default_clickhouse_user() -> str:
    return (
        os.environ.get(REAL_LIVE_CLICKHOUSE_WRITE_USER_ENV)
        or os.environ.get(CLICKHOUSE_WORKSTATION_USER_ENV)
        or os.environ.get(CLICKHOUSE_USER_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_USER_ENV)
        or "default"
    )

def default_clickhouse_password() -> str:
    return (
        os.environ.get(REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD_ENV)
        or os.environ.get(CLICKHOUSE_WORKSTATION_PASSWORD_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_ENV)
        or ""
    )

def default_database() -> str:
    return os.environ.get(HISTORICAL_CLICKHOUSE_DATABASE_ENV) or DEFAULT_DATABASE

def default_clickhouse_file_root() -> str:
    return os.environ.get("CLICKHOUSE_FLATFILES_ROOT") or os.environ.get(CLICKHOUSE_FILE_ROOT_ENV) or DEFAULT_FLATFILES_ROOT_CH

def default_storage_policy() -> str:
    return (
        os.environ.get(CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV)
        or os.environ.get(CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_STORAGE_POLICY_ENV)
        or ""
    )

def default_preflight_processes() -> int:
    return int(os.environ.get("SIP_INGEST_PREFLIGHT_PROCESSES") or DEFAULT_PREFLIGHT_PROCESSES)

def clickhouse_env_status_keys() -> list[str]:
    return [
        CLICKHOUSE_URL_ENV,
        REAL_LIVE_CLICKHOUSE_WRITE_URL_ENV,
        REAL_LIVE_CLICKHOUSE_WRITE_USER_ENV,
        REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD_ENV,
        CLICKHOUSE_WORKSTATION_USER_ENV,
        CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
        CLICKHOUSE_USER_SIMPLE_ENV,
        CLICKHOUSE_PASSWORD_SIMPLE_ENV,
        HISTORICAL_CLICKHOUSE_DATABASE_ENV,
        CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV,
        CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV,
        CLICKHOUSE_ENDPOINT_ENV,
        CLICKHOUSE_USER_ENV,
        CLICKHOUSE_PASSWORD_ENV,
        CLICKHOUSE_FILE_ROOT_ENV,
    ]

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
        query = sql.strip().rstrip(";").rstrip()
        if re.search(r"\bFORMAT\s+[A-Za-z0-9_]+\s*$", query, flags=re.IGNORECASE):
            return self.execute(query)
        return self.execute(query + "\nFORMAT TSV")

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

def source_identity(source: SourceFile) -> str:
    return f"{source.kind}|{source.date}|{source.windows_path.name}"

def preflight_source_files(source_files: list[SourceFile], processes: int) -> dict[str, SourcePreflight]:
    print("=" * 96, flush=True)
    print(f"START source preflight files={len(source_files):,} processes={processes}", flush=True)
    started_at = time.perf_counter()
    payloads = [(source_identity(source), str(source.windows_path)) for source in source_files]
    results: dict[str, SourcePreflight] = {}
    if processes <= 1:
        for index, payload in enumerate(payloads, start=1):
            result = preflight_source_worker(payload)
            results[result.source_key] = result
            print_preflight_progress(index, len(payloads), result, started_at)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=processes) as executor:
            future_to_payload = {executor.submit(preflight_source_worker, payload): payload for payload in payloads}
            for index, future in enumerate(concurrent.futures.as_completed(future_to_payload), start=1):
                result = future.result()
                results[result.source_key] = result
                print_preflight_progress(index, len(payloads), result, started_at)
    elapsed = time.perf_counter() - started_at
    print(f"DONE source preflight files={len(results):,} elapsed_seconds={elapsed:.1f}", flush=True)
    print("=" * 96, flush=True)
    return results

def preflight_source_worker(payload: tuple[str, str]) -> SourcePreflight:
    source_key, path_text = payload
    started_at = time.perf_counter()
    import polars as pl

    lazy = pl.scan_csv(
        path_text,
        has_header=True,
        schema_overrides={"sip_timestamp": pl.UInt64},
        ignore_errors=True,
    ).select(
        pl.len().alias("rows"),
        pl.col("sip_timestamp").cast(pl.UInt64, strict=False).min().fill_null(0).alias("min_sip_timestamp"),
        pl.col("sip_timestamp").cast(pl.UInt64, strict=False).max().fill_null(0).alias("max_sip_timestamp"),
    )
    frame = collect_polars_lazy(lazy)
    row = frame.row(0, named=True)
    return SourcePreflight(
        source_key=source_key,
        stats=RowStats(
            rows=int(row["rows"] or 0),
            min_sip_timestamp=int(row["min_sip_timestamp"] or 0),
            max_sip_timestamp=int(row["max_sip_timestamp"] or 0),
        ),
        wall_seconds=time.perf_counter() - started_at,
    )

def collect_polars_lazy(lazy: Any) -> Any:
    try:
        return lazy.collect(engine="streaming")
    except (TypeError, ValueError):
        return lazy.collect(streaming=True)

def print_preflight_progress(index: int, total: int, result: SourcePreflight, started_at: float) -> None:
    elapsed = time.perf_counter() - started_at
    rate = index / elapsed if elapsed > 0 else 0.0
    remaining = total - index
    eta_seconds = remaining / rate if rate > 0 else 0.0
    print(
        f"PREFLIGHT [{index:,}/{total:,}] {result.source_key} rows={result.stats.rows:,} "
        f"min={result.stats.min_sip_timestamp} max={result.stats.max_sip_timestamp} "
        f"file_seconds={result.wall_seconds:.1f} elapsed_min={elapsed / 60:.1f} eta_min={eta_seconds / 60:.1f}",
        flush=True,
    )

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

def format_optional_int(value: int | None) -> str:
    return "unknown" if value is None else f"{value:,}"

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

def mergetree_settings_sql(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    policy = storage_policy.strip()
    if policy:
        settings.append(f"storage_policy = {sql_string(policy)}")
    return "SETTINGS " + ", ".join(settings)

def windows_path_to_clickhouse_path(path: Path, flatfiles_root_win: Path, flatfiles_root_ch: str) -> str:
    root = flatfiles_root_win.resolve()
    relative = path.resolve().relative_to(root)
    return normalize_clickhouse_file_path(flatfiles_root_ch).rstrip("/") + "/" + relative.as_posix()

def normalize_clickhouse_file_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith(DEFAULT_CLICKHOUSE_FILE_ROOT.rstrip("/") + "/"):
        return normalized.rstrip("/")
    for prefix in CLICKHOUSE_FILE_ROOT_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if normalized.startswith("/"):
        return normalized.rstrip("/")
    return DEFAULT_CLICKHOUSE_FILE_ROOT.rstrip("/") + "/" + normalized.rstrip("/")

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

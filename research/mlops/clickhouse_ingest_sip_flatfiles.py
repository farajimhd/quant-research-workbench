from __future__ import annotations

import argparse
import concurrent.futures
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


@dataclass(frozen=True, slots=True)
class RowStats:
    rows: int
    min_sip_timestamp: int
    max_sip_timestamp: int


@dataclass(frozen=True, slots=True)
class SourcePreflight:
    source_key: str
    stats: RowStats
    wall_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Production ClickHouse ingest for Massive SIP quote/trade flatfiles.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=default_database())
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE, help="Manifest bookkeeping table. Defaults to ingest_manifest.")
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-05-14")
    parser.add_argument("--kinds", default="quotes,trades", help="Comma-separated subset of quotes,trades.")
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--preflight-processes", type=int, default=default_preflight_processes(), help="Worker processes for Polars source row/min/max preflight. Use 1 for serial.")
    parser.add_argument("--storage-policy", default=default_storage_policy(), help="Optional MergeTree storage_policy for raw and manifest tables. Defaults to CLICKHOUSE_HISTORICAL_STORAGE_POLICY when set.")
    parser.add_argument("--limit-files", type=int, default=0, help="Debug limit after discovery. 0 means all files.")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true", help="Retry files whose latest manifest status is started.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rebuild-manifest-only", action="store_true", help="Rediscover source files, recompute source/raw stats, append fresh manifest statuses, and exit without ingesting.")
    parser.add_argument("--optimize-final", action="store_true", help="Run OPTIMIZE FINAL on raw tables after ingest. Usually leave off for full-year ingest.")
    return parser.parse_args()


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
    manifest_table = args.manifest_table.strip()
    flatfiles_root_win = Path(args.flatfiles_root_win)
    flatfiles_root_ch = normalize_clickhouse_file_path(args.flatfiles_root_ch)

    print("=" * 96, flush=True)
    print("Production ClickHouse SIP flatfile ingest", flush=True)
    print(f"database={database}", flush=True)
    print(f"manifest_table={manifest_table}", flush=True)
    print(f"kinds={','.join(kinds)} start_date={args.start_date} end_date={args.end_date}", flush=True)
    print(f"flatfiles_root_win={flatfiles_root_win}", flush=True)
    print(f"flatfiles_root_ch={flatfiles_root_ch}", flush=True)
    print(f"settings={settings.strip()}", flush=True)
    print(f"preflight_processes={args.preflight_processes}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"dry_run={args.dry_run} rebuild_manifest_only={args.rebuild_manifest_only} retry_failed={args.retry_failed} retry_started={args.retry_started}", flush=True)
    print(f"output_report={run_report_path}", flush=True)
    print(f"secret_status={secret_status(clickhouse_env_status_keys())}", flush=True)
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

    create_database_and_tables(client, database, manifest_table, args.storage_policy)
    source_preflight = preflight_source_files(source_files, args.preflight_processes)
    if args.rebuild_manifest_only:
        rebuild_manifest_from_raw(client, database, manifest_table, source_files, source_preflight, run_id, run_report_path)
        print_table_stats(client, database)
        return

    for source in source_files:
        insert_manifest(client, database, manifest_table, source, status="discovered", run_id=run_id, expected_stats=source_preflight[source_identity(source)].stats)

    completed = 0
    skipped = 0
    failed = 0
    started_at = time.perf_counter()

    for index, source in enumerate(source_files, start=1):
        expected_stats = source_preflight[source_identity(source)].stats
        latest_status = latest_manifest_status(client, database, manifest_table, source)
        actual_stats = query_raw_stats(client, database, source)
        if actual_stats.rows:
            print(
                f"[{index:,}/{len(source_files):,}] ROW-CHECK {source.kind}:{source.date} "
                f"status={latest_status or '<none>'} expected_rows={expected_stats.rows:,} actual_rows={actual_stats.rows:,} "
                f"expected_min={expected_stats.min_sip_timestamp} actual_min={actual_stats.min_sip_timestamp} "
                f"expected_max={expected_stats.max_sip_timestamp} actual_max={actual_stats.max_sip_timestamp}",
                flush=True,
            )
            if row_stats_match(expected_stats, actual_stats):
                if latest_status == "ok":
                    skipped += 1
                    print(f"[{index:,}/{len(source_files):,}] SKIP {source.kind}:{source.date} status=ok source_stats_match", flush=True)
                    continue
                insert_manifest(
                    client,
                    database,
                    manifest_table,
                    source,
                    status="ok",
                    run_id=run_id,
                    profile=QueryProfile(label="source_stats_recovery", query_id="", wall_seconds=0.0, written_rows=actual_stats.rows),
                    expected_stats=expected_stats,
                    actual_stats=actual_stats,
                    exception=f"Recovered from {latest_status or 'missing'} manifest; raw stats match source file.",
                )
                skipped += 1
                print(f"[{index:,}/{len(source_files):,}] RECOVER-SKIP {source.kind}:{source.date} source_stats_match", flush=True)
                continue
            print(
                f"[{index:,}/{len(source_files):,}] ROW-MISMATCH {source.kind}:{source.date} "
                f"status={latest_status or '<none>'}; deleting old rows and reinserting",
                flush=True,
            )
            if actual_stats.rows:
                delete_profile = delete_raw_rows(client, database, source)
                append_jsonl(run_report_path, {"source": source_to_json(source), "profile": asdict(delete_profile), "status": "deleted_mismatched_rows"})
                print_profile_summary(delete_profile)
        elif latest_status and latest_status != "discovered":
            print(
                f"[{index:,}/{len(source_files):,}] RAW-MISSING {source.kind}:{source.date} "
                f"status={latest_status} expected_rows={expected_stats.rows:,} actual_rows=0; inserting without delete",
                flush=True,
            )

        print("=" * 96, flush=True)
        print(f"[{index:,}/{len(source_files):,}] START {source.kind}:{source.date} file={source.windows_path.name} size_gib={source.bytes / (1024 ** 3):.2f} expected_rows={expected_stats.rows:,}", flush=True)
        insert_manifest(client, database, manifest_table, source, status="started", run_id=run_id, expected_stats=expected_stats)
        try:
            profile = ingest_one_file(client, database, source, settings)
            actual_stats = query_raw_stats(client, database, source)
            profile.written_rows = actual_stats.rows
            if not row_stats_match(expected_stats, actual_stats):
                insert_manifest(client, database, manifest_table, source, status="failed", run_id=run_id, profile=profile, expected_stats=expected_stats, actual_stats=actual_stats, exception="Post-insert raw stats do not match source preflight stats.")
                raise RuntimeError(
                    f"Post-insert validation failed for {source.kind}:{source.date}: "
                    f"expected={expected_stats} actual={actual_stats}"
                )
            insert_manifest(client, database, manifest_table, source, status="ok", run_id=run_id, profile=profile, expected_stats=expected_stats, actual_stats=actual_stats)
            append_jsonl(run_report_path, {"source": source_to_json(source), "profile": asdict(profile), "status": "ok"})
            completed += 1
            print_profile_summary(profile)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            actual_stats = safe_query_raw_stats(client, database, source)
            insert_manifest(client, database, manifest_table, source, status="failed", run_id=run_id, expected_stats=expected_stats, actual_stats=actual_stats, exception=repr(exc))
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


def rebuild_manifest_from_raw(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    source_files: list[SourceFile],
    source_preflight: dict[str, SourcePreflight],
    run_id: str,
    run_report_path: Path,
) -> None:
    print("=" * 96, flush=True)
    print(f"START manifest rebuild files={len(source_files):,}", flush=True)
    started_at = time.perf_counter()
    status_counts: dict[str, int] = {}
    for index, source in enumerate(source_files, start=1):
        expected_stats = source_preflight[source_identity(source)].stats
        actual_stats = query_raw_stats(client, database, source)
        if row_stats_match(expected_stats, actual_stats):
            status = "ok"
            exception = ""
        elif actual_stats.rows == 0:
            status = "missing"
            exception = "No rows currently exist in raw table for this source file."
        else:
            status = "mismatch"
            exception = "Raw table stats do not match source preflight stats."
        status_counts[status] = status_counts.get(status, 0) + 1
        insert_manifest(
            client,
            database,
            manifest_table,
            source,
            status=status,
            run_id=run_id,
            profile=QueryProfile(label="manifest_rebuild", query_id="", wall_seconds=0.0, written_rows=actual_stats.rows),
            expected_stats=expected_stats,
            actual_stats=actual_stats,
            exception=exception,
        )
        append_jsonl(
            run_report_path,
            {
                "source": source_to_json(source),
                "status": status,
                "expected_stats": asdict(expected_stats),
                "actual_stats": asdict(actual_stats),
            },
        )
        elapsed = time.perf_counter() - started_at
        rate = index / elapsed if elapsed > 0 else 0.0
        remaining = len(source_files) - index
        eta_seconds = remaining / rate if rate > 0 else 0.0
        print(
            f"MANIFEST-REBUILD [{index:,}/{len(source_files):,}] {source.kind}:{source.date} status={status} "
            f"expected_rows={expected_stats.rows:,} actual_rows={actual_stats.rows:,} "
            f"expected_min={expected_stats.min_sip_timestamp} actual_min={actual_stats.min_sip_timestamp} "
            f"expected_max={expected_stats.max_sip_timestamp} actual_max={actual_stats.max_sip_timestamp} "
            f"elapsed_min={elapsed / 60:.1f} eta_min={eta_seconds / 60:.1f}",
            flush=True,
        )
    elapsed = time.perf_counter() - started_at
    print(f"DONE manifest rebuild elapsed_seconds={elapsed:.1f} status_counts={status_counts}", flush=True)
    print(f"report={run_report_path}", flush=True)
    print("=" * 96, flush=True)


def create_database_and_tables(client: ClickHouseHttpClient, database: str, manifest_table: str, storage_policy: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    client.execute(create_quotes_table_sql(database, storage_policy))
    client.execute(create_trades_table_sql(database, storage_policy))
    client.execute(create_manifest_table_sql(database, manifest_table, storage_policy))
    ensure_manifest_columns(client, database, manifest_table)


def create_quotes_table_sql(database: str, storage_policy: str) -> str:
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
{mergetree_settings_sql(storage_policy)}
"""


def create_trades_table_sql(database: str, storage_policy: str) -> str:
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
{mergetree_settings_sql(storage_policy)}
"""


def create_manifest_table_sql(database: str, manifest_table: str, storage_policy: str) -> str:
    db = quote_ident(database)
    table = quote_ident(manifest_table)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.{table}
(
    kind LowCardinality(String),
    source_date Date,
    source_file String,
    source_path_ch String,
    file_bytes UInt64,
    expected_rows UInt64,
    expected_min_sip_timestamp UInt64,
    expected_max_sip_timestamp UInt64,
    actual_rows UInt64,
    actual_min_sip_timestamp UInt64,
    actual_max_sip_timestamp UInt64,
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
{mergetree_settings_sql(storage_policy)}
"""


def ensure_manifest_columns(client: ClickHouseHttpClient, database: str, manifest_table: str) -> None:
    db = quote_ident(database)
    table = f"{db}.{quote_ident(manifest_table)}"
    columns = [
        ("expected_rows", "UInt64"),
        ("expected_min_sip_timestamp", "UInt64"),
        ("expected_max_sip_timestamp", "UInt64"),
        ("actual_rows", "UInt64"),
        ("actual_min_sip_timestamp", "UInt64"),
        ("actual_max_sip_timestamp", "UInt64"),
    ]
    for name, dtype in columns:
        client.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {dtype}")


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


def latest_manifest_status(client: ClickHouseHttpClient, database: str, manifest_table: str, source: SourceFile) -> str:
    try:
        rows = client.query_tsv(
            "SELECT status FROM "
            f"{quote_ident(database)}.{quote_ident(manifest_table)} "
            f"WHERE kind = {sql_string(source.kind)} "
            f"AND source_date = toDate({sql_string(source.date)}) "
            f"AND source_file = {sql_string(source.windows_path.name)} "
            "ORDER BY updated_at DESC, "
            "multiIf(status = 'failed', 90, status = 'mismatch', 80, status = 'missing', 70, "
            "status = 'ok', 60, status = 'started', 50, status = 'discovered', 40, 0) DESC "
            "LIMIT 1"
        ).strip().splitlines()
    except Exception:
        return ""
    return rows[0] if rows else ""


def query_raw_stats(client: ClickHouseHttpClient, database: str, source: SourceFile) -> RowStats:
    table = "quotes_raw" if source.kind == "quotes" else "trades_raw"
    rows = client.query_tsv(
        "SELECT count(), if(count() = 0, 0, min(sip_timestamp)), if(count() = 0, 0, max(sip_timestamp)) FROM "
        f"{quote_ident(database)}.{quote_ident(table)} "
        f"WHERE source_date = toDate({sql_string(source.date)}) "
        f"AND source_file = {sql_string(source.windows_path.name)}"
    ).strip().splitlines()
    if not rows:
        return RowStats(rows=0, min_sip_timestamp=0, max_sip_timestamp=0)
    values = rows[0].split("\t")
    return RowStats(
        rows=int(values[0] or "0"),
        min_sip_timestamp=int(values[1] or "0"),
        max_sip_timestamp=int(values[2] or "0"),
    )


def safe_query_raw_stats(client: ClickHouseHttpClient, database: str, source: SourceFile) -> RowStats:
    try:
        return query_raw_stats(client, database, source)
    except Exception:
        return RowStats(rows=0, min_sip_timestamp=0, max_sip_timestamp=0)


def row_stats_match(expected: RowStats, actual: RowStats) -> bool:
    return (
        expected.rows == actual.rows
        and expected.min_sip_timestamp == actual.min_sip_timestamp
        and expected.max_sip_timestamp == actual.max_sip_timestamp
    )


def delete_raw_rows(client: ClickHouseHttpClient, database: str, source: SourceFile) -> QueryProfile:
    table = "quotes_raw" if source.kind == "quotes" else "trades_raw"
    sql = (
        f"ALTER TABLE {quote_ident(database)}.{quote_ident(table)} DELETE "
        f"WHERE source_date = toDate({sql_string(source.date)}) "
        f"AND source_file = {sql_string(source.windows_path.name)} "
        "SETTINGS mutations_sync = 1"
    )
    return run_profiled(client, f"delete_{source.kind}_{source.date}", sql)


def insert_manifest(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    source: SourceFile,
    *,
    status: str,
    run_id: str,
    profile: QueryProfile | None = None,
    expected_stats: RowStats | None = None,
    actual_stats: RowStats | None = None,
    exception: str = "",
) -> None:
    profile = profile or QueryProfile(label="", query_id="", wall_seconds=0.0)
    expected_stats = expected_stats or RowStats(rows=0, min_sip_timestamp=0, max_sip_timestamp=0)
    actual_stats = actual_stats or RowStats(rows=0, min_sip_timestamp=0, max_sip_timestamp=0)
    db = quote_ident(database)
    table = quote_ident(manifest_table)
    client.execute(
        f"""
INSERT INTO {db}.{table}
(
    kind, source_date, source_file, source_path_ch, file_bytes,
    expected_rows, expected_min_sip_timestamp, expected_max_sip_timestamp,
    actual_rows, actual_min_sip_timestamp, actual_max_sip_timestamp,
    status, run_id, query_id,
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
    {int(expected_stats.rows)},
    {int(expected_stats.min_sip_timestamp)},
    {int(expected_stats.max_sip_timestamp)},
    {int(actual_stats.rows)},
    {int(actual_stats.min_sip_timestamp)},
    {int(actual_stats.max_sip_timestamp)},
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
        f"rows_per_sec={format_optional_int(None if rows_per_second is None else round(rows_per_second))}",
        flush=True,
    )


def format_optional_int(value: int | None) -> str:
    return "unknown" if value is None else f"{value:,}"


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


if __name__ == "__main__":
    main()

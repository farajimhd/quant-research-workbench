from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_PASSWORD_SIMPLE_ENV,
    CLICKHOUSE_USER_ENV,
    CLICKHOUSE_USER_SIMPLE_ENV,
    CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
    CLICKHOUSE_WORKSTATION_USER_ENV,
    DEFAULT_CLICKHOUSE_FILE_ROOT,
    DEFAULT_CLICKHOUSE_URL,
    ClickHouseHttpClient,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_MANIFEST_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_parts")
DEFAULT_DATABASE = "q_live"
DEFAULT_PART_MANIFEST_TABLE = "sec_filing_text_file_ingest_manifest_v1"
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = DEFAULT_CLICKHOUSE_FILE_ROOT
EXPECTED_TARGET_TABLES = {
    "filing": "sec_filing_v2",
    "document": "sec_filing_document_v2",
    "text_source": "sec_filing_text_v1",
    "text": "sec_filing_text_v2",
    "skip": "sec_filing_document_skip_v1",
}
DATASET_ORDER = {
    "filing": 0,
    "document": 1,
    "text_source": 2,
    "text": 3,
    "skip": 4,
}


@dataclass(frozen=True, slots=True)
class PartFile:
    run_id: str
    dataset_name: str
    target_table: str
    part_index: int
    windows_path: Path
    clickhouse_path: str
    expected_rows: int
    expected_bytes: int
    columns: list[str]
    structure: str


@dataclass(frozen=True, slots=True)
class InsertProfile:
    run_id: str
    dataset_name: str
    part_index: int
    path: str
    status: str
    expected_rows: int
    target_rows_before: int
    target_rows_after: int
    inserted_delta: int
    wall_seconds: float
    exception: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Push SEC filing text extractor JSONEachRow part files into ClickHouse through the "
            "server-side file() table function."
        )
    )
    parser.add_argument("--manifest-json", default=os.environ.get("SEC_FILING_TEXT_EXTRACT_MANIFEST_JSON") or "")
    parser.add_argument("--manifest-root-win", default=os.environ.get("SEC_TEXT_PARTS_OUTPUT_ROOT_WIN") or str(DEFAULT_MANIFEST_ROOT_WIN))
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE") or DEFAULT_DATABASE)
    parser.add_argument("--part-manifest-table", default=os.environ.get("SEC_TEXT_FILE_INGEST_MANIFEST_TABLE") or DEFAULT_PART_MANIFEST_TABLE)
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    parser.add_argument("--parts-root-win", default=os.environ.get("SEC_TEXT_PARTS_ROOT_WIN") or str(DEFAULT_PARTS_ROOT_WIN))
    parser.add_argument("--parts-root-ch", default=os.environ.get("SEC_TEXT_PARTS_ROOT_CH") or os.environ.get("TD__DATABASE__CLICKHOUSE__FILE_ROOT") or str(DEFAULT_PARTS_ROOT_CH))
    parser.add_argument("--max-threads", type=int, default=int(os.environ.get("SEC_TEXT_FILE_INGEST_MAX_THREADS", "24")))
    parser.add_argument("--max-memory-usage", default=os.environ.get("SEC_TEXT_FILE_INGEST_MAX_MEMORY", "0"))
    parser.add_argument("--limit-parts", type=int, default=int(os.environ.get("SEC_TEXT_FILE_INGEST_LIMIT_PARTS", "0")))
    parser.add_argument("--dataset", choices=["all", "filing", "document", "text_source", "text", "skip"], default="all")
    parser.add_argument("--execute", action="store_true", help="Actually insert rows. Without this, only validate and print SQL.")
    parser.add_argument("--preflight-only", action="store_true", help="Validate ClickHouse file() access and exit before inserting.")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip file() row-count preflight. Use only after a successful preflight-only run for the same manifest.")
    parser.add_argument("--force", action="store_true", help="Insert even when a part is already marked ok in the manifest table.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry parts whose latest manifest status is failed.")
    parser.add_argument("--skip-create-manifest-table", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    manifest_path = resolve_manifest_path(args)
    if not manifest_path.exists():
        raise SystemExit(f"SEC filing text extract manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest, manifest_path)

    parts = load_part_files(args, manifest)
    if args.dataset != "all":
        parts = [part for part in parts if part.dataset_name == args.dataset]
    if args.limit_parts:
        parts = parts[: max(0, args.limit_parts)]
    if not parts:
        raise SystemExit("no non-empty SEC text part files selected")

    print("=" * 96, flush=True)
    print("SEC filing text ClickHouse file ingest", flush=True)
    print(f"manifest_path={manifest_path}", flush=True)
    print(f"clickhouse_url={args.clickhouse_url}", flush=True)
    print(f"database={args.database}", flush=True)
    print(f"part_manifest_table={args.database}.{args.part_manifest_table}", flush=True)
    print(f"parts={len(parts):,} execute={args.execute} dataset={args.dataset}", flush=True)
    print(f"parts_root_win={args.parts_root_win}", flush=True)
    print(f"parts_root_ch={args.parts_root_ch}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(secret_keys()), sort_keys=True), flush=True)
    print("=" * 96, flush=True)

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    validate_target_tables(client, args, parts)
    if not args.skip_create_manifest_table and args.execute:
        create_part_manifest_table(client, args)
    elif not args.skip_create_manifest_table:
        print("dry_run=create SEC text file ingest manifest table", flush=True)

    if args.preflight_only and args.skip_preflight:
        raise SystemExit("--preflight-only and --skip-preflight cannot be used together")
    if args.skip_preflight:
        print("preflight=skipped; assuming prior preflight-only run succeeded for this manifest", flush=True)
    else:
        preflight_parts(client, args, parts)
    if args.preflight_only:
        print("preflight_only=done", flush=True)
        return

    latest_status = load_latest_part_status(client, args) if args.execute else {}
    selected = select_parts_for_insert(parts, latest_status, args)
    print(f"selected_parts={len(selected):,} skipped_parts={len(parts) - len(selected):,}", flush=True)
    if not selected:
        print("nothing_to_insert=1", flush=True)
        return

    if not args.execute:
        print("dry_run_insert_sql_example=" + insert_sql(args, selected[0]), flush=True)
        print("dry_run=done; pass --execute to insert", flush=True)
        return

    started = time.perf_counter()
    profiles = insert_per_part(client, args, selected)
    elapsed = time.perf_counter() - started
    ok = sum(1 for profile in profiles if profile.status == "ok")
    failed = sum(1 for profile in profiles if profile.status == "failed")
    print(f"DONE inserted_parts_ok={ok:,} failed={failed:,} elapsed_seconds={elapsed:.1f}", flush=True)
    print("profiles=" + json.dumps([asdict(profile) for profile in profiles], ensure_ascii=False, default=str), flush=True)


def resolve_manifest_path(args: argparse.Namespace) -> Path:
    explicit = str(args.manifest_json or "").strip()
    if explicit:
        return Path(explicit)
    root = Path(args.manifest_root_win)
    manifests = sorted(root.glob("*/sec_filing_text_extract_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if manifests:
        return manifests[0]
    return root / "sec_filing_text_extract_manifest.json"


def validate_manifest(manifest: dict[str, Any], manifest_path: Path) -> None:
    if manifest.get("clickhouse_format") != "JSONEachRow":
        raise SystemExit("manifest clickhouse_format must be JSONEachRow")
    if not manifest.get("source_run_id"):
        raise SystemExit("manifest contains no source_run_id")
    target_tables = manifest.get("target_tables") or {}
    for dataset_name, expected_table in EXPECTED_TARGET_TABLES.items():
        table = str(target_tables.get(dataset_name) or "")
        if table != expected_table:
            raise SystemExit(f"manifest target table mismatch dataset={dataset_name} expected={expected_table} actual={table}")
    part_files = manifest.get("part_files") or []
    if not part_files:
        raise SystemExit(f"manifest contains no non-empty part_files: {manifest_path}")


def load_part_files(args: argparse.Namespace, manifest: dict[str, Any]) -> list[PartFile]:
    root_win = Path(args.parts_root_win)
    root_ch = str(args.parts_root_ch)
    run_id = str(manifest.get("source_run_id") or "")
    parts: list[PartFile] = []
    counters: dict[str, int] = {}
    for item in manifest.get("part_files") or []:
        dataset_name = str(item.get("dataset_name") or "")
        target_table = str(item.get("target_table") or "")
        if dataset_name not in EXPECTED_TARGET_TABLES:
            raise SystemExit(f"unknown SEC text dataset in manifest: {dataset_name!r}")
        if target_table != EXPECTED_TARGET_TABLES[dataset_name]:
            raise SystemExit(f"unexpected target table for dataset={dataset_name}: {target_table}")
        rows = int(item.get("rows") or 0)
        if rows <= 0:
            continue
        windows_path = resolve_part_windows_path(str(item.get("path") or ""), root_win)
        if not windows_path.exists():
            raise SystemExit(f"part file does not exist: {windows_path}")
        expected_bytes = int(item.get("bytes") or 0)
        actual_bytes = windows_path.stat().st_size
        if expected_bytes and actual_bytes != expected_bytes:
            raise SystemExit(f"part file byte mismatch path={windows_path} expected={expected_bytes} actual={actual_bytes}")
        counters[dataset_name] = counters.get(dataset_name, 0) + 1
        parts.append(
            PartFile(
                run_id=run_id,
                dataset_name=dataset_name,
                target_table=target_table,
                part_index=int(item.get("part_index") or counters[dataset_name]),
                windows_path=windows_path,
                clickhouse_path=windows_path_to_clickhouse_path(windows_path, root_win, root_ch),
                expected_rows=rows,
                expected_bytes=actual_bytes,
                columns=list(item.get("columns") or []),
                structure=str(item.get("structure") or ""),
            )
        )
    for part in parts:
        if not part.columns or not part.structure:
            raise SystemExit(f"part lacks columns/structure: {part.windows_path}")
    return sorted(parts, key=lambda part: (DATASET_ORDER.get(part.dataset_name, 99), part.part_index))


def resolve_part_windows_path(raw_path: str, root_win: Path) -> Path:
    path = Path(raw_path)
    if path.exists():
        return path
    normalized = raw_path.replace("\\", "/")
    root_name = root_win.name.strip("\\/")
    marker = f"/{root_name}/"
    marker_index = normalized.lower().find(marker.lower())
    if marker_index >= 0:
        relative = normalized[marker_index + len(marker) :]
        candidate = root_win / Path(*[part for part in relative.split("/") if part])
        if candidate.exists():
            return candidate
    drive_marker = f"{root_name}/"
    drive_index = normalized.lower().find(drive_marker.lower())
    if drive_index >= 0:
        relative = normalized[drive_index + len(drive_marker) :]
        candidate = root_win / Path(*[part for part in relative.split("/") if part])
        if candidate.exists():
            return candidate
    return path


def validate_target_tables(client: ClickHouseHttpClient, args: argparse.Namespace, parts: list[PartFile]) -> None:
    for table in sorted({part.target_table for part in parts}):
        sql = (
            "SELECT count() "
            "FROM system.tables "
            f"WHERE database = {sql_string(args.database)} AND name = {sql_string(table)}"
        )
        exists = int((client.execute(sql).strip() or "0").splitlines()[0])
        if exists != 1:
            raise RuntimeError(
                f"target table {args.database}.{table} does not exist. "
                "Run sec_text_v2_schema.py --execute before loading SEC text parts."
            )


def preflight_parts(client: ClickHouseHttpClient, args: argparse.Namespace, parts: list[PartFile]) -> None:
    print("preflight=start", flush=True)
    total = len(parts)
    for index, part in enumerate(parts, start=1):
        sql = f"SELECT count() FROM file({sql_string(part.clickhouse_path)}, 'JSONEachRow', {sql_string(part.structure)})"
        try:
            actual_rows = int((client.execute(sql + settings_sql(args)).strip() or "0").splitlines()[0])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "ClickHouse cannot read SEC text part through file(). "
                f"dataset={part.dataset_name} part={part.windows_path} clickhouse_path={part.clickhouse_path} exception={exc!r}. "
                "Check --parts-root-win and --parts-root-ch mapping and ClickHouse user_files_path/bind mounts."
            ) from exc
        if part.expected_rows and actual_rows != part.expected_rows:
            raise RuntimeError(f"row count mismatch part={part.windows_path} expected={part.expected_rows} actual={actual_rows}")
        print(f"preflight_part={index:,}/{total:,} dataset={part.dataset_name} part_index={part.part_index} rows={actual_rows:,}", flush=True)
    print("preflight=done", flush=True)


def create_part_manifest_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    settings = merge_tree_settings(args.storage_policy)
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.part_manifest_table)}
(
    run_id String,
    dataset_name LowCardinality(String),
    target_table String,
    part_index UInt32,
    part_path String,
    clickhouse_path String,
    status LowCardinality(String),
    expected_rows UInt64,
    target_rows_before UInt64,
    target_rows_after UInt64,
    inserted_delta Int64,
    exception String,
    updated_at_utc DateTime64(9, 'UTC') DEFAULT now64(9)
)
ENGINE = MergeTree
ORDER BY (run_id, dataset_name, part_index, updated_at_utc)
{settings}
"""
    )


def load_latest_part_status(client: ClickHouseHttpClient, args: argparse.Namespace) -> dict[tuple[str, str, int], str]:
    sql = f"""
SELECT run_id, dataset_name, part_index, argMax(status, updated_at_utc) AS status
FROM {quote_ident(args.database)}.{quote_ident(args.part_manifest_table)}
GROUP BY run_id, dataset_name, part_index
FORMAT JSONEachRow
"""
    try:
        text = client.execute(sql)
    except Exception:
        return {}
    output: dict[tuple[str, str, int], str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        output[(str(row.get("run_id") or ""), str(row.get("dataset_name") or ""), int(row.get("part_index") or 0))] = str(row.get("status") or "")
    return output


def select_parts_for_insert(parts: list[PartFile], latest_status: dict[tuple[str, str, int], str], args: argparse.Namespace) -> list[PartFile]:
    if args.force:
        return parts
    selected: list[PartFile] = []
    for part in parts:
        status = latest_status.get((part.run_id, part.dataset_name, part.part_index), "")
        if status == "ok":
            continue
        if status == "failed" and not args.retry_failed:
            continue
        selected.append(part)
    return selected


def insert_per_part(client: ClickHouseHttpClient, args: argparse.Namespace, parts: list[PartFile]) -> list[InsertProfile]:
    profiles: list[InsertProfile] = []
    total = len(parts)
    for index, part in enumerate(parts, start=1):
        profile = insert_one_part(client, args, part)
        profiles.append(profile)
        insert_part_manifest(client, args, part, profile)
        print(
            f"insert_part={index:,}/{total:,} dataset={part.dataset_name} part_index={part.part_index} status={profile.status} "
            f"expected_rows={profile.expected_rows:,} delta={profile.inserted_delta:,} elapsed={profile.wall_seconds:.1f}s",
            flush=True,
        )
        if profile.status != "ok":
            raise RuntimeError(profile.exception)
    return profiles


def insert_one_part(client: ClickHouseHttpClient, args: argparse.Namespace, part: PartFile) -> InsertProfile:
    started = time.perf_counter()
    status = "ok"
    exception = ""
    try:
        client.execute(insert_sql(args, part) + settings_sql(args))
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        exception = repr(exc)
    inserted_delta = part.expected_rows if status == "ok" else 0
    return InsertProfile(
        run_id=part.run_id,
        dataset_name=part.dataset_name,
        part_index=part.part_index,
        path=str(part.windows_path),
        status=status,
        expected_rows=part.expected_rows,
        target_rows_before=0,
        target_rows_after=0,
        inserted_delta=inserted_delta,
        wall_seconds=round(time.perf_counter() - started, 3),
        exception=exception,
    )


def insert_sql(args: argparse.Namespace, part: PartFile) -> str:
    columns = ", ".join(quote_ident(column) for column in part.columns)
    select_columns = ", ".join(quote_ident(column) for column in part.columns)
    return (
        f"INSERT INTO {quote_ident(args.database)}.{quote_ident(part.target_table)} ({columns})\n"
        f"SELECT {select_columns}\n"
        f"FROM file({sql_string(part.clickhouse_path)}, 'JSONEachRow', {sql_string(part.structure)})"
    )


def insert_part_manifest(client: ClickHouseHttpClient, args: argparse.Namespace, part: PartFile, profile: InsertProfile) -> None:
    row = {
        "run_id": part.run_id,
        "dataset_name": part.dataset_name,
        "target_table": part.target_table,
        "part_index": part.part_index,
        "part_path": str(part.windows_path),
        "clickhouse_path": part.clickhouse_path,
        "status": profile.status,
        "expected_rows": part.expected_rows,
        "target_rows_before": profile.target_rows_before,
        "target_rows_after": profile.target_rows_after,
        "inserted_delta": profile.inserted_delta,
        "exception": profile.exception,
    }
    client.execute(
        f"INSERT INTO {quote_ident(args.database)}.{quote_ident(args.part_manifest_table)} SETTINGS date_time_input_format = 'best_effort' FORMAT JSONEachRow\n"
        + json.dumps(row, ensure_ascii=False, default=str)
    )


def settings_sql(args: argparse.Namespace) -> str:
    settings = [
        "input_format_skip_unknown_fields = 0",
        "date_time_input_format = 'best_effort'",
    ]
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


def windows_path_to_clickhouse_path(path: Path, root_win: Path, root_ch: str) -> str:
    resolved_path = path.resolve()
    resolved_root = root_win.resolve()
    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise SystemExit(f"part path {path} is not under --parts-root-win {root_win}") from exc
    return normalize_ch_root(root_ch).rstrip("/") + "/" + relative.as_posix()


def normalize_ch_root(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("/"):
        return text.rstrip("/")
    return "/" + text.rstrip("/")


def default_clickhouse_url() -> str:
    return (
        os.environ.get("SEC_CLICKHOUSE_URL")
        or os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL")
        or os.environ.get("QMD_CLICKHOUSE_URL")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or DEFAULT_CLICKHOUSE_URL
    )


def default_clickhouse_user() -> str:
    return (
        os.environ.get("SEC_CLICKHOUSE_USER")
        or os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER")
        or os.environ.get("QMD_CLICKHOUSE_USER")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER")
        or os.environ.get(CLICKHOUSE_WORKSTATION_USER_ENV)
        or os.environ.get(CLICKHOUSE_USER_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_USER_ENV)
        or "default"
    )


def default_clickhouse_password() -> str:
    return (
        os.environ.get("SEC_CLICKHOUSE_PASSWORD")
        or os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or os.environ.get(CLICKHOUSE_WORKSTATION_PASSWORD_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_ENV)
        or ""
    )


def secret_keys() -> list[str]:
    return [
        "SEC_CLICKHOUSE_URL",
        "SEC_CLICKHOUSE_USER",
        "SEC_CLICKHOUSE_PASSWORD",
        "QLIVE_MIGRATION_CLICKHOUSE_URL",
        "QLIVE_MIGRATION_CLICKHOUSE_USER",
        "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
        "QMD_CLICKHOUSE_URL",
        "QMD_CLICKHOUSE_USER",
        "QMD_CLICKHOUSE_PASSWORD",
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_USER",
        "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
        "CLICKHOUSE_LIVE_STORAGE_POLICY",
    ]


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
    raise ValueError(f"invalid size: {value}")


def merge_tree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return "SETTINGS " + ", ".join(settings)


if __name__ == "__main__":
    main()

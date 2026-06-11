from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
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
from research.mlops.news_benzinga_build_normalized_rows import (  # noqa: E402
    NEWS_TABLE_COLUMNS,
    NEWS_TABLE_STRUCTURE,
)
from research.mlops.news_benzinga_clickhouse import create_news_database_and_tables  # noqa: E402


DEFAULT_MANIFEST_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_normalized_rows")
DEFAULT_DATABASE = "q_live"
DEFAULT_NEWS_TABLE = "benzinga_news_normalized_v1"
DEFAULT_PART_MANIFEST_TABLE = "benzinga_news_file_ingest_manifest_v1"
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = DEFAULT_CLICKHOUSE_FILE_ROOT


@dataclass(frozen=True, slots=True)
class PartFile:
    part_index: int
    windows_path: Path
    clickhouse_path: str
    expected_rows: int
    expected_bytes: int
    format: str


@dataclass(frozen=True, slots=True)
class InsertProfile:
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
            "Push final Benzinga normalized JSONEachRow part files into ClickHouse using the "
            "server-side file() table function."
        )
    )
    parser.add_argument("--manifest-json", default=os.environ.get("NEWS_BENZINGA_NORMALIZED_MANIFEST_JSON") or "")
    parser.add_argument("--manifest-root-win", default=os.environ.get("NEWS_BENZINGA_NORMALIZED_ROWS_OUTPUT_ROOT_WIN") or str(DEFAULT_MANIFEST_ROOT_WIN))
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_DATABASE") or DEFAULT_DATABASE)
    parser.add_argument("--table", default=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_TABLE") or DEFAULT_NEWS_TABLE)
    parser.add_argument("--part-manifest-table", default=os.environ.get("NEWS_BENZINGA_FILE_INGEST_MANIFEST_TABLE") or DEFAULT_PART_MANIFEST_TABLE)
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    parser.add_argument("--parts-root-win", default=os.environ.get("NEWS_BENZINGA_PARTS_ROOT_WIN") or str(DEFAULT_PARTS_ROOT_WIN))
    parser.add_argument("--parts-root-ch", default=os.environ.get("NEWS_BENZINGA_PARTS_ROOT_CH") or os.environ.get("TD__DATABASE__CLICKHOUSE__FILE_ROOT") or str(DEFAULT_PARTS_ROOT_CH))
    parser.add_argument("--max-threads", type=int, default=int(os.environ.get("NEWS_BENZINGA_FILE_INGEST_MAX_THREADS", "24")))
    parser.add_argument("--max-memory-usage", default=os.environ.get("NEWS_BENZINGA_FILE_INGEST_MAX_MEMORY", "0"))
    parser.add_argument("--limit-parts", type=int, default=int(os.environ.get("NEWS_BENZINGA_FILE_INGEST_LIMIT_PARTS", "0")))
    parser.add_argument("--execute", action="store_true", help="Actually insert rows. Without this, only validate and print SQL.")
    parser.add_argument("--preflight-only", action="store_true", help="Validate ClickHouse file() access and exit before inserting.")
    parser.add_argument("--force", action="store_true", help="Insert even when a part is already marked ok in the manifest table.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry parts whose latest manifest status is failed.")
    parser.add_argument("--skip-create-tables", action="store_true")
    parser.add_argument("--insert-mode", choices=["per-part", "single-glob"], default="per-part")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    manifest_path = resolve_manifest_path(args)
    if not manifest_path.exists():
        raise SystemExit(f"normalized manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest, manifest_path)

    parts = load_part_files(args, manifest)
    if args.limit_parts:
        parts = parts[: max(0, args.limit_parts)]

    print("=" * 96, flush=True)
    print("Benzinga ClickHouse file ingest", flush=True)
    print(f"manifest_path={manifest_path}", flush=True)
    print(f"clickhouse_url={args.clickhouse_url}", flush=True)
    print(f"target={args.database}.{args.table}", flush=True)
    print(f"part_manifest_table={args.database}.{args.part_manifest_table}", flush=True)
    print(f"parts={len(parts):,} execute={args.execute} insert_mode={args.insert_mode}", flush=True)
    print(f"parts_root_win={args.parts_root_win}", flush=True)
    print(f"parts_root_ch={args.parts_root_ch}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(secret_keys()), sort_keys=True), flush=True)
    print("=" * 96, flush=True)

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    if not args.skip_create_tables and args.execute:
        create_news_database_and_tables(client, database=args.database, news_table=args.table, storage_policy=args.storage_policy)
        create_part_manifest_table(client, args)
    elif not args.skip_create_tables:
        print("dry_run=create target/news and file manifest tables", flush=True)

    preflight = preflight_parts(client, args, parts)
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
    if args.insert_mode == "single-glob":
        profiles = insert_single_glob(client, args, selected, manifest)
    else:
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
    manifests = sorted(root.glob("*/benzinga_news_normalized_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if manifests:
        return manifests[0]
    return root / "benzinga_news_normalized_manifest.json"


def validate_manifest(manifest: dict[str, Any], manifest_path: Path) -> None:
    if manifest.get("interrupted"):
        raise SystemExit(f"manifest is interrupted and should not be loaded: {manifest_path}")
    if manifest.get("clickhouse_format") != "JSONEachRow":
        raise SystemExit("manifest clickhouse_format must be JSONEachRow")
    columns = list(manifest.get("clickhouse_columns") or [])
    if columns != NEWS_TABLE_COLUMNS:
        raise SystemExit("manifest columns do not match current Benzinga news table contract")
    part_files = manifest.get("normalized_part_files") or []
    if not part_files:
        raise SystemExit("manifest contains no normalized_part_files")


def load_part_files(args: argparse.Namespace, manifest: dict[str, Any]) -> list[PartFile]:
    parts: list[PartFile] = []
    root_win = Path(args.parts_root_win)
    root_ch = str(args.parts_root_ch)
    for item in manifest.get("normalized_part_files") or []:
        windows_path = Path(str(item.get("path") or ""))
        if not windows_path.exists():
            raise SystemExit(f"part file does not exist: {windows_path}")
        expected_bytes = int(item.get("bytes") or 0)
        actual_bytes = windows_path.stat().st_size
        if expected_bytes and actual_bytes != expected_bytes:
            raise SystemExit(f"part file byte mismatch path={windows_path} expected={expected_bytes} actual={actual_bytes}")
        parts.append(
            PartFile(
                part_index=int(item.get("part_index") or len(parts) + 1),
                windows_path=windows_path,
                clickhouse_path=windows_path_to_clickhouse_path(windows_path, root_win, root_ch),
                expected_rows=int(item.get("rows") or 0),
                expected_bytes=actual_bytes,
                format=str(item.get("format") or "JSONEachRow"),
            )
        )
    return parts


def preflight_parts(client: ClickHouseHttpClient, args: argparse.Namespace, parts: list[PartFile]) -> dict[int, int]:
    print("preflight=start", flush=True)
    output: dict[int, int] = {}
    for index, part in enumerate(parts, start=1):
        sql = f"SELECT count() FROM file({sql_string(part.clickhouse_path)}, 'JSONEachRow', {sql_string(structure_sql())})"
        try:
            actual_rows = int((client.execute(sql + settings_sql(args)).strip() or "0").splitlines()[0])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "ClickHouse cannot read normalized part through file(). "
                f"part={part.windows_path} clickhouse_path={part.clickhouse_path} exception={exc!r}. "
                "Check --parts-root-win and --parts-root-ch mapping and ClickHouse user_files_path/bind mounts."
            ) from exc
        if part.expected_rows and actual_rows != part.expected_rows:
            raise RuntimeError(f"row count mismatch part={part.windows_path} expected={part.expected_rows} actual={actual_rows}")
        output[part.part_index] = actual_rows
        print(f"preflight_part={index:,}/{len(parts):,} part_index={part.part_index} rows={actual_rows:,}", flush=True)
    print("preflight=done", flush=True)
    return output


def create_part_manifest_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    settings = merge_tree_settings(args.storage_policy)
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.part_manifest_table)}
(
    run_id String,
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
ORDER BY (run_id, part_index, updated_at_utc)
{settings}
"""
    )


def load_latest_part_status(client: ClickHouseHttpClient, args: argparse.Namespace) -> dict[tuple[str, int], str]:
    sql = f"""
SELECT run_id, part_index, argMax(status, updated_at_utc) AS status
FROM {quote_ident(args.database)}.{quote_ident(args.part_manifest_table)}
GROUP BY run_id, part_index
FORMAT JSONEachRow
"""
    try:
        text = client.execute(sql)
    except Exception:
        return {}
    output: dict[tuple[str, int], str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        output[(str(row.get("run_id") or ""), int(row.get("part_index") or 0))] = str(row.get("status") or "")
    return output


def select_parts_for_insert(parts: list[PartFile], latest_status: dict[tuple[str, int], str], args: argparse.Namespace) -> list[PartFile]:
    if args.force:
        return parts
    run_id = current_run_id_from_parts(parts)
    selected: list[PartFile] = []
    for part in parts:
        status = latest_status.get((run_id, part.part_index), "")
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
            f"insert_part={index:,}/{total:,} part_index={part.part_index} status={profile.status} "
            f"expected_rows={profile.expected_rows:,} delta={profile.inserted_delta:,} "
            f"elapsed={profile.wall_seconds:.1f}s",
            flush=True,
        )
        if profile.status != "ok":
            raise RuntimeError(profile.exception)
    return profiles


def insert_one_part(client: ClickHouseHttpClient, args: argparse.Namespace, part: PartFile) -> InsertProfile:
    started = time.perf_counter()
    before = count_target_rows(client, args)
    status = "ok"
    exception = ""
    try:
        client.execute(insert_sql(args, part) + settings_sql(args))
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        exception = repr(exc)
    after = count_target_rows(client, args)
    inserted_delta = after - before
    if status == "ok" and part.expected_rows and inserted_delta != part.expected_rows:
        status = "failed"
        exception = f"inserted_delta_mismatch expected={part.expected_rows} actual={inserted_delta}"
    return InsertProfile(
        part_index=part.part_index,
        path=str(part.windows_path),
        status=status,
        expected_rows=part.expected_rows,
        target_rows_before=before,
        target_rows_after=after,
        inserted_delta=inserted_delta,
        wall_seconds=round(time.perf_counter() - started, 3),
        exception=exception,
    )


def insert_single_glob(client: ClickHouseHttpClient, args: argparse.Namespace, parts: list[PartFile], manifest: dict[str, Any]) -> list[InsertProfile]:
    started = time.perf_counter()
    before = count_target_rows(client, args)
    expected = sum(part.expected_rows for part in parts)
    glob_path = common_glob_path(parts)
    pseudo = PartFile(part_index=0, windows_path=Path(str(manifest.get("normalized_file_glob") or "")), clickhouse_path=glob_path, expected_rows=expected, expected_bytes=0, format="JSONEachRow")
    status = "ok"
    exception = ""
    try:
        client.execute(insert_sql(args, pseudo) + settings_sql(args))
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        exception = repr(exc)
    after = count_target_rows(client, args)
    inserted_delta = after - before
    if status == "ok" and expected and inserted_delta != expected:
        status = "failed"
        exception = f"inserted_delta_mismatch expected={expected} actual={inserted_delta}"
    profile = InsertProfile(
        part_index=0,
        path=str(pseudo.windows_path),
        status=status,
        expected_rows=expected,
        target_rows_before=before,
        target_rows_after=after,
        inserted_delta=inserted_delta,
        wall_seconds=round(time.perf_counter() - started, 3),
        exception=exception,
    )
    for part in parts:
        insert_part_manifest(client, args, part, profile)
    if status != "ok":
        raise RuntimeError(exception)
    return [profile]


def insert_sql(args: argparse.Namespace, part: PartFile) -> str:
    columns = ", ".join(quote_ident(column) for column in NEWS_TABLE_COLUMNS)
    select_columns = ", ".join(quote_ident(column) for column in NEWS_TABLE_COLUMNS)
    return (
        f"INSERT INTO {quote_ident(args.database)}.{quote_ident(args.table)} ({columns})\n"
        f"SELECT {select_columns}\n"
        f"FROM file({sql_string(part.clickhouse_path)}, 'JSONEachRow', {sql_string(structure_sql())})"
    )


def insert_part_manifest(client: ClickHouseHttpClient, args: argparse.Namespace, part: PartFile, profile: InsertProfile) -> None:
    run_id = current_run_id_from_parts([part])
    row = {
        "run_id": run_id,
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
        f"INSERT INTO {quote_ident(args.database)}.{quote_ident(args.part_manifest_table)} FORMAT JSONEachRow\n"
        + json.dumps(row, ensure_ascii=False, default=str)
    )


def count_target_rows(client: ClickHouseHttpClient, args: argparse.Namespace) -> int:
    text = client.execute(f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(args.table)}")
    return int((text.strip() or "0").splitlines()[0])


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


def structure_sql() -> str:
    return ", ".join(NEWS_TABLE_STRUCTURE)


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


def common_glob_path(parts: list[PartFile]) -> str:
    if not parts:
        raise ValueError("no parts")
    first = Path(parts[0].clickhouse_path)
    return str(first.parent / "benzinga_news_normalized_part_*.jsonl").replace("\\", "/")


def current_run_id_from_parts(parts: list[PartFile]) -> str:
    if not parts:
        return ""
    return parts[0].windows_path.parent.parent.name


def default_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or os.environ.get(CLICKHOUSE_ENDPOINT_ENV) or DEFAULT_CLICKHOUSE_URL


def default_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or os.environ.get(CLICKHOUSE_WORKSTATION_USER_ENV) or os.environ.get(CLICKHOUSE_USER_SIMPLE_ENV) or os.environ.get(CLICKHOUSE_USER_ENV) or "default"


def default_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or os.environ.get(CLICKHOUSE_WORKSTATION_PASSWORD_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_ENV)
        or ""
    )


def secret_keys() -> list[str]:
    return [
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

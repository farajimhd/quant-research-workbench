from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar import sec_filing_text_clickhouse_file_ingest as file_ingest  # noqa: E402
from pipelines.sec.edgar import sec_filing_text_extract_parts as extractor  # noqa: E402
from pipelines.sec.edgar import sec_text_v3_schema as text_schema  # noqa: E402
from pipelines.sec.edgar.sec_parquet_parts import (  # noqa: E402
    DEFAULT_FILE_BYTES,
    DEFAULT_ROW_GROUP_BYTES,
    ParquetShardWriter,
    convert_json_part,
    validate_parquet_part,
)
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_ARCHIVE_ROOT_WIN = Path("D:/market-data/sec_core/daily_archives")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_parts")
DEFAULT_HISTORICAL_ROOT_WIN = Path("D:/market-data/prepared/sec_historical_gap_fill")
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = "/mnt/d/market-data"
DEFAULT_DATABASE = "q_live"
DEFAULT_ARCHIVE_MANIFEST_TABLE = "sec_filing_archive_ingest_manifest_v3"
EVENT_PREFIX = "SEC_ARCHIVE_EVENT="
DATASET_ORDER = ("filing", "document", "text_source", "text", "skip")
PART_DIRECTORIES = {
    "filing": "sec_filing_v3_parts",
    "document": "sec_filing_document_v3_parts",
    "text_source": "sec_filing_text_v3_parts",
    "text": "sec_filing_text_rendered_v3_parts",
    "skip": "sec_filing_document_skip_v3_parts",
}
DATE_SCOPED_DATASETS = {"document", "text_source", "text", "skip"}
PART_ARCHIVE_DATE_PATTERN = re.compile(r"(?:^|_)(20\d{6})(?:_|\.|$)")


@dataclass(frozen=True, slots=True)
class DatasetCheckpoint:
    run_id: str
    dataset_name: str
    target_table: str
    archive_date: str
    status: str
    expected_rows: int
    records: tuple[file_ingest.PartManifestRecord, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild SEC archive-derived v3 rows with bounded staging. Each worker lane extracts, "
            "renders into byte-bounded Parquet shards, inserts, verifies, and removes one archive's temporary parts before "
            "advancing to its next assigned archive."
        )
    )
    parser.add_argument("--clickhouse-url", default=extractor.default_sec_clickhouse_url())
    parser.add_argument("--user", default=extractor.default_sec_clickhouse_user())
    parser.add_argument("--password", default=extractor.default_sec_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_TEXT_TARGET_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_DAILY_ARCHIVE_ROOT_WIN", str(DEFAULT_ARCHIVE_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_TEXT_PARTS_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--historical-output-root-win", default=os.environ.get("SEC_HISTORICAL_GAP_FILL_OUTPUT_ROOT_WIN", str(DEFAULT_HISTORICAL_ROOT_WIN)))
    parser.add_argument("--parts-root-win", default=os.environ.get("SEC_TEXT_PARTS_ROOT_WIN", str(DEFAULT_PARTS_ROOT_WIN)))
    parser.add_argument("--parts-root-ch", default=os.environ.get("SEC_TEXT_PARTS_ROOT_CH", DEFAULT_PARTS_ROOT_CH))
    parser.add_argument("--start-date", required=True, help="Inclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SEC_ARCHIVE_REBUILD_WORKERS", "32")))
    parser.add_argument("--insert-max-threads", type=int, default=int(os.environ.get("SEC_ARCHIVE_INSERT_MAX_THREADS", "8")))
    parser.add_argument("--insert-max-memory-usage", default=os.environ.get("SEC_ARCHIVE_INSERT_MAX_MEMORY", "16G"))
    parser.add_argument("--insert-concurrency", type=int, default=int(os.environ.get("SEC_ARCHIVE_INSERT_CONCURRENCY", "8")))
    parser.add_argument(
        "--parquet-row-group-mb",
        type=int,
        default=int(os.environ.get("SEC_TEXT_PARQUET_ROW_GROUP_MB", str(DEFAULT_ROW_GROUP_BYTES // 1024**2))),
    )
    parser.add_argument(
        "--parquet-file-mb",
        type=int,
        default=int(os.environ.get("SEC_TEXT_PARQUET_FILE_MB", str(DEFAULT_FILE_BYTES // 1024**2))),
    )
    parser.add_argument("--parquet-compression-level", type=int, default=int(os.environ.get("SEC_TEXT_PARQUET_ZSTD_LEVEL", "1")))
    parser.add_argument("--part-manifest-table", default=os.environ.get("SEC_TEXT_FILE_INGEST_MANIFEST_TABLE", file_ingest.DEFAULT_PART_MANIFEST_TABLE))
    parser.add_argument("--archive-manifest-table", default=os.environ.get("SEC_ARCHIVE_INGEST_MANIFEST_TABLE", DEFAULT_ARCHIVE_MANIFEST_TABLE))
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", os.environ.get("CLICKHOUSE_STORAGE_POLICY", "")))
    parser.add_argument("--limit-archives", type=int, default=0)
    parser.add_argument("--max-filings-per-archive", type=int, default=0)
    parser.add_argument("--sample-limit-per-archive", type=int, default=1)
    parser.add_argument("--sample-text-chars", type=int, default=2000)
    parser.add_argument("--parent-window-days-before", type=int, default=1)
    parser.add_argument("--parent-window-days-after", type=int, default=2)
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument("--max-text-chars", type=int, default=0)
    parser.add_argument("--cleanup-parts", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--recover-incomplete-runs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--repair-failed-inserts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete and verify date-scoped rows from failed dataset attempts before resuming them.",
    )
    parser.add_argument(
        "--cleanup-date-batch-size",
        type=int,
        default=int(os.environ.get("SEC_ARCHIVE_CLEANUP_DATE_BATCH_SIZE", "500")),
        help="Maximum failed archive dates repaired by one synchronous ClickHouse delete.",
    )
    parser.add_argument("--progress-layout", choices=("events", "text"), default="events")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    run_stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    source_run_id = f"sec_archive_rebuild_{run_stamp}"
    run_root = Path(args.output_root_win) / run_stamp
    parts_root = run_root / "parts"
    states_root = run_root / "archive_states"
    run_root.mkdir(parents=True, exist_ok=True)
    parts_root.mkdir(parents=True, exist_ok=True)
    states_root.mkdir(parents=True, exist_ok=True)

    archives = extractor.discover_archives(Path(args.archive_root_win), args.start_date, args.end_date)
    if args.limit_archives:
        archives = archives[: max(0, int(args.limit_archives))]
    archive_by_date = {extractor.archive_date_from_name(path.name).isoformat(): path for path in archives}

    print_header(args, run_root, archives, loaded_env)
    if not args.execute:
        write_json(
            run_root / "sec_filing_archive_rebuild_plan.json",
            {
                "source_run_id": source_run_id,
                "archives": len(archives),
                "workers": bounded_worker_count(args.workers, len(archives)),
                "start_date": args.start_date,
                "end_date": args.end_date,
                "dry_run": True,
            },
        )
        print("dry_run=true; pass --execute to extract and insert archive rows", flush=True)
        return

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    created_tables, target_table_uuids = ensure_target_tables(client, args.database, args.storage_policy)
    args.target_table_uuids = target_table_uuids
    args.text_source_table_uuid = target_table_uuids[file_ingest.EXPECTED_TARGET_TABLES["text_source"]]
    ingest_args = ingest_namespace(args)
    file_ingest.create_part_manifest_table(client, ingest_args)
    create_archive_manifest_table(client, args)
    completed_keys = load_completed_archive_keys(client, args)
    completed_units = load_completed_archive_units(client, args)
    part_records = file_ingest.load_latest_part_records(client, ingest_args)
    checkpoints = build_dataset_checkpoints(part_records, set(archive_by_date))
    retry_dataset_keys = failed_dataset_keys(checkpoints, completed_units)
    if retry_dataset_keys and not args.repair_failed_inserts:
        raise RuntimeError(
            f"found {len(retry_dataset_keys):,} failed SEC archive dataset attempts; "
            "rerun with --repair-failed-inserts so partial ClickHouse rows are removed before retry"
        )
    cleanup_summary = cleanup_failed_dataset_rows(
        client,
        args,
        checkpoints,
        retry_dataset_keys,
        progress_layout=args.progress_layout,
    )
    if created_tables:
        print(
            "created_target_tables=" + ",".join(sorted(created_tables))
            + f" text_source_generation={args.text_source_table_uuid}",
            flush=True,
        )

    recovery_tasks: list[dict[str, Any]] = []
    if args.recover_incomplete_runs:
        recovery_tasks = discover_recovery_tasks(args, archive_by_date, completed_keys, current_run_root=run_root)
        annotate_recovery_tasks(recovery_tasks, checkpoints, retry_dataset_keys)
    recovered_dates = {task["archive_date"] for task in recovery_tasks}
    new_tasks = [
        new_archive_task(path, source_run_id, parts_root, states_root, index)
        for index, path in enumerate(archives, start=1)
        if archive_identity(path)["archive_key"] not in completed_keys
        and extractor.archive_date_from_name(path.name).isoformat() not in recovered_dates
    ]
    tasks = [*recovery_tasks, *new_tasks]
    already_completed = len(archives) - len(tasks)
    if not tasks:
        if args.cleanup_parts:
            cleanup_obsolete_incomplete_parts(Path(args.output_root_win), run_root, archive_by_date)
            cleanup_empty_part_directories(Path(args.output_root_win))
        print(f"all_archives_completed={len(archives):,}; nothing to rebuild", flush=True)
        write_run_summary(run_root, args, source_run_id, len(archives), already_completed, [], loaded_env, cleanup_summary)
        return

    lanes = partition_tasks(tasks, bounded_worker_count(args.workers, len(tasks)))
    emit_event(
        args.progress_layout,
        {
            "kind": "init",
            "total": len(archives),
            "already_completed": already_completed,
            "recovery": len(recovery_tasks),
            "lanes": [{"lane": index + 1, "total": len(items)} for index, items in enumerate(lanes)],
        },
    )
    payloads = [
        lane_payload(args, source_run_id, run_root, lane_index + 1, lane, retry_dataset_keys)
        for lane_index, lane in enumerate(lanes)
    ]
    results = run_lanes(payloads, args.progress_layout)
    failed = [item for result in results for item in result.get("archives", []) if item.get("status") == "failed"]
    cancelled = [item for result in results for item in result.get("archives", []) if item.get("status") == "cancelled"]
    completed = sum(int(result.get("completed") or 0) for result in results)
    write_run_summary(
        run_root,
        args,
        source_run_id,
        len(archives),
        already_completed + completed,
        results,
        loaded_env,
        cleanup_summary,
    )
    if not failed and args.cleanup_parts:
        cleanup_obsolete_incomplete_parts(Path(args.output_root_win), run_root, archive_by_date)
    cleanup_empty_part_directories(Path(args.output_root_win))
    print(
        f"archive_rebuild_done completed={already_completed + completed:,}/{len(archives):,} "
        f"failed={len(failed):,} cancelled={len(cancelled):,} run_root={run_root}",
        flush=True,
    )
    if failed or cancelled:
        raise SystemExit(1)


def validate_args(args: argparse.Namespace) -> None:
    extractor.validate_identifier(args.database, "--database")
    extractor.validate_identifier(args.part_manifest_table, "--part-manifest-table")
    extractor.validate_identifier(args.archive_manifest_table, "--archive-manifest-table")
    extractor.validate_date(args.start_date, "--start-date")
    extractor.validate_date(args.end_date, "--end-date")
    if date.fromisoformat(args.start_date) >= date.fromisoformat(args.end_date):
        raise SystemExit("--start-date must be earlier than --end-date")
    if int(args.workers) < 1:
        raise SystemExit("--workers must be positive")
    if int(args.insert_concurrency) < 1:
        raise SystemExit("--insert-concurrency must be positive")
    if int(args.parquet_row_group_mb) < 1:
        raise SystemExit("--parquet-row-group-mb must be positive")
    if int(args.parquet_file_mb) < int(args.parquet_row_group_mb):
        raise SystemExit("--parquet-file-mb must be at least --parquet-row-group-mb")
    if int(args.cleanup_date_batch_size) < 1:
        raise SystemExit("--cleanup-date-batch-size must be positive")


def bounded_worker_count(requested: int, task_count: int) -> int:
    return max(1, min(int(requested), max(1, int(task_count)), 61 if os.name == "nt" else int(requested)))


def partition_tasks(tasks: list[dict[str, Any]], workers: int) -> list[list[dict[str, Any]]]:
    lanes = [[] for _ in range(max(1, min(int(workers), len(tasks))))]
    for index, task in enumerate(tasks):
        lanes[index % len(lanes)].append(task)
    return lanes


def new_archive_task(archive: Path, source_run_id: str, parts_root: Path, states_root: Path, index: int) -> dict[str, Any]:
    identity = archive_identity(archive)
    return {
        "kind": "extract",
        **identity,
        "source_run_id": source_run_id,
        "parts_root": str(parts_root),
        "state_path": str(states_root / f"{identity['archive_date'].replace('-', '')}_{identity['archive_key'][:12]}.json"),
        "archive_index": int(index),
    }


def archive_identity(archive: Path) -> dict[str, Any]:
    stat = archive.stat()
    normalized = str(archive.resolve()).replace("\\", "/").lower()
    material = f"{normalized}|{stat.st_size}|{stat.st_mtime_ns}"
    return {
        "archive_key": hashlib.sha256(material.encode("utf-8")).hexdigest(),
        "archive_date": extractor.archive_date_from_name(archive.name).isoformat(),
        "archive_path": str(archive),
        "archive_size": int(stat.st_size),
        "archive_mtime_ns": int(stat.st_mtime_ns),
    }


def lane_payload(
    args: argparse.Namespace,
    source_run_id: str,
    run_root: Path,
    lane: int,
    tasks: list[dict[str, Any]],
    retry_dataset_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    return {
        "lane": lane,
        "tasks": tasks,
        "source_run_id": source_run_id,
        "run_root": str(run_root),
        "database": args.database,
        "clickhouse_url": args.clickhouse_url,
        "user": args.user,
        "password": args.password,
        "parts_root_win": args.parts_root_win,
        "parts_root_ch": args.parts_root_ch,
        "part_manifest_table": args.part_manifest_table,
        "archive_manifest_table": args.archive_manifest_table,
        "storage_policy": args.storage_policy,
        "target_table_uuids": dict(args.target_table_uuids),
        "text_source_table_uuid": args.text_source_table_uuid,
        "insert_max_threads": max(1, int(args.insert_max_threads)),
        "insert_max_memory_usage": args.insert_max_memory_usage,
        "insert_concurrency": max(1, int(args.insert_concurrency)),
        "parquet_row_group_bytes": max(1, int(args.parquet_row_group_mb)) * 1024**2,
        "parquet_file_bytes": max(1, int(args.parquet_file_mb)) * 1024**2,
        "parquet_compression_level": int(args.parquet_compression_level),
        "max_filings_per_archive": max(0, int(args.max_filings_per_archive)),
        "sample_limit_per_archive": max(0, int(args.sample_limit_per_archive)),
        "sample_text_chars": max(0, int(args.sample_text_chars)),
        "parent_window_days_before": max(0, int(args.parent_window_days_before)),
        "parent_window_days_after": max(1, int(args.parent_window_days_after)),
        "min_text_chars": max(0, int(args.min_text_chars)),
        "max_text_chars": max(0, int(args.max_text_chars)),
        "cleanup_parts": bool(args.cleanup_parts),
        "retry_dataset_keys": sorted(retry_dataset_keys),
    }


def run_lanes(payloads: list[dict[str, Any]], progress_layout: str) -> list[dict[str, Any]]:
    import multiprocessing

    results: list[dict[str, Any]] = []
    with multiprocessing.Manager() as manager:
        event_queue = manager.Queue()
        stop_event = manager.Event()
        insert_semaphore = manager.BoundedSemaphore(max(1, int(payloads[0].get("insert_concurrency", 8))))
        for payload in payloads:
            payload["event_queue"] = event_queue
            payload["stop_event"] = stop_event
            payload["insert_semaphore"] = insert_semaphore
        with concurrent.futures.ProcessPoolExecutor(max_workers=len(payloads)) as pool:
            futures = [pool.submit(process_lane, payload) for payload in payloads]
            while futures:
                try:
                    event = event_queue.get(timeout=0.25)
                    emit_event(progress_layout, event)
                except queue.Empty:
                    pass
                done = [future for future in futures if future.done()]
                for future in done:
                    futures.remove(future)
                    results.append(future.result())
            while True:
                try:
                    emit_event(progress_layout, event_queue.get_nowait())
                except queue.Empty:
                    break
    return results


def process_lane(payload: dict[str, Any]) -> dict[str, Any]:
    lane = int(payload["lane"])
    tasks = list(payload["tasks"])
    event_queue = payload["event_queue"]
    stop_event = payload["stop_event"]
    insert_semaphore = payload["insert_semaphore"]
    client = ClickHouseHttpClient(payload["clickhouse_url"], payload["user"], payload["password"])
    args = ingest_namespace(SimpleNamespace(**payload))
    latest_part_status = file_ingest.load_latest_part_status(client, args)
    retry_dataset_keys = {tuple(item) for item in payload.get("retry_dataset_keys", [])}
    archive_results: list[dict[str, Any]] = []
    completed = 0
    for position, task in enumerate(tasks, start=1):
        if stop_event.is_set():
            break
        archive_started = time.perf_counter()
        stages: dict[str, float] = {}
        part_paths: list[Path] = []
        emit_lane(event_queue, lane, task, position, len(tasks), "extract", stages, status="running")
        try:
            if task["kind"] == "recovery":
                result = recovery_result(task, payload)
                stages["extract"] = 0.0
                if task.get("state_path"):
                    write_json(Path(task["state_path"]), {"status": "extracted", "task": task, "result": result})
            else:
                extraction_started = time.perf_counter()
                result = extractor.process_archive_worker(extractor_payload(payload, task))
                stages["extract"] = time.perf_counter() - extraction_started
                if result.get("status") == "cancelled":
                    cleanup_result_parts(result)
                    archive_results.append(cancelled_archive_row(event_queue, lane, task, position, len(tasks), stages, archive_started))
                    break
                if result.get("status") != "ok":
                    cleanup_result_parts(result)
                    raise RuntimeError(f"archive extraction failed: {result.get('errors')}")
                write_json(Path(task["state_path"]), {"status": "extracted", "task": task, "result": result})
            part_paths = [
                Path(path)
                for path in result.get("cleanup_paths", [item["path"] for item in result.get("part_files", [])])
            ]
            if stop_event.is_set():
                archive_results.append(cancelled_archive_row(event_queue, lane, task, position, len(tasks), stages, archive_started, result))
                break
            emit_lane(event_queue, lane, task, position, len(tasks), "preflight", stages, result=result)
            parts, preflight_seconds = build_and_preflight_parts(client, args, task, result)
            stages["preflight"] = preflight_seconds
            if stop_event.is_set():
                archive_results.append(cancelled_archive_row(event_queue, lane, task, position, len(tasks), stages, archive_started, result))
                break
            emit_lane(event_queue, lane, task, position, len(tasks), "insert", stages, result=result)
            insert_started = time.perf_counter()
            cancelled = False
            for part in parts:
                if stop_event.is_set():
                    cancelled = True
                    break
                key = (part.run_id, part.dataset_name, part.part_index)
                if should_skip_part(part, str(task["archive_date"]), latest_part_status, retry_dataset_keys):
                    continue
                acquired = False
                while not stop_event.is_set():
                    if insert_semaphore.acquire(timeout=0.25):
                        acquired = True
                        break
                if not acquired:
                    cancelled = True
                    break
                try:
                    profile = file_ingest.insert_one_part(client, args, part)
                finally:
                    insert_semaphore.release()
                file_ingest.insert_part_manifest(client, args, part, profile)
                if profile.status != "ok":
                    raise RuntimeError(profile.exception)
                latest_part_status[key] = "ok"
            stages["insert"] = time.perf_counter() - insert_started
            if cancelled or stop_event.is_set():
                archive_results.append(cancelled_archive_row(event_queue, lane, task, position, len(tasks), stages, archive_started, result))
                break
            emit_lane(event_queue, lane, task, position, len(tasks), "verify", stages, result=result)
            verify_started = time.perf_counter()
            verify_parts_inserted(parts, latest_part_status)
            insert_archive_manifest(client, payload, task, result, status="ok", error="")
            stages["verify"] = time.perf_counter() - verify_started
            emit_lane(event_queue, lane, task, position, len(tasks), "cleanup", stages, result=result)
            cleanup_started = time.perf_counter()
            cleanup_error = ""
            if payload["cleanup_parts"]:
                try:
                    delete_part_files(part_paths)
                except OSError as exc:
                    cleanup_error = repr(exc)
            stages["cleanup"] = time.perf_counter() - cleanup_started
            completed += 1
            row = archive_result_row(task, result, stages, time.perf_counter() - archive_started, "ok", cleanup_error)
            archive_results.append(row)
            emit_lane(event_queue, lane, task, position, len(tasks), "done", stages, result=result, status="ok")
        except Exception as exc:  # noqa: BLE001
            error = repr(exc)
            stop_event.set()
            try:
                insert_archive_manifest(client, payload, task, {}, status="failed", error=error)
            except Exception:
                pass
            archive_results.append(archive_result_row(task, {}, stages, time.perf_counter() - archive_started, "failed", error))
            emit_lane(event_queue, lane, task, position, len(tasks), "failed", stages, status="failed", error=error)
            break
    return {"lane": lane, "assigned": len(tasks), "completed": completed, "archives": archive_results}


def should_skip_part(
    part: file_ingest.PartFile,
    archive_date: str,
    latest_part_status: dict[tuple[str, str, int], str],
    retry_dataset_keys: set[tuple[str, str, str]],
) -> bool:
    key = (part.run_id, part.dataset_name, part.part_index)
    logical_key = (part.run_id, part.dataset_name, archive_date)
    return latest_part_status.get(key) == "ok" and logical_key not in retry_dataset_keys


def cancelled_archive_row(
    event_queue: Any,
    lane: int,
    task: dict[str, Any],
    position: int,
    total: int,
    stages: dict[str, float],
    archive_started: float,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = "cancelled after peer archive failure"
    result = result or {}
    emit_lane(event_queue, lane, task, position, total, "cancelled", stages, result=result, status="cancelled", error=error)
    return archive_result_row(task, result, stages, time.perf_counter() - archive_started, "cancelled", error)


def extractor_payload(payload: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_path": task["archive_path"],
        "archive_index": task["archive_index"],
        "parts_root": task["parts_root"],
        "source_run_id": task["source_run_id"],
        "database": payload["database"],
        "clickhouse_url": payload["clickhouse_url"],
        "user": payload["user"],
        "password": payload["password"],
        "stop_event": payload.get("stop_event"),
        "max_filings_per_archive": payload["max_filings_per_archive"],
        "sample_limit": payload["sample_limit_per_archive"],
        "sample_text_chars": payload["sample_text_chars"],
        "parent_window_days_before": payload["parent_window_days_before"],
        "parent_window_days_after": payload["parent_window_days_after"],
        "min_text_chars": payload["min_text_chars"],
        "max_text_chars": payload["max_text_chars"],
        "parquet_row_group_bytes": payload["parquet_row_group_bytes"],
        "parquet_file_bytes": payload["parquet_file_bytes"],
        "parquet_compression_level": payload["parquet_compression_level"],
    }


def ingest_namespace(args: Any) -> SimpleNamespace:
    return SimpleNamespace(
        database=args.database,
        part_manifest_table=args.part_manifest_table,
        storage_policy=getattr(args, "storage_policy", ""),
        parts_root_win=getattr(args, "parts_root_win", str(DEFAULT_PARTS_ROOT_WIN)),
        parts_root_ch=getattr(args, "parts_root_ch", DEFAULT_PARTS_ROOT_CH),
        max_threads=max(1, int(getattr(args, "insert_max_threads", 8))),
        max_memory_usage=str(getattr(args, "insert_max_memory_usage", "16G")),
        execute=True,
        force=False,
        retry_failed=True,
        target_table_uuids=dict(getattr(args, "target_table_uuids", {})),
    )


def build_and_preflight_parts(
    _client: ClickHouseHttpClient,
    args: SimpleNamespace,
    task: dict[str, Any],
    result: dict[str, Any],
) -> tuple[list[file_ingest.PartFile], float]:
    started = time.perf_counter()
    parts: list[file_ingest.PartFile] = []
    for item in sorted(result.get("part_files", []), key=lambda row: file_ingest.DATASET_ORDER.get(row["dataset_name"], 99)):
        path = Path(item["path"])
        expected_rows = int(item.get("rows") if item.get("rows") is not None else -1)
        if expected_rows == 0:
            continue
        part = file_ingest.PartFile(
            run_id=str(task["source_run_id"]),
            dataset_name=str(item["dataset_name"]),
            target_table=str(item["target_table"]),
            part_index=int(item["part_index"]),
            windows_path=path,
            clickhouse_path=file_ingest.windows_path_to_clickhouse_path(path, Path(args.parts_root_win), args.parts_root_ch),
            expected_rows=max(0, expected_rows),
            expected_bytes=path.stat().st_size,
            columns=list(item["columns"]),
            structure=str(item.get("structure") or ""),
            file_format=str(item.get("format") or "Parquet"),
            row_groups=int(item.get("row_groups") or 0),
        )
        metadata = validate_parquet_part(path, expected_rows, part.columns)
        actual_rows = metadata["rows"]
        if expected_rows >= 0 and actual_rows != expected_rows:
            raise RuntimeError(f"archive part row mismatch path={path} expected={expected_rows} actual={actual_rows}")
        if expected_rows < 0:
            item["rows"] = actual_rows
            part = file_ingest.PartFile(
                run_id=part.run_id,
                dataset_name=part.dataset_name,
                target_table=part.target_table,
                part_index=part.part_index,
                windows_path=part.windows_path,
                clickhouse_path=part.clickhouse_path,
                expected_rows=actual_rows,
                expected_bytes=part.expected_bytes,
                columns=part.columns,
                structure=part.structure,
                file_format=part.file_format,
                row_groups=metadata["row_groups"],
            )
        if actual_rows == 0:
            continue
        parts.append(part)
    checkpoint_rows = {str(key): int(value) for key, value in result.get("checkpoint_rows", {}).items()}
    result["filing_parent_rows"] = rows_for(result.get("part_files", []), "filing") + checkpoint_rows.get("filing", 0)
    result["document_rows"] = rows_for(result.get("part_files", []), "document") + checkpoint_rows.get("document", 0)
    result["text_source_rows"] = rows_for(result.get("part_files", []), "text_source") + checkpoint_rows.get("text_source", 0)
    result["text_rows"] = rows_for(result.get("part_files", []), "text") + checkpoint_rows.get("text", 0)
    result["skip_rows"] = rows_for(result.get("part_files", []), "skip") + checkpoint_rows.get("skip", 0)
    return parts, time.perf_counter() - started


def verify_parts_inserted(parts: list[file_ingest.PartFile], statuses: dict[tuple[str, str, int], str]) -> None:
    missing = [part.windows_path for part in parts if statuses.get((part.run_id, part.dataset_name, part.part_index)) != "ok"]
    if missing:
        raise RuntimeError(f"archive part manifest verification failed: {missing}")


def archive_date_from_part_path(raw_path: str) -> str:
    filename = str(raw_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    match = PART_ARCHIVE_DATE_PATTERN.search(filename)
    if not match:
        return ""
    return datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()


def build_dataset_checkpoints(
    records: list[file_ingest.PartManifestRecord],
    selected_dates: set[str],
) -> dict[tuple[str, str, str], DatasetCheckpoint]:
    grouped: dict[tuple[str, str, str], list[file_ingest.PartManifestRecord]] = defaultdict(list)
    for record in records:
        expected_table = file_ingest.EXPECTED_TARGET_TABLES.get(record.dataset_name)
        archive_date = archive_date_from_part_path(record.part_path)
        if not expected_table or record.target_table != expected_table or archive_date not in selected_dates:
            continue
        grouped[(record.run_id, record.dataset_name, archive_date)].append(record)

    checkpoints: dict[tuple[str, str, str], DatasetCheckpoint] = {}
    for key, items in grouped.items():
        statuses = {item.status for item in items}
        status = "failed" if "failed" in statuses else "ok" if statuses == {"ok"} else "incomplete"
        checkpoints[key] = DatasetCheckpoint(
            run_id=key[0],
            dataset_name=key[1],
            target_table=file_ingest.EXPECTED_TARGET_TABLES[key[1]],
            archive_date=key[2],
            status=status,
            expected_rows=sum(item.expected_rows for item in items),
            records=tuple(items),
        )
    return checkpoints


def failed_dataset_keys(
    checkpoints: dict[tuple[str, str, str], DatasetCheckpoint],
    completed_units: set[tuple[str, str]],
) -> set[tuple[str, str, str]]:
    return {
        key
        for key, checkpoint in checkpoints.items()
        if checkpoint.status == "failed" and (checkpoint.run_id, checkpoint.archive_date) not in completed_units
    }


def annotate_recovery_tasks(
    tasks: list[dict[str, Any]],
    checkpoints: dict[tuple[str, str, str], DatasetCheckpoint],
    retry_dataset_keys: set[tuple[str, str, str]],
) -> None:
    status_by_part = {
        (record.run_id, record.dataset_name, record.part_index): record.status
        for checkpoint in checkpoints.values()
        for record in checkpoint.records
    }
    for task in tasks:
        run_id = str(task["source_run_id"])
        archive_date = str(task["archive_date"])
        completed_rows: dict[str, int] = {}
        recovered = list(task.get("recovery_part_files") or [])
        if recovered:
            by_dataset: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in recovered:
                by_dataset[str(item["dataset_name"])].append(item)
            for dataset, items in by_dataset.items():
                logical_key = (run_id, dataset, archive_date)
                if logical_key in retry_dataset_keys:
                    continue
                has_stable_part_indexes = all(int(item.get("part_index") or 0) > 0 for item in items)
                exact_parts_complete = has_stable_part_indexes and all(
                    status_by_part.get((run_id, dataset, int(item["part_index"]))) == "ok" for item in items
                )
                checkpoint = checkpoints.get(logical_key)
                legacy_dataset_complete = not has_stable_part_indexes and checkpoint is not None and checkpoint.status == "ok"
                if exact_parts_complete or legacy_dataset_complete:
                    completed_rows[dataset] = sum(max(0, int(item.get("rows") or 0)) for item in items)
        else:
            for dataset in task.get("part_paths", {}):
                logical_key = (run_id, str(dataset), archive_date)
                checkpoint = checkpoints.get(logical_key)
                if logical_key not in retry_dataset_keys and checkpoint and checkpoint.status == "ok":
                    completed_rows[str(dataset)] = checkpoint.expected_rows
        task["completed_dataset_rows"] = completed_rows


def cleanup_failed_dataset_rows(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    checkpoints: dict[tuple[str, str, str], DatasetCheckpoint],
    retry_dataset_keys: set[tuple[str, str, str]],
    *,
    progress_layout: str,
) -> dict[str, Any]:
    if not retry_dataset_keys:
        emit_cleanup(progress_layout, "done", attempts=0, rows=0, batches=0)
        return {"failed_dataset_attempts": 0, "rows_removed": 0, "delete_batches": 0}

    unsupported = sorted(key for key in retry_dataset_keys if key[1] not in DATE_SCOPED_DATASETS)
    if unsupported:
        sample = ", ".join(f"{run_id}/{dataset}/{archive_date}" for run_id, dataset, archive_date in unsupported[:5])
        raise RuntimeError(
            "failed SEC filing-parent parts cannot be date-scoped safely because sec_filing_v3 has no "
            f"source_archive_date; refusing an ambiguous cleanup. examples={sample}"
        )

    grouped_dates: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for key in sorted(retry_dataset_keys):
        checkpoint = checkpoints[key]
        grouped_dates[(checkpoint.run_id, checkpoint.dataset_name, checkpoint.target_table)].append(checkpoint.archive_date)

    emit_cleanup(progress_layout, "scan", attempts=len(retry_dataset_keys), rows=0, batches=0)
    rows_removed = 0
    batches = 0
    batch_size = max(1, int(args.cleanup_date_batch_size))
    for (run_id, dataset, target_table), dates in sorted(grouped_dates.items()):
        for offset in range(0, len(dates), batch_size):
            batch_dates = dates[offset : offset + batch_size]
            predicate = cleanup_predicate(run_id, batch_dates)
            before = scalar_count(
                client,
                f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(target_table)} WHERE {predicate}",
            )
            if before:
                emit_cleanup(
                    progress_layout,
                    "delete",
                    attempts=len(retry_dataset_keys),
                    rows=rows_removed,
                    batches=batches,
                    dataset=dataset,
                    run_id=run_id,
                    archive_dates=len(batch_dates),
                    batch_rows=before,
                )
                client.execute(
                    f"DELETE FROM {quote_ident(args.database)}.{quote_ident(target_table)} "
                    f"WHERE {predicate} SETTINGS lightweight_deletes_sync = 2"
                )
            remaining = scalar_count(
                client,
                f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(target_table)} WHERE {predicate}",
            )
            if remaining:
                raise RuntimeError(
                    f"failed SEC insert cleanup verification failed table={args.database}.{target_table} "
                    f"run_id={run_id} dates={batch_dates[0]}..{batch_dates[-1]} remaining_rows={remaining:,}"
                )
            rows_removed += before
            batches += 1

    emit_cleanup(
        progress_layout,
        "done",
        attempts=len(retry_dataset_keys),
        rows=rows_removed,
        batches=batches,
    )
    return {
        "failed_dataset_attempts": len(retry_dataset_keys),
        "rows_removed": rows_removed,
        "delete_batches": batches,
    }


def cleanup_predicate(run_id: str, archive_dates: list[str]) -> str:
    dates_sql = ", ".join(f"toDate({sql_string(value)})" for value in archive_dates)
    return f"source_run_id = {sql_string(run_id)} AND source_archive_date IN ({dates_sql})"


def scalar_count(client: ClickHouseHttpClient, sql: str) -> int:
    return int((client.execute(sql).strip() or "0").splitlines()[0])


def emit_cleanup(layout: str, stage: str, **details: Any) -> None:
    payload = {"kind": "cleanup", "stage": stage, **details}
    if layout == "events":
        print(EVENT_PREFIX + json.dumps(payload, separators=(",", ":"), ensure_ascii=True), flush=True)
    else:
        detail_text = " ".join(f"{key}={value}" for key, value in details.items())
        print(f"failed_insert_cleanup stage={stage} {detail_text}".rstrip(), flush=True)


def recovery_result(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    all_recovered = list(task.get("recovery_part_files") or [])
    if not all_recovered:
        all_recovered = [
            {
                "dataset_name": dataset,
                "target_table": file_ingest.EXPECTED_TARGET_TABLES[dataset],
                "path": str(path),
                "rows": -1,
                "columns": columns_for_dataset(dataset),
                "format": "JSONEachRow",
            }
            for dataset, path in task.get("part_paths", {}).items()
        ]
    completed_rows = {str(key): int(value) for key, value in task.get("completed_dataset_rows", {}).items()}
    recovered = [item for item in all_recovered if str(item["dataset_name"]) not in completed_rows]
    if recovered and all(str(item.get("format") or "").lower() == "parquet" for item in recovered):
        part_files = recovered
        converted_paths: list[str] = []
    elif recovered:
        part_files, converted_paths = convert_legacy_recovery_parts(task, payload, recovered)
    else:
        part_files = []
        converted_paths = []
    cleanup_paths = list(
        dict.fromkeys(
            [*(str(item["path"]) for item in all_recovered), *converted_paths, *(str(item["path"]) for item in part_files)]
        )
    )
    return {
        "archive_date": task["archive_date"],
        "archive_path": task["archive_path"],
        "status": "ok",
        "part_files": part_files,
        "cleanup_paths": cleanup_paths,
        "checkpoint_rows": completed_rows,
        "filing_parent_rows": rows_for(part_files, "filing") + completed_rows.get("filing", 0),
        "document_rows": rows_for(part_files, "document") + completed_rows.get("document", 0),
        "text_source_rows": rows_for(part_files, "text_source") + completed_rows.get("text_source", 0),
        "text_rows": rows_for(part_files, "text") + completed_rows.get("text", 0),
        "skip_rows": rows_for(part_files, "skip") + completed_rows.get("skip", 0),
        "samples": [],
        "errors": [],
    }


def convert_legacy_recovery_parts(
    task: dict[str, Any],
    payload: dict[str, Any],
    legacy_parts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    converted: list[dict[str, Any]] = []
    source_paths: list[str] = []
    date_key = str(task["archive_date"]).replace("-", "")
    archive_index = int(task.get("archive_index") or date_key)
    for source_number, item in enumerate(legacy_parts, start=1):
        source_path = Path(item["path"])
        dataset = str(item["dataset_name"])
        if source_path.suffix.lower() == ".parquet":
            converted.append(item)
            source_paths.append(str(source_path))
            continue
        columns = columns_for_dataset(dataset)
        target_table = file_ingest.EXPECTED_TARGET_TABLES[dataset]
        writer = ParquetShardWriter(
            dataset_name=dataset,
            target_table=target_table,
            output_directory=Path(payload["run_root"]) / "parts" / PART_DIRECTORIES[dataset],
            filename_prefix=f"{target_table}_part_{date_key}_recovery_{source_number:02d}",
            columns=columns,
            archive_index=archive_index,
            row_group_bytes=int(payload["parquet_row_group_bytes"]),
            file_bytes=int(payload["parquet_file_bytes"]),
            compression_level=int(payload["parquet_compression_level"]),
        )
        converted.extend(convert_json_part(source_path=source_path, writer=writer))
        source_paths.append(str(source_path))
    return converted, [*source_paths, *(item["path"] for item in converted)]


def rows_for(parts: list[dict[str, Any]], dataset: str) -> int:
    return sum(max(0, int(item["rows"])) for item in parts if item["dataset_name"] == dataset)


def columns_for_dataset(dataset: str) -> list[str]:
    return {
        "filing": extractor.FILING_COLUMNS,
        "document": extractor.DOCUMENT_COLUMNS,
        "text_source": extractor.TEXT_SOURCE_COLUMNS,
        "text": extractor.TEXT_COLUMNS,
        "skip": extractor.SKIP_COLUMNS,
    }[dataset]


def discover_recovery_tasks(
    args: argparse.Namespace,
    archive_by_date: dict[str, Path],
    completed_keys: set[str],
    *,
    current_run_root: Path,
) -> list[dict[str, Any]]:
    output_root = Path(args.output_root_win)
    successful_dates_by_run = legacy_successful_dates(Path(args.historical_output_root_win))
    tasks_by_date: dict[str, dict[str, Any]] = {}
    for run_root in sorted((path for path in output_root.glob("*") if path.is_dir() and path != current_run_root), reverse=True):
        source_run_id = f"sec_text_extract_{run_root.name}"
        successful_dates = successful_dates_by_run.get(source_run_id, set())
        for state_path in (run_root / "archive_states").glob("*.json") if (run_root / "archive_states").exists() else []:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                task = dict(state["task"])
                result = dict(state["result"])
            except Exception:
                continue
            archive_date = str(task.get("archive_date") or "")
            archive = archive_by_date.get(archive_date)
            if not archive or archive_identity(archive)["archive_key"] in completed_keys:
                continue
            if all(Path(item["path"]).exists() for item in result.get("part_files", [])):
                tasks_by_date.setdefault(
                    archive_date,
                    {
                        **task,
                        "kind": "recovery",
                        "recovery_part_files": result["part_files"],
                        "state_path": str(state_path),
                    },
                )
        for archive_date in sorted(successful_dates):
            archive = archive_by_date.get(archive_date)
            if not archive or archive_date in tasks_by_date:
                continue
            identity = archive_identity(archive)
            if identity["archive_key"] in completed_keys:
                continue
            part_paths = legacy_part_paths(run_root, archive_date)
            if part_paths:
                tasks_by_date[archive_date] = {
                    "kind": "recovery",
                    **identity,
                    "source_run_id": source_run_id,
                    "part_paths": {key: str(value) for key, value in part_paths.items()},
                    "archive_index": int(archive_date.replace("-", "")),
                    "state_path": str(
                        current_run_root
                        / "archive_states"
                        / f"{archive_date.replace('-', '')}_{identity['archive_key'][:12]}.json"
                    ),
                }
    return [tasks_by_date[key] for key in sorted(tasks_by_date)]


def legacy_successful_dates(historical_root: Path) -> dict[str, set[str]]:
    output: dict[str, set[str]] = {}
    if not historical_root.exists():
        return output
    for log_path in historical_root.glob("*/logs/text-extract.log"):
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r"source_run_id=(sec_text_extract_\d{8}_\d{6})", text)
        if not match:
            continue
        dates = {
            datetime.strptime(value, "%Y%m%d").date().isoformat()
            for value in re.findall(r"last=(\d{8})\.nc\.tar\.gz status=ok", text)
        }
        output.setdefault(match.group(1), set()).update(dates)
    return output


def legacy_part_paths(run_root: Path, archive_date: str) -> dict[str, Path]:
    date_key = archive_date.replace("-", "")
    paths: dict[str, Path] = {}
    for dataset, directory in PART_DIRECTORIES.items():
        matches = sorted((run_root / "parts" / directory).glob(f"*_{date_key}_*.jsonl*"))
        if len(matches) != 1:
            return {}
        paths[dataset] = matches[0]
    return paths


def create_archive_manifest_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    settings = file_ingest.merge_tree_settings(args.storage_policy)
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.archive_manifest_table)}
(
    archive_key String,
    archive_date Date,
    archive_path String,
    archive_size UInt64,
    archive_mtime_ns UInt64,
    source_run_id String,
    text_source_table_uuid String,
    status LowCardinality(String),
    filing_rows UInt64,
    document_rows UInt64,
    text_source_rows UInt64,
    rendered_text_rows UInt64,
    skip_rows UInt64,
    error String,
    updated_at_utc DateTime64(9, 'UTC') DEFAULT now64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY archive_key
{settings}
"""
    )
    client.execute(
        f"ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.archive_manifest_table)} "
        "ADD COLUMN IF NOT EXISTS text_source_table_uuid String AFTER source_run_id"
    )


def load_completed_archive_keys(client: ClickHouseHttpClient, args: argparse.Namespace) -> set[str]:
    text = client.execute(
        f"""
SELECT archive_key
FROM {quote_ident(args.database)}.{quote_ident(args.archive_manifest_table)} FINAL
WHERE status = 'ok'
  AND text_source_table_uuid = {sql_string(args.text_source_table_uuid)}
FORMAT TSV
"""
    )
    return {line.strip() for line in text.splitlines() if line.strip()}


def load_completed_archive_units(client: ClickHouseHttpClient, args: argparse.Namespace) -> set[tuple[str, str]]:
    text = client.execute(
        f"""
SELECT source_run_id, toString(archive_date)
FROM {quote_ident(args.database)}.{quote_ident(args.archive_manifest_table)} FINAL
WHERE status = 'ok'
  AND text_source_table_uuid = {sql_string(args.text_source_table_uuid)}
FORMAT TSV
"""
    )
    output: set[tuple[str, str]] = set()
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[0] and fields[1]:
            output.add((fields[0], fields[1]))
    return output


def insert_archive_manifest(
    client: ClickHouseHttpClient,
    payload: dict[str, Any],
    task: dict[str, Any],
    result: dict[str, Any],
    *,
    status: str,
    error: str,
) -> None:
    row = {
        "archive_key": task["archive_key"],
        "archive_date": task["archive_date"],
        "archive_path": task["archive_path"],
        "archive_size": int(task["archive_size"]),
        "archive_mtime_ns": int(task["archive_mtime_ns"]),
        "source_run_id": task["source_run_id"],
        "text_source_table_uuid": payload["text_source_table_uuid"],
        "status": status,
        "filing_rows": int(result.get("filing_parent_rows") or 0),
        "document_rows": int(result.get("document_rows") or 0),
        "text_source_rows": int(result.get("text_source_rows") or 0),
        "rendered_text_rows": int(result.get("text_rows") or 0),
        "skip_rows": int(result.get("skip_rows") or 0),
        "error": error,
    }
    client.execute(
        f"INSERT INTO {quote_ident(payload['database'])}.{quote_ident(payload['archive_manifest_table'])} "
        "SETTINGS date_time_input_format = 'best_effort' FORMAT JSONEachRow\n"
        + json.dumps(row, ensure_ascii=False)
    )


def ensure_target_tables(
    client: ClickHouseHttpClient,
    database: str,
    storage_policy: str,
) -> tuple[set[str], dict[str, str]]:
    missing: set[str] = set()
    for table in file_ingest.EXPECTED_TARGET_TABLES.values():
        exists = int(
            (client.execute(f"SELECT count() FROM system.tables WHERE database={sql_string(database)} AND name={sql_string(table)}").strip() or "0").splitlines()[0]
        )
        if exists != 1:
            missing.add(table)
    if missing:
        archive_tables = {
            "sec_filing_document_v3",
            "sec_filing_text_v3",
            "sec_filing_text_rendered_v3",
            "sec_filing_document_skip_v3",
        }
        unsupported = missing - archive_tables
        if unsupported:
            raise RuntimeError(f"missing non-archive SEC v3 target tables in {database}: {sorted(unsupported)}")
        if not str(storage_policy).strip():
            raise RuntimeError(
                "CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required to create missing SEC v3 text targets"
            )
        raw_sql = text_schema.DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8")
        rendered_sql = text_schema.render_schema(raw_sql, database, str(storage_policy), False)
        statements = text_schema.split_sql_statements(rendered_sql)
        for statement in statements:
            client.execute(statement)

    validate_source_text_layout(client, database)
    target_table_uuids = file_ingest.load_target_table_uuids(client, database)
    unresolved = set(file_ingest.EXPECTED_TARGET_TABLES.values()) - set(target_table_uuids)
    if unresolved:
        raise RuntimeError(f"missing SEC v3 target tables after schema creation in {database}: {sorted(unresolved)}")
    return missing, target_table_uuids


def validate_source_text_layout(client: ClickHouseHttpClient, database: str) -> None:
    table = file_ingest.EXPECTED_TARGET_TABLES["text_source"]
    text = client.execute(
        f"SELECT partition_key, sorting_key FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name = {sql_string(table)} FORMAT TSV"
    )
    fields = (text.strip().splitlines() or [""])[0].split("\t")
    partition_key = fields[0] if fields else ""
    sorting_key = fields[1] if len(fields) > 1 else ""
    expected_partition = "toYYYYMM(source_archive_date)"
    expected_sorting = "cik,accession_number,document_id,content_format"
    if normalized_clickhouse_key(partition_key) != normalized_clickhouse_key(expected_partition) or normalized_clickhouse_key(
        sorting_key
    ) != normalized_clickhouse_key(expected_sorting):
        raise RuntimeError(
            f"{database}.{table} has incompatible layout partition_key={partition_key!r} sorting_key={sorting_key!r}; "
            f"expected partition_key={expected_partition!r} sorting_key={expected_sorting!r}. "
            "Drop the stale source-text table before running the historical rebuild."
        )


def normalized_clickhouse_key(value: str) -> str:
    return re.sub(r"[\s`()]+", "", value).lower()


def emit_lane(
    event_queue: Any,
    lane: int,
    task: dict[str, Any],
    position: int,
    total: int,
    stage: str,
    stages: dict[str, float],
    *,
    result: dict[str, Any] | None = None,
    status: str = "running",
    error: str = "",
) -> None:
    result = result or {}
    temp_bytes = sum(Path(item["path"]).stat().st_size for item in result.get("part_files", []) if Path(item["path"]).exists())
    event_queue.put(
        {
            "kind": "lane",
            "lane": lane,
            "archive": Path(task["archive_path"]).name,
            "position": position,
            "total": total,
            "stage": stage,
            "durations": {key: round(value, 3) for key, value in stages.items()},
            "rows": sum(int(result.get(key) or 0) for key in ("document_rows", "text_source_rows", "text_rows", "skip_rows")),
            "temp_bytes": temp_bytes,
            "status": status,
            "error": error[:2000],
            "recovery": task["kind"] == "recovery",
        }
    )


def emit_event(layout: str, payload: dict[str, Any]) -> None:
    if layout == "events":
        print(EVENT_PREFIX + json.dumps(payload, separators=(",", ":"), ensure_ascii=True), flush=True)
    elif payload.get("kind") == "lane" and payload.get("stage") in {"done", "failed", "cancelled"}:
        print(
            f"lane={payload['lane']} archive={payload['archive']} progress={payload['position']}/{payload['total']} "
            f"status={payload['status']} rows={payload['rows']}",
            flush=True,
        )


def archive_result_row(
    task: dict[str, Any],
    result: dict[str, Any],
    stages: dict[str, float],
    elapsed: float,
    status: str,
    error: str,
) -> dict[str, Any]:
    return {
        "archive_key": task["archive_key"],
        "archive_date": task["archive_date"],
        "archive_path": task["archive_path"],
        "status": status,
        "recovery": task["kind"] == "recovery",
        "elapsed_seconds": round(elapsed, 3),
        "stage_seconds": {key: round(value, 3) for key, value in stages.items()},
        "filing_rows": int(result.get("filing_parent_rows") or 0),
        "document_rows": int(result.get("document_rows") or 0),
        "text_source_rows": int(result.get("text_source_rows") or 0),
        "rendered_text_rows": int(result.get("text_rows") or 0),
        "skip_rows": int(result.get("skip_rows") or 0),
        "error": error,
    }


def delete_part_files(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def cleanup_result_parts(result: dict[str, Any]) -> None:
    delete_part_files([Path(item["path"]) for item in result.get("part_files", [])])


def cleanup_empty_part_directories(output_root: Path) -> None:
    for parts_root in output_root.glob("*/parts"):
        for directory in sorted((path for path in parts_root.rglob("*") if path.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass


def cleanup_obsolete_incomplete_parts(output_root: Path, current_run_root: Path, archive_by_date: dict[str, Path]) -> None:
    valid_dates = set(archive_by_date)
    date_pattern = re.compile(r"_(\d{8})_\d{6}(?:_\d{2})?\.(?:parquet|jsonl(?:\.gz)?)$")
    for run_root in output_root.glob("*"):
        if not run_root.is_dir() or run_root == current_run_root:
            continue
        if (run_root / "sec_filing_text_extract_manifest.json").exists():
            continue
        candidates = (
            [
                *list((run_root / "parts").rglob("*.parquet")),
                *list((run_root / "parts").rglob("*.jsonl*")),
            ]
            if (run_root / "parts").exists()
            else []
        )
        for path in candidates:
            match = date_pattern.search(path.name)
            if not match:
                continue
            archive_date = datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()
            if archive_date in valid_dates:
                path.unlink()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def write_run_summary(
    run_root: Path,
    args: argparse.Namespace,
    source_run_id: str,
    archive_count: int,
    completed: int,
    lane_results: list[dict[str, Any]],
    loaded_env: list[Path],
    failed_insert_cleanup: dict[str, Any],
) -> None:
    archives = [row for lane in lane_results for row in lane.get("archives", [])]
    payload = {
        "source_run_id": source_run_id,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "archive_count": archive_count,
        "archives_completed": completed,
        "archives_failed": sum(1 for row in archives if row.get("status") == "failed"),
        "archives_cancelled": sum(1 for row in archives if row.get("status") == "cancelled"),
        "workers": bounded_worker_count(args.workers, archive_count),
        "cleanup_parts": bool(args.cleanup_parts),
        "parquet_row_group_mb": int(args.parquet_row_group_mb),
        "parquet_file_mb": int(args.parquet_file_mb),
        "parquet_compression_level": int(args.parquet_compression_level),
        "loaded_env_files": [str(path) for path in loaded_env],
        "failed_insert_cleanup": failed_insert_cleanup,
        "archives": archives,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    write_json(run_root / "sec_filing_archive_rebuild_summary.json", payload)


def print_header(args: argparse.Namespace, run_root: Path, archives: list[Path], loaded_env: list[Path]) -> None:
    print("=" * 96, flush=True)
    print("SEC bounded archive extract and ingest", flush=True)
    print(f"range=[{args.start_date},{args.end_date}) archives={len(archives):,} workers={bounded_worker_count(args.workers, len(archives))}", flush=True)
    print(
        f"run_root={run_root} parquet_row_group_mb={args.parquet_row_group_mb} "
        f"parquet_file_mb={args.parquet_file_mb} insert_concurrency={args.insert_concurrency} "
        f"cleanup_parts={args.cleanup_parts}",
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in loaded_env]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(extractor.secret_keys()), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar import sec_archive_identity_audit as identity_audit  # noqa: E402
from pipelines.sec.edgar import sec_filing_text_clickhouse_file_ingest as file_ingest  # noqa: E402
from pipelines.sec.edgar import sec_filing_text_extract_parts as extractor  # noqa: E402
from pipelines.sec.edgar.sec_filing_archive_rebuild import build_and_preflight_parts  # noqa: E402
from pipelines.sec.edgar.sec_missing_document_repair import cleanup_parts  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import ensure_sec_write_database  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


DOCUMENT_TABLES_CHILD_FIRST = (
    "sec_filing_document_skip_v3",
    "sec_filing_text_rendered_v3",
    "sec_filing_text_v3",
    "sec_filing_document_v3",
)
MODEL_TABLES = ("sec_filing_text_tokens_v3", "sec_filing_text_embeddings_v3")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair SEC document/text rows stored under a non-primary entity CIK. "
            "Correct rows are parsed and verified before stale rows are synchronously deleted."
        )
    )
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_WRITE_DATABASE", "q_live"))
    parser.add_argument("--submissions-database", default=os.environ.get("SEC_BULK_MIRROR_DATABASE", "sec_core"))
    parser.add_argument("--submissions-table", default="sec_bulk_mirror_filing_v3")
    parser.add_argument("--submissions-overlay-table", default="sec_submissions_filing_overlay_v3")
    parser.add_argument("--model-database", default="market_sip_compact")
    parser.add_argument("--output-root-win", default="D:/market-data/prepared/sec_archive_identity_repair")
    parser.add_argument("--parts-root-win", default=os.environ.get("SEC_TEXT_PARTS_ROOT_WIN", "D:/market-data"))
    parser.add_argument("--parts-root-ch", default=os.environ.get("SEC_TEXT_PARTS_ROOT_CH", "/mnt/d/market-data"))
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--insert-max-threads", type=int, default=8)
    parser.add_argument("--insert-max-memory-usage", default="16G")
    parser.add_argument("--part-manifest-table", default="sec_filing_text_file_ingest_manifest_v3")
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument("--max-text-chars", type=int, default=0)
    parser.add_argument("--parquet-row-group-mb", type=int, default=256)
    parser.add_argument("--parquet-file-mb", type=int, default=1024)
    parser.add_argument("--parquet-compression-level", type=int, default=1)
    parser.add_argument("--limit-accessions", type=int, default=0)
    parser.add_argument("--mutations-sync", type=int, default=2, choices=[1, 2])
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    ensure_sec_write_database(client, read_database=args.database, write_database=args.database)
    candidates, discovery = discover_mismatches(client, args)
    print("identity_discovery=" + json.dumps(discovery, sort_keys=True), flush=True)
    print(f"repairable_identity_mismatches={len(candidates):,} execute={args.execute}", flush=True)
    if not candidates:
        return 0
    if not args.execute:
        for row in candidates:
            print("candidate=" + json.dumps(row, sort_keys=True), flush=True)
        return 0

    run_id = "sec_archive_identity_repair_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    parts_root = run_root / "parts"
    parts_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_root / "sec_archive_identity_repair_manifest.json"
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "status": "active",
        "execute": True,
        "candidates": candidates,
        "discovery_before": discovery,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    ingest_args = ingest_namespace(args)
    file_ingest.create_part_manifest_table(client, ingest_args)
    ingest_args.target_table_uuids = file_ingest.load_target_table_uuids(client, args.database)

    try:
        extraction = extract_and_insert(client, args, candidates, run_id, parts_root, ingest_args)
        verification = verify_replacements(client, args.database, candidates, run_id)
        cleanup = delete_stale_identities(client, args, candidates)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = repr(exc)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        raise

    manifest.update({
        "status": "ok",
        "extraction": extraction,
        "verification": verification,
        "cleanup": cleanup,
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("summary=" + json.dumps({
        "run_id": run_id,
        "repaired_identities": len(candidates),
        "inserted_documents": extraction["document_rows"],
        "deleted_stale_rows": sum(cleanup.values()),
        "stale_keys_verified_deleted": len(candidates),
        "manifest": str(manifest_path),
    }, sort_keys=True), flush=True)
    return 0


def validate_args(args: argparse.Namespace) -> None:
    for name in ("database", "submissions_database", "submissions_table", "submissions_overlay_table", "model_database"):
        value = str(getattr(args, name))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise SystemExit(f"--{name.replace('_', '-')} must be a simple ClickHouse identifier: {value!r}")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.max_text_chars < 0:
        raise SystemExit("--max-text-chars must be >= 0")


def discover_mismatches(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = identity_audit.load_unconfirmed_document_identities(client, args)
    grouped = identity_audit.group_unconfirmed_identities(rows)
    candidates: list[dict[str, Any]] = []
    totals = {"archives": len(grouped), "relationships": len(rows), "matched": 0, "mismatched": 0, "missing": 0, "archive_errors": 0}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(identity_audit.audit_archive, archive_path, wanted): archive_path
            for archive_path, wanted in sorted(grouped.items())
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            for key in ("matched", "mismatched", "missing", "archive_errors"):
                totals[key] += int(result[key])
            if result["missing"] or result["archive_errors"]:
                raise RuntimeError(f"identity discovery could not read authoritative archive input: {result}")
            for mismatch in result["mismatches"]:
                candidates.append({"archive_path": result["archive_path"], **mismatch})
    candidates.sort(key=lambda row: (row["archive_path"], row["member"], row["stored_cik"]))
    if args.limit_accessions:
        candidates = candidates[: max(0, int(args.limit_accessions))]
    return candidates, totals


def extract_and_insert(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    candidates: list[dict[str, Any]],
    run_id: str,
    parts_root: Path,
    ingest_args: SimpleNamespace,
) -> dict[str, int]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[str(row["archive_path"])].append(row)
    totals = {"archives": 0, "filings": 0, "document_rows": 0, "text_source_rows": 0, "text_rows": 0, "skip_rows": 0}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(extractor.process_archive_worker, worker_payload(args, run_id, parts_root, index, path, rows)): path
            for index, (path, rows) in enumerate(grouped.items(), start=1)
        }
        try:
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result.get("status") != "ok":
                    raise RuntimeError(f"identity repair extraction failed: {result.get('errors')}")
                task = {"source_run_id": run_id}
                parts, _ = build_and_preflight_parts(client, ingest_args, task, result)
                file_ingest.validate_target_tables(client, ingest_args, parts)
                for part in parts:
                    profile = file_ingest.insert_one_part(client, ingest_args, part)
                    file_ingest.insert_part_manifest(client, ingest_args, part, profile)
                    if profile.status != "ok":
                        raise RuntimeError(profile.exception)
                totals["archives"] += 1
                for key in ("filings", "document_rows", "text_source_rows", "text_rows", "skip_rows"):
                    totals[key] += int(result.get(key) or 0)
                cleanup_parts(result)
                print(
                    f"identity repair archives={totals['archives']:,}/{len(grouped):,} "
                    f"filings={totals['filings']:,} documents={totals['document_rows']:,}",
                    flush=True,
                )
        except Exception:
            for future in futures:
                future.cancel()
            raise
    return totals


def worker_payload(
    args: argparse.Namespace,
    run_id: str,
    parts_root: Path,
    archive_index: int,
    archive_path: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "archive_path": archive_path,
        "archive_index": archive_index,
        "parts_root": str(parts_root),
        "source_run_id": run_id,
        "database": args.database,
        "clickhouse_url": args.clickhouse_url,
        "user": args.user,
        "password": args.password,
        "max_filings_per_archive": 0,
        "sample_limit": 0,
        "sample_text_chars": 0,
        "parent_window_days_before": 3,
        "parent_window_days_after": 3,
        "min_text_chars": args.min_text_chars,
        "max_text_chars": args.max_text_chars,
        "parquet_row_group_bytes": args.parquet_row_group_mb * 1024**2,
        "parquet_file_bytes": args.parquet_file_mb * 1024**2,
        "parquet_compression_level": args.parquet_compression_level,
        "target_accessions": sorted({row["sgml_accession"] for row in rows}),
        "target_members": sorted({row["member"] for row in rows}),
        "parent_rows": [],
    }


def ingest_namespace(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        database=args.database,
        part_manifest_table=args.part_manifest_table,
        storage_policy="",
        parts_root_win=args.parts_root_win,
        parts_root_ch=args.parts_root_ch,
        max_threads=max(1, args.insert_max_threads),
        max_memory_usage=args.insert_max_memory_usage,
        execute=True,
        force=False,
        retry_failed=True,
        target_table_uuids={},
    )


def verify_replacements(
    client: ClickHouseHttpClient, database: str, candidates: list[dict[str, Any]], run_id: str
) -> dict[str, int]:
    db = quote_ident(database)
    totals = {"documents": 0, "sources": 0, "rendered": 0, "skips": 0}
    for row in candidates:
        cik = sql_string(str(row["sgml_cik"]))
        accession = sql_string(str(row["sgml_accession"]))
        run = sql_string(run_id)
        result = json.loads(client.execute(
            f"""
SELECT
    count() AS documents,
    (SELECT count() FROM {db}.sec_filing_text_v3 WHERE cik={cik} AND accession_number={accession} AND source_run_id={run}) AS sources,
    (SELECT count() FROM {db}.sec_filing_text_rendered_v3 WHERE cik={cik} AND accession_number={accession} AND source_run_id={run}) AS rendered,
    (SELECT count() FROM {db}.sec_filing_document_skip_v3 WHERE cik={cik} AND accession_number={accession} AND source_run_id={run}) AS skips
FROM {db}.sec_filing_document_v3
WHERE cik={cik} AND accession_number={accession} AND source_run_id={run}
FORMAT JSONEachRow
"""
        ).strip())
        expected = int(row["expected_document_count"])
        if int(result["documents"]) != expected:
            raise RuntimeError(
                f"replacement verification failed accession={row['sgml_accession']} cik={row['sgml_cik']} "
                f"expected_documents={expected} observed={result['documents']}"
            )
        if int(result["rendered"]) + int(result["skips"]) != expected:
            raise RuntimeError(f"replacement text/skip lineage incomplete: {row} result={result}")
        for key in totals:
            totals[key] += int(result[key])
    return totals


def delete_stale_identities(
    client: ClickHouseHttpClient, args: argparse.Namespace, candidates: list[dict[str, Any]]
) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in candidates:
        predicate = identity_predicate(str(row["stored_cik"]), str(row["stored_accession"]))
        for table in MODEL_TABLES:
            if identity_audit.table_exists(client, args.model_database, table):
                totals[f"{args.model_database}.{table}"] += delete_and_verify(
                    client, args.model_database, table, predicate, args.mutations_sync
                )
        for table in DOCUMENT_TABLES_CHILD_FIRST:
            totals[f"{args.database}.{table}"] += delete_and_verify(
                client, args.database, table, predicate, args.mutations_sync
            )
    return dict(totals)


def identity_predicate(cik: str, accession_number: str) -> str:
    return f"cik={sql_string(cik)} AND accession_number={sql_string(accession_number)}"


def delete_and_verify(
    client: ClickHouseHttpClient, database: str, table: str, predicate: str, mutations_sync: int
) -> int:
    target = f"{quote_ident(database)}.{quote_ident(table)}"
    before = int(client.execute(f"SELECT count() FROM {target} FINAL WHERE {predicate} FORMAT TSV").strip() or "0")
    if before:
        client.execute(f"ALTER TABLE {target} DELETE WHERE {predicate} SETTINGS mutations_sync={int(mutations_sync)}")
    after = int(client.execute(f"SELECT count() FROM {target} FINAL WHERE {predicate} FORMAT TSV").strip() or "0")
    if after:
        raise RuntimeError(f"stale SEC identity deletion incomplete table={target} remaining={after}")
    return before


if __name__ == "__main__":
    raise SystemExit(main())

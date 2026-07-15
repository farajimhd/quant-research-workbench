from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar import sec_filing_text_clickhouse_file_ingest as file_ingest  # noqa: E402
from pipelines.sec.edgar import sec_filing_text_extract_parts as extractor  # noqa: E402
from pipelines.sec.edgar.sec_filing_archive_rebuild import build_and_preflight_parts  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import ensure_sec_write_database  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url,
    default_clickhouse_user, quote_ident,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair archive-backed SEC filing parents whose documents were not inserted.")
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_WRITE_DATABASE", "q_live"))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_MISSING_DOCUMENT_REPAIR_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_missing_document_repair"))
    parser.add_argument("--parts-root-win", default=os.environ.get("SEC_TEXT_PARTS_ROOT_WIN", "D:/market-data"))
    parser.add_argument("--parts-root-ch", default=os.environ.get("SEC_TEXT_PARTS_ROOT_CH", "/mnt/d/market-data"))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SEC_MISSING_DOCUMENT_REPAIR_WORKERS", "32")))
    parser.add_argument("--insert-max-threads", type=int, default=8)
    parser.add_argument("--insert-max-memory-usage", default="16G")
    parser.add_argument("--part-manifest-table", default="sec_filing_text_file_ingest_manifest_v3")
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument("--max-text-chars", type=int, default=0)
    parser.add_argument("--parquet-row-group-mb", type=int, default=256)
    parser.add_argument("--parquet-file-mb", type=int, default=1024)
    parser.add_argument("--parquet-compression-level", type=int, default=1)
    parser.add_argument("--limit-accessions", type=int, default=0)
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    ensure_sec_write_database(client, read_database=args.database, write_database=args.database)
    classification = classify_missing_parents(client, args.database)
    candidates = load_candidates(client, args.database, args.limit_accessions)
    print("classification=" + json.dumps(classification, sort_keys=True), flush=True)
    print(f"repairable_archive_backed={len(candidates):,} execute={args.execute}", flush=True)
    if not args.execute or not candidates:
        return 0

    run_id = "sec_missing_document_repair_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    parts_root = run_root / "parts"
    parts_root.mkdir(parents=True, exist_ok=True)
    groups = group_candidates(candidates)
    ingest_args = ingest_namespace(args)
    file_ingest.create_part_manifest_table(client, ingest_args)
    ingest_args.target_table_uuids = file_ingest.load_target_table_uuids(client, args.database)
    completed_archives = repaired = document_rows = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(extractor.process_archive_worker, worker_payload(args, run_id, parts_root, index, path, rows)): path
            for index, (path, rows) in enumerate(groups.items(), start=1)
        }
        try:
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result.get("status") != "ok":
                    raise RuntimeError(f"missing-document extraction failed: {result.get('errors')}")
                task = {"source_run_id": run_id}
                parts, _ = build_and_preflight_parts(client, ingest_args, task, result)
                file_ingest.validate_target_tables(client, ingest_args, parts)
                for part in parts:
                    profile = file_ingest.insert_one_part(client, ingest_args, part)
                    file_ingest.insert_part_manifest(client, ingest_args, part, profile)
                    if profile.status != "ok":
                        raise RuntimeError(profile.exception)
                repaired += int(result.get("filings") or 0)
                document_rows += int(result.get("document_rows") or 0)
                completed_archives += 1
                cleanup_parts(result)
                print(
                    f"repair archives={completed_archives:,}/{len(groups):,} filings={repaired:,}/{len(candidates):,} "
                    f"last={Path(futures[future]).name} document_rows={document_rows:,}", flush=True
                )
        except Exception:
            for future in futures:
                future.cancel()
            raise
    after = classify_missing_parents(client, args.database)
    if int(after["archive_backed_repairable"]):
        raise RuntimeError(f"archive-backed document repair incomplete: {after}")
    print("after=" + json.dumps(after, sort_keys=True), flush=True)
    return 0


def classify_missing_parents(client: ClickHouseHttpClient, database: str) -> dict[str, int]:
    text = client.execute(classification_sql(database)).strip()
    return {key: int(value) for key, value in json.loads(text or "{}").items()}


def classification_sql(database: str) -> str:
    db = quote_ident(database)
    return f"""
WITH
parents AS (
  SELECT f.cik, f.accession_number
  FROM {db}.sec_filing_v3 AS f FINAL
  LEFT ANTI JOIN (SELECT DISTINCT cik, accession_number FROM {db}.sec_filing_document_v3 FINAL) AS d USING (cik, accession_number)
),
docs_any AS (SELECT DISTINCT accession_number FROM {db}.sec_filing_document_v3 FINAL),
inventory AS (SELECT * FROM {db}.sec_filing_archive_accession_current_v3 WHERE source_kind='daily_archive')
SELECT count() AS parents_without_exact_documents,
       countIf(d.accession_number != '') AS documents_under_other_cik,
       countIf(d.accession_number = '' AND i.accession_number != '' AND i.document_count > 0 AND p.cik = i.primary_cik) AS archive_backed_repairable,
       countIf(d.accession_number = '' AND i.accession_number != '' AND i.document_count = 0) AS archive_member_without_documents,
       countIf(d.accession_number = '' AND i.accession_number = '') AS metadata_only_not_disseminated,
       countIf(d.accession_number = '' AND i.accession_number != '' AND i.document_count > 0 AND p.cik != i.primary_cik) AS archive_identity_mismatch
FROM parents AS p
LEFT JOIN docs_any AS d USING (accession_number)
LEFT JOIN inventory AS i USING (accession_number)
FORMAT JSONEachRow
"""


def load_candidates(client: ClickHouseHttpClient, database: str, limit: int) -> list[dict[str, Any]]:
    db = quote_ident(database)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    text = client.execute(
        f"""
SELECT i.source_archive_path, i.source_archive_member,
       f.filing_id, f.accession_number, f.accession_number_compact, f.cik, f.form_type,
       toString(f.accepted_at_utc) AS accepted_at_utc, ifNull(f.primary_document,'') AS primary_document,
       ifNull(f.primary_document_url,'') AS primary_document_url, ifNull(f.filing_detail_url,'') AS filing_detail_url
FROM {db}.sec_filing_v3 AS f FINAL
INNER JOIN {db}.sec_filing_archive_accession_current_v3 AS i
  ON f.accession_number=i.accession_number AND f.cik=i.primary_cik
LEFT ANTI JOIN (SELECT DISTINCT accession_number FROM {db}.sec_filing_document_v3 FINAL) AS d
  ON f.accession_number=d.accession_number
WHERE i.source_kind='daily_archive' AND i.document_count > 0
ORDER BY i.source_archive_date, i.source_archive_member
{limit_sql}
FORMAT JSONEachRow
"""
    )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def group_candidates(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_archive_path"])].append(row)
    return dict(grouped)


def worker_payload(
    args: argparse.Namespace, run_id: str, parts_root: Path, archive_index: int,
    archive_path: str, rows: list[dict[str, Any]],
) -> dict[str, Any]:
    parent_rows = [
        {key: row[key] for key in (
            "filing_id", "accession_number", "accession_number_compact", "cik", "form_type",
            "accepted_at_utc", "primary_document", "primary_document_url", "filing_detail_url",
        )}
        for row in rows
    ]
    return {
        "archive_path": archive_path, "archive_index": archive_index, "parts_root": str(parts_root),
        "source_run_id": run_id, "database": args.database, "clickhouse_url": args.clickhouse_url,
        "user": args.user, "password": args.password, "max_filings_per_archive": 0,
        "sample_limit": 0, "sample_text_chars": 0, "parent_window_days_before": 0,
        "parent_window_days_after": 1, "min_text_chars": args.min_text_chars,
        "max_text_chars": args.max_text_chars,
        "parquet_row_group_bytes": args.parquet_row_group_mb * 1024**2,
        "parquet_file_bytes": args.parquet_file_mb * 1024**2,
        "parquet_compression_level": args.parquet_compression_level,
        "target_accessions": [row["accession_number"] for row in rows],
        "target_members": [row["source_archive_member"] for row in rows],
        "parent_resolution_mode": "supplied_only",
        "parent_rows": parent_rows,
    }


def ingest_namespace(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        database=args.database, part_manifest_table=args.part_manifest_table, storage_policy="",
        parts_root_win=args.parts_root_win, parts_root_ch=args.parts_root_ch,
        max_threads=max(1, args.insert_max_threads), max_memory_usage=args.insert_max_memory_usage,
        execute=True, force=False, retry_failed=True, target_table_uuids={},
    )


def cleanup_parts(result: dict[str, Any]) -> None:
    for item in result.get("part_files", []):
        path = Path(item["path"])
        if path.exists():
            path.unlink()
    for directory in {Path(item["path"]).parent for item in result.get("part_files", [])}:
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())

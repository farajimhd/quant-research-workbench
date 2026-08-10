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
from pipelines.sec.edgar.sec_missing_document_repair import cleanup_parts, prune_empty_part_directories  # noqa: E402
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
CANONICAL_TABLES_CHILD_FIRST = (
    ("sec_filing_entity_v3", "primary_cik"),
    ("sec_filing_archive_accession_v3", "primary_cik"),
    ("sec_filing_v3", "cik"),
)
MODEL_TABLES = ("sec_filing_text_tokens_v3", "sec_filing_text_embeddings_v3")
LIVE_INGEST_MANIFEST_TABLE = "sec_filing_live_ingest_manifest_v3"


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
    parser.add_argument(
        "--archive-fallback-root-win",
        default=os.environ.get("SEC_ARCHIVE_FALLBACK_ROOT_WIN", ""),
        help=(
            "Optional daily_archives root used only when a stored source_archive_path no longer exists. "
            "The suffix below daily_archives is preserved."
        ),
    )
    parser.add_argument("--parts-root-win", default=os.environ.get("SEC_TEXT_PARTS_ROOT_WIN", "D:/market-data"))
    parser.add_argument("--parts-root-ch", default=os.environ.get("SEC_TEXT_PARTS_ROOT_CH", "/mnt/d/market-data"))
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--insert-max-threads", type=int, default=8)
    parser.add_argument("--insert-max-memory-usage", default="16G")
    parser.add_argument("--part-manifest-table", default="sec_filing_text_file_ingest_manifest_v3")
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
    extraction_candidates, discovery = discover_mismatches(client, args)
    recovery_candidates = discover_interrupted_cleanup_candidates(client, args)
    for row in recovery_candidates:
        row["archive_path"] = identity_audit.resolve_archive_path(
            str(row["archive_path"]), args.archive_fallback_root_win
        )
    extraction_keys = {
        (str(row["stored_cik"]), str(row["stored_accession"]), str(row["source_version_key"]))
        for row in extraction_candidates
    }
    recovery_candidates = [
        row
        for row in recovery_candidates
        if (str(row["stored_cik"]), str(row["stored_accession"]), str(row["source_version_key"]))
        not in extraction_keys
    ]
    candidates = [*extraction_candidates, *recovery_candidates]
    discovery["interrupted_cleanup_candidates"] = len(recovery_candidates)
    print("identity_discovery=" + json.dumps(discovery, sort_keys=True), flush=True)
    print(
        f"repairable_identity_mismatches={len(extraction_candidates):,} "
        f"interrupted_cleanup_candidates={len(recovery_candidates):,} execute={args.execute}",
        flush=True,
    )
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
        extraction = extract_and_insert(client, args, extraction_candidates, run_id, parts_root, ingest_args)
        verification = verify_replacements(client, args.database, extraction_candidates, run_id)
        recovered_verification = verify_existing_replacements(
            client, args.database, recovery_candidates
        )
        cleanup = delete_stale_identities(client, args, candidates)
        live_manifest = reconcile_live_ingest_manifest(client, args, candidates)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = repr(exc)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        raise

    manifest.update({
        "status": "ok",
        "extraction": extraction,
        "verification": verification,
        "recovered_verification": recovered_verification,
        "cleanup": cleanup,
        "live_manifest": live_manifest,
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


def discover_mismatches(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = identity_audit.load_unconfirmed_document_identities(client, args)
    grouped = identity_audit.group_unconfirmed_identities(rows)
    candidates: list[dict[str, Any]] = []
    totals = {"archives": len(grouped), "relationships": len(rows), "matched": 0, "mismatched": 0, "missing": 0, "archive_errors": 0}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                identity_audit.audit_archive,
                archive_path,
                wanted,
                args.archive_fallback_root_win,
            ): archive_path
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


def discover_interrupted_cleanup_candidates(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    """Find verified replacements whose completed live manifest still names a stale CIK.

    This includes interrupted repair cleanup as well as older live ingests that
    committed correct products while retaining the feed/source CIK in the
    manifest. Discovery is anchored to the exact live source-version identity,
    so a newer filing revision cannot be mistaken for recoverable manifest drift.
    """
    if not identity_audit.table_exists(client, args.database, LIVE_INGEST_MANIFEST_TABLE):
        return []
    db = quote_ident(args.database)
    text = client.execute(
        f"""
WITH
recovery AS
(
    SELECT
        m.primary_cik AS stored_cik,
        m.accession_number AS accession_number,
        i.primary_cik AS primary_cik,
        i.source_archive_date AS source_archive_date,
        i.source_archive_member AS source_archive_member,
        i.source_archive_path AS source_archive_path,
        i.document_count AS document_count,
        i.source_content_sha256 AS source_content_sha256,
        i.source_version_key AS source_version_key,
        i.source_revision_at AS source_revision_at,
        i.source_revision_rank AS source_revision_rank,
        i.source_revision_kind AS source_revision_kind,
        i.pac_event_id AS pac_event_id
    FROM {db}.{quote_ident(LIVE_INGEST_MANIFEST_TABLE)} AS m FINAL
    INNER JOIN {db}.sec_filing_archive_accession_current_v3 AS i
        ON m.accession_number = i.accession_number
       AND m.source_version_key = i.source_version_key
    WHERE m.status = 'complete'
      AND m.error = ''
      AND m.primary_cik != ''
      AND m.primary_cik != i.primary_cik
      AND i.source_kind = 'live_accession_text'
      AND i.source_revision_kind = 'live_feed_occurrence'
),
filings AS
(
    SELECT f.cik, f.accession_number, count() AS row_count
    FROM {db}.sec_filing_v3 AS f FINAL
    INNER JOIN recovery AS r
        ON f.cik = r.primary_cik
       AND f.accession_number = r.accession_number
       AND f.source_content_sha256 = r.source_content_sha256
    GROUP BY f.cik, f.accession_number
),
entities AS
(
    SELECT e.primary_cik, e.accession_number, count() AS row_count
    FROM {db}.sec_filing_entity_v3 AS e FINAL
    INNER JOIN recovery AS r
        ON e.primary_cik = r.primary_cik
       AND e.accession_number = r.accession_number
    GROUP BY e.primary_cik, e.accession_number
),
documents AS
(
    SELECT d.cik, d.accession_number, d.source_version_key, count() AS row_count
    FROM {db}.sec_filing_document_v3 AS d FINAL
    INNER JOIN recovery AS r
        ON d.cik = r.primary_cik
       AND d.accession_number = r.accession_number
       AND d.source_version_key = r.source_version_key
    GROUP BY d.cik, d.accession_number, d.source_version_key
),
rendered AS
(
    SELECT t.cik, t.accession_number, t.source_version_key, count() AS row_count
    FROM {db}.sec_filing_text_rendered_v3 AS t FINAL
    INNER JOIN recovery AS r
        ON t.cik = r.primary_cik
       AND t.accession_number = r.accession_number
       AND t.source_version_key = r.source_version_key
    GROUP BY t.cik, t.accession_number, t.source_version_key
),
skips AS
(
    SELECT s.cik, s.accession_number, s.source_version_key, count() AS row_count
    FROM {db}.sec_filing_document_skip_v3 AS s FINAL
    INNER JOIN recovery AS r
        ON s.cik = r.primary_cik
       AND s.accession_number = r.accession_number
       AND s.source_version_key = r.source_version_key
    GROUP BY s.cik, s.accession_number, s.source_version_key
)
SELECT
    r.stored_cik AS stored_cik,
    r.accession_number AS stored_accession,
    r.primary_cik AS sgml_cik,
    r.accession_number AS sgml_accession,
    r.source_archive_date AS source_archive_date,
    r.source_archive_member AS member,
    assumeNotNull(r.source_archive_path) AS archive_path,
    r.document_count AS expected_document_count,
    r.source_version_key AS source_version_key,
    toString(r.source_revision_at) AS source_revision_at,
    r.source_revision_rank AS source_revision_rank,
    r.source_revision_kind AS source_revision_kind,
    assumeNotNull(r.pac_event_id) AS pac_event_id,
    'verified_manifest_identity_drift' AS recovery_kind
FROM recovery AS r
INNER JOIN filings AS f
    ON f.cik = r.primary_cik AND f.accession_number = r.accession_number
INNER JOIN entities AS e
    ON e.primary_cik = r.primary_cik AND e.accession_number = r.accession_number
INNER JOIN documents AS d
    ON d.cik = r.primary_cik
   AND d.accession_number = r.accession_number
   AND d.source_version_key = r.source_version_key
LEFT JOIN rendered AS t
    ON t.cik = r.primary_cik
   AND t.accession_number = r.accession_number
   AND t.source_version_key = r.source_version_key
LEFT JOIN skips AS s
    ON s.cik = r.primary_cik
   AND s.accession_number = r.accession_number
   AND s.source_version_key = r.source_version_key
WHERE f.row_count = 1
  AND e.row_count >= 1
  AND d.row_count = r.document_count
  AND ifNull(t.row_count, 0) + ifNull(s.row_count, 0) = r.document_count
FORMAT JSONEachRow
"""
    )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


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
    if not candidates:
        return totals
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
    prune_empty_part_directories(parts_root)
    return totals


def worker_payload(
    args: argparse.Namespace,
    run_id: str,
    parts_root: Path,
    archive_index: int,
    archive_path: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    revisions = {
        str(row["sgml_accession"]): {
            "source_version_key": str(row["source_version_key"]),
            "source_revision_at": str(row["source_revision_at"]),
            "source_revision_rank": int(row["source_revision_rank"]),
            "source_revision_kind": str(row["source_revision_kind"]),
            "pac_event_id": str(row.get("pac_event_id") or ""),
        }
        for row in rows
    }
    return {
        "archive_path": archive_path,
        "source_archive_date": str(rows[0]["source_archive_date"]),
        "source_kind": (
            "daily_archive"
            if archive_path.lower().endswith(".tar.gz")
            else "live_accession_text"
        ),
        "source_revisions": revisions,
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
        "parquet_row_group_bytes": args.parquet_row_group_mb * 1024**2,
        "parquet_file_bytes": args.parquet_file_mb * 1024**2,
        "parquet_compression_level": args.parquet_compression_level,
        "target_accessions": sorted({row["sgml_accession"] for row in rows}),
        "target_members": sorted({row["member"] for row in rows}),
        "parent_resolution_mode": "supplied_only",
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
    totals = {
        "filings": 0,
        "entities": 0,
        "archive_accessions": 0,
        "documents": 0,
        "sources": 0,
        "rendered": 0,
        "skips": 0,
    }
    for row in candidates:
        cik = sql_string(str(row["sgml_cik"]))
        accession = sql_string(str(row["sgml_accession"]))
        run = sql_string(run_id)
        result = json.loads(client.execute(
            f"""
SELECT
    count() AS documents,
    (SELECT count() FROM {db}.sec_filing_v3 WHERE cik={cik} AND accession_number={accession} AND source_run_id={run}) AS filings,
    (SELECT count() FROM {db}.sec_filing_entity_v3 WHERE primary_cik={cik} AND accession_number={accession} AND source_run_id={run}) AS entities,
    (SELECT count() FROM {db}.sec_filing_archive_accession_v3 WHERE primary_cik={cik} AND accession_number={accession} AND source_run_id={run}) AS archive_accessions,
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
        if int(result["filings"]) != 1:
            raise RuntimeError(f"replacement filing parent missing or duplicated: {row} result={result}")
        if int(result["entities"]) < 1:
            raise RuntimeError(f"replacement filing entities missing: {row} result={result}")
        if int(result["archive_accessions"]) != 1:
            raise RuntimeError(f"replacement accession inventory missing or duplicated: {row} result={result}")
        for key in totals:
            totals[key] += int(result[key])
    return totals


def verify_existing_replacements(
    client: ClickHouseHttpClient,
    database: str,
    candidates: list[dict[str, Any]],
) -> dict[str, int]:
    """Revalidate recovery-only replacements immediately before stale cleanup."""
    db = quote_ident(database)
    totals = {"filings": 0, "entities": 0, "documents": 0, "rendered": 0, "skips": 0}
    for row in candidates:
        cik = sql_string(str(row["sgml_cik"]))
        accession = sql_string(str(row["sgml_accession"]))
        version = sql_string(str(row["source_version_key"]))
        result = json.loads(
            client.execute(
                f"""
SELECT
    (SELECT count() FROM {db}.sec_filing_v3 FINAL
     WHERE cik={cik} AND accession_number={accession}) AS filings,
    (SELECT count() FROM {db}.sec_filing_entity_v3 FINAL
     WHERE primary_cik={cik} AND accession_number={accession}) AS entities,
    (SELECT count() FROM {db}.sec_filing_document_v3 FINAL
     WHERE cik={cik} AND accession_number={accession} AND source_version_key={version}) AS documents,
    (SELECT count() FROM {db}.sec_filing_text_rendered_v3 FINAL
     WHERE cik={cik} AND accession_number={accession} AND source_version_key={version}) AS rendered,
    (SELECT count() FROM {db}.sec_filing_document_skip_v3 FINAL
     WHERE cik={cik} AND accession_number={accession} AND source_version_key={version}) AS skips
FORMAT JSONEachRow
"""
            ).strip()
        )
        expected = int(row["expected_document_count"])
        if int(result["filings"]) != 1 or int(result["entities"]) < 1:
            raise RuntimeError(f"recovered replacement parent lineage incomplete: {row} result={result}")
        if int(result["documents"]) != expected:
            raise RuntimeError(f"recovered replacement document lineage incomplete: {row} result={result}")
        if int(result["rendered"]) + int(result["skips"]) != expected:
            raise RuntimeError(f"recovered replacement text/skip lineage incomplete: {row} result={result}")
        for key in totals:
            totals[key] += int(result[key])
    return totals


def delete_stale_identities(
    client: ClickHouseHttpClient, args: argparse.Namespace, candidates: list[dict[str, Any]]
) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    keys = sorted({
        (str(row["stored_cik"]), str(row["stored_accession"]))
        for row in candidates
    })
    if not keys:
        return {}
    predicate = identity_keys_predicate(keys)
    for table in MODEL_TABLES:
        if identity_audit.table_exists(client, args.model_database, table):
            totals[f"{args.model_database}.{table}"] += delete_and_verify(
                client, args.model_database, table, predicate, args.mutations_sync
            )
    for table in DOCUMENT_TABLES_CHILD_FIRST:
        totals[f"{args.database}.{table}"] += delete_and_verify(
            client, args.database, table, predicate, args.mutations_sync
        )
    for table, cik_column in CANONICAL_TABLES_CHILD_FIRST:
        canonical_predicate = identity_keys_predicate(keys, cik_column=cik_column)
        totals[f"{args.database}.{table}"] += delete_and_verify(
            client, args.database, table, canonical_predicate, args.mutations_sync
        )
    return dict(totals)


def reconcile_live_ingest_manifest(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    candidates: list[dict[str, Any]],
) -> dict[str, int]:
    if not identity_audit.table_exists(client, args.database, LIVE_INGEST_MANIFEST_TABLE):
        return {"candidates": 0, "updated_rows": 0}
    target = f"{quote_ident(args.database)}.{quote_ident(LIVE_INGEST_MANIFEST_TABLE)}"
    live_candidates = [
        row
        for row in candidates
        if str(row.get("source_revision_kind") or "") == "live_feed_occurrence"
    ]
    corrections = sorted({
        (
            str(row["sgml_accession"]),
            str(row["source_version_key"]),
            str(row["stored_cik"]),
            str(row["sgml_cik"]),
        )
        for row in live_candidates
    })
    if not corrections:
        return {"candidates": 0, "updated_rows": 0}
    values_sql = ",".join(
        "(" + ",".join(sql_string(value) for value in correction) + ")"
        for correction in corrections
    )
    values_table = (
        "values("
        "'accession_number String, source_version_key String, "
        "old_primary_cik String, new_primary_cik String',"
        f"{values_sql})"
    )
    before = int(
        client.execute(
            f"""
SELECT count()
FROM {target} AS m FINAL
INNER JOIN {values_table} AS c
    ON m.accession_number=c.accession_number
   AND m.source_version_key=c.source_version_key
   AND m.primary_cik=c.old_primary_cik
FORMAT TSV
"""
        ).strip()
        or "0"
    )
    if before:
        client.execute(
            f"""
INSERT INTO {target}
(
    accession_number, source_cik, primary_cik, source_version_key,
    source_revision_at, source_revision_rank, renderer_version,
    expected_document_rows, expected_text_source_rows,
    expected_rendered_text_rows, expected_skip_rows,
    expected_xbrl_company_fact_rows, expected_xbrl_frame_observation_rows,
    metadata_status, xbrl_status, status, error, retry_after_utc,
    source_run_id, updated_at_utc
)
SELECT
    m.accession_number, m.source_cik, c.new_primary_cik, m.source_version_key,
    m.source_revision_at, m.source_revision_rank, m.renderer_version,
    m.expected_document_rows, m.expected_text_source_rows,
    m.expected_rendered_text_rows, m.expected_skip_rows,
    m.expected_xbrl_company_fact_rows, m.expected_xbrl_frame_observation_rows,
    m.metadata_status, m.xbrl_status, m.status, m.error, m.retry_after_utc,
    m.source_run_id, now64(9, 'UTC')
FROM {target} AS m FINAL
INNER JOIN {values_table} AS c
    ON m.accession_number=c.accession_number
   AND m.source_version_key=c.source_version_key
   AND m.primary_cik=c.old_primary_cik
"""
        )
    remaining = int(
        client.execute(
            f"""
SELECT count()
FROM {target} AS m FINAL
INNER JOIN {values_table} AS c
    ON m.accession_number=c.accession_number
   AND m.source_version_key=c.source_version_key
   AND m.primary_cik=c.old_primary_cik
FORMAT TSV
"""
        ).strip()
        or "0"
    )
    corrected = int(
        client.execute(
            f"""
SELECT count()
FROM {target} AS m FINAL
INNER JOIN {values_table} AS c
    ON m.accession_number=c.accession_number
   AND m.source_version_key=c.source_version_key
   AND m.primary_cik=c.new_primary_cik
FORMAT TSV
"""
        ).strip()
        or "0"
    )
    if remaining or (before and corrected != len(corrections)):
        raise RuntimeError(
            "live ingest manifest identity reconciliation failed "
            f"remaining={remaining} corrected={corrected} expected={len(corrections)}"
        )
    return {"candidates": len(corrections), "updated_rows": before}


def identity_predicate(cik: str, accession_number: str, *, cik_column: str = "cik") -> str:
    if cik_column not in {"cik", "primary_cik"}:
        raise ValueError(f"unsupported CIK column: {cik_column}")
    return f"{cik_column}={sql_string(cik)} AND accession_number={sql_string(accession_number)}"


def identity_keys_predicate(
    keys: list[tuple[str, str]],
    *,
    cik_column: str = "cik",
) -> str:
    if cik_column not in {"cik", "primary_cik"}:
        raise ValueError(f"unsupported CIK column: {cik_column}")
    if not keys:
        raise ValueError("identity keys cannot be empty")
    values = ",".join(
        f"({sql_string(cik)},{sql_string(accession)})"
        for cik, accession in keys
    )
    return f"({cik_column},accession_number) IN ({values})"


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

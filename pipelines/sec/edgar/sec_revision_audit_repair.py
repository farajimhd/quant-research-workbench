from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_filing_text_extract_parts import (  # noqa: E402
    NORMALIZER_VERSION,
    detect_content_format,
    file_extension,
    mime_type_for_format,
    parse_filing,
    sha256_text,
    text_kind_for_role,
)
from pipelines.sec.edgar.sec_pipeline.revision import PacDocumentChange, PacEvent, parse_pac_event, source_revision  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/sec_revision_audit_repair")
DEFAULT_ARCHIVE_ROOT = Path("D:/market-data/sec_core/daily_archives")
DOCUMENT_FAMILIES = (
    "sec_filing_document_v3",
    "sec_filing_text_v3",
    "sec_filing_text_rendered_v3",
    "sec_filing_document_skip_v3",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair SEC v3 raw-source lineage and select post-acceptance revisions by SEC archive occurrence, "
            "independent of worker completion or database insertion order. Dry-run is the default."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_sec_clickhouse_url())
    parser.add_argument("--user", default=default_sec_clickhouse_user())
    parser.add_argument("--password", default=default_sec_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", "q_live"))
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_ARCHIVE_ROOT_WIN", str(DEFAULT_ARCHIVE_ROOT)))
    parser.add_argument("--archive-path-from", default=r"D:\market-data")
    parser.add_argument("--archive-path-to", default="")
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_REVISION_AUDIT_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT)))
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--scan-pac", action="store_true", help="Scan .pc archive members and apply explicit SEC correction/deletion records.")
    parser.add_argument("--apply-stored-pac", action="store_true", help="Apply PAC rows already extracted by the archive rebuild.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mutations-sync", type=int, choices=(0, 1, 2), default=2)
    return parser.parse_args()


def main() -> int:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier(args.database, "--database")
    run_id = "sec_revision_audit_repair_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    started = time.perf_counter()

    ensure_schema(client, args.database) if args.execute else None
    before = load_inventory(client, args.database)
    missing = load_rendered_without_raw(client, args.database, args.limit)
    mismatches = load_revision_mismatches(client, args.database, args.limit)
    write_jsonl(run_root / "missing_raw_sources.jsonl", missing)
    write_jsonl(run_root / "revision_mismatches.jsonl", mismatches)
    print(
        f"SEC revision repair | mode={'EXECUTE' if args.execute else 'DRY-RUN'} "
        f"missing_raw={len(missing):,} revision_mismatches={len(mismatches):,}",
        flush=True,
    )

    repair = {"raw_sources_recovered": 0, "revision_documents_repaired": 0, "stale_skips_deleted": 0}
    pac = {"archives_scanned": 0, "events": 0, "rows": 0, "filing_deletions": 0, "document_deletions": 0}
    if args.execute:
        repair["raw_sources_recovered"] = recover_missing_raw_sources(client, args, missing, run_id)
        # Raw recovery creates authoritative source-version keys. Reload the
        # comparison so document and rendered metadata are reconciled to those exact revisions.
        mismatches = load_revision_mismatches(client, args.database, args.limit)
        revision_result = repair_revision_mismatches(client, args, mismatches, run_id)
        repair.update(revision_result)
        if args.scan_pac:
            pac = scan_and_apply_pac(client, args, run_id)
        elif args.apply_stored_pac:
            pac = apply_stored_pac(client, args)

    after = load_inventory(client, args.database) if args.execute else before
    summary = {
        "run_id": run_id,
        "mode": "execute" if args.execute else "dry_run",
        "inventory_before": before,
        "inventory_after": after,
        "repair": repair,
        "pac": pac,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(["SEC_CLICKHOUSE_URL", "SEC_CLICKHOUSE_USER", "SEC_CLICKHOUSE_PASSWORD"]),
    }
    (run_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True), flush=True)
    print(f"report={run_root / 'summary.json'}", flush=True)
    return 0 if not args.execute or all(value == 0 for value in after.values()) else 2


def load_inventory(client: ClickHouseHttpClient, database: str) -> dict[str, int]:
    db = quote_ident(database)
    return {
        "rendered_without_raw": scalar_int(
            client,
            f"""
            WITH r AS (SELECT cik, accession_number, document_id FROM {db}.sec_filing_text_rendered_v3 FINAL),
                 s AS (SELECT cik, accession_number, document_id FROM {db}.sec_filing_text_v3 GROUP BY cik, accession_number, document_id)
            SELECT count() FROM r LEFT ANTI JOIN s USING (cik, accession_number, document_id)
            """,
        ),
        "document_raw_mismatches": scalar_int(
            client,
            f"""
            WITH d AS (SELECT cik, accession_number, document_id, content_sha256 FROM {db}.sec_filing_document_v3 FINAL),
                 s AS (
                    SELECT cik, accession_number, document_id,
                           argMax(content_sha256, tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS content_sha256
                    FROM {db}.sec_filing_text_v3 GROUP BY cik, accession_number, document_id
                 )
            SELECT count() FROM d INNER JOIN s USING (cik, accession_number, document_id) WHERE d.content_sha256 != s.content_sha256
            """,
        ),
        "rendered_source_revision_mismatches": scalar_int(
            client,
            f"""
            WITH r AS (SELECT cik, accession_number, document_id, source_version_key FROM {db}.sec_filing_text_rendered_v3 FINAL),
                 s AS (
                    SELECT cik, accession_number, document_id,
                           argMax(source_version_key, tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS source_version_key
                    FROM {db}.sec_filing_text_v3 GROUP BY cik, accession_number, document_id
                 )
            SELECT count() FROM r INNER JOIN s USING (cik, accession_number, document_id)
            WHERE notEmpty(s.source_version_key) AND r.source_version_key != s.source_version_key
            """,
        ),
    }


def load_rendered_without_raw(client: ClickHouseHttpClient, database: str, limit: int) -> list[dict[str, Any]]:
    limit_sql = f"LIMIT {int(limit)}" if limit > 0 else ""
    db = quote_ident(database)
    return json_lines(
        client.execute(
            f"""
            WITH r AS (SELECT * FROM {db}.sec_filing_text_rendered_v3 FINAL),
                 s AS (SELECT cik, accession_number, document_id FROM {db}.sec_filing_text_v3 GROUP BY cik, accession_number, document_id),
                 d AS (SELECT * FROM {db}.sec_filing_document_v3 FINAL)
            SELECT r.cik AS cik, r.accession_number AS accession_number, r.document_id AS document_id,
                   r.filing_id AS filing_id, r.accession_number_compact AS accession_number_compact,
                   d.sequence_number AS sequence_number, d.document_name AS document_name,
                   d.document_type AS document_type, d.document_role AS document_role,
                   d.description AS description, d.document_url AS document_url,
                   r.text_kind AS text_kind, r.source_archive_date AS source_archive_date,
                   r.source_archive_member AS source_archive_member, ifNull(d.source_archive_path, '') AS source_archive_path,
                   d.file_extension AS file_extension, d.content_format AS content_format, d.mime_type AS mime_type,
                   d.content_sha256 AS content_sha256, r.source_revision_rank AS source_revision_rank,
                   r.source_version_key AS source_version_key
            FROM r LEFT ANTI JOIN s USING (cik, accession_number, document_id)
            INNER JOIN d USING (cik, accession_number, document_id)
            ORDER BY source_archive_date, source_archive_member, sequence_number
            {limit_sql}
            FORMAT JSONEachRow
            """
        )
    )


def load_revision_mismatches(client: ClickHouseHttpClient, database: str, limit: int) -> list[dict[str, Any]]:
    limit_sql = f"LIMIT {int(limit)}" if limit > 0 else ""
    db = quote_ident(database)
    return json_lines(
        client.execute(
            f"""
            WITH d AS (SELECT * FROM {db}.sec_filing_document_v3 FINAL),
                 s AS (
                    SELECT cik, accession_number, document_id,
                           argMax(content_sha256, tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS source_sha256,
                           argMax(source_version_key, tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS winner_source_version_key,
                           argMax(source_revision_rank, tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS winner_revision_rank
                    FROM {db}.sec_filing_text_v3 GROUP BY cik, accession_number, document_id
                 )
            SELECT d.cik AS cik, d.accession_number AS accession_number, d.document_id AS document_id,
                   d.content_sha256 AS document_sha256, s.source_sha256 AS source_sha256,
                   d.source_version_key AS document_version_key, s.winner_source_version_key AS source_version_key
            FROM d INNER JOIN s USING (cik, accession_number, document_id)
            WHERE d.content_sha256 != s.source_sha256
               OR (notEmpty(s.winner_source_version_key) AND d.source_version_key != s.winner_source_version_key)
               OR d.source_revision_rank != s.winner_revision_rank
            ORDER BY d.accession_number, d.sequence_number
            {limit_sql}
            FORMAT JSONEachRow
            """
        )
    )


def recover_missing_raw_sources(
    client: ClickHouseHttpClient, args: argparse.Namespace, rows: list[dict[str, Any]], run_id: str
) -> int:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        path = remap_archive_path(str(row["source_archive_path"]), args.archive_path_from, args.archive_path_to)
        grouped[(path, str(row["source_archive_member"]).lstrip("./"))].append(row)
    recovered = 0
    for index, ((archive_path, member_name), targets) in enumerate(grouped.items(), start=1):
        print(f"raw_recovery={index}/{len(grouped)} archive={Path(archive_path).name} documents={len(targets)}", flush=True)
        raw, member_sequence = read_archive_member(Path(archive_path), member_name)
        filing = parse_filing(raw, member_name)
        documents = {(int(doc["sequence_number"]), doc["document_name"], doc["document_type"]): doc for doc in filing["documents"]}
        source_rows: list[dict[str, Any]] = []
        for target in targets:
            document = documents.get((int(target["sequence_number"]), str(target["document_name"]), str(target["document_type"])))
            if document is None:
                raise RuntimeError(
                    f"archive document not found accession={target['accession_number']} sequence={target['sequence_number']} name={target['document_name']}"
                )
            payload = str(document["payload"])
            content_sha = sha256_text(payload)
            if content_sha != str(target["content_sha256"]):
                raise RuntimeError(
                    f"archive source hash mismatch accession={target['accession_number']} document={target['document_name']} "
                    f"expected={target['content_sha256']} actual={content_sha}"
                )
            revision = source_revision(
                archive_date=str(target["source_archive_date"]),
                archive_member=str(target["source_archive_member"]),
                archive_path=str(target["source_archive_path"]),
                source_content_sha256=hashlib.sha256(raw).hexdigest(),
                occurrence_sequence=member_sequence,
            )
            now = utc_now()
            source_row = {
                **{key: target.get(key) for key in (
                    "document_id", "filing_id", "accession_number", "accession_number_compact", "cik",
                    "sequence_number", "document_name", "document_type", "document_role", "description",
                    "document_url", "text_kind", "source_archive_date", "source_archive_member",
                    "source_archive_path", "file_extension", "content_format", "mime_type",
                )},
                "source_text": payload,
                "source_text_char_count": len(payload),
                "source_text_byte_count": len(payload.encode("utf-8", errors="replace")),
                "content_sha256": content_sha,
                "normalizer_version": NORMALIZER_VERSION,
                "source_version_key": revision.source_version_key,
                "source_revision_at": revision.source_revision_at,
                "source_revision_rank": revision.source_revision_rank,
                "source_revision_kind": "archive_raw_lineage_recovery",
                "pac_event_id": None,
                "source_run_id": run_id,
                "inserted_at": now,
            }
            source_rows.append(source_row)
            recovered += 1
        insert_json_rows(client, args.database, "sec_filing_text_v3", source_rows)
    return recovered


def repair_revision_mismatches(
    client: ClickHouseHttpClient, args: argparse.Namespace, rows: list[dict[str, Any]], run_id: str
) -> dict[str, int]:
    if not rows:
        return {"revision_documents_repaired": 0, "stale_skips_deleted": 0}
    key_table = "sec_revision_repair_keys_" + re.sub(r"[^a-zA-Z0-9_]", "_", run_id)
    target = f"{quote_ident(args.database)}.{quote_ident(key_table)}"
    client.execute(f"DROP TABLE IF EXISTS {target}")
    client.execute(f"CREATE TABLE {target} (document_id String, content_changed UInt8) ENGINE=Memory")
    try:
        for offset in range(0, len(rows), 5_000):
            key_rows = [
                {
                    "document_id": str(row["document_id"]),
                    "content_changed": int(row.get("document_sha256") != row.get("source_sha256")),
                }
                for row in rows[offset : offset + 5_000]
            ]
            insert_json_rows(client, args.database, key_table, key_rows)
        print(f"revision_reconcile_keys={len(rows):,}", flush=True)
        totals = repair_revision_key_table(client, args, key_table, len(rows), run_id)
    finally:
        client.execute(f"DROP TABLE IF EXISTS {target}")
    content_changed_ids = [str(row["document_id"]) for row in rows if row.get("document_sha256") != row.get("source_sha256")]
    if content_changed_ids:
        ids = ",".join(sql_string(value) for value in content_changed_ids)
        totals["stale_skips_deleted"] = delete_where(
            client, args.database, "sec_filing_document_skip_v3", f"document_id IN ({ids})", args.mutations_sync
        )
    return totals


def repair_revision_key_table(
    client: ClickHouseHttpClient, args: argparse.Namespace, key_table: str, row_count: int, run_id: str
) -> dict[str, int]:
    db = quote_ident(args.database)
    key_source = f"SELECT document_id FROM {db}.{quote_ident(key_table)}"
    columns = (
        "document_id, filing_id, accession_number, accession_number_compact, cik, sequence_number, document_name, "
        "document_type, document_role, description, document_url, source_archive_date, source_archive_member, "
        "source_archive_path, file_extension, content_format, mime_type, byte_size, payload_char_count, content_sha256, "
        "text_sha256, has_normalized_text, extraction_status, extraction_error, normalizer_version, source_version_key, "
        "source_revision_at, source_revision_kind, pac_event_id, source_revision_rank, source_run_id, inserted_at"
    )
    client.execute(
        f"""
        INSERT INTO {db}.sec_filing_document_v3 ({columns})
        WITH
        s AS
        (
            SELECT * EXCEPT authority_row
            FROM
            (
                SELECT *, row_number() OVER
                (
                    PARTITION BY cik, accession_number, document_id
                    ORDER BY source_revision_rank DESC, source_text_byte_count DESC, source_version_key DESC
                ) AS authority_row
                FROM {db}.sec_filing_text_v3
                WHERE document_id IN ({key_source})
            )
            WHERE authority_row = 1
        ),
        r AS
        (
            SELECT cik, accession_number, document_id, argMax(text_sha256, inserted_at) AS text_sha256
            FROM {db}.sec_filing_text_rendered_v3
            WHERE document_id IN ({key_source})
            GROUP BY cik, accession_number, document_id
        )
        SELECT
            s.document_id, s.filing_id, s.accession_number, s.accession_number_compact, s.cik, s.sequence_number,
            s.document_name, s.document_type, s.document_role, s.description, s.document_url, s.source_archive_date,
            s.source_archive_member, s.source_archive_path, s.file_extension, s.content_format, s.mime_type,
            s.source_text_byte_count, s.source_text_char_count, s.content_sha256, nullIf(r.text_sha256, ''),
            toUInt8(notEmpty(r.text_sha256)),
            if(notEmpty(r.text_sha256), 'text_extracted_revision_reconciled', 'source_recovered_revision_reconciled'),
            NULL, s.normalizer_version, s.source_version_key, s.source_revision_at, s.source_revision_kind,
            s.pac_event_id, s.source_revision_rank, {sql_string(run_id)}, now64(3)
        FROM s LEFT JOIN r USING (cik, accession_number, document_id)
        SETTINGS max_threads=32
        """
    )
    rendered_columns = (
        "document_id, filing_id, accession_number, accession_number_compact, cik, text_kind, text, text_char_count, "
        "text_byte_count, text_sha256, extraction_method, normalizer_version, quality_flags, source_archive_date, "
        "source_archive_member, source_version_key, source_revision_at, source_revision_kind, pac_event_id, "
        "source_revision_rank, extracted_at_utc, source_run_id, inserted_at"
    )
    client.execute(
        f"""
        INSERT INTO {db}.sec_filing_text_rendered_v3 ({rendered_columns})
        WITH
        s AS
        (
            SELECT * EXCEPT authority_row
            FROM
            (
                SELECT *, row_number() OVER
                (
                    PARTITION BY cik, accession_number, document_id
                    ORDER BY source_revision_rank DESC, source_text_byte_count DESC, source_version_key DESC
                ) AS authority_row
                FROM {db}.sec_filing_text_v3
                WHERE document_id IN ({key_source})
            )
            WHERE authority_row = 1
        ),
        r AS (SELECT * FROM {db}.sec_filing_text_rendered_v3 FINAL WHERE document_id IN ({key_source}))
        SELECT
            r.document_id, r.filing_id, r.accession_number, r.accession_number_compact, r.cik, r.text_kind,
            r.text, r.text_char_count, r.text_byte_count, r.text_sha256, r.extraction_method, r.normalizer_version,
            r.quality_flags, s.source_archive_date, s.source_archive_member, s.source_version_key,
            s.source_revision_at, s.source_revision_kind, s.pac_event_id, s.source_revision_rank,
            r.extracted_at_utc, {sql_string(run_id)}, now64(3)
        FROM r INNER JOIN s USING (cik, accession_number, document_id)
        SETTINGS max_threads=32
        """
    )
    return {"revision_documents_repaired": row_count, "stale_skips_deleted": 0}


def scan_and_apply_pac(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> dict[str, int]:
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    archives = sorted(Path(args.archive_root_win).rglob("*.tar.gz"))
    result = {"archives_scanned": 0, "events": 0, "rows": 0, "filing_deletions": 0, "document_deletions": 0}
    for archive_index, archive_path in enumerate(archives, start=1):
        archive_date = archive_date_from_path(archive_path)
        if archive_date is None or not start <= archive_date <= end:
            continue
        result["archives_scanned"] += 1
        print(f"pac_scan={archive_index}/{len(archives)} archive={archive_path.name}", flush=True)
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                if not member.isfile() or not member.name.lower().endswith(".pc"):
                    continue
                handle = archive.extractfile(member)
                raw = handle.read() if handle else b""
                event = parse_pac_event(
                    raw.decode("latin-1", errors="replace"),
                    archive_date=archive_date,
                    archive_member=member.name,
                    archive_path=archive_path,
                    source_content_sha256=hashlib.sha256(raw).hexdigest(),
                )
                if event is None:
                    continue
                pac_rows = event.rows(source_run_id=run_id, inserted_at=utc_now())
                insert_json_rows(client, args.database, "sec_filing_pac_event_v3", pac_rows)
                apply_pac_event(client, args, event)
                result["events"] += 1
                result["rows"] += len(pac_rows)
                result["filing_deletions"] += int(event.filing_deleted)
                result["document_deletions"] += sum(int(change.deleted) for change in event.document_changes)
    return result


def apply_stored_pac(client: ClickHouseHttpClient, args: argparse.Namespace) -> dict[str, int]:
    rows = select_json(
        client,
        f"SELECT * FROM {quote_ident(args.database)}.sec_filing_pac_event_v3 FINAL "
        f"WHERE source_archive_date >= toDate({sql_string(args.start_date)}) "
        f"AND source_archive_date <= toDate({sql_string(args.end_date)}) "
        "ORDER BY source_archive_date, correction_order_key, source_archive_member, sequence_number",
    )
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(
            str(row["accession_number"]),
            str(row.get("correction_timestamp_raw") or ""),
            str(row["source_archive_date"]),
            str(row["source_archive_member"]),
        )].append(row)
    result = {"archives_scanned": 0, "events": 0, "rows": len(rows), "filing_deletions": 0, "document_deletions": 0}
    for event_rows in grouped.values():
        first = event_rows[0]
        changes = tuple(
            PacDocumentChange(
                sequence_number=int(row.get("sequence_number") or 0),
                document_name=str(row.get("document_name") or ""),
                document_type=str(row.get("document_type") or ""),
                deleted=bool(row.get("document_deleted")),
            )
            for row in event_rows
            if int(row.get("sequence_number") or 0) or row.get("document_name")
        )
        event = PacEvent(
            accession_number=str(first["accession_number"]),
            cik=str(first.get("cik") or ""),
            correction_timestamp_raw=str(first.get("correction_timestamp_raw") or ""),
            correction_order_key=int(first.get("correction_order_key") or 0),
            filing_date=str(first.get("filing_date") or ""),
            date_as_of_change=str(first.get("date_as_of_change") or ""),
            form_type=str(first.get("form_type") or ""),
            filing_deleted=bool(first.get("filing_deleted")),
            document_changes=changes,
            source_archive_date=str(first["source_archive_date"]),
            source_archive_member=str(first["source_archive_member"]),
            source_archive_path=str(first.get("source_archive_path") or ""),
            source_content_sha256=str(first.get("source_content_sha256") or ""),
        )
        apply_pac_event(client, args, event)
        result["events"] += 1
        result["filing_deletions"] += int(event.filing_deleted)
        result["document_deletions"] += sum(int(change.deleted) for change in changes)
    return result


def apply_pac_event(client: ClickHouseHttpClient, args: argparse.Namespace, event: PacEvent) -> None:
    accession_filter = f"accession_number={sql_string(event.accession_number)}"
    if event.filing_deleted:
        for table in ("sec_filing_v3", *DOCUMENT_FAMILIES):
            delete_where(client, args.database, table, accession_filter, args.mutations_sync)
        return
    if event.filing_date or event.date_as_of_change or event.form_type:
        rows = select_json(client, f"SELECT * FROM {quote_ident(args.database)}.sec_filing_v3 FINAL WHERE {accession_filter}")
        if rows:
            filing = rows[0]
            filing["filing_date"] = event.date_as_of_change or event.filing_date or filing.get("filing_date")
            filing["form_type"] = event.form_type or filing.get("form_type")
            filing["source_run_id"] = "pac:" + event.event_id
            filing["inserted_at"] = utc_now()
            insert_json_rows(client, args.database, "sec_filing_v3", [filing])
    for change in event.document_changes:
        predicate = accession_filter + f" AND sequence_number={int(change.sequence_number)}"
        if change.document_name:
            predicate += f" AND document_name={sql_string(change.document_name)}"
        document_ids = [row["document_id"] for row in select_json(
            client,
            f"SELECT document_id FROM {quote_ident(args.database)}.sec_filing_document_v3 FINAL WHERE {predicate}",
        )]
        if change.deleted:
            delete_where(client, args.database, "sec_filing_document_v3", predicate, args.mutations_sync)
            if document_ids:
                ids = ",".join(sql_string(str(value)) for value in document_ids)
                for table in DOCUMENT_FAMILIES[1:]:
                    delete_where(client, args.database, table, f"document_id IN ({ids})", args.mutations_sync)
        elif change.document_type and document_ids:
            ids = ",".join(sql_string(str(value)) for value in document_ids)
            for table in ("sec_filing_document_v3", "sec_filing_text_v3"):
                current = select_json(client, f"SELECT * FROM {quote_ident(args.database)}.{quote_ident(table)} FINAL WHERE document_id IN ({ids})")
                for row in current:
                    row["document_type"] = change.document_type
                    row["source_run_id"] = "pac:" + event.event_id
                    row["inserted_at"] = utc_now()
                insert_json_rows(client, args.database, table, current)


def load_authoritative_source(client: ClickHouseHttpClient, database: str, key: dict[str, Any]) -> dict[str, Any]:
    rows = select_json(
        client,
        f"SELECT * FROM {quote_ident(database)}.sec_filing_text_v3 "
        f"WHERE cik={sql_string(str(key['cik']))} AND accession_number={sql_string(str(key['accession_number']))} "
        f"AND document_id={sql_string(str(key['document_id']))} "
        "ORDER BY source_revision_rank DESC, source_text_byte_count DESC, source_version_key DESC LIMIT 1",
    )
    if not rows:
        raise RuntimeError(f"missing source candidate for document_id={key['document_id']}")
    return rows[0]


def load_current_row(client: ClickHouseHttpClient, database: str, table: str, key: dict[str, Any]) -> dict[str, Any]:
    rows = select_json(
        client,
        f"SELECT * FROM {quote_ident(database)}.{quote_ident(table)} FINAL "
        f"WHERE cik={sql_string(str(key['cik']))} AND accession_number={sql_string(str(key['accession_number']))} "
        f"AND document_id={sql_string(str(key['document_id']))} LIMIT 1",
    )
    if not rows:
        raise RuntimeError(f"missing {table} row for document_id={key['document_id']}")
    return rows[0]


def load_optional_current_row(client: ClickHouseHttpClient, database: str, table: str, key: dict[str, Any]) -> dict[str, Any] | None:
    rows = select_json(
        client,
        f"SELECT * FROM {quote_ident(database)}.{quote_ident(table)} FINAL "
        f"WHERE cik={sql_string(str(key['cik']))} AND accession_number={sql_string(str(key['accession_number']))} "
        f"AND document_id={sql_string(str(key['document_id']))} LIMIT 1",
    )
    return rows[0] if rows else None


def read_archive_member(path: Path, wanted_member: str) -> tuple[bytes, int]:
    if not path.exists():
        raise FileNotFoundError(path)
    wanted = wanted_member.lstrip("./")
    with tarfile.open(path, "r:gz") as archive:
        for sequence, member in enumerate(archive, start=1):
            if member.name.lstrip("./") != wanted or not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                break
            return handle.read(), sequence
    raise RuntimeError(f"archive member not found: {path}::{wanted_member}")


def ensure_schema(client: ClickHouseHttpClient, database: str) -> None:
    db = quote_ident(database)
    client.execute(f"ALTER TABLE {db}.sec_filing_pac_event_v3 ADD COLUMN IF NOT EXISTS correction_timestamp_raw String DEFAULT '' AFTER cik")
    client.execute(f"ALTER TABLE {db}.sec_filing_pac_event_v3 ADD COLUMN IF NOT EXISTS correction_order_key UInt64 DEFAULT 0 AFTER correction_timestamp_raw")
    old_timestamp_exists = scalar_int(
        client,
        "SELECT count() FROM system.columns "
        f"WHERE database={sql_string(database)} AND table='sec_filing_pac_event_v3' AND name='correction_timestamp_utc'",
    )
    if old_timestamp_exists:
        client.execute(
            f"ALTER TABLE {db}.sec_filing_pac_event_v3 MODIFY COLUMN correction_timestamp_utc "
            "Nullable(DateTime64(3, 'UTC')) DEFAULT NULL"
        )


def insert_json_rows(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(
        f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} SETTINGS date_time_input_format='best_effort', input_format_skip_unknown_fields=1 FORMAT JSONEachRow\n{body}"
    )


def delete_where(client: ClickHouseHttpClient, database: str, table: str, predicate: str, mutations_sync: int) -> int:
    target = f"{quote_ident(database)}.{quote_ident(table)}"
    count = scalar_int(client, f"SELECT count() FROM {target} WHERE {predicate}")
    if count:
        client.execute(f"ALTER TABLE {target} DELETE WHERE {predicate} SETTINGS mutations_sync={int(mutations_sync)}")
    return count


def select_json(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    return json_lines(client.execute(sql + " FORMAT JSONEachRow"))


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    return int(client.execute(sql + " FORMAT TSV").strip() or "0")


def json_lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows), encoding="utf-8")


def remap_archive_path(path: str, source: str, target: str) -> str:
    if not target:
        return path
    normalized = path.replace("/", "\\")
    source_prefix = source.rstrip("\\/")
    if normalized.lower().startswith(source_prefix.lower()):
        return target.rstrip("\\/") + normalized[len(source_prefix) :]
    return path


def archive_date_from_path(path: Path) -> date | None:
    match = re.match(r"(\d{8})", path.name)
    return datetime.strptime(match.group(1), "%Y%m%d").date() if match else None


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def default_sec_clickhouse_url() -> str:
    return os.environ.get("SEC_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_sec_clickhouse_user() -> str:
    return os.environ.get("SEC_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_sec_clickhouse_password() -> str:
    return os.environ.get("SEC_CLICKHOUSE_PASSWORD") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD") or default_clickhouse_password()


if __name__ == "__main__":
    raise SystemExit(main())

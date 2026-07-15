from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_filing_archive_rebuild import archive_identity  # noqa: E402
from pipelines.sec.edgar.sec_filing_text_extract_parts import (  # noqa: E402
    archive_date_from_name,
    decode_sec_bytes,
    discover_archives,
    header_value,
    normalize_accession,
    tag_value,
)
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import (  # noqa: E402
    create_entity_current_view,
    create_entity_table_schema,
)
from pipelines.sec.edgar.sec_pipeline.entities import (  # noqa: E402
    build_entity_rows,
    parse_filing_entities,
    primary_filing_entity,
)
from pipelines.sec.edgar.sec_pipeline.revision import source_revision  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


DEFAULT_MANIFEST_TABLE = "sec_filing_entity_archive_manifest_v3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Populate SEC filing/entity relationships from SGML headers only.")
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_DAILY_ARCHIVE_ROOT_WIN", "D:/market-data/sec_core/daily_archives"))
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_WRITE_DATABASE", "q_live"))
    parser.add_argument("--manifest-table", default=os.environ.get("SEC_ENTITY_ARCHIVE_MANIFEST_TABLE", DEFAULT_MANIFEST_TABLE))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SEC_ENTITY_BACKFILL_WORKERS", "32")))
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    archives = discover_archives(Path(args.archive_root_win), args.start_date, args.end_date)
    if not args.execute:
        print(
            f"SEC entity backfill archives={len(archives):,} workers={max(1, args.workers)} execute=False",
            flush=True,
        )
        return
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    create_entity_table_schema(client, target_database=args.database, reference_database=args.database)
    create_entity_current_view(client, target_database=args.database)
    create_manifest_table(client, args)
    completed = completed_archive_keys(client, args)
    pending = [path for path in archives if archive_identity(path)["archive_key"] not in completed]
    print(
        f"SEC entity backfill archives={len(archives):,} completed={len(archives) - len(pending):,} "
        f"pending={len(pending):,} workers={max(1, args.workers)} execute={args.execute}",
        flush=True,
    )
    run_id = "sec_entity_backfill_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    payloads = [worker_payload(args, path, run_id) for path in pending]
    done = rows = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(process_archive, payload): payload["archive_path"] for payload in payloads}
        try:
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                done += 1
                rows += int(result["entity_rows"])
                print(
                    f"entity archives={done:,}/{len(pending):,} date={result['archive_date']} "
                    f"filings={result['filings']:,} rows={result['entity_rows']:,}",
                    flush=True,
                )
        except Exception:
            for future in futures:
                future.cancel()
            raise
    print(f"SEC entity backfill completed archives={done:,} entity_rows={rows:,}", flush=True)


def worker_payload(args: argparse.Namespace, archive: Path, run_id: str) -> dict[str, Any]:
    return {
        "archive_path": str(archive), "database": args.database, "manifest_table": args.manifest_table,
        "clickhouse_url": args.clickhouse_url, "user": args.user, "password": args.password, "run_id": run_id,
    }


def process_archive(payload: dict[str, Any]) -> dict[str, Any]:
    archive = Path(payload["archive_path"])
    identity = archive_identity(archive)
    archive_date = archive_date_from_name(archive.name)
    inserted_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []
    filings = 0
    with tarfile.open(archive, "r:gz") as tar:
        for occurrence_sequence, member in enumerate(tar, start=1):
            if not member.isfile() or not member.name.lower().endswith(".nc"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            header_bytes, source_sha = read_header_and_hash(handle)
            header_text = decode_sec_bytes(header_bytes)
            entities = parse_filing_entities(header_text)
            primary = primary_filing_entity(entities)
            if primary is None:
                continue
            accession = normalize_accession(
                header_value(header_text, "ACCESSION NUMBER")
                or tag_value(header_text, "ACCESSION-NUMBER")
                or Path(member.name).stem
            )
            if not accession:
                continue
            revision = source_revision(
                archive_date=archive_date,
                archive_member=member.name,
                archive_path=archive,
                source_content_sha256=source_sha,
                occurrence_sequence=occurrence_sequence,
            )
            rows.extend(
                build_entity_rows(
                    entities=entities,
                    filing_id=None,
                    accession_number=accession,
                    primary_cik=primary.cik,
                    source_archive_date=archive_date.isoformat(),
                    source_archive_member=member.name,
                    source_archive_path=str(archive),
                    source_header_sha256=hashlib.sha256(header_text.encode("utf-8", errors="replace")).hexdigest(),
                    revision=revision,
                    source_run_id=str(payload["run_id"]),
                    inserted_at=inserted_at,
                )
            )
            filings += 1
    client = ClickHouseHttpClient(payload["clickhouse_url"], payload["user"], payload["password"])
    insert_rows(client, payload["database"], "sec_filing_entity_v3", rows)
    insert_manifest(client, payload, identity, filings, len(rows), inserted_at)
    return {"archive_date": archive_date.isoformat(), "filings": filings, "entity_rows": len(rows)}


def read_header_and_hash(handle: Any) -> tuple[bytes, str]:
    digest = hashlib.sha256()
    header = bytearray()
    found_document = False
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        if not found_document:
            header.extend(chunk)
            marker = bytes(header).upper().find(b"<DOCUMENT>")
            if marker >= 0:
                del header[marker:]
                found_document = True
    return bytes(header), digest.hexdigest()


def insert_rows(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), 10_000):
        body = "\n".join(json.dumps(row, separators=(",", ":"), default=str) for row in rows[offset : offset + 10_000])
        client.execute(
            f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} SETTINGS date_time_input_format='best_effort' FORMAT JSONEachRow\n{body}"
        )


def create_manifest_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(
        f"""CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
        (archive_key String, archive_date Date, archive_path String, archive_size UInt64,
         archive_mtime_ns UInt64, filings UInt64, entity_rows UInt64, status LowCardinality(String),
         run_id String, error String, updated_at DateTime64(3, 'UTC'))
        ENGINE=ReplacingMergeTree(updated_at) PARTITION BY toYYYYMM(archive_date)
        ORDER BY archive_key"""
    )


def completed_archive_keys(client: ClickHouseHttpClient, args: argparse.Namespace) -> set[str]:
    out = client.execute(
        f"SELECT archive_key FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL "
        "WHERE status='ok' FORMAT TSV"
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def insert_manifest(
    client: ClickHouseHttpClient, payload: dict[str, Any], identity: dict[str, Any], filings: int, rows: int, updated_at: str
) -> None:
    row = {
        **identity, "filings": filings, "entity_rows": rows, "status": "ok",
        "run_id": payload["run_id"], "error": "", "updated_at": updated_at,
    }
    insert_rows(client, payload["database"], payload["manifest_table"], [row])


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipelines.sec.edgar.sec_text_layout import (
    TEXT_SOURCE_PARTITION_KEY,
    TEXT_SOURCE_SORTING_KEY,
    text_source_layout_matches,
)
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string


SOURCE_AUTHORITY_VERSION = 2
SOURCE_REVISION_ENGINE = "ReplacingMergeTree(source_revision_rank)"


def ensure_source_revision_engine(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table_name: str,
    report_path: Path,
    run_id: str,
) -> set[int]:
    """Migrate a stale insertion-ranked source table using partition hard links."""
    layout = load_source_layout(client, database, table_name)
    report = load_report(report_path)
    migration_table = f"{table_name}_revision_engine_migration"
    backup_table = f"{table_name}_inserted_at_engine_backup"

    if revision_engine_matches(layout):
        if table_exists(client, database, migration_table) and not report:
            raise RuntimeError(
                f"untracked source migration table exists beside a canonical source table: "
                f"{database}.{migration_table}"
            )
        finalize_interrupted_cutover(client, database, table_name, migration_table, backup_table)
        if report and report.get("migration_status") != "completed":
            report["migration_status"] = "completed"
            write_report(report_path, report)
        if report and not report.get("renderer_reset_completed", False):
            return {int(value) for value in report.get("affected_partitions", [])}
        return set()

    if normalize_engine(layout.get("engine_full")) != normalize_engine("ReplacingMergeTree(inserted_at)"):
        raise RuntimeError(
            f"unsupported source replacement engine {layout.get('engine_full')!r}; "
            f"expected stale ReplacingMergeTree(inserted_at) or {SOURCE_REVISION_ENGINE}"
        )
    if not text_source_layout_matches(str(layout.get("partition_key") or ""), str(layout.get("sorting_key") or "")):
        raise RuntimeError(
            "source table cannot be migrated because its partition or sorting key is noncanonical "
            f"partition={layout.get('partition_key')!r} sorting={layout.get('sorting_key')!r}"
        )
    if table_exists(client, database, backup_table):
        raise RuntimeError(f"source engine backup already exists while active table is stale: {database}.{backup_table}")
    pending_mutations = int(
        client.execute(
            f"SELECT count() FROM system.mutations WHERE database={sql_string(database)} "
            f"AND table={sql_string(table_name)} AND is_done=0"
        ).strip()
        or "0"
    )
    if pending_mutations:
        raise RuntimeError(
            f"source engine migration requires a stable table, but {pending_mutations} mutation(s) are still active"
        )

    if report:
        affected = {int(value) for value in report.get("affected_partitions", [])}
    else:
        drift = load_authority_drift(client, database, table_name)
        parent_drift = load_authority_parent_drift(client, database, table_name)
        affected = {
            int(row[key])
            for row in drift
            for key in ("inserted_partition", "authority_partition")
        }
        affected.update(int(row["authority_partition"]) for row in parent_drift)
        report = {
            "run_id": run_id,
            "source_authority_version": SOURCE_AUTHORITY_VERSION,
            "source_table": f"{database}.{table_name}",
            "old_engine": str(layout.get("engine_full") or ""),
            "new_engine": SOURCE_REVISION_ENGINE,
            "authority_drift_documents": sum(int(row["documents"]) for row in drift),
            "authority_drift_filing_pair_sum": sum(int(row["filings"]) for row in drift),
            "authority_parent_drift_documents": sum(int(row["documents"]) for row in parent_drift),
            "authority_parent_drift_accessions": sum(int(row["accessions"]) for row in parent_drift),
            "affected_partitions": sorted(affected),
            "partition_pairs": drift,
            "parent_drift_partitions": parent_drift,
            "migration_status": "active",
            "partition_clone_completed": False,
            "parent_identity_repair_completed": False,
            "renderer_reset_completed": False,
        }
        write_report(report_path, report)

    if not table_exists(client, database, migration_table):
        create_revision_table(client, database, table_name, migration_table, str(layout.get("storage_policy") or ""))
    migration_layout = load_source_layout(client, database, migration_table)
    if not revision_engine_matches(migration_layout):
        raise RuntimeError(f"source migration table has wrong engine: {migration_layout.get('engine_full')!r}")

    source = table(database, table_name)
    destination = table(database, migration_table)
    client.execute(f"SYSTEM STOP MERGES {source}")
    client.execute(f"SYSTEM STOP MERGES {destination}")
    partitions = load_physical_partitions(client, database, table_name)
    for index, partition_id in enumerate(partitions, start=1):
        source_stats = physical_stats(client, database, table_name, partition_id)
        destination_stats = physical_stats(client, database, migration_table, partition_id)
        if destination_stats == source_stats:
            print(
                f"source_engine_migration={index}/{len(partitions)} partition={partition_id} "
                f"status=completed reused=true rows={source_stats[0]:,}",
                flush=True,
            )
            continue
        if destination_stats[0]:
            client.execute(f"ALTER TABLE {destination} DROP PARTITION ID {sql_string(partition_id)}")
        print(
            f"source_engine_migration={index}/{len(partitions)} partition={partition_id} "
            f"status=active rows={source_stats[0]:,}",
            flush=True,
        )
        client.execute(
            f"ALTER TABLE {destination} ATTACH PARTITION ID {sql_string(partition_id)} FROM {source}"
        )
        destination_stats = physical_stats(client, database, migration_table, partition_id)
        if destination_stats != source_stats:
            raise RuntimeError(
                f"source engine migration validation failed partition={partition_id} "
                f"source={source_stats} destination={destination_stats}"
            )
        print(
            f"source_engine_migration={index}/{len(partitions)} partition={partition_id} "
            f"status=completed reused=false rows={source_stats[0]:,}",
            flush=True,
        )

    source_stats = physical_stats(client, database, table_name)
    if not report.get("partition_clone_completed", False):
        destination_stats = physical_stats(client, database, migration_table)
        if destination_stats != source_stats:
            raise RuntimeError(
                f"source engine migration total validation failed source={source_stats} destination={destination_stats}"
            )
        report["partition_clone_completed"] = True
        report["physical_rows"] = source_stats[0]
        report["physical_source_bytes"] = source_stats[1]
        write_report(report_path, report)

    remaining_parent_mismatches = authoritative_parent_mismatch_count(
        client, database, migration_table
    )
    if remaining_parent_mismatches:
        print(
            f"source_parent_identity_repair status=active documents={remaining_parent_mismatches:,}",
            flush=True,
        )
        insert_parent_identity_corrections(
            client,
            database=database,
            source_table=table_name,
            destination_table=migration_table,
            run_id=run_id,
        )
        remaining_parent_mismatches = authoritative_parent_mismatch_count(
            client, database, migration_table
        )
    if remaining_parent_mismatches:
        raise RuntimeError(
            f"source parent identity repair left {remaining_parent_mismatches:,} authoritative mismatches"
        )
    report["parent_identity_repair_completed"] = True
    write_report(report_path, report)
    client.execute(f"EXCHANGE TABLES {source} AND {destination}")
    client.execute(f"RENAME TABLE {destination} TO {table(database, backup_table)}")
    client.execute(f"SYSTEM START MERGES {source}")
    client.execute(f"SYSTEM STOP MERGES {table(database, backup_table)}")
    report["migration_status"] = "completed"
    write_report(report_path, report)
    return affected


def mark_renderer_reset_completed(report_path: Path) -> None:
    report = load_report(report_path)
    if not report:
        return
    report["renderer_reset_completed"] = True
    write_report(report_path, report)


def load_source_layout(client: ClickHouseHttpClient, database: str, table_name: str) -> dict[str, Any]:
    rows = json_rows(
        client.execute(
            f"SELECT engine_full,partition_key,sorting_key,storage_policy FROM system.tables "
            f"WHERE database={sql_string(database)} AND name={sql_string(table_name)} FORMAT JSONEachRow"
        )
    )
    if len(rows) != 1:
        raise RuntimeError(f"source table metadata missing: {database}.{table_name}")
    return rows[0]


def revision_engine_matches(layout: dict[str, Any]) -> bool:
    return normalize_engine(layout.get("engine_full")) == normalize_engine(SOURCE_REVISION_ENGINE)


def normalize_engine(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def load_authority_drift(
    client: ClickHouseHttpClient,
    database: str,
    table_name: str,
) -> list[dict[str, Any]]:
    return json_rows(
        client.execute(
            f"""
SELECT inserted_partition, authority_partition, count() AS documents,
       uniqExact(accession_number) AS filings
FROM
(
    SELECT accession_number,
           argMax(toYYYYMM(source_archive_date), tuple(inserted_at, source_revision_rank, source_text_byte_count)) AS inserted_partition,
           argMax(toYYYYMM(source_archive_date), tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS authority_partition,
           argMax(tuple(filing_id, content_sha256, source_revision_rank), tuple(inserted_at, source_revision_rank, source_text_byte_count)) AS inserted_winner,
           argMax(tuple(filing_id, content_sha256, source_revision_rank), tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS authority_winner
    FROM {table(database, table_name)}
    GROUP BY cik, accession_number, document_id, content_format
    HAVING inserted_winner != authority_winner
)
GROUP BY inserted_partition, authority_partition
ORDER BY inserted_partition, authority_partition
SETTINGS max_threads=4, max_memory_usage=68719476736
FORMAT JSONEachRow
"""
        )
    )


def load_authority_parent_drift(
    client: ClickHouseHttpClient,
    database: str,
    table_name: str,
) -> list[dict[str, Any]]:
    return json_rows(
        client.execute(
            f"""
WITH
f AS
(
    SELECT cik, accession_number, filing_id
    FROM {table(database, 'sec_filing_v3')} FINAL
),
s AS
(
    SELECT cik, accession_number,
           argMax(filing_id, tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS filing_id,
           argMax(toYYYYMM(source_archive_date), tuple(source_revision_rank, source_text_byte_count, source_version_key)) AS authority_partition
    FROM {table(database, table_name)}
    GROUP BY cik, accession_number, document_id, content_format
)
SELECT authority_partition, count() AS documents, uniqExact(accession_number) AS accessions
FROM s INNER JOIN f USING (cik, accession_number)
WHERE s.filing_id != f.filing_id
GROUP BY authority_partition
ORDER BY authority_partition
SETTINGS max_threads=4, max_memory_usage=68719476736
FORMAT JSONEachRow
"""
        )
    )


def authoritative_parent_mismatch_count(
    client: ClickHouseHttpClient,
    database: str,
    table_name: str,
) -> int:
    text = client.execute(
        f"""
WITH f AS
(
    SELECT cik, accession_number, filing_id
    FROM {table(database, 'sec_filing_v3')} FINAL
)
SELECT count()
FROM {table(database, table_name)} AS s FINAL
INNER JOIN f USING (cik, accession_number)
WHERE s.filing_id != f.filing_id
SETTINGS do_not_merge_across_partitions_select_final=0, max_threads=4, max_memory_usage=68719476736
"""
    )
    return int(text.strip() or "0")


def insert_parent_identity_corrections(
    client: ClickHouseHttpClient,
    *,
    database: str,
    source_table: str,
    destination_table: str,
    run_id: str,
    accessions: set[str] | None = None,
) -> None:
    conditions = ["s.filing_id != f.filing_id"]
    if accessions:
        conditions.append("s.accession_number IN (" + ",".join(sql_string(value) for value in sorted(accessions)) + ")")
    client.execute(
        f"""
INSERT INTO {table(database, destination_table)}
WITH f AS
(
    SELECT cik, accession_number, filing_id
    FROM {table(database, 'sec_filing_v3')} FINAL
)
SELECT s.* REPLACE
(
    f.filing_id AS filing_id,
    'source_parent_identity_repair' AS source_revision_kind,
    {sql_string(run_id)} AS source_run_id,
    now64(3, 'UTC') AS inserted_at
)
FROM {table(database, source_table)} AS s
INNER JOIN f USING (cik, accession_number)
WHERE {" AND ".join(conditions)}
SETTINGS max_threads=4, max_insert_threads=1, max_memory_usage=68719476736,
         max_bytes_before_external_sort=4294967296
"""
    )


def create_revision_table(
    client: ClickHouseHttpClient,
    database: str,
    source_table: str,
    destination_table: str,
    storage_policy: str,
) -> None:
    settings = "index_granularity=8192"
    if storage_policy:
        settings += f", storage_policy={sql_string(storage_policy)}"
    client.execute(
        f"""
CREATE TABLE {table(database, destination_table)} AS {table(database, source_table)}
ENGINE = {SOURCE_REVISION_ENGINE}
PARTITION BY {TEXT_SOURCE_PARTITION_KEY}
ORDER BY ({TEXT_SOURCE_SORTING_KEY})
SETTINGS {settings}
"""
    )


def load_physical_partitions(client: ClickHouseHttpClient, database: str, table_name: str) -> list[str]:
    text = client.execute(
        f"SELECT DISTINCT partition_id FROM system.parts WHERE database={sql_string(database)} "
        f"AND table={sql_string(table_name)} AND active ORDER BY partition_id FORMAT TSV"
    )
    return [line.strip() for line in text.splitlines() if line.strip()]


def physical_stats(
    client: ClickHouseHttpClient,
    database: str,
    table_name: str,
    partition_id: str | None = None,
) -> tuple[int, int, int]:
    where = f"WHERE _partition_id={sql_string(partition_id)}" if partition_id is not None else ""
    rows = json_rows(
        client.execute(
            f"""
SELECT count() AS rows, sum(source_text_byte_count) AS source_bytes,
       groupBitXor(cityHash64(cik, accession_number, document_id, content_format, filing_id,
                              content_sha256, source_revision_rank, source_version_key,
                              source_text_byte_count)) AS metadata_hash
FROM {table(database, table_name)}
{where}
FORMAT JSONEachRow
"""
        )
    )
    row = rows[0]
    return int(row["rows"]), int(row.get("source_bytes") or 0), int(row.get("metadata_hash") or 0)


def finalize_interrupted_cutover(
    client: ClickHouseHttpClient,
    database: str,
    source_table: str,
    migration_table: str,
    backup_table: str,
) -> None:
    if table_exists(client, database, migration_table):
        if table_exists(client, database, backup_table):
            raise RuntimeError(
                f"both source migration and backup tables exist after cutover: {migration_table}, {backup_table}"
            )
        client.execute(f"RENAME TABLE {table(database, migration_table)} TO {table(database, backup_table)}")
    client.execute(f"SYSTEM START MERGES {table(database, source_table)}")
    if table_exists(client, database, backup_table):
        client.execute(f"SYSTEM STOP MERGES {table(database, backup_table)}")


def table_exists(client: ClickHouseHttpClient, database: str, table_name: str) -> bool:
    value = client.execute(
        f"SELECT count() FROM system.tables WHERE database={sql_string(database)} "
        f"AND name={sql_string(table_name)}"
    ).strip()
    return bool(int(value or "0"))


def table(database: str, table_name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(table_name)}"


def json_rows(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)

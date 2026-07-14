from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_filing_text_extract_parts import FILING_COLUMNS  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


FALLBACK_SOURCES = (
    "archive_filing_date_midnight",
    "archive_date_midnight",
    "filing_date_midnight_fallback",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair date-only SEC v3 acceptance timestamps from preserved raw SEC timestamps. "
            "Only explicit UTC values ending in Z are accepted; stored legacy timestamps are never trusted."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", "q_live"))
    parser.add_argument("--target-table", default=os.environ.get("SEC_FILING_TABLE", "sec_filing_v3"))
    parser.add_argument("--mirror-database", default=os.environ.get("SEC_BULK_MIRROR_DATABASE", "sec_core"))
    parser.add_argument("--mirror-table", default="sec_bulk_mirror_filing_v3")
    parser.add_argument("--enriched-table", default="sec_bulk_mirror_filing_acceptance_v3")
    parser.add_argument(
        "--max-partitions-per-insert-block",
        type=int,
        default=int(os.environ.get("SEC_ACCEPTANCE_REPAIR_MAX_PARTITIONS", "1000")),
    )
    parser.add_argument("--max-threads", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_REPAIR_MAX_THREADS", "32")))
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    started = time.perf_counter()
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier_args(args)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    require_table(client, args.target_database, args.target_table)

    require_table(client, args.mirror_database, args.mirror_table)
    enriched_exists = table_exists(client, args.mirror_database, args.enriched_table)
    if args.execute and not enriched_exists:
        require_table(client, args.mirror_database, args.enriched_table)

    run_id = f"sec_acceptance_raw_metadata_repair_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    resolved_cte = resolved_raw_cte_sql(
        args.mirror_database,
        args.mirror_table,
        args.enriched_table if enriched_exists else None,
    )
    before = query_summary(client, args, resolved_cte)
    if int(before["target_partitions"]) > int(args.max_partitions_per_insert_block):
        raise SystemExit(
            "SEC acceptance repair requires "
            f"{before['target_partitions']} target partitions, exceeding "
            f"--max-partitions-per-insert-block={args.max_partitions_per_insert_block}."
        )
    print("=" * 96, flush=True)
    print("SEC raw acceptance metadata repair", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"target={args.target_database}.{args.target_table}", flush=True)
    print(f"source={args.mirror_database}.{args.mirror_table}", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["CLICKHOUSE_URL", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"]), sort_keys=True), flush=True)
    print("before=" + json.dumps(before, sort_keys=True), flush=True)
    print("=" * 96, flush=True)

    if args.execute and int(before["repairable_rows"]) > 0:
        client.execute(insert_replacements_sql(args, resolved_cte, run_id))
        client.execute(delete_replaced_fallbacks_sql(args))
    after = query_summary(client, args, resolved_cte)
    summary = {
        "run_id": run_id,
        "execute": bool(args.execute),
        "source_tables": [f"{args.mirror_database}.{args.mirror_table}"]
        + ([f"{args.mirror_database}.{args.enriched_table}"] if enriched_exists else []),
        "fallback_rows_before": int(before["fallback_rows"]),
        "repairable_rows_before": int(before["repairable_rows"]),
        "differing_rows_before": int(before["differing_rows"]),
        "equal_rows_before": int(before["equal_rows"]),
        "cross_partition_rows_before": int(before["cross_partition_rows"]),
        "target_partitions_before": int(before["target_partitions"]),
        "unresolved_rows_before": int(before["unresolved_rows"]),
        "fallback_rows_after": int(after["fallback_rows"]),
        "repairable_rows_after": int(after["repairable_rows"]),
        "unresolved_rows_after": int(after["unresolved_rows"]),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    return 0


def resolved_raw_cte_sql(database: str, table: str, enriched_table: str | None = "sec_bulk_mirror_filing_acceptance_v3") -> str:
    enriched_union = ""
    if enriched_table:
        enriched_union = f"""

    UNION ALL

    SELECT
        e.cik,
        e.accession_number,
        e.acceptance_datetime_raw,
        concat('sec_core_', e.accepted_at_source, '_raw_z_repair') AS accepted_at_source,
        2 AS source_priority
    FROM {quote_ident(database)}.{quote_ident(enriched_table)} AS e FINAL
    WHERE e.acceptance_datetime_raw IS NOT NULL
      AND endsWith(e.acceptance_datetime_raw, 'Z')
      AND parseDateTime64BestEffortOrNull(e.acceptance_datetime_raw, 9, 'UTC') IS NOT NULL
"""
    return f"""
raw_candidates AS
(
    SELECT
        cik,
        accession_number,
        acceptance_datetime_raw,
        'sec_core_submissions_bulk_raw_z_repair' AS accepted_at_source,
        1 AS source_priority
    FROM {quote_ident(database)}.{quote_ident(table)} FINAL
    WHERE acceptance_datetime_raw IS NOT NULL
      AND endsWith(acceptance_datetime_raw, 'Z')
      AND parseDateTime64BestEffortOrNull(acceptance_datetime_raw, 9, 'UTC') IS NOT NULL
    {enriched_union}
),
resolved_raw_values AS
(
    SELECT
        c.cik,
        c.accession_number,
        argMax(c.acceptance_datetime_raw, c.source_priority) AS acceptance_datetime_raw,
        argMax(c.accepted_at_source, c.source_priority) AS accepted_at_source
    FROM raw_candidates AS c
    GROUP BY c.cik, c.accession_number
),
resolved_raw AS
(
    SELECT
        cik,
        accession_number,
        acceptance_datetime_raw,
        accepted_at_source,
        parseDateTime64BestEffortOrNull(acceptance_datetime_raw, 9, 'UTC') AS accepted_at_utc
    FROM resolved_raw_values
)
""".strip()


def query_summary(client: ClickHouseHttpClient, args: argparse.Namespace, resolved_cte: str) -> dict[str, int]:
    sources = ", ".join(sql_string(value) for value in FALLBACK_SOURCES)
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    text = client.execute(
        f"""
WITH {resolved_cte}
SELECT
    count() AS fallback_rows,
    countIf(r.accepted_at_utc IS NOT NULL) AS repairable_rows,
    countIf(r.accepted_at_utc IS NULL) AS unresolved_rows,
    countIf(r.accepted_at_utc IS NOT NULL AND r.accepted_at_utc != f.accepted_at_utc) AS differing_rows,
    countIf(r.accepted_at_utc IS NOT NULL AND r.accepted_at_utc = f.accepted_at_utc) AS equal_rows,
    countIf(
        r.accepted_at_utc IS NOT NULL
        AND toYYYYMM(r.accepted_at_utc) != toYYYYMM(coalesce(f.accepted_at_utc, toDateTime64(ifNull(f.filing_date, toDate('1970-01-01')), 9, 'UTC')))
    ) AS cross_partition_rows,
    uniqExactIf(toYYYYMM(r.accepted_at_utc), r.accepted_at_utc IS NOT NULL) AS target_partitions
FROM {target} AS f FINAL
LEFT JOIN resolved_raw AS r USING (cik, accession_number)
WHERE f.accepted_at_source IN ({sources})
FORMAT JSONEachRow
"""
    ).strip()
    return json.loads(text or "{}")


def insert_replacements_sql(args: argparse.Namespace, resolved_cte: str, run_id: str) -> str:
    sources = ", ".join(sql_string(value) for value in FALLBACK_SOURCES)
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    select_columns = []
    for column in FILING_COLUMNS:
        if column == "accepted_at_utc":
            select_columns.append("r.accepted_at_utc AS accepted_at_utc")
        elif column == "acceptance_datetime_raw":
            select_columns.append("r.acceptance_datetime_raw AS acceptance_datetime_raw")
        elif column == "accepted_at_source":
            select_columns.append("r.accepted_at_source AS accepted_at_source")
        elif column == "source_run_id":
            select_columns.append(f"{sql_string(run_id)} AS source_run_id")
        elif column == "inserted_at":
            select_columns.append("now64(3, 'UTC') AS inserted_at")
        else:
            select_columns.append(f"f.{quote_ident(column)}")
    return f"""
INSERT INTO {target} ({', '.join(quote_ident(column) for column in FILING_COLUMNS)})
WITH {resolved_cte}
SELECT
    {', '.join(select_columns)}
FROM {target} AS f FINAL
INNER JOIN resolved_raw AS r USING (cik, accession_number)
WHERE f.accepted_at_source IN ({sources})
SETTINGS
    date_time_input_format = 'best_effort',
    max_partitions_per_insert_block = {int(args.max_partitions_per_insert_block)},
    max_threads = {max(1, int(args.max_threads))}
""".strip()


def delete_replaced_fallbacks_sql(args: argparse.Namespace) -> str:
    sources = ", ".join(sql_string(value) for value in FALLBACK_SOURCES)
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    mirror = f"{quote_ident(args.mirror_database)}.{quote_ident(args.mirror_table)}"
    enriched = f"{quote_ident(args.mirror_database)}.{quote_ident(getattr(args, 'enriched_table', 'sec_bulk_mirror_filing_acceptance_v3'))}"
    return f"""
ALTER TABLE {target}
DELETE WHERE accepted_at_source IN ({sources})
  AND (cik, accession_number) IN
  (
      SELECT cik, accession_number
      FROM {mirror} FINAL
      WHERE acceptance_datetime_raw IS NOT NULL
        AND endsWith(acceptance_datetime_raw, 'Z')
        AND parseDateTime64BestEffortOrNull(acceptance_datetime_raw, 9, 'UTC') IS NOT NULL

      UNION ALL

      SELECT cik, accession_number
      FROM {enriched} FINAL
      WHERE acceptance_datetime_raw IS NOT NULL
        AND endsWith(acceptance_datetime_raw, 'Z')
        AND parseDateTime64BestEffortOrNull(acceptance_datetime_raw, 9, 'UTC') IS NOT NULL
  )
SETTINGS mutations_sync = 2, allow_nondeterministic_mutations = 1
""".strip()


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    out = client.execute(
        f"SELECT count() FROM system.tables WHERE database={sql_string(database)} AND name={sql_string(table)} FORMAT TSV"
    )
    return int(out.strip() or "0") == 1


def require_table(client: ClickHouseHttpClient, database: str, table: str) -> None:
    if not table_exists(client, database, table):
        raise SystemExit(f"Required table does not exist: {database}.{table}")


def validate_identifier_args(args: argparse.Namespace) -> None:
    for name in ("target_database", "target_table", "mirror_database", "mirror_table", "enriched_table"):
        value = str(getattr(args, name))
        if not value or not value.replace("_", "").isalnum():
            raise SystemExit(f"Invalid --{name.replace('_', '-')}: {value!r}")
    if int(args.max_partitions_per_insert_block) < 1:
        raise SystemExit("--max-partitions-per-insert-block must be >= 1")
    if int(args.max_threads) < 1:
        raise SystemExit("--max-threads must be >= 1")


if __name__ == "__main__":
    raise SystemExit(main())

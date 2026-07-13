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
    parser.add_argument("--legacy-database", default="q_live")
    parser.add_argument("--legacy-table", default="sec_filing_v2")
    parser.add_argument("--no-legacy-source", action="store_true")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    started = time.perf_counter()
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier_args(args)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    require_table(client, args.target_database, args.target_table)

    sources: list[tuple[str, str, int, str]] = []
    if table_exists(client, args.mirror_database, args.mirror_table):
        sources.append((args.mirror_database, args.mirror_table, 20, "sec_core_raw_z_acceptance_repair"))
    if not args.no_legacy_source and table_exists(client, args.legacy_database, args.legacy_table):
        sources.append((args.legacy_database, args.legacy_table, 10, "legacy_raw_z_acceptance_repair"))
    if not sources:
        raise SystemExit("No authoritative raw acceptance source table is available.")

    run_id = f"sec_acceptance_raw_metadata_repair_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    resolved_cte = resolved_raw_cte_sql(sources)
    before = query_summary(client, args, resolved_cte)
    print("=" * 96, flush=True)
    print("SEC raw acceptance metadata repair", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"target={args.target_database}.{args.target_table}", flush=True)
    print(f"sources={[f'{db}.{table}' for db, table, _, _ in sources]}", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["CLICKHOUSE_URL", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"]), sort_keys=True), flush=True)
    print("before=" + json.dumps(before, sort_keys=True), flush=True)
    print("=" * 96, flush=True)

    if args.execute and int(before["repairable_rows"]) > 0:
        client.execute(insert_replacements_sql(args, resolved_cte, run_id))
    after = query_summary(client, args, resolved_cte)
    summary = {
        "run_id": run_id,
        "execute": bool(args.execute),
        "source_tables": [f"{db}.{table}" for db, table, _, _ in sources],
        "fallback_rows_before": int(before["fallback_rows"]),
        "repairable_rows_before": int(before["repairable_rows"]),
        "unresolved_rows_before": int(before["unresolved_rows"]),
        "fallback_rows_after": int(after["fallback_rows"]),
        "repairable_rows_after": int(after["repairable_rows"]),
        "unresolved_rows_after": int(after["unresolved_rows"]),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    return 0


def resolved_raw_cte_sql(sources: list[tuple[str, str, int, str]]) -> str:
    source_sql = []
    for database, table, priority, source_name in sources:
        source_sql.append(
            f"""
SELECT
    cik,
    accession_number,
    acceptance_datetime_raw AS raw_value,
    toUInt8({priority}) AS source_priority,
    {sql_string(source_name)} AS repaired_source
FROM {quote_ident(database)}.{quote_ident(table)} FINAL
WHERE acceptance_datetime_raw IS NOT NULL
  AND endsWith(acceptance_datetime_raw, 'Z')
  AND parseDateTime64BestEffortOrNull(acceptance_datetime_raw, 9, 'UTC') IS NOT NULL
""".strip()
        )
    return f"""
raw_candidates AS
(
    {f' UNION ALL '.join(source_sql)}
),
resolved_raw AS
(
    SELECT
        cik,
        accession_number,
        tupleElement(argMax(tuple(raw_value, repaired_source), source_priority), 1) AS acceptance_datetime_raw,
        tupleElement(argMax(tuple(raw_value, repaired_source), source_priority), 2) AS accepted_at_source,
        parseDateTime64BestEffortOrNull(acceptance_datetime_raw, 9, 'UTC') AS accepted_at_utc
    FROM raw_candidates
    GROUP BY cik, accession_number
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
    countIf(r.accepted_at_utc IS NULL) AS unresolved_rows
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
SETTINGS date_time_input_format = 'best_effort'
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
    for name in ("target_database", "target_table", "mirror_database", "mirror_table", "legacy_database", "legacy_table"):
        value = str(getattr(args, name))
        if not value or not value.replace("_", "").isalnum():
            raise SystemExit(f"Invalid --{name.replace('_', '-')}: {value!r}")


if __name__ == "__main__":
    raise SystemExit(main())

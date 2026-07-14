from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


DEPENDENT_TABLES = (
    "sec_filing_document_v3",
    "sec_filing_text_v3",
    "sec_filing_text_rendered_v3",
    "sec_filing_document_skip_v3",
    "sec_xbrl_company_fact_v3",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Remove submissions-derived q_live filing parents that only duplicate an accession-to-CIK "
            "relationship. A row is eligible only when another single CIK owns all dependent v3 data."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", "q_live"))
    parser.add_argument("--filing-table", default="sec_filing_v3")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier(args.database, "--database")
    validate_identifier(args.filing_table, "--filing-table")
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    require_tables(client, args.database, (args.filing_table, *DEPENDENT_TABLES))

    run_id = "sec_filing_parent_reconcile_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    stage_table = f"sec_filing_parent_reconcile_keys__{run_id}"
    started = time.perf_counter()
    before = parent_summary(client, args.database, args.filing_table)
    candidate_count = scalar_int(client, candidate_count_sql(args.database, args.filing_table))
    print("=" * 96, flush=True)
    print("SEC filing parent reconciliation", flush=True)
    print(f"run_id={run_id} execute={args.execute}", flush=True)
    print(f"target={args.database}.{args.filing_table}", flush=True)
    print("before=" + json.dumps(before, sort_keys=True), flush=True)
    print(f"safe_relationship_only_candidates={candidate_count:,}", flush=True)
    print("=" * 96, flush=True)

    deleted = 0
    if args.execute and candidate_count:
        create_candidate_stage(client, args.database, stage_table, args.filing_table)
        staged = scalar_int(client, f"SELECT count() FROM {table(args.database, stage_table)}")
        if staged != candidate_count:
            safe_drop(client, args.database, stage_table)
            raise RuntimeError(f"candidate stage drifted: counted={candidate_count:,} staged={staged:,}")
        dependent_overlap = scalar_int(client, staged_dependency_overlap_sql(args.database, stage_table))
        if dependent_overlap:
            safe_drop(client, args.database, stage_table)
            raise RuntimeError(f"refusing parent repair: {dependent_overlap:,} staged keys have dependent rows")
        client.execute(delete_sql(args.database, args.filing_table, stage_table))
        remaining = scalar_int(
            client,
            f"SELECT count() FROM {table(args.database, args.filing_table)} FINAL "
            f"WHERE (cik, accession_number) IN (SELECT cik, accession_number FROM {table(args.database, stage_table)})",
        )
        if remaining:
            raise RuntimeError(f"parent reconciliation mutation left {remaining:,} staged rows")
        deleted = staged
        safe_drop(client, args.database, stage_table)

    after = parent_summary(client, args.database, args.filing_table)
    summary = {
        "run_id": run_id,
        "execute": bool(args.execute),
        "candidate_rows": candidate_count,
        "deleted_rows": deleted,
        "before": before,
        "after": after,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    return 0


def child_keys_cte(database: str) -> str:
    db = quote_ident(database)
    unions = [
        f"SELECT cik, accession_number FROM {db}.{quote_ident(name)} FINAL"
        for name in DEPENDENT_TABLES
    ]
    return "\nUNION ALL\n".join(unions)


def candidates_select_sql(database: str, filing_table: str) -> str:
    target = table(database, filing_table)
    return f"""
WITH
child_keys AS
(
    SELECT cik, accession_number
    FROM
    (
        {child_keys_cte(database)}
    )
    WHERE cik != '' AND accession_number != ''
    GROUP BY cik, accession_number
),
single_owner AS
(
    SELECT accession_number, any(cik) AS canonical_cik
    FROM child_keys
    GROUP BY accession_number
    HAVING uniqExact(cik) = 1
)
SELECT assumeNotNull(p.cik) AS cik, assumeNotNull(p.accession_number) AS accession_number
FROM {target} AS p FINAL
INNER JOIN single_owner AS o USING (accession_number)
LEFT ANTI JOIN child_keys AS c
    ON p.cik = c.cik AND p.accession_number = c.accession_number
WHERE p.text_status = 'submissions_bulk_parent'
  AND p.cik != o.canonical_cik
GROUP BY p.cik, p.accession_number
""".strip()


def candidate_count_sql(database: str, filing_table: str) -> str:
    return f"SELECT count() FROM ({candidates_select_sql(database, filing_table)})"


def create_candidate_stage(client: ClickHouseHttpClient, database: str, stage_table: str, filing_table: str) -> None:
    client.execute(
        f"CREATE TABLE {table(database, stage_table)} "
        "ENGINE = MergeTree ORDER BY (cik, accession_number) AS "
        + candidates_select_sql(database, filing_table)
    )


def staged_dependency_overlap_sql(database: str, stage_table: str) -> str:
    return f"""
SELECT count()
FROM {table(database, stage_table)} AS s
INNER JOIN
(
    SELECT cik, accession_number
    FROM
    (
        {child_keys_cte(database)}
    )
    GROUP BY cik, accession_number
) AS c USING (cik, accession_number)
""".strip()


def delete_sql(database: str, filing_table: str, stage_table: str) -> str:
    return f"""
ALTER TABLE {table(database, filing_table)}
DELETE WHERE (cik, accession_number) IN
(
    SELECT cik, accession_number FROM {table(database, stage_table)}
)
SETTINGS mutations_sync = 2, allow_nondeterministic_mutations = 1
""".strip()


def parent_summary(client: ClickHouseHttpClient, database: str, filing_table: str) -> dict[str, int]:
    text = client.execute(
        f"""
SELECT
    count() AS rows,
    uniqExact(accession_number) AS accessions,
    countIf(text_status = 'submissions_bulk_parent') AS submissions_bulk_parent_rows
FROM {table(database, filing_table)} FINAL
FORMAT JSONEachRow
"""
    ).strip()
    row = json.loads(text or "{}")
    row["multi_cik_accessions"] = scalar_int(
        client,
        f"SELECT countIf(c > 1) FROM (SELECT accession_number, count() AS c "
        f"FROM {table(database, filing_table)} FINAL GROUP BY accession_number)",
    )
    return {key: int(value) for key, value in row.items()}


def require_tables(client: ClickHouseHttpClient, database: str, names: tuple[str, ...]) -> None:
    requested = ",".join(sql_string(name) for name in names)
    present = {
        line.strip()
        for line in client.execute(
            f"SELECT name FROM system.tables WHERE database={sql_string(database)} AND name IN ({requested}) FORMAT TSV"
        ).splitlines()
        if line.strip()
    }
    missing = sorted(set(names) - present)
    if missing:
        raise SystemExit(f"Required tables are missing from {database}: {missing}")


def safe_drop(client: ClickHouseHttpClient, database: str, name: str) -> None:
    client.execute(f"DROP TABLE IF EXISTS {table(database, name)} SYNC")


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    return int(client.execute(sql.strip() + "\nFORMAT TSV").strip() or "0")


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


if __name__ == "__main__":
    raise SystemExit(main())

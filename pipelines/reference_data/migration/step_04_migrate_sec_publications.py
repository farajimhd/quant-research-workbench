from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.paths import machine_name  # noqa: E402


DEFAULT_SOURCE_DATABASE = "trading_dashboard_dev"
DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_04_sec_publications")


@dataclass(frozen=True, slots=True)
class StepPaths:
    run_root: Path
    manifest_json: Path
    execution_jsonl: Path
    rendered_sql: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "StepPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "step_04_manifest.json",
            execution_jsonl=run_root / "step_04_execution.jsonl",
            rendered_sql=run_root / "step_04_insert_select.sql",
        )


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    name: str
    target_table: str
    source_tables: tuple[str, ...]
    columns: tuple[str, ...]
    select_sql: str
    source_count_sql: str
    expected_count_sql: str
    critical_columns: tuple[str, ...]
    batch_date_column: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 4 of q_live migration: migrate SEC filing and XBRL publication tables. "
            "Default mode is dry-run and writes counts/rendered SQL without inserting rows."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--source-database", default=os.environ.get("QLIVE_MIGRATION_SOURCE_DATABASE", DEFAULT_SOURCE_DATABASE))
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_04_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--execute", action="store_true", help="Execute inserts. Without this flag, the script is dry-run only.")
    parser.add_argument("--validate-only", action="store_true", help="Record validation rows for already migrated targets without inserting migration rows.")
    parser.add_argument("--allow-non-empty-targets", action="store_true", help="Permit appending/upserting into target tables that already contain rows.")
    parser.add_argument("--skip-non-empty-targets", action="store_true", help="Resume mode: execute only specs whose target table is empty.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_database_name(args.source_database, "--source-database")
    validate_database_name(args.target_database, "--target-database")

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    migration_run_id = f"step_04_sec_publications_{run_id}"
    inserted_at = clickhouse_now64()
    paths = StepPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    specs = build_specs(args.source_database, migration_run_id, inserted_at)

    print_header(args, paths, loaded_env, migration_run_id, specs)
    preflight = run_preflight(client, args, specs)
    write_rendered_sql(paths.rendered_sql, args.target_database, specs)
    write_manifest(paths.manifest_json, args, paths, loaded_env, migration_run_id, specs, preflight)

    if args.validate_only:
        totals = totals_from_preflight(preflight)
        insert_run_row(client, args.source_database, args.target_database, migration_run_id, "validation_only", inserted_at, rows_read=sum(item["source_rows"] for item in totals), rows_written=0, rows_failed=0)
        write_validations(client, args.target_database, migration_run_id, specs, totals, inserted_at)
        write_execution_row(paths.execution_jsonl, {"status": "validation_only", "migration_run_id": migration_run_id, "preflight": preflight})
        print("validation_only=true; no migration rows inserted", flush=True)
        return

    if not args.execute:
        write_execution_row(paths.execution_jsonl, {"status": "dry_run", "migration_run_id": migration_run_id, "preflight": preflight})
        print("dry_run=true; no rows inserted", flush=True)
        print(f"rendered_sql={paths.rendered_sql}", flush=True)
        return

    ensure_empty_or_allowed(preflight, args)
    specs_to_execute = filter_specs_for_resume(specs, preflight, args.skip_non_empty_targets)
    insert_run_row(client, args.source_database, args.target_database, migration_run_id, "running", inserted_at, rows_read=0, rows_written=0, rows_failed=0)
    executed_totals = execute_specs(client, args, specs_to_execute, paths.execution_jsonl)
    totals = merge_executed_with_skipped(preflight, executed_totals)
    write_validations(client, args.target_database, migration_run_id, specs, totals, inserted_at)
    insert_run_row(
        client,
        args.source_database,
        args.target_database,
        migration_run_id,
        "completed",
        inserted_at,
        rows_read=sum(item["source_rows"] for item in totals),
        rows_written=sum(item["inserted_delta"] for item in totals),
        rows_failed=sum(item["failed_rows"] for item in totals),
    )
    print("summary=" + json.dumps({"migration_run_id": migration_run_id, "totals": totals}, sort_keys=True), flush=True)


def default_migration_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_migration_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_migration_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


def build_specs(source_db: str, run_id: str, inserted_at: str) -> list[MigrationSpec]:
    s = quote_ident(source_db)
    literal_run_id = sql_string(run_id)
    literal_inserted_at = sql_string(inserted_at)
    suffix = ", ".join([literal_run_id, "source_content_sha256", f"toDateTime64({literal_inserted_at}, 3, 'UTC')"])
    return [
        MigrationSpec(
            name="sec_filing",
            target_table="sec_filing_v2",
            source_tables=("sec_filing_v1",),
            columns=("filing_id", "accession_number", "accession_number_compact", "cik", "issuer_id", "company_name", "form_type", "filing_date", "report_date", "accepted_at_utc", "acceptance_datetime_raw", "accepted_at_source", "primary_document", "primary_document_url", "filing_detail_url", "source_file_name", "filing_size", "items", "text_status", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT
    filing_id,
    accession_number,
    replaceAll(accession_number, '-', '') AS accession_number_compact,
    cik,
    issuer_id,
    CAST(NULL, 'Nullable(String)') AS company_name,
    form_type,
    filing_date,
    report_date,
    CAST(NULL, 'Nullable(DateTime64(9, \\'UTC\\'))') AS accepted_at_utc,
    CAST(NULL, 'Nullable(String)') AS acceptance_datetime_raw,
    'missing_in_source' AS accepted_at_source,
    primary_document,
    if(primary_document IS NULL OR primary_document = '', CAST(NULL, 'Nullable(String)'), concat('https://www.sec.gov/Archives/edgar/data/', toString(toUInt64(cik)), '/', replaceAll(accession_number, '-', ''), '/', primary_document)) AS primary_document_url,
    concat('https://www.sec.gov/Archives/edgar/data/', toString(toUInt64(cik)), '/', replaceAll(accession_number, '-', ''), '/', replaceAll(accession_number, '-', ''), '-index.html') AS filing_detail_url,
    source_file_name,
    CAST(NULL, 'Nullable(UInt64)') AS filing_size,
    CAST(NULL, 'Nullable(String)') AS items,
    'metadata_only' AS text_status,
    {suffix}
FROM {s}.sec_filing_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.sec_filing_v1",
            expected_count_sql=f"SELECT uniqExact(tuple(cik, accession_number)) FROM {s}.sec_filing_v1",
            critical_columns=("filing_id", "accession_number", "cik", "form_type"),
            batch_date_column="filing_date",
        ),
        MigrationSpec(
            name="sec_xbrl_concept",
            target_table="sec_xbrl_concept_v1",
            source_tables=("sec_xbrl_concept_v1",),
            columns=("concept_id", "taxonomy", "tag", "concept_label", "concept_description", "first_observed_at_utc", "last_observed_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT concept_id, taxonomy, tag, concept_label, concept_description, first_observed_at_utc, last_observed_at_utc, {suffix}
FROM {s}.sec_xbrl_concept_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.sec_xbrl_concept_v1",
            expected_count_sql=f"SELECT uniqExact(tuple(taxonomy, tag)) FROM {s}.sec_xbrl_concept_v1",
            critical_columns=("concept_id", "taxonomy", "tag"),
        ),
        MigrationSpec(
            name="sec_xbrl_company_fact",
            target_table="sec_xbrl_company_fact_v1",
            source_tables=("sec_xbrl_company_fact_v1",),
            columns=("company_fact_id", "issuer_id", "cik", "taxonomy", "tag", "unit_code", "fiscal_year", "fiscal_period", "filed_at_utc", "period_end_date", "value", "form_type", "accession_number", "recorded_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT company_fact_id, issuer_id, cik, taxonomy, tag, unit_code, fiscal_year, fiscal_period, filed_at_utc, period_end_date, value, form_type, accession_number, recorded_at_utc, {suffix}
FROM {s}.sec_xbrl_company_fact_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.sec_xbrl_company_fact_v1",
            expected_count_sql=f"SELECT uniqExact(tuple(cik, taxonomy, tag, unit_code, ifNull(period_end_date, toDate('1970-01-01')), ifNull(accession_number, ''))) FROM {s}.sec_xbrl_company_fact_v1",
            critical_columns=("company_fact_id", "cik", "taxonomy", "tag", "unit_code"),
            batch_date_column="period_end_date",
        ),
        MigrationSpec(
            name="sec_xbrl_frame",
            target_table="sec_xbrl_frame_v1",
            source_tables=("sec_xbrl_frame_v1",),
            columns=("frame_id", "taxonomy", "tag", "unit_code", "calendar_period_code", "recorded_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT frame_id, taxonomy, tag, unit_code, calendar_period_code, recorded_at_utc, {suffix}
FROM {s}.sec_xbrl_frame_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.sec_xbrl_frame_v1",
            expected_count_sql=f"SELECT uniqExact(tuple(taxonomy, tag, unit_code, calendar_period_code)) FROM {s}.sec_xbrl_frame_v1",
            critical_columns=("frame_id", "taxonomy", "tag", "unit_code", "calendar_period_code"),
            batch_date_column="recorded_at_utc",
        ),
        MigrationSpec(
            name="sec_xbrl_frame_observation",
            target_table="sec_xbrl_frame_observation_v1",
            source_tables=("sec_xbrl_frame_observation_v1",),
            columns=("frame_observation_id", "frame_id", "taxonomy", "tag", "unit_code", "calendar_period_code", "issuer_id", "cik", "entity_name", "location_code", "period_end_date", "value", "accession_number", "recorded_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT frame_observation_id, frame_id, taxonomy, tag, unit_code, calendar_period_code, issuer_id, cik, entity_name, location_code, period_end_date, value, accession_number, recorded_at_utc, {suffix}
FROM {s}.sec_xbrl_frame_observation_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.sec_xbrl_frame_observation_v1",
            expected_count_sql=f"SELECT uniqExact(tuple(taxonomy, tag, unit_code, calendar_period_code, cik, accession_number, period_end_date)) FROM {s}.sec_xbrl_frame_observation_v1",
            critical_columns=("frame_observation_id", "frame_id", "cik", "accession_number", "taxonomy", "tag", "unit_code"),
            batch_date_column="period_end_date",
        ),
    ]


def run_preflight(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[MigrationSpec]) -> list[dict[str, Any]]:
    required_source_tables = sorted({table for spec in specs for table in spec.source_tables})
    required_target_tables = sorted({spec.target_table for spec in specs} | {"source_run_v1", "sync_validation_v1"})
    missing_source = missing_tables(client, args.source_database, required_source_tables)
    missing_target = missing_tables(client, args.target_database, required_target_tables)
    if missing_source or missing_target:
        raise SystemExit("Missing required tables: " + json.dumps({"source": missing_source, "target": missing_target}, indent=2))

    rows = []
    for spec in specs:
        source_rows = scalar_int(client, spec.source_count_sql)
        expected_rows = scalar_int(client, spec.expected_count_sql)
        target_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
        target_logical_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)} FINAL")
        critical_empty = critical_empty_count(client, args.target_database, spec.target_table, spec.critical_columns) if target_rows else 0
        rows.append(
            {
                "name": spec.name,
                "target_table": spec.target_table,
                "source_rows": source_rows,
                "expected_logical_rows": expected_rows,
                "target_rows_before": target_rows,
                "target_logical_rows_before": target_logical_rows,
                "target_critical_empty_before": critical_empty,
            }
        )
        print(
            f"preflight {spec.name}: source_rows={source_rows:,} expected_logical_rows={expected_rows:,} "
            f"target_rows_before={target_rows:,} target_logical_rows_before={target_logical_rows:,}",
            flush=True,
        )
    return rows


def execute_specs(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[MigrationSpec], log_path: Path) -> list[dict[str, Any]]:
    totals = []
    for index, spec in enumerate(specs, start=1):
        started = time.perf_counter()
        source_rows = scalar_int(client, spec.source_count_sql)
        expected_rows = scalar_int(client, spec.expected_count_sql)
        before_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
        statements = insert_statements_for_spec(client, args, spec)
        row: dict[str, Any] = {
            "index": index,
            "name": spec.name,
            "target_table": spec.target_table,
            "source_rows": source_rows,
            "expected_logical_rows": expected_rows,
            "target_rows_before": before_rows,
            "started_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            for batch_index, statement in enumerate(statements, start=1):
                client.execute(statement)
                if len(statements) > 1:
                    print(f"  batch {batch_index}/{len(statements)} {spec.name}", flush=True)
            after_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
            after_logical_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)} FINAL")
            critical_empty = critical_empty_count(client, args.target_database, spec.target_table, spec.critical_columns)
            row.update(
                {
                    "status": "ok",
                    "target_rows_after": after_rows,
                    "target_logical_rows_after": after_logical_rows,
                    "inserted_delta": max(0, after_rows - before_rows),
                    "critical_empty_after": critical_empty,
                    "failed_rows": 0,
                    "wall_seconds": round(time.perf_counter() - started, 3),
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "target_rows_after": before_rows,
                    "target_logical_rows_after": before_rows,
                    "failed_rows": source_rows,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "wall_seconds": round(time.perf_counter() - started, 3),
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                }
            )
            write_execution_row(log_path, row)
            raise
        write_execution_row(log_path, row)
        totals.append(row)
        print(f"executed {index}/{len(specs)} {spec.name}: target_rows_after={row['target_rows_after']:,} target_logical_rows_after={row['target_logical_rows_after']:,} seconds={row['wall_seconds']}", flush=True)
    return totals


def insert_statement(target_db: str, spec: MigrationSpec) -> str:
    columns = ", ".join(quote_ident(column) for column in spec.columns)
    return f"INSERT INTO {quote_ident(target_db)}.{quote_ident(spec.target_table)} ({columns})\n{spec.select_sql.strip()}"


def insert_statements_for_spec(client: ClickHouseHttpClient, args: argparse.Namespace, spec: MigrationSpec) -> list[str]:
    if not spec.batch_date_column:
        return [insert_statement(args.target_database, spec)]
    years = source_years(client, args.source_database, spec.source_tables[0], spec.batch_date_column)
    if len(years) <= 1:
        return [insert_statement(args.target_database, spec)]
    statements = []
    columns = ", ".join(quote_ident(column) for column in spec.columns)
    date_col = quote_ident(spec.batch_date_column)
    for year in years:
        statements.append(
            f"""
INSERT INTO {quote_ident(args.target_database)}.{quote_ident(spec.target_table)} ({columns})
SELECT *
FROM
(
{spec.select_sql.strip()}
)
WHERE toYear({date_col}) = {year}
""".strip()
        )
    return statements


def source_years(client: ClickHouseHttpClient, source_db: str, source_table: str, date_column: str) -> list[int]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT toYear({quote_ident(date_column)}) AS year
        FROM {quote_ident(source_db)}.{quote_ident(source_table)}
        GROUP BY year
        ORDER BY year
        """,
    )
    return [int(row["year"]) for row in rows]


def write_rendered_sql(path: Path, target_db: str, specs: list[MigrationSpec]) -> None:
    statements = []
    for spec in specs:
        statements.append(f"-- {spec.name}\n-- target: {spec.target_table}\n{insert_statement(target_db, spec)};")
    path.write_text("\n\n".join(statements) + "\n", encoding="utf-8")


def ensure_empty_or_allowed(preflight: list[dict[str, Any]], args: argparse.Namespace) -> None:
    non_empty = [row for row in preflight if row["target_rows_before"] > 0]
    if non_empty and not args.allow_non_empty_targets and not args.skip_non_empty_targets:
        raise SystemExit("Target tables are not empty. Pass --allow-non-empty-targets to append/upsert: " + json.dumps(non_empty, indent=2))


def filter_specs_for_resume(specs: list[MigrationSpec], preflight: list[dict[str, Any]], skip_non_empty: bool) -> list[MigrationSpec]:
    if not skip_non_empty:
        return specs
    non_empty_by_name = {row["name"]: row["target_rows_before"] > 0 for row in preflight}
    filtered = [spec for spec in specs if not non_empty_by_name.get(spec.name, False)]
    skipped = [spec.name for spec in specs if non_empty_by_name.get(spec.name, False)]
    print("resume_skip_non_empty=" + json.dumps(skipped), flush=True)
    return filtered


def totals_from_preflight(preflight: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": row["name"],
            "target_table": row["target_table"],
            "source_rows": row["source_rows"],
            "expected_logical_rows": row["expected_logical_rows"],
            "target_rows_before": row["target_rows_before"],
            "target_rows_after": row["target_rows_before"],
            "target_logical_rows_after": row["target_logical_rows_before"],
            "inserted_delta": 0,
            "failed_rows": 0,
            "critical_empty_after": row["target_critical_empty_before"],
        }
        for row in preflight
    ]


def merge_executed_with_skipped(preflight: list[dict[str, Any]], executed_totals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {row["name"]: row for row in executed_totals}
    merged = []
    for row in preflight:
        if row["name"] in by_name:
            merged.append(by_name[row["name"]])
        else:
            merged.append(
                {
                    "name": row["name"],
                    "target_table": row["target_table"],
                    "source_rows": row["source_rows"],
                    "expected_logical_rows": row["expected_logical_rows"],
                    "target_rows_before": row["target_rows_before"],
                    "target_rows_after": row["target_rows_before"],
                    "target_logical_rows_after": row["target_logical_rows_before"],
                    "inserted_delta": 0,
                    "failed_rows": 0,
                    "critical_empty_after": row["target_critical_empty_before"],
                    "status": "skipped_existing",
                }
            )
    return merged


def insert_run_row(client: ClickHouseHttpClient, source_db: str, target_db: str, run_id: str, status: str, inserted_at: str, *, rows_read: int, rows_written: int, rows_failed: int) -> None:
    now = clickhouse_now64()
    row = {
        "run_id": run_id,
        "job_name": "step_04_migrate_sec_publications",
        "job_type": "migration",
        "source_system": "trading_dashboard_dev",
        "source_database": source_db,
        "target_database": target_db,
        "status": status,
        "started_at_utc": inserted_at,
        "finished_at_utc": now if status != "running" else None,
        "source_watermark_before": None,
        "source_watermark_after": None,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "rows_failed": rows_failed,
        "config_json": "{}",
        "error_json": "{}",
        "code_version": quiet_git_commit(REPO_ROOT),
        "inserted_at": now,
    }
    insert_json_each_row(client, target_db, "source_run_v1", [row])


def write_validations(client: ClickHouseHttpClient, target_db: str, run_id: str, specs: list[MigrationSpec], totals: list[dict[str, Any]], inserted_at: str) -> None:
    rows = []
    by_name = {row["name"]: row for row in totals}
    for spec in specs:
        result = by_name[spec.name]
        mismatch = abs(int(result["target_logical_rows_after"]) - int(result["expected_logical_rows"]))
        rows.append(
            {
                "validation_id": f"{run_id}:{spec.name}:row_count",
                "run_id": run_id,
                "check_name": "row_count_after_step_04",
                "target_table": spec.target_table,
                "check_status": "pass" if mismatch == 0 else "warn",
                "severity": "info" if mismatch == 0 else "warning",
                "expected_value": str(result["expected_logical_rows"]),
                "observed_value": str(result["target_logical_rows_after"]),
                "mismatch_count": mismatch,
                "details_json": json.dumps({"source_rows": result["source_rows"], "target_rows_after": result["target_rows_after"], "target_logical_rows_after": result["target_logical_rows_after"]}, separators=(",", ":")),
                "checked_at_utc": inserted_at,
            }
        )
        rows.append(
            {
                "validation_id": f"{run_id}:{spec.name}:critical_empty",
                "run_id": run_id,
                "check_name": "critical_columns_not_empty",
                "target_table": spec.target_table,
                "check_status": "pass" if int(result.get("critical_empty_after", 0)) == 0 else "fail",
                "severity": "info" if int(result.get("critical_empty_after", 0)) == 0 else "error",
                "expected_value": "0",
                "observed_value": str(result.get("critical_empty_after", 0)),
                "mismatch_count": int(result.get("critical_empty_after", 0)),
                "details_json": json.dumps({"critical_columns": spec.critical_columns}, separators=(",", ":")),
                "checked_at_utc": inserted_at,
            }
        )
    insert_json_each_row(client, target_db, "sync_validation_v1", rows)


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")


def missing_tables(client: ClickHouseHttpClient, database: str, tables: list[str]) -> list[str]:
    values = ", ".join(sql_string(table) for table in tables)
    found = query_json_each_row(client, f"SELECT name FROM system.tables WHERE database = {sql_string(database)} AND name IN ({values})")
    found_names = {row["name"] for row in found}
    return [table for table in tables if table not in found_names]


def critical_empty_count(client: ClickHouseHttpClient, database: str, table: str, columns: tuple[str, ...]) -> int:
    checks = []
    for column in columns:
        quoted = quote_ident(column)
        checks.append(f"isNull({quoted})")
        checks.append(f"toString({quoted}) = ''")
    return scalar_int(client, f"SELECT countIf({' OR '.join(checks)}) FROM {quote_ident(database)}.{quote_ident(table)}")


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = execute_readonly_with_retries(client, sql.strip() + "\nFORMAT TSV").strip()
    return int(text.splitlines()[0].split("\t")[0]) if text else 0


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = execute_readonly_with_retries(client, sql.rstrip(";") + "\nFORMAT JSONEachRow")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def execute_readonly_with_retries(client: ClickHouseHttpClient, sql: str, *, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.execute(sql)
        except (ConnectionResetError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(0.5 * attempt)
    assert last_error is not None
    raise last_error


def write_execution_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_manifest(path: Path, args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[MigrationSpec], preflight: list[dict[str, Any]]) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "step_04_migrate_sec_publications",
        "migration_run_id": run_id,
        "dry_run": not args.execute,
        "source_database": args.source_database,
        "target_database": args.target_database,
        "run_root": str(paths.run_root),
        "rendered_sql": str(paths.rendered_sql),
        "execution_jsonl": str(paths.execution_jsonl),
        "loaded_env_files": [str(path) for path in loaded_env],
        "tables": [{"name": spec.name, "target_table": spec.target_table, "source_tables": spec.source_tables} for spec in specs],
        "preflight": preflight,
        "secret_status": secret_status(["QLIVE_MIGRATION_CLICKHOUSE_URL", "QLIVE_MIGRATION_CLICKHOUSE_USER", "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_URL", "QMD_CLICKHOUSE_USER", "QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_database_name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[MigrationSpec]) -> None:
    print("=" * 96, flush=True)
    print("q_live migration step 4: SEC publication migration", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"validate_only={args.validate_only}", flush=True)
    print(f"source_database={args.source_database}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"migration_run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"specs={len(specs)}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print("secret_status=" + json.dumps(secret_status(["QLIVE_MIGRATION_CLICKHOUSE_URL", "QLIVE_MIGRATION_CLICKHOUSE_USER", "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_URL", "QMD_CLICKHOUSE_USER", "QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def quiet_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(cwd), stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()

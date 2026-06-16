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

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from pipelines.reference_data.migration.step_02_migrate_reference_identity import build_specs as build_step_02_specs  # noqa: E402
from pipelines.reference_data.migration.step_03_migrate_market_publications import build_specs as build_step_03_specs  # noqa: E402
from pipelines.reference_data.migration.step_04_migrate_sec_publications import build_specs as build_step_04_specs  # noqa: E402
from research.mlops.paths import machine_name  # noqa: E402


DEFAULT_SOURCE_DATABASE = "trading_dashboard_dev"
DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_05_validation")


@dataclass(frozen=True, slots=True)
class StepPaths:
    run_root: Path
    manifest_json: Path
    table_validation_jsonl: Path
    operational_validation_jsonl: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "StepPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "step_05_manifest.json",
            table_validation_jsonl=run_root / "step_05_table_validation.jsonl",
            operational_validation_jsonl=run_root / "step_05_operational_validation.jsonl",
            summary_md=run_root / "step_05_summary.md",
        )


@dataclass(frozen=True, slots=True)
class ValidationSpec:
    step: str
    name: str
    target_table: str
    source_tables: tuple[str, ...]
    source_count_sql: str
    expected_count_sql: str
    critical_columns: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 5 of q_live migration: validate q_live migration reconciliation, "
            "storage policy, critical keys, and known pending SEC/feature work."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--source-database", default=os.environ.get("QLIVE_MIGRATION_SOURCE_DATABASE", DEFAULT_SOURCE_DATABASE))
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_05_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--execute", action="store_true", help="Write validation rows to q_live.source_run_v1 and q_live.sync_validation_v1.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_database_name(args.source_database, "--source-database")
    validate_database_name(args.target_database, "--target-database")

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    validation_run_id = f"step_05_validate_q_live_migration_{run_id}"
    inserted_at = clickhouse_now64()
    paths = StepPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    specs = build_validation_specs(args.source_database, args.target_database, validation_run_id, inserted_at)
    expected_storage_policy = os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", "")

    print_header(args, paths, loaded_env, validation_run_id, specs, expected_storage_policy)
    started = time.perf_counter()
    table_rows = validate_tables(client, args, specs, expected_storage_policy)
    operational_rows = validate_operational_state(client, args, expected_storage_policy)
    wall_seconds = round(time.perf_counter() - started, 3)

    write_jsonl(paths.table_validation_jsonl, table_rows)
    write_jsonl(paths.operational_validation_jsonl, operational_rows)
    write_manifest(paths.manifest_json, args, paths, loaded_env, validation_run_id, specs, expected_storage_policy, wall_seconds)
    write_summary(paths.summary_md, args, validation_run_id, table_rows, operational_rows, wall_seconds)

    if args.execute:
        sync_rows = sync_validation_rows(validation_run_id, table_rows, operational_rows, inserted_at)
        insert_run_row(
            client,
            args.source_database,
            args.target_database,
            validation_run_id,
            "completed",
            inserted_at,
            rows_read=sum(int(row.get("source_rows", 0)) for row in table_rows),
            rows_written=len(sync_rows),
            rows_failed=sum(1 for row in sync_rows if row["check_status"] == "fail"),
        )
        insert_json_each_row(client, args.target_database, "sync_validation_v1", sync_rows)
        print(f"wrote_validation_rows={len(sync_rows)}", flush=True)
    else:
        print("dry_run=true; validation rows were not written to ClickHouse", flush=True)

    print(f"summary_md={paths.summary_md}", flush=True)


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


def build_validation_specs(source_db: str, target_db: str, run_id: str, inserted_at: str) -> list[ValidationSpec]:
    specs = []
    for step, built in (
        ("step_02", build_step_02_specs(source_db, target_db, run_id, inserted_at)),
        ("step_03", build_step_03_specs(source_db, run_id, inserted_at)),
        ("step_04", build_step_04_specs(source_db, run_id, inserted_at)),
    ):
        for spec in built:
            specs.append(
                ValidationSpec(
                    step=step,
                    name=spec.name,
                    target_table=spec.target_table,
                    source_tables=spec.source_tables,
                    source_count_sql=spec.source_count_sql,
                    expected_count_sql=spec.expected_count_sql,
                    critical_columns=spec.critical_columns,
                )
            )
    return specs


def validate_tables(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[ValidationSpec], expected_storage_policy: str) -> list[dict[str, Any]]:
    table_meta = target_table_metadata(client, args.target_database)
    column_map = target_column_map(client, args.target_database)
    rows = []
    for index, spec in enumerate(specs, start=1):
        started = time.perf_counter()
        table_exists = spec.target_table in table_meta
        target_columns = column_map.get(spec.target_table, set())
        row: dict[str, Any] = {
            "index": index,
            "step": spec.step,
            "name": spec.name,
            "target_table": spec.target_table,
            "source_tables": spec.source_tables,
            "table_exists": table_exists,
            "critical_columns": spec.critical_columns,
        }
        if not table_exists:
            row.update({"status": "fail", "message": "target table is missing", "wall_seconds": round(time.perf_counter() - started, 3)})
            rows.append(row)
            continue

        source_rows = scalar_int(client, spec.source_count_sql)
        expected_rows = scalar_int(client, spec.expected_count_sql)
        target_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
        target_logical_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)} FINAL")
        critical_empty = critical_empty_count(client, args.target_database, spec.target_table, spec.critical_columns)
        source_hash_empty = nullable_column_empty_count(client, args.target_database, spec.target_table, "source_content_sha256") if "source_content_sha256" in target_columns else None
        source_run_empty = nullable_column_empty_count(client, args.target_database, spec.target_table, "source_run_id") if "source_run_id" in target_columns else None
        latest_inserted_at = scalar_text(client, f"SELECT toString(max(inserted_at)) FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}") if "inserted_at" in target_columns else ""
        storage_policy = str(table_meta[spec.target_table].get("storage_policy") or "")
        duplicate_replacing_rows = max(0, target_rows - target_logical_rows)
        row_count_mismatch = abs(target_logical_rows - expected_rows)
        storage_status = "pass" if not expected_storage_policy or storage_policy == expected_storage_policy else "warn"

        status = "pass"
        messages = []
        if row_count_mismatch:
            status = "fail"
            messages.append(f"logical row mismatch={row_count_mismatch}")
        if critical_empty:
            status = "fail"
            messages.append(f"critical empty={critical_empty}")
        if storage_status != "pass" and status == "pass":
            status = "warn"
            messages.append(f"storage_policy={storage_policy or '<empty>'}")
        if source_hash_empty not in (None, 0) and status == "pass":
            status = "warn"
            messages.append(f"empty source_content_sha256={source_hash_empty}")

        row.update(
            {
                "status": status,
                "message": "; ".join(messages),
                "source_rows": source_rows,
                "expected_logical_rows": expected_rows,
                "target_rows": target_rows,
                "target_logical_rows": target_logical_rows,
                "row_count_mismatch": row_count_mismatch,
                "duplicate_replacing_rows": duplicate_replacing_rows,
                "critical_empty": critical_empty,
                "source_hash_empty": source_hash_empty,
                "source_run_empty": source_run_empty,
                "latest_inserted_at": latest_inserted_at,
                "storage_policy": storage_policy,
                "expected_storage_policy": expected_storage_policy,
                "storage_status": storage_status,
                "wall_seconds": round(time.perf_counter() - started, 3),
            }
        )
        rows.append(row)
        print(
            f"validated {index}/{len(specs)} {spec.target_table}: status={status} "
            f"target_final={target_logical_rows:,} expected={expected_rows:,} mismatch={row_count_mismatch:,}",
            flush=True,
        )
    return rows


def validate_operational_state(client: ClickHouseHttpClient, args: argparse.Namespace, expected_storage_policy: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    db = quote_ident(args.target_database)
    rows.extend(storage_policy_checks(client, args.target_database, expected_storage_policy))
    rows.extend(run_status_checks(client, args.target_database))
    rows.extend(
        [
            count_check(client, "sec_missing_accepted_at", "sec_filing_v2", f"SELECT countIf(isNull(accepted_at_utc)) FROM {db}.sec_filing_v2 FINAL", expected_zero=True, severity="warning", message="SEC accepted timestamp backfill should populate every migrated filing row."),
            count_check(client, "sec_filing_documents", "sec_filing_document_v1", f"SELECT count() FROM {db}.sec_filing_document_v1", expected_zero=False, pass_if_positive=True, severity="warning", message="Document extraction has not been populated yet."),
            count_check(client, "sec_filing_text", "sec_filing_text_v1", f"SELECT count() FROM {db}.sec_filing_text_v1", expected_zero=False, pass_if_positive=True, severity="warning", message="Filing text extraction has not been populated yet."),
            count_check(client, "sec_market_bridge", "id_sec_market_bridge_v1", f"SELECT count() FROM {db}.id_sec_market_bridge_v1", expected_zero=False, pass_if_positive=True, severity="warning", message="SEC CIK-to-market bridge has not been built yet."),
            count_check(client, "tradable_universe_features", "feature_tradable_universe_v1", f"SELECT count() FROM {db}.feature_tradable_universe_v1", expected_zero=False, pass_if_positive=True, severity="warning", message="Derived tradable universe features are not built yet."),
            count_check(client, "scanner_static_features", "feature_scanner_static_v1", f"SELECT count() FROM {db}.feature_scanner_static_v1", expected_zero=False, pass_if_positive=True, severity="warning", message="Derived scanner static features are not built yet."),
            sec_event_feature_check(client, args.target_database),
        ]
    )
    return rows


def storage_policy_checks(client: ClickHouseHttpClient, database: str, expected_storage_policy: str) -> list[dict[str, Any]]:
    if not expected_storage_policy:
        return []
    rows = query_json_each_row(
        client,
        f"""
        SELECT name AS target_table, storage_policy
        FROM system.tables
        WHERE database = {sql_string(database)}
          AND engine LIKE '%MergeTree%'
        ORDER BY name
        """,
    )
    return [
        {
            "check_name": "storage_policy",
            "target_table": row["target_table"],
            "status": "pass" if row.get("storage_policy") == expected_storage_policy else "warn",
            "severity": "info" if row.get("storage_policy") == expected_storage_policy else "warning",
            "observed_value": row.get("storage_policy") or "",
            "expected_value": expected_storage_policy,
            "mismatch_count": 0 if row.get("storage_policy") == expected_storage_policy else 1,
            "message": "",
        }
        for row in rows
    ]


def run_status_checks(client: ClickHouseHttpClient, database: str) -> list[dict[str, Any]]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT
            job_name,
            argMax(status, inserted_at) AS status,
            max(inserted_at) AS last_inserted_at,
            argMax(rows_failed, inserted_at) AS rows_failed
        FROM {quote_ident(database)}.source_run_v1
        WHERE startsWith(job_name, 'step_')
        GROUP BY job_name
        ORDER BY job_name
        """,
    )
    return [
        {
            "check_name": "latest_source_run_status",
            "target_table": "source_run_v1",
            "status": "pass" if row.get("status") in {"completed", "validation_only"} and int(row.get("rows_failed") or 0) == 0 else "warn",
            "severity": "info" if row.get("status") in {"completed", "validation_only"} and int(row.get("rows_failed") or 0) == 0 else "warning",
            "observed_value": f"{row.get('job_name')}:{row.get('status')}",
            "expected_value": "completed_or_validation_only",
            "mismatch_count": int(row.get("rows_failed") or 0),
            "message": f"last_inserted_at={row.get('last_inserted_at')}",
        }
        for row in rows
    ]


def count_check(
    client: ClickHouseHttpClient,
    check_name: str,
    target_table: str,
    sql: str,
    *,
    expected_zero: bool,
    severity: str,
    message: str,
    pass_if_positive: bool = False,
) -> dict[str, Any]:
    observed = scalar_int(client, sql)
    if expected_zero:
        status = "pass" if observed == 0 else "warn"
        expected = "0"
        mismatch = observed
    elif pass_if_positive:
        status = "pass" if observed > 0 else "warn"
        expected = ">0"
        mismatch = 0 if observed > 0 else 1
    else:
        status = "warn" if observed > 0 else "pass"
        expected = "tracked"
        mismatch = observed
    return {
        "check_name": check_name,
        "target_table": target_table,
        "status": status,
        "severity": "info" if status == "pass" else severity,
        "observed_value": str(observed),
        "expected_value": expected,
        "mismatch_count": mismatch,
        "message": message,
    }


def sec_event_feature_check(client: ClickHouseHttpClient, database: str) -> dict[str, Any]:
    db = quote_ident(database)
    accepted_rows = scalar_int(client, f"SELECT count() FROM {db}.sec_filing_v2 WHERE accepted_at_utc IS NOT NULL")
    event_rows = scalar_int(client, f"SELECT count() FROM {db}.feature_sec_event_market_bridge_v1")
    if accepted_rows == 0:
        return {
            "check_name": "sec_event_market_bridge_features",
            "target_table": "feature_sec_event_market_bridge_v1",
            "status": "pass",
            "severity": "info",
            "observed_value": f"{event_rows} events / {accepted_rows} accepted filings",
            "expected_value": "blocked_until_accepted_at_backfill",
            "mismatch_count": 0,
            "message": "SEC event features depend on accepted_at_utc; accepted timestamp backfill has no source data yet.",
        }
    status = "pass" if event_rows > 0 else "warn"
    return {
        "check_name": "sec_event_market_bridge_features",
        "target_table": "feature_sec_event_market_bridge_v1",
        "status": status,
        "severity": "info" if status == "pass" else "warning",
        "observed_value": f"{event_rows} events / {accepted_rows} accepted filings",
        "expected_value": ">0 events when accepted filings exist",
        "mismatch_count": 0 if status == "pass" else 1,
        "message": "" if status == "pass" else "Accepted filings exist but SEC event-to-market feature bridge is empty.",
    }


def target_table_metadata(client: ClickHouseHttpClient, database: str) -> dict[str, dict[str, Any]]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT name, engine, total_rows, total_bytes, storage_policy
        FROM system.tables
        WHERE database = {sql_string(database)}
        FORMAT JSONEachRow
        """,
    )
    return {row["name"]: row for row in rows}


def target_column_map(client: ClickHouseHttpClient, database: str) -> dict[str, set[str]]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT table, name
        FROM system.columns
        WHERE database = {sql_string(database)}
        FORMAT JSONEachRow
        """,
    )
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["table"], set()).add(row["name"])
    return result


def critical_empty_count(client: ClickHouseHttpClient, database: str, table: str, columns: tuple[str, ...]) -> int:
    checks = []
    for column in columns:
        quoted = quote_ident(column)
        checks.append(f"isNull({quoted})")
        checks.append(f"toString({quoted}) = ''")
    return scalar_int(client, f"SELECT countIf({' OR '.join(checks)}) FROM {quote_ident(database)}.{quote_ident(table)}")


def nullable_column_empty_count(client: ClickHouseHttpClient, database: str, table: str, column: str) -> int:
    quoted = quote_ident(column)
    return scalar_int(client, f"SELECT countIf(isNull({quoted}) OR toString({quoted}) = '') FROM {quote_ident(database)}.{quote_ident(table)}")


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = execute_readonly_with_retries(client, sql.strip().rstrip(";") + "\nFORMAT TSV").strip()
    return int(text.splitlines()[0].split("\t")[0]) if text else 0


def scalar_text(client: ClickHouseHttpClient, sql: str) -> str:
    return execute_readonly_with_retries(client, sql.strip().rstrip(";") + "\nFORMAT TSV").strip()


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"\s+FORMAT\s+JSONEachRow\s*;?\s*$", "", sql, flags=re.IGNORECASE).strip().rstrip(";")
    text = execute_readonly_with_retries(client, normalized + "\nFORMAT JSONEachRow")
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


def sync_validation_rows(run_id: str, table_rows: list[dict[str, Any]], operational_rows: list[dict[str, Any]], checked_at: str) -> list[dict[str, Any]]:
    rows = []
    for row in table_rows:
        target = row["target_table"]
        rows.extend(
            [
                validation_row(run_id, f"{target}:row_count", "row_count_after_step_05", target, "pass" if row.get("row_count_mismatch", 1) == 0 else "fail", "info" if row.get("row_count_mismatch", 1) == 0 else "error", row.get("expected_logical_rows"), row.get("target_logical_rows"), row.get("row_count_mismatch", 0), row, checked_at),
                validation_row(run_id, f"{target}:critical_empty", "critical_columns_not_empty_after_step_05", target, "pass" if row.get("critical_empty", 1) == 0 else "fail", "info" if row.get("critical_empty", 1) == 0 else "error", 0, row.get("critical_empty"), row.get("critical_empty", 0), row, checked_at),
                validation_row(run_id, f"{target}:storage_policy", "storage_policy_after_step_05", target, row.get("storage_status", "warn"), "info" if row.get("storage_status") == "pass" else "warning", row.get("expected_storage_policy"), row.get("storage_policy"), 0 if row.get("storage_status") == "pass" else 1, row, checked_at),
            ]
        )
    for row in operational_rows:
        rows.append(
            validation_row(
                run_id,
                f"{row['check_name']}:{row['target_table']}:{row.get('observed_value', '')}",
                row["check_name"],
                row["target_table"],
                row["status"],
                row["severity"],
                row.get("expected_value"),
                row.get("observed_value"),
                row.get("mismatch_count", 0),
                row,
                checked_at,
            )
        )
    return rows


def validation_row(
    run_id: str,
    suffix: str,
    check_name: str,
    target_table: str,
    status: str,
    severity: str,
    expected: Any,
    observed: Any,
    mismatch: Any,
    details: dict[str, Any],
    checked_at: str,
) -> dict[str, Any]:
    return {
        "validation_id": f"{run_id}:{suffix}",
        "run_id": run_id,
        "check_name": check_name,
        "target_table": target_table,
        "check_status": status,
        "severity": severity,
        "expected_value": "" if expected is None else str(expected),
        "observed_value": "" if observed is None else str(observed),
        "mismatch_count": int(mismatch or 0),
        "details_json": json.dumps(details, ensure_ascii=False, separators=(",", ":"), default=str),
        "checked_at_utc": checked_at,
    }


def insert_run_row(client: ClickHouseHttpClient, source_db: str, target_db: str, run_id: str, status: str, inserted_at: str, *, rows_read: int, rows_written: int, rows_failed: int) -> None:
    now = clickhouse_now64()
    row = {
        "run_id": run_id,
        "job_name": "step_05_validate_q_live_migration",
        "job_type": "validation",
        "source_system": "trading_dashboard_dev",
        "source_database": source_db,
        "target_database": target_db,
        "status": status,
        "started_at_utc": inserted_at,
        "finished_at_utc": now,
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


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_manifest(path: Path, args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[ValidationSpec], expected_storage_policy: str, wall_seconds: float) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "step_05_validate_q_live_migration",
        "validation_run_id": run_id,
        "execute": args.execute,
        "source_database": args.source_database,
        "target_database": args.target_database,
        "expected_storage_policy": expected_storage_policy,
        "run_root": str(paths.run_root),
        "table_validation_jsonl": str(paths.table_validation_jsonl),
        "operational_validation_jsonl": str(paths.operational_validation_jsonl),
        "summary_md": str(paths.summary_md),
        "tables": [{"step": spec.step, "name": spec.name, "target_table": spec.target_table, "source_tables": spec.source_tables} for spec in specs],
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(["CLICKHOUSE_LIVE_STORAGE_POLICY", "QLIVE_MIGRATION_CLICKHOUSE_URL", "QLIVE_MIGRATION_CLICKHOUSE_USER", "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_URL", "QMD_CLICKHOUSE_USER", "QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]),
        "wall_seconds": wall_seconds,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, run_id: str, table_rows: list[dict[str, Any]], operational_rows: list[dict[str, Any]], wall_seconds: float) -> None:
    table_status = counts_by_status(table_rows)
    op_status = counts_by_status(operational_rows)
    failed_tables = [row for row in table_rows if row.get("status") == "fail"]
    warned_tables = [row for row in table_rows if row.get("status") == "warn"]
    warned_ops = [row for row in operational_rows if row.get("status") != "pass"]
    lines = [
        "# q_live Migration Validation",
        "",
        f"- Run id: `{run_id}`",
        f"- Source database: `{args.source_database}`",
        f"- Target database: `{args.target_database}`",
        f"- Execute mode: `{args.execute}`",
        f"- Wall seconds: `{wall_seconds}`",
        "",
        "## Status Counts",
        "",
        f"- Table checks: `{json.dumps(table_status, sort_keys=True)}`",
        f"- Operational checks: `{json.dumps(op_status, sort_keys=True)}`",
        "",
        "## Failed Table Reconciliations",
        "",
    ]
    if not failed_tables:
        lines.append("None.")
    else:
        lines.extend(["| Step | Table | Message |", "| --- | --- | --- |"])
        for row in failed_tables:
            lines.append(f"| `{row['step']}` | `{row['target_table']}` | `{row.get('message', '')}` |")
    lines.extend(["", "## Warning Table Reconciliations", ""])
    if not warned_tables:
        lines.append("None.")
    else:
        lines.extend(["| Step | Table | Message |", "| --- | --- | --- |"])
        for row in warned_tables[:100]:
            lines.append(f"| `{row['step']}` | `{row['target_table']}` | `{row.get('message', '')}` |")
    lines.extend(["", "## Pending Operational Work", ""])
    if not warned_ops:
        lines.append("None.")
    else:
        lines.extend(["| Check | Table | Observed | Message |", "| --- | --- | ---: | --- |"])
        for row in warned_ops:
            lines.append(f"| `{row['check_name']}` | `{row['target_table']}` | `{row.get('observed_value', '')}` | {row.get('message', '')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def counts_by_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_database_name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[ValidationSpec], expected_storage_policy: str) -> None:
    print("=" * 96, flush=True)
    print("q_live migration step 5: validation", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"source_database={args.source_database}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"validation_run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"specs={len(specs)}", flush=True)
    print(f"expected_storage_policy={expected_storage_policy or '<not set>'}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print("secret_status=" + json.dumps(secret_status(["CLICKHOUSE_LIVE_STORAGE_POLICY", "QLIVE_MIGRATION_CLICKHOUSE_URL", "QLIVE_MIGRATION_CLICKHOUSE_USER", "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_URL", "QMD_CLICKHOUSE_USER", "QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def quiet_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(cwd), stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()

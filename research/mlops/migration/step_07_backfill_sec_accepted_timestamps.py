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


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_SOURCE_DATABASE = "sec_core"
DEFAULT_SOURCE_TABLE = "sec_filing_v1"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_07_sec_accepted_timestamps")


@dataclass(frozen=True, slots=True)
class StepPaths:
    run_root: Path
    manifest_json: Path
    execution_jsonl: Path
    validation_jsonl: Path
    rendered_sql: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "StepPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "step_07_manifest.json",
            execution_jsonl=run_root / "step_07_execution.jsonl",
            validation_jsonl=run_root / "step_07_validation.jsonl",
            rendered_sql=run_root / "step_07_insert_select.sql",
            summary_md=run_root / "step_07_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 7 of q_live migration: backfill SEC accepted timestamps for existing "
            "q_live.sec_filing_v2 rows only. The script inserts newer ReplacingMergeTree "
            "versions for matched q_live keys and never introduces new logical filing keys."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument(
        "--source-database",
        default=os.environ.get("QLIVE_MIGRATION_SEC_ACCEPTED_SOURCE_DATABASE") or os.environ.get("SEC_CLICKHOUSE_DATABASE") or os.environ.get("SEC_ACCEPTED_SOURCE_DATABASE") or DEFAULT_SOURCE_DATABASE,
    )
    parser.add_argument(
        "--source-table",
        default=os.environ.get("QLIVE_MIGRATION_SEC_ACCEPTED_SOURCE_TABLE") or os.environ.get("SEC_ACCEPTED_SOURCE_TABLE") or DEFAULT_SOURCE_TABLE,
    )
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_07_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--execute", action="store_true", help="Insert replacement versions for matched existing q_live filing rows.")
    parser.add_argument("--validate-only", action="store_true", help="Validate current target/source coverage without inserting replacements.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_database_name(args.target_database, "--target-database")
    validate_database_name(args.source_database, "--source-database")
    validate_database_name(args.source_table, "--source-table")

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backfill_run_id = f"step_07_sec_accepted_timestamps_{run_id}"
    inserted_at = clickhouse_now64()
    paths = StepPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env, backfill_run_id)
    readiness = check_readiness(client, args)
    write_manifest(paths.manifest_json, args, paths, loaded_env, backfill_run_id, readiness)

    if readiness["status"] != "ready":
        validations = [blocked_validation(readiness)]
        write_jsonl(paths.validation_jsonl, validations)
        write_summary(paths.summary_md, args, backfill_run_id, readiness, validations, execute=False)
        write_jsonl(paths.execution_jsonl, [{"status": "blocked", "backfill_run_id": backfill_run_id, "readiness": readiness}])
        print("blocked=" + json.dumps(readiness, sort_keys=True), flush=True)
        print(f"summary_md={paths.summary_md}", flush=True)
        if args.execute:
            raise SystemExit("Cannot execute Step 7 because the accepted timestamp source is not ready.")
        return

    source_columns = set(readiness["source_columns"])
    rendered_sql = replacement_insert_sql(args, backfill_run_id, inserted_at, source_columns)
    paths.rendered_sql.write_text(rendered_sql.strip() + "\n", encoding="utf-8")
    preflight = preflight_counts(client, args, source_columns)
    write_jsonl(paths.execution_jsonl, [{"status": "preflight", "backfill_run_id": backfill_run_id, "preflight": preflight}])

    if args.validate_only or not args.execute:
        validations = validate_after(client, args, before=preflight, after=preflight, execute=False)
        write_jsonl(paths.validation_jsonl, validations)
        write_summary(paths.summary_md, args, backfill_run_id, readiness | preflight, validations, execute=False)
        print("dry_run=true; no replacement rows inserted", flush=True)
        print(f"candidate_existing_rows={preflight['candidate_existing_rows']:,}", flush=True)
        print(f"summary_md={paths.summary_md}", flush=True)
        return

    insert_run_row(client, args.target_database, backfill_run_id, "running", inserted_at, rows_read=preflight["candidate_existing_rows"], rows_written=0, rows_failed=0)
    started = time.perf_counter()
    client.execute(rendered_sql)
    after = preflight_counts(client, args, source_columns)
    inserted_delta = max(0, after["target_physical_rows"] - preflight["target_physical_rows"])
    execution = {
        "status": "ok",
        "backfill_run_id": backfill_run_id,
        "inserted_delta": inserted_delta,
        "preflight": preflight,
        "after": after,
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    write_jsonl(paths.execution_jsonl, [execution])
    validations = validate_after(client, args, before=preflight, after=after, execute=True)
    write_jsonl(paths.validation_jsonl, validations)
    sync_rows = sync_validation_rows(backfill_run_id, validations, inserted_at)
    insert_json_each_row(client, args.target_database, "sync_validation_v1", sync_rows)
    insert_run_row(
        client,
        args.target_database,
        backfill_run_id,
        "completed",
        inserted_at,
        rows_read=preflight["candidate_existing_rows"],
        rows_written=inserted_delta,
        rows_failed=sum(1 for row in validations if row["status"] == "fail"),
    )
    write_summary(paths.summary_md, args, backfill_run_id, readiness | after | {"inserted_delta": inserted_delta}, validations, execute=True)
    print("summary=" + json.dumps({"backfill_run_id": backfill_run_id, "inserted_delta": inserted_delta, "validations": validations}, sort_keys=True), flush=True)
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


def check_readiness(client: ClickHouseHttpClient, args: argparse.Namespace) -> dict[str, Any]:
    target_exists = table_exists(client, args.target_database, "sec_filing_v2")
    source_exists = table_exists(client, args.source_database, args.source_table)
    if not target_exists:
        return {"status": "blocked", "reason": "missing_target_table", "target_table": f"{args.target_database}.sec_filing_v2"}
    if not source_exists:
        return {"status": "blocked", "reason": "missing_source_table", "source_table": f"{args.source_database}.{args.source_table}"}

    source_columns = column_names(client, args.source_database, args.source_table)
    required = {"accession_number", "cik", "accepted_at_utc", "acceptance_datetime_raw", "accepted_at_source"}
    missing_columns = sorted(required - source_columns)
    if missing_columns:
        return {"status": "blocked", "reason": "missing_source_columns", "source_table": f"{args.source_database}.{args.source_table}", "missing_columns": missing_columns}
    return {
        "status": "ready",
        "source_table": f"{args.source_database}.{args.source_table}",
        "target_table": f"{args.target_database}.sec_filing_v2",
        "source_columns": sorted(source_columns),
    }


def preflight_counts(client: ClickHouseHttpClient, args: argparse.Namespace, source_columns: set[str]) -> dict[str, int]:
    target = f"{quote_ident(args.target_database)}.sec_filing_v2"
    source_projection = source_projection_sql(args, source_columns)
    return {
        "target_logical_rows": scalar_int(client, f"SELECT count() FROM {target} FINAL"),
        "target_physical_rows": scalar_int(client, f"SELECT count() FROM {target}"),
        "target_missing_accepted_rows": scalar_int(client, f"SELECT count() FROM {target} FINAL WHERE accepted_at_utc IS NULL"),
        "target_has_accepted_rows": scalar_int(client, f"SELECT count() FROM {target} FINAL WHERE accepted_at_utc IS NOT NULL"),
        "source_accepted_rows": scalar_int(client, f"SELECT count() FROM ({source_projection}) AS s"),
        "candidate_existing_rows": scalar_int(
            client,
            f"""
            SELECT count()
            FROM (SELECT * FROM {target} FINAL) AS q
            INNER JOIN ({source_projection}) AS s
                ON q.cik = s.cik
               AND q.accession_number = s.accession_number
            WHERE q.accepted_at_utc IS NULL
            """,
        ),
        "matched_existing_rows": scalar_int(
            client,
            f"""
            SELECT count()
            FROM (SELECT * FROM {target} FINAL) AS q
            INNER JOIN ({source_projection}) AS s
                ON q.cik = s.cik
               AND q.accession_number = s.accession_number
            """,
        ),
    }


def replacement_insert_sql(args: argparse.Namespace, run_id: str, inserted_at: str, source_columns: set[str]) -> str:
    target = f"{quote_ident(args.target_database)}.sec_filing_v2"
    source_projection = source_projection_sql(args, source_columns)
    literal_run_id = sql_string(run_id)
    inserted_expr = f"toDateTime64({sql_string(inserted_at)}, 3, 'UTC')"
    return f"""
INSERT INTO {target}
(filing_id, accession_number, accession_number_compact, cik, issuer_id, company_name, form_type, filing_date, report_date, accepted_at_utc, acceptance_datetime_raw, accepted_at_source, primary_document, primary_document_url, filing_detail_url, source_file_name, filing_size, items, text_status, source_run_id, source_content_sha256, inserted_at)
SELECT
    q.filing_id,
    q.accession_number,
    q.accession_number_compact,
    q.cik,
    q.issuer_id,
    coalesce(nullIf(q.company_name, ''), s.company_name) AS company_name,
    q.form_type,
    q.filing_date,
    q.report_date,
    s.accepted_at_utc,
    s.acceptance_datetime_raw,
    if(s.accepted_at_source = '' OR s.accepted_at_source = 'missing', 'submissions_bulk', s.accepted_at_source) AS accepted_at_source,
    coalesce(q.primary_document, s.primary_document) AS primary_document,
    coalesce(q.primary_document_url, s.primary_document_url) AS primary_document_url,
    coalesce(q.filing_detail_url, s.filing_detail_url) AS filing_detail_url,
    q.source_file_name,
    coalesce(q.filing_size, s.filing_size) AS filing_size,
    coalesce(q.items, s.items) AS items,
    q.text_status,
    {literal_run_id} AS source_run_id,
    lower(hex(MD5(concat(q.source_content_sha256, ':accepted-at:', q.cik, ':', q.accession_number, ':', toString(s.accepted_at_utc), ':', ifNull(s.acceptance_datetime_raw, ''))))) AS source_content_sha256,
    {inserted_expr} AS inserted_at
FROM (SELECT * FROM {target} FINAL) AS q
INNER JOIN ({source_projection}) AS s
    ON q.cik = s.cik
   AND q.accession_number = s.accession_number
WHERE q.accepted_at_utc IS NULL
"""


def source_projection_sql(args: argparse.Namespace, source_columns: set[str]) -> str:
    source = f"{quote_ident(args.source_database)}.{quote_ident(args.source_table)}"

    def optional_string(column: str) -> str:
        if column in source_columns:
            return f"CAST(nullIf({quote_ident(column)}, ''), 'Nullable(String)')"
        return "CAST(NULL, 'Nullable(String)')"

    def optional_uint64(column: str) -> str:
        if column in source_columns:
            return f"CAST({quote_ident(column)}, 'Nullable(UInt64)')"
        return "CAST(NULL, 'Nullable(UInt64)')"

    return f"""
SELECT
    cik,
    accession_number,
    accepted_at_utc,
    acceptance_datetime_raw,
    accepted_at_source,
    {optional_string("company_name")} AS company_name,
    {optional_string("primary_document")} AS primary_document,
    {optional_string("primary_document_url")} AS primary_document_url,
    {optional_string("filing_detail_url")} AS filing_detail_url,
    {optional_uint64("filing_size")} AS filing_size,
    {optional_string("items")} AS items
FROM {source} FINAL
WHERE accepted_at_utc IS NOT NULL
"""


def validate_after(client: ClickHouseHttpClient, args: argparse.Namespace, *, before: dict[str, int], after: dict[str, int], execute: bool) -> list[dict[str, Any]]:
    validations = []
    logical_changed = after["target_logical_rows"] - before["target_logical_rows"]
    expected_filled = before["candidate_existing_rows"] if execute else 0
    observed_filled = after["target_has_accepted_rows"] - before["target_has_accepted_rows"]
    validations.append(
        {
            "name": "logical_row_count_unchanged",
            "status": "pass" if logical_changed == 0 else "fail",
            "expected": 0,
            "observed": logical_changed,
            "mismatch_count": abs(logical_changed),
            "message": "Backfill must not introduce new logical q_live filing keys.",
        }
    )
    validations.append(
        {
            "name": "accepted_timestamp_fill_count",
            "status": "pass" if observed_filled == expected_filled else "warn",
            "expected": expected_filled,
            "observed": observed_filled,
            "mismatch_count": abs(observed_filled - expected_filled),
            "message": "Number of q_live rows whose accepted_at_utc became populated.",
        }
    )
    validations.append(
        {
            "name": "remaining_missing_accepted_at",
            "status": "pass" if after["target_missing_accepted_rows"] == 0 else "warn",
            "expected": 0,
            "observed": after["target_missing_accepted_rows"],
            "mismatch_count": after["target_missing_accepted_rows"],
            "message": "Rows still missing accepted_at_utc need another source such as daily feed hdr_sgml or submissions API.",
        }
    )
    return validations


def blocked_validation(readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "accepted_timestamp_source_ready",
        "status": "blocked",
        "expected": "ready",
        "observed": readiness.get("reason", "unknown"),
        "mismatch_count": 1,
        "message": json.dumps(readiness, sort_keys=True),
    }


def sync_validation_rows(run_id: str, validations: list[dict[str, Any]], checked_at: str) -> list[dict[str, Any]]:
    rows = []
    for row in validations:
        check_status = "fail" if row["status"] == "fail" else ("warn" if row["status"] in {"warn", "blocked"} else "pass")
        rows.append(
            {
                "validation_id": f"{run_id}:{row['name']}",
                "run_id": run_id,
                "check_name": row["name"],
                "target_table": "sec_filing_v2",
                "check_status": check_status,
                "severity": "error" if check_status == "fail" else ("warning" if check_status == "warn" else "info"),
                "expected_value": str(row.get("expected", "")),
                "observed_value": str(row.get("observed", "")),
                "mismatch_count": int(row.get("mismatch_count") or 0),
                "details_json": json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str),
                "checked_at_utc": checked_at,
            }
        )
    return rows


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    return scalar_int(client, f"SELECT count() FROM system.tables WHERE database = {sql_string(database)} AND name = {sql_string(table)}") == 1


def column_names(client: ClickHouseHttpClient, database: str, table: str) -> set[str]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT name
        FROM system.columns
        WHERE database = {sql_string(database)}
          AND table = {sql_string(table)}
        FORMAT JSONEachRow
        """,
    )
    return {row["name"] for row in rows}


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = execute_readonly_with_retries(client, sql.strip().rstrip(";") + "\nFORMAT TSV").strip()
    return int(text.splitlines()[0].split("\t")[0]) if text else 0


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


def insert_run_row(client: ClickHouseHttpClient, target_db: str, run_id: str, status: str, inserted_at: str, *, rows_read: int, rows_written: int, rows_failed: int) -> None:
    now = clickhouse_now64()
    row = {
        "run_id": run_id,
        "job_name": "step_07_backfill_sec_accepted_timestamps",
        "job_type": "migration",
        "source_system": "sec_edgar",
        "source_database": None,
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


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_manifest(path: Path, args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, readiness: dict[str, Any]) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "step_07_backfill_sec_accepted_timestamps",
        "backfill_run_id": run_id,
        "dry_run": not args.execute,
        "target_database": args.target_database,
        "source_database": args.source_database,
        "source_table": args.source_table,
        "run_root": str(paths.run_root),
        "rendered_sql": str(paths.rendered_sql),
        "execution_jsonl": str(paths.execution_jsonl),
        "validation_jsonl": str(paths.validation_jsonl),
        "summary_md": str(paths.summary_md),
        "readiness": readiness,
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": migration_secret_status(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, run_id: str, state: dict[str, Any], validations: list[dict[str, Any]], *, execute: bool) -> None:
    lines = [
        "# q_live SEC Accepted Timestamp Backfill",
        "",
        f"- Run id: `{run_id}`",
        f"- Execute mode: `{execute}`",
        f"- Source table: `{args.source_database}.{args.source_table}`",
        f"- Target table: `{args.target_database}.sec_filing_v2`",
        f"- Readiness/status: `{state.get('status', 'ready')}`",
        "",
        "## Counts",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key in sorted(k for k in state if isinstance(state[k], int)):
        lines.append(f"| `{key}` | {state[key]:,} |")
    lines.extend(["", "## Validations", "", "| Name | Status | Expected | Observed | Message |", "| --- | --- | ---: | ---: | --- |"])
    for row in validations:
        lines.append(f"| `{row['name']}` | `{row['status']}` | `{row.get('expected', '')}` | `{row.get('observed', '')}` | {row.get('message', '')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_database_name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str) -> None:
    print("=" * 96, flush=True)
    print("q_live migration step 7: SEC accepted timestamp backfill", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"validate_only={args.validate_only}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"source_table={args.source_database}.{args.source_table}", flush=True)
    print(f"backfill_run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print("secret_status=" + json.dumps(migration_secret_status(), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def migration_secret_status() -> dict[str, str]:
    return secret_status(
        [
            "QLIVE_MIGRATION_SEC_ACCEPTED_SOURCE_DATABASE",
            "QLIVE_MIGRATION_SEC_ACCEPTED_SOURCE_TABLE",
            "SEC_CLICKHOUSE_DATABASE",
            "SEC_ACCEPTED_SOURCE_DATABASE",
            "SEC_ACCEPTED_SOURCE_TABLE",
            "QLIVE_MIGRATION_CLICKHOUSE_URL",
            "QLIVE_MIGRATION_CLICKHOUSE_USER",
            "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
            "QMD_CLICKHOUSE_URL",
            "QMD_CLICKHOUSE_USER",
            "QMD_CLICKHOUSE_PASSWORD",
            "REAL_LIVE_CLICKHOUSE_WRITE_URL",
            "REAL_LIVE_CLICKHOUSE_WRITE_USER",
            "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
        ]
    )


def quiet_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(cwd), stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()

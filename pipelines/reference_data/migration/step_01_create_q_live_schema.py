from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.paths import machine_name  # noqa: E402


DEFAULT_SCHEMA_PATH = Path(__file__).with_name("q_live_target_schema.sql")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/schema_create")
STORAGE_POLICY_PLACEHOLDER = "{{CLICKHOUSE_LIVE_STORAGE_POLICY}}"
DEFAULT_TARGET_DATABASE = "q_live"


@dataclass(frozen=True, slots=True)
class SchemaCreatePaths:
    run_root: Path
    rendered_sql: Path
    manifest_json: Path
    execution_jsonl: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "SchemaCreatePaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            rendered_sql=run_root / "rendered_q_live_schema.sql",
            manifest_json=run_root / "schema_create_manifest.json",
            execution_jsonl=run_root / "schema_create_execution.jsonl",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render and optionally execute the q_live target schema. "
            "Default mode is dry-run and writes the final SQL without touching ClickHouse."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument(
        "--storage-policy",
        default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", os.environ.get("QLIVE_MIGRATION_STORAGE_POLICY", "")),
        help="Storage policy to substitute into q_live_target_schema.sql.",
    )
    parser.add_argument("--schema-path", default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_SCHEMA_CREATE_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--execute", action="store_true", help="Execute rendered DDL. Without this flag, the script is dry-run only.")
    parser.add_argument("--allow-empty-storage-policy", action="store_true", help="Permit removing the storage_policy setting if no policy is configured.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = SchemaCreatePaths.create(Path(args.output_root_win), run_id)

    raw_sql = Path(args.schema_path).read_text(encoding="utf-8")
    rendered_sql = render_schema_sql(raw_sql, args.target_database, args.storage_policy, args.allow_empty_storage_policy)
    statements = split_sql_statements(rendered_sql)
    validate_rendered_sql(rendered_sql, statements, args)

    paths.rendered_sql.write_text(rendered_sql, encoding="utf-8")
    write_manifest(paths.manifest_json, args, paths, loaded_env, statements, dry_run=not args.execute)
    print_header(args, paths, loaded_env, statements)

    if not args.execute:
        write_execution_row(
            paths.execution_jsonl,
            {
                "status": "dry_run",
                "statement_count": len(statements),
                "rendered_sql": str(paths.rendered_sql),
                "created_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        print("dry_run=true; no ClickHouse statements executed", flush=True)
        print(f"rendered_sql={paths.rendered_sql}", flush=True)
        return

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    execute_statements(client, statements, paths.execution_jsonl)
    print(f"executed_statements={len(statements)}", flush=True)
    print(f"execution_log={paths.execution_jsonl}", flush=True)


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


def validate_args(args: argparse.Namespace) -> None:
    schema_path = Path(args.schema_path)
    if not schema_path.exists():
        raise SystemExit(f"--schema-path does not exist: {schema_path}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.target_database):
        raise SystemExit(f"--target-database must be a simple ClickHouse identifier: {args.target_database!r}")
    if not args.storage_policy.strip() and not args.allow_empty_storage_policy:
        raise SystemExit("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required, or pass --allow-empty-storage-policy.")


def render_schema_sql(raw_sql: str, target_database: str, storage_policy: str, allow_empty_storage_policy: bool) -> str:
    rendered = raw_sql
    if target_database != DEFAULT_TARGET_DATABASE:
        rendered = re.sub(r"\bq_live\.", f"{target_database}.", rendered)
        rendered = re.sub(r"\bCREATE DATABASE IF NOT EXISTS q_live\b", f"CREATE DATABASE IF NOT EXISTS {target_database}", rendered)

    storage_policy = storage_policy.strip()
    if storage_policy:
        rendered = rendered.replace(STORAGE_POLICY_PLACEHOLDER, storage_policy)
    elif allow_empty_storage_policy:
        rendered = remove_storage_policy_setting(rendered)
    return rendered


def remove_storage_policy_setting(sql: str) -> str:
    sql = sql.replace(", storage_policy = '" + STORAGE_POLICY_PLACEHOLDER + "'", "")
    sql = sql.replace("storage_policy = '" + STORAGE_POLICY_PLACEHOLDER + "', ", "")
    sql = sql.replace("storage_policy = '" + STORAGE_POLICY_PLACEHOLDER + "'", "")
    return sql


def split_sql_statements(sql: str) -> list[str]:
    statements = []
    current: list[str] = []
    in_single_quote = False
    index = 0
    while index < len(sql):
        char = sql[index]
        current.append(char)
        if char == "'" and (index == 0 or sql[index - 1] != "\\"):
            in_single_quote = not in_single_quote
        if char == ";" and not in_single_quote:
            statement = "".join(current).strip()
            if statement and not statement.startswith("--"):
                statements.append(statement)
            elif statement:
                stripped = strip_sql_comments(statement).strip()
                if stripped:
                    statements.append(stripped)
            current = []
        index += 1
    tail = "".join(current).strip()
    if tail:
        stripped = strip_sql_comments(tail).strip()
        if stripped:
            statements.append(stripped)
    return [strip_sql_comments(statement).strip().rstrip(";") for statement in statements if strip_sql_comments(statement).strip()]


def strip_sql_comments(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines)


def validate_rendered_sql(rendered_sql: str, statements: list[str], args: argparse.Namespace) -> None:
    if STORAGE_POLICY_PLACEHOLDER in rendered_sql:
        raise SystemExit(f"Rendered SQL still contains {STORAGE_POLICY_PLACEHOLDER}.")
    if not statements:
        raise SystemExit("Rendered SQL produced zero statements.")
    create_tables = sum(1 for statement in statements if statement.upper().startswith("CREATE TABLE"))
    if create_tables < 1:
        raise SystemExit("Rendered SQL produced no CREATE TABLE statements.")
    if args.storage_policy.strip():
        missing_policy = [
            statement.splitlines()[0]
            for statement in statements
            if statement.upper().startswith("CREATE TABLE") and "storage_policy" not in statement
        ]
        if missing_policy:
            raise SystemExit("CREATE TABLE statements missing storage_policy: " + json.dumps(missing_policy))
    nullable_sorting_errors = find_nullable_sorting_key_errors(rendered_sql)
    if nullable_sorting_errors:
        raise SystemExit("Nullable columns used directly in ORDER BY: " + json.dumps(nullable_sorting_errors, indent=2))


def find_nullable_sorting_key_errors(sql: str) -> list[dict[str, Any]]:
    errors = []
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+([^\s(]+)(.*?);", sql, flags=re.S | re.I):
        table_name = match.group(1)
        block = match.group(2)
        nullable_columns = set(re.findall(r"^\s*(\w+)\s+Nullable\(", block, flags=re.M))
        order_match = re.search(r"ORDER BY\s+(.+?)\nSETTINGS", block, flags=re.S | re.I)
        if not order_match:
            continue
        order_expression = order_match.group(1).strip()
        for column in sorted(nullable_columns):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(column)}(?![A-Za-z0-9_])", order_expression) and f"ifNull({column}" not in order_expression:
                errors.append({"table": table_name, "column": column, "order_by": order_expression})
    return errors


def execute_statements(client: ClickHouseHttpClient, statements: list[str], log_path: Path) -> None:
    for index, statement in enumerate(statements, start=1):
        started = datetime.now(UTC)
        row: dict[str, Any] = {
            "index": index,
            "statement_preview": compact_statement_preview(statement),
            "started_at_utc": started.isoformat(),
        }
        try:
            client.execute(statement)
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            write_execution_row(log_path, row)
            raise
        row.update({"status": "ok", "finished_at_utc": datetime.now(UTC).isoformat()})
        write_execution_row(log_path, row)
        print(f"executed {index}/{len(statements)} {row['statement_preview']}", flush=True)


def compact_statement_preview(statement: str) -> str:
    text = " ".join(statement.split())
    return text[:220]


def write_execution_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_manifest(path: Path, args: argparse.Namespace, paths: SchemaCreatePaths, loaded_env: list[Path], statements: list[str], dry_run: bool) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "q_live_schema_create",
        "dry_run": dry_run,
        "target_database": args.target_database,
        "schema_path": str(Path(args.schema_path)),
        "rendered_sql": str(paths.rendered_sql),
        "execution_jsonl": str(paths.execution_jsonl),
        "statement_count": len(statements),
        "create_table_count": sum(1 for statement in statements if statement.upper().startswith("CREATE TABLE")),
        "storage_policy_status": "present" if args.storage_policy.strip() else "empty_allowed",
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(
            [
                "QLIVE_MIGRATION_CLICKHOUSE_URL",
                "QLIVE_MIGRATION_CLICKHOUSE_USER",
                "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                "QMD_CLICKHOUSE_URL",
                "QMD_CLICKHOUSE_USER",
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_LIVE_STORAGE_POLICY",
            ]
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def print_header(args: argparse.Namespace, paths: SchemaCreatePaths, loaded_env: list[Path], statements: list[str]) -> None:
    print("=" * 96, flush=True)
    print("q_live migration step 1: schema create", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"schema_path={args.schema_path}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"statement_count={len(statements)}", flush=True)
    print(f"create_table_count={sum(1 for statement in statements if statement.upper().startswith('CREATE TABLE'))}", flush=True)
    print("storage_policy=" + ("present" if args.storage_policy.strip() else "empty_allowed"), flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print(
        "secret_status="
        + json.dumps(
            secret_status(
                [
                    "QLIVE_MIGRATION_CLICKHOUSE_URL",
                    "QLIVE_MIGRATION_CLICKHOUSE_USER",
                    "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                    "QMD_CLICKHOUSE_URL",
                    "QMD_CLICKHOUSE_USER",
                    "QMD_CLICKHOUSE_PASSWORD",
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                    "CLICKHOUSE_LIVE_STORAGE_POLICY",
                ]
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    print("=" * 96, flush=True)


def quiet_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()

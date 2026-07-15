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

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_SCHEMA_PATH = Path(__file__).with_name("sec_text_v3_schema.sql")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_text_v3_schema")
DEFAULT_TARGET_DATABASE = "q_live"
STORAGE_POLICY_PLACEHOLDER = "{{CLICKHOUSE_LIVE_STORAGE_POLICY}}"


@dataclass(frozen=True, slots=True)
class SchemaPaths:
    run_root: Path
    rendered_sql: Path
    manifest_json: Path
    execution_jsonl: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "SchemaPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            rendered_sql=run_root / "rendered_sec_text_v3_schema.sql",
            manifest_json=run_root / "sec_text_v3_schema_manifest.json",
            execution_jsonl=run_root / "sec_text_v3_schema_execution.jsonl",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render and optionally execute q_live SEC archive-derived document/source-text/rendered-text v3 table DDL."
    )
    parser.add_argument("--clickhouse-url", default=default_sec_clickhouse_url())
    parser.add_argument("--user", default=default_sec_clickhouse_user())
    parser.add_argument("--password", default=default_sec_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("SEC_TEXT_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--schema-path", default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_TEXT_V3_SCHEMA_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument(
        "--storage-policy",
        default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", os.environ.get("SEC_CLICKHOUSE_STORAGE_POLICY", "")),
        help="Storage policy for SEC text v3 tables. Defaults to CLICKHOUSE_LIVE_STORAGE_POLICY.",
    )
    parser.add_argument("--execute", action="store_true", help="Execute rendered DDL. Default writes a dry-run manifest only.")
    parser.add_argument("--allow-empty-storage-policy", action="store_true", help="Permit omitting storage_policy if none is configured.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = SchemaPaths.create(Path(args.output_root_win), run_id)
    raw_sql = Path(args.schema_path).read_text(encoding="utf-8")
    rendered_sql = render_schema(raw_sql, args.target_database, args.storage_policy, args.allow_empty_storage_policy)
    statements = split_sql_statements(rendered_sql)
    validate_rendered_sql(rendered_sql, statements, args)

    paths.rendered_sql.write_text(rendered_sql, encoding="utf-8")
    write_manifest(paths.manifest_json, args, paths, loaded_env, statements, dry_run=not args.execute)
    print_header(args, paths, loaded_env, statements)

    if not args.execute:
        append_jsonl(
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
    for index, statement in enumerate(statements, start=1):
        started = datetime.now(UTC).isoformat()
        row: dict[str, Any] = {
            "index": index,
            "statement": first_statement_line(statement),
            "started_at_utc": started,
            "status": "pending",
        }
        try:
            client.execute(statement)
            row["status"] = "ok"
            print(f"executed {index}/{len(statements)} {row['statement']}", flush=True)
        except Exception as exc:  # noqa: BLE001
            row["status"] = "error"
            row["error"] = repr(exc)
            append_jsonl(paths.execution_jsonl, row)
            raise
        append_jsonl(paths.execution_jsonl, row)
    print(f"executed_statements={len(statements)}", flush=True)
    print(f"execution_log={paths.execution_jsonl}", flush=True)


def default_sec_clickhouse_url() -> str:
    return os.environ.get("SEC_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_sec_clickhouse_user() -> str:
    return os.environ.get("SEC_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_sec_clickhouse_password() -> str:
    return (
        os.environ.get("SEC_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


def validate_args(args: argparse.Namespace) -> None:
    if not Path(args.schema_path).exists():
        raise SystemExit(f"--schema-path does not exist: {args.schema_path}")
    validate_identifier(args.target_database, "--target-database")
    if not args.storage_policy.strip() and not args.allow_empty_storage_policy:
        raise SystemExit("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required, or pass --allow-empty-storage-policy.")


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def render_schema(raw_sql: str, target_database: str, storage_policy: str, allow_empty_storage_policy: bool) -> str:
    rendered = raw_sql
    if target_database != DEFAULT_TARGET_DATABASE:
        rendered = re.sub(r"\bq_live\.", f"{target_database}.", rendered)
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
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    index = 0
    while index < len(sql):
        char = sql[index]
        current.append(char)
        if char == "'" and (index == 0 or sql[index - 1] != "\\"):
            in_single_quote = not in_single_quote
        if char == ";" and not in_single_quote:
            statement = strip_sql_comments("".join(current)).strip().rstrip(";")
            if statement:
                statements.append(statement)
            current = []
        index += 1
    tail = strip_sql_comments("".join(current)).strip().rstrip(";")
    if tail:
        statements.append(tail)
    return statements


def strip_sql_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def validate_rendered_sql(rendered_sql: str, statements: list[str], args: argparse.Namespace) -> None:
    if STORAGE_POLICY_PLACEHOLDER in rendered_sql:
        raise SystemExit(f"Rendered SQL still contains {STORAGE_POLICY_PLACEHOLDER}.")
    create_statements = [statement for statement in statements if statement.upper().startswith("CREATE TABLE IF NOT EXISTS")]
    alter_statements = [statement for statement in statements if statement.upper().startswith("ALTER TABLE")]
    if len(create_statements) != 5 or len(alter_statements) != 22 or len(statements) != 27:
        raise SystemExit(
            f"Expected 5 CREATE TABLE and 22 ALTER TABLE statements, got "
            f"create={len(create_statements)} alter={len(alter_statements)} total={len(statements)}."
        )
    for statement in statements:
        if not statement.upper().startswith(("CREATE TABLE IF NOT EXISTS", "ALTER TABLE")):
            raise SystemExit("Only CREATE TABLE IF NOT EXISTS and ALTER TABLE statements are allowed in this schema script.")
        if statement.upper().startswith("CREATE TABLE") and args.storage_policy.strip() and "storage_policy" not in statement:
            raise SystemExit(f"CREATE TABLE statement missing storage_policy: {first_statement_line(statement)}")
        nullable_order = nullable_columns_in_order_by(statement)
        if nullable_order:
            raise SystemExit(f"Nullable columns used in ORDER BY: {nullable_order}")


def nullable_columns_in_order_by(statement: str) -> list[str]:
    nullable_columns = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+Nullable\(", statement, flags=re.M))
    match = re.search(r"ORDER BY\s*\((.*?)\)", statement, flags=re.S | re.I)
    if not match:
        return []
    order_expr = match.group(1)
    return sorted(column for column in nullable_columns if re.search(rf"\b{re.escape(column)}\b", order_expr))


def first_statement_line(statement: str) -> str:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return ""


def write_manifest(path: Path, args: argparse.Namespace, paths: SchemaPaths, loaded_env: list[Path], statements: list[str], dry_run: bool) -> None:
    payload = {
        "run_root": str(paths.run_root),
        "rendered_sql": str(paths.rendered_sql),
        "statement_count": len(statements),
        "target_database": args.target_database,
        "storage_policy_present": bool(args.storage_policy.strip()),
        "dry_run": dry_run,
        "git_commit": git_commit(),
        "loaded_env_files": [str(item) for item in loaded_env],
        "secret_status": secret_status(
            [
                "SEC_CLICKHOUSE_URL",
                "SEC_CLICKHOUSE_USER",
                "SEC_CLICKHOUSE_PASSWORD",
                "QMD_CLICKHOUSE_URL",
                "QMD_CLICKHOUSE_USER",
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_LIVE_STORAGE_POLICY",
            ]
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_header(args: argparse.Namespace, paths: SchemaPaths, loaded_env: list[Path], statements: list[str]) -> None:
    print("=" * 96, flush=True)
    print("SEC text v3 schema", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"schema_path={args.schema_path}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"statement_count={len(statements)}", flush=True)
    print(f"storage_policy={'present' if args.storage_policy.strip() else 'empty'}", flush=True)
    print("loaded_env_files=" + json.dumps([str(item) for item in loaded_env]), flush=True)
    print("=" * 96, flush=True)


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


if __name__ == "__main__":
    main()

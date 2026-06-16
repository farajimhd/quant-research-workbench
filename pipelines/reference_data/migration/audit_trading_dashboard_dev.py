from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
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
from research.mlops.manifest import git_commit  # noqa: E402
from research.mlops.paths import machine_name  # noqa: E402


DEFAULT_SOURCE_DATABASE = "trading_dashboard_dev"
DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/schema_audit")
ID_RE = re.compile(r"(^id$|_id$|_ids$|^id_|_key$|_code$|accession_number$|ticker$|symbol$|cik$|conid$)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AuditPaths:
    run_root: Path
    tables_jsonl: Path
    columns_jsonl: Path
    parts_jsonl: Path
    table_profiles_jsonl: Path
    inferred_relations_jsonl: Path
    create_sql: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "AuditPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            tables_jsonl=run_root / "tables.jsonl",
            columns_jsonl=run_root / "columns.jsonl",
            parts_jsonl=run_root / "parts.jsonl",
            table_profiles_jsonl=run_root / "table_profiles.jsonl",
            inferred_relations_jsonl=run_root / "inferred_relations.jsonl",
            create_sql=run_root / "create_statements.sql",
            manifest_json=run_root / "audit_manifest.json",
            summary_md=run_root / "schema_audit_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of the existing trading_dashboard_dev ClickHouse schema. "
            "The outputs are the inputs for designing a corrected q_live publication schema."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_audit_clickhouse_url())
    parser.add_argument("--user", default=default_audit_clickhouse_user())
    parser.add_argument("--password", default=default_audit_clickhouse_password())
    parser.add_argument("--source-database", default=os.environ.get("QLIVE_MIGRATION_SOURCE_DATABASE", DEFAULT_SOURCE_DATABASE))
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--sample-rows", type=int, default=int(os.environ.get("QLIVE_MIGRATION_SAMPLE_ROWS", "3")))
    parser.add_argument(
        "--profile-mode",
        choices=["metadata", "light"],
        default=os.environ.get("QLIVE_MIGRATION_PROFILE_MODE", "metadata"),
        help="metadata avoids table scans. light samples rows and profiles only likely key columns.",
    )
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = AuditPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env)
    tables = fetch_tables(client, args.source_database)
    columns = fetch_columns(client, args.source_database)
    parts = fetch_parts(client, args.source_database)
    profiles = build_table_profiles(client, args.source_database, tables, columns, args.profile_mode, args.sample_rows)
    relations = infer_relations(tables, columns)

    write_jsonl(paths.tables_jsonl, tables)
    write_jsonl(paths.columns_jsonl, columns)
    write_jsonl(paths.parts_jsonl, parts)
    write_jsonl(paths.table_profiles_jsonl, profiles)
    write_jsonl(paths.inferred_relations_jsonl, relations)
    write_create_sql(paths.create_sql, tables)
    write_manifest(paths.manifest_json, args, paths, loaded_env, tables, columns, parts, profiles, relations)
    write_summary(paths.summary_md, args, paths, tables, columns, parts, profiles, relations)

    print(f"wrote_run_root={paths.run_root}", flush=True)
    print(f"tables={len(tables):,} columns={len(columns):,} inferred_relations={len(relations):,}", flush=True)
    print(f"summary={paths.summary_md}", flush=True)


def default_audit_clickhouse_url() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL")
        or os.environ.get("SEC_CLICKHOUSE_URL")
        or os.environ.get("QMD_CLICKHOUSE_URL")
        or default_clickhouse_url()
    )


def default_audit_clickhouse_user() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_USER")
        or os.environ.get("SEC_CLICKHOUSE_USER")
        or os.environ.get("QMD_CLICKHOUSE_USER")
        or default_clickhouse_user()
    )


def default_audit_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_PASSWORD")
        or os.environ.get("SEC_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or default_clickhouse_password()
    )


def fetch_tables(client: ClickHouseHttpClient, database: str) -> list[dict[str, Any]]:
    available = available_system_columns(client, "tables")
    select_fields = system_select_fields(
        [
            "database",
            "name",
            "uuid",
            "engine",
            "engine_full",
            "total_rows",
            "total_bytes",
            "total_bytes_uncompressed",
            "metadata_modification_time",
            "partition_key",
            "sorting_key",
            "primary_key",
            "sampling_key",
            "storage_policy",
            "create_table_query",
        ],
        available,
    )
    rows = query_json_each_row(
        client,
        f"""
        SELECT {select_fields}
        FROM system.tables
        WHERE database = {sql_string(database)}
        ORDER BY name
        """,
    )
    for row in rows:
        row["table"] = row["name"]
        row["family"] = table_family(row["name"])
    return rows


def fetch_columns(client: ClickHouseHttpClient, database: str) -> list[dict[str, Any]]:
    available = available_system_columns(client, "columns")
    select_fields = system_select_fields(
        [
            "database",
            "table",
            "position",
            "name",
            "type",
            "default_kind",
            "default_expression",
            "comment",
            "codec_expression",
            "ttl_expression",
            "is_in_partition_key",
            "is_in_sorting_key",
            "is_in_primary_key",
            "is_in_sampling_key",
        ],
        available,
    )
    rows = query_json_each_row(
        client,
        f"""
        SELECT {select_fields}
        FROM system.columns
        WHERE database = {sql_string(database)}
        ORDER BY table, position
        """,
    )
    for row in rows:
        for optional in ("comment", "codec_expression", "ttl_expression"):
            row.setdefault(optional, "")
        for optional in ("is_in_partition_key", "is_in_sorting_key", "is_in_primary_key", "is_in_sampling_key"):
            row.setdefault(optional, 0)
    for row in rows:
        row["family"] = table_family(row["table"])
        row["role_hint"] = column_role_hint(row["name"], row["type"])
    return rows


def fetch_parts(client: ClickHouseHttpClient, database: str) -> list[dict[str, Any]]:
    return query_json_each_row(
        client,
        f"""
        SELECT
            database,
            table,
            disk_name,
            count() AS active_parts,
            uniqExact(partition) AS partitions,
            sum(rows) AS rows,
            sum(bytes_on_disk) AS bytes_on_disk,
            sum(data_compressed_bytes) AS data_compressed_bytes,
            sum(data_uncompressed_bytes) AS data_uncompressed_bytes,
            min(min_time) AS min_time,
            max(max_time) AS max_time
        FROM system.parts
        WHERE database = {sql_string(database)}
          AND active
        GROUP BY database, table, disk_name
        ORDER BY table, disk_name
        """,
    )


def available_system_columns(client: ClickHouseHttpClient, table: str) -> set[str]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT name
        FROM system.columns
        WHERE database = 'system'
          AND table = {sql_string(table)}
        """,
    )
    return {row["name"] for row in rows}


def system_select_fields(fields: list[str], available: set[str]) -> str:
    expressions = []
    for field in fields:
        if field in available:
            expressions.append(quote_ident(field))
        else:
            expressions.append(f"'' AS {quote_ident(field)}")
    return ", ".join(expressions)


def build_table_profiles(
    client: ClickHouseHttpClient,
    database: str,
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    profile_mode: str,
    sample_rows: int,
) -> list[dict[str, Any]]:
    columns_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for column in columns:
        columns_by_table[column["table"]].append(column)
    parts_by_table = parts_summary_by_table(fetch_parts(client, database))
    profiles: list[dict[str, Any]] = []
    for table in tables:
        table_name = table["name"]
        table_columns = columns_by_table.get(table_name, [])
        likely_keys = [col["name"] for col in table_columns if is_likely_key_column(col["name"])]
        profile = {
            "database": database,
            "table": table_name,
            "family": table_family(table_name),
            "engine": table.get("engine", ""),
            "storage_policy": table.get("storage_policy", ""),
            "total_rows": int_or_zero(table.get("total_rows")),
            "total_bytes": int_or_zero(table.get("total_bytes")),
            "column_count": len(table_columns),
            "likely_key_columns": likely_keys,
            "parts": parts_by_table.get(table_name, {}),
            "sample_rows": [],
            "key_column_profile": [],
            "profile_mode": profile_mode,
        }
        if profile_mode == "light":
            profile["sample_rows"] = sample_table_rows(client, database, table_name, sample_rows)
            profile["key_column_profile"] = profile_key_columns(client, database, table_name, table_columns, likely_keys[:12])
        profiles.append(profile)
    return profiles


def parts_summary_by_table(parts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_table: dict[str, dict[str, Any]] = {}
    for part in parts:
        table = part["table"]
        summary = by_table.setdefault(
            table,
            {
                "disk_names": [],
                "active_parts": 0,
                "partitions": 0,
                "rows": 0,
                "bytes_on_disk": 0,
                "data_compressed_bytes": 0,
                "data_uncompressed_bytes": 0,
            },
        )
        summary["disk_names"].append(part.get("disk_name", ""))
        summary["active_parts"] += int_or_zero(part.get("active_parts"))
        summary["partitions"] += int_or_zero(part.get("partitions"))
        summary["rows"] += int_or_zero(part.get("rows"))
        summary["bytes_on_disk"] += int_or_zero(part.get("bytes_on_disk"))
        summary["data_compressed_bytes"] += int_or_zero(part.get("data_compressed_bytes"))
        summary["data_uncompressed_bytes"] += int_or_zero(part.get("data_uncompressed_bytes"))
    return by_table


def sample_table_rows(client: ClickHouseHttpClient, database: str, table: str, sample_rows: int) -> list[dict[str, Any]]:
    if sample_rows <= 0:
        return []
    return query_json_each_row(
        client,
        f"""
        SELECT *
        FROM {quote_ident(database)}.{quote_ident(table)}
        LIMIT {int(sample_rows)}
        """,
    )


def profile_key_columns(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    columns: list[dict[str, Any]],
    key_columns: list[str],
) -> list[dict[str, Any]]:
    if not key_columns:
        return []
    col_types = {col["name"]: col["type"] for col in columns}
    expressions = ["count() AS rows"]
    for col in key_columns:
        quoted = quote_ident(col)
        expressions.append(f"countIf(isNull({quoted})) AS {quote_ident(col + '__nulls')}" if is_nullable_type(col_types[col]) else f"0 AS {quote_ident(col + '__nulls')}")
        expressions.append(f"countIf(toString({quoted}) = '') AS {quote_ident(col + '__empty_strings')}")
        expressions.append(f"uniqCombined64({quoted}) AS {quote_ident(col + '__uniq_estimate')}")
    rows = query_json_each_row(
        client,
        f"""
        SELECT {", ".join(expressions)}
        FROM {quote_ident(database)}.{quote_ident(table)}
        """,
    )
    if not rows:
        return []
    raw = rows[0]
    profiles = []
    row_count = int_or_zero(raw.get("rows"))
    for col in key_columns:
        profiles.append(
            {
                "column": col,
                "type": col_types[col],
                "rows": row_count,
                "nulls": int_or_zero(raw.get(col + "__nulls")),
                "empty_strings": int_or_zero(raw.get(col + "__empty_strings")),
                "uniq_estimate": int_or_zero(raw.get(col + "__uniq_estimate")),
            }
        )
    return profiles


def infer_relations(tables: list[dict[str, Any]], columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    columns_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    primary_like: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for column in columns:
        columns_by_name[column["name"]].append(column)
        if is_primary_like(column["table"], column["name"]):
            primary_like[column["name"]].append(column)

    table_names = {row["name"] for row in tables}
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for column in columns:
        source_table = column["table"]
        source_column = column["name"]
        if not is_likely_key_column(source_column):
            continue
        for target in candidate_targets(source_column, columns_by_name, primary_like, table_names):
            if target["table"] == source_table and target["column"] == source_column:
                continue
            key = (source_table, source_column, target["table"], target["column"], target["reason"])
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                {
                    "source_table": source_table,
                    "source_column": source_column,
                    "target_table": target["table"],
                    "target_column": target["column"],
                    "confidence": target["confidence"],
                    "reason": target["reason"],
                    "source_family": table_family(source_table),
                    "target_family": table_family(target["table"]),
                    "notes": "Inferred from schema names only; validate with coverage queries before migration.",
                }
            )
    return sorted(relations, key=lambda row: (-float(row["confidence"]), row["source_table"], row["source_column"], row["target_table"]))


def candidate_targets(
    source_column: str,
    columns_by_name: dict[str, list[dict[str, Any]]],
    primary_like: dict[str, list[dict[str, Any]]],
    table_names: set[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for target in primary_like.get(source_column, []):
        candidates.append({"table": target["table"], "column": target["name"], "confidence": 0.95, "reason": "same_primary_like_column"})
    if source_column in columns_by_name:
        for target in columns_by_name[source_column]:
            candidates.append({"table": target["table"], "column": target["name"], "confidence": 0.75, "reason": "same_column_name"})

    if source_column.endswith("_id"):
        stem = source_column[:-3]
        expected_table_suffix = stem + "_v1"
        for table_name in table_names:
            if table_name.endswith(expected_table_suffix):
                target_column = source_column
                for target in columns_by_name.get(target_column, []):
                    if target["table"] == table_name:
                        candidates.append({"table": table_name, "column": target_column, "confidence": 0.9, "reason": "table_name_matches_id_stem"})
    return candidates


def write_create_sql(path: Path, tables: list[dict[str, Any]]) -> None:
    lines = [
        "-- Generated from system.tables.create_table_query.",
        "-- This file snapshots the source database DDL for migration design.",
        "",
    ]
    for table in tables:
        lines.append(f"-- {table['database']}.{table['name']}")
        lines.append(table.get("create_table_query") or "")
        lines.append(";")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_manifest(
    path: Path,
    args: argparse.Namespace,
    paths: AuditPaths,
    loaded_env: list[Path],
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": git_commit(REPO_ROOT),
        "job_type": "q_live_migration_schema_audit",
        "source_database": args.source_database,
        "target_database": args.target_database,
        "profile_mode": args.profile_mode,
        "sample_rows": args.sample_rows,
        "output_root": str(paths.run_root),
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(
            [
                "QLIVE_MIGRATION_CLICKHOUSE_URL",
                "QLIVE_MIGRATION_CLICKHOUSE_USER",
                "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_READ_URL",
                "REAL_LIVE_CLICKHOUSE_READ_USER",
                "REAL_LIVE_CLICKHOUSE_READ_PASSWORD",
                "CLICKHOUSE_LIVE_STORAGE_POLICY",
            ]
        ),
        "output_files": {
            "tables_jsonl": str(paths.tables_jsonl),
            "columns_jsonl": str(paths.columns_jsonl),
            "parts_jsonl": str(paths.parts_jsonl),
            "table_profiles_jsonl": str(paths.table_profiles_jsonl),
            "inferred_relations_jsonl": str(paths.inferred_relations_jsonl),
            "create_sql": str(paths.create_sql),
            "summary_md": str(paths.summary_md),
        },
        "counts": {
            "tables": len(tables),
            "columns": len(columns),
            "part_disk_groups": len(parts),
            "profiles": len(profiles),
            "inferred_relations": len(relations),
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_summary(
    path: Path,
    args: argparse.Namespace,
    paths: AuditPaths,
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> None:
    families = group_count(tables, "family")
    rows_by_family: dict[str, int] = defaultdict(int)
    bytes_by_family: dict[str, int] = defaultdict(int)
    for table in tables:
        rows_by_family[table["family"]] += int_or_zero(table.get("total_rows"))
        bytes_by_family[table["family"]] += int_or_zero(table.get("total_bytes"))

    storage_policies = group_count(tables, "storage_policy")
    disks = group_count(parts, "disk_name")
    largest = sorted(tables, key=lambda row: int_or_zero(row.get("total_bytes")), reverse=True)[:15]
    relation_pairs = sorted(relations, key=lambda row: -float(row["confidence"]))[:30]
    missing_policy = [row["name"] for row in tables if not row.get("storage_policy")]

    lines = [
        "# Trading Dashboard Schema Audit",
        "",
        f"- Source database: `{args.source_database}`",
        f"- Target database under design: `{args.target_database}`",
        f"- Audit run root: `{paths.run_root}`",
        f"- Profile mode: `{args.profile_mode}`",
        f"- Tables: `{len(tables):,}`",
        f"- Columns: `{len(columns):,}`",
        f"- Inferred schema relations: `{len(relations):,}`",
        "",
        "## Output Files",
        "",
        f"- `tables.jsonl`: table engines, keys, row counts, bytes, storage policy, and original create SQL fields.",
        f"- `columns.jsonl`: full column inventory with type, key membership, codec, defaults, and role hints.",
        f"- `parts.jsonl`: active part storage by table/disk from `system.parts`.",
        f"- `table_profiles.jsonl`: per-table summary; in `light` mode includes samples and key-column profiles.",
        f"- `inferred_relations.jsonl`: schema-name relation candidates to validate before migration.",
        f"- `create_statements.sql`: source DDL snapshot.",
        f"- `audit_manifest.json`: reproducibility metadata and secret presence only.",
        "",
        "## Table Families",
        "",
        "| Family | Tables | Rows | Bytes |",
        "| --- | ---: | ---: | ---: |",
    ]
    for family, count in sorted(families.items()):
        lines.append(f"| `{family}` | {count:,} | {rows_by_family[family]:,} | {bytes_by_family[family]:,} |")

    lines.extend(
        [
            "",
            "## Storage",
            "",
            "| Storage Policy | Tables |",
            "| --- | ---: |",
        ]
    )
    for policy, count in sorted(storage_policies.items()):
        lines.append(f"| `{policy or '<empty>'}` | {count:,} |")
    lines.extend(["", "| Disk | Active Part Groups |", "| --- | ---: |"])
    for disk, count in sorted(disks.items()):
        lines.append(f"| `{disk or '<unknown>'}` | {count:,} |")
    if missing_policy:
        lines.extend(["", f"Tables without explicit storage policy: `{len(missing_policy):,}`."])

    lines.extend(["", "## Largest Tables", "", "| Table | Family | Rows | Bytes | Storage Policy |", "| --- | --- | ---: | ---: | --- |"])
    for table in largest:
        lines.append(
            f"| `{table['name']}` | `{table['family']}` | {int_or_zero(table.get('total_rows')):,} | "
            f"{int_or_zero(table.get('total_bytes')):,} | `{table.get('storage_policy') or ''}` |"
        )

    lines.extend(
        [
            "",
            "## Highest Confidence Inferred Relations",
            "",
            "These are inferred from schema names only. Use them as migration-design inputs, not as verified foreign keys.",
            "",
            "| Source | Target | Confidence | Reason |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for relation in relation_pairs:
        lines.append(
            f"| `{relation['source_table']}.{relation['source_column']}` | "
            f"`{relation['target_table']}.{relation['target_column']}` | "
            f"{float(relation['confidence']):.2f} | `{relation['reason']}` |"
        )

    lines.extend(
        [
            "",
            "## Design Notes For Phase 2",
            "",
            "- Use this audit as source truth for current table shapes; do not infer data correctness from schema alone.",
            "- Validate relation candidates with coverage queries before creating q_live bridge tables.",
            "- Tables with empty or HDD-oriented storage policy should be recreated in q_live with `CLICKHOUSE_LIVE_STORAGE_POLICY`.",
            "- SEC filing market labels need exact acceptance timestamps; if the current table only has filing dates, add a companion/enriched target table.",
            "- Preserve source ids, publication batch ids, and source hashes where they exist; they are useful for idempotent migration and future sync jobs.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip(";") + "\nFORMAT JSONEachRow")
    rows = []
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def table_family(table: str) -> str:
    if "_" not in table:
        return "other"
    if table.startswith("market_"):
        return "market"
    if table.startswith("sec_"):
        return "sec"
    if table.startswith("massive_"):
        return "massive"
    if table.startswith("live_"):
        return "live"
    return table.split("_", 1)[0]


def column_role_hint(name: str, type_name: str) -> str:
    lower = name.lower()
    if is_primary_like("", name):
        return "primary_like_id"
    if is_likely_key_column(name):
        return "relationship_or_identifier"
    if lower.endswith("_at") or lower.endswith("_at_utc") or "datetime" in lower:
        return "timestamp"
    if lower.endswith("_date"):
        return "date"
    if "json" in lower or type_name.startswith("Map(") or type_name.startswith("Array("):
        return "semi_structured"
    if "price" in lower or "value" in lower or "amount" in lower:
        return "measure"
    return "attribute"


def is_primary_like(table: str, column: str) -> bool:
    if column == "id":
        return True
    if table:
        stem = re.sub(r"_v\d+$", "", table)
        return column == stem + "_id"
    return column.endswith("_id")


def is_likely_key_column(name: str) -> bool:
    return bool(ID_RE.search(name))


def is_nullable_type(type_name: str) -> bool:
    return type_name.startswith("Nullable(")


def group_count(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get(field) or "")] += 1
    return dict(counts)


def int_or_zero(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def print_header(args: argparse.Namespace, paths: AuditPaths, loaded_env: list[Path]) -> None:
    print("=" * 96, flush=True)
    print("q_live migration phase 1: trading_dashboard_dev schema audit", flush=True)
    print(f"source_database={args.source_database}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"profile_mode={args.profile_mode}", flush=True)
    print(f"output_root={paths.run_root}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print(
        "secret_status="
        + json.dumps(
            secret_status(
                [
                    "QLIVE_MIGRATION_CLICKHOUSE_URL",
                    "QLIVE_MIGRATION_CLICKHOUSE_USER",
                    "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                    "REAL_LIVE_CLICKHOUSE_READ_URL",
                    "REAL_LIVE_CLICKHOUSE_READ_USER",
                    "REAL_LIVE_CLICKHOUSE_READ_PASSWORD",
                    "CLICKHOUSE_LIVE_STORAGE_POLICY",
                ]
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()

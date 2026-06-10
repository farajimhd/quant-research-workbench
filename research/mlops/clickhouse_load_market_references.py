from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse_ingest_sip_compact_codec import DEFAULT_DATABASE, default_storage_policy, env_status_keys  # noqa: E402
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_REFERENCE_DIR = REPO_ROOT / "research" / "market_references" / "massive"


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    table: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load dense market reference tables into ClickHouse.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--reference-dir", default=str(DEFAULT_REFERENCE_DIR))
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--rebuild", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def reference_specs(reference_dir: Path) -> list[ReferenceSpec]:
    return [
        ReferenceSpec("ref_stock_conditions", reference_dir / "stock_conditions.json"),
        ReferenceSpec("ref_stock_exchanges", reference_dir / "stock_exchanges.json"),
        ReferenceSpec("ref_stock_tapes", reference_dir / "stock_tapes.json"),
    ]


def create_reference_table_sql(database: str, table: str, storage_policy: str) -> str:
    settings = f"SETTINGS storage_policy = {sql_string(storage_policy)}" if storage_policy.strip() else ""
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    reference_name LowCardinality(String),
    raw_id Nullable(Int32),
    raw_code LowCardinality(String),
    dense_id UInt8,
    dense_id_bits UInt8,
    dense_id_kind LowCardinality(String),
    name String,
    description String,
    provider LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY (reference_name, dense_id)
{settings}
"""


def json_rows(path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    reference_name = str(payload.get("name") or path.stem)
    rows = []
    for row in payload.get("results", []):
        dense_id = row.get("dense_id")
        if dense_id is None:
            continue
        rows.append(
            {
                "reference_name": reference_name,
                "raw_id": row.get("id"),
                "raw_code": str(row.get("code") if row.get("code") is not None else ""),
                "dense_id": int(dense_id),
                "dense_id_bits": int(row.get("dense_id_bits") or payload.get("dense_encoding", {}).get("dense_id_bits") or 0),
                "dense_id_kind": str(row.get("dense_id_kind") or ""),
                "name": str(row.get("name") or ""),
                "description": str(row.get("description") or ""),
                "provider": str(row.get("provider") or payload.get("provider") or ""),
            }
        )
    return reference_name, rows


def value_sql(row: dict[str, Any]) -> str:
    raw_id = "NULL" if row["raw_id"] is None else str(int(row["raw_id"]))
    return (
        "("
        f"{sql_string(row['reference_name'])}, "
        f"{raw_id}, "
        f"{sql_string(row['raw_code'])}, "
        f"{int(row['dense_id'])}, "
        f"{int(row['dense_id_bits'])}, "
        f"{sql_string(row['dense_id_kind'])}, "
        f"{sql_string(row['name'])}, "
        f"{sql_string(row['description'])}, "
        f"{sql_string(row['provider'])}"
        ")"
    )


def load_one(client: ClickHouseHttpClient, args: argparse.Namespace, spec: ReferenceSpec) -> None:
    table = f"{quote_ident(args.database)}.{quote_ident(spec.table)}"
    reference_name, rows = json_rows(spec.path)
    if args.rebuild:
        client.execute(f"DROP TABLE IF EXISTS {table} SYNC")
    client.execute(create_reference_table_sql(args.database, spec.table, args.storage_policy))
    if not args.rebuild:
        client.execute(f"ALTER TABLE {table} DELETE WHERE reference_name = {sql_string(reference_name)}")
    if rows:
        values = ",\n".join(value_sql(row) for row in rows)
        client.execute(
            f"""
INSERT INTO {table}
(
    reference_name,
    raw_id,
    raw_code,
    dense_id,
    dense_id_bits,
    dense_id_kind,
    name,
    description,
    provider
)
VALUES
{values}
"""
        )
    count = client.query_tsv(f"SELECT count() FROM {table}").strip()
    print(f"LOADED {table} reference={reference_name} rows={count}", flush=True)


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    reference_dir = Path(args.reference_dir)
    print("=" * 96, flush=True)
    print("Load market reference dense-id tables", flush=True)
    print(f"database={args.database} reference_dir={reference_dir}", flush=True)
    print(f"storage_policy={args.storage_policy} rebuild={args.rebuild}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)
    for spec in reference_specs(reference_dir):
        if not spec.path.exists():
            raise FileNotFoundError(spec.path)
        load_one(client, args, spec)
    print("=" * 96, flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

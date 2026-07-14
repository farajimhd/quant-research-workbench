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

from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import DEFAULT_DATABASE, default_storage_policy, env_status_keys  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_REFERENCE_DIR = REPO_ROOT / "research" / "market_references" / "massive"

GLOSSARY_REFERENCE_TABLES = [
    ("ref_quote_conditions", "quote_conditions"),
    ("ref_trade_conditions", "trade_conditions"),
    ("ref_trade_corrections_nyse", "trade_corrections_nyse"),
    ("ref_financial_status", "financial_status"),
    ("ref_cta_security_status", "cta_security_status"),
    ("ref_halt_reason", "halt_reason"),
    ("ref_utp_security_status", "utp_security_status"),
    ("ref_nbbo_indicators", "nbbo_indicators"),
    ("ref_held_trade_indicators", "held_trade_indicators"),
    ("ref_misc_indicators", "misc_indicators"),
    ("ref_luld_indicators", "luld_indicators"),
]

CONDITION_TOKEN_REFERENCE_TABLE = "event_condition_token_reference"
CONDITION_TOKEN_BITS = 8
CONDITION_TOKEN_SLOT_COUNT = 5
CONDITION_TOKEN_MAX_ID = (1 << CONDITION_TOKEN_BITS) - 1


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    table: str
    path: Path
    kind: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load dense market reference tables into ClickHouse.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--reference-dir", default=str(DEFAULT_REFERENCE_DIR))
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--rebuild", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--drop-deprecated", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--token-reference-table", default=CONDITION_TOKEN_REFERENCE_TABLE)
    return parser.parse_args()


def reference_specs(reference_dir: Path) -> list[ReferenceSpec]:
    specs = [
        ReferenceSpec(table, reference_dir / "conditions_indicators_glossary.json", kind)
        for table, kind in GLOSSARY_REFERENCE_TABLES
    ]
    specs.extend(
        [
        ReferenceSpec("ref_stock_exchanges", reference_dir / "stock_exchanges.json", "json_results"),
        ReferenceSpec("ref_stock_tapes", reference_dir / "stock_tapes.json", "json_results"),
        ]
    )
    return specs


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


def create_condition_table_sql(database: str, table: str, storage_policy: str) -> str:
    settings = f"SETTINGS storage_policy = {sql_string(storage_policy)}" if storage_policy.strip() else ""
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    reference_name LowCardinality(String),
    source_row UInt16,
    modifier_int Int16,
    raw_modifier LowCardinality(String),
    dense_id UInt8,
    dense_id_bits UInt8,
    condition String,
    sip_mapping LowCardinality(String),
    update_high_low UInt8,
    update_last UInt8,
    update_volume UInt8,
    provider LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY (reference_name, source_row)
{settings}
"""


def create_condition_token_table_sql(database: str, table: str, storage_policy: str) -> str:
    settings = f"SETTINGS storage_policy = {sql_string(storage_policy)}" if storage_policy.strip() else ""
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    token_id UInt16,
    token_bits UInt8,
    source_family LowCardinality(String),
    source_table LowCardinality(String),
    reference_name LowCardinality(String),
    source_row UInt16,
    modifier_int Int16,
    raw_modifier LowCardinality(String),
    condition String,
    sip_mapping LowCardinality(String),
    update_high_low UInt8,
    update_last UInt8,
    update_volume UInt8,
    provider LowCardinality(String),
    is_join_canonical UInt8,
    is_unknown UInt8
)
ENGINE = MergeTree
ORDER BY (token_id, source_family, modifier_int, source_row)
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


def official_trade_update_rules(reference_dir: Path) -> dict[int, tuple[int, int, int]]:
    """Return consolidated price/volume eligibility from Massive's conditions API snapshot.

    The glossary remains the token identity authority, but blank glossary update cells
    are not equivalent to ``false``.  The conditions API is the authoritative source
    for consolidated aggregate update rules.
    """
    path = reference_dir / "stock_conditions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rules: dict[int, tuple[int, int, int]] = {}
    for row in payload.get("results", []):
        if row.get("dense_id_kind") != "actual" or "trade" not in (row.get("data_types") or []):
            continue
        consolidated = (row.get("update_rules") or {}).get("consolidated")
        if not isinstance(consolidated, dict):
            continue
        modifier = int(row["id"])
        rules[modifier] = (
            int(bool(consolidated.get("updates_high_low"))),
            int(bool(consolidated.get("updates_open_close"))),
            int(bool(consolidated.get("updates_volume"))),
        )
    if not rules:
        raise ValueError(f"{path} contains no consolidated stock trade update rules")
    return rules


def condition_update_values(
    row: dict[str, Any],
    *,
    table_name: str,
    trade_rules: dict[int, tuple[int, int, int]],
) -> tuple[int, int, int]:
    modifier = int(row["modifier_int"])
    if table_name == "trade_conditions" and modifier in trade_rules:
        return trade_rules[modifier]
    return (
        yes_no_to_int(row.get("update_high_low")),
        yes_no_to_int(row.get("update_last")),
        yes_no_to_int(row.get("update_volume")),
    )


def glossary_condition_rows(path: Path, table_name: str) -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    table = payload.get("tables", {}).get(table_name)
    if not isinstance(table, dict):
        raise KeyError(f"{path} does not contain glossary table {table_name!r}")
    metadata = table.get("metadata", {})
    dense_bits = int(metadata.get("dense_combo_id_bits_with_unknown") or 0)
    reference_name = str(table_name)
    trade_rules = official_trade_update_rules(path.parent)
    rows = [
        {
            "reference_name": reference_name,
            "source_row": 0,
            "modifier_int": -32768,
            "raw_modifier": "",
            "dense_id": 0,
            "dense_id_bits": dense_bits,
            "condition": f"{table_name} missing or unknown",
            "sip_mapping": "",
            "update_high_low": 0,
            "update_last": 0,
            "update_volume": 0,
            "provider": "internal",
        }
    ]
    for dense_id, row in enumerate(table.get("rows", []), start=1):
        update_high_low, update_last, update_volume = condition_update_values(
            row,
            table_name=table_name,
            trade_rules=trade_rules,
        )
        rows.append(
            {
                "reference_name": reference_name,
                "source_row": int(row.get("source_row") or dense_id),
                "modifier_int": int(row["modifier_int"]),
                "raw_modifier": str(row.get("modifier") or ""),
                "dense_id": dense_id,
                "dense_id_bits": dense_bits,
                "condition": str(row.get("condition") or ""),
                "sip_mapping": str(row.get("sip_mapping") or ""),
                "update_high_low": update_high_low,
                "update_last": update_last,
                "update_volume": update_volume,
                "provider": str(payload.get("provider") or "massive"),
            }
        )
    return reference_name, rows


def glossary_condition_payload(path: Path, table_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    table = payload.get("tables", {}).get(table_name)
    if not isinstance(table, dict):
        raise KeyError(f"{path} does not contain glossary table {table_name!r}")
    return payload, table


def yes_no_to_int(value: Any) -> int:
    return 1 if str(value or "").strip().lower() == "yes" else 0


def build_condition_token_rows(reference_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "token_id": 0,
            "token_bits": CONDITION_TOKEN_BITS,
            "source_family": "unknown",
            "source_table": "",
            "reference_name": "unknown",
            "source_row": 0,
            "modifier_int": -32768,
            "raw_modifier": "",
            "condition": "missing or unknown condition/indicator/correction token",
            "sip_mapping": "",
            "update_high_low": 0,
            "update_last": 0,
            "update_volume": 0,
            "provider": "internal",
            "is_join_canonical": 1,
            "is_unknown": 1,
        }
    ]
    token_id = 1
    seen_join_keys: set[tuple[str, int]] = set()
    trade_rules = official_trade_update_rules(reference_dir)
    path = reference_dir / "conditions_indicators_glossary.json"
    for source_table, kind in GLOSSARY_REFERENCE_TABLES:
        payload, table = glossary_condition_payload(path, kind)
        table_rows = sorted(
            table.get("rows", []),
            key=lambda item: (int(item.get("source_row") or 0), int(item.get("modifier_int") or 0), str(item.get("condition") or "")),
        )
        for row in table_rows:
            modifier = int(row["modifier_int"])
            update_high_low, update_last, update_volume = condition_update_values(
                row,
                table_name=kind,
                trade_rules=trade_rules,
            )
            join_key = (kind, modifier)
            is_canonical = 0 if join_key in seen_join_keys else 1
            seen_join_keys.add(join_key)
            rows.append(
                {
                    "token_id": token_id,
                    "token_bits": CONDITION_TOKEN_BITS,
                    "source_family": kind,
                    "source_table": source_table,
                    "reference_name": kind,
                    "source_row": int(row.get("source_row") or token_id),
                    "modifier_int": modifier,
                    "raw_modifier": str(row.get("modifier") or ""),
                    "condition": str(row.get("condition") or ""),
                    "sip_mapping": str(row.get("sip_mapping") or ""),
                    "update_high_low": update_high_low,
                    "update_last": update_last,
                    "update_volume": update_volume,
                    "provider": str(payload.get("provider") or "massive"),
                    "is_join_canonical": is_canonical,
                    "is_unknown": 0,
                }
            )
            token_id += 1
    validate_condition_token_rows(rows)
    return rows


def validate_condition_token_rows(rows: list[dict[str, Any]]) -> None:
    max_token_id = max(int(row["token_id"]) for row in rows) if rows else 0
    if max_token_id > CONDITION_TOKEN_MAX_ID:
        raise ValueError(
            f"Unified condition token ids overflow {CONDITION_TOKEN_BITS} bits: "
            f"max_token_id={max_token_id} capacity={CONDITION_TOKEN_MAX_ID}"
        )
    seen_ids: set[int] = set()
    canonical_keys: set[tuple[str, int]] = set()
    for row in rows:
        token_id = int(row["token_id"])
        if token_id in seen_ids:
            raise ValueError(f"Duplicate unified condition token id: {token_id}")
        seen_ids.add(token_id)
        if int(row["is_join_canonical"]):
            key = (str(row["source_family"]), int(row["modifier_int"]))
            if key in canonical_keys:
                raise ValueError(f"Duplicate canonical condition token join key: {key}")
            canonical_keys.add(key)


def value_string(value: Any) -> str:
    text = str(value or "")
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def value_sql(row: dict[str, Any]) -> str:
    raw_id = "NULL" if row["raw_id"] is None else str(int(row["raw_id"]))
    return (
        "("
        f"{value_string(row['reference_name'])}, "
        f"{raw_id}, "
        f"{value_string(row['raw_code'])}, "
        f"{int(row['dense_id'])}, "
        f"{int(row['dense_id_bits'])}, "
        f"{value_string(row['dense_id_kind'])}, "
        f"{value_string(row['name'])}, "
        f"{value_string(row['description'])}, "
        f"{value_string(row['provider'])}"
        ")"
    )


def condition_value_sql(row: dict[str, Any]) -> str:
    return (
        "("
        f"{value_string(row['reference_name'])}, "
        f"{int(row['source_row'])}, "
        f"{int(row['modifier_int'])}, "
        f"{value_string(row['raw_modifier'])}, "
        f"{int(row['dense_id'])}, "
        f"{int(row['dense_id_bits'])}, "
        f"{value_string(row['condition'])}, "
        f"{value_string(row['sip_mapping'])}, "
        f"{int(row['update_high_low'])}, "
        f"{int(row['update_last'])}, "
        f"{int(row['update_volume'])}, "
        f"{value_string(row['provider'])}"
        ")"
    )


def condition_token_value_sql(row: dict[str, Any]) -> str:
    return (
        "("
        f"{int(row['token_id'])}, "
        f"{int(row['token_bits'])}, "
        f"{value_string(row['source_family'])}, "
        f"{value_string(row['source_table'])}, "
        f"{value_string(row['reference_name'])}, "
        f"{int(row['source_row'])}, "
        f"{int(row['modifier_int'])}, "
        f"{value_string(row['raw_modifier'])}, "
        f"{value_string(row['condition'])}, "
        f"{value_string(row['sip_mapping'])}, "
        f"{int(row['update_high_low'])}, "
        f"{int(row['update_last'])}, "
        f"{int(row['update_volume'])}, "
        f"{value_string(row['provider'])}, "
        f"{int(row['is_join_canonical'])}, "
        f"{int(row['is_unknown'])}"
        ")"
    )


def load_one(client: ClickHouseHttpClient, args: argparse.Namespace, spec: ReferenceSpec) -> None:
    table = f"{quote_ident(args.database)}.{quote_ident(spec.table)}"
    if spec.kind != "json_results":
        reference_name, rows = glossary_condition_rows(spec.path, spec.kind)
        create_sql = create_condition_table_sql(args.database, spec.table, args.storage_policy)
        columns = """
(
    reference_name,
    source_row,
    modifier_int,
    raw_modifier,
    dense_id,
    dense_id_bits,
    condition,
    sip_mapping,
    update_high_low,
    update_last,
    update_volume,
    provider
)
"""
        values_sql = condition_value_sql
    else:
        reference_name, rows = json_rows(spec.path)
        create_sql = create_reference_table_sql(args.database, spec.table, args.storage_policy)
        columns = """
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
"""
        values_sql = value_sql
    if args.rebuild:
        client.execute(f"DROP TABLE IF EXISTS {table} SYNC")
    client.execute(create_sql)
    if not args.rebuild:
        client.execute(f"ALTER TABLE {table} DELETE WHERE reference_name = {sql_string(reference_name)}")
    if rows:
        values = ",\n".join(values_sql(row) for row in rows)
        client.execute(
            f"""
INSERT INTO {table}
{columns}
VALUES
{values}
"""
        )
    count = client.query_tsv(f"SELECT count() FROM {table}").strip()
    print(f"LOADED {table} reference={reference_name} rows={count}", flush=True)


def load_condition_token_reference(client: ClickHouseHttpClient, args: argparse.Namespace, reference_dir: Path) -> None:
    table = f"{quote_ident(args.database)}.{quote_ident(args.token_reference_table)}"
    create_sql = create_condition_token_table_sql(args.database, args.token_reference_table, args.storage_policy)
    rows = build_condition_token_rows(reference_dir)
    if args.rebuild:
        client.execute(f"DROP TABLE IF EXISTS {table} SYNC")
    client.execute(create_sql)
    if not args.rebuild:
        client.execute(f"TRUNCATE TABLE {table} SYNC")
    if rows:
        values = ",\n".join(condition_token_value_sql(row) for row in rows)
        client.execute(
            f"""
INSERT INTO {table}
(
    token_id,
    token_bits,
    source_family,
    source_table,
    reference_name,
    source_row,
    modifier_int,
    raw_modifier,
    condition,
    sip_mapping,
    update_high_low,
    update_last,
    update_volume,
    provider,
    is_join_canonical,
    is_unknown
)
VALUES
{values}
"""
        )
    count = int(client.query_tsv(f"SELECT count() FROM {table}").strip() or 0)
    max_token_id = int(client.query_tsv(f"SELECT max(token_id) FROM {table}").strip() or 0)
    canonical_rows = int(client.query_tsv(f"SELECT count() FROM {table} WHERE is_join_canonical = 1").strip() or 0)
    if count != len(rows) or max_token_id > CONDITION_TOKEN_MAX_ID:
        raise RuntimeError(
            f"Unified condition token reference validation failed: count={count} expected={len(rows)} "
            f"max_token_id={max_token_id} capacity={CONDITION_TOKEN_MAX_ID}"
        )
    print(
        f"LOADED {table} rows={count} canonical_rows={canonical_rows} "
        f"token_bits={CONDITION_TOKEN_BITS} token_slots={CONDITION_TOKEN_SLOT_COUNT} "
        f"max_token_id={max_token_id} capacity={CONDITION_TOKEN_MAX_ID}",
        flush=True,
    )


def drop_deprecated_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    if not args.drop_deprecated:
        return
    table = f"{quote_ident(args.database)}.{quote_ident('ref_stock_conditions')}"
    client.execute(f"DROP TABLE IF EXISTS {table} SYNC")
    print(f"DROPPED deprecated {table}", flush=True)


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    reference_dir = Path(args.reference_dir)
    print("=" * 96, flush=True)
    print("Load market reference dense-id tables", flush=True)
    print(f"database={args.database} reference_dir={reference_dir}", flush=True)
    print(f"storage_policy={args.storage_policy} rebuild={args.rebuild}", flush=True)
    print(f"drop_deprecated={args.drop_deprecated}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)
    drop_deprecated_tables(client, args)
    for spec in reference_specs(reference_dir):
        if not spec.path.exists():
            raise FileNotFoundError(spec.path)
        load_one(client, args, spec)
    load_condition_token_reference(client, args, reference_dir)
    print("=" * 96, flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

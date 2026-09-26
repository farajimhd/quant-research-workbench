"""Normalized scalar-tree contract for immutable Strategy 1 configuration.

This is not a JSON/blob store: each node has a parent, a dictionary key or
list ordinal, one declared kind, and at most one typed scalar. Empty
collections have explicit rows. Runtime reconstruction is in memory only.
The release row is written last and is the sole read-admission authority.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import isfinite
from typing import Any, Mapping, Sequence


NODE_TABLE = "arte.strategy_one_configuration_node_v1"
RELEASE_TABLE = "arte.strategy_one_configuration_release_v1"
STORAGE_POLICY = "live_market_ssd"
MAX_NODES = 10_000
MAX_TEXT_LENGTH = 1_024

NODE_COLUMNS = (
    ("strategy_number", "UInt32"), ("release_attempt_id", "UUID"),
    ("node_id", "UInt32"), ("parent_node_id", "Nullable(UInt32)"),
    ("child_key", "Nullable(String)"), ("child_ordinal", "Nullable(UInt32)"),
    ("value_kind", "LowCardinality(String)"),
    ("text_value", "Nullable(String)"), ("int_value", "Nullable(Int64)"),
    ("float_value", "Nullable(Float64)"), ("bool_value", "Nullable(UInt8)"),
)
RELEASE_COLUMNS = (
    ("strategy_number", "UInt32"), ("release_attempt_id", "UUID"),
    ("strategy_id", "String"), ("source_candidate_id", "String"),
    ("source_candidate_hash", "FixedString(64)"),
    ("payload_hash", "FixedString(64)"), ("node_count", "UInt32"),
    ("node_hash", "FixedString(64)"),
    ("published_at", "DateTime64(6,'UTC')"),
)


def ddl() -> tuple[str, str]:
    return (
        f"""CREATE TABLE IF NOT EXISTS {NODE_TABLE} (
          strategy_number UInt32, release_attempt_id UUID, node_id UInt32,
          parent_node_id Nullable(UInt32), child_key Nullable(String),
          child_ordinal Nullable(UInt32), value_kind LowCardinality(String),
          text_value Nullable(String), int_value Nullable(Int64),
          float_value Nullable(Float64), bool_value Nullable(UInt8)
        ) ENGINE=MergeTree PARTITION BY intDiv(strategy_number,100)
          ORDER BY (strategy_number,release_attempt_id,node_id)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {RELEASE_TABLE} (
          strategy_number UInt32, release_attempt_id UUID,
          strategy_id String, source_candidate_id String,
          source_candidate_hash FixedString(64), payload_hash FixedString(64),
          node_count UInt32, node_hash FixedString(64),
          published_at DateTime64(6,'UTC')
        ) ENGINE=MergeTree PARTITION BY intDiv(strategy_number,100)
          ORDER BY (strategy_number,release_attempt_id)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
    )


def verify_tables(client: Any) -> None:
    policies = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy configuration requires SSD-only policy")
    names = (NODE_TABLE.split(".", 1)[1], RELEASE_TABLE.split(".", 1)[1])
    catalog = [json.loads(line) for line in client.execute(
        "SELECT name,engine,storage_policy,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' AND name IN "
        f"('{names[0]}','{names[1]}') FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(catalog) != 2 or {row.get("name") for row in catalog} != set(names):
        raise RuntimeError("Strategy configuration tables are missing or duplicate")
    for row in catalog:
        expected = ("strategy_number,release_attempt_id,node_id"
                    if row["name"] == names[0] else
                    "strategy_number,release_attempt_id")
        if (row.get("engine") != "MergeTree"
                or row.get("storage_policy") != STORAGE_POLICY
                or str(row.get("partition_key") or "").replace(" ", "")
                   != "intDiv(strategy_number,100)"
                or str(row.get("sorting_key") or "").replace(" ", "") != expected):
            raise RuntimeError("Strategy configuration table layout differs")
    columns = [json.loads(line) for line in client.execute(
        "SELECT table,name,type,position FROM system.columns "
        "WHERE database='arte' AND table IN "
        f"('{names[0]}','{names[1]}') "
        "ORDER BY table,position FORMAT JSONEachRow").splitlines()
        if line.strip()]
    actual = {name: [] for name in names}
    for row in columns:
        if row.get("table") not in actual:
            raise RuntimeError("Strategy configuration catalog has unexpected table")
        actual[row["table"]].append((row.get("name"),
                                     str(row.get("type") or "").replace(" ", "")))
    if (tuple(actual[names[0]]) != NODE_COLUMNS
            or tuple(actual[names[1]]) != RELEASE_COLUMNS):
        raise RuntimeError("Strategy configuration columns differ from contract")
    misplaced = client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active "
        "AND database='arte' AND table IN "
        f"('{names[0]}','{names[1]}') "
        f"AND disk_name!='{STORAGE_POLICY}' LIMIT 1 FORMAT JSONEachRow")
    if misplaced.strip():
        raise RuntimeError("Strategy configuration parts are outside SSD")


def install_tables(admin_client: Any) -> None:
    """One-time operator setup; Backtest gets SELECT and cannot invoke DDL."""
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)


def encode_nodes(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Deterministic preorder with bounded scalar values, never serialized JSON."""
    if not isinstance(payload, Mapping):
        raise ValueError("Strategy configuration root must be an object")
    nodes: list[dict[str, Any]] = []

    def visit(value: Any, parent: int | None, key: str | None,
              ordinal: int | None) -> None:
        if len(nodes) >= MAX_NODES:
            raise ValueError("Strategy configuration exceeds the node budget")
        kind = ("object" if isinstance(value, dict) else
                "array" if isinstance(value, list) else
                "null" if value is None else
                "bool" if type(value) is bool else
                "int" if type(value) is int else
                "float" if type(value) is float else
                "text" if type(value) is str else "unsupported")
        if kind == "unsupported":
            raise ValueError("Strategy configuration contains an unsupported value")
        if kind == "text" and len(value) > MAX_TEXT_LENGTH:
            raise ValueError("Strategy configuration text is too long")
        if kind == "int" and not -(1 << 63) <= value < (1 << 63):
            raise ValueError("Strategy configuration integer exceeds Int64")
        if kind == "float" and not isfinite(value):
            raise ValueError("Strategy configuration float is nonfinite")
        node_id = len(nodes)
        nodes.append({
            "node_id": node_id, "parent_node_id": parent,
            "child_key": key, "child_ordinal": ordinal, "value_kind": kind,
            "text_value": value if kind == "text" else None,
            "int_value": value if kind == "int" else None,
            "float_value": value if kind == "float" else None,
            "bool_value": int(value) if kind == "bool" else None,
        })
        if kind == "object":
            for child_key in sorted(value):
                if not isinstance(child_key, str) or not child_key:
                    raise ValueError("Strategy configuration keys must be nonempty text")
                visit(value[child_key], node_id, child_key, None)
        elif kind == "array":
            for index, child in enumerate(value):
                visit(child, node_id, None, index)

    visit(dict(payload), None, None, None)
    return tuple(nodes)


def node_hash(nodes: Sequence[Mapping[str, Any]]) -> str:
    """Seal the typed rows in ordinal order; no persisted JSON representation."""
    keys = tuple(name for name, _ in NODE_COLUMNS if name not in {
        "strategy_number", "release_attempt_id"})
    # ClickHouse JSONEachRow can render an integral Float64 as 0 instead of
    # 0.0. Hash the declared scalar type, not that transport spelling.
    content = [[float(row[key]) if key == "float_value"
                and row.get("value_kind") == "float" else row[key]
                for key in keys] for row in nodes]
    return sha256(json.dumps(content, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def decode_nodes(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate parent/ordinal topology and typed columns before projection."""
    if not 1 <= len(rows) <= MAX_NODES:
        raise ValueError("Strategy configuration node population is invalid")
    values: list[Any] = []
    child_keys: dict[int, set[str]] = {}
    scalar_fields = {"text": "text_value", "int": "int_value",
                     "float": "float_value", "bool": "bool_value"}
    for expected_id, row in enumerate(rows):
        if row.get("node_id") != expected_id:
            raise ValueError("Strategy configuration node order changed")
        kind = row.get("value_kind")
        if kind not in {"object", "array", "null", *scalar_fields}:
            raise ValueError("Strategy configuration has an unknown node kind")
        present = [name for name in scalar_fields.values() if row.get(name) is not None]
        if (present != ([scalar_fields[kind]] if kind in scalar_fields else [])
                or (kind == "int" and type(row["int_value"]) is not int)
                or (kind == "float" and (type(row["float_value"]) not in (int, float)
                                          or not isfinite(row["float_value"])))
                or (kind == "bool" and row["bool_value"] not in (0, 1))
                or (kind == "text" and (not isinstance(row["text_value"], str)
                                         or len(row["text_value"]) > MAX_TEXT_LENGTH))):
            raise ValueError("Strategy configuration scalar columns differ from kind")
        value = {} if kind == "object" else [] if kind == "array" else (
            None if kind == "null" else
            bool(row["bool_value"]) if kind == "bool" else
            float(row["float_value"]) if kind == "float" else
            row[scalar_fields[kind]])
        parent = row.get("parent_node_id")
        key, ordinal = row.get("child_key"), row.get("child_ordinal")
        if expected_id == 0:
            if parent is not None or key is not None or ordinal is not None or kind != "object":
                raise ValueError("Strategy configuration root is invalid")
        else:
            if type(parent) is not int or not 0 <= parent < expected_id:
                raise ValueError("Strategy configuration parent is invalid")
            target = values[parent]
            if isinstance(target, dict):
                if (not isinstance(key, str) or not key or ordinal is not None
                        or key in child_keys.setdefault(parent, set())):
                    raise ValueError("Strategy configuration object key is invalid")
                child_keys[parent].add(key)
                target[key] = value
            elif isinstance(target, list):
                if (key is not None or type(ordinal) is not int
                        or ordinal != len(target)):
                    raise ValueError("Strategy configuration list ordinal is invalid")
                target.append(value)
            else:
                raise ValueError("Strategy configuration scalar cannot own children")
        values.append(value)
    return values[0]

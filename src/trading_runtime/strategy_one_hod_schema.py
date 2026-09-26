"""Normalized producer-owned late-HOD context for unpublished Strategy 1.

One scalar row per certified candidate boundary; no bar copy, JSON, blob, or
snapshot is stored. A producer publishes rows under an immutable attempt,
verifies them, then publishes coverage. Backtest may only SELECT covered rows.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


CONTEXT_TABLE = "arte.strategy_one_hod_context_v1"
COVERAGE_TABLE = "arte.strategy_one_hod_coverage_v1"
STORAGE_POLICY = "live_market_ssd"
PRODUCT_DIGEST = sha256(json.dumps({
    "contract": "strategy-one-hod-completed-100ms-v1",
    "source": "certified-arte-bars-and-as-of-filtered-v7",
    "late_fraction": 1.15,
    "zone_floor_fraction": .7,
    "crossing": "contiguous-price-bearing-open-and-prior-close-at-or-below-unchanged-midpoint",
    "extremes": "certified-price-and-extremes-valid",
    "gap": "no-crossing-through-non-price-bearing-bucket",
    "output": "exact-certified-candidate-boundaries",
}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_CONTEXT_COLUMNS = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("ticker", "LowCardinality(String)"),
    ("derivation_attempt_id", "UUID"), ("boundary_ms", "UInt32"),
    ("session_open_int", "UInt64"), ("prior_hod_int", "UInt64"),
    ("late_mode", "UInt8"), ("gate_level_id", "String"),
)
_COVERAGE_COLUMNS = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("ticker", "LowCardinality(String)"),
    ("derivation_attempt_id", "UUID"), ("bars_attempt_id", "UUID"),
    ("candidate_attempt_id", "UUID"),
    ("candidate_content_hash", "FixedString(64)"),
    ("v7_seed_plan_token", "FixedString(64)"),
    ("product_digest", "FixedString(64)"),
    ("context_count", "UInt32"), ("content_hash", "FixedString(64)"),
    ("certified_at", "DateTime64(6,'UTC')"),
)


def ddl() -> tuple[str, str]:
    return (
        f"""CREATE TABLE IF NOT EXISTS {CONTEXT_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          boundary_ms UInt32,
          session_open_int UInt64,
          prior_hod_int UInt64,
          late_mode UInt8,
          gate_level_id String
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id,boundary_ms)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          bars_attempt_id UUID,
          candidate_attempt_id UUID,
          candidate_content_hash FixedString(64),
          v7_seed_plan_token FixedString(64),
          product_digest FixedString(64),
          context_count UInt32,
          content_hash FixedString(64),
          certified_at DateTime64(6,'UTC')
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
    )


def verify_tables(client: Any) -> None:
    """Fail closed on wrong schema, disk policy, or actual part placement."""
    policies = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 HOD product needs SSD-only policy")
    names = (CONTEXT_TABLE.rsplit(".", 1)[1],
             COVERAGE_TABLE.rsplit(".", 1)[1])
    selected = ",".join(f"'{name}'" for name in names)
    catalog = [json.loads(line) for line in client.execute(
        "SELECT name,engine,storage_policy,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' "
        f"AND name IN ({selected}) FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(catalog) != 2 or {row.get("name") for row in catalog} != set(names):
        raise RuntimeError("Strategy 1 HOD tables are absent or duplicate")
    for row in catalog:
        suffix = ",boundary_ms" if row["name"] == names[0] else ""
        sorting = str(row.get("sorting_key") or "").replace(" ", "").replace("\n", "")
        if (row.get("engine") != "MergeTree"
                or row.get("storage_policy") != STORAGE_POLICY
                or str(row.get("partition_key") or "").replace(" ", "")
                   != "toYYYYMM(session_date)"
                or sorting !=
                "source_build_id,session_date,ticker,derivation_attempt_id" + suffix):
            raise RuntimeError("Strategy 1 HOD table layout differs from contract")
    columns = [json.loads(line) for line in client.execute(
        "SELECT table,name,type,position FROM system.columns "
        "WHERE database='arte' "
        f"AND table IN ({selected}) ORDER BY table,position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = {name: [] for name in names}
    for row in columns:
        if row.get("table") not in actual:
            raise RuntimeError("Unexpected Strategy 1 HOD catalog row")
        actual[row["table"]].append((row.get("name"),
                                      str(row.get("type") or "").replace(" ", "")))
    if (tuple(actual[names[0]]) != _CONTEXT_COLUMNS
            or tuple(actual[names[1]]) != _COVERAGE_COLUMNS):
        raise RuntimeError("Strategy 1 HOD columns differ from contract")
    misplaced = client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active "
        "AND database='arte' "
        f"AND table IN ({selected}) "
        f"AND disk_name!='{STORAGE_POLICY}' LIMIT 1 FORMAT JSONEachRow")
    if misplaced.strip():
        raise RuntimeError("Strategy 1 HOD parts are outside SSD")


def install_tables(admin_client: Any) -> None:
    """Explicit operator/producer setup; never callable by Backtest."""
    # Fail before DDL if a same-named policy can route to backup/default.
    policies = [json.loads(line) for line in admin_client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 HOD product needs SSD-only policy")
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)

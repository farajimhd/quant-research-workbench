"""Producer-owned normalized structural pivot product for Strategy 1.

The Backtest principal is SELECT-only. A producer inserts immutable interval
rows first and coverage last; only a fully verified covered attempt is visible
to a Strategy 1 run. No detector state, snapshots, JSON, or blobs are stored.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any

from src.market_engine.structural_detector import DetectorSettings, VERSION


PIVOT_TABLE = "arte.strategy_one_pivot_interval_v1"
COVERAGE_TABLE = "arte.strategy_one_pivot_coverage_v1"
STORAGE_POLICY = "live_market_ssd"
PRODUCT_DIGEST = sha256(json.dumps({
    "detector": VERSION,
    "settings": asdict(DetectorSettings()),
    "normalizer": "strategy-one-pivot-interval-v1",
    "source_resolution_ms": 1_000,
    "gap_policy": "end-active-intervals-and-restart-detector",
}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ddl() -> tuple[str, str]:
    return (
        f"""CREATE TABLE IF NOT EXISTS {PIVOT_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          side Enum8('low'=1,'high'=2),
          price_int UInt64,
          pivot_at_us UInt64,
          confirmed_at_us UInt64,
          valid_from_boundary_ms UInt32,
          valid_to_boundary_ms Nullable(UInt32)
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id,
                    valid_from_boundary_ms,side,pivot_at_us,confirmed_at_us,price_int)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          bars_attempt_id UUID,
          product_digest FixedString(64),
          interval_count UInt32,
          content_hash FixedString(64),
          certified_at DateTime64(6,'UTC')
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
    )


def verify_tables(client: Any) -> None:
    """Producer/operator admission; Backtest only verifies read-side authority."""
    expected = {
        PIVOT_TABLE.rsplit(".", 1)[1]: (
            ("source_build_id", "String"), ("session_date", "Date"),
            ("ticker", "LowCardinality(String)"),
            ("derivation_attempt_id", "UUID"),
            ("side", "Enum8('low'=1,'high'=2)"),
            ("price_int", "UInt64"), ("pivot_at_us", "UInt64"),
            ("confirmed_at_us", "UInt64"),
            ("valid_from_boundary_ms", "UInt32"),
            ("valid_to_boundary_ms", "Nullable(UInt32)"),
        ),
        COVERAGE_TABLE.rsplit(".", 1)[1]: (
            ("source_build_id", "String"), ("session_date", "Date"),
            ("ticker", "LowCardinality(String)"),
            ("derivation_attempt_id", "UUID"),
            ("bars_attempt_id", "UUID"),
            ("product_digest", "FixedString(64)"),
            ("interval_count", "UInt32"),
            ("content_hash", "FixedString(64)"),
            ("certified_at", "DateTime64(6,'UTC')"),
        ),
    }
    rows = [json.loads(line) for line in client.execute(
        "SELECT name,engine,storage_policy,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' AND name IN "
        "('strategy_one_pivot_interval_v1','strategy_one_pivot_coverage_v1') "
        "FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(rows) != 2 or {row["name"] for row in rows} != set(expected):
        raise RuntimeError("Strategy 1 pivot product tables are absent or duplicate")
    for row in rows:
        sort = str(row.get("sorting_key") or "").replace(" ", "").replace("\n", "")
        base = "source_build_id,session_date,ticker,derivation_attempt_id"
        suffix = (",valid_from_boundary_ms,side,pivot_at_us,confirmed_at_us,price_int"
                  if row["name"] == "strategy_one_pivot_interval_v1" else "")
        if (row.get("engine") != "MergeTree"
                or row.get("storage_policy") != STORAGE_POLICY
                or str(row.get("partition_key") or "").replace(" ", "")
                   != "toYYYYMM(session_date)"
                or sort != base + suffix):
            raise RuntimeError("Strategy 1 pivot table layout differs from contract")
    columns = [json.loads(line) for line in client.execute(
        "SELECT table,name,type,position FROM system.columns "
        "WHERE database='arte' AND table IN "
        "('strategy_one_pivot_interval_v1','strategy_one_pivot_coverage_v1') "
        "ORDER BY table,position FORMAT JSONEachRow").splitlines() if line.strip()]
    found = {name: [] for name in expected}
    for row in columns:
        if row.get("table") not in found:
            raise RuntimeError("Unexpected Strategy 1 pivot catalog row")
        found[row["table"]].append((row["name"],
                                     str(row["type"]).replace(" ", "")))
    if any(tuple(found[name]) != columns for name, columns in expected.items()):
        raise RuntimeError("Strategy 1 pivot columns differ from contract")
    misplaced = client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active "
        "AND database='arte' AND table IN "
        "('strategy_one_pivot_interval_v1','strategy_one_pivot_coverage_v1') "
        f"AND disk_name!='{STORAGE_POLICY}' LIMIT 1 FORMAT JSONEachRow")
    if misplaced.strip():
        raise RuntimeError("Strategy 1 pivot parts are outside SSD")


def install_tables(admin_client: Any) -> None:
    """Explicit operator setup; never callable from the Backtest reader."""
    policies = [json.loads(line) for line in admin_client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 pivot product needs SSD-only policy")
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)

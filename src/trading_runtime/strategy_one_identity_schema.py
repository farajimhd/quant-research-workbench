"""Producer-owned point-in-time broker identity for Strategy 1.

The dated universe producer writes these normalized rows, not Backtest.  A
coverage row is the publication commit: orphaned identity rows are invisible
to readers.  The source build and dated universe run are pinned independently
so a current broker mapping cannot silently replace historical identity.
"""
from __future__ import annotations

import json
from typing import Any


IDENTITY_TABLE = "arte.strategy_one_identity_v1"
COVERAGE_TABLE = "arte.strategy_one_identity_coverage_v1"
STORAGE_POLICY = "live_market_ssd"

IDENTITY_COLUMNS = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("identity_attempt_id", "UUID"), ("ticker", "LowCardinality(String)"),
    ("symbol_id", "String"), ("listing_id", "String"),
    ("security_id", "String"), ("ibkr_conid", "UInt64"),
    ("source_run_id", "String"),
)
COVERAGE_COLUMNS = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("identity_attempt_id", "UUID"), ("universe_date", "Date"),
    ("ticker_count", "UInt32"), ("content_hash", "FixedString(64)"),
    ("certified_at", "DateTime64(6,'UTC')"),
)


def ddl() -> tuple[str, str]:
    return (
        f"""CREATE TABLE IF NOT EXISTS {IDENTITY_TABLE} (
          source_build_id String, session_date Date, identity_attempt_id UUID,
          ticker LowCardinality(String), symbol_id String, listing_id String,
          security_id String, ibkr_conid UInt64, source_run_id String
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,identity_attempt_id,ticker)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
          source_build_id String, session_date Date, identity_attempt_id UUID,
          universe_date Date, ticker_count UInt32, content_hash FixedString(64),
          certified_at DateTime64(6,'UTC')
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,identity_attempt_id)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
    )


def verify_tables(client: Any) -> None:
    """Fail closed on missing, altered, or misplaced operational storage."""
    policies = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 identity requires SSD-only policy")
    names = (IDENTITY_TABLE.split(".", 1)[1], COVERAGE_TABLE.split(".", 1)[1])
    catalog = [json.loads(line) for line in client.execute(
        "SELECT name,engine,storage_policy,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' AND name IN "
        f"('{names[0]}','{names[1]}') FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(catalog) != 2 or {row.get("name") for row in catalog} != set(names):
        raise RuntimeError("Strategy 1 identity tables are missing or duplicate")
    for row in catalog:
        expected_sort = ("source_build_id,session_date,identity_attempt_id"
                         + (",ticker" if row["name"] == names[0] else ""))
        if (row.get("engine") != "MergeTree"
                or row.get("storage_policy") != STORAGE_POLICY
                or str(row.get("partition_key") or "").replace(" ", "")
                   != "toYYYYMM(session_date)"
                or str(row.get("sorting_key") or "").replace(" ", "")
                   != expected_sort):
            raise RuntimeError("Strategy 1 identity table layout differs from contract")
    columns = [json.loads(line) for line in client.execute(
        "SELECT table,name,type,position FROM system.columns "
        "WHERE database='arte' AND table IN "
        f"('{names[0]}','{names[1]}') "
        "ORDER BY table,position FORMAT JSONEachRow").splitlines()
        if line.strip()]
    actual = {name: [] for name in names}
    for row in columns:
        if row.get("table") not in actual:
            raise RuntimeError("Strategy 1 identity catalog has unexpected table")
        actual[row["table"]].append(
            (row.get("name"), str(row.get("type") or "").replace(" ", "")))
    if (tuple(actual[names[0]]) != IDENTITY_COLUMNS
            or tuple(actual[names[1]]) != COVERAGE_COLUMNS):
        raise RuntimeError("Strategy 1 identity columns differ from contract")
    misplaced = client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active "
        "AND database='arte' AND table IN "
        f"('{names[0]}','{names[1]}') "
        f"AND disk_name!='{STORAGE_POLICY}' LIMIT 1 FORMAT JSONEachRow")
    if misplaced.strip():
        raise RuntimeError("Strategy 1 identity parts are outside SSD")


def install_tables(admin_client: Any) -> None:
    """Explicit producer/operator setup only; never call from Backtest."""
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)

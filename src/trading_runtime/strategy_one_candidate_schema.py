"""Producer-owned normalized candidate product for immutable Strategy 1.

The Backtest principal may SELECT these two tables but never INSERT into them.
Every certified ticker-day, including one with no candidates, needs exactly one
coverage row. Coverage is published only after all child rows are validated.
An unreferenced attempt is invisible to Backtest and safe to abandon.
"""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .momentum_session_policy import DEFAULTS as PURCHASE_DEFAULTS
from .strategy_one_columnar import MACD_RESOLUTIONS_MS


CANDIDATE_TABLE = "arte.strategy_one_candidate_v1"
COVERAGE_TABLE = "arte.strategy_one_candidate_coverage_v1"
STORAGE_POLICY = "live_market_ssd"
RULE_CONTRACT = (
    "strategy-one-candidate-v1:completed-100ms-squeeze:closed-macd-1s-5s-10s-30s:"
    "eligible-liquidity-spread-vwap-prior-close:completed-30s-low:one-dollar-floor"
)
RULE_DIGEST = sha256(json.dumps({
    "contract": RULE_CONTRACT,
    "macd_resolutions_ms": MACD_RESOLUTIONS_MS,
    "purchase_defaults": PURCHASE_DEFAULTS,
    "quote_freshness_us": 1_000_000,
    "stop_source_resolution_ms": 30_000,
}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


_CANDIDATE_COLUMNS = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("ticker", "LowCardinality(String)"), ("derivation_attempt_id", "UUID"),
    ("boundary_ms", "UInt32"), ("source_row_index", "UInt32"),
    ("episode_start_ms", "UInt32"), ("macd_1s_boundary_ms", "UInt32"),
    ("macd_5s_boundary_ms", "UInt32"), ("macd_10s_boundary_ms", "UInt32"),
    ("macd_30s_boundary_ms", "UInt32"),
    ("stop_30s_boundary_ms", "UInt32"), ("stop_low_int", "UInt64"),
)
_COVERAGE_COLUMNS = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("ticker", "LowCardinality(String)"), ("derivation_attempt_id", "UUID"),
    ("bars_attempt_id", "UUID"), ("technical_attempt_id", "UUID"),
    ("liquidity_attempt_id", "UUID"), ("candidate_rule_digest", "FixedString(64)"),
    ("scan_query_sha256", "FixedString(64)"),
    ("candidate_count", "UInt32"), ("content_hash", "FixedString(64)"),
    ("certified_at", "DateTime64(6,'UTC')"),
)


def ddl() -> tuple[str, str]:
    """Separate sparse decision boundaries from their source/contract seal."""
    return (
        f"""CREATE TABLE IF NOT EXISTS {CANDIDATE_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          boundary_ms UInt32,
          source_row_index UInt32,
          episode_start_ms UInt32,
          macd_1s_boundary_ms UInt32,
          macd_5s_boundary_ms UInt32,
          macd_10s_boundary_ms UInt32,
          macd_30s_boundary_ms UInt32,
          stop_30s_boundary_ms UInt32,
          stop_low_int UInt64
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id,boundary_ms)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          bars_attempt_id UUID,
          technical_attempt_id UUID,
          liquidity_attempt_id UUID,
          candidate_rule_digest FixedString(64),
          scan_query_sha256 FixedString(64),
          candidate_count UInt32,
          content_hash FixedString(64),
          certified_at DateTime64(6,'UTC')
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
    )


def verify_tables(client: Any) -> None:
    """Operator/producer admission only; Backtest never invokes table DDL."""
    policies = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 candidate product needs SSD-only policy")
    names = tuple(table.split(".", 1)[1] for table in (
        CANDIDATE_TABLE, COVERAGE_TABLE))
    catalog = [json.loads(line) for line in client.execute(
        "SELECT name,engine,storage_policy,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' AND name IN "
        f"('{names[0]}','{names[1]}') FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(catalog) != 2 or {row.get("name") for row in catalog} != set(names):
        raise RuntimeError("Strategy 1 candidate tables are missing or duplicate")
    for row in catalog:
        normalized_sort = str(row.get("sorting_key") or "").replace(" ", "")
        expected_sort = ("source_build_id,session_date,ticker,derivation_attempt_id"
                         + (",boundary_ms" if row["name"] == names[0] else ""))
        if (row.get("engine") != "MergeTree"
                or row.get("storage_policy") != STORAGE_POLICY
                or str(row.get("partition_key") or "").replace(" ", "")
                   != "toYYYYMM(session_date)"
                or normalized_sort != expected_sort):
            raise RuntimeError("Strategy 1 candidate table layout differs from contract")
    columns = [json.loads(line) for line in client.execute(
        "SELECT table,name,type,position FROM system.columns "
        "WHERE database='arte' AND table IN "
        f"('{names[0]}','{names[1]}') "
        "ORDER BY table,position FORMAT JSONEachRow").splitlines() if line.strip()]
    by_table = {name: [] for name in names}
    for row in columns:
        if row.get("table") not in by_table:
            raise RuntimeError("Strategy 1 candidate catalog has an unexpected table")
        by_table[row["table"]].append((row.get("name"),
                                       str(row.get("type") or "").replace(" ", "")))
    if (tuple(by_table[names[0]]) != _CANDIDATE_COLUMNS
            or tuple(by_table[names[1]]) != _COVERAGE_COLUMNS):
        raise RuntimeError("Strategy 1 candidate columns differ from contract")
    misplaced = client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active "
        "AND database='arte' AND table IN "
        f"('{names[0]}','{names[1]}') "
        f"AND disk_name!='{STORAGE_POLICY}' LIMIT 1 FORMAT JSONEachRow")
    if misplaced.strip():
        raise RuntimeError("Strategy 1 candidate parts are outside SSD")


def install_tables(admin_client: Any) -> None:
    """Explicit operator-owned setup; never give this client to Backtest."""
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)


def rename_empty_legacy_rule_column(admin_client: Any) -> None:
    """One-time repair of our unused draft column, with exact empty proof."""
    for table in (CANDIDATE_TABLE, COVERAGE_TABLE):
        count = admin_client.execute(f"SELECT count() FROM {table}").strip()
        if count != "0":
            raise RuntimeError("Strategy 1 candidate column repair requires empty tables")
    columns = [json.loads(line) for line in admin_client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='strategy_one_candidate_coverage_v1' "
        "AND name IN ('strategy_digest','candidate_rule_digest') "
        "FORMAT JSONEachRow").splitlines() if line.strip()]
    if columns != [{"name": "strategy_digest", "type": "FixedString(64)"}]:
        raise RuntimeError("Strategy 1 draft column differs from exact repair scope")
    admin_client.execute(
        f"ALTER TABLE {COVERAGE_TABLE} RENAME COLUMN strategy_digest "
        "TO candidate_rule_digest")
    admin_client.execute(
        f"ALTER TABLE {COVERAGE_TABLE} ADD COLUMN scan_query_sha256 "
        "FixedString(64) AFTER candidate_rule_digest")
    verify_tables(admin_client)

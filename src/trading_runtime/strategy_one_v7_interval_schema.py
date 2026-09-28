"""Producer-owned normalized Strategy 1 intraday V7 derivative contract.

The clock contains completed, price-valid 1s input boundaries. Intervals
contain only changed strategy-facing scalar geometry; coverage seals both
families after exact read-back. Backtest may SELECT a sealed attempt but may
never call install_tables or INSERT into these tables.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.reaction_center import SOLVER_VERSION
from src.market_engine.streaming_level_book import EXTRACTION_VERSION, VERSION


STORAGE_POLICY = "live_market_ssd"
CLOCK_TABLE = "arte.strategy_one_v7_clock_v1"
INTERVAL_TABLE = "arte.strategy_one_v7_level_interval_v1"
COVERAGE_TABLE = "arte.strategy_one_v7_coverage_v1"
TABLES = (CLOCK_TABLE, INTERVAL_TABLE, COVERAGE_TABLE)
PRODUCT_DIGEST = sha256(json.dumps({
    "product": "strategy-one-v7-interval-v1",
    "book_version": VERSION,
    "seed_extraction_version": EXTRACTION_VERSION,
    "fitter": SOLVER_VERSION,
    "input_policy": POLICY,
    "clock": "completed-price-and-extremes-valid-1s",
    "freshness_ms": 1_000,
    "geometry": "ordered-intervals-at-completed-second-revisions",
}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

_BASE = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("ticker", "LowCardinality(String)"),
    ("derivation_attempt_id", "UUID"),
)
_CLOCK = _BASE + (("boundary_ms", "UInt32"),)
_INTERVAL = _BASE + (
    ("level_id", "String"), ("ordinal", "UInt32"),
    ("valid_from_ms", "UInt32"), ("valid_to_ms", "UInt32"),
    ("lower", "Float64"), ("upper", "Float64"),
    ("role", "Enum8('support'=1,'resistance'=2,'transition'=3)"),
    ("transition_from", "Enum8('none'=0,'support'=1,'resistance'=2)"),
    ("confirmed_at_ms", "UInt64"), ("historical", "UInt8"),
)
_COVERAGE = _BASE + (
    ("bars_attempt_id", "UUID"),
    ("source_checkpoint_hash", "String"),
    ("decoded_seed_hash", "FixedString(64)"),
    ("seed_source_plan_hash", "FixedString(64)"),
    ("split_evidence_hash", "FixedString(64)"),
    ("seed_input_policy", "LowCardinality(String)"),
    ("product_digest", "FixedString(64)"),
    ("clock_count", "UInt32"), ("interval_count", "UInt32"),
    ("clock_hash", "FixedString(64)"),
    ("interval_hash", "FixedString(64)"),
    ("certified_at", "DateTime64(6,'UTC')"),
)
CONTRACTS = {CLOCK_TABLE: _CLOCK, INTERVAL_TABLE: _INTERVAL,
             COVERAGE_TABLE: _COVERAGE}
SORT_KEYS = {
    CLOCK_TABLE: "source_build_id,session_date,ticker,derivation_attempt_id,boundary_ms",
    INTERVAL_TABLE: (
        "source_build_id,session_date,ticker,derivation_attempt_id,"
        "valid_from_ms,ordinal,level_id"),
    COVERAGE_TABLE: "source_build_id,session_date,ticker,derivation_attempt_id",
}


def ddl() -> tuple[str, str, str]:
    def statement(table: str, columns: tuple[tuple[str, str], ...]) -> str:
        fields = ",\n          ".join(f"{name} {kind}" for name, kind in columns)
        return (f"CREATE TABLE IF NOT EXISTS {table} (\n          {fields}\n"
                ") ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) "
                f"ORDER BY ({SORT_KEYS[table]}) "
                f"SETTINGS storage_policy='{STORAGE_POLICY}'")

    return tuple(statement(table, CONTRACTS[table]) for table in TABLES)


def verify_tables(client: Any) -> None:
    """Check schema, SSD policy, and actual active-part placement."""
    policies = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 V7 derivative needs SSD-only policy")
    names = tuple(table.rsplit(".", 1)[1] for table in TABLES)
    in_names = ",".join(f"'{name}'" for name in names)
    catalog = [json.loads(line) for line in client.execute(
        "SELECT name,engine,storage_policy,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' AND name IN "
        f"({in_names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(catalog) != len(TABLES) or {row.get("name") for row in catalog} != set(names):
        raise RuntimeError("Strategy 1 V7 derivative tables are absent or duplicate")
    for row in catalog:
        table = "arte." + row["name"]
        if (row.get("engine") != "MergeTree"
                or row.get("storage_policy") != STORAGE_POLICY
                or str(row.get("partition_key") or "").replace(" ", "")
                   != "toYYYYMM(session_date)"
                or "".join(str(row.get("sorting_key") or "").split())
                   != SORT_KEYS[table]):
            raise RuntimeError("Strategy 1 V7 derivative layout differs from contract")
    columns = [json.loads(line) for line in client.execute(
        "SELECT table,name,type,position FROM system.columns "
        "WHERE database='arte' AND table IN "
        f"({in_names}) ORDER BY table,position FORMAT JSONEachRow").splitlines()
        if line.strip()]
    found = {name: [] for name in names}
    for row in columns:
        if row.get("table") not in found:
            raise RuntimeError("Unexpected Strategy 1 V7 derivative catalog row")
        found[row["table"]].append((row.get("name"),
                                     "".join(str(row.get("type") or "").split())))
    if any(tuple(found[name]) != CONTRACTS["arte." + name] for name in names):
        raise RuntimeError("Strategy 1 V7 derivative columns differ from contract")
    misplaced = client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active "
        "AND database='arte' AND table IN "
        f"({in_names}) AND disk_name!='{STORAGE_POLICY}' "
        "LIMIT 1 FORMAT JSONEachRow")
    if misplaced.strip():
        raise RuntimeError("Strategy 1 V7 derivative parts are outside SSD")


def install_tables(admin_client: Any) -> None:
    """Explicit producer/operator setup; never callable by Backtest."""
    policies = [json.loads(line) for line in admin_client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 V7 derivative needs SSD-only policy")
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)

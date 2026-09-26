"""Producer-owned, normalized Strategy 1 causal entry-evidence layout.

STRATEGY CREATION RULES: this is a technical product revision, not a second
user-facing Strategy number. Backtest may SELECT a coverage-certified attempt
but must never install, repair, or INSERT these tables. A changed decision
contract requires a new product table and, after Strategy 1 publication, a new
Strategy number. Rows contain scalar derived evidence, not copied bars, V7
books, JSON, or opaque checkpoints.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


ACTIVATION_TABLE = "arte.strategy_one_entry_activation_v1"
ACTIVATION_RESISTANCE_TABLE = "arte.strategy_one_entry_activation_resistance_v1"
EVIDENCE_TABLE = "arte.strategy_one_entry_evidence_v1"
COVERAGE_TABLE = "arte.strategy_one_entry_coverage_v1"
STORAGE_POLICY = "live_market_ssd"
TICK_SIZE = 0.01
PRODUCT_DIGEST = sha256(json.dumps({
    "contract": "strategy-one-causal-entry-evidence-v1",
    "strategy_number": 1,
    "evaluation_interval_ms": 100,
    "tick_size": TICK_SIZE,
    "source": "certified-candidate-pivot-hod-v7-seed-and-completed-bars",
    "activation": "one-frozen-gap-per-episode-with-ordinal-resistance-children",
    "bos": "completed-one-second-confirmed-and-supported-break",
    "protection": "completed-thirty-second-low-and-third-overhead-v7-target",
    "clock": "never-consume-a-bar-after-the-candidate-boundary",
}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_IDENTITY = (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("ticker", "LowCardinality(String)"),
    ("derivation_attempt_id", "UUID"),
)
_ACTIVATION_COLUMNS = _IDENTITY + (
    ("episode_start_ms", "UInt32"), ("price_int", "UInt64"),
    ("average_gap", "Nullable(Float64)"),
    ("resistance_count", "UInt16"),
)
_RESISTANCE_COLUMNS = _IDENTITY + (
    ("episode_start_ms", "UInt32"), ("ordinal", "UInt16"),
    ("level_id", "String"),
)
_EVIDENCE_COLUMNS = _IDENTITY + (
    ("boundary_ms", "UInt32"), ("episode_start_ms", "UInt32"),
    ("bos_break_boundary_ms", "Nullable(UInt32)"),
    ("bos_pivot_id", "String"), ("bos_break_close_int", "Nullable(UInt64)"),
    ("bos_support_kind", "String"), ("bos_support_level_id", "String"),
    ("bos_support_pivot_id", "String"),
    ("protection_valid", "UInt8"),
    ("stop_price", "Nullable(Float64)"),
    ("target_price", "Nullable(Float64)"),
    ("target_level_id", "String"),
    ("target_ordinal", "Nullable(UInt8)"),
)
_COVERAGE_COLUMNS = _IDENTITY + (
    ("bars_attempt_id", "UUID"), ("candidate_attempt_id", "UUID"),
    ("candidate_content_hash", "FixedString(64)"),
    ("pivot_plan_token", "FixedString(64)"),
    ("hod_plan_token", "FixedString(64)"),
    ("v7_seed_plan_token", "FixedString(64)"),
    ("product_digest", "FixedString(64)"),
    ("activation_count", "UInt32"),
    ("resistance_count", "UInt32"),
    ("evidence_count", "UInt32"),
    ("content_hash", "FixedString(64)"),
    ("certified_at", "DateTime64(6,'UTC')"),
)
_LAYOUT = (
    (ACTIVATION_TABLE, _ACTIVATION_COLUMNS,
     "source_build_id,session_date,ticker,derivation_attempt_id,episode_start_ms"),
    (ACTIVATION_RESISTANCE_TABLE, _RESISTANCE_COLUMNS,
     "source_build_id,session_date,ticker,derivation_attempt_id,episode_start_ms,ordinal"),
    (EVIDENCE_TABLE, _EVIDENCE_COLUMNS,
     "source_build_id,session_date,ticker,derivation_attempt_id,boundary_ms"),
    (COVERAGE_TABLE, _COVERAGE_COLUMNS,
     "source_build_id,session_date,ticker,derivation_attempt_id"),
)


def ddl() -> tuple[str, ...]:
    """Exact SSD-only DDL, callable by an explicit producer installer only."""
    return tuple(
        f"CREATE TABLE IF NOT EXISTS {name} ("
        + ", ".join(f"{column} {type_name}" for column, type_name in columns)
        + ") ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) "
        + f"ORDER BY ({sorting}) SETTINGS storage_policy='{STORAGE_POLICY}'"
        for name, columns, sorting in _LAYOUT
    )


def _catalog_rows(client: Any, query: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        query + " FORMAT JSONEachRow").splitlines() if line.strip()]


def verify_tables(client: Any) -> None:
    """Check exact columns, order, policy, and physical part placement."""
    policy = _catalog_rows(client, "SELECT disks FROM system.storage_policies "
                           f"WHERE policy_name='{STORAGE_POLICY}'")
    if len(policy) != 1 or policy[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 entry evidence needs SSD-only policy")
    names = tuple(name.rsplit(".", 1)[1] for name, _, _ in _LAYOUT)
    selected = ",".join(f"'{name}'" for name in names)
    catalog = _catalog_rows(client,
        "SELECT name,engine,storage_policy,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' "
        f"AND name IN ({selected})")
    if (len(catalog) != len(names)
            or {row.get("name") for row in catalog} != set(names)):
        raise RuntimeError("Strategy 1 entry evidence tables are absent or duplicate")
    sorting_by_name = {name.rsplit(".", 1)[1]: sorting
                       for name, _, sorting in _LAYOUT}
    for row in catalog:
        compact = lambda value: str(value or "").replace(" ", "").replace("\n", "")
        if (row.get("engine") != "MergeTree"
                or row.get("storage_policy") != STORAGE_POLICY
                or compact(row.get("partition_key")) != "toYYYYMM(session_date)"
                or compact(row.get("sorting_key")) != sorting_by_name[row["name"]]):
            raise RuntimeError("Strategy 1 entry evidence table layout differs")
    columns = _catalog_rows(client,
        "SELECT table,name,type,position FROM system.columns "
        "WHERE database='arte' "
        f"AND table IN ({selected}) ORDER BY table,position")
    actual = {name: [] for name in names}
    for row in columns:
        if row.get("table") not in actual:
            raise RuntimeError("Unexpected Strategy 1 entry evidence column")
        actual[row["table"]].append(
            (row.get("name"), str(row.get("type") or "").replace(" ", "")))
    if any(tuple(actual[name.rsplit(".", 1)[1]]) != expected
           for name, expected, _ in _LAYOUT):
        raise RuntimeError("Strategy 1 entry evidence columns differ")
    misplaced = client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active "
        "AND database='arte' "
        f"AND table IN ({selected}) "
        f"AND disk_name!='{STORAGE_POLICY}' LIMIT 1 FORMAT JSONEachRow")
    if misplaced.strip():
        raise RuntimeError("Strategy 1 entry evidence parts are outside SSD")


def install_tables(admin_client: Any) -> None:
    """Producer/operator-only installation; Backtest never calls this."""
    policy = _catalog_rows(admin_client,
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}'")
    if len(policy) != 1 or policy[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 1 entry evidence needs SSD-only policy")
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)

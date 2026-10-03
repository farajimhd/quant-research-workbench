"""Normalized producer contract; Strategy 43/Backtest may only SELECT it."""
from __future__ import annotations

from hashlib import sha256
import json

STORAGE_POLICY = "live_market_ssd"
FACT_TABLE = "arte.strategy_forty_three_second_fact_v1"
POPULATION_TABLE = "arte.strategy_forty_three_population_v1"
COVERAGE_TABLE = "arte.strategy_forty_three_fact_coverage_v1"
BASE = (("source_build_id", "String"), ("session_date", "Date"),
        ("ticker", "LowCardinality(String)"), ("derivation_attempt_id", "UUID"))
FACT_COLUMNS = BASE + (
    ("fact_id", "UUID"), ("boundary_ms", "UInt32"), ("observed", "UInt8"),
    ("close", "Nullable(Float64)"), ("low", "Nullable(Float64)"),
    ("high", "Nullable(Float64)"), ("dollar_volume", "Float64"),
    ("previous_five_second_close", "Nullable(Float64)"),
    ("previous_ten_second_mean_notional", "Nullable(Float64)"),
    ("swing_low", "Nullable(Float64)"), ("swing_available_ms", "UInt32"),
    ("ten_second_mean_movement", "Nullable(Float64)"),
)
POPULATION_COLUMNS = BASE + (
    ("listing_id", "String"), ("symbol_id", "String"), ("security_id", "String"),
    ("conid", "UInt64"), ("admission_ms", "UInt32"),
    ("source_snapshot_hash", "FixedString(64)"), ("source_market_token", "FixedString(64)"),
    ("source_signal_query_hash", "FixedString(64)"),
)
COVERAGE_COLUMNS = BASE + (
    ("product_digest", "FixedString(64)"), ("bars_attempt_id", "UUID"),
    ("liquidity_attempt_id", "UUID"), ("source_market_token", "FixedString(64)"),
    ("population_hash", "FixedString(64)"), ("source_v7_token", "FixedString(64)"),
    ("session_end_ms", "UInt32"), ("fact_count", "UInt32"),
    ("fact_hash", "FixedString(64)"), ("certified_at", "DateTime64(6, 'UTC')"),
)
CONTRACTS = {FACT_TABLE: FACT_COLUMNS, POPULATION_TABLE: POPULATION_COLUMNS,
             COVERAGE_TABLE: COVERAGE_COLUMNS}
SORT_KEYS = {table: "source_build_id,session_date,ticker,derivation_attempt_id"
             + (",boundary_ms" if table == FACT_TABLE else "") for table in CONTRACTS}
PRODUCT_DIGEST = sha256(json.dumps({"product": "strategy-43-completed-history-v1",
    "transport": "arrow-ieee-float64-v2",
    "notional_sum": "ordered-100ms-bucket-array-sum",
    "swing": "2-left-2-right-tied-minimum-available-next-second",
    "movement": "10-consecutive-observed-close-changes-current-second-included",
    "attention": "10-completed-execution-notionals-current-second-excluded",
    "missing_notional": "zero-capacity", "price_history": "no-gap-fill",
    "schemas": CONTRACTS}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ddl() -> tuple[str, ...]:
    return tuple(f"CREATE TABLE IF NOT EXISTS {table} ("
        + ",".join(f"{name} {kind}" for name, kind in columns)
        + ") ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) "
        + f"ORDER BY ({SORT_KEYS[table]}) SETTINGS storage_policy='{STORAGE_POLICY}'"
        for table, columns in CONTRACTS.items())


def verify_tables(client) -> None:
    def rows(query):
        return [json.loads(line) for line in client.execute(query + " FORMAT JSONEachRow").splitlines()
                if line.strip()]
    policies = rows("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if len(policies) != 1 or policies[0]["disks"] != [STORAGE_POLICY]:
        raise RuntimeError("Strategy 43 producer requires the explicit SSD-only policy")
    names = ",".join("'" + table.split(".")[1] + "'" for table in CONTRACTS)
    found = rows("SELECT name,engine,storage_policy,partition_key,sorting_key FROM system.tables "
                 f"WHERE database='arte' AND name IN ({names})")
    if len(found) != len(CONTRACTS) or {"arte." + row["name"] for row in found} != set(CONTRACTS):
        raise RuntimeError("Strategy 43 producer tables are absent or ambiguous")
    for row in found:
        table = "arte." + row["name"]
        if (row["engine"] != "MergeTree" or row["storage_policy"] != STORAGE_POLICY
                or "".join(row["partition_key"].split()) != "toYYYYMM(session_date)"
                or "".join(row["sorting_key"].split()) != SORT_KEYS[table]):
            raise RuntimeError("Strategy 43 producer table layout differs")
        columns = rows(f"SELECT name,type FROM system.columns WHERE database='arte' "
                       f"AND table='{row['name']}' ORDER BY position")
        if tuple((col["name"], col["type"]) for col in columns) != CONTRACTS[table]:
            raise RuntimeError("Strategy 43 producer columns differ")
    if rows("SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
            f"AND table IN ({names}) AND disk_name!='{STORAGE_POLICY}' LIMIT 1"):
        raise RuntimeError("Strategy 43 producer parts are outside the required SSD")


def install_tables(admin_client) -> None:
    """Explicit producer/operator setup; never invoked by Backtest/preflight."""
    policy = admin_client.execute("SELECT count() FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' AND disks=['live_market_ssd']").strip()
    if policy != "1":
        raise RuntimeError("Strategy 43 cannot install without its SSD-only policy")
    for statement in ddl():
        admin_client.execute(statement)
    verify_tables(admin_client)

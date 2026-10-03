"""Producer-owned Strategy 45 liquidity windows; Backtest is SELECT only."""
from hashlib import sha256
import json

STORAGE_POLICY = "live_market_ssd"
FACT_TABLE = "arte.strategy_forty_five_liquidity_fact_v1"
COVERAGE_TABLE = "arte.strategy_forty_five_liquidity_coverage_v1"
BASE = (("source_build_id", "String"), ("session_date", "Date"),
        ("ticker", "LowCardinality(String)"), ("derivation_attempt_id", "UUID"))
FACT_COLUMNS = BASE + (("fact_id", "UUID"), ("boundary_ms", "UInt32"),
    ("history_ready", "UInt8"), ("trades_60s", "Nullable(UInt64)"),
    ("volume_300s", "Nullable(Float64)"))
COVERAGE_COLUMNS = BASE + (("product_digest", "FixedString(64)"),
    ("bars_attempt_id", "UUID"), ("source_market_token", "FixedString(64)"),
    ("identity_token", "FixedString(64)"), ("snapshot_hash", "FixedString(64)"),
    ("session_end_ms", "UInt32"), ("fact_count", "UInt32"),
    ("fact_hash", "FixedString(64)"), ("certified_at", "DateTime64(6, 'UTC')"))
CONTRACTS = {FACT_TABLE: FACT_COLUMNS, COVERAGE_TABLE: COVERAGE_COLUMNS}
SORT_KEYS = {table: "source_build_id,session_date,ticker,derivation_attempt_id"
    + (",boundary_ms" if table == FACT_TABLE else "") for table in CONTRACTS}
PRODUCT_DIGEST = sha256(json.dumps(dict(version=1, schemas=CONTRACTS,
    source="certified arte.bars_v1 completed 30s", window="last 2/10 completed candles",
    origin="04:00 America/New_York", sparse="zero activity only after complete source certification",
    insufficient_history="not ready before ten full candles", transport="Arrow IEEE Float64"),
    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ddl():
    return tuple(f"CREATE TABLE IF NOT EXISTS {table} ("
        + ",".join(f"{name} {kind}" for name, kind in columns)
        + ") ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) "
        + f"ORDER BY ({SORT_KEYS[table]}) SETTINGS storage_policy='{STORAGE_POLICY}'"
        for table, columns in CONTRACTS.items())


def verify_tables(client):
    def rows(sql):
        return [json.loads(line) for line in client.execute(sql + " FORMAT JSONEachRow").splitlines() if line.strip()]
    if rows("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'") != [{"disks": [STORAGE_POLICY]}]:
        raise RuntimeError("Strategy 45 requires the explicit SSD-only policy")
    names = ",".join("'" + table.split(".")[1] + "'" for table in CONTRACTS)
    found = rows("SELECT name,engine,storage_policy,partition_key,sorting_key FROM system.tables "
                 f"WHERE database='arte' AND name IN ({names})")
    if {"arte." + r["name"] for r in found} != set(CONTRACTS) or len(found) != len(CONTRACTS):
        raise RuntimeError("Strategy 45 liquidity tables are absent or ambiguous")
    for row in found:
        table = "arte." + row["name"]
        if (row["engine"] != "MergeTree" or row["storage_policy"] != STORAGE_POLICY
                or "".join(row["partition_key"].split()) != "toYYYYMM(session_date)"
                or "".join(row["sorting_key"].split()) != SORT_KEYS[table]):
            raise RuntimeError("Strategy 45 liquidity layout differs")
        cols = rows(f"SELECT name,type FROM system.columns WHERE database='arte' AND table='{row['name']}' ORDER BY position")
        if tuple((c["name"], c["type"]) for c in cols) != CONTRACTS[table]:
            raise RuntimeError("Strategy 45 liquidity columns differ")
    if rows(f"SELECT table FROM system.parts WHERE active AND database='arte' AND table IN ({names}) AND disk_name!='live_market_ssd' LIMIT 1"):
        raise RuntimeError("Strategy 45 liquidity parts are outside the required SSD")


def install_tables(admin):
    if admin.execute("SELECT count() FROM system.storage_policies WHERE policy_name='live_market_ssd' AND disks=['live_market_ssd']").strip() != "1":
        raise RuntimeError("Strategy 45 SSD-only policy is absent")
    for sql in ddl():
        admin.execute(sql)
    verify_tables(admin)

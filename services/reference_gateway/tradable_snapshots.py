"""Immutable pre-open tradable populations and their session certificates.

The current identity graph may be published only for an upcoming exchange
session. Historical repair copies an actual retained publication, never a
fresh query of today's identity graph with an old date stamped onto it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import ClickHouseHttpClient


EASTERN = ZoneInfo("America/New_York")
REVISION = "preopen-tradable-snapshot-v3"
SNAPSHOT_ID_REVISION = "preopen-tradable-snapshot-v2"
SNAPSHOTS = "feature_tradable_universe_snapshot_v2"
COVERAGE = "feature_tradable_universe_snapshot_coverage_v2"
SOURCE_COLUMNS = "ticker,symbol_id,listing_id,security_id,is_tradable,exclusion_reason,source_run_id,inserted_at"
SOURCE_HASH = "sum(cityHash64(tuple(ticker,symbol_id,listing_id,security_id,is_tradable,exclusion_reason,source_run_id,inserted_at)))"


def sql_literal(value: object) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def session_cutoff(day: date) -> datetime:
    return datetime.combine(day, time(4), EASTERN).astimezone(UTC)


def target_session(captured_at_utc: datetime) -> date:
    """First XNYS session whose 04:00 ET causal cutoff is still in the future."""
    import pandas_market_calendars as mcal

    stamp = captured_at_utc.astimezone(UTC)
    local = stamp.astimezone(EASTERN)
    calendar = mcal.get_calendar("XNYS")
    sessions = calendar.schedule(start_date=local.date(), end_date=local.date() + timedelta(days=14)).index
    for session in sessions:
        day = session.date()
        if stamp < session_cutoff(day):
            return day
    raise ValueError("No upcoming exchange session within 14 days")


def query(client: ClickHouseHttpClient, statement: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(statement + " FORMAT JSONEachRow").splitlines()]


def ensure_schema(client: ClickHouseHttpClient, database: str = "q_live") -> None:
    if not database.replace("_", "").isalnum():
        raise ValueError("Invalid database name")
    policy = query(client, "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if len(policy) != 1 or policy[0]["disks"] != ["live_market_ssd"]:
        raise RuntimeError("Required live_market_ssd storage policy is unavailable")
    client.execute(f"""CREATE TABLE IF NOT EXISTS {database}.{SNAPSHOTS} (
        session_date Date, snapshot_id String, source_universe_date Date,
        ticker LowCardinality(String), symbol_id String, listing_id String,
        security_id String, is_tradable UInt8, exclusion_reason Nullable(String),
        source_run_id String, captured_at_utc DateTime64(3,'UTC'))
        ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
        ORDER BY (session_date,snapshot_id,ticker,listing_id)
        SETTINGS storage_policy='live_market_ssd'""")
    client.execute(f"""CREATE TABLE IF NOT EXISTS {database}.{COVERAGE} (
        session_date Date, snapshot_id String, source_universe_date Date,
        captured_at_utc DateTime64(3,'UTC'), cutoff_utc DateTime64(3,'UTC'),
        available_at_utc DateTime64(3,'UTC'),
        row_count UInt64, tradable_count UInt64, source_hash UInt64,
        revision String, status LowCardinality(String), certified_at_utc DateTime64(3,'UTC'))
        ENGINE=ReplacingMergeTree(certified_at_utc)
        PARTITION BY toYYYYMM(session_date) ORDER BY session_date
        SETTINGS storage_policy='live_market_ssd'""")
    client.execute(f"ALTER TABLE {database}.{COVERAGE} ADD COLUMN IF NOT EXISTS available_at_utc DateTime64(3,'UTC') DEFAULT toDateTime64('1970-01-01 00:00:00.000',3,'UTC')")
    tables = query(client, f"SELECT name,storage_policy FROM system.tables WHERE database={sql_literal(database)} AND name IN ({sql_literal(SNAPSHOTS)},{sql_literal(COVERAGE)})")
    if len(tables) != 2 or any(row["storage_policy"] != "live_market_ssd" for row in tables):
        raise RuntimeError("Snapshot tables must use live_market_ssd")
    misplaced = query(client, f"SELECT table,disk_name FROM system.parts WHERE active AND database={sql_literal(database)} AND table IN ({sql_literal(SNAPSHOTS)},{sql_literal(COVERAGE)}) AND disk_name!='live_market_ssd' LIMIT 1")
    if misplaced:
        raise RuntimeError("Snapshot table has active parts outside live_market_ssd")


def source_evidence(client: ClickHouseHttpClient, database: str, source_day: date) -> dict:
    rows = query(client, f"""SELECT count() AS n, countIf(is_tradable=1) AS tradable,
        uniqExact(tuple(ticker,listing_id)) AS keys, uniqExact(source_run_id) AS runs,
        min(source_run_id) AS run_id, min(inserted_at) AS first_insert,
        max(inserted_at) AS last_insert, {SOURCE_HASH} AS source_hash
        FROM {database}.feature_tradable_universe_v1 FINAL
        WHERE universe_date=toDate({sql_literal(source_day)})""")
    evidence = rows[0]
    if not int(evidence["n"]) or int(evidence["n"]) != int(evidence["keys"]) or int(evidence["runs"]) != 1:
        raise ValueError(f"{source_day}: legacy publication is missing or ambiguous")
    if evidence["first_insert"] != evidence["last_insert"]:
        raise ValueError(f"{source_day}: legacy publication has mixed capture clocks")
    return evidence


def publish_retained_snapshot(client: ClickHouseHttpClient, database: str, source_day: date,
                              *, available_at_utc: datetime, expected_session: date | None = None) -> dict:
    """Certify only a retained historical publication captured before the open."""
    ensure_schema(client, database)
    evidence = source_evidence(client, database, source_day)
    captured = datetime.fromisoformat(evidence["last_insert"]).replace(tzinfo=UTC)
    session = target_session(captured)
    if expected_session is not None and session != expected_session:
        raise ValueError(f"{source_day}: captured snapshot belongs to {session}, not {expected_session}")
    cutoff = session_cutoff(session)
    available = available_at_utc.astimezone(UTC)
    if captured >= cutoff or available >= cutoff or available < captured:
        raise ValueError(f"{source_day}: capture or publication was outside {session} pre-open cutoff")
    identity = f"{SNAPSHOT_ID_REVISION}|{session}|{source_day}|{evidence['run_id']}|{evidence['source_hash']}"
    snapshot_id = hashlib.sha256(identity.encode()).hexdigest()
    existing = query(client, f"SELECT snapshot_id,captured_at_utc,available_at_utc,revision FROM {database}.{COVERAGE} FINAL WHERE session_date=toDate({sql_literal(session)}) AND status='certified'")
    if existing and existing[0]["snapshot_id"] != snapshot_id:
        prior_capture = datetime.fromisoformat(existing[0]["captured_at_utc"]).replace(tzinfo=UTC)
        if prior_capture >= captured:
            raise ValueError(f"{session}: a newer or equal certified snapshot already exists")
    selected = f"session_date=toDate({sql_literal(session)}) AND snapshot_id={sql_literal(snapshot_id)}"
    observed = query(client, f"SELECT count() AS n,{SOURCE_HASH.replace('inserted_at','captured_at_utc')} AS source_hash FROM {database}.{SNAPSHOTS} WHERE {selected}")[0]
    if int(observed["n"]) == 0:
        client.execute(f"""INSERT INTO {database}.{SNAPSHOTS}
            (session_date,snapshot_id,source_universe_date,ticker,symbol_id,listing_id,security_id,is_tradable,exclusion_reason,source_run_id,captured_at_utc)
            SELECT toDate({sql_literal(session)}),{sql_literal(snapshot_id)},universe_date,
                   ticker,symbol_id,listing_id,security_id,is_tradable,exclusion_reason,source_run_id,inserted_at
            FROM {database}.feature_tradable_universe_v1 FINAL
            WHERE universe_date=toDate({sql_literal(source_day)})""")
        observed = query(client, f"SELECT count() AS n,{SOURCE_HASH.replace('inserted_at','captured_at_utc')} AS source_hash FROM {database}.{SNAPSHOTS} WHERE {selected}")[0]
    if int(observed["n"]) != int(evidence["n"]) or int(observed["source_hash"]) != int(evidence["source_hash"]):
        raise RuntimeError(f"{source_day}: snapshot copy failed integrity check; certificate withheld")
    if not existing or existing[0]["snapshot_id"] != snapshot_id or existing[0]['revision'] != REVISION:
        client.execute(f"""INSERT INTO {database}.{COVERAGE} VALUES
            (toDate({sql_literal(session)}),{sql_literal(snapshot_id)},toDate({sql_literal(source_day)}),
             toDateTime64({sql_literal(captured.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},3,'UTC'),
             toDateTime64({sql_literal(cutoff.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},3,'UTC'),
             toDateTime64({sql_literal(available.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},3,'UTC'),
             {int(evidence['n'])},{int(evidence['tradable'])},{int(evidence['source_hash'])},
             {sql_literal(REVISION)},'certified',now64(3))""")
    ensure_schema(client, database)
    return dict(session_date=str(session),source_universe_date=str(source_day),snapshot_id=snapshot_id,
                rows=int(evidence["n"]),tradable=int(evidence["tradable"]),
                captured_at_utc=captured.isoformat(),available_at_utc=available.isoformat())


def record_missing_sessions(client: ClickHouseHttpClient, database: str, start: date, end: date) -> list[str]:
    """Persist unresolved past-session gaps without fabricating historical rows."""
    import pandas_market_calendars as mcal

    ensure_schema(client, database)
    sessions = [stamp.date() for stamp in mcal.get_calendar("XNYS").schedule(start_date=start,end_date=end).index]
    known = {date.fromisoformat(row["session_date"]) for row in query(client,
        f"SELECT session_date FROM {database}.{COVERAGE} FINAL WHERE session_date BETWEEN toDate({sql_literal(start)}) AND toDate({sql_literal(end)}) AND revision={sql_literal(REVISION)}")}
    now = datetime.now(UTC)
    missing = [day for day in sessions if day not in known and session_cutoff(day) < now]
    for day in missing:
        cutoff = session_cutoff(day).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        client.execute(f"""INSERT INTO {database}.{COVERAGE} VALUES
            (toDate({sql_literal(day)}),'',toDate({sql_literal(day)}),
             toDateTime64('1970-01-01 00:00:00.000',3,'UTC'),
             toDateTime64({sql_literal(cutoff)},3,'UTC'),
             toDateTime64('1970-01-01 00:00:00.000',3,'UTC'),0,0,0,
             {sql_literal(REVISION)},'unresolved_no_preopen_capture',now64(3))""")
    return [str(day) for day in missing]

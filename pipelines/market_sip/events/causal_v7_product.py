"""Producer-owned causal V7 views from certified, completed 1s market-day bars.

This module is outside the future-migration ``arte/`` source tree.  The
producer may publish to the current arte database; Backtest only reads pinned,
complete attempts.  Retrospective structural_levels_v7 rows are never an
intraday input.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4

from zoneinfo import ZoneInfo

from src.market_engine.causal_v7_contract import (
    COVERAGE_TABLE, LEVEL_TABLE, SOURCE_POLICY, STATE_TABLE, STORAGE_POLICY,
)

NEW_YORK = ZoneInfo("America/New_York")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def session_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=NEW_YORK)


def bar_cutoff(day: date, bucket_index: int) -> int:
    """Producer bucket_index counts from midnight ET, not the 04:00 open."""
    if not 14_700 <= bucket_index < 72_000:
        raise ValueError("V7 1s input must start at 04:05 ET and end before 20:00 ET")
    return int((session_midnight(day) + timedelta(seconds=bucket_index + 1)).timestamp())


def input_bar(day: date, row: Mapping[str, Any]) -> dict[str, float]:
    if int(row.get("price_valid") or 0) != 1:
        raise ValueError("V7 input requires a price-eligible completed 1s bar")
    value = {
        "t": float(bar_cutoff(day, int(row["bucket_index"]))),
        "open": int(row["open_int"]) / 10_000,
        "high": int(row["high_int"]) / 10_000,
        "low": int(row["low_int"]) / 10_000,
        "close": int(row["close_int"]) / 10_000,
        "volume": float(row["volume"]),
    }
    if (value["low"] <= 0 or value["low"] > min(value["open"], value["close"])
            or value["high"] < max(value["open"], value["close"])
            or value["volume"] < 0):
        raise ValueError("V7 input has invalid completed-bar geometry")
    return value


def input_hash(day: date, rows: Iterable[Mapping[str, Any]]) -> str:
    checksum = sha256()
    for row in rows:
        checksum.update((canonical_json(input_bar(day, row)) + "\n").encode("utf-8"))
    return checksum.hexdigest()


def materialize(
    *, day: date, ticker: str, engine: Any, bars: Iterable[Mapping[str, Any]],
    provenance: Mapping[str, Any], source_hash: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Observe only completed bars, retaining small state and sparse level views.

    The seed is visible from 04:00 ET.  Bars before 04:05 are excluded to match
    the existing QMD V7 historical input policy.  A level view is persisted
    only when its canonical contents change; per-bar metadata remains exact.
    """
    from src.market_engine.v7_qmd import projection

    opening = int((session_midnight(day) + timedelta(hours=4)).timestamp())
    states: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []
    input_digest = sha256()
    last_second = opening
    last_levels_hash = ""
    level_revision = -1

    def capture(cutoff: int) -> None:
        nonlocal last_levels_hash, level_revision
        snapshot = projection(engine, cutoff, dict(provenance), False)
        if (snapshot.get("as_of") != cutoff
                or float(snapshot.get("max_input_timestamp") or 0) > cutoff):
            raise ValueError("V7 projection crossed its completed-second cutoff")
        view = snapshot.pop("unified_levels")
        alias = snapshot.pop("qmd_structure_unified_levels")
        if view != alias:
            raise ValueError("V7 level aliases disagree")
        levels_hash = digest(view)
        second_index = int((datetime.fromtimestamp(cutoff, NEW_YORK) - session_midnight(day)).total_seconds())
        if levels_hash != last_levels_hash:
            level_revision += 1
            levels.append({
                "second_index": second_index, "level_revision": level_revision,
                "levels_json": canonical_json(view), "levels_hash": levels_hash,
            })
            last_levels_hash = levels_hash
        snapshot["source_audit"] = {
            "source": "arte.bars_v1", "source_policy": SOURCE_POLICY,
            "source_hash": source_hash, "consumed_through": cutoff,
        }
        states.append({
            "second_index": second_index, "level_revision": level_revision,
            "metadata_json": canonical_json(snapshot), "metadata_hash": digest(snapshot),
        })

    capture(opening)
    for row in bars:
        bar = input_bar(day, row)
        cutoff = int(bar["t"])
        if cutoff <= last_second:
            raise ValueError("V7 input bars are duplicated or out of order")
        engine.update(bar, observed_at=cutoff)
        input_digest.update((canonical_json(bar) + "\n").encode("utf-8"))
        capture(cutoff)
        last_second = cutoff
    return states, levels, input_digest.hexdigest()


def ddl() -> tuple[str, str, str]:
    shared = "build_id String, session_date Date, ticker LowCardinality(String), attempt_id UUID"
    return (
        f"""CREATE TABLE IF NOT EXISTS {STATE_TABLE} ({shared},
            second_index UInt32, level_revision UInt32,
            metadata_json String CODEC(ZSTD(3)), metadata_hash FixedString(64))
            ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
            ORDER BY (build_id,session_date,ticker,attempt_id,second_index)
            SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {LEVEL_TABLE} ({shared},
            second_index UInt32, level_revision UInt32,
            levels_json String CODEC(ZSTD(3)), levels_hash FixedString(64))
            ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
            ORDER BY (build_id,session_date,ticker,attempt_id,level_revision)
            SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} ({shared},
            source_bars_attempt UUID, source_policy String, source_hash FixedString(64),
            input_hash FixedString(64), seed_checkpoint_hash String, catalog_hash String,
            producer_hash FixedString(64),
            state_rows UInt32, state_hash String, level_rows UInt32, level_hash String,
            status LowCardinality(String), published_at DateTime64(3, 'UTC'))
            ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
            ORDER BY (build_id,session_date,ticker,attempt_id)
            SETTINGS storage_policy='{STORAGE_POLICY}'""",
    )


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def product_storage_preflight(client: Any, *, create: bool = False) -> None:
    """Fail before writes if the SSD-only policy or existing placement differs."""
    policies = client.query(
        "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'",
        "v7_storage_policy",
    )
    if not policies or any(row["disks"] != [STORAGE_POLICY] for row in policies):
        raise ValueError("Causal V7 requires the SSD-only live_market_ssd policy")
    names = ("causal_v7_state_1s_v1", "causal_v7_levels_v1", "causal_v7_coverage_v1")
    quoted = ",".join(sql_literal(name) for name in names)
    existing = client.query(
        f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({quoted})",
        "v7_table_policy",
    )
    if any(row["storage_policy"] != STORAGE_POLICY for row in existing):
        raise ValueError("Causal V7 table has an incorrect storage policy; explicit migration required")
    misplaced = client.query(
        "SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
        f"AND table IN ({quoted}) AND disk_name!='live_market_ssd' LIMIT 1",
        "v7_part_placement",
    )
    if misplaced:
        raise ValueError("Causal V7 has active parts outside live_market_ssd")
    if create:
        for statement in ddl():
            client.query(statement, "v7_schema", read=False)
        product_storage_preflight(client)
    elif {row["name"] for row in existing} != set(names):
        raise ValueError("Causal V7 product tables are incomplete")


def _evidence(client: Any, table: str, build_id: str, day: date, ticker: str, attempt: str,
              key: str) -> dict[str, Any]:
    rows = client.query(
        "SELECT count() AS n,"
        f"uniqExact({key}) AS unique_keys,toString(sum(cityHash64(tuple(*)))) AS hash "
        f"FROM {table} WHERE build_id={sql_literal(build_id)} "
        f"AND session_date=toDate({sql_literal(day.isoformat())}) "
        f"AND ticker={sql_literal(ticker)} AND attempt_id=toUUID({sql_literal(attempt)})",
        "v7_integrity",
    )
    if len(rows) != 1 or int(rows[0]["n"]) != int(rows[0]["unique_keys"]):
        raise ValueError(f"Causal V7 {table} contains duplicate keys")
    return rows[0]


def _insert_rows(client: Any, table: str, rows: list[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), 512):
        packet = rows[offset:offset + 512]
        body = "\n".join(canonical_json(row) for row in packet)
        client.query(f"INSERT INTO {table} FORMAT JSONEachRow\n{body}", "v7_insert", read=False)


def published_coverage(client: Any, build_id: str, day: date, ticker: str) -> dict[str, Any] | None:
    rows = client.query(
        f"SELECT * FROM {COVERAGE_TABLE} WHERE build_id={sql_literal(build_id)} "
        f"AND session_date=toDate({sql_literal(day.isoformat())}) "
        f"AND ticker={sql_literal(ticker)}",
        "v7_coverage",
    )
    if len(rows) > 1:
        raise ValueError(f"Duplicate causal V7 publications for {day} {ticker}")
    return rows[0] if rows else None


def publish(
    client: Any, *, build_id: str, day: date, ticker: str, bars_attempt: str,
    source_hash: str, seed_checkpoint_hash: str, catalog_hash: str,
    producer_hash: str, input_hash: str,
    states: list[dict[str, Any]], levels: list[dict[str, Any]],
) -> tuple[str, str]:
    """Publish one immutable attempt, then certify it after read-back integrity."""
    if _TICKER.fullmatch(ticker) is None:
        raise ValueError("Invalid causal V7 ticker")
    if any(len(value) != 64 for value in (source_hash, producer_hash, input_hash)):
        raise ValueError("Causal V7 source and producer hashes must be SHA-256")
    existing = published_coverage(client, build_id, day, ticker)
    if existing:
        if (existing["status"] != "complete" or existing["source_bars_attempt"] != bars_attempt
                or existing["source_hash"] != source_hash or existing["input_hash"] != input_hash
                or existing["seed_checkpoint_hash"] != seed_checkpoint_hash
                or existing["catalog_hash"] != catalog_hash
                or existing["producer_hash"] != producer_hash
                or existing["source_policy"] != SOURCE_POLICY):
            raise ValueError(f"Existing causal V7 publication for {day} {ticker} changed")
        attempt = str(existing["attempt_id"])
        for table, key, prefix in ((STATE_TABLE, "second_index", "state"),
                                   (LEVEL_TABLE, "level_revision", "level")):
            proof = _evidence(client, table, build_id, day, ticker, attempt, key)
            if (int(proof["n"]) != int(existing[f"{prefix}_rows"])
                    or str(proof["hash"]) != str(existing[f"{prefix}_hash"])):
                raise ValueError(f"Existing causal V7 {prefix} publication failed integrity")
        return "skipped", attempt

    if not states or not levels or states[0]["level_revision"] != 0:
        raise ValueError("Causal V7 product lacks its opening seed")

    attempt = str(uuid4())
    shared = {"build_id": build_id, "session_date": day.isoformat(),
              "ticker": ticker, "attempt_id": attempt}
    _insert_rows(client, STATE_TABLE, [{**shared, **row} for row in states])
    _insert_rows(client, LEVEL_TABLE, [{**shared, **row} for row in levels])
    state_proof = _evidence(client, STATE_TABLE, build_id, day, ticker, attempt, "second_index")
    level_proof = _evidence(client, LEVEL_TABLE, build_id, day, ticker, attempt, "level_revision")
    if int(state_proof["n"]) != len(states) or int(level_proof["n"]) != len(levels):
        raise ValueError("Causal V7 staged attempt is incomplete; it was not published")
    coverage = {
        **shared, "source_bars_attempt": bars_attempt,
        "source_policy": SOURCE_POLICY, "source_hash": source_hash,
        "input_hash": input_hash, "seed_checkpoint_hash": seed_checkpoint_hash,
        "catalog_hash": catalog_hash, "producer_hash": producer_hash,
        "state_rows": len(states), "state_hash": str(state_proof["hash"]),
        "level_rows": len(levels), "level_hash": str(level_proof["hash"]),
        "status": "complete", "published_at": datetime.now(timezone.utc).isoformat(),
    }
    _insert_rows(client, COVERAGE_TABLE, [coverage])
    confirmed = published_coverage(client, build_id, day, ticker)
    if confirmed is None or str(confirmed["attempt_id"]) != attempt:
        raise ValueError("Causal V7 coverage publication was not confirmed")
    return "completed", attempt

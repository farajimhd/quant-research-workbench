"""SELECT-only causal V7 certification and as-of snapshots for Backtest.

No producer, schema, or INSERT imports are reachable from this module.  The
dedicated Backtest ClickHouse account must have SELECT on the three product
tables and cannot publish or repair them.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time
from hashlib import sha256
import json
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.backend.backtest_market_data import CertifiedMarketDayPlan, assert_select_only
from src.market_engine.causal_v7_contract import (
    COVERAGE_TABLE, LEVEL_TABLE, SOURCE_POLICY, STATE_TABLE,
)


NEW_YORK = ZoneInfo("America/New_York")


def _literal(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CausalV7Unit:
    build_id: str
    session_date: str
    ticker: str
    attempt_id: str
    source_bars_attempt: str
    source_hash: str
    input_hash: str
    seed_checkpoint_hash: str
    catalog_hash: str
    producer_hash: str
    state_rows: int
    state_hash: str
    level_rows: int
    level_hash: str


@dataclass(frozen=True, slots=True)
class CertifiedV7Plan:
    build_id: str
    catalog_hash: str
    units: tuple[CausalV7Unit, ...]
    token: str

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "causal-v7-backtest-plan-v1",
            "build_id": self.build_id, "catalog_hash": self.catalog_hash,
            "unit_count": len(self.units), "token": self.token,
        }


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(assert_select_only(sql)).splitlines()
            if line.strip()]


def certified_plan(market: CertifiedMarketDayPlan, catalog_hash: str,
                   client: Any) -> CertifiedV7Plan:
    bars = {(unit.session_date, unit.ticker): unit for unit in market.units
            if unit.stage == "bars"}
    if not bars or not catalog_hash:
        raise ValueError("Causal V7 requires pinned market-day bars and a catalog hash")
    units: list[CausalV7Unit] = []
    for day in market.sessions:
        tickers = sorted(ticker for session, ticker in bars if session == day)
        for offset in range(0, len(tickers), 256):
            batch = tickers[offset:offset + 256]
            names = ",".join(_literal(ticker) for ticker in batch)
            rows = _rows(client,
                f"SELECT * FROM {COVERAGE_TABLE} WHERE build_id={_literal(market.build_id)} "
                f"AND session_date=toDate({_literal(day)}) AND ticker IN ({names}) "
                "FORMAT JSONEachRow")
            if len(rows) != len(batch) or {str(row["ticker"]) for row in rows} != set(batch):
                raise ValueError(f"Causal V7 coverage is incomplete or duplicated for {day}")
            for row in rows:
                ticker = str(row["ticker"])
                source = bars[(day, ticker)]
                if (row["status"] != "complete" or row["source_policy"] != SOURCE_POLICY
                        or str(row["source_bars_attempt"]) != source.attempt_id
                        or str(row["source_hash"]) != source.source_hash
                        or str(row["catalog_hash"]) != catalog_hash
                        or int(row["state_rows"]) < 1 or int(row["level_rows"]) < 1
                        or any(len(str(row[field])) != 64 for field in
                               ("source_hash", "input_hash", "producer_hash"))):
                    raise ValueError(f"Causal V7 publication differs from pinned inputs: {day} {ticker}")
                units.append(CausalV7Unit(
                    build_id=market.build_id, session_date=day, ticker=ticker,
                    attempt_id=str(row["attempt_id"]),
                    source_bars_attempt=source.attempt_id,
                    source_hash=source.source_hash,
                    input_hash=str(row["input_hash"]),
                    seed_checkpoint_hash=str(row["seed_checkpoint_hash"]),
                    catalog_hash=catalog_hash,
                    producer_hash=str(row["producer_hash"]),
                    state_rows=int(row["state_rows"]), state_hash=str(row["state_hash"]),
                    level_rows=int(row["level_rows"]), level_hash=str(row["level_hash"]),
                ))
    result = tuple(sorted(units, key=lambda unit: (unit.session_date, unit.ticker)))
    for table, row_count, row_hash, key in (
        (STATE_TABLE, "state_rows", "state_hash", "second_index"),
        (LEVEL_TABLE, "level_rows", "level_hash", "level_revision"),
    ):
        for day in market.sessions:
            day_units = [unit for unit in result if unit.session_date == day]
            for offset in range(0, len(day_units), 256):
                batch = day_units[offset:offset + 256]
                names = ",".join(_literal(unit.ticker) for unit in batch)
                rows = _rows(client,
                    "SELECT ticker,toString(attempt_id) AS attempt_id,count() AS n,"
                    f"uniqExact({key}) AS unique_keys,"
                    "toString(sum(cityHash64(tuple(*)))) AS hash "
                    f"FROM {table} WHERE build_id={_literal(market.build_id)} "
                    f"AND session_date=toDate({_literal(day)}) AND ticker IN ({names}) "
                    "GROUP BY ticker,attempt_id FORMAT JSONEachRow")
                actual = {(str(row["ticker"]), str(row["attempt_id"])): row for row in rows}
                if len(actual) != len(rows):
                    raise ValueError(f"Causal V7 {table} contains duplicate integrity groups")
                for unit in batch:
                    proof = actual.get((unit.ticker, unit.attempt_id))
                    if (proof is None or int(proof["n"]) != getattr(unit, row_count)
                            or int(proof["unique_keys"]) != int(proof["n"])
                            or str(proof["hash"]) != getattr(unit, row_hash)):
                        raise ValueError(f"Causal V7 {table} integrity changed: {day} {unit.ticker}")
    token = _hash([{
        name: getattr(unit, name) for name in CausalV7Unit.__dataclass_fields__
    } for unit in result])
    return CertifiedV7Plan(market.build_id, catalog_hash, result, token)


class CausalV7Cursor:
    """Bounded one-ticker immutable working set; no on-demand calculation."""

    def __init__(self, unit: CausalV7Unit, client: Any) -> None:
        selection = (
            f"build_id={_literal(unit.build_id)} "
            f"AND session_date=toDate({_literal(unit.session_date)}) "
            f"AND ticker={_literal(unit.ticker)} "
            f"AND attempt_id=toUUID({_literal(unit.attempt_id)})"
        )
        states = _rows(client, f"SELECT second_index,level_revision,metadata_json,metadata_hash "
                      f"FROM {STATE_TABLE} WHERE {selection} ORDER BY second_index FORMAT JSONEachRow")
        levels = _rows(client, f"SELECT second_index,level_revision,levels_json,levels_hash "
                      f"FROM {LEVEL_TABLE} WHERE {selection} ORDER BY level_revision FORMAT JSONEachRow")
        if len(states) != unit.state_rows or len(levels) != unit.level_rows:
            raise ValueError("Causal V7 cursor lost pinned rows")
        self.unit = unit
        self.seconds: list[int] = []
        self.states: list[tuple[int, dict[str, Any]]] = []
        self.levels: dict[int, list[dict[str, Any]]] = {}
        for row in levels:
            revision = int(row["level_revision"])
            view = json.loads(row["levels_json"])
            if (revision in self.levels or revision != len(self.levels)
                    or _hash(view) != str(row["levels_hash"])):
                raise ValueError("Causal V7 level revision changed")
            self.levels[revision] = view
        for row in states:
            second = int(row["second_index"])
            revision = int(row["level_revision"])
            metadata = json.loads(row["metadata_json"])
            if (self.seconds and second <= self.seconds[-1]) or revision not in self.levels:
                raise ValueError("Causal V7 state order or level revision changed")
            if _hash(metadata) != str(row["metadata_hash"]):
                raise ValueError("Causal V7 state metadata changed")
            audit = metadata.get("source_audit") or {}
            if (audit.get("source_hash") != unit.source_hash
                    or audit.get("source_policy") != SOURCE_POLICY
                    or int(audit.get("consumed_through") or 0) != int(
                        (datetime.combine(date.fromisoformat(unit.session_date), time.min,
                                          tzinfo=NEW_YORK)).timestamp()) + second):
                raise ValueError("Causal V7 state source audit changed")
            self.seconds.append(second)
            self.states.append((revision, metadata))
        if self.seconds[0] != 14_400 or 0 not in self.levels:
            raise ValueError("Causal V7 cursor lacks the 04:00 seed")
        if any(second < 14_701 or second > 72_000 for second in self.seconds[1:]):
            raise ValueError("Causal V7 state lies outside the completed-bar session")

    def snapshot(self, as_of: datetime) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise ValueError("Causal V7 cutoff must be timezone-aware")
        local = as_of.astimezone(NEW_YORK)
        if local.date().isoformat() != self.unit.session_date:
            raise ValueError("Causal V7 cursor cannot cross sessions")
        second = int((local - datetime.combine(local.date(), time.min,
                                               tzinfo=NEW_YORK)).total_seconds())
        index = bisect_right(self.seconds, second) - 1
        if index < 0:
            raise ValueError("Causal V7 snapshot precedes the 04:00 seed")
        revision, source = self.states[index]
        if float(source["max_input_timestamp"]) > as_of.timestamp():
            raise ValueError("Causal V7 state contains future input")
        levels = self.levels[revision]
        return {
            **source, "as_of": as_of.timestamp(),
            "unified_levels": levels, "qmd_structure_unified_levels": levels,
        }

"""Restart-safe coverage-last publication for one Strategy 43 ticker/session.

Market, reference and V7 certification happen before this producer is called.
Uncertain inserts are resolved by exact readback using the SAME attempt; a
retry never silently appends duplicate children or replaces a sealed product.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import struct
from uuid import UUID, NAMESPACE_URL, uuid5

import polars as pl

from pipelines.market_sip.events.market_day_sql import literal
from pipelines.strategy_one.configuration_publisher import _insert_rows
from pipelines.strategy_one.strategy_forty_three_facts import derive_completed_features
from src.trading_runtime.strategy_forty_three_fact_schema import (
    COVERAGE_TABLE, FACT_COLUMNS, FACT_TABLE, POPULATION_COLUMNS, POPULATION_TABLE,
    PRODUCT_DIGEST, verify_tables,
)
from src.trading_runtime.strategy_forty_three_source_codec import scalar_hash

FLOAT_FIELDS = tuple(name for name, kind in FACT_COLUMNS if "Float64" in kind)
_HEX = re.compile("[0-9a-f]{64}\\Z")


@dataclass(frozen=True, slots=True)
class PublicationSource:
    build_id: str
    session_date: str
    ticker: str
    attempt_id: str
    bars_attempt_id: str
    liquidity_attempt_id: str
    source_market_token: str
    source_v7_token: str
    source_snapshot_hash: str
    source_signal_query_hash: str
    listing_id: str
    symbol_id: str
    security_id: str
    conid: int
    admission_ms: int
    session_end_ms: int

    def validate(self):
        from datetime import date
        date.fromisoformat(self.session_date)
        for value in (self.attempt_id, self.bars_attempt_id, self.liquidity_attempt_id):
            if str(UUID(value)) != value:
                raise ValueError("Strategy 43 producer attempt UUID must be canonical")
        if (not self.build_id or not self.ticker or self.ticker != self.ticker.upper()
                or self.ticker == "LGHL" or not self.listing_id
                or not self.symbol_id or not self.security_id
                or type(self.conid) is not int or not 0 < self.conid < 2**64
                or any(not _HEX.fullmatch(value) for value in (
                    self.source_market_token, self.source_v7_token, self.source_snapshot_hash,
                    self.source_signal_query_hash))
                or type(self.session_end_ms) is not int or self.session_end_ms % 1000
                or not 1000 <= self.session_end_ms <= 57_600_000
                or type(self.admission_ms) is not int or self.admission_ms % 1000
                or not 0 < self.admission_ms <= self.session_end_ms):
            raise ValueError("Strategy 43 publication requires pinned tradable source identities")

    def base(self):
        return dict(source_build_id=self.build_id, session_date=self.session_date,
            ticker=self.ticker, derivation_attempt_id=str(UUID(self.attempt_id)))


def prepare_rows(source: PublicationSource, seconds: pl.DataFrame):
    source.validate()
    frame = derive_completed_features(seconds)
    if frame["boundary_ms"][-1] != source.session_end_ms:
        raise ValueError("Strategy 43 publication grid does not reach its declared session end")
    rows = []
    for feature in frame.iter_rows(named=True):
        row = {**source.base(), "fact_id": str(uuid5(NAMESPACE_URL,
            f"strategy-43-fact:{source.build_id}:{source.session_date}:{source.ticker}:"
            f"{source.attempt_id}:{feature['boundary_ms']}")), **feature}
        row["observed"] = int(row["observed"])
        rows.append(row)
    population = {**source.base(), "listing_id": source.listing_id,
        "symbol_id": source.symbol_id, "security_id": source.security_id, "conid": source.conid,
        "admission_ms": source.admission_ms, "source_snapshot_hash": source.source_snapshot_hash,
        "source_market_token": source.source_market_token,
        "source_signal_query_hash": source.source_signal_query_hash}
    coverage = {**source.base(), "product_digest": PRODUCT_DIGEST,
        "bars_attempt_id": str(UUID(source.bars_attempt_id)),
        "liquidity_attempt_id": str(UUID(source.liquidity_attempt_id)),
        "source_market_token": source.source_market_token,
        "population_hash": scalar_hash((population,)), "source_v7_token": source.source_v7_token,
        "session_end_ms": source.session_end_ms, "fact_count": len(rows), "fact_hash": scalar_hash(rows)}
    return rows, population, coverage


def _where(source, *, attempt=True):
    result = (f"source_build_id={literal(source.build_id)} AND "
        f"session_date=toDate({literal(source.session_date)}) AND ticker={literal(source.ticker)}")
    if attempt:
        result += f" AND derivation_attempt_id=toUUID({literal(source.attempt_id)})"
    return result


def read_rows(client, query):
    return [json.loads(line) for line in client.execute(query + " FORMAT JSONEachRow").splitlines()
            if line.strip()]


def read_facts(client, source):
    names = []
    for name, kind in FACT_COLUMNS:
        if name in FLOAT_FIELDS:
            names.append(f"reinterpretAsUInt64({name}) AS {name}_bits")
        elif kind in ("Date", "UUID"):
            names.append(f"toString({name}) AS {name}")
        else:
            names.append(name)
    rows = read_rows(client, f"SELECT {','.join(names)} FROM {FACT_TABLE} "
        f"WHERE {_where(source)} ORDER BY boundary_ms")
    for row in rows:
        for name in FLOAT_FIELDS:
            bits = row.pop(name + "_bits")
            row[name] = None if bits is None else struct.unpack("<d", struct.pack("<Q", int(bits)))[0]
    return rows


def read_population(client, source):
    result = read_rows(client, f"SELECT * FROM {POPULATION_TABLE} WHERE {_where(source)}")
    for row in result:
        for name in ("conid", "admission_ms"):
            row[name] = int(row[name])
    return result


def publish_unit(client, keeper, source: PublicationSource, seconds: pl.DataFrame, *, catalog_reader=None) -> str:
    """Publish after exact readback, preserving all previously certified units."""
    source.validate()
    verify_tables(catalog_reader if catalog_reader is not None else client)
    facts, population, expected = prepare_rows(source, seconds)
    lock = f"/trading/ownership/v1/strategy_forty_three_facts/{source.build_id}/{source.session_date}/{source.ticker}"
    keeper.create(lock, source.attempt_id.encode(), ephemeral=True, makepath=True)
    owner = keeper.exists(lock)
    if owner is None or not owner.ephemeralOwner:
        raise RuntimeError("Strategy 43 producer lock is not ephemeral")
    try:
        if not keeper.connected:
            raise RuntimeError("Strategy 43 producer Keeper ownership is lost")
        seals = read_rows(client, f"SELECT * FROM {COVERAGE_TABLE} WHERE {_where(source, attempt=False)}")
        if seals:
            if len(seals) != 1 or any(seals[0].get(key) != value for key, value in expected.items()):
                raise RuntimeError("Strategy 43 refuses to replace or reinterpret sealed coverage")
            if (read_population(client, source) != [population]
                    or read_facts(client, source) != facts
                    or not keeper.connected):
                raise RuntimeError("Strategy 43 sealed product readback differs")
            return scalar_hash((expected,))
        existing_population = read_population(client, source)
        if not existing_population:
            _insert_rows(client, POPULATION_TABLE, tuple(name for name, _ in POPULATION_COLUMNS), [population])
        elif existing_population != [population]:
            raise RuntimeError("Strategy 43 population attempt is incomplete or differs")
        existing_count = int(client.execute(f"SELECT count() FROM {FACT_TABLE} WHERE {_where(source)}").strip())
        if existing_count == 0:
            for start in range(0, len(facts), 4096):
                if not keeper.connected:
                    raise RuntimeError("Strategy 43 producer Keeper ownership is lost")
                _insert_rows(client, FACT_TABLE, tuple(name for name, _ in FACT_COLUMNS), facts[start:start + 4096])
        elif existing_count != len(facts):
            # A known exact prefix is restartable; any duplicate or changed
            # prefix fails closed. Missing suffix alone is not silently skipped.
            existing = read_facts(client, source)
            if not 0 < len(existing) < len(facts) or existing != facts[:len(existing)]:
                raise RuntimeError("Strategy 43 partial fact attempt cannot be resumed exactly")
            for start in range(len(existing), len(facts), 4096):
                if not keeper.connected:
                    raise RuntimeError("Strategy 43 producer Keeper ownership is lost")
                _insert_rows(client, FACT_TABLE, tuple(name for name, _ in FACT_COLUMNS), facts[start:start + 4096])
        actual = read_facts(client, source)
        if actual != facts or scalar_hash(actual) != expected["fact_hash"]:
            raise RuntimeError("Strategy 43 producer exact fact readback differs")
        if read_population(client, source) != [population]:
            raise RuntimeError("Strategy 43 producer population readback differs")
        if not seals:
            if not keeper.connected:
                raise RuntimeError("Strategy 43 producer Keeper ownership is lost")
            _insert_rows(client, COVERAGE_TABLE, tuple(expected) + ("certified_at",), [{**expected,
                "certified_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")}])
        published = read_rows(client, f"SELECT * FROM {COVERAGE_TABLE} WHERE {_where(source, attempt=False)}")
        if len(published) != 1 or any(published[0].get(key) != value for key, value in expected.items()):
            raise RuntimeError("Strategy 43 coverage publication receipt differs")
        return scalar_hash((expected,))
    finally:
        # A disconnected writer no longer owns the ephemeral node. In
        # particular, never delete a successor's lock after session expiry.
        if keeper.connected:
            current = keeper.exists(lock)
            if (current is not None
                    and current.ephemeralOwner == owner.ephemeralOwner
                    and current.czxid == owner.czxid):
                keeper.delete(lock, version=current.version)

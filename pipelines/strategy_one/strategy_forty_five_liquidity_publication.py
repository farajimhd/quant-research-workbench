"""Restart-safe, Keeper-owned, coverage-last Strategy 45 producer publication."""
from datetime import datetime, date, timezone
from uuid import uuid5, NAMESPACE_URL
import pyarrow as pa
import polars as pl
import struct

from .configuration_publisher import _insert_rows
from .strategy_forty_five_liquidity import derive_windows
from src.backend.backtest_strategy_forty_five_liquidity import rows, scope, read_facts
from src.backend.backtest_market_data import _literal, SESSION_OPEN_OFFSET_MS
from src.trading_runtime.strategy_forty_five_liquidity_schema import (
    FACT_COLUMNS, FACT_TABLE, COVERAGE_TABLE, PRODUCT_DIGEST)
from src.trading_runtime.strategy_forty_three_source_codec import scalar_hash


def insert_facts(writer, facts):
    types = {"String": pa.string(), "LowCardinality(String)": pa.string(), "Date": pa.date32(),
        "UUID": pa.string(), "UInt32": pa.uint32(), "UInt8": pa.uint8(),
        "Nullable(UInt64)": pa.uint64(), "Nullable(Float64)": pa.float64()}
    table = pa.Table.from_arrays([pa.array([date.fromisoformat(r[name]) if kind == "Date" else r[name]
        for r in facts], type=types[kind]) for name, kind in FACT_COLUMNS], names=[k for k, _ in FACT_COLUMNS])
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as stream:
        stream.write_table(table)
    writer.execute(f"INSERT INTO {FACT_TABLE} ({','.join(table.column_names)}) FORMAT ArrowStream\n".encode()
                   + sink.getvalue().to_pybytes())


def publish_unit(plan, ticker, reader, writer, keeper):
    unit, = [u for u in plan.market.units if u.stage == "bars" and u.ticker == ticker]
    day, build = plan.market.sessions[0], plan.market.build_id
    parent = dict(product_digest=PRODUCT_DIGEST, bars_attempt_id=unit.attempt_id,
        source_market_token=plan.market.token, identity_token=plan.identity.token,
        snapshot_hash=plan.snapshot_hash, session_end_ms=plan.session_end_ms,
        fact_count=plan.session_end_ms // 30000)
    attempt = str(uuid5(NAMESPACE_URL, "strategy45-windows:" + scalar_hash(({"build": build, "day": day, "ticker": ticker, **parent},))))
    selected = scope(build, day, ticker, attempt)
    bar_scope = (f"build_id={_literal(build)} AND session_date=toDate({_literal(day)}) AND ticker={_literal(ticker)} "
                 f"AND attempt_id=toUUID({_literal(unit.attempt_id)}) AND resolution_ms=30000 "
                 f"AND bucket_index>={SESSION_OPEN_OFFSET_MS // 30000} AND bucket_index<{(SESSION_OPEN_OFFSET_MS + plan.session_end_ms) // 30000}")
    raw = rows(reader, "SELECT (toInt64(bucket_index)+1)*30000-" + str(SESSION_OPEN_OFFSET_MS)
        + f" AS boundary_ms,reinterpretAsUInt64(volume) AS volume_bits,trade_count FROM arte.bars_v1 WHERE {bar_scope} ORDER BY bucket_index")
    for row in raw:
        row["volume"] = struct.unpack("<d", struct.pack("<Q", int(row.pop("volume_bits"))))[0]
        row["boundary_ms"] = int(row["boundary_ms"])
        row["trade_count"] = int(row["trade_count"])
    bars = pl.DataFrame(raw, schema={"boundary_ms": pl.Int64, "volume": pl.Float64, "trade_count": pl.UInt64})
    facts = derive_windows(bars, ticker=ticker, session_end_ms=plan.session_end_ms,
                          build=build, day=day, attempt=attempt).select([k for k, _ in FACT_COLUMNS]).to_dicts()
    expected = dict(source_build_id=build, session_date=day, ticker=ticker,
        derivation_attempt_id=attempt, **parent, fact_hash=scalar_hash(facts))
    lock = f"/trading/ownership/v1/strategy_forty_five_liquidity/{build}/{day}/{ticker}"
    keeper.create(lock, attempt.encode(), ephemeral=True, makepath=True)
    owner = keeper.exists(lock)
    if owner is None or not owner.ephemeralOwner:
        raise RuntimeError("Strategy 45 producer ownership is not ephemeral")
    try:
        if not keeper.connected:
            raise RuntimeError("Strategy 45 producer lost Keeper ownership")
        seals = rows(writer, f"SELECT * FROM {COVERAGE_TABLE} WHERE {scope(build, day, ticker)}")
        if seals and (len(seals) != 1 or any(str(seals[0].get(k)) != str(v) for k, v in expected.items())):
            raise RuntimeError("Strategy 45 refuses to replace certified liquidity")
        existing = read_facts(writer, selected)
        if existing != facts[:len(existing)] or len(existing) > len(facts):
            raise RuntimeError("Strategy 45 partial liquidity attempt differs")
        if seals and len(existing) != len(facts):
            raise RuntimeError("Strategy 45 certified liquidity is incomplete")
        for offset in range(len(existing), len(facts), 4096):
            if not keeper.connected:
                raise RuntimeError("Strategy 45 producer lost Keeper ownership")
            insert_facts(writer, facts[offset:offset + 4096])
        if read_facts(writer, selected) != facts:
            raise RuntimeError("Strategy 45 liquidity exact readback differs")
        if not seals:
            if not keeper.connected:
                raise RuntimeError("Strategy 45 producer lost Keeper ownership")
            _insert_rows(writer, COVERAGE_TABLE, tuple(expected) + ("certified_at",), [{**expected,
                "certified_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")}])
        seals = rows(writer, f"SELECT * FROM {COVERAGE_TABLE} WHERE {scope(build, day, ticker)}")
        if len(seals) != 1 or any(str(seals[0].get(k)) != str(v) for k, v in expected.items()):
            raise RuntimeError("Strategy 45 coverage receipt differs")
        return scalar_hash((expected,))
    finally:
        if keeper.connected:
            current = keeper.exists(lock)
            if current and current.ephemeralOwner == owner.ephemeralOwner and current.czxid == owner.czxid:
                keeper.delete(lock, version=current.version)

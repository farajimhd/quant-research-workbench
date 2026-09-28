"""Coverage-last publication of scalar intraday V7 clocks and intervals.

Only a producer principal may insert. A failed or uncertain child attempt is
never certified; Backtest can see only a separately verified coverage row.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from math import isfinite
from struct import pack, unpack
from typing import Any
from uuid import UUID, uuid4

from pipelines.market_sip.events.market_day_sql import literal
from pipelines.strategy_one.v7_interval_derivation import DerivedV7TickerDay
from src.trading_runtime.strategy_one_v7_interval_schema import (
    CLOCK_TABLE, COVERAGE_TABLE, INTERVAL_TABLE, PRODUCT_DIGEST,
    verify_tables,
)
from src.trading_runtime.strategy_one_v7_intervals import V7LevelInterval
from src.trading_runtime.strategy_one_v7_intervals import SESSION_MS
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_v7 import PROVISIONAL_SEED_POLICY


_HASH = re.compile(r"[0-9a-f]{64}\Z")


class V7ReadbackMismatch(RuntimeError):
    """Safe field-level discrepancy, without source prices or SQL."""


_INTERVAL_FIELDS = ("level_id", "ordinal", "valid_from_ms", "valid_to_ms",
                    "lower_bits", "upper_bits", "role", "transition_from",
                    "confirmed_at_ms", "historical")


def _readback_mismatch(actual_clocks: tuple[int, ...],
                       expected_clocks: tuple[int, ...],
                       actual_intervals: tuple[tuple[object, ...], ...],
                       expected_intervals: tuple[tuple[object, ...], ...]) -> str | None:
    if len(actual_clocks) != len(expected_clocks):
        return (f"clock count {len(actual_clocks)} != "
                f"{len(expected_clocks)}")
    for index, (actual, expected) in enumerate(zip(actual_clocks, expected_clocks)):
        if actual != expected:
            return f"clock index {index} differs"
    if len(actual_intervals) != len(expected_intervals):
        return (f"interval count {len(actual_intervals)} != "
                f"{len(expected_intervals)}")
    for index, (actual, expected) in enumerate(zip(actual_intervals,
                                                    expected_intervals)):
        for field, left, right in zip(_INTERVAL_FIELDS, actual, expected):
            if left != right:
                return f"interval index {index} field {field} differs"
    return None


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _bits(value: float) -> int:
    return unpack("<Q", pack("<d", value))[0]


def clock_hash(clocks: tuple[int, ...]) -> str:
    return _hash(clocks)


def interval_hash(intervals: tuple[V7LevelInterval, ...]) -> str:
    return _hash(tuple((row.level_id, row.ordinal, row.valid_from_ms,
                        row.valid_to_ms, _bits(row.lower), _bits(row.upper),
                        row.role, row.transition_from, row.confirmed_at_ms,
                        row.historical) for row in intervals))


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _where(item: DerivedV7TickerDay, attempt: str | None = None) -> str:
    result = (f"source_build_id={literal(item.build_id)} "
              f"AND session_date=toDate({literal(item.session_date)}) "
              f"AND ticker={literal(item.ticker)}")
    if attempt is not None:
        result += f" AND derivation_attempt_id=toUUID({literal(attempt)})"
    return result


def _coverage(client: Any, item: DerivedV7TickerDay) -> list[dict[str, Any]]:
    return _rows(client, f"""SELECT
      toString(derivation_attempt_id) AS derivation_attempt_id,
      toString(bars_attempt_id) AS bars_attempt_id,
      source_checkpoint_hash,decoded_seed_hash,seed_source_plan_hash,
      split_evidence_hash,seed_input_policy,product_digest,
      clock_count,interval_count,clock_hash,interval_hash
      FROM {COVERAGE_TABLE} WHERE {_where(item)}""")


def _read_clocks(client: Any, item: DerivedV7TickerDay,
                 attempt: str) -> tuple[int, ...]:
    return tuple(int(row["boundary_ms"]) for row in _rows(client,
        f"SELECT boundary_ms FROM {CLOCK_TABLE} WHERE {_where(item, attempt)} "
        "ORDER BY boundary_ms"))


def _read_intervals(client: Any, item: DerivedV7TickerDay,
                    attempt: str) -> tuple[tuple[object, ...], ...]:
    return tuple((str(row["level_id"]), int(row["ordinal"]),
                  int(row["valid_from_ms"]), int(row["valid_to_ms"]),
                  int(row["lower_bits"]), int(row["upper_bits"]),
                  str(row["role"]),
                  ("" if row["transition_from"] == "none"
                   else str(row["transition_from"])),
                  int(row["confirmed_at_ms"]), bool(row["historical"]))
                 for row in _rows(client,
        "SELECT level_id,ordinal,valid_from_ms,valid_to_ms,"
        "reinterpretAsUInt64(lower) AS lower_bits,"
        "reinterpretAsUInt64(upper) AS upper_bits,"
        "toString(role) AS role,toString(transition_from) AS transition_from,"
        f"confirmed_at_ms,historical FROM {INTERVAL_TABLE} "
        f"WHERE {_where(item, attempt)} "
        "ORDER BY valid_from_ms,ordinal,level_id"))


def _expected_intervals(item: DerivedV7TickerDay) -> tuple[tuple[object, ...], ...]:
    return tuple((row.level_id, row.ordinal, row.valid_from_ms,
                  row.valid_to_ms, _bits(row.lower), _bits(row.upper),
                  row.role, row.transition_from, row.confirmed_at_ms,
                  row.historical) for row in sorted(item.intervals,
                  key=lambda row: (row.valid_from_ms, row.ordinal, row.level_id)))


def _expected_coverage(item: DerivedV7TickerDay) -> dict[str, object]:
    return {
        "bars_attempt_id": item.bars_attempt_id,
        "source_checkpoint_hash": item.source_checkpoint_hash,
        "decoded_seed_hash": item.decoded_seed_hash,
        "seed_source_plan_hash": item.seed_source_plan_hash,
        "split_evidence_hash": item.split_evidence_hash,
        "seed_input_policy": item.seed_input_policy,
        "product_digest": PRODUCT_DIGEST,
        "clock_count": len(item.valid_seconds),
        "interval_count": len(item.intervals),
        "clock_hash": clock_hash(item.valid_seconds),
        "interval_hash": interval_hash(item.intervals),
    }


def _validate_item(item: DerivedV7TickerDay) -> None:
    if (not isinstance(item, DerivedV7TickerDay)
            or not item.build_id or not item.ticker
            or item.seed_input_policy not in {POLICY, PROVISIONAL_SEED_POLICY}
            or (item.source_checkpoint_hash
                and _HASH.fullmatch(item.source_checkpoint_hash) is None)
            or any(_HASH.fullmatch(value) is None for value in (
                item.decoded_seed_hash, item.seed_source_plan_hash,
                item.split_evidence_hash))):
        raise ValueError("V7 derivative publication lacks certified scalar identity")
    UUID(item.bars_attempt_id)
    if (any(type(clock) is not int or not 0 < clock <= SESSION_MS
            or clock % 1_000 for clock in item.valid_seconds)
            or any(left >= right for left, right in zip(
                item.valid_seconds, item.valid_seconds[1:]))):
        raise ValueError("V7 derivative completed-second clocks are invalid")
    by_id: dict[str, list[V7LevelInterval]] = {}
    for row in item.intervals:
        if (not isinstance(row, V7LevelInterval) or not row.level_id
                or type(row.ordinal) is not int or row.ordinal < 0
                or type(row.valid_from_ms) is not int
                or type(row.valid_to_ms) is not int
                or not 0 <= row.valid_from_ms < row.valid_to_ms <= SESSION_MS + 1
                or row.valid_from_ms % 1_000
                or row.valid_to_ms != SESSION_MS + 1
                and row.valid_to_ms % 1_000
                or type(row.lower) is not float or type(row.upper) is not float
                or not isfinite(row.lower) or not isfinite(row.upper)
                or not 0 < row.lower <= row.upper
                or row.role not in {"support", "resistance", "transition"}
                or row.transition_from not in {"", "support", "resistance"}
                or type(row.confirmed_at_ms) is not int
                or row.confirmed_at_ms <= 0
                or type(row.historical) is not bool):
            raise ValueError("V7 derivative interval is invalid")
        by_id.setdefault(row.level_id, []).append(row)
    for rows in by_id.values():
        ordered = sorted(rows, key=lambda row: row.valid_from_ms)
        if any(left.valid_to_ms > right.valid_from_ms
               for left, right in zip(ordered, ordered[1:])):
            raise ValueError("V7 derivative level intervals overlap")


def _verify_covered(client: Any, item: DerivedV7TickerDay,
                    expected: dict[str, object]) -> str | None:
    rows = _coverage(client, item)
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("V7 derivative has duplicate coverage")
    row = rows[0]
    attempt = str(UUID(str(row.pop("derivation_attempt_id"))))
    if (row != expected or _read_clocks(client, item, attempt) != item.valid_seconds
            or _read_intervals(client, item, attempt) != _expected_intervals(item)):
        raise RuntimeError("V7 derivative covered attempt differs from source")
    return attempt


def publish_unit(writer: Any, reader: Any, item: DerivedV7TickerDay, *,
                 clock_batch_size: int = 1_000,
                 attempt_id: str | None = None) -> str:
    """Write bounded child batches; certify only after exact read-back."""
    if (type(clock_batch_size) is not int
            or not 1 <= clock_batch_size <= 10_000
            or not callable(getattr(writer, "execute", None))
            or not callable(getattr(reader, "execute", None))):
        raise ValueError("V7 derivative publication lacks typed source or writer")
    _validate_item(item)
    expected = _expected_coverage(item)
    # The exact producer principal has only product SELECT/INSERT authority;
    # the separate read principal owns system-catalog and SSD-part inspection.
    verify_tables(reader)
    existing = _verify_covered(reader, item, expected)
    if existing is not None:
        return "already_published"
    attempt = str(UUID(attempt_id)) if attempt_id is not None else str(uuid4())
    if _read_clocks(reader, item, attempt) or _read_intervals(reader, item, attempt):
        raise RuntimeError("V7 derivative attempt is not fresh")
    base = (f"{literal(item.build_id)},toDate({literal(item.session_date)}),"
            f"{literal(item.ticker)},toUUID({literal(attempt)})")
    for offset in range(0, len(item.valid_seconds), clock_batch_size):
        values = ",".join(
            f"({base},{clock})"
            for clock in item.valid_seconds[offset:offset + clock_batch_size])
        writer.execute(f"INSERT INTO {CLOCK_TABLE} "
                       "(source_build_id,session_date,ticker,derivation_attempt_id,"
                       f"boundary_ms) VALUES {values}")
    # Geometry is sparse; bound it independently of the clock batch size.
    for offset in range(0, len(item.intervals), clock_batch_size):
        values = ",".join(
            f"({base},{literal(row.level_id)},{row.ordinal},"
            f"{row.valid_from_ms},{row.valid_to_ms},"
            f"reinterpretAsFloat64(toUInt64({_bits(row.lower)})),"
            f"reinterpretAsFloat64(toUInt64({_bits(row.upper)})),"
            f"{literal(row.role)},"
            f"{literal(row.transition_from or 'none')},"
            f"{row.confirmed_at_ms},{int(row.historical)})"
            for row in item.intervals[offset:offset + clock_batch_size])
        writer.execute(f"INSERT INTO {INTERVAL_TABLE} "
                       "(source_build_id,session_date,ticker,derivation_attempt_id,"
                       "level_id,ordinal,valid_from_ms,valid_to_ms,lower,upper,"
                       "role,transition_from,confirmed_at_ms,historical) "
                       f"VALUES {values}")
    mismatch = _readback_mismatch(
        _read_clocks(reader, item, attempt), item.valid_seconds,
        _read_intervals(reader, item, attempt), _expected_intervals(item))
    if mismatch is not None:
        raise V7ReadbackMismatch(mismatch)
    writer.execute(f"""INSERT INTO {COVERAGE_TABLE}
      (source_build_id,session_date,ticker,derivation_attempt_id,
       bars_attempt_id,source_checkpoint_hash,decoded_seed_hash,
       seed_source_plan_hash,split_evidence_hash,seed_input_policy,
       product_digest,clock_count,interval_count,clock_hash,interval_hash,
       certified_at)
      SELECT {base},toUUID({literal(item.bars_attempt_id)}),
       {literal(item.source_checkpoint_hash)},
       {literal(item.decoded_seed_hash)},
       {literal(item.seed_source_plan_hash)},
       {literal(item.split_evidence_hash)},
       {literal(item.seed_input_policy)},
       {literal(PRODUCT_DIGEST)},toUInt32({len(item.valid_seconds)}),
       toUInt32({len(item.intervals)}),
       {literal(expected['clock_hash'])},
       {literal(expected['interval_hash'])},now64(6,'UTC')""")
    if _verify_covered(reader, item, expected) != attempt:
        raise RuntimeError("V7 derivative coverage failed exact verification")
    return "published"

"""Coverage-last publication of scalar Strategy 1 entry evidence.

Only a producer may call this module. An uncertain INSERT leaves an orphan
attempt, never a certified Backtest input. No market or journal table is written.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any, Callable
from uuid import UUID, uuid4

from pipelines.market_sip.events.market_day_sql import literal
from src.backend.backtest_strategy_one_entry_product import (
    ActivationFact, CandidateFact, canonical_price, content_hash, exact_float64,
)
from src.trading_runtime.strategy_one_entry_evidence_schema import (
    ACTIVATION_TABLE, ACTIVATION_RESISTANCE_TABLE, EVIDENCE_TABLE,
    COVERAGE_TABLE, PRODUCT_DIGEST,
)


@dataclass(frozen=True, slots=True)
class EntryPublicationScope:
    build_id: str
    session_date: str
    ticker: str
    bars_attempt_id: str
    candidate_attempt_id: str
    candidate_content_hash: str
    candidate_plan_token: str
    activation_plan_token: str
    pivot_plan_token: str
    hod_plan_token: str
    v7_seed_plan_token: str
    candidate_boundaries: tuple[int, ...]
    episode_starts: tuple[int, ...]

    def __post_init__(self) -> None:
        date.fromisoformat(self.session_date)
        UUID(self.bars_attempt_id)
        UUID(self.candidate_attempt_id)
        if (not self.build_id or not self.ticker
                or self.ticker != self.ticker.upper()
                or any(len(value) != 64 for value in (
                    self.candidate_content_hash, self.candidate_plan_token,
                    self.activation_plan_token, self.pivot_plan_token,
                    self.hod_plan_token, self.v7_seed_plan_token))
                or not self.candidate_boundaries
                or not self.episode_starts
                or any(a >= b for a, b in zip(
                    self.candidate_boundaries, self.candidate_boundaries[1:]))
                or any(a >= b for a, b in zip(
                    self.episode_starts, self.episode_starts[1:]))):
            raise ValueError("Strategy 1 entry publication scope is not pinned")


class EntryReadbackMismatch(RuntimeError):
    """Only safe field names and counts, never values or credentials."""


def _rows(client: Any, sql: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _where(scope: EntryPublicationScope, attempt: str | None = None) -> str:
    where = (f"source_build_id={literal(scope.build_id)} AND "
             f"session_date=toDate({literal(scope.session_date)}) AND "
             f"ticker={literal(scope.ticker)}")
    if attempt is not None:
        where += f" AND derivation_attempt_id=toUUID({literal(attempt)})"
    return where


def _covered(client: Any, scope: EntryPublicationScope) -> list[dict]:
    return _rows(client, f"""SELECT
      toString(derivation_attempt_id) AS derivation_attempt_id,
      toString(bars_attempt_id) AS bars_attempt_id,
      toString(candidate_attempt_id) AS candidate_attempt_id,
      candidate_content_hash,candidate_plan_token,activation_plan_token,
      pivot_plan_token,hod_plan_token,v7_seed_plan_token,product_digest,
      activation_count,resistance_count,evidence_count,content_hash
      FROM {COVERAGE_TABLE} WHERE {_where(scope)}""")


def _read_children(client: Any, scope: EntryPublicationScope, attempt: str,
                   ) -> tuple[tuple[ActivationFact, ...], tuple[CandidateFact, ...], int]:
    activation_rows = _rows(client, f"""SELECT episode_start_ms,price_int,
      isNull(average_gap) AS average_gap_is_null,
      reinterpretAsUInt64(ifNull(average_gap,toFloat64(0))) AS average_gap_bits,
      resistance_count FROM {ACTIVATION_TABLE}
      WHERE {_where(scope, attempt)} ORDER BY episode_start_ms""")
    resistance_rows = _rows(client, f"""SELECT episode_start_ms,ordinal,level_id
      FROM {ACTIVATION_RESISTANCE_TABLE} WHERE {_where(scope, attempt)}
      ORDER BY episode_start_ms,ordinal""")
    evidence_rows = _rows(client, f"""SELECT boundary_ms,episode_start_ms,
      bos_break_boundary_ms,bos_pivot_id,bos_break_close_int,bos_support_kind,
      bos_support_level_id,bos_support_pivot_id,protection_valid,
      isNull(stop_price) AS stop_price_is_null,
      reinterpretAsUInt64(ifNull(stop_price,toFloat64(0))) AS stop_price_bits,
      isNull(target_price) AS target_price_is_null,
      reinterpretAsUInt64(ifNull(target_price,toFloat64(0))) AS target_price_bits,
      target_level_id,target_ordinal FROM {EVIDENCE_TABLE}
      WHERE {_where(scope, attempt)} ORDER BY boundary_ms""")
    activations = []
    cursor = 0
    for row in activation_rows:
        start = int(row["episode_start_ms"])
        count = int(row["resistance_count"])
        children = resistance_rows[cursor:cursor + count]
        if (len(children) != count or any(
                int(child["episode_start_ms"]) != start
                or int(child["ordinal"]) != ordinal
                for ordinal, child in enumerate(children, 1))):
            raise RuntimeError("Strategy 1 entry resistance read-back is incomplete")
        activations.append(ActivationFact(
            scope.ticker, start, int(row["price_int"]),
            exact_float64(row["average_gap_bits"])
            if not int(row["average_gap_is_null"]) else None,
            tuple(str(child["level_id"]) for child in children)))
        cursor += count
    if cursor != len(resistance_rows):
        raise RuntimeError("Strategy 1 entry resistance read-back has orphan rows")
    candidates = []
    for row in evidence_rows:
        valid = int(row["protection_valid"])
        if valid not in (0, 1):
            raise RuntimeError("Strategy 1 entry protection flag is malformed")
        candidates.append(CandidateFact(
            scope.ticker, int(row["boundary_ms"]), int(row["episode_start_ms"]),
            int(row["bos_break_boundary_ms"])
            if row["bos_break_boundary_ms"] is not None else None,
            str(row["bos_pivot_id"]), int(row["bos_break_close_int"])
            if row["bos_break_close_int"] is not None else None,
            str(row["bos_support_kind"]), str(row["bos_support_level_id"]),
            str(row["bos_support_pivot_id"]), bool(valid),
            canonical_price(exact_float64(row["stop_price_bits"]))
            if not int(row["stop_price_is_null"]) else None,
            canonical_price(exact_float64(row["target_price_bits"]))
            if not int(row["target_price_is_null"]) else None,
            str(row["target_level_id"]), int(row["target_ordinal"])
            if row["target_ordinal"] is not None else None))
    return tuple(activations), tuple(candidates), len(resistance_rows)


def _verify_existing(client: Any, scope: EntryPublicationScope) -> str | None:
    covered = _covered(client, scope)
    if len(covered) > 1:
        raise RuntimeError("Strategy 1 entry product has duplicate coverage")
    if not covered:
        return None
    row = covered[0]
    attempt = str(UUID(row["derivation_attempt_id"]))
    expected = {
        "bars_attempt_id": scope.bars_attempt_id,
        "candidate_attempt_id": scope.candidate_attempt_id,
        "candidate_content_hash": scope.candidate_content_hash,
        "candidate_plan_token": scope.candidate_plan_token,
        "activation_plan_token": scope.activation_plan_token,
        "pivot_plan_token": scope.pivot_plan_token,
        "hod_plan_token": scope.hod_plan_token,
        "v7_seed_plan_token": scope.v7_seed_plan_token,
        "product_digest": PRODUCT_DIGEST,
    }
    if any(row.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Strategy 1 entry coverage differs from pinned sources")
    activations, candidates, resistance_count = _read_children(
        client, scope, attempt)
    if (tuple(item.boundary_ms for item in candidates)
            != scope.candidate_boundaries
            or tuple(item.episode_start_ms for item in activations)
            != scope.episode_starts
            or int(row["activation_count"]) != len(activations)
            or int(row["resistance_count"]) != resistance_count
            or int(row["evidence_count"]) != len(candidates)
            or row["content_hash"] != content_hash(activations, candidates)):
        raise RuntimeError("Strategy 1 entry coverage child read-back differs")
    return attempt


def _insert_rows(client: Any, table: str, scope: EntryPublicationScope,
                 attempt: str, rows: tuple[dict, ...], *, chunk_size: int = 500) -> None:
    if not rows:
        return
    columns = tuple(rows[0])
    if any(tuple(row) != columns for row in rows):
        raise ValueError("Strategy 1 entry insert rows have inconsistent columns")
    all_columns = ("source_build_id", "session_date", "ticker",
                   "derivation_attempt_id", *columns)
    prefix = (literal(scope.build_id), f"toDate({literal(scope.session_date)})",
              literal(scope.ticker), f"toUUID({literal(attempt)})")

    def scalar(value: object) -> str:
        if value is None:
            return "NULL"
        if type(value) is bool:
            return str(int(value))
        if type(value) in (int, float):
            return str(value)
        if type(value) is str:
            return literal(value)
        raise TypeError("Strategy 1 entry insert contains a non-scalar")

    for offset in range(0, len(rows), chunk_size):
        chunk = rows[offset:offset + chunk_size]
        values = ",".join("(" + ",".join((*prefix, *(scalar(value)
                    for value in row.values()))) + ")" for row in chunk)
        client.execute(f"INSERT INTO {table} ({','.join(all_columns)}) "
                       f"VALUES {values}")


def publish_unit(
    writer: Any, scope: EntryPublicationScope, *,
    derive: Callable[[], tuple[tuple[ActivationFact, ...],
                               tuple[CandidateFact, ...]]],
) -> str:
    """Read back children exactly before the single authoritative seal."""
    if _verify_existing(writer, scope) is not None:
        return "skipped"
    activations, candidates = derive()
    if (tuple(item.episode_start_ms for item in activations)
            != scope.episode_starts
            or tuple(item.boundary_ms for item in candidates)
            != scope.candidate_boundaries):
        raise ValueError("Strategy 1 entry derivation differs from certified clocks")
    digest = content_hash(activations, candidates)
    attempt = str(uuid4())
    _insert_rows(writer, ACTIVATION_TABLE, scope, attempt,
                 tuple(value.row() for value in activations))
    _insert_rows(writer, ACTIVATION_RESISTANCE_TABLE, scope, attempt,
                 tuple(row for value in activations
                       for row in value.resistance_rows()))
    _insert_rows(writer, EVIDENCE_TABLE, scope, attempt,
                 tuple(value.row() for value in candidates))
    observed_activations, observed_candidates, resistance_count = _read_children(
        writer, scope, attempt)
    if (observed_activations != activations
            or observed_candidates != candidates
            or resistance_count != sum(len(value.resistance_ids)
                                       for value in activations)
            or content_hash(observed_activations, observed_candidates) != digest):
        differences = []
        for family, expected, observed in (
                ("activation", activations, observed_activations),
                ("evidence", candidates, observed_candidates)):
            if len(expected) != len(observed):
                differences.append(f"{family}_count={len(expected)}/{len(observed)}")
            else:
                for left, right in zip(expected, observed):
                    if left != right:
                        differences.extend(
                            f"{family}.{name}" for name in left.__dataclass_fields__
                            if getattr(left, name) != getattr(right, name))
                        break
        expected_resistance = sum(len(value.resistance_ids)
                                  for value in activations)
        if resistance_count != expected_resistance:
            differences.append(
                f"resistance_count={expected_resistance}/{resistance_count}")
        if not differences:
            differences.append("content_hash")
        raise EntryReadbackMismatch(
            "Strategy 1 entry child read-back differs: " + ",".join(differences))
    writer.execute(f"""INSERT INTO {COVERAGE_TABLE}
      (source_build_id,session_date,ticker,derivation_attempt_id,bars_attempt_id,
       candidate_attempt_id,candidate_content_hash,candidate_plan_token,
       activation_plan_token,pivot_plan_token,hod_plan_token,v7_seed_plan_token,
       product_digest,activation_count,resistance_count,evidence_count,
       content_hash,certified_at)
      SELECT {literal(scope.build_id)},toDate({literal(scope.session_date)}),
        {literal(scope.ticker)},toUUID({literal(attempt)}),
        toUUID({literal(scope.bars_attempt_id)}),
        toUUID({literal(scope.candidate_attempt_id)}),
        {literal(scope.candidate_content_hash)},
        {literal(scope.candidate_plan_token)},
        {literal(scope.activation_plan_token)},
        {literal(scope.pivot_plan_token)},
        {literal(scope.hod_plan_token)},
        {literal(scope.v7_seed_plan_token)},
        {literal(PRODUCT_DIGEST)},toUInt32({len(activations)}),
        toUInt32({resistance_count}),toUInt32({len(candidates)}),
        {literal(digest)},now64(6,'UTC')""")
    if _verify_existing(writer, scope) != attempt:
        raise RuntimeError("Strategy 1 entry coverage publication failed")
    return "published"

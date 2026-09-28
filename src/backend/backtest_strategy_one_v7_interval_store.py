"""Read-only, coverage-last certificate for producer-owned Strategy 1 V7.

Backtest never derives missing intervals or writes any ARTE market product.
The completed-second clock and sparse geometry are loaded as typed Arrow
columns, validated against a pinned bars/seed plan, and shared immutably.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Sequence
from uuid import UUID

from src.backend.backtest_market_data import CertifiedMarketDayPlan
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_v7 import PROVISIONAL_SEED_POLICY
from src.trading_runtime.strategy_one_v7_interval_schema import (
    CLOCK_TABLE, COVERAGE_TABLE, INTERVAL_TABLE, PRODUCT_DIGEST,
    verify_tables,
)
from src.trading_runtime.strategy_one_v7_intervals import (
    V7LevelInterval, clock_hash, interval_hash, levels_at,
)


_HASH = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class CertifiedV7IntervalUnit:
    ticker: str
    attempt_id: str
    bars_attempt_id: str
    source_checkpoint_hash: str
    decoded_seed_hash: str
    seed_source_plan_hash: str
    split_evidence_hash: str
    seed_input_policy: str
    clock_count: int
    interval_count: int
    clock_hash: str
    interval_hash: str


@dataclass(frozen=True, slots=True)
class CertifiedV7IntervalPlan:
    source_build_id: str
    session_date: str
    coverage: tuple[CertifiedV7IntervalUnit, ...]
    valid_seconds: tuple[tuple[str, tuple[int, ...]], ...]
    intervals: tuple[tuple[str, tuple[V7LevelInterval, ...]], ...]
    token: str

    def levels(self, ticker: str, *, boundary_ms: int) -> tuple[dict[str, object], ...]:
        """Causal 100ms lookup from the already verified immutable columns."""
        index = next((index for index, row in enumerate(self.coverage)
                      if row.ticker == ticker), None)
        if index is None:
            raise ValueError("Strategy 1 ticker lacks certified V7 intervals")
        return levels_at(
            boundary_ms=boundary_ms,
            seed_policy=self.coverage[index].seed_input_policy,
            valid_seconds=self.valid_seconds[index][1],
            intervals=self.intervals[index][1])


def _literal(value: str) -> str:
    from src.backend.backtest_market_data import _literal as market_literal
    return market_literal(value)


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _arrow_columns(client: Any, sql: str,
                   names: tuple[str, ...]) -> Sequence[list[Any]]:
    """Yield bounded Arrow batch columns, never dictionaries per market row."""
    for batch in client.iter_arrow_record_batches(sql + " FORMAT ArrowStream"):
        if tuple(batch.schema.names) != names:
            raise RuntimeError("Strategy 1 V7 Arrow columns differ from contract")
        yield [batch.column(index).to_pylist() for index in range(len(names))]


def certify_v7_interval_plan(
    market: CertifiedMarketDayPlan, seeds: CertifiedSeedPlan, *,
    session_date: str, candidate_tickers: tuple[str, ...], client: Any,
) -> CertifiedV7IntervalPlan:
    """Cold-read all selected ticker-days and reject absent or changed rows."""
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(seeds, CertifiedSeedPlan)
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or market.sessions != (session_date,)
            or seeds.build_id != market.build_id
            or not candidate_tickers
            or tuple(sorted(set(candidate_tickers))) != candidate_tickers
            or not set(candidate_tickers) <= set(market.tickers)
            or not callable(getattr(client, "iter_arrow_record_batches", None))):
        raise ValueError("Strategy 1 V7 interval scope is not certified")
    verify_tables(client)
    bar_attempts = {unit.ticker: str(UUID(unit.attempt_id))
                    for unit in market.units if unit.stage == "bars"
                    and unit.session_date == session_date}
    seed_units = {str(row["ticker"]): row for row in seeds.units
                  if row["backtest_session"] == session_date}
    if (not set(candidate_tickers) <= set(bar_attempts)
            or not set(candidate_tickers) <= set(seed_units)):
        raise ValueError("Strategy 1 V7 interval source plan is incomplete")
    selected = ",".join(_literal(ticker) for ticker in candidate_tickers)
    where = (f"source_build_id={_literal(market.build_id)} "
             f"AND session_date=toDate({_literal(session_date)}) "
             f"AND ticker IN ({selected})")
    facts = _rows(client, f"""SELECT ticker,
       toString(derivation_attempt_id) AS attempt_id,
       toString(bars_attempt_id) AS bars_attempt_id,
       source_checkpoint_hash,decoded_seed_hash,seed_source_plan_hash,
       split_evidence_hash,seed_input_policy,product_digest,
       clock_count,interval_count,clock_hash,interval_hash
       FROM {COVERAGE_TABLE} WHERE {where}""")
    if len(facts) != len(candidate_tickers):
        raise RuntimeError("Strategy 1 V7 coverage is missing or duplicate")
    coverage_by_ticker: dict[str, CertifiedV7IntervalUnit] = {}
    for row in facts:
        ticker = str(row["ticker"])
        if ticker not in candidate_tickers or ticker in coverage_by_ticker:
            raise RuntimeError("Strategy 1 V7 coverage duplicates a candidate")
        seed = seed_units[ticker]
        source_hash = str(row["source_checkpoint_hash"])
        policy = (str(seed["input_policy"])
                  if int(seed["level_count"]) else POLICY)
        fields = ("decoded_seed_hash", "seed_source_plan_hash",
                  "split_evidence_hash", "clock_hash", "interval_hash")
        if (str(UUID(str(row["bars_attempt_id"]))) != bar_attempts[ticker]
                or source_hash != str(seed["source_checkpoint_hash"])
                or str(row["seed_source_plan_hash"]) != str(seed["source_plan_hash"])
                or str(row["seed_input_policy"]) != policy
                or policy not in {POLICY, PROVISIONAL_SEED_POLICY}
                or row["product_digest"] != PRODUCT_DIGEST
                or any(_HASH.fullmatch(str(row[name])) is None for name in fields)
                or not 0 <= int(row["clock_count"]) <= 57_600
                or not 0 <= int(row["interval_count"]) <= 100_000):
            raise RuntimeError("Strategy 1 V7 coverage differs from pinned source")
        coverage_by_ticker[ticker] = CertifiedV7IntervalUnit(
            ticker, str(UUID(str(row["attempt_id"]))),
            bar_attempts[ticker], source_hash,
            str(row["decoded_seed_hash"]),
            str(row["seed_source_plan_hash"]),
            str(row["split_evidence_hash"]), policy,
            int(row["clock_count"]), int(row["interval_count"]),
            str(row["clock_hash"]), str(row["interval_hash"]))
    if set(coverage_by_ticker) != set(candidate_tickers):
        raise RuntimeError("Strategy 1 V7 coverage omits a candidate")
    clocks: dict[str, list[int]] = {ticker: [] for ticker in candidate_tickers}
    intervals: dict[str, list[V7LevelInterval]] = {
        ticker: [] for ticker in candidate_tickers}
    for ticker_col, attempt_col, boundary_col in _arrow_columns(client,
            f"SELECT ticker,toString(derivation_attempt_id) AS attempt_id,"
            f"boundary_ms FROM {CLOCK_TABLE} WHERE {where} "
            "ORDER BY ticker,boundary_ms", ("ticker", "attempt_id", "boundary_ms")):
        for ticker, attempt, boundary in zip(ticker_col, attempt_col,
                                              boundary_col):
            unit = coverage_by_ticker[str(ticker)]
            if str(attempt) == unit.attempt_id:
                clocks[unit.ticker].append(int(boundary))
    names = ("ticker", "attempt_id", "level_id", "ordinal", "valid_from_ms",
             "valid_to_ms", "lower", "upper", "role", "transition_from",
             "confirmed_at_ms", "historical")
    query = (f"SELECT ticker,toString(derivation_attempt_id) AS attempt_id,"
             "level_id,ordinal,valid_from_ms,valid_to_ms,lower,upper,"
             "toString(role) AS role,toString(transition_from) AS transition_from,"
             f"confirmed_at_ms,historical FROM {INTERVAL_TABLE} WHERE {where} "
             "ORDER BY ticker,valid_from_ms,ordinal,level_id")
    for columns in _arrow_columns(client, query, names):
        for values in zip(*columns):
            (ticker, attempt, identity, ordinal, start, stop, lower, upper,
             role, transition, confirmed, historical) = values
            unit = coverage_by_ticker[str(ticker)]
            if str(attempt) != unit.attempt_id:
                continue
            intervals[unit.ticker].append(V7LevelInterval(
                str(identity), int(ordinal), int(start), int(stop),
                float(lower), float(upper), str(role),
                "" if transition == "none" else str(transition),
                int(confirmed), bool(historical)))
    coverage: list[CertifiedV7IntervalUnit] = []
    validated_clocks: list[tuple[str, tuple[int, ...]]] = []
    validated_intervals: list[tuple[str, tuple[V7LevelInterval, ...]]] = []
    token = sha256(b"strategy-one-v7-interval-plan-v1\0")
    for value in (market.token, seeds.token, session_date, PRODUCT_DIGEST):
        token.update(value.encode()); token.update(b"\0")
    for ticker in candidate_tickers:
        unit = coverage_by_ticker[ticker]
        seconds = tuple(clocks[ticker])
        rows = tuple(intervals[ticker])
        if (len(seconds) != unit.clock_count
                or len(rows) != unit.interval_count
                or clock_hash(seconds) != unit.clock_hash
                or interval_hash(tuple(sorted(rows, key=lambda row: (
                    row.valid_from_ms, row.level_id)))) != unit.interval_hash):
            raise RuntimeError("Strategy 1 V7 children differ from coverage")
        # Also validate causal interval geometry at representative boundaries.
        levels_at(boundary_ms=0, seed_policy=unit.seed_input_policy,
                  valid_seconds=seconds, intervals=rows)
        coverage.append(unit)
        validated_clocks.append((ticker, seconds))
        validated_intervals.append((ticker, rows))
        for value in (ticker, unit.attempt_id, unit.clock_hash,
                      unit.interval_hash):
            token.update(value.encode()); token.update(b"\0")
    return CertifiedV7IntervalPlan(
        market.build_id, session_date, tuple(coverage),
        tuple(validated_clocks), tuple(validated_intervals), token.hexdigest())

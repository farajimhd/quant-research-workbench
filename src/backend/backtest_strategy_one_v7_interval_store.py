"""Read-only, coverage-last certificate for producer-owned Strategy 1 V7.

Backtest never derives missing intervals or writes any ARTE market product.
The completed-second clock and sparse geometry are loaded as typed Arrow
columns, validated against a pinned bars/seed plan, and shared immutably.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Any, Sequence
from uuid import UUID

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, market_day_boundary,
)
from src.backend.backtest_market_plan_cache import (
    FingerprintPlanCache, product_inventory_fingerprint,
)
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_v7 import PROVISIONAL_SEED_POLICY
from src.trading_runtime.strategy_one_v7_interval_schema import (
    CLOCK_TABLE, COVERAGE_TABLE, INTERVAL_TABLE, PRODUCT_DIGEST,
    verify_tables,
)
from src.trading_runtime.strategy_one_v7_intervals import (
    SESSION_MS, V7LevelInterval, clock_hash, interval_hash, levels_at,
)


_HASH = re.compile(r"[0-9a-f]{64}\Z")
V7_INTERVAL_PLAN_CACHE = FingerprintPlanCache()
_TABLE_NAMES = tuple(table.split(".", 1)[1] for table in (
    CLOCK_TABLE, INTERVAL_TABLE, COVERAGE_TABLE))


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
    _tickers: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        tickers = tuple(row.ticker for row in self.coverage)
        if (tuple(sorted(set(tickers))) != tickers
                or tuple(row[0] for row in self.valid_seconds) != tickers
                or tuple(row[0] for row in self.intervals) != tickers):
            raise ValueError("Strategy 1 V7 interval plan is not ticker-aligned")
        object.__setattr__(self, "_tickers", tickers)

    def levels(self, ticker: str, *, boundary_ms: int) -> tuple[dict[str, object], ...]:
        """Causal 100ms lookup without scanning the all-ticker coverage."""
        index = bisect_left(self._tickers, ticker)
        if index == len(self._tickers) or self._tickers[index] != ticker:
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


def _validate_children(seconds: tuple[int, ...],
                       rows: tuple[V7LevelInterval, ...], *,
                       origin_ms: int) -> None:
    if (any(type(value) is not int or not 0 < value <= SESSION_MS
            or value % 1_000 for value in seconds)
            or any(left >= right for left, right in zip(seconds, seconds[1:]))):
        raise RuntimeError("Strategy 1 V7 validity clock is malformed")
    valid_clocks = set(seconds)
    changes: list[tuple[int, int, V7LevelInterval]] = []
    for row in rows:
        if (not row.level_id or type(row.ordinal) is not int
                or row.ordinal < 0
                or not 0 <= row.valid_from_ms < row.valid_to_ms <= SESSION_MS + 1
                or row.valid_from_ms != 0
                and row.valid_from_ms not in valid_clocks
                or row.valid_to_ms != SESSION_MS + 1
                and row.valid_to_ms not in valid_clocks
                or not isfinite(row.lower) or not isfinite(row.upper)
                or not 0 < row.lower <= row.upper
                or row.role not in {"support", "resistance", "transition"}
                or row.transition_from not in {"", "support", "resistance"}
                or not 0 < row.confirmed_at_ms <= origin_ms + row.valid_from_ms
                or type(row.historical) is not bool):
            raise RuntimeError("Strategy 1 V7 interval violates causal geometry")
        changes.append((row.valid_to_ms, 0, row))
        changes.append((row.valid_from_ms, 1, row))
    active_ids: set[str] = set()
    active_ordinals: set[int] = set()
    for _, kind, row in sorted(changes, key=lambda item: (item[0], item[1])):
        if kind == 0:
            if (row.level_id not in active_ids
                    or row.ordinal not in active_ordinals):
                raise RuntimeError("Strategy 1 V7 interval closes an absent level")
            active_ids.remove(row.level_id)
            active_ordinals.remove(row.ordinal)
        else:
            if row.level_id in active_ids or row.ordinal in active_ordinals:
                raise RuntimeError("Strategy 1 V7 intervals overlap in identity or ordinal")
            active_ids.add(row.level_id)
            active_ordinals.add(row.ordinal)


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
    from research.mlops.clickhouse import ClickHouseHttpClient
    cache_key = sha256("\0".join((
        market.token, seeds.token, session_date, PRODUCT_DIGEST,
        *candidate_tickers)).encode()).hexdigest()
    before = None
    if isinstance(client, ClickHouseHttpClient):
        before = product_inventory_fingerprint(client, _TABLE_NAMES)
        cached = V7_INTERVAL_PLAN_CACHE.get(cache_key, before)
        if (cached is not None and
                product_inventory_fingerprint(client, _TABLE_NAMES) == before):
            return cached
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
    # Failed producer attempts can retain unsealed child rows. Push the exact
    # sealed attempt pairs into ClickHouse so their accumulation cannot make
    # Backtest load unbounded data or silently select a non-authoritative row.
    attempt_pairs = ",".join(
        f"({_literal(ticker)},toUUID({_literal(coverage_by_ticker[ticker].attempt_id)}))"
        for ticker in candidate_tickers)
    child_where = (where + " AND (ticker,derivation_attempt_id) IN ("
                   + attempt_pairs + ")")
    clocks: dict[str, list[int]] = {ticker: [] for ticker in candidate_tickers}
    intervals: dict[str, list[V7LevelInterval]] = {
        ticker: [] for ticker in candidate_tickers}
    origin_ms = round(market_day_boundary(session_date, 0).timestamp() * 1_000)
    for ticker_col, attempt_col, boundary_col in _arrow_columns(client,
            f"SELECT ticker,toString(derivation_attempt_id) AS attempt_id,"
            f"boundary_ms FROM {CLOCK_TABLE} WHERE {child_where} "
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
             f"confirmed_at_ms,historical FROM {INTERVAL_TABLE} WHERE {child_where} "
             "ORDER BY ticker,valid_from_ms,ordinal,level_id")
    for columns in _arrow_columns(client, query, names):
        for values in zip(*columns):
            (ticker, attempt, identity, ordinal, start, stop, lower, upper,
             role, transition, confirmed, historical) = values
            unit = coverage_by_ticker[str(ticker)]
            if str(attempt) != unit.attempt_id:
                continue
            if type(historical) is not int or historical not in (0, 1):
                raise RuntimeError("Strategy 1 V7 historical flag is malformed")
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
        _validate_children(seconds, rows, origin_ms=origin_ms)
        # Validate the same strategy-facing projection invoked by execution.
        levels_at(boundary_ms=0, seed_policy=unit.seed_input_policy,
                  valid_seconds=seconds, intervals=rows)
        coverage.append(unit)
        validated_clocks.append((ticker, seconds))
        validated_intervals.append((ticker, rows))
        for value in (ticker, unit.attempt_id, unit.clock_hash,
                      unit.interval_hash):
            token.update(value.encode()); token.update(b"\0")
    result = CertifiedV7IntervalPlan(
        market.build_id, session_date, tuple(coverage),
        tuple(validated_clocks), tuple(validated_intervals), token.hexdigest())
    if before is not None:
        after = product_inventory_fingerprint(client, _TABLE_NAMES)
        if after != before:
            raise RuntimeError("Strategy 1 V7 interval parts changed during cold read")
        V7_INTERVAL_PLAN_CACHE.put(cache_key, after, result)
    return result

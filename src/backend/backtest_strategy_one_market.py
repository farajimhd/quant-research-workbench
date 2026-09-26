"""Bounded, SELECT-only market projection for certified Strategy 1 candidates.

QMD/producer owns the vectorized candidate product. Backtest reads its exact
completed 100ms keys and never computes bars, indicators, or market structure.
The returned rows feed the causal coordinator; they do not replace active-order
liquidity reads or position-management boundaries.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Any, Callable, Mapping

from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, iter_candidate_market_rows,
    project_market_day_plan,
)
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST


def candidate_market_shards(
    prepared: tuple[PreparedStrategyOneTicker, ...],
) -> tuple[dict[str, tuple[int, ...]], ...]:
    """Bound each primary-key SELECT to eight tickers and 512 exact keys."""
    if (not isinstance(prepared, tuple)
            or any(not isinstance(item, PreparedStrategyOneTicker)
                   for item in prepared)
            or len({item.ticker for item in prepared}) != len(prepared)):
        raise ValueError("Strategy 1 market shards need unique prepared tickers")
    shards: list[dict[str, tuple[int, ...]]] = []
    current: dict[str, tuple[int, ...]] = {}
    count = 0
    for item in sorted(prepared, key=lambda row: row.ticker):
        clocks = tuple(int(value) for value in item.boundary_ms)
        if not clocks or any(clock <= 0 or clock % 100
                             or clock > 57_600_000 for clock in clocks) \
                or any(a >= b for a, b in zip(clocks, clocks[1:])):
            raise ValueError("Strategy 1 prepared market boundaries are invalid")
        for offset in range(0, len(clocks), 512):
            chunk = clocks[offset:offset + 512]
            if current and (len(current) >= 8 or count + len(chunk) > 512
                            or item.ticker in current):
                shards.append(current)
                current, count = {}, 0
            current[item.ticker] = chunk
            count += len(chunk)
    if current:
        shards.append(current)
    return tuple(shards)


def load_sparse_candidate_market(
    market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan, *,
    price_plan: PriceLevelPlan, client_factory: Callable[[], Any],
    max_workers: int = 4, max_rows: int = 250_000,
) -> tuple[Mapping[str, Any], ...]:
    """Read exact certified entry boundaries in parallel; return global order."""
    if (len(market.sessions) != 1 or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or candidates.source_build_id != market.build_id
            or candidates.candidate_rule_digest != RULE_DIGEST
            or not isinstance(price_plan, PriceLevelPlan)
            or price_plan.source_build_id != market.build_id
            or type(max_workers) is not int or not 1 <= max_workers <= 16
            or type(max_rows) is not int or max_rows < 1
            or not callable(client_factory)):
        raise ValueError("Strategy 1 sparse market read lacks pinned authority")
    selected = {item.ticker for item in candidates.prepared}
    if not selected <= set(market.tickers):
        raise ValueError("Strategy 1 candidates exceed the certified market scope")
    expected = sum(len(item.boundary_ms) for item in candidates.prepared)
    if expected > max_rows:
        raise RuntimeError("Strategy 1 candidate rows exceed memory budget")
    if not expected:
        return ()
    shards = candidate_market_shards(candidates.prepared)

    def read(shard: dict[str, tuple[int, ...]]) -> list[Mapping[str, Any]]:
        scoped = project_market_day_plan(market, tuple(sorted(shard)))
        prices = price_plan.projected(scoped)
        client = client_factory()
        if client is None or not callable(getattr(client, "close", None)):
            raise TypeError("Strategy 1 sparse read needs a closable SELECT client")
        with closing(client):
            return list(iter_candidate_market_rows(
                scoped, candidate_boundaries=shard, price_plan=prices,
                client=client))

    rows: list[Mapping[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for batch in pool.map(read, shards):
            rows.extend(batch)
    if len(rows) != expected:
        raise RuntimeError("Strategy 1 sparse market rows differ from certified candidates")
    rows.sort(key=lambda row: (int(row["boundary_ms"]), str(row["ticker"])))
    if any((left["boundary_ms"], left["ticker"])
           >= (right["boundary_ms"], right["ticker"])
           for left, right in zip(rows, rows[1:])):
        raise RuntimeError("Strategy 1 sparse market rows are not globally unique")
    return tuple(rows)

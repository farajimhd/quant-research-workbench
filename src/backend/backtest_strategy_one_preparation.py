"""Bounded, read-only Strategy 1 preparation from certified ARTE products.

The scanner covers the entire certified universe. Only tickers with a causal
episode start need Arrow market loading. Workers never touch portfolio, OMS,
broker, or journal state; the coordinator consumes the stable merged cursor.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from heapq import merge
from typing import Any, Callable, Iterator, Mapping

import numpy as np

from src.backend.backtest_market_data import CertifiedMarketDayPlan, market_day_boundary
from src.backend.backtest_strategy_one_loader import load_strategy_one_entry_batch
from src.backend.fixed_bar_signal import CONTRACT, STREAM_ID, load_first_squeeze_occurrences
from src.trading_runtime.strategy_one_columnar import schedule_strategy_one_entries


@dataclass(frozen=True, slots=True)
class PreparedStrategyOneTicker:
    ticker: str
    source_rows: int
    row_index: np.ndarray
    boundary_ms: np.ndarray
    episode_start_ms: np.ndarray
    macd_boundary_ms: np.ndarray
    stop_bar_boundary_ms: np.ndarray
    stop_low_int: np.ndarray


@dataclass(frozen=True, slots=True, order=True)
class StrategyOneEntryCursor:
    boundary_ms: int
    ticker: str
    source_row_index: int
    episode_start_ms: int
    macd_boundary_ms: tuple[int, int, int, int]
    stop_bar_boundary_ms: int
    stop_low_int: int


def _episode_starts(plan: CertifiedMarketDayPlan, session_date: str,
                    scan: Mapping[str, Any]) -> dict[str, tuple[int, ...]]:
    authority = dict(scan.get("authority") or {})
    occurrences = list(scan.get("occurrences") or ())
    if (authority.get("authority") != CONTRACT
            or authority.get("market_plan_token") != plan.token
            or authority.get("row_count") != len(occurrences)):
        raise ValueError("Strategy 1 squeeze scan lacks certified authority")
    origin = market_day_boundary(session_date, 0)
    grouped: dict[str, list[int]] = {}
    for row in occurrences:
        ticker = str(row.get("ticker") or "")
        if (ticker not in plan.tickers or row.get("signal_stream_id") != STREAM_ID
                or row.get("source_authority") != CONTRACT
                or row.get("market_plan_token") != plan.token
                or row.get("query_sha256") != authority.get("query_sha256")
                or row.get("squeeze_episode_role") != "start"):
            raise ValueError("Strategy 1 squeeze occurrence differs from certified scope")
        available = datetime.fromisoformat(str(row.get("available_at") or ""))
        expires = datetime.fromisoformat(str(row.get("squeeze_expires_at") or ""))
        if available.tzinfo is None or expires != available + timedelta(milliseconds=300_000):
            raise ValueError("Strategy 1 squeeze occurrence has invalid clocks")
        offset = available - origin
        offset_us = ((offset.days * 86_400 + offset.seconds) * 1_000_000
                     + offset.microseconds)
        if (not 0 < offset_us <= 57_600_000_000 or offset_us % 100_000
                or row.get("effective_at") != row.get("available_at")
                or row.get("event_time") != row.get("available_at")
                or row.get("squeeze_episode_started_at") != row.get("available_at")):
            raise ValueError("Strategy 1 squeeze start is not a completed 100ms boundary")
        grouped.setdefault(ticker, []).append(offset_us // 1_000)
    return {ticker: tuple(starts) for ticker, starts in grouped.items()}


def prepare_strategy_one_session(
    plan: CertifiedMarketDayPlan, *, session_date: str,
    through_boundary_ms: int, stream: Mapping[str, Any],
    activation: Mapping[str, Any], scan_client: Any,
    client_factory: Callable[[], Any], max_workers: int = 4,
) -> tuple[PreparedStrategyOneTicker, ...]:
    """Scan all certified tickers, then load episode-bearing tickers in bounded lanes."""
    if (plan.execution_interval.kind != "fixed"
            or plan.execution_interval.milliseconds != 100
            or plan.sessions != (session_date,)
            or type(max_workers) is not int or not 1 <= max_workers <= 16):
        raise ValueError("Strategy 1 preparation needs one certified 100ms session")
    scan = load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=through_boundary_ms, client=scan_client)
    episodes = _episode_starts(plan, session_date, scan)
    if not episodes:
        return ()

    def load(ticker: str) -> PreparedStrategyOneTicker:
        with closing(client_factory()) as client:
            batch = load_strategy_one_entry_batch(
                plan, session_date=session_date, ticker=ticker,
                through_boundary_ms=through_boundary_ms, client=client)
        schedule = schedule_strategy_one_entries(batch, episodes[ticker])
        rows = schedule.row_index
        return PreparedStrategyOneTicker(
            ticker, len(batch.evaluation_boundary_ms), rows,
            schedule.evaluation_boundary_ms, schedule.episode_start_boundary_ms,
            batch.macd_boundary_ms[rows], batch.stop_bar_boundary_ms[rows],
            batch.stop_low_int[rows])

    results: dict[str, PreparedStrategyOneTicker] = {}
    tickers = iter(sorted(episodes))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending = {}
        for ticker in tickers:
            pending[pool.submit(load, ticker)] = ticker
            if len(pending) == max_workers:
                break
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                ticker = pending.pop(future)
                results[ticker] = future.result()
                try:
                    successor = next(tickers)
                except StopIteration:
                    continue
                pending[pool.submit(load, successor)] = successor
    return tuple(results[ticker] for ticker in sorted(results))


def iter_strategy_one_entries(
    prepared: tuple[PreparedStrategyOneTicker, ...],
) -> Iterator[StrategyOneEntryCursor]:
    """Stable global time/ticker merge; never mutates shared financial state."""
    def ticker_rows(item: PreparedStrategyOneTicker):
        for index, boundary in enumerate(item.boundary_ms):
            yield StrategyOneEntryCursor(
                int(boundary), item.ticker, int(item.row_index[index]),
                int(item.episode_start_ms[index]),
                tuple(int(value) for value in item.macd_boundary_ms[index]),
                int(item.stop_bar_boundary_ms[index]), int(item.stop_low_int[index]))
    yield from merge(*(ticker_rows(item) for item in prepared))

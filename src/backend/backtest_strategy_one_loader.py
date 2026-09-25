"""Read-only Arrow projection of certified arte rows for Strategy 1 gates.

One ticker/session is the bounded unit of parallel preparation. The caller
owns the V3 read-only client and must coordinate candidate output in global
causal order before touching portfolio, OMS, broker, or journal state.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
import json
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, market_day_boundary, market_day_source_sqls,
    project_market_day_plan,
)
from src.trading_runtime.strategy_one_columnar import (
    CompletedMacd, CompletedThirtySecondLow, MACD_RESOLUTIONS_MS,
    StrategyOneCandidateBatch, prepare_strategy_one_entries,
)


_NEEDED = frozenset((100, *MACD_RESOLUTIONS_MS))


def _source_plan(plan: CertifiedMarketDayPlan, session_date: str,
                 ticker: str) -> CertifiedMarketDayPlan:
    if (session_date not in plan.sessions or ticker not in plan.tickers
            or not _NEEDED <= set(plan.required_resolutions_ms)):
        raise ValueError("Strategy 1 source scope or resolutions are not certified")
    units = tuple(unit for unit in plan.units
                  if unit.session_date == session_date and unit.ticker == ticker)
    if len(units) != 3 or {unit.stage for unit in units} != {
            "bars", "technical", "broker_100ms"}:
        raise ValueError("Strategy 1 source lacks three pinned arte attempts")
    token = hashlib.sha256(json.dumps(
        [plan.token, session_date, ticker], separators=(",", ":")
    ).encode()).hexdigest()
    return replace(plan, sessions=(session_date,), tickers=(ticker,),
                   units=units, token=token)


def _table(client: Any, query: str, *, maximum_rows: int) -> pa.Table:
    if not query.rstrip().endswith("FORMAT JSONEachRow"):
        raise ValueError("Strategy 1 source query differs from certified loader")
    arrow_query = query.rstrip()[:-len("JSONEachRow")] + "ArrowStream"
    stream = client.iter_arrow_record_batches(arrow_query)
    batches = []
    row_count = 0
    byte_count = 0
    try:
        for batch in stream:
            if not isinstance(batch, pa.RecordBatch):
                raise RuntimeError("Strategy 1 source returned a non-Arrow batch")
            row_count += batch.num_rows
            byte_count += batch.nbytes
            if row_count > maximum_rows or byte_count > 512 * 1024 * 1024:
                raise RuntimeError("Strategy 1 Arrow source exceeds one-ticker memory budget")
            batches.append(batch)
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    if not batches:
        raise ValueError("Strategy 1 source has no persisted rows")
    return pa.Table.from_batches(batches)


def _numpy(table: pa.Table, name: str, *, fill: int | float,
           dtype: Any) -> np.ndarray:
    if name not in table.column_names:
        raise ValueError(f"Strategy 1 source lacks {name}")
    column = pc.fill_null(table[name], fill)
    return np.asarray(column.to_numpy(zero_copy_only=False), dtype=dtype)


def _resolution(table: pa.Table, milliseconds: int) -> pa.Table:
    return table.filter(pc.equal(table["resolution_ms"], milliseconds))


def _assert_scope(table: pa.Table, *, session_date: str, ticker: str,
                  resolutions: frozenset[int]) -> None:
    if (pc.unique(table["session_date"]).to_pylist() != [date.fromisoformat(session_date)]
            or pc.unique(table["ticker"]).to_pylist() != [ticker]
            or not set(pc.unique(table["resolution_ms"]).to_pylist()) <= resolutions):
        raise ValueError("Strategy 1 Arrow source differs from pinned scope")


def _decode_entry_batch(hundred: pa.Table, higher: pa.Table, *,
                        session_date: str, ticker: str) -> StrategyOneCandidateBatch:
    _assert_scope(hundred, session_date=session_date, ticker=ticker,
                  resolutions=frozenset({100}))
    _assert_scope(higher, session_date=session_date, ticker=ticker,
                  resolutions=frozenset(MACD_RESOLUTIONS_MS))
    evaluation = _numpy(hundred, "boundary_ms", fill=0, dtype=np.int64)
    if len(evaluation) == 0:
        raise ValueError("Strategy 1 has no completed 100ms liquidity rows")
    hundred_price_valid = _numpy(hundred, "price_valid", fill=0, dtype=np.uint8)
    hundred_indicator = _numpy(hundred, "indicator_resolution_ms", fill=0,
                               dtype=np.int64)
    higher_price_valid = _numpy(higher, "price_valid", fill=0, dtype=np.uint8)
    higher_indicator = _numpy(higher, "indicator_resolution_ms", fill=0,
                              dtype=np.int64)
    higher_resolution = _numpy(higher, "resolution_ms", fill=0, dtype=np.int64)
    if (np.any((hundred_price_valid == 1) & (hundred_indicator != 100))
            or np.any((higher_price_valid == 1)
                      & (higher_indicator != higher_resolution))):
        raise ValueError("Strategy 1 price-bearing bar lacks a pinned indicator row")
    origin = int(market_day_boundary(date.fromisoformat(session_date), 0)
                 .timestamp() * 1_000_000)
    epoch = origin + evaluation * 1000
    macd = {}
    for resolution in MACD_RESOLUTIONS_MS:
        source = _resolution(higher, resolution)
        macd[resolution] = CompletedMacd(
            _numpy(source, "boundary_ms", fill=0, dtype=np.int64),
            _numpy(source, "macd_line", fill=np.nan, dtype=np.float64),
            _numpy(source, "macd_signal", fill=np.nan, dtype=np.float64))
    stops = _resolution(higher, 30_000)
    return prepare_strategy_one_entries(
        evaluation_boundary_ms=evaluation, evaluation_epoch_us=epoch,
        close_int=_numpy(hundred, "close_int", fill=0, dtype=np.int64),
        price_valid=hundred_price_valid,
        bid_int=_numpy(hundred, "bid_int", fill=0, dtype=np.int64),
        ask_int=_numpy(hundred, "ask_int", fill=0, dtype=np.int64),
        quote_valid=_numpy(hundred, "quote_valid", fill=0, dtype=np.uint8),
        quote_timestamp_us=_numpy(hundred, "quote_timestamp_us", fill=0,
                                  dtype=np.int64),
        execution_vwap=_numpy(hundred, "execution_vwap", fill=0,
                              dtype=np.float64),
        cumulative_volume=_numpy(hundred, "cumulative_volume", fill=np.nan,
                                 dtype=np.float64),
        cumulative_notional=_numpy(hundred, "cumulative_notional", fill=np.nan,
                                   dtype=np.float64),
        volume_trade_count=_numpy(hundred, "volume_trade_count", fill=0,
                                  dtype=np.int64),
        previous_close=_numpy(hundred, "previous_close", fill=np.nan,
                              dtype=np.float64),
        macd=macd,
        thirty_second_low=CompletedThirtySecondLow(
            _numpy(stops, "boundary_ms", fill=0, dtype=np.int64),
            _numpy(stops, "low_int", fill=0, dtype=np.int64),
            _numpy(stops, "price_valid", fill=0, dtype=np.uint8),
            _numpy(stops, "extremes_valid", fill=0, dtype=np.uint8)))


def load_strategy_one_entry_batch(plan: CertifiedMarketDayPlan, *,
                                  session_date: str, ticker: str,
                                  through_boundary_ms: int,
                                  client: Any) -> StrategyOneCandidateBatch:
    """Decode one certified session/ticker into a vectorized causal mask.

    The ClickHouse principal must be SELECT-only. No QMD frame, market-event
    replay, SQLite, or strategy-side indicator calculation is used here.
    """
    if (type(through_boundary_ms) is not int or through_boundary_ms <= 0
            or through_boundary_ms % 100):
        raise ValueError("Strategy 1 needs a completed 100ms end boundary")
    scoped = _source_plan(plan, session_date, ticker)
    sources = market_day_source_sqls(scoped,
                                    through_boundary_ms=through_boundary_ms,
                                    strategy_one_projection=True)
    if len(sources) != 2:
        raise RuntimeError("Strategy 1 needs liquidity and higher-bar sources")
    hundred = _table(client, sources[0], maximum_rows=576_000)
    higher = _table(client, sources[1], maximum_rows=80_000)
    return _decode_entry_batch(hundred, higher,
                               session_date=session_date, ticker=ticker)


def load_strategy_one_entry_batches(plan: CertifiedMarketDayPlan, *,
                                    session_date: str, tickers: tuple[str, ...],
                                    through_boundary_ms: int,
                                    client: Any) -> dict[str, StrategyOneCandidateBatch]:
    """Read up to eight pinned ticker scopes in two Arrow queries, then split natively."""
    if (type(through_boundary_ms) is not int or through_boundary_ms <= 0
            or through_boundary_ms % 100 or not 1 <= len(tickers) <= 8
            or tuple(sorted(set(tickers))) != tickers):
        raise ValueError("Strategy 1 Arrow shard needs sorted tickers and a completed boundary")
    scoped = project_market_day_plan(plan, tickers)
    if scoped.sessions != (session_date,):
        raise ValueError("Strategy 1 Arrow shard differs from pinned session")
    source_rows = {(unit.ticker, unit.stage): unit.output_rows for unit in scoped.units}
    if len(source_rows) != 3 * len(tickers):
        raise ValueError("Strategy 1 Arrow shard has duplicate or missing pinned units")
    liquidity_limit = sum(source_rows[(ticker, "broker_100ms")] for ticker in tickers)
    bars_limit = sum(source_rows[(ticker, "bars")] for ticker in tickers)
    if not 0 < liquidity_limit <= 1_000_000:
        raise ValueError("Strategy 1 Arrow shard exceeds the liquidity row budget")
    sources = market_day_source_sqls(scoped,
                                    through_boundary_ms=through_boundary_ms,
                                    strategy_one_projection=True)
    if len(sources) != 2:
        raise RuntimeError("Strategy 1 needs liquidity and higher-bar sources")
    hundred = _table(client, sources[0], maximum_rows=liquidity_limit)
    higher = _table(client, sources[1], maximum_rows=bars_limit)
    if (set(pc.unique(hundred["ticker"]).to_pylist()) != set(tickers)
            or set(pc.unique(higher["ticker"]).to_pylist()) != set(tickers)):
        raise ValueError("Strategy 1 Arrow shard omitted a pinned ticker")
    return {ticker: _decode_entry_batch(
        hundred.filter(pc.equal(hundred["ticker"], ticker)),
        higher.filter(pc.equal(higher["ticker"], ticker)),
        session_date=session_date, ticker=ticker)
        for ticker in tickers}

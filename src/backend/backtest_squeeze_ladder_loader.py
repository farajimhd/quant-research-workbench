"""Bounded SELECT-only prepared ladder observations, not a published strategy.

Reuse native source certification and Signal Stream admission proof. Load full
completed observations instead of Strategy 1's filtered candidate product.
No source writer, private indicator, broker mutation or journal authority lives
in this reader. Preflight and the eventual numbered release remain required.
"""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from typing import Any, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, market_day_boundary, market_day_source_sqls,
    project_market_day_plan,
)
from src.backend.backtest_strategy_one_loader import _assert_scope, _numpy, _table
from src.backend.backtest_strategy_one_preparation import _episode_starts
from src.backend.fixed_bar_signal import first_squeeze_sql
from src.trading_runtime.squeeze_ladder_columnar import (
    LadderGateBatch, LadderGatePolicy, compile_ladder_gate,
)


@dataclass(frozen=True, slots=True)
class PreparedLadderObservations:
    ticker: str
    market_plan_token: str
    source_build_id: str
    scan_content_hash: str
    completed_source: pa.Table
    gate: LadderGateBatch


def load_ladder_observations(plan: CertifiedMarketDayPlan, *, session_date: str,
                             tickers: tuple[str, ...], through_boundary_ms: int,
                             certified_scan: Mapping[str, Any], policy: LadderGatePolicy,
                             client: Any) -> tuple[PreparedLadderObservations, ...]:
    """Read at most eight scopes in one bounded Arrow query, retaining evidence.

    The source table preserves original VWAP bits and quote/liquidity operands
    for survivor binding. Its plan token pins bar/technical/broker attempts.
    The caller must use a market principal whose grants were checked in
    preflight; this module exposes no write or repair path.
    """
    if (not isinstance(plan, CertifiedMarketDayPlan) or plan.sessions != (session_date,)
            or plan.execution_interval.kind != 'fixed' or plan.execution_interval.milliseconds != 100
            or not isinstance(tickers, tuple) or not 1 <= len(tickers) <= 8
            or tuple(sorted(set(tickers))) != tickers
            or type(through_boundary_ms) is not int or not 0 < through_boundary_ms <= 57_600_000
            or through_boundary_ms % 100 or not isinstance(certified_scan, Mapping)
            or not isinstance(policy, LadderGatePolicy)):
        raise ValueError("Ladder observations require a pinned bounded 100ms scope")
    authority = dict(certified_scan.get('authority') or {})
    occurrences = certified_scan.get('occurrences')
    expected_query = sha256(first_squeeze_sql(plan, through_boundary_ms=through_boundary_ms).encode()).hexdigest()
    if (not isinstance(occurrences, list) or authority.get('query_sha256') != expected_query
            or authority.get('content_hash') != sha256(json.dumps(
                occurrences, sort_keys=True, separators=(',', ':')).encode()).hexdigest()):
        raise ValueError("Ladder Signal Stream proof differs from its pinned query/content")
    episodes = _episode_starts(plan, session_date, certified_scan)
    scoped = project_market_day_plan(plan, tickers)
    rows = {(unit.ticker, unit.stage): unit.output_rows for unit in scoped.units}
    if (len(rows) != 3 * len(tickers) or any((ticker, stage) not in rows
            for ticker in tickers for stage in ('bars', 'technical', 'broker_100ms'))):
        raise ValueError("Ladder observations lack unique certified source attempts")
    maximum = sum(rows[(ticker, 'broker_100ms')] for ticker in tickers)
    if not 0 < maximum <= 1_000_000:
        raise ValueError("Ladder Arrow scope exceeds the source row budget")
    sqls = market_day_source_sqls(scoped, through_boundary_ms=through_boundary_ms,
                                strategy_one_projection=True)
    if len(sqls) != 2:
        raise ValueError("Native source projection differs from its declared contract")
    source = _table(client, sqls[0], maximum_rows=maximum)
    if set(pc.unique(source['ticker']).to_pylist()) != set(tickers):
        raise ValueError("Ladder Arrow source omitted a pinned ticker")
    origin = int(market_day_boundary(date.fromisoformat(session_date), 0).timestamp()) * 1_000_000
    result = []
    for ticker in tickers:
        table = source.filter(pc.equal(source['ticker'], ticker))
        _assert_scope(table, session_date=session_date, ticker=ticker, resolutions=frozenset({100}))
        boundary = _numpy(table, 'boundary_ms', fill=0, dtype=np.int64)
        if np.any(boundary > through_boundary_ms):
            raise ValueError("Ladder Arrow source exceeds its completed run prefix")
        if table['volume_trade_count'].null_count:
            raise ValueError("Ladder source has missing eligible bucket trade counts")
        gate = compile_ladder_gate(policy=policy, boundary_ms=boundary,
            evaluation_epoch_us=origin + boundary * 1000,
            close_int=_numpy(table, 'close_int', fill=0, dtype=np.int64),
            price_valid=_numpy(table, 'price_valid', fill=0, dtype=np.uint8),
            execution_vwap=_numpy(table, 'execution_vwap', fill=np.nan, dtype=np.float64),
            bid_int=_numpy(table, 'bid_int', fill=0, dtype=np.int64),
            ask_int=_numpy(table, 'ask_int', fill=0, dtype=np.int64),
            quote_valid=_numpy(table, 'quote_valid', fill=0, dtype=np.uint8),
            quote_timestamp_us=_numpy(table, 'quote_timestamp_us', fill=0, dtype=np.int64),
            cumulative_volume=_numpy(table, 'cumulative_volume', fill=np.nan, dtype=np.float64),
            cumulative_notional=_numpy(table, 'cumulative_notional', fill=np.nan, dtype=np.float64),
            volume_trade_count=_numpy(table, 'volume_trade_count', fill=0, dtype=np.int64),
            admission_boundaries_ms=np.array(episodes.get(ticker, ()), dtype=np.int64))
        result.append(PreparedLadderObservations(ticker, plan.token, plan.build_id,
                                                authority['content_hash'], table, gate))
    return tuple(result)

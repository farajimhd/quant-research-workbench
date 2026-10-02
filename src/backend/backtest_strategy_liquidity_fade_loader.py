"""One bounded SELECT of pinned native 5s activity for a prepared lookup.

No derived market writer, raw SIP fallback or per-decision query exists. The
caller must independently certify the supplied plan before using this loader.
"""
from datetime import date

import polars as pl

from .backtest_market_data import (
    CertifiedMarketDayPlan, MarketDayUnit, SESSION_OPEN_OFFSET_MS, _literal, assert_select_only,
)
from .backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup


def load_compiled_liquidity_fade_lookup(client, *, plan, session_date, max_rows=2_000_000,
                                      tickers=None, after_boundary_ms=0,
                                      through_boundary_ms=57_600_000, strategy_number=35):
    """Stream at most max_rows+1 source rows, then vectorize once.

    The extra row is an overflow sentinel, never a silent population cut. Arrow
    batches are bounded to 64MiB and no producer row is filtered or deduplicated
    after read. A necessary-condition candidate reduction may narrow ticker
    and session I/O; the cache retains the original certified plan identity.
    Whole candles start at or after the lower boundary and end by the upper
    boundary. The cache serves completed-only lookups without network access.
    """
    if (type(plan) is not CertifiedMarketDayPlan or type(session_date) is not date
            or plan.sessions != (session_date.isoformat(),)
            or 5000 not in plan.required_resolutions_ms
            or type(max_rows) is not int or not 1 <= max_rows <= 20_000_000
            or type(plan.units) is not tuple or len(plan.units) > 65_536
            or any(type(u) is not MarketDayUnit for u in plan.units)):
        raise ValueError('Liquidity loader requires one bounded certified source plan')
    units = tuple(u for u in plan.units if u.stage == 'bars')
    if (not units or len({u.ticker for u in units}) != len(units)
            or {u.ticker for u in units} != set(plan.tickers)
            or any(u.build_id != plan.build_id or u.session_date != session_date.isoformat() for u in units)):
        raise ValueError('Liquidity loader has missing, duplicate or foreign native bar units')
    if type(strategy_number) is not int or strategy_number not in (35,36,37,38,39):
        raise ValueError('Liquidity loader requires an exact supported strategy number')
    selected = plan.tickers if tickers is None else tickers
    if (type(selected) is not tuple or any(type(t) is not str or not t for t in selected)
            or len(set(selected)) != len(selected) or set(selected) - set(plan.tickers)
            or type(after_boundary_ms) is not int or type(through_boundary_ms) is not int
            or not 0 <= after_boundary_ms < through_boundary_ms <= 57_600_000
            or after_boundary_ms % 100 or through_boundary_ms % 100):
        raise ValueError('Liquidity loader scope differs from certified tickers or session clocks')
    selected_set = set(selected)
    units = tuple(u for u in units if u.ticker in selected_set)
    lower_bucket = (SESSION_OPEN_OFFSET_MS + after_boundary_ms + 4999) // 5000
    upper_bucket = (SESSION_OPEN_OFFSET_MS + through_boundary_ms) // 5000
    empty = pl.DataFrame(schema=dict(source_build_id=pl.String, session_date=pl.String,
        ticker=pl.String, source_attempt_id=pl.String, boundary_ms=pl.UInt64, trade_count=pl.UInt64))
    if not units or lower_bucket >= upper_bucket:
        return CompiledLiquidityFadeLookup(empty, plan=plan, session_date=session_date,
                                           max_rows=max_rows, strategy_number=strategy_number)
    attempts = ','.join(f'({_literal(u.ticker)},toUUID({_literal(u.attempt_id)}))' for u in units)
    query = assert_select_only(
        'SELECT build_id AS source_build_id,session_date,ticker,'
        'toString(attempt_id) AS source_attempt_id,'
        f'(toUInt64(bucket_index)+1)*5000-{SESSION_OPEN_OFFSET_MS} AS boundary_ms,trade_count '
        f'FROM arte.bars_v1 WHERE build_id={_literal(plan.build_id)} '
        f'AND session_date=toDate({_literal(session_date.isoformat())}) '
        f'AND (ticker,attempt_id) IN ({attempts}) AND resolution_ms=5000 '
        f'AND bucket_index>={lower_bucket} '
        f'AND bucket_index<{upper_bucket} '
        f'ORDER BY ticker,bucket_index LIMIT {max_rows+1} FORMAT ArrowStream')
    stream = client.iter_arrow_record_batches(query)
    frames, count = [], 0
    expected = ('source_build_id', 'session_date', 'ticker', 'source_attempt_id', 'boundary_ms', 'trade_count')
    try:
        for batch in stream:
            if tuple(batch.schema.names) != expected or batch.nbytes > 64*1024*1024:
                raise ValueError('Liquidity source batch has changed shape or exceeds 64MiB')
            count += batch.num_rows
            if count > max_rows:
                raise ValueError(f'Liquidity source exceeds {max_rows} rows; incomplete load rejected')
            frame = pl.from_arrow(batch).with_columns(pl.col('session_date').cast(pl.String))
            # SQL scope is part of the load contract, not a post-read filter.
            # Reject unexpected rows instead of silently discarding them.
            if frame.select((
                ~pl.col('ticker').is_in(selected)
                | (pl.col('boundary_ms') < after_boundary_ms + 5000)
                | (pl.col('boundary_ms') > through_boundary_ms)
            ).fill_null(True).any()).item():
                raise ValueError('Liquidity source returned rows outside its requested scope')
            frames.append(frame)
    finally:
        close = getattr(stream, 'close', None)
        if close is not None:
            close()
    if frames:
        frame = pl.concat(frames)
    else:
        frame = empty
    return CompiledLiquidityFadeLookup(frame, plan=plan, session_date=session_date,
                                       max_rows=max_rows, strategy_number=strategy_number)

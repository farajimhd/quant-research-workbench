"""Bounded certified arte reader for research; never installs feature products."""
import polars as pl
import pyarrow as pa
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, FIXED_RESOLUTIONS_MS, _literal, verify_market_day_plan,
)
from pipelines.market_sip.events.completed_endpoint_return_producer import read_arrow
from .native_bars import COLUMNS, native_channels

SOURCE_SCHEMA = pa.schema([pa.field(name,kind,nullable=False) for name,kind in [
    *((name,pa.string()) for name in ('build_id','session_date','ticker','attempt_id')),
    ('resolution_ms',pa.uint32()),('bucket_index',pa.uint32()),
    *((name,pa.uint64()) for name in ('open_int','high_int','low_int','close_int')),
    ('execution_volume',pa.float64()),('execution_notional',pa.float64()),
    ('trade_count',pa.uint64()),('price_valid',pa.uint8()),('extremes_valid',pa.uint8()),
]])
MAX_PACKET_ROWS = 2_000_000
MAX_PACKET_TICKERS = 8


def certified_channel_packets(market, client, *, session_date, tickers,
                              resolutions_ms, through_day_boundary_ms,
                              lookback_bars=5):
    """Yield bounded packets, verifying market authority once before source reads.

    All earlier regular-session source rows remain available for AH warming.
    This supplies research rows only, not a new published input certificate.
    """
    if type(market) is not CertifiedMarketDayPlan:
        raise ValueError('Exact certified market plan required')
    if (type(tickers) is not tuple or not tickers or len(set(tickers))!=len(tickers) or
            any(type(t) is not str for t in tickers) or not set(tickers).issubset(market.tickers) or
            session_date not in market.sessions):
        raise ValueError('Requested ticker/day scope differs from market certificate')
    if (type(resolutions_ms) is not tuple or not resolutions_ms or
            len(set(resolutions_ms))!=len(resolutions_ms) or
            any(type(r) is not int or r not in FIXED_RESOLUTIONS_MS for r in resolutions_ms) or
            not set(resolutions_ms).issubset(market.required_resolutions_ms)):
        raise ValueError('Feature resolutions must be native and explicitly certified')
    if (type(through_day_boundary_ms) is not int or not 0<=through_day_boundary_ms<=86400000 or
            type(lookback_bars) is not int or lookback_bars<=0):
        raise ValueError('Explicit source boundary and trailing baseline required')
    verify_market_day_plan(market,client)
    units = {u.ticker:u for u in market.units if u.stage=='bars' and u.session_date==session_date}
    if not set(tickers).issubset(units):
        raise ValueError('Requested bar source units are missing')
    per_ticker = sum(through_day_boundary_ms//r for r in resolutions_ms)
    packet_size = min(MAX_PACKET_TICKERS, MAX_PACKET_ROWS//max(1,per_ticker))
    if packet_size<1:
        raise ValueError('Declared resolution packet exceeds source row bound')
    for offset in range(0,len(tickers),packet_size):
        selected = tickers[offset:offset+packet_size]
        pins = ','.join(f'({_literal(t)},toUUID({_literal(units[t].attempt_id)}))' for t in selected)
        resolutions = ','.join(str(r) for r in resolutions_ms)
        row_bound = min(MAX_PACKET_ROWS,per_ticker*len(selected))
        sql = ('SELECT build_id,toString(session_date) AS session_date,toString(ticker) AS ticker,'
               'toString(attempt_id) AS attempt_id,resolution_ms,bucket_index,'
               'open_int,high_int,low_int,close_int,execution_volume,execution_notional,'
               'trade_count,price_valid,extremes_valid FROM (SELECT '
               'build_id,session_date,ticker,attempt_id,resolution_ms,bucket_index,'
               'open_int,high_int,low_int,close_int,execution_volume,execution_notional,'
               'trade_count,price_valid,extremes_valid FROM arte.bars_v1 '
               f'WHERE build_id={_literal(market.build_id)} AND session_date=toDate({_literal(session_date)}) '
               f'AND (ticker,attempt_id) IN ({pins}) AND resolution_ms IN ({resolutions}) '
               f'AND (toUInt64(bucket_index)+1)*resolution_ms<={through_day_boundary_ms} '
               'ORDER BY ticker,resolution_ms,bucket_index '
               f'LIMIT {row_bound+1}) FORMAT ArrowStream')
        # Read-only principals retain their server resource policy. The extra
        # row is an overflow sentinel: read_arrow rejects it, never truncates.
        table = read_arrow(client,sql,SOURCE_SCHEMA,row_bound)
        raw = pl.from_arrow(table)
        expected = pl.DataFrame({'ticker':selected,'attempt_id':[units[t].attempt_id for t in selected]})
        if (raw.filter((pl.col('build_id')!=market.build_id)|(pl.col('session_date')!=session_date)).height or
                raw.join(expected,on=['ticker','attempt_id'],how='anti').height or
                raw.filter((pl.col('bucket_index').cast(pl.Int64)+1)*pl.col('resolution_ms')>through_day_boundary_ms).height):
            raise ValueError('Native source read returned foreign identity or future rows')
        yield native_channels(raw.select(COLUMNS),resolutions_ms=resolutions_ms,
                              through_day_boundary_ms=through_day_boundary_ms,
                              lookback_bars=lookback_bars)

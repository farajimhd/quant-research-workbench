"""Read-only projected ClickHouse funnel. All market reads are ARTE only."""
from .contract import origin_us


def literal(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def names(tickers):
    if not tickers:
        raise ValueError('Empty ticker scope')
    return ','.join(literal(t) for t in sorted(tickers))


def scope(build, day, attempts):
    if not attempts:
        raise ValueError('Empty certified attempt scope')
    pairs = ','.join(f'({literal(t)},toUUID({literal(a)}))' for t, a in sorted(attempts.items()))
    return f'build_id={literal(build)} AND session_date=toDate({literal(day)}) AND (ticker,attempt_id) IN ({pairs})'


def daily(day, tickers):
    return f"""SELECT source_ticker AS ticker,bar_end_us AS time_us,available_at_us,
      trade_open AS open,trade_high AS high,trade_low AS low,trade_close AS close,
      trade_size_sum AS volume,trade_event_count AS trade_count,trade_price_size_sum AS notional,
      trade_present AS price_valid,trade_present AS extremes_valid
      FROM arte.daily_session_bars_by_symbol_time_v1 FINAL
      WHERE source_ticker IN ({names(tickers)}) AND session_date=toDate({literal(day)})
      AND session_kind='regular' AND feature_version='tradable_timely_daily_sessions_v1'
      AND source_contract='tradable_timely_canonical_v1' AND adjusted=0 ORDER BY ticker,time_us"""


def previous_close(build,day,attempts,regular):
    low,high=regular
    return f"""SELECT ticker,toInt64({origin_us(day)+high*1000000}) time_us,
      toInt64({origin_us(day)+high*1000000}) available_at_us,
      argMax(close_int,bucket_index)/10000. AS close,toUInt8(1) price_valid
      FROM arte.bars_v1 WHERE {scope(build,day,attempts)} AND resolution_ms=60000
      AND bucket_index>={low//60} AND bucket_index<{high//60} AND price_valid=1
      GROUP BY ticker ORDER BY ticker,time_us"""


def ranking(build, day, attempts):
    # Sparse event deltas hold a score through missing seconds and remove it
    # exactly sixty seconds later. Only five tuples/clock survive aggregation.
    return f"""WITH deltas AS (
      SELECT ticker,toInt64(bucket_index) AS second,toInt64(trade_count) AS delta
      FROM arte.bars_v1 WHERE {scope(build, day, attempts)} AND resolution_ms=1000
      AND bucket_index>=14400 AND bucket_index<34200 AND trade_count>0
    ), changes AS (
      SELECT ticker,second,sum(delta) AS delta FROM (
        SELECT ticker,second+offset AS second,if(offset=0,delta,-delta) AS delta
        FROM deltas ARRAY JOIN [0,60] AS offset
      ) WHERE second<34200 GROUP BY ticker,second
    ), segments AS (
      SELECT ticker,second,
        sum(delta) OVER (PARTITION BY ticker ORDER BY second ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS score,
        leadInFrame(second,1,toInt64(34200)) OVER (PARTITION BY ticker ORDER BY second ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) AS until
      FROM changes
    ), leaders AS (
      SELECT clock,groupArraySorted(5)(tuple(-score,ticker)) AS top
      FROM segments ARRAY JOIN range(toUInt32(second+1),toUInt32(until+1)) AS clock
      WHERE score>0 GROUP BY clock
    ) SELECT toInt64(clock)*1000000+{origin_us(day)} AS time_us,
      toUInt8(rank) AS rank,tupleElement(top[rank],2) AS ticker,
      toUInt64(-tupleElement(top[rank],1)) AS trade_count_60s
      FROM leaders ARRAY JOIN arrayEnumerate(top) AS rank ORDER BY time_us,rank"""


def trade(build, day, attempts, resolution, full=False):
    lo, hi = 14400000//resolution, (72000000 if full else 34200000)//resolution
    return f"""SELECT ticker,{origin_us(day)}+(toInt64(bucket_index)+1)*{resolution*1000} AS time_us,
      open_int/10000. AS open,high_int/10000. AS high,low_int/10000. AS low,close_int/10000. AS close,
      volume,trade_count,notional,execution_volume,execution_notional,price_valid,extremes_valid
      FROM arte.bars_v1 WHERE {scope(build, day, attempts)} AND resolution_ms={resolution}
      AND bucket_index>={lo} AND bucket_index<{hi} ORDER BY ticker,time_us"""


def quotes(build, day, attempts):
    return f"""SELECT ticker,toString(side) AS side,{origin_us(day)}+(toInt64(bucket_index)+1)*1000000 AS time_us,
      open_int/10000. AS open,high_int/10000. AS high,low_int/10000. AS low,close_int/10000. AS close,
      close_size,event_count,first_event_us,last_event_us
      FROM arte.quote_bars_v1 WHERE {scope(build, day, attempts)} AND resolution_ms=1000
      AND bucket_index>=14400 AND bucket_index<34200 ORDER BY ticker,side,time_us"""


def execution(build, day, attempts, begin, end):
    # Same close-of-second view and timestamp/size contract as the V64 sidecar.
    origin = origin_us(day)
    valid = f'quote_timestamp_us>0 AND quote_timestamp_us<=last_event_us AND last_event_us<{origin}+(toInt64(bucket_index)+1)*100000'
    return f"""SELECT ticker,{origin}+(intDiv(toInt64(bucket_index),10)+1)*1000000 AS time_us,
      sum(execution_volume) AS capacity,
      argMaxIf(close_int,bucket_index,price_valid=1)/10000. AS mark,
      argMaxIf(bid_int,bucket_index,{valid})/10000. AS bid,
      argMaxIf(ask_int,bucket_index,{valid})/10000. AS ask,
      argMaxIf(bid_size,bucket_index,{valid}) AS bid_size,
      argMaxIf(ask_size,bucket_index,{valid}) AS ask_size,
      argMaxIf(quote_valid,bucket_index,{valid}) AS book_valid,
      argMaxIf(quote_timestamp_us,bucket_index,{valid}) AS quote_us
      FROM arte.liquidity_100ms_v1 WHERE {scope(build, day, attempts)}
      AND bucket_index>={begin*10} AND bucket_index<{end*10}
      GROUP BY ticker,time_us ORDER BY ticker,time_us"""

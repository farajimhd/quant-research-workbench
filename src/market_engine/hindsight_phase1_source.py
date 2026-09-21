"""Read-only, ordinal-pruned canonical SQL for compact Phase 1 inputs."""
import json
from io import StringIO
import polars as pl
from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_url, default_clickhouse_user, default_clickhouse_password
from src.backend.swing_book_indexed_source import ordinal_bounds
from src.market_engine.hindsight_phase1 import bounds

RULE_SQL="SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id"


def literal(value):
    return "'"+str(value).replace("\\","\\\\").replace("'","\\'")+"'"


def client(threads=2):
    return ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password(),timeout_seconds=180,
        default_query_params=dict(readonly=1,max_execution_time=150,max_threads=threads,max_memory_usage=2147483648,
                                  max_result_rows=200000,max_result_bytes=100000000,result_overflow_mode='throw'))


def query(c, sql):
    return [json.loads(line) for line in c.execute(sql+' FORMAT JSONEachRow').splitlines() if line]


def quote_frame(c, sql):
    """Native columnar parsing, without 60,000 Python row dictionaries."""
    return pl.read_csv(StringIO(c.execute(sql+' FORMAT CSVWithNames')), null_values='\\N',
        schema_overrides={'time_us':pl.Int64,'quote_us':pl.Int64,'ask':pl.Float64,
                          'bid':pl.Float64,'ask_size':pl.Float64,'bid_size':pl.Float64})


def universe_sql(day):
    return f"SELECT ticker,listing_id,symbol_id,security_id,ibkr_conid,product_type,asset_class,currency_code,exchange_code,source_run_id FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date={literal(day)} AND is_tradable=1 ORDER BY ticker"


def validate_universe(rows):
    if not rows:raise ValueError('No dated tradable-listing snapshot; latest/current snapshots are not substitutes')
    for key in ('ticker','listing_id'):
        if any(not r[key] for r in rows) or len({r[key] for r in rows})!=len(rows):raise ValueError('Ambiguous dated tradable '+key)
    return rows


def metadata(c,day,ticker):
    where=f"source_date={literal(day)} AND ticker={literal(ticker)}"
    source=query(c,'SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE '+where)
    if len(source)!=1:raise ValueError('Missing certified canonical SIP day')
    ordinal_bounds(source[0])
    return dict(source=source[0],timing_policy='canonical_sip_without_execution_clock_sidecar')



def predicate(day,ticker,meta):
    first,stop=ordinal_bounds(meta['source']);left,right=bounds(day)
    return f"e.ticker={literal(ticker)} AND e.ordinal>={first} AND e.ordinal<{stop} AND e.sip_timestamp_us>={left} AND e.sip_timestamp_us<{right}"


def extrema_sql(day,ticker,meta,rules):
    ids=lambda predicate:'['+','.join(str(r['token_id']) for r in rules if predicate(r))+']'
    known=ids(lambda r:0<=r['modifier_int']<=65535)
    both=ids(lambda r:r['update_last']==r['update_high_low']==1 or r['modifier_int'] in (0,12))
    form=ids(lambda r:r['modifier_int']==12);last=ids(lambda r:r['update_last']==1)
    return f"""WITH arrayFilter(x->has({known},x),[condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5]) AS tokens,
      fromUnixTimestamp64Micro(toInt64(e.sip_timestamp_us),'America/New_York') AS local_time,
      toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS sec,
      ((sec<34200 OR sec>=57600) AND hasAny(tokens,{form}) AND arrayAll(x->has({both},x),tokens)) AS form_t,
      toFloat64(price_primary_int)/if(bitAnd(event_meta,2)=2,10000.,100.) AS price,
      (arrayAll(x->has({last},x) OR (form_t AND has({form},x)),tokens) AND sec>=14700
       AND price>0) AS eligible
    SELECT intDiv(e.sip_timestamp_us+999999,1000000)*1000000 decision_us,
      e.sip_timestamp_us%1000000=0 boundary,
      countIf(eligible) prices,countIf(eligible AND size_primary>0 AND isFinite(size_primary)) trades,sumIf(toFloat64(size_primary),eligible AND size_primary>0 AND isFinite(size_primary)) volume,
      argMinIf(tuple(e.sip_timestamp_us,e.ordinal,price),tuple(price,e.sip_timestamp_us,e.ordinal),eligible) lo,
      argMinIf(tuple(e.sip_timestamp_us,e.ordinal,price),tuple(-price,e.sip_timestamp_us,e.ordinal),eligible) hi
    FROM market_sip_compact.events_{day.year} e
    WHERE {predicate(day,ticker,meta)} AND bitAnd(event_meta,1)=1
    GROUP BY decision_us,boundary ORDER BY decision_us,boundary"""


def quotes_sql(day,ticker,meta,targets):
    left,right=bounds(day)
    quote_predicate=predicate(day,ticker,meta).replace(f'e.sip_timestamp_us>={left}',f'e.sip_timestamp_us>={left-1000000}').replace(f'e.sip_timestamp_us<{right}',f'e.sip_timestamp_us<={right}')
    target_times=','.join(str(round(p['exit_time']*1e6)) for p in targets)
    # Include invalid last quotes: an invalid quote must invalidate the sample.
    return f"""SELECT p.time_us time_us,q.quote_us quote_us,q.v.1 ask,q.v.2 bid,q.v.3 ask_size,q.v.4 bid_size
    FROM (SELECT toUInt8(1) k,arrayJoin(arraySort(arrayDistinct(arrayConcat(range(toUInt64({left}),toUInt64({right+1}),toUInt64(1000000)),CAST([{target_times}], 'Array(UInt64)'))))) time_us ORDER BY time_us) p
    ASOF LEFT JOIN (SELECT toUInt8(1) k,e.sip_timestamp_us quote_us,
       argMax(tuple(toFloat64(price_primary_int)/if(bitAnd(event_meta,2)=2,10000.,100.),
                    toFloat64(price_secondary_int)/if(bitAnd(event_meta,4)=4,10000.,100.),
                    toFloat64(size_primary),toFloat64(size_secondary)),ordinal) v
       FROM market_sip_compact.events_{day.year} e WHERE {quote_predicate} AND bitAnd(event_meta,1)=0
       GROUP BY quote_us ORDER BY quote_us) q ON p.k=q.k AND p.time_us>=q.quote_us ORDER BY time_us"""

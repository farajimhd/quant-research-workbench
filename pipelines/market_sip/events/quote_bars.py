"""Exact, sparse two-sided quote OHLC; no snapshot or carried-price extrema.

One canonical scan builds bounded one-second sufficient statistics. Rollups and
readback stay in ClickHouse. Runtime receipts refer to immutable attempts;
consumer reads must join the coverage commit boundary.
"""
from datetime import date, timedelta
from .market_day_sql import literal as L, identifier, bounds
from pathlib import Path
from pipelines.certified_research.contracts import digest

VERSION='canonical-quote-ohlc-v1'
FRAMES=(1000,5000,10000,30000)
PRICE_FIELDS=('open_int','high_int','low_int','close_int')
FIELDS=('build_id','session_date','ticker','attempt_id','resolution_ms','bucket_index','side',
        *PRICE_FIELDS,'close_size','event_count','first_event_us','last_event_us')
ORDER='build_id,session_date,ticker,resolution_ms,side,bucket_index,attempt_id'


def recipe(parent):
    from . import market_day_sql
    return digest(dict(version=VERSION,source=Path(__file__).read_text(encoding='utf-8'),
                       shared_source=Path(market_day_sql.__file__).read_text(encoding='utf-8'),
                       parent=parent,frames=FRAMES,quote_policy='existing-broker-structural-pair-validity'))


def broker_aggregates():
    expressions=['countIf(valid_quote)']
    for price in ('secondary_int','price_int'):
        expressions.extend([f'argMinIf({price},tuple(sip_timestamp_us,ordinal),valid_quote)',
            f'maxIf({price},valid_quote)',f'minIf({price},valid_quote)',
            f'argMaxIf({price},tuple(sip_timestamp_us,ordinal),valid_quote)'])
    expressions.extend(['argMaxIf(toFloat64(size_secondary),tuple(sip_timestamp_us,ordinal),valid_quote)',
        'argMaxIf(toFloat64(size_primary),tuple(sip_timestamp_us,ordinal),valid_quote)',
        'minIf(sip_timestamp_us,valid_quote)','maxIf(sip_timestamp_us,valid_quote)'])
    return ','.join(expressions)


EXTRAS=(('quote_count','UInt64'),*((s+'_'+p,'UInt64') for s in ('bid','ask') for p in ('open','high','low','close')),
        ('bid_close_size','Float64'),('ask_close_size','Float64'),('first_quote_us','UInt64'),('last_quote_us','UInt64'))


def forward_broker(client,db,build,day,ticker,attempt,rules,source_hash,settings=''):
    """One original broker scan also supplies exact quote sufficient statistics.

    Ephemeral SSD staging holds 100ms aggregates, not a raw-event copy. The
    existing liquidity/trade calculation and its validation remain unchanged.
    """
    from . import market_day_sql as market
    import uuid
    stage='quote_forward_'+uuid.uuid4().hex
    target=f'{db}.{stage}'
    try:
        client.query(f"CREATE TABLE {target} AS {db}.liquidity_100ms_v1 ENGINE=MergeTree ORDER BY bucket_index SETTINGS storage_policy='live_market_ssd'",'shared_broker_stage',False)
        client.query(f"ALTER TABLE {target} "+','.join('ADD COLUMN _q_'+n+' '+t for n,t in EXTRAS),'shared_quote_columns',False)
        query=market.broker_sql(db,build,day,ticker,attempt,rules,verified_timely=True,
            table_names={'broker_100ms':stage},quote_ohlc=True)
        client.query(query+settings,'shared_trade_quote_scan',False)
        columns=client.query(f"SELECT name FROM system.columns WHERE database={L(db)} AND table='liquidity_100ms_v1' ORDER BY position",'broker_column_contract')
        client.query(f"INSERT INTO {db}.liquidity_100ms_v1 SELECT "+','.join(identifier(x['name']) for x in columns)+f' FROM {target}','publish_existing_liquidity',False)
        policy=recipe(build)
        prior=client.query(f"SELECT * FROM {db}.quote_bars_coverage_v1 FINAL WHERE {scope(policy,day,ticker)}",'forward_quote_receipt')
        if prior:
            if len(prior)!=1 or prior[0]['source_hash']!=source_hash or prior[0]['policy_hash']!=policy:raise ValueError('Forward quote receipt changed')
            r=prior[0];e=client.query(evidence(db,policy,day,ticker,r['attempt_id']),'forward_quote_reuse')[0]
            if int(e['bad']) or e['n']!=r['row_count'] or e['hash']!=r['content_hash']:raise ValueError('Forward quote reuse differs')
            return
        # One quote row per observed side/second. No synthetic empty bars.
        aggregates=[]
        for side in ('bid','ask'):
            aggregates.extend([f'argMinIf(_q_{side}_open,bucket_index,_q_quote_count>0) {side}_open',
                f'maxIf(_q_{side}_high,_q_quote_count>0) {side}_high',
                f'minIf(_q_{side}_low,_q_quote_count>0) {side}_low',
                f'argMaxIf(_q_{side}_close,bucket_index,_q_quote_count>0) {side}_close',
                f'argMaxIf(_q_{side}_close_size,bucket_index,_q_quote_count>0) {side}_size'])
        query=f"SELECT intDiv(bucket_index,10) second_bucket,"+','.join(aggregates)+f",sum(_q_quote_count) quote_count,minIf(_q_first_quote_us,_q_quote_count>0) first_quote_us,max(_q_last_quote_us) last_quote_us FROM {target} GROUP BY intDiv(bucket_index,10)"
        client.query(f"INSERT INTO {db}.quote_bars_v1 SELECT {L(policy)},toDate({L(day)}),{L(ticker)},toUUID({L(attempt)}),toUInt32(1000),toUInt32(second_bucket),side,"+
            ','.join(f"if(side='bid',bid_{p},ask_{p})" for p in ('open','high','low','close'))+
            f",if(side='bid',bid_size,ask_size),quote_count,first_quote_us,last_quote_us FROM ({query}) ARRAY JOIN ['bid','ask'] side WHERE quote_count>0",'forward_quote_1s',False)
        for parent,target_frame in zip(FRAMES,FRAMES[1:]):
            client.query(f'INSERT INTO {db}.quote_bars_v1 '+rollup_select(db,policy,day,ticker,attempt,parent,target_frame),'forward_quote_rollup',False)
            if client.query(parity(db,policy,day,ticker,attempt,parent,target_frame),'forward_quote_parity')[0]['n']:raise ValueError('Forward quote rollup differs')
        e=client.query(evidence(db,policy,day,ticker,attempt),'forward_quote_readback')[0]
        if int(e['bad']) or e['n']!=e['unique_keys']:raise ValueError('Forward quote values/keys differ')
        expected=client.query(f'SELECT sum(_q_quote_count) n FROM {db}.{stage}','forward_quote_count')[0]['n']
        actual=client.query(f'SELECT resolution_ms,side,sum(event_count) n FROM {db}.quote_bars_v1 WHERE {scope(policy,day,ticker,attempt)} GROUP BY resolution_ms,side','forward_quote_conservation')
        if (expected and (len(actual)!=8 or any(x['n']!=expected for x in actual))) or (not expected and actual):raise ValueError('Forward quote event conservation differs')
        verify_storage(client.query,db,['quote_bars_v1','quote_bars_coverage_v1',stage])
        client.query(f"INSERT INTO {db}.quote_bars_coverage_v1 VALUES ({L(policy)},toDate({L(day)}),{L(ticker)},toUUID({L(attempt)}),{L(source_hash)},{L(policy)},{int(e['n'])},{L(e['hash'])},now64(6))",'forward_quote_commit',False)
    finally:client.query(f'DROP TABLE IF EXISTS {db}.{stage} SYNC','shared_stage_cleanup',False)


def ddl(db='arte'):
    identifier(db)
    return (
        f"CREATE TABLE IF NOT EXISTS {db}.quote_bars_v1 (build_id String,session_date Date,ticker LowCardinality(String),attempt_id UUID,resolution_ms UInt32,bucket_index UInt32,side Enum8('bid'=1,'ask'=2),open_int UInt64,high_int UInt64,low_int UInt64,close_int UInt64,close_size Float64,event_count UInt64,first_event_us UInt64,last_event_us UInt64) ENGINE=MergeTree ORDER BY ({ORDER}) PARTITION BY toYYYYMM(session_date) SETTINGS storage_policy='live_market_ssd',index_granularity=1024",
        f"CREATE TABLE IF NOT EXISTS {db}.quote_bars_coverage_v1 (build_id String,session_date Date,ticker LowCardinality(String),attempt_id UUID,source_hash String,policy_hash String,row_count UInt64,content_hash String,certified_at DateTime64(6,'UTC')) ENGINE=ReplacingMergeTree ORDER BY (build_id,session_date,ticker) PARTITION BY toYYYYMM(session_date) SETTINGS storage_policy='live_market_ssd'",
        f"CREATE VIEW IF NOT EXISTS {db}.quote_bars_current_v1 AS SELECT b.* FROM {db}.quote_bars_v1 b INNER JOIN (SELECT build_id,session_date,ticker,attempt_id FROM {db}.quote_bars_coverage_v1 FINAL) c USING(build_id,session_date,ticker,attempt_id)",
    )


def verify_storage(query, db, tables, *, required=True):
    identifier(db)
    for t in tables:identifier(t)
    policy=query("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if policy!=[dict(disks=['live_market_ssd'])]:raise ValueError('Explicit SSD-only policy required')
    names=','.join(map(L,tables))
    rows=query(f"SELECT name,storage_policy FROM system.tables WHERE database={L(db)} AND name IN ({names})")
    if required and {x['name'] for x in rows}!=set(tables):raise ValueError('Quote tables missing')
    if any(x['storage_policy']!='live_market_ssd' for x in rows):raise ValueError('Quote table is not SSD-only')
    if query(f"SELECT DISTINCT disk_name FROM system.parts WHERE active AND database={L(db)} AND table IN ({names}) AND disk_name!='live_market_ssd'"):
        raise ValueError('Quote parts are outside required SSD')


def source_sql(day,ticker):
    day=date.fromisoformat(str(day));tomorrow=day+timedelta(days=1)
    years=sorted({day.year,tomorrow.year})
    return ' UNION ALL '.join(
        f"SELECT * FROM market_sip_compact.events_{year} PREWHERE ticker={L(ticker)} AND event_date BETWEEN toDate({L(day)}) AND toDate({L(tomorrow)}) WHERE sip_timestamp_us>={bounds(day)} AND sip_timestamp_us<{bounds(tomorrow)}"
        for year in years)


def stage_sql(db,stage,day,ticker,*,source=None):
    """Full local-day hash matches existing trade producer source_evidence.

    Pair validity deliberately matches market_day_sql.broker_sql: positive,
    noncrossed prices and finite nonnegative sizes. No invented forward fill.
    """
    identifier(db);identifier(stage)
    raw=source or source_sql(day,ticker)
    return f"""CREATE TABLE {db}.{stage} ENGINE=MergeTree ORDER BY bucket_index
      SETTINGS storage_policy='live_market_ssd' AS
      WITH decoded AS (SELECT *,cityHash64(tuple(*)) raw_hash,
        toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) ask,
        toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) bid,
        sip_timestamp_us>={bounds(day,'04:00:00')} AND sip_timestamp_us<{bounds(day,'20:00:00')} in_session,
        bitAnd(event_meta,1)=0 AND in_session AND bid>0 AND ask>=bid
          AND isFinite(size_primary) AND isFinite(size_secondary)
          AND size_primary>=0 AND size_secondary>=0 valid
        FROM ({raw}))
      SELECT toUInt32(intDiv(sip_timestamp_us-{bounds(day)},1000000)) bucket_index,
        count() raw_n,uniqExactState(ordinal) ordinals,sum(raw_hash) raw_hash,
        min(ordinal) first_ordinal,max(ordinal) last_ordinal,
        min(sip_timestamp_us) first_us,max(sip_timestamp_us) last_us,
        countIf(in_session) session_events,
        countIf(in_session AND bitAnd(event_meta,1)=1 AND bitAnd(event_meta,128)!=0) reporting_delayed_trades,
        countIf(valid) quote_count,
        minIf(sip_timestamp_us,valid) first_quote_us,maxIf(sip_timestamp_us,valid) last_quote_us,
        argMinIf(bid,tuple(sip_timestamp_us,ordinal),valid) bid_open,
        maxIf(bid,valid) bid_high,minIf(bid,valid) bid_low,
        argMaxIf(bid,tuple(sip_timestamp_us,ordinal),valid) bid_close,
        argMinIf(ask,tuple(sip_timestamp_us,ordinal),valid) ask_open,
        maxIf(ask,valid) ask_high,minIf(ask,valid) ask_low,
        argMaxIf(ask,tuple(sip_timestamp_us,ordinal),valid) ask_close,
        argMaxIf(toFloat64(size_secondary),tuple(sip_timestamp_us,ordinal),valid) bid_size,
        argMaxIf(toFloat64(size_primary),tuple(sip_timestamp_us,ordinal),valid) ask_size
      FROM decoded GROUP BY bucket_index"""


def stage_evidence(db,stage):
    return f"""SELECT sum(raw_n) n,uniqExactMerge(ordinals) unique_ordinals,
      min(first_ordinal) first_ordinal,max(last_ordinal) last_ordinal,
      min(first_us) first_us,max(last_us) last_us,sum(session_events) session_events,
      sum(reporting_delayed_trades) reporting_delayed_trades,sum(raw_hash) hash
      FROM {identifier(db)}.{identifier(stage)}"""


def base_insert(db,stage,build,day,ticker,attempt):
    return f"""INSERT INTO {db}.quote_bars_v1 SELECT {L(build)},toDate({L(day)}),{L(ticker)},toUUID({L(attempt)}),toUInt32(1000),bucket_index,
      side,if(side='bid',bid_open,ask_open),if(side='bid',bid_high,ask_high),
      if(side='bid',bid_low,ask_low),if(side='bid',bid_close,ask_close),
      if(side='bid',bid_size,ask_size),quote_count,first_quote_us,last_quote_us
      FROM {db}.{stage} ARRAY JOIN ['bid','ask'] AS side WHERE quote_count>0"""


def scope(build,day,ticker,attempt=None):
    s=f"build_id={L(build)} AND session_date=toDate({L(day)}) AND ticker={L(ticker)}"
    return s+(f' AND attempt_id=toUUID({L(attempt)})' if attempt else '')


def rollup_select(db,build,day,ticker,attempt,parent,target):
    if (parent,target) not in tuple(zip(FRAMES,FRAMES[1:])):raise ValueError('Invalid quote funnel edge')
    return f"""SELECT build_id,session_date,ticker,attempt_id,toUInt32({target}) resolution_ms,
      toUInt32(intDiv(bucket_index,{target//parent})) bucket_index,side,
      argMin(open_int,b.bucket_index) open_int,max(high_int) high_int,min(low_int) low_int,
      argMax(close_int,b.bucket_index) close_int,argMax(close_size,b.bucket_index) close_size,
      sum(event_count) event_count,min(first_event_us) first_event_us,max(last_event_us) last_event_us
      FROM {db}.quote_bars_v1 b WHERE {scope(build,day,ticker,attempt)} AND b.resolution_ms={parent}
      GROUP BY build_id,session_date,ticker,attempt_id,side,intDiv(b.bucket_index,{target//parent})"""


def evidence(db,build,day,ticker,attempt):
    cols=','.join(FIELDS)
    return f"""SELECT count() n,uniqExact(tuple(resolution_ms,side,bucket_index)) unique_keys,
      countIf(open_int=0 OR low_int=0 OR high_int<greatest(open_int,close_int) OR low_int>least(open_int,close_int) OR event_count=0 OR NOT isFinite(close_size) OR close_size<0 OR first_event_us>last_event_us) bad,
      toString(tuple(sum(cityHash64(tuple({cols}))),sum(sipHash64(tuple({cols}))))) hash
      FROM {db}.quote_bars_v1 WHERE {scope(build,day,ticker,attempt)}"""


def parity(db,build,day,ticker,attempt,parent,target):
    expected=rollup_select(db,build,day,ticker,attempt,parent,target)
    cols=','.join(FIELDS)
    return f"""SELECT count() n FROM ((SELECT {cols} FROM ({expected}) EXCEPT DISTINCT
      SELECT {cols} FROM {db}.quote_bars_v1 WHERE {scope(build,day,ticker,attempt)} AND resolution_ms={target})
      UNION ALL (SELECT {cols} FROM {db}.quote_bars_v1 WHERE {scope(build,day,ticker,attempt)} AND resolution_ms={target}
      EXCEPT DISTINCT SELECT {cols} FROM ({expected})))"""

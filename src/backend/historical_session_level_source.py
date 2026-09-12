"""Bounded canonical SIP aggregation for the retrospective level experiment."""
from datetime import timedelta,timezone
import json
from math import isclose
from .swing_book_source import source_metadata, session_bounds, HISTORICAL_POLICY
from research.mlops.clickhouse import ClickHouseHttpClient,default_clickhouse_url,default_clickhouse_user,default_clickhouse_password


def _query(sql):
    client=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password(),
        timeout_seconds=120,default_query_params=dict(readonly=1,max_threads=2,max_memory_usage=536870912,
            max_result_rows=100000,max_result_bytes=16000000,result_overflow_mode='throw'))
    return [json.loads(line) for line in client.execute(sql+' FORMAT JSONEachRow').splitlines() if line]


def load(ticker,session,query=_query,progress=None):
    revision,rules=source_metadata(ticker,session,query,policy=HISTORICAL_POLICY)
    start,end=session_bounds(session)
    tokens=lambda pred:'['+','.join(str(r['token_id']) for r in rules if pred(r))+']'
    known=tokens(lambda r:0<=r['modifier_int']<=65535)
    both=tokens(lambda r:r['update_last']==r['update_high_low']==1 or r['modifier_int'] in (0,12))
    form=tokens(lambda r:r['modifier_int']==12)
    last=tokens(lambda r:r['update_last']==1)
    extrema=tokens(lambda r:r['update_high_low']==1)
    volume=tokens(lambda r:r['update_volume']==1)
    def source(left,right):
        years='|'.join(str(y) for y in range(left.astimezone(timezone.utc).year,
            (right-timedelta(microseconds=1)).astimezone(timezone.utc).year+1))
        return f"""WITH
          arrayFilter(x->has({known},x),[condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5]) AS tokens,
          fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time,
          toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS sec,
          ((sec<34200 OR sec>=57600) AND hasAny(tokens,{form}) AND arrayAll(x->has({both},x),tokens)) AS form_t
          SELECT sip_timestamp_us,ordinal,toFloat64(price_primary_int)/if(bitAnd(event_meta,2)=2,10000.,100.) AS price,
          toFloat64(size_primary) AS size,
          arrayAll(x->has({last},x) OR (form_t AND has({form},x)),tokens) AS last_ok,
          arrayAll(x->has({extrema},x) OR (form_t AND has({form},x)),tokens) AS extrema_ok,
          arrayAll(x->has({volume},x),tokens) AS volume_ok
          FROM merge('market_sip_compact','^events_({years})$')
          WHERE ticker='{ticker}' AND sip_timestamp_us>={int(left.timestamp()*1e6)} AND sip_timestamp_us<{int(right.timestamp()*1e6)}
          AND bitAnd(event_meta,1)=1 AND price_primary_int>0 AND size_primary>0"""
    bars=[];profile={};audit=[]
    for i in range(8):
        left=start+timedelta(hours=2*i);right=left+timedelta(hours=2)
        sql=source(left,right)
        rows=query(f"""SELECT intDiv(sip_timestamp_us,1000000)+1 AS t,
          argMinIf(price,tuple(sip_timestamp_us,ordinal),last_ok) AS open,
          maxIf(price,extrema_ok) AS high,minIf(price,extrema_ok) AS low,
          argMaxIf(price,tuple(sip_timestamp_us,ordinal),last_ok) AS close,
          sumIf(size,volume_ok) AS volume,count() AS trades,
          countIf(last_ok) AS last_count,countIf(extrema_ok) AS extrema_count
          FROM ({sql}) GROUP BY t ORDER BY t""")
        skipped=0;skipped_volume=0.
        for r in rows:
            if not int(r['last_count']) or not int(r['extrema_count']):
                skipped+=1;skipped_volume+=float(r['volume']);continue
            bars.append({k:float(r[k]) for k in ('t','open','high','low','close','volume')})
        audit.append(dict(start=left.isoformat(),seconds=len(rows),trades=sum(int(r['trades']) for r in rows),
            price_unavailable_seconds=skipped,price_unavailable_volume=skipped_volume))
        for r in query(f'SELECT price,sumIf(size,volume_ok) AS volume FROM ({sql}) GROUP BY price HAVING volume>0 ORDER BY price'):
            price=float(r['price']);profile[price]=profile.get(price,0.)+float(r['volume'])
        if progress:progress(i+1,8)
    if source_metadata(ticker,session,query,policy=HISTORICAL_POLICY)[0]['token']!=revision['token']:
        raise ValueError('Canonical session changed during extraction')
    if not isclose(sum(profile.values()),sum(b['volume'] for b in bars)+sum(r['price_unavailable_volume'] for r in audit),rel_tol=1e-10,abs_tol=1e-4):
        raise ValueError('Volume profile and second aggregation disagree')
    if any(a['t']>=b['t'] for a,b in zip(bars,bars[1:])):
        raise ValueError('Canonical bars are not strictly ordered')
    tables=[f'market_sip_compact.events_{y}' for y in range(start.astimezone(timezone.utc).year,
        (end-timedelta(microseconds=1)).astimezone(timezone.utc).year+1)]
    return bars,[dict(price=k,volume=v) for k,v in sorted(profile.items())],dict(revision=revision,
        authority=', '.join(tables),clock='SIP timestamp',size_codec='canonical Float32 size_primary',
        start=start.isoformat(),end=end.isoformat(),audit=audit)

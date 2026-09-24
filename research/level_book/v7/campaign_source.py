"""Indexed ClickHouse OHLCV for V7's historical SIP-time contract."""
import json
from datetime import timedelta,timezone
from src.backend.swing_book_source import session_bounds,HISTORICAL_POLICY
from src.backend.swing_book_indexed_source import ordinal_bounds
from src.market_engine.historical_level_checkpoint import digest
from pipelines.market_sip.events.trade_reporting_flags import DELAYED, REVISION as REPORTING_REVISION

META_COLUMNS='source_date,build_step,event_count,next_ordinal,last_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,updated_at'
SIGNATURE=f"lower(hex(SHA256(toJSONString(arraySort(groupArray(tuple({META_COLUMNS})))))))"


def literal(value):
    return "'"+str(value).replace('\\','\\\\').replace("'","\\'")+"'"


def coverage_sql(start,end,names=None):
    where=f"source_date BETWEEN {literal(start)} AND {literal(end)}"
    if names:where+=' AND ticker IN ('+','.join(map(literal,names))+')'
    return f"SELECT ticker,count() days,sum(event_count) events,min(source_date) first,max(source_date) last,{SIGNATURE} signature FROM market_sip_compact.events_ordinal_continuity FINAL WHERE {where} GROUP BY ticker ORDER BY ticker"


def reporting_coverage_sql(start, end):
    return ("SELECT toString(source_date) source_date, status,source_digest,updated_at FROM "
            "q_live.historical_trade_reporting_coverage_v1 FINAL "
            f"WHERE source_date BETWEEN {literal(start)} AND {literal(end)} "
            f"AND revision={literal(REPORTING_REVISION)} ORDER BY source_date")


def require_reporting_coverage(source_days, reporting_rows):
    expected = set(source_days)
    actual = {}
    for row in reporting_rows:
        day = str(row['source_date'])
        if day in actual:
            raise ValueError(f'Duplicate trade-reporting coverage for {day}')
        actual[day] = row['status']
    missing = sorted(day for day in expected if actual.get(day) != 'complete')
    if missing:
        raise ValueError(f'V7 source requires completed {REPORTING_REVISION} coverage for all canonical days; '
                         f'{len(missing)} missing/incomplete, first: {", ".join(missing[:5])}')


def bars_sql(ticker,day,rules,metadata):
    first,stop=ordinal_bounds(metadata);left,right=session_bounds(day)
    years='|'.join(str(y) for y in range(left.astimezone(timezone.utc).year,(right-timedelta(microseconds=1)).astimezone(timezone.utc).year+1))
    ids=lambda predicate:'['+','.join(str(r['token_id']) for r in rules if predicate(r))+']'
    known=ids(lambda r:0<=r['modifier_int']<=65535)
    both=ids(lambda r:r['update_last']==r['update_high_low']==1 or r['modifier_int'] in (0,12))
    form=ids(lambda r:r['modifier_int']==12)
    last=ids(lambda r:r['update_last']==1);extrema=ids(lambda r:r['update_high_low']==1);volume=ids(lambda r:r['update_volume']==1)
    return f"""WITH arrayFilter(x->has({known},x),[condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5]) AS tokens,
      fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time,
      toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS sec,
      ((sec<34200 OR sec>=57600) AND hasAny(tokens,{form}) AND arrayAll(x->has({both},x),tokens)) AS form_t,
      arrayAll(x->has({last},x) OR (form_t AND has({form},x)),tokens) AS last_ok,
      arrayAll(x->has({extrema},x) OR (form_t AND has({form},x)),tokens) AS extrema_ok,
      arrayAll(x->has({volume},x),tokens) AS volume_ok,
      toFloat64(price_primary_int)/if(bitAnd(event_meta,2)=2,10000.,100.) AS price
      SELECT intDiv(sip_timestamp_us,1000000)+1 AS t,
      argMinIf(price,tuple(sip_timestamp_us,ordinal),last_ok) AS open,
      maxIf(price,extrema_ok) AS high,minIf(price,extrema_ok) AS low,
      argMaxIf(price,tuple(sip_timestamp_us,ordinal),last_ok) AS close,
      sumIf(toFloat64(size_primary),volume_ok) AS volume,
      count() AS trades,countIf(last_ok) AS last_count,countIf(extrema_ok) AS extrema_count
      FROM merge('market_sip_compact','^events_({years})$')
      WHERE ticker={literal(ticker)} AND ordinal>={first} AND ordinal<{stop}
      AND sip_timestamp_us>={int(left.timestamp()*1e6)} AND sip_timestamp_us<{int(right.timestamp()*1e6)}
      AND bitAnd(event_meta,1)=1 AND bitAnd(event_meta,{DELAYED})=0
      AND price_primary_int>0 AND size_primary>0
      AND sec>=14700
      GROUP BY t ORDER BY t"""


def decode(rows):
    accepted=[r for r in rows if r['last_count'] and r['extrema_count']]
    bars=[{k:float(r[k]) for k in ('t','open','high','low','close','volume')} for r in accepted]
    if len(bars)>57600 or any(a['t']>=b['t'] for a,b in zip(bars,bars[1:])):raise ValueError('Invalid canonical second ordering/count')
    return bars,dict(trades=sum(r['trades'] for r in rows),price_unavailable_seconds=len(rows)-len(bars),
                    price_unavailable_volume=sum(r['volume'] for r in rows if not r['last_count'] or not r['extrema_count']))


def source_hash(metadata,rules):
    return digest(dict(policy=HISTORICAL_POLICY,reporting_revision=REPORTING_REVISION,
                       delayed_flag=DELAYED,metadata=metadata,rules=rules))

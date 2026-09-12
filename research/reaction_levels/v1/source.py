"""Read-once canonical partitions, with source-revision and content verification."""
from datetime import timedelta
import json
import os
from pathlib import Path
from src.backend.historical_session_level_source import load,_query
from src.backend.swing_book_source import source_metadata,session_bounds,HISTORICAL_POLICY
from src.market_engine.historical_level_checkpoint import digest


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(value,f,allow_nan=False,separators=(',',':'));f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def session_inputs(root,ticker,day):
    path=root/'inputs'/f'{day}.json'
    revision=source_metadata(ticker,day,policy=HISTORICAL_POLICY)[0]
    if path.exists():
        value=json.loads(path.read_text())
        if value['source']['revision']!=revision or value['content_hash']!=digest({k:v for k,v in value.items() if k!='content_hash'}):
            raise ValueError(f'Cached canonical input changed: {day}; use a new run')
        return value,True
    bars,profile,source=load(ticker,day,window_hours=16)
    start,end=session_bounds(day);quotes=[]
    for i in range(1):
        left=start;right=end
        years='|'.join(str(y) for y in range(left.year,right.year+1))
        rows=_query(f"""SELECT intDiv(sip_timestamp_us,1000000)+1 AS t,
            argMax(tuple(toFloat64(price_primary_int)/if(bitAnd(event_meta,2)>0,10000.,100.),
                         toFloat64(price_secondary_int)/if(bitAnd(event_meta,4)>0,10000.,100.),
                         toFloat64(size_primary),toFloat64(size_secondary),sip_timestamp_us),
                         tuple(sip_timestamp_us,ordinal)) AS q
            FROM merge('market_sip_compact','^events_({years})$')
            WHERE ticker='{ticker}' AND sip_timestamp_us>={int(left.timestamp()*1e6)}
              AND sip_timestamp_us<{int(right.timestamp()*1e6)} AND bitAnd(event_meta,1)=0
            GROUP BY t ORDER BY t""")
        quotes.extend(dict(t=r['t'],ask=r['q'][0],bid=r['q'][1],ask_size=r['q'][2],bid_size=r['q'][3],sip_us=r['q'][4]) for r in rows)
    if source_metadata(ticker,day,policy=HISTORICAL_POLICY)[0]!=revision:
        raise ValueError(f'Canonical inputs changed during quote read: {day}')
    value=dict(bars=bars,profile=profile,source=source,quotes=quotes)
    value['content_hash']=digest(value);write_json(path,value)
    return value,False

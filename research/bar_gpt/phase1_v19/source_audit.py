"""Trace retained failure windows to sealed shards and canonical quote events."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,hashlib,json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import torch
from ..v5.shards import source
from ..v5.shards.contract import file_hash,digest,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--audit',type=Path,required=True);a=p.parse_args()
    manifest=json.loads((a.audit/'manifest.json').read_text());batch=manifest['batch'];workers=manifest['workers']
    root=a.runtime_root;idxroot=root/'phase1-v6-cohort-balanced-v4'/('loader-index-v1-'+manifest['split_hash'][:20])
    seal=json.loads((idxroot/'index.json').read_text());entry=seal['splits']['validation']
    assert file_hash(idxroot/entry['file'])==entry['sha256']
    index=np.load(idxroot/entry['file'],mmap_mode='r');counts=index['stop']-index['start']
    cases=torch.load(a.audit/'cases.pt',map_location='cpu',weights_only=False);opened={};results=[]
    c=source.client(memory_gib=.5);c.default_query_params.update(max_threads=1,max_execution_time=20,max_result_bytes=1048576)
    try:
        for case in cases:
            global_batch,within=divmod(case['stream_window'],batch);local,lane=divmod(global_batch,workers)
            if min(counts[i::workers].sum()//batch for i in range(workers))<=local:raise ValueError('Late uneven lanes require explicit replay')
            unshuffled=local*batch+np.random.default_rng(17+local*workers+lane).permutation(batch)[within]
            entries=index[lane::workers];cumulative=np.cumsum(entries['stop']-entries['start']);at=int(np.searchsorted(cumulative,unshuffled,side='right'));e=entries[at]
            offset=int(e['start']+unshuffled-(cumulative[at-1] if at else 0));native=e['path'].decode()
            path=Path(native.replace('D:\\TradingML',str(root.parent.parent)))
            if native not in opened:
                assert file_hash(path)==e['sha256'].decode()
                receipt=json.loads((path.parent/'receipt.json').read_text())
                assert digest({k:v for k,v in receipt.items() if k!='receipt_hash'})==receipt['receipt_hash']
                opened[native]=(np.load(path,mmap_mode='r'),receipt)
            rows,receipt=opened[native];row=rows[offset]
            sha=hashlib.sha256(row['values'].tobytes()+row['end_us'].tobytes()+row['valid'].tobytes()).hexdigest();assert sha==case['window_sha']
            bar=case['bar'];end=int(row['end_us'][bar]);dt=datetime.fromtimestamp(end/1e6,ZoneInfo('America/New_York'));start=int(dt.replace(hour=4,minute=0,second=0,microsecond=0).timestamp()*1e6)
            _,first,stop=receipt['unit'].split('-');ticker=receipt['ticker'];year=receipt['year']
            query=f"SELECT ordinal,sip_timestamp_us,event_meta,price_primary_int,price_secondary_int,size_primary,size_secondary FROM market_sip_compact.events_{year} PREWHERE ticker={source.literal(ticker)} AND ordinal>={int(first)} AND ordinal<{int(stop)} WHERE sip_timestamp_us>={start} AND sip_timestamp_us<{end} AND bitAnd(event_meta,1)=0 ORDER BY sip_timestamp_us DESC,ordinal DESC LIMIT 1"
            events=source.rows(c,query)
            if not events:raise ValueError('No bounded canonical quote evidence')
            event=events[0];meta=event['event_meta'];bid=event['price_secondary_int']*(1 if meta&4 else 100)/10000.;ask=event['price_primary_int']*(1 if meta&2 else 100)/10000.
            expected=np.asarray([bid,event['size_secondary'],ask,event['size_primary']],dtype=np.float32);actual=row['values'][bar,[9,10,15,16]]
            results.append(dict(window_sha=sha,path=native,row=offset,bar=bar,ticker=ticker,end_us=end,canonical=event,fields=['bid_close','bid_size','ask_close','ask_size'],expected=expected.tolist(),actual=actual.tolist(),equal=bool(np.array_equal(expected,actual)),source_valid=row['valid'][bar,[9,10,15,16]].tolist()))
        write_json(a.audit/'canonical-quote-audit.json',dict(scope='Retained32 failure bars: quote closes and displayed sizes only; not a complete dataset audit',count=len(results),mismatches=sum(not r['equal'] for r in results),results=results))
        print(dict(count=len(results),mismatches=sum(not r['equal'] for r in results),tickers=sorted({r['ticker'] for r in results})),flush=True)
    finally:source.close(c)


if __name__=='__main__':main()

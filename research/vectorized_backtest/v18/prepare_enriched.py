"""Restart-safe CPU session workers over certified bars; never use raw SIP files."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
os.environ.setdefault('OMP_NUM_THREADS','1')
import argparse,json,time,shutil,traceback
from pathlib import Path
from datetime import date,datetime
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from contextlib import closing
import numpy as np
import polars as pl
from .enriched_math import own,returns,market_summary,NAMES,OWN_NAMES,MARKET_HORIZONS,VERSION
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v6.torch_backtest.compact_prepare import KEY_STRIDE
from research.vectorized_backtest.v4.torch_backtest.source import arte_source as authority,arte_sql as sql
from research.rl_trading.v6.source import require_reporting_coverage
from .indicator_identity import population_map
from research.rl_trading.v1.common import digest,NY

read=lambda p:json.loads(Path(p).read_bytes())


def definition():
    return dict(version=VERSION,names=list(NAMES),math_sha256=file_hash(Path(__file__).with_name('enriched_math.py')),
        extractor_sha256=file_hash(Path(__file__)),
        universe='unique tickers in the pinned preopen tradable snapshot, not ranked feature scope',
        clock='completed source second <= feature clock; no overnight return; max endpoint age 1 second',
        context='contemporaneously observed valid ticker returns; explicit coverage; minimum 2 peers',
        rank='average-tie full-universe trailing-60-second dollar-volume percentile',validation_feedback=False)


def verify(folder,input_hash,role):
    c=read(folder/'complete.json')
    if c['status']!='complete' or c['definition']!=definition() or c['input_sha256']!=input_hash or c['validation_opened']!=(role=='validation'):
        raise ValueError('Enriched certificate identity changed')
    for name,h in c['files'].items():
        if file_hash(folder/name)!=h:raise ValueError('Enriched bytes changed')
    return c


def prepare_day(dataset,day,output):
    root=Path(dataset['inputs'])/day;folder=require_runtime(Path(output)/day);expected=dataset['receipts'][day]['inputs']
    if file_hash(root/'complete.json')!=expected:raise ValueError('Session input changed')
    if (folder/'complete.json').exists():verify(folder,expected,dataset['role']);return dict(day=day,status='skipped')
    started=time.perf_counter();receipt=read(root/'complete.json');session=receipt['identity']['session']
    if bool(receipt['validation_opened'])!=(dataset['role']=='validation'):raise ValueError('Split role changed')
    keys=np.load(root/'feature_keys.npy',mmap_mode='r');ticks=keys%KEY_STRIDE;members=receipt['listings']
    ids=(keys//KEY_STRIDE).astype(np.int64)
    if len(ids) and (ids.min()<0 or ids.max()>=len(members)):raise ValueError('Feature identity outside compact census')
    sources=pl.Series(ticks*1000000).cast(pl.Datetime('us','UTC')).dt.convert_time_zone('America/New_York').dt.date().cast(pl.String).to_numpy()
    values=np.lib.format.open_memmap(folder/'values.npy',mode='w+',dtype=np.float32,shape=(len(keys),len(NAMES)));values[:]=np.nan
    source=authority.load_build(session['source_manifest'],session['source_ledger'],[date.fromisoformat(d) for d in sorted(set(sources))])
    bank=Path(session['feature_root']);plan=read(bank/'plan.json')
    if file_hash(bank/'complete.json')!=receipt['bank_certificate_sha256'] or plan['source_build_id']!=source['build_id'] or plan['source_definition_hash']!=source['definition_hash']:
        raise ValueError('Compact features and canonical bar authority differ')
    proofs=[];prepared=0
    with closing(authority.reader(threads=1)) as client:
        storage=authority.storage_check(client)
        for source_day in sorted(set(sources)):
            if (Path(output)/'STOP').exists():raise InterruptedError('Cooperative preparation stop requested')
            require_reporting_coverage(source,date.fromisoformat(source_day))
            saved=next(p for p in source['definition']['plan']['population'] if p['session_date']==source_day)
            where=f'session_date=toDate({sql.literal(source_day)}) AND snapshot_id={sql.literal(saved["certificate"]["snapshot_id"])} AND is_tradable=1'
            rows=sql.query(client,f'SELECT listing_id,ticker FROM q_live.feature_tradable_universe_snapshot_v2 WHERE {where}')
            expected_map={r['listing_id']:r['ticker'] for r in rows}
            if len(expected_map)!=len(rows):raise ValueError('Duplicate certified listing identity')
            population,proof=population_map(client,source,date.fromisoformat(source_day),expected_map)
            tickers=sorted(set(population.values()));ticker_index={t:i for i,t in enumerate(tickers)}
            origin=int(datetime.combine(date.fromisoformat(source_day),datetime.min.time().replace(hour=4),NY).timestamp())
            clocks=origin+np.arange(1,57601,dtype=np.int64)
            target_rows=np.flatnonzero(sources==source_day);requests={}
            for identity in np.unique(ids[target_rows]):
                member=members[int(identity)];listing=member['listing_id'];ticker=member['ticker']
                if population.get(listing)!=ticker:raise ValueError('Target listing absent from pinned source universe')
                rr=target_rows[ids[target_rows]==identity];requests.setdefault(ticker,[]).append(rr)
            # Large dense cross-section stays disk-backed; only a clock tile is resident.
            scratch=folder/'scratch';scratch.mkdir(exist_ok=True)
            need=len(tickers)*len(clocks)*4*4
            if shutil.disk_usage(folder).free<need+2*1024**3:raise MemoryError('Insufficient runtime disk for bounded market scratch')
            dense=np.lib.format.open_memmap(scratch/'cross-section.npy',mode='w+',dtype=np.float32,shape=(4,len(tickers),len(clocks)));dense[:]=np.nan
            units=[]
            for start in range(0,len(tickers),32):
                if (Path(output)/'STOP').exists():raise InterruptedError('Cooperative preparation stop requested')
                batch=tickers[start:start+32]
                pairs=','.join(f'({sql.literal(t)},toUUID({sql.literal(source["units"][source_day][t]["bars"]["attempt_id"])}))' for t in batch)
                condition=f'build_id={sql.literal(source["build_id"])} AND session_date=toDate({sql.literal(source_day)}) AND (ticker,attempt_id) IN ({pairs})'
                audits=sql.query(client,f'SELECT ticker,count() AS n,uniqExact((resolution_ms,bucket_index)) AS unique_keys,sum(cityHash64(tuple(*))) AS hash FROM arte.bars_v1 WHERE {condition} GROUP BY ticker')
                actual={r['ticker']:r for r in audits}
                for ticker in batch:
                    cert=source['units'][source_day][ticker]['bars'];a=actual.get(ticker,dict(n=0,unique_keys=0,hash='0'))
                    if int(a['n'])!=cert['output_rows'] or int(a['n'])!=int(a['unique_keys']) or str(a['hash'])!=cert['output_hash']:raise ValueError('Certified bar unit changed: '+ticker)
                end=f'intDiv(toInt64({sql.bounds(source_day)}),1000000)+toInt64(bucket_index)+1'
                frame=authority.frame(client,f'SELECT ticker,toInt64({end}) AS second,open_int/10000. AS open,high_int/10000. AS high,low_int/10000. AS low,close_int/10000. AS close,volume,notional,trade_count,price_valid,extremes_valid FROM arte.bars_v1 WHERE {condition} AND resolution_ms=1000 ORDER BY ticker,bucket_index',
                    dict(ticker=pl.String,second=pl.Int64,open=pl.Float64,high=pl.Float64,low=pl.Float64,close=pl.Float64,volume=pl.Float64,notional=pl.Float64,trade_count=pl.Int64,price_valid=pl.Int64,extremes_valid=pl.Int64))
                grouped=frame.partition_by('ticker',as_dict=True,maintain_order=True)
                for ticker in batch:
                    f=grouped.get((ticker,),frame.head(0));units.append(dict(ticker=ticker,certificate=source['units'][source_day][ticker]['bars']))
                    if not len(f):continue
                    t=f['second'].to_numpy();positions=t-origin-1
                    if np.any(np.diff(t)<=0) or positions.min()<0 or positions.max()>=len(clocks):raise ValueError('Source clock ordering/session mismatch')
                    c=f['close'].to_numpy();ok=f['price_valid'].to_numpy()!=0;ti=ticker_index[ticker]
                    for hi,h in enumerate(MARKET_HORIZONS):dense[hi,ti,positions]=returns(t,c,ok,t,h)
                    s=np.r_[0.,np.cumsum(f['notional'].to_numpy(),dtype=np.float64)];beg=np.searchsorted(t,t-60,side='right')
                    dense[3,ti,positions]=s[np.arange(1,len(t)+1)]-s[beg]
                    for rr in requests.get(ticker,[]):
                        exact=np.searchsorted(t,ticks[rr])
                        if np.any(exact>=len(t)) or not np.array_equal(t[exact],ticks[rr]):raise ValueError('Requested completed feature clock absent')
                        values[rr,:len(OWN_NAMES)]=own(f,ticks[rr],origin);prepared+=len(rr)
                write_json(folder/'progress.json',dict(status='running',day=day,source_day=source_day,stage='certified bars',completed_tickers=min(start+32,len(tickers)),total_tickers=len(tickers),prepared_rows=prepared))
            dense.flush()
            rank=np.lib.format.open_memmap(scratch/'rank.npy',mode='w+',dtype=np.float32,shape=(len(tickers),len(clocks)));rank[:]=np.nan
            # Native sorting and reductions over all peers in bounded 1024-second tiles.
            target_tickers=np.asarray([ticker_index[members[int(i)]['ticker']] for i in ids[target_rows]])
            target_clock=ticks[target_rows]-origin-1
            for begin in range(0,len(clocks),1024):
                end=min(len(clocks),begin+1024);pick=(target_clock>=begin)&(target_clock<end);rr=target_rows[pick];ti=target_tickers[pick];ci=target_clock[pick]-begin
                for hi,h in enumerate(MARKET_HORIZONS):
                    tile=np.array(dense[hi,:,begin:end]);common,percentile=market_summary(tile,len(tickers))
                    values[rr,len(OWN_NAMES)+hi*7:len(OWN_NAMES)+hi*7+5]=common[ci]
                    values[rr,len(OWN_NAMES)+hi*7+5]=tile[ti,ci]-common[ci,0]
                    values[rr,len(OWN_NAMES)+hi*7+6]=percentile[ti,ci]
                _,pct=market_summary(np.array(dense[3,:,begin:end]),len(tickers));rank[:,begin:end]=pct
            values[target_rows,-2]=rank[target_tickers,target_clock]
            prior=target_clock-15;values[target_rows,-1]=np.where(prior>=0,rank[target_tickers,target_clock]-rank[target_tickers,prior.clip(0)],np.nan)
            del dense,rank
            # Only unsealed, disposable scratch below this exact session is removed.
            if scratch.resolve().parent!=folder.resolve():raise ValueError('Scratch escaped runtime session')
            shutil.rmtree(scratch)
            proofs.append(dict(day=source_day,scope=proof,units=units))
    if prepared!=len(keys):raise ValueError('Incomplete requested feature rows')
    values.flush();shape=list(values.shape);del values
    write_json(folder/'complete.json',dict(status='complete',definition=definition(),input_sha256=expected,
        files={'values.npy':file_hash(folder/'values.npy')},shape=shape,prepared_rows=prepared,
        validation_opened=dataset['role']=='validation',source_proofs=proofs,storage=storage,elapsed_seconds=time.perf_counter()-started))
    return dict(day=day,status='completed',elapsed_seconds=time.perf_counter()-started,rows=prepared)


def _prepare(training,validation,output,workers=4):
    if not 2<=workers<=4:raise ValueError('Use 2 to 4 bounded CPU session workers')
    out=require_runtime(Path(output));datasets={role:read(path) for path,role in ((training,'training'),(validation,'validation'))}
    import psutil
    if psutil.virtual_memory().available<(workers*16+16)*1024**3:raise MemoryError('Insufficient RAM headroom for bounded CPU workers')
    if shutil.disk_usage(out).free<workers*16*1024**3:raise MemoryError('Insufficient disk headroom for parallel disk-backed scratch')
    for role,d in datasets.items():
        if d['version']!='v8-split-manifest-v1' or d['role']!=role or d['days']!=sorted(set(d['days'])):raise ValueError('Explicit sorted split manifests required')
    if max(datasets['training']['days'])>=min(datasets['validation']['days']):raise ValueError('Chronological disjoint splits required')
    tasks=[(d,day,str(out)) for d in datasets.values() for day in d['days']];done=[];failed=[];started=time.time()
    write_json(out/'job.json',dict(definition=definition(),workers=workers,training_sha256=file_hash(training),validation_sha256=file_hash(validation),pid=os.getpid(),started_epoch=started))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={};queue=iter(tasks)
        def refill():
            if failed or (out/'STOP').exists():return
            while len(pending)<workers:
                item=next(queue,None)
                if item is None:break
                pending[pool.submit(prepare_day,*item)]=item[1]
        refill()
        while pending:
            completed,_=wait(pending,timeout=10,return_when=FIRST_COMPLETED)
            for future in completed:
                day=pending.pop(future)
                try:done.append(future.result())
                except Exception as e:
                    failed.append(dict(day=day,error=str(e),traceback=traceback.format_exc()));write_json(out/'failures.json',failed)
            refill();write_json(out/'status.json',dict(status='failed' if failed else 'running',active=list(pending.values()),completed=done,failed=failed,queued=len(tasks)-len(done)-len(failed)-len(pending),total=len(tasks),elapsed_seconds=time.time()-started))
    if failed or len(done)!=len(tasks):raise RuntimeError('Preparation incomplete; sealed days preserved, rerun resumes them')
    for role,d in datasets.items():write_json(out/(role+'.json'),dict(d,enriched_root=str(out),enriched_receipts={day:file_hash(out/day/'complete.json') for day in d['days']}))
    write_json(out/'complete.json',dict(status='complete',definition=definition(),days=[x[1] for x in tasks],receipts={x[1]:file_hash(out/x[1]/'complete.json') for x in tasks}))
    write_json(out/'status.json',dict(status='complete',completed=done,failed=[],active=[],queued=0,total=len(tasks)))


def prepare(training,validation,output,workers=4):
    from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
    root=require_runtime(Path(output))
    with owned_run(root,version=VERSION):return _prepare(training,validation,root,workers)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('training','validation','output'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--read-credential-file',type=Path,help='Read-only credential reference; never copied into artifacts')
    args=vars(p.parse_args());credential=args.pop('read_credential_file')
    names=('BACKTEST_V3_READ_CREDENTIAL_FILE','BACKTEST_V3_READ_CLICKHOUSE_URL','BACKTEST_V3_READ_CLICKHOUSE_USER','BACKTEST_V3_READ_CLICKHOUSE_PASSWORD')
    if credential is not None:
        if any(os.environ.get(n) for n in names[1:]):raise ValueError('Cannot mix file and inline read credentials')
        os.environ[names[0]]=str(credential)
    elif not any(os.environ.get(n) for n in names):
        os.environ[names[0]]='D:/TradingML/secrets/backtest_v3_read.env'
    from src.backend.backtest_v3_clients import _credential
    _credential('read',os.environ)  # Fail before launching workers; never print values.
    prepare(**args)

if __name__=='__main__':main()

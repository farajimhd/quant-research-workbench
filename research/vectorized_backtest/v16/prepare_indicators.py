"""Read certified full-session bars once; persist only top10-funnel feature rows."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from datetime import date,datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from zoneinfo import ZoneInfo
from time import perf_counter
from hashlib import sha256
import numpy as np
import polars as pl
from .features import VERSION,SPECS
from .indicator_math import calculate,initial_state
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v6.torch_backtest.compact_prepare import KEY_STRIDE
from research.vectorized_backtest.v6.torch_backtest.splits import price_factor,VERSION as SPLIT_VERSION
from research.vectorized_backtest.v4.torch_backtest.source import arte_source as authority,arte_sql
from research.vectorized_backtest.v4.torch_backtest.encoding.config import DEFAULT_EXCLUDED_TICKERS
from research.rl_trading.v1.common import digest


def prepare(training,validation,output,workers=4):
    if not 1<=workers<=8:raise ValueError('Bounded preparation workers required')
    output=require_runtime(Path(output));start=perf_counter();datasets={}
    for path,role in ((training,'training'),(validation,'validation')):
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if value['role']!=role or value['version']!='v8-split-manifest-v1':raise ValueError('Explicit split manifest required')
        datasets[role]=value
    days=sorted(datasets['training']['days']+datasets['validation']['days'])
    if len(set(days))!=len(days) or max(datasets['training']['days'])>=min(datasets['validation']['days']):raise ValueError('Disjoint chronological splits required')
    bases={};requests={};arrays={};listing_set=set();sessions={};target_counts={};compact_members={};bank_certificates={};identity_hashes={}
    for role,dataset in datasets.items():
        for day in dataset['days']:
            write_json(output/'status.json',dict(status='running',stage='Index top10 causal feature rows',day=day,total_days=len(days)))
            root=Path(dataset['inputs'])/day;receipt=json.loads((root/'complete.json').read_text(encoding='utf-8-sig'))
            if file_hash(root/'complete.json')!=dataset['receipts'][day]['inputs'] or bool(receipt['validation_opened'])!=(role=='validation'):raise ValueError('Certified input changed')
            bases[day]=file_hash(root/'complete.json');sessions[day]=receipt['identity']['session']
            compact_members[day]=receipt['listings']
            bank_certificates[day]=receipt['bank_certificate_sha256']
            keys=np.load(root/'feature_keys.npy',mmap_mode='r');top=np.unique(np.load(root/'top_indices.npy',mmap_mode='r'));top=top[top>=0]
            folder=require_runtime(output/day)
            if (folder/'complete.json').exists():raise ValueError('Preparation output already sealed; use a distinct source-bound directory')
            arrays[day]=np.lib.format.open_memmap(folder/'values.npy',mode='w+',dtype=np.float32,shape=(len(keys),len(SPECS)));arrays[day][:]=np.nan
            offsets=receipt['feature_offsets'];target_counts[day]=0
            for index in top:
                row=receipt['listings'][int(index)];identity=row['listing_id'];listing_set.add(identity)
                a,b=offsets[int(index):int(index)+2];seconds=np.asarray(keys[a:b])%KEY_STRIDE
                target_counts[day]+=b-a
                source_days=pl.Series(seconds*1000000).cast(pl.Datetime('us','UTC')).dt.convert_time_zone('America/New_York').dt.date().cast(pl.String).to_numpy()
                for source_day in np.unique(source_days):
                    selected=np.flatnonzero(source_days==source_day)
                    requests.setdefault((source_day,identity),[]).append((day,selected+a,seconds[selected]))
    if set(d for d,_ in requests)-set(days):raise ValueError('Indicator predecessor clocks precede certified bootstrap authority')
    first=sessions[days[0]];source=authority.load_build(first['source_manifest'],first['source_ledger'],[date.fromisoformat(d) for d in days])
    c=authority.reader(threads=1);storage=authority.storage_check(c)
    if source['definition']['plan']['requested']!=days:raise ValueError('Continuous bank requires the full consecutive certified source sequence')
    mapping={};split={};split_hashes={}
    last_needed={i:max(d for d,identity in requests if identity==i) for i in listing_set}
    for day in days:
        write_json(output/'status.json',dict(status='running',stage='Verify certified universe and splits',day=day,total_days=len(days)))
        session=sessions[day]
        if session['source_manifest']!=first['source_manifest'] or session['source_ledger']!=first['source_ledger']:raise ValueError('Mixed bar authorities')
        wanted={r['ticker'] for r in compact_members[day] if r['listing_id'] in listing_set and day<=last_needed[r['listing_id']]}
        subset=dict(source,units={**source['units'],day:{t:u for t,u in source['units'][day].items() if t in wanted}})
        try:population,_=authority.population(c,subset,date.fromisoformat(day),diagnostic_directory=output/'population-audit'/day,
            excluded_tickers=DEFAULT_EXCLUDED_TICKERS,regular_us_exchanges_only=True) if wanted else ([],None)
        except ValueError as error:raise ValueError(f'{day}: certified indicator universe: {error}') from error
        mapping[day]={r['listing_id']:r['ticker'] for r in population if r['listing_id'] in listing_set and day<=last_needed[r['listing_id']]}
        canonical=json.loads(Path(session['identity_map']).read_text(encoding='utf-8-sig'))
        bank_root=Path(session['feature_root']);bank=json.loads((bank_root/'complete.json').read_text(encoding='utf-8-sig'));plan=json.loads((bank_root/'plan.json').read_text(encoding='utf-8-sig'))
        if canonical['bank_certificate_sha256']!=file_hash(Path(session['feature_root'])/'complete.json') or canonical['bank_certificate_sha256']!=bank_certificates[day]:
            raise ValueError('Feature identity map changed')
        saved_snapshot=next(p['snapshot_hash'] for p in source['definition']['plan']['population'] if p['session_date']==day)
        if (plan['hash']!=digest({k:v for k,v in plan.items() if k!='hash'}) or bank.get('status')!='complete' or bank['plan_hash']!=plan['hash']
                or plan['source_build_id']!=source['build_id'] or plan['source_definition_hash']!=source['definition_hash']
                or plan['population_snapshot_hash']!=saved_snapshot or canonical.get('population_snapshot_hash',saved_snapshot)!=saved_snapshot):raise ValueError('Feature identity source snapshot changed')
        if any(canonical['listing_to_ticker'].get(i)!=t for i,t in mapping[day].items()):raise ValueError('Continuous state differs from certified feature identity')
        identity_hashes[day]=file_hash(Path(session['identity_map']))
        path=Path(session['split_certificate']);value=json.loads(path.read_text(encoding='utf-8-sig'))
        receipt=json.loads((Path(next(v['inputs'] for v in datasets.values() if day in v['days']))/day/'complete.json').read_text(encoding='utf-8-sig'))
        if (file_hash(path)!=receipt['split_certificate_sha256'] or value.get('hash')!=digest({k:v for k,v in value.items() if k!='hash'})
                or value.get('version')!=SPLIT_VERSION or value.get('status')!='complete' or value['day']!=day
                or value['bank_certificate_sha256']!=receipt['bank_certificate_sha256']):raise ValueError('Certified opening share basis changed')
        cutoff=datetime.fromisoformat(value['opening_asof_utc']).replace(tzinfo=timezone.utc)
        for identity,evidence in value['listings'].items():
            if identity not in listing_set:continue
            for row in evidence['splits']:
                inserted=datetime.fromisoformat(str(row['inserted_at']).replace('Z','+00:00'))
                if inserted.tzinfo is None:inserted=inserted.replace(tzinfo=timezone.utc)
                if inserted>cutoff or str(row['execution_date'])>day:raise ValueError('Noncausal opening split')
        split[day]=value['listings'];split_hashes[day]=file_hash(path)
    states={};previous={};counts_done={d:0 for d in days};proofs=[]
    # Compile once before threads contend for the compiler.
    s,n=initial_state();calculate(np.array([1.]),np.array([1.]),np.array([1.]),np.array([True]),np.array([True]),s,n,1.,np.array([True]))
    def where(day,items):
        pairs=','.join(f'({arte_sql.literal(t)},toUUID({arte_sql.literal(source["units"][day][t]["bars"]["attempt_id"])}))' for _,t in items)
        return f'build_id={arte_sql.literal(source["build_id"])} AND session_date=toDate({arte_sql.literal(day)}) AND (ticker,attempt_id) IN ({pairs})'
    def unit(day,identity,ticker,frame):
        seconds=frame['time_us'].to_numpy()//1000000
        if len(seconds) and np.any(np.diff(seconds)<=0):raise ValueError('Nonunique chronological certified bars')
        targets=requests.get((day,identity),[]);wanted=np.unique(np.concatenate([v[2] for v in targets])) if targets else np.empty(0,dtype=np.int64)
        indices=np.searchsorted(seconds,wanted)
        if len(wanted) and (not len(seconds) or np.any(indices>=len(seconds)) or not np.array_equal(seconds[indices],wanted)):raise ValueError(f'{day} {ticker}: requested feature clock absent from certified bars')
        emit=np.zeros(len(seconds),dtype=bool);emit[indices]=True
        state,count=states.get(identity,initial_state());factor=1.
        if identity in previous:
            evidence=split[day].get(identity)
            if evidence is None or evidence['ticker']!=ticker:raise ValueError('Missing continuous listing share-basis evidence')
            factor=price_factor(evidence['splits'],previous[identity],day)
        values,state,count=calculate(frame['close'].to_numpy(),frame['high'].to_numpy(),frame['low'].to_numpy(),
            frame['price_valid'].to_numpy()!=0,frame['extremes_valid'].to_numpy()!=0,state,count,factor,emit)
        for target,rows,clocks in targets:arrays[target][rows]=values[np.searchsorted(wanted,clocks)]
        return identity,state,count,dict(day=day,listing_id=identity,ticker=ticker,bar_certificate=source['units'][day][ticker]['bars'],
            split_factor=factor,predecessor_day=previous.get(identity),emitted_rows=len(wanted),end_state_sha256=sha256(state.tobytes()+count.tobytes()).hexdigest()),[(d,len(r)) for d,r,_ in targets]
    def read_batch(day,items,client):
        condition=where(day,items)
        audits=arte_sql.query(client,f'SELECT ticker,count() AS n,uniqExact((resolution_ms,bucket_index)) AS unique_keys,sum(cityHash64(tuple(*))) AS hash '
            f'FROM arte.bars_v1 WHERE {condition} GROUP BY ticker')
        actual={r['ticker']:r for r in audits}
        for _,ticker in items:
            saved=source['units'][day][ticker]['bars'];r=actual.get(ticker,dict(n=0,unique_keys=0,hash='0'))
            if int(r['n'])!=saved['output_rows'] or int(r['n'])!=int(r['unique_keys']) or str(r['hash'])!=saved['output_hash']:raise ValueError(f'{day} {ticker}: certified bars changed')
        end=f'toInt64({arte_sql.bounds(day)})+(toInt64(bucket_index)+1)*1000000'
        frame=authority.frame(client,f'SELECT ticker,{end} AS time_us,close_int/10000. AS close,high_int/10000. AS high,low_int/10000. AS low,price_valid,extremes_valid '
            f'FROM arte.bars_v1 WHERE {condition} AND resolution_ms=1000 ORDER BY ticker,bucket_index',
            {'ticker':pl.String,'time_us':pl.Int64,'close':pl.Float64,'high':pl.Float64,'low':pl.Float64,'price_valid':pl.Int64,'extremes_valid':pl.Int64})
        grouped=frame.partition_by('ticker',as_dict=True,maintain_order=True)
        return [unit(day,identity,ticker,grouped.get((ticker,),frame.head(0))) for identity,ticker in items]
    def batch(day,items):
        with closing(authority.reader(threads=1)) as client:return read_batch(day,items,client)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for day in days:
            write_json(output/'status.json',dict(status='running',stage='Continuous certified indicators',day=day,completed_days=len(proofs),total_days=len(days),listing_count=len(listing_set)))
            day_proofs=[]
            items=sorted(mapping[day].items());chunks=[items[i:i+32] for i in range(0,len(items),32)]
            for results in pool.map(lambda chunk:batch(day,chunk),chunks):
                for identity,state,count,proof,emitted in results:
                    states[identity]=(state,count);previous[identity]=day;day_proofs.append(proof)
                    for target,n in emitted:counts_done[target]+=n
            missing={i for d,i in requests if d==day}-set(mapping[day])
            if missing:raise ValueError('Requested listing absent from certified point-in-time universe')
            proofs.append(dict(day=day,units=day_proofs))
    definition=dict(version=VERSION,specs=SPECS,bootstrap='first eligible certified bar from frozen source origin',origin=days[0],
        observation_axis='completed nonempty one-second candles',carry='full sessions, across days, listing identity and opening-known splits',source_build=source['build_id'],source_definition_sha256=source['definition_hash'],math_sha256=file_hash(Path(__file__).with_name('indicator_math.py')))
    previous_hash=None
    for day,proof in zip(days,proofs):
        if counts_done[day]!=target_counts[day]:raise ValueError('Indicator row coverage did not reconcile')
        arrays[day].flush();path=output/day/'values.npy'
        write_json(output/day/'complete.json',dict(status='complete',version=VERSION,definition=definition,input_sha256=bases[day],
            values_sha256=file_hash(path),shape=list(arrays[day].shape),split_sha256=split_hashes[day],identity_map_sha256=identity_hashes[day],
            predecessor_receipt_sha256=previous_hash,source_proofs=proof['units'],required_rows=target_counts[day],prepared_rows=counts_done[day],validation_opened=day in datasets['validation']['days']))
        previous_hash=file_hash(output/day/'complete.json')
    for role,dataset in datasets.items():
        dataset=dict(dataset,indicator_root=str(output),indicator_receipts={d:file_hash(output/d/'complete.json') for d in dataset['days']})
        write_json(output/(role+'.json'),dataset)
    write_json(output/'complete.json',dict(status='complete',version=VERSION,definition=definition,days=days,listing_count=len(listing_set),
        storage=storage,elapsed_seconds=perf_counter()-start,receipts={d:file_hash(output/d/'complete.json') for d in days}))
    write_json(output/'status.json',dict(status='complete',completed_days=len(days),total_days=len(days),elapsed_seconds=perf_counter()-start))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('training','validation','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--workers',type=int,default=4)
    prepare(**vars(p.parse_args()))

if __name__=='__main__':main()

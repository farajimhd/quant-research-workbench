"""Frozen diverse sessions from certified Phase 2 pools, never historical events."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',POLARS_MAX_THREADS='4')
import argparse,hashlib,json,time,shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import polars as pl
from ..phase2_v1.data import sealed,plan_block
from ..v5.phase2_ar_shards.storage import ShardReader
from ..v5.phase2_ar_shards.contract import BAR,FRAMES
from ..phase2_v1.config import TOKENS
from ..v5.shards.phase1_contract import SAMPLE_DTYPE,file_hash,digest,write_json

SAMPLE=np.dtype([('context',SAMPLE_DTYPE,(16,)),('future',BAR,(64,)),('origin_us','<i8')])


def novel(ticker):return int(hashlib.sha256(ticker.encode()).hexdigest()[:8],16)%8==0


def check(row,minimum=None,maximum=None):
    c=row['context'];f=row['future'];origin=int(row['origin_us'])
    if c.shape!=(16,) or f.shape!=(64,):raise ValueError('Wrong joint shape')
    if np.any(c['end_us']>origin) or np.any(f['end_us']<=origin):raise ValueError('Future entered context or past entered labels')
    if np.any(np.diff(c['end_us'],axis=1)<=0) or np.any(np.diff(f['end_us'])<=0):raise ValueError('Unordered bars')
    if c['end_us'][7,-1]!=origin:raise ValueError('Latest 1s window must end at origin')
    if minimum is not None and int(c['end_us'].min())<minimum:raise ValueError('Context crosses partition purge boundary')
    if maximum is not None and int(f['end_us'].max())>=maximum:raise ValueError('Target crosses partition boundary')
    for values,valid in ((c['values'],c['valid']),(f['values'],f['valid'])):
        if not np.isfinite(values[valid.astype(bool)]).all():raise ValueError('Nonfinite valid source')
        if not np.isin(valid,(0,1)).all():raise ValueError('Invalid source mask')


def extract(item):
    reader=ShardReader(Path(item['root'])/item['path'],item['binding'])
    if reader.receipt['hash']!=item['receipt_hash']:raise ValueError('Release receipt changed')
    if not reader.price_basis:raise ValueError('Require certified session-adjusted source; no implicit adjustment')
    seed=int(hashlib.sha256((item['ticker']+item['date']).encode()).hexdigest()[:8],16)
    rng=np.random.default_rng(seed);order=rng.permutation(reader.receipt['blocks'])
    reject=Counter();result=[];identities=[]
    for index in order[:min(4,len(order))]:
        plan=plan_block(reader,int(index))
        if not len(plan.origins):reject['no_eligible_origins']+=1;continue
        picks=np.linspace(0,len(plan.origins)-1,min(4,len(plan.origins)),dtype=int)
        previous=-1000
        for selected in picks:
            if int(plan.origins[selected])-previous<64:reject['overlapping_target_support']+=1;continue
            row=np.zeros((),dtype=SAMPLE);row['origin_us']=plan.origin_us[selected];slot=0
            for frame,n in enumerate(TOKENS):
                for end in plan.endpoints[frame][selected]:
                    bars=reader.slice(frame,int(end)-63,int(end)+1)
                    for key in ('values','valid','end_us'):row['context'][slot][key]=bars[key]
                    row['context'][slot]['timeframe_us']=FRAMES[frame]
                    row['context'][slot]['split']=int(item['year']==2026);slot+=1
            end=int(plan.targets[selected]);row['future']=reader.slice(0,end-63,end+1)
            try:check(row,item['minimum'],item['maximum'])
            except ValueError as error:
                if 'partition' not in str(error):raise
                reject[str(error)]+=1;continue
            previous=int(plan.origins[selected]);result.append(row)
            identities.append(dict(ticker=item['ticker'],session=item['date'],year=item['year'],liquid=item['liquid'],novel=novel(item['ticker']),
                root=item['root'],path=item['path'],receipt_hash=item['receipt_hash'],block=int(index),origin_index=int(plan.origins[selected]),
                origin_us=int(row['origin_us']),context_start_us=int(row['context']['end_us'].min()),target_end_us=int(row['future']['end_us'][-1]),sampling_weight=1.))
        if result:break
    return result,identities,dict(reject)


def prepare(certificate,output,per_cell=240,workers=8,base=None):
    started=time.time();output.mkdir(parents=True,exist_ok=False);cert=sealed(certificate)
    base_manifest=None;base_keys=set()
    if base is not None:
        base_manifest=sealed(base/'manifest.json')
        if base_manifest['source_certificate_hash']!=cert['hash']:raise ValueError('Expansion source changed')
        for name,expected in base_manifest['files'].items():
            if file_hash(base/name)!=expected:raise ValueError('Base dataset changed')
        base_ids=json.loads((base/'train-identities.json').read_text())
        base_keys={r['ticker']+'|'+r['session'] for r in base_ids}
    if not cert.get('input_context_certified'):raise ValueError('Uncertified Phase 2 release')
    tables=[]
    for number,root in enumerate(cert['roots']):
        for name,key in [('plan.json','plan_sha256'),('result.json','result_sha256'),('ledger-file-v1/HEAD.json','ledger_head_sha256')]:
            if file_hash(Path(root['root'])/name)!=root[key]:raise ValueError('Certified authority changed')
        path=certificate.parent/root['units']['name']
        if file_hash(path)!=root['units']['sha256']:raise ValueError('Unit inventory changed')
        table=pl.read_parquet(path)
        if table.height!=root['units']['rows']:raise ValueError('Unit count changed')
        tables.append(table.with_columns(pl.lit(root['root']).alias('root'),pl.lit(root['binding']).alias('binding'),
            pl.col('path').str.replace_all('\\','/',literal=True).str.split('/').list.last().alias('date')))
    all_units=pl.concat(tables);total=all_units.height
    # Repeated publications of a ticker/session are one sampling group, not more evidence.
    units=all_units.sort('blocks',descending=True).unique(['ticker','date'],maintain_order=True).sort('ticker','date')
    tickers=units['ticker'].unique().to_list();held={t:novel(t) for t in tickers}
    units=units.with_columns(pl.col('ticker').replace_strict(held).alias('novel'))
    bounds={'train':(None,1704067200000000),'development':(1704067200000000,1735689600000000),
        'novel_development':(1704067200000000,1735689600000000),'audit_2025':(1735689600000000,1767225600000000),
        'audit_2026':(1735689600000000,None)}
    groups={
        'train':units.filter((pl.col('year')<=2023)&~pl.col('novel')),
        'development':units.filter((pl.col('year')==2024)&(pl.col('date')>='2024-07-01')&~pl.col('novel')),
        'novel_development':units.filter((pl.col('year')==2024)&(pl.col('date')>='2024-07-01')&pl.col('novel')),
        'audit_2025':units.filter((pl.col('year')==2025)&(pl.col('date')>='2025-07-01')),
        'audit_2026':units.filter(pl.col('year')==2026)}
    summary={};rejections=Counter(duplicate_publication_groups=total-units.height)
    for partition,table in groups.items():
        if base is not None and partition!='train':
            for suffix in ('.npy','-identities.json'):shutil.copyfile(base/(partition+suffix),output/(partition+suffix))
            summary[partition]=base_manifest['summary'][partition]
            continue
        selected=[]
        for _,cell in table.group_by('year','liquid',maintain_order=True):
            n=per_cell if partition=='train' else max(80,per_cell//2)
            if partition=='audit_2026':n=cell.height
            # Fixed seeded sample over independent session groups; not adjacent origins.
            if base is not None:
                cell=cell.with_columns((pl.col('ticker')+'|'+pl.col('date')).alias('_group'))
                retained=cell.filter(pl.col('_group').is_in(list(base_keys)))
                extra=cell.filter(~pl.col('_group').is_in(list(base_keys)))
                selected.extend(retained.drop('_group').to_dicts())
                selected.extend(extra.sample(n=min(max(0,n-retained.height),extra.height),seed=2026111709,shuffle=True).drop('_group').to_dicts())
            else:selected.extend(cell.sample(n=min(n,cell.height),seed=2026111709,shuffle=True).to_dicts())
        for item in selected:item.update(minimum=bounds[partition][0],maximum=bounds[partition][1])
        arrays=[];identities=[];done=0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for values,ids,rejected in pool.map(extract,selected):
                if time.time()-started>1800:raise TimeoutError('Data preparation thirty-minute ceiling')
                arrays.extend(values);identities.extend(ids);rejections.update(rejected);done+=1
                if done%20==0:write_json(output/'progress.json',dict(stage=partition,sessions=done,total=len(selected),examples=len(arrays),seconds=time.time()-started))
        if not arrays:raise ValueError('Empty partition '+partition)
        array=np.array(arrays,dtype=SAMPLE);np.save(output/(partition+'.npy'),array,allow_pickle=False)
        write_json(output/(partition+'-identities.json'),identities)
        if base is not None and partition=='train':
            def key(r):return (r['ticker'],r['session'],r['block'],r['origin_index'])
            index={key(r):i for i,r in enumerate(identities)};old=np.load(base/'train.npy',mmap_mode='r')
            for i,r in enumerate(base_ids):
                if key(r) not in index or old[i].tobytes()!=array[index[key(r)]].tobytes():raise ValueError('Expansion failed byte-exact nested training retention')
        price=array['context']['values'][:,7,-1,3]
        summary[partition]=dict(examples=len(array),sessions=len({(r['ticker'],r['session']) for r in identities}),tickers=len({r['ticker'] for r in identities}),
            price_bands=np.bincount(np.digitize(price,[.1,1,5,20,100]),minlength=6).tolist(),
            valid_market_counts=array['future']['valid'].sum((0,1)).tolist(),year_counts=dict(Counter(r['year'] for r in identities)),
            liquid_counts=dict(Counter(r['liquid'] for r in identities)),min_context_us=int(array['context']['end_us'].min()),max_target_us=int(array['future']['end_us'].max()))
    train=np.load(output/'train.npy',mmap_mode='r')
    if base is not None:shutil.copyfile(base/'tiny.npy',output/'tiny.npy')
    else:np.save(output/'tiny.npy',train[np.linspace(0,len(train)-1,16,dtype=int)],allow_pickle=False)
    result=dict(version='joint-v9-frozen-causal-1',source_certificate_hash=cert['hash'],source_certificate=str(certificate),
        geometry=dict(windows=16,bars=64,allocation=TOKENS,frames=FRAMES,future='next 64 observed 1s bars inside same source block'),
        split_policy='Train 2019-2023 excluding SHA-held tickers; development second-half 2024 with all context >=2024; novel development uses excluded tickers; sealed audits 2025/2026 with context >=2025. No train/development context-target overlap. Historical audits were exposed to prior unrelated experiments; not pristine external tests.',
        sampling='Seeded equal maximum session counts per year/liquidity; up to four nonoverlapping future supports/session. Uniform retained samples; report natural counts, no population claim.',
        seed=2026111709,summary=summary,rejections=dict(rejections),elapsed_seconds=time.time()-started,
        files={p.name:file_hash(p) for p in output.iterdir() if p.suffix=='.npy' or p.name.endswith('-identities.json')})
    if base is not None:result['expansion']=dict(base_hash=base_manifest['hash'],retained_training_examples=len(base_ids),unchanged_holdouts=True,retention='byte-exact examples; fixed source identities')
    result['hash']=digest(result);write_json(output/'manifest.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--certificate',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--per-cell',type=int,default=240);p.add_argument('--workers',type=int,default=8);p.add_argument('--base',type=Path);a=p.parse_args()
    print(json.dumps(prepare(a.certificate,a.output,a.per_cell,a.workers,a.base)))

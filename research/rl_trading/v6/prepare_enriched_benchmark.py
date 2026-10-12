"""Prepare enrichment once from certified products for frozen benchmark rows.

No target selection, label changes, broker simulation or historical writers.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
os.environ.setdefault('OMP_NUM_THREADS','2')
import argparse,gc,json,time,warnings
from pathlib import Path
from datetime import date,datetime,time as daytime
from zoneinfo import ZoneInfo
import numpy as np
import polars as pl
import torch
from research.rl_trading.v1.common import file_hash,digest
from research.rl_trading.v6.enriched_features import VERSION,SCALES,aggregate,timeframe_history,liquidity,quote_features,market_listing
from research.rl_trading.v6 import saved_label_audit as authority
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.ranked_teacher_data import load_prepared
from research.rl_trading.v6.run_architecture_benchmark import write,save


def origin(day,hour=4):
    return int(datetime.combine(date.fromisoformat(str(day)),daytime(hour),ZoneInfo('America/New_York')).timestamp()*1_000_000)


def listing(bank,identity,end):
    left,right=bank.manifest['offsets'][identity]
    clocks=bank.close_us[left:right];stop=int(np.searchsorted(clocks,end,side='left'))
    return np.asarray(clocks[:stop]),np.asarray(bank.scalar[left:left+stop])


def prior_day(session):
    plan=json.loads((session.previous.root.parent/'plan.json').read_text())
    if plan['hash']!=session.previous.manifest['source_hash'] or digest({k:v for k,v in plan.items() if k!='hash'})!=plan['hash']:
        raise ValueError('Prior session calendar binding changed')
    return plan['day']


def source_history(session,identity,end,previous_day):
    parts=[];origins=[]
    if session.previous and identity in session.previous.manifest['offsets']:
        day=previous_day;start=origin(day,18)
        clocks,scalar=listing(session.previous,identity,origin(day,20)+1)
        begin=np.searchsorted(clocks,start,side='left')
        parts.append((clocks[begin:],scalar[begin:]));origins.append(np.full(len(clocks)-begin,origin(day),np.int64))
    clocks,scalar=listing(session.bank,identity,end)
    parts.append((clocks,scalar));origins.append(np.full(len(clocks),origin(session.day),np.int64))
    return np.concatenate([x[0] for x in parts]),np.concatenate([x[1] for x in parts]),np.concatenate(origins)


def market_context(session,clocks,names):
    """Bounded cross-section: each listing evaluated once at unique target clocks."""
    identities=session.listings
    if len(set(names[i] for i in identities))!=len(identities):
        raise ValueError('Market universe contains duplicate symbols; explicit identity reconciliation required')
    values=np.zeros((len(identities),len(clocks),9),np.float32)
    for i,identity in enumerate(identities):
        t,x=listing(session.bank,identity,int(clocks.max())+1)
        if len(t):values[i]=market_listing(t,x,clocks)
    columns=[]
    for h in range(3):
        valid=values[:,:,h*2+1]>0;counts=valid.sum(0)
        returns=np.where(valid,values[:,:,h*2],np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',RuntimeWarning)
            median=np.nanmedian(returns,axis=0);dispersion=np.nanstd(returns,axis=0)
        columns.extend((np.nan_to_num(median),np.divide(((returns>0)&valid).sum(0),counts,out=np.zeros(len(clocks)),where=counts>0),
            np.nan_to_num(dispersion),counts/len(identities),np.log1p(counts)))
    columns.extend(((values[:,:,6]>0).mean(0),np.log1p(np.expm1(values[:,:,7]).sum(0)),np.log1p(np.expm1(values[:,:,8]).sum(0))))
    for symbol in ('SPY','QQQ'):
        index=next((i for i,k in enumerate(identities) if names[k]==symbol),None)
        columns.extend(values[index,:,j] if index is not None else np.zeros(len(clocks)) for j in range(6))
    return np.column_stack(columns).astype(np.float32)


def prepare_day(session,keys,names,quotes):
    clocks=np.array([k[1] for k in keys],np.int64);unique,inverse=np.unique(clocks,return_inverse=True)
    multi=np.zeros((len(keys),3,120,16),np.float32);present=np.zeros((len(keys),3,120),bool)
    activity=np.zeros((len(keys),30),np.float32)
    previous_day=prior_day(session)
    groups={}
    for i,k in enumerate(keys):groups.setdefault(k[2][len(k[0])+1:].split(':pair:')[0],[]).append(i)
    for identity,rows in groups.items():
        target=clocks[rows];t,x,origins=source_history(session,identity,int(target.max())+1,previous_day)
        if not len(t):raise ValueError('Target listing lacks source history')
        for j,seconds in enumerate(SCALES):
            table=aggregate(t,x,origins,seconds)
            previous_origin=origin(previous_day)
            multi[rows,j],present[rows,j]=timeframe_history(table,target,previous_origin,origin(session.day),seconds)
        activity[rows,:25]=liquidity(t,x,target)
        activity[rows,25:]=quote_features(quotes.filter(pl.col('ticker')==names[identity]),target)
    market=market_context(session,unique,names)[inverse]
    return dict(multi=torch.from_numpy(multi),multi_present=torch.from_numpy(present),liquidity=torch.from_numpy(activity),market=torch.from_numpy(market))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark',type=Path,default=Path('D:/TradingML/runtimes/v6-transformer-search-32-project-20261011'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();runtime=Path('D:/TradingML/runtimes').resolve();out=args.output.resolve()
    if not runtime.is_dir() or not out.is_relative_to(runtime) or out.exists():raise ValueError('Fresh explicit runtime required')
    out.mkdir();start=time.perf_counter()
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS);torch.set_num_threads(2)
    benchmark=args.benchmark;manifest=json.loads((benchmark/'manifest.json').read_text());audit=json.loads((benchmark/'final-comparison-audit.json').read_text())
    if audit['status']!='passed' or audit['candidates']!=32:raise ValueError('Completed audited comparison required')
    authority_root=Path(manifest['source_runtime']);os.environ['RL_V6_LABEL_AUDIT_RUNTIME']=str(authority_root)
    parent_root=Path(manifest['parent_runtime']);parent=json.loads((parent_root/'manifest.json').read_text())
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path.cwd()),verbose=False)
    from research.rl_trading.v1.arte_source import load_build
    from research.rl_trading.v1.arte_sql import ArteReader,literal
    from research.rl_trading.v6.environment_source import ArteExecutionSource
    from research.rl_trading.v6.sparse_coverage import certify_source
    repair=authority_root/'rl-v6-reporting-repair-20261002';ledger=repair/'build-ledger-v2.sqlite3'
    reader=ArteReader(threads=2);bindings=[];frames={}
    try:
        for role in ('train','validation'):
            snapshot=benchmark/(role+'.pt')
            expected=manifest['train_sha256'] if role=='train' else json.loads((benchmark/'validation.json').read_text())['sha256']
            if file_hash(snapshot)!=expected:raise ValueError('Frozen snapshot changed')
            data=torch.load(snapshot,weights_only=False);chunks={name:[] for name in ('multi','multi_present','liquidity','market')};indices=[]
            for day in sorted({k[0] for k in data['keys']}):
                if psutil.virtual_memory().available<8*1024**3:raise RuntimeError('Host reserve below8GiB')
                write(out/'progress.json',dict(phase='preparing',role=role,day=day,completed_days=len(bindings),active=1))
                rows=[i for i,k in enumerate(data['keys']) if k[0]==day];keys=[data['keys'][i] for i in rows]
                if role=='train':
                    binding=next(b for b in parent['sessions'] if b['day']==day);cache=Path(binding['cache'])
                    source=Path(parent['arguments']['initial_source']) if day==parent['sessions'][0]['day'] else cache/'source.json'
                    if file_hash(source)!=binding['source_sha256']:raise ValueError('TRAIN source binding changed')
                    old=json.loads(source.read_text())
                    _,entry,_,_,bankroot,_=authority.session(day)
                    if entry['bank_certificate_sha256']!=old['bank_certificate_sha256']:raise ValueError('TRAIN bank changed since frozen snapshot')
                    session=open_session(bankroot,runtime_root=authority_root,previous_root=authority.mapped(entry['previous_root']))
                else:
                    if day not in ('2026-08-24','2026-08-25'):raise ValueError('No other validation/ sealed access authorized')
                    _,entry,_,_,bankroot,_=authority.session(day)
                    session=open_session(bankroot,runtime_root=authority_root,previous_root=authority.mapped(entry['previous_root']))
                if session.role!=('train' if role=='train' else 'development'):raise ValueError('Role mismatch')
                names=authority.saved_symbols(str(session.root),session.source_certificate_sha256)
                tickers=sorted({names[k[2][len(day)+1:].split(':pair:')[0]] for k in keys})
                build=load_build(repair/'bars/latest.json',ledger,[date.fromisoformat(day)],tickers)
                bank_plan=json.loads((session.root/'plan.json').read_text())
                if build['build_id']!=bank_plan['source_build_id'] or build['definition_hash']!=bank_plan['source_definition_hash']:
                    raise ValueError('Quote build and fixed candle build differ')
                end=max(k[1] for k in keys);execution=ArteExecutionSource(reader,build,ledger,date.fromisoformat(day),end_us=end)
                _,quote_certificate=certify_source(execution,tickers)
                scopes=','.join(f'({literal(t)},toUUID({literal(execution.attempts[t])}))' for t in tickers)
                first=(min(k[1] for k in keys)-execution.origin-1_000000)//100000;last=(end-execution.origin)//100000-1
                from research.rl_trading.v6.environment_source import EXECUTION_SCHEMA
                q=execution._frame('SELECT '+','.join(k for k in EXECUTION_SCHEMA if k not in ('quote_count','extrema_count'))+
                    f' FROM arte.liquidity_100ms_v1 WHERE build_id={literal(build["build_id"])} AND session_date=toDate({literal(day)}) AND resolution_ms=100 AND bucket_index BETWEEN {first} AND {last} AND (ticker,attempt_id) IN ({scopes}) ORDER BY ticker,bucket_index',
                    {k:v for k,v in EXECUTION_SCHEMA.items() if k not in ('quote_count','extrema_count')})
                q=q.with_columns((pl.lit(execution.origin)+(pl.col('bucket_index')+1)*100000).alias('bucket_end_us'))
                features=prepare_day(session,keys,names,q)
                for name in chunks:chunks[name].append(features[name])
                indices.extend(rows);bindings.append(dict(role=role,day=day,bank=session.source_certificate_sha256,split=session.context_split_receipt_sha256,quote_certificate=quote_certificate,query_rows=q.height))
                del session,features,q;gc.collect()
            order=np.argsort(indices)
            for name in chunks:data[name]=torch.cat(chunks[name])[order]
            save(out/(role+'.pt'),data)
            del data,chunks;gc.collect()
    finally:reader.close()
    train=torch.load(out/'train.pt',weights_only=False);normalization={}
    for name in ('multi','liquidity','market'):
        x=(train[name][train['multi_present']] if name=='multi' else train[name].reshape(-1,train[name].shape[-1])).double()
        if not len(x):raise ValueError('No TRAIN observations for enrichment normalization')
        mean=x.mean(0);std=x.std(0,unbiased=False);std=torch.where(std>1e-6,std,1.)
        normalization[name]=dict(mean=mean.tolist(),std=std.tolist())
    write(out/'normalization.json',normalization)
    proof=dict(version=VERSION,status='prepared',source_benchmark=str(benchmark),source_manifest_sha256=file_hash(benchmark/'manifest.json'),train_sha256=file_hash(out/'train.pt'),validation_sha256=file_hash(out/'validation.pt'),normalization_sha256=file_hash(out/'normalization.json'),bindings=bindings,seconds=time.perf_counter()-start,sealed_labels_read=False,label_selection_changed=False,cross_decision_feedback=False)
    write(out/'complete.json',proof);write(out/'progress.json',dict(phase='prepared',active=0,completed_days=len(bindings)))
    print(json.dumps(proof),flush=True)


if __name__=='__main__':main()

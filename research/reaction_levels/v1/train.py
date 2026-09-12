"""Chronological books -> causal partitions -> frozen fit -> one test day."""
import argparse
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from math import prod
from pathlib import Path
import subprocess
import time

import exchange_calendars as xc
import joblib
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits

from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from src.backend.historical_session_level_source import _query
from src.market_engine.historical_session_levels import extract
from src.market_engine.historical_level_checkpoint import seed,consolidate,digest
from scripts.build_structure_book_clickhouse import canonical_splits
from .config import CONTRACT
from .source import session_inputs,write_json
from .data import feature_rows
from .objectives import label_rows
from .model import train,calibrate,predict,metrics


def file_hash(path):
    h=sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()


def read(path):return json.loads(path.read_text())


def extracted(value,ticker,day):
    return extract(value['bars'],value['profile'],ticker=ticker,session=day,
                   available_at=datetime.fromisoformat(value['source']['end']).timestamp(),source=value['source'])


def adjusted(book,factor):
    result=deepcopy(book)
    for level in result['levels']:
        for key in ('lower','upper','price'):level[key]*=factor
        for role in level.get('reaction_center',{}).get('roles',{}).values():
            for key in ('center','scale'):
                if role.get(key) is not None:role[key]*=factor
    return result


def prepare(root,ticker,day,book,splits):
    started=time.perf_counter();inputs,cached=session_inputs(root,ticker,day)
    input_time=time.perf_counter()-started
    actions=[s for s in splits if book['session']<s['execution_date']<=day]
    factor=prod(float(s['split_from'])/float(s['split_to']) for s in actions)
    meta_path=root/'partitions'/f'{day}.json';parquet=meta_path.with_suffix('.parquet')
    checkpoint=root/'books'/f'{day}.json'
    if meta_path.exists():
        meta=read(meta_path)
        if (meta['input_hash']!=inputs['content_hash'] or meta['prior_hash']!=book['checkpoint_hash']
                or meta['split_actions']!=actions or file_hash(parquet)!=meta['parquet_hash']
                or file_hash(checkpoint)!=meta['checkpoint_file_hash']):
            raise ValueError(f'Prepared partition provenance changed: {day}')
        return read(checkpoint),meta,True
    effective=adjusted(book,factor);stage=time.perf_counter()
    rows,features,audit,grid=feature_rows(inputs,effective)
    rows=label_rows(rows,grid,effective['levels'])
    rows['book_hash']=book['checkpoint_hash'];rows['session']=day
    parquet.parent.mkdir(parents=True,exist_ok=True)
    temp=parquet.with_suffix('.parquet.tmp');rows.to_parquet(temp,index=False);temp.replace(parquet)
    feature_time=time.perf_counter()-stage;stage=time.perf_counter()
    today=extracted(inputs,ticker,day)
    final=consolidate(book,today,inputs['bars'],inputs['profile'],split_factor=factor,split_evidence=actions)
    write_json(checkpoint,final)
    meta=dict(session=day,input_hash=inputs['content_hash'],prior_hash=book['checkpoint_hash'],
              checkpoint_hash=final['checkpoint_hash'],checkpoint_file_hash=file_hash(checkpoint),
              parquet_hash=file_hash(parquet),features=features,audit=audit,split_actions=actions,
              rows=len(rows),labeled=int((rows.label>=0).sum()),censored=int((rows.label<0).sum()),
              label_counts={str(k):int(v) for k,v in rows.label.value_counts().items()},
              future_horizon=CONTRACT['horizon_seconds'],levels=len(final['levels']),
              timings=dict(input_seconds=input_time,features_labels_seconds=feature_time,
                           book_seconds=time.perf_counter()-stage,total_seconds=time.perf_counter()-started))
    write_json(meta_path,meta)
    return final,meta,False


def matrix(root,days,features,name):
    counts=[read(root/'partitions'/f'{d}.json')['labeled'] for d in days];n=sum(counts)
    arrays=root/'matrices';arrays.mkdir(exist_ok=True)
    x=np.lib.format.open_memmap(arrays/f'{name}-x.npy',mode='w+',dtype='float32',shape=(n,len(features)))
    y=np.lib.format.open_memmap(arrays/f'{name}-y.npy',mode='w+',dtype='int8',shape=(n,))
    offset=0
    for day,count in zip(days,counts):
        frame=pd.read_parquet(root/'partitions'/f'{day}.parquet',columns=features+['label'])
        frame=frame[frame.label>=0]
        if len(frame)!=count:raise ValueError('Partition row count mismatch')
        x[offset:offset+count]=frame[features].to_numpy(dtype='float32');y[offset:offset+count]=frame.label.to_numpy();offset+=count
    x.flush();y.flush();return x,y


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ticker',default='AAPL');p.add_argument('--start',default='2026-07-01')
    p.add_argument('--train-end',default='2026-08-20');p.add_argument('--test-day',default='2026-08-21')
    p.add_argument('--calibration-sessions',type=int,default=5)
    p.add_argument('--runtime',type=Path,default=Path(r'D:\TradingML\runtimes\reaction-level-model\AAPL-jul-aug2026-v1-batched'))
    args=p.parse_args();root=args.runtime.resolve();allowed=Path(r'D:\TradingML\runtimes').resolve()
    if not allowed.is_dir() or not root.is_relative_to(allowed):raise ValueError('Required runtime root unavailable or invalid')
    if not args.start<=args.train_end<args.test_day:raise ValueError('Invalid chronological date split')
    calendar=xc.get_calendar('XNYS');sessions=calendar.sessions_in_range(args.start,args.train_end).strftime('%Y-%m-%d').tolist()
    if not 0<args.calibration_sessions<len(sessions):raise ValueError('Invalid calibration session count')
    if not calendar.is_session(args.test_day):raise ValueError('Test day is not a trading session')
    seed_day=calendar.previous_session(sessions[0]).strftime('%Y-%m-%d')
    gap=calendar.sessions_in_range(args.train_end,args.test_day).strftime('%Y-%m-%d').tolist()
    if len(gap)!=2:raise ValueError('Test must immediately follow training cutoff for this runner')
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    from src.backend.swing_book_source import source_metadata,HISTORICAL_POLICY
    source_metadata(args.ticker,seed_day,policy=HISTORICAL_POLICY)
    coverage=_query(f"SELECT source_date FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker='{args.ticker}' AND source_date>='{seed_day}' AND source_date<='{args.test_day}' ORDER BY source_date")
    missing=set([seed_day,*sessions,args.test_day])-{r['source_date'] for r in coverage}
    if missing:raise ValueError(f'Missing certified sessions: {sorted(missing)}')
    splits=canonical_splits(_query(f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker='{args.ticker}' AND execution_date>'{seed_day}' AND execution_date<='{args.test_day}' ORDER BY execution_date"))
    repo=Path(__file__).resolve().parents[3]
    files=list(Path(__file__).parent.glob('*.py'))+[repo/'src/market_engine'/n for n in ('historical_session_levels.py','historical_level_checkpoint.py','reaction_center.py')]+[repo/'src/backend/historical_session_level_source.py',repo/'src/backend/swing_book_source.py',repo/'scripts/train_level_reaction.py']
    fit_days=sessions[:-args.calibration_sessions];cal_days=sessions[-args.calibration_sessions:]
    spec=dict(contract=CONTRACT,ticker=args.ticker,seed_day=seed_day,fit_days=fit_days,calibration_days=cal_days,test_day=args.test_day,
              source_files={str(f.relative_to(repo)):file_hash(f) for f in files},splits=splits,
              sklearn_version=sklearn.__version__,runtime=str(root),git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip())
    # Commit text is informational; source hashes determine compatible resume.
    root.mkdir(parents=True,exist_ok=True);manifest=root/'manifest.json'
    if manifest.exists():
        old=read(manifest)
        if {k:v for k,v in old.items() if k!='git_commit'}!={k:v for k,v in spec.items() if k!='git_commit'}:
            raise ValueError('Run contract/source changed; choose a new runtime directory')
    else:write_json(manifest,spec)
    def progress(stage,completed,detail):
        write_json(root/'status.json',dict(stage=stage,completed=completed,total=len(sessions)+1,detail=detail,updated_at=datetime.now().astimezone().isoformat()))
        print(f'[{completed}/{len(sessions)+1}] {stage}: {detail}',flush=True)
    try:
        progress('seed',0,f'{args.ticker} {seed_day}; fit {len(fit_days)} sessions, calibrate {len(cal_days)}, test {args.test_day}')
        initial,_=session_inputs(root,args.ticker,seed_day)
        seed_path=root/'books'/f'{seed_day}.json'
        book=seed(extracted(initial,args.ticker,seed_day),reaction_inputs=(initial['bars'],initial['profile']))
        if seed_path.exists() and read(seed_path)!=book:raise ValueError('Seed checkpoint changed')
        write_json(seed_path,book)
        for i,day in enumerate(sessions,1):
            progress('prepare',i-1,day)
            book,meta,resumed=prepare(root,args.ticker,day,book,splits)
            progress('prepared',i,f"{day}: {meta['labeled']:,} labeled, {meta['censored']:,} censored, {meta['levels']} levels"+(' (verified resume)' if resumed else f"; {meta['timings']['total_seconds']:.1f}s"))
        features=meta['features']
        if any(read(root/'partitions'/f'{day}.json')['features']!=features for day in sessions):raise ValueError('Feature schema drift')
        artifact=root/'model.joblib';frozen=root/'model-manifest.json'
        with threadpool_limits(limits=4):
            if frozen.exists():
                model_meta=read(frozen)
                if file_hash(artifact)!=model_meta['model_hash']:raise ValueError('Frozen model hash mismatch')
                bundle=joblib.load(artifact);model=bundle['model'];calibration=bundle['calibration'];base=np.array(bundle['baseline'])
            else:
                progress('training',len(sessions),'Building memory-mapped fit/calibration matrices; 4 CPU threads')
                x,y=matrix(root,fit_days,features,'fit');cx,cy=matrix(root,cal_days,features,'calibration')
                started=time.perf_counter();model=train(x,y);fit_seconds=time.perf_counter()-started
                progress('calibration',len(sessions),f'Tree fit complete in {fit_seconds:.1f}s; calibrating on {cal_days[0]} through {cal_days[-1]}')
                started=time.perf_counter();calibration=calibrate(model,cx,cy);cal_seconds=time.perf_counter()-started
                base=(np.bincount(y,minlength=4)+1)/(len(y)+4)
                bundle=dict(model=model,calibration=calibration,features=features,baseline=base.tolist(),contract=CONTRACT)
                temp=artifact.with_suffix('.tmp');joblib.dump(bundle,temp,compress=3);temp.replace(artifact)
                model_meta=dict(model_hash=file_hash(artifact),manifest_hash=file_hash(manifest),fit_rows=len(y),calibration_rows=len(cy),features=features,
                                fit_seconds=fit_seconds,calibration_seconds=cal_seconds,fit_days=fit_days,calibration_days=cal_days,
                                calibration_metrics=metrics(cy,predict(model,calibration,cx)),frozen_before_test=True)
                write_json(frozen,model_meta)
                del x,y,cx,cy
            progress('test',len(sessions),f'Model frozen; now preparing {args.test_day}')
            _,test_meta,_=prepare(root,args.ticker,args.test_day,book,splits)
            test=pd.read_parquet(root/'partitions'/f'{args.test_day}.parquet');valid=test.label>=0
            started=time.perf_counter();prob=predict(model,calibration,test[features].to_numpy(dtype='float32'));inference_seconds=time.perf_counter()-started
            for i,label in enumerate(CONTRACT['labels']):test['p_'+label]=prob[:,i]
            test.to_parquet(root/'test-predictions.parquet',index=False)
            y=test.loc[valid,'label'].to_numpy();pvalid=prob[valid]
            evaluation=dict(test_day=args.test_day,model_hash=file_hash(artifact),test_book_hash=test_meta['prior_hash'],
                            test=metrics(y,pvalid),baseline=metrics(y,np.tile(base,(len(y),1))),test_partition=test_meta,
                            batch_inference_seconds=inference_seconds,scored_rows=len(test),
                            by_target={},by_session_period={})
            for side,name in [(1,'upper'),(0,'lower')]:
                mask=valid&(test.target_upper==side)
                if mask.any():evaluation['by_target'][name]=metrics(test.loc[mask,'label'],prob[mask])
            for name,mask in [('premarket',test.session_seconds<=19800),('regular',(test.session_seconds>19800)&(test.session_seconds<=43200)),('afterhours',test.session_seconds>43200)]:
                mask=mask&valid
                if mask.any():evaluation['by_session_period'][name]=metrics(test.loc[mask,'label'],prob[mask])
            contacts=test[valid&(test.touch_time>=0)].drop_duplicates(['level_id','target_upper','touch_time'])
            evaluation['distinct_touch_timestamp_keys']=len(contacts)
            if len(contacts):evaluation['first_prediction_per_touch_timestamp']=metrics(contacts.label,contacts[['p_'+s for s in CONTRACT['labels']]].to_numpy())
            # Single-row path timing, not confused with batch throughput.
            sample=test[features].iloc[:1].to_numpy(dtype='float32');latencies=[]
            for _ in range(100):
                started=time.perf_counter();predict(model,calibration,sample);latencies.append(time.perf_counter()-started)
            evaluation['single_row_model_latency_ms']=dict(median=float(np.median(latencies)*1000),p95=float(np.quantile(latencies,.95)*1000))
            write_json(root/'evaluation.json',evaluation)
        report=[f'# {args.ticker} causal level-reaction study','',f"Fit: {fit_days[0]} to {fit_days[-1]}; calibration: {cal_days[0]} to {cal_days[-1]}; test: {args.test_day}.",'',
                f"Seed book: {seed_day}. One-second inference, 60-second horizon. Historical geometry only; future gaps censored.",'',
                '| Metric | Model | Training-frequency baseline |','|---|---:|---:|']
        for key in ('log_loss','brier','accuracy','balanced_accuracy','resolved_contact_log_loss'):
            if key in evaluation['test']:report.append(f"| {key} | {evaluation['test'][key]:.5f} | {evaluation['baseline'][key]:.5f} |")
        report+=['',f"Fit rows: {model_meta['fit_rows']:,}; calibration rows: {model_meta['calibration_rows']:,}; scored test rows: {len(test):,}; censored: {test_meta['censored']:,}.",
                 f"Tree fit: {model_meta['fit_seconds']:.2f}s; calibration: {model_meta['calibration_seconds']:.2f}s; test batch inference: {inference_seconds:.2f}s.",'',
                 'These are correlated one-second forecasts, not independent trades. The test is historically exposed in earlier level research, although it was excluded from this fit and calibration. No trading profitability or production acceptance is established.',
                 'Quote features use latest canonical quote events (primary ask, secondary bid), not reconstructed exchange depth or a certified NBBO consolidation. Reaction outcomes use 1s close-based rules, not intrasecond execution order.',
                 'Training examples, censored records, predictions, source hashes, daily books and full metrics are retained in this run directory.']
        (root/'report.md').write_text('\n'.join(report),encoding='utf-8')
        progress('complete',len(sessions)+1,f"Test log loss {evaluation['test']['log_loss']:.4f}; baseline {evaluation['baseline']['log_loss']:.4f}; report.md")
    except BaseException as error:
        write_json(root/'status.json',dict(stage='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',error=str(error),restart='Rerun identical command; completed partitions are verified and reused'))
        raise

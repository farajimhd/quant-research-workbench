"""Offline V66 supervision. Existing market shards and optimizers are read-only."""
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os
import time
import numpy as np
import polars as pl
from research.market_shards.v1.data import Session
from research.market_shards.v1.contract import origin_us, sha
from research.vectorized_backtest.v66.indicator_math import calculate as indicators, initial_state
from research.vectorized_backtest.v66.features import VARIANTS
from research.rl_trading.v6.price_action_opportunities import Config, calculate, VERSION as ALGORITHM
from research.rl_trading.v6.market_teacher_preview import Config as Selection, select, group, VERSION as GROUPING
from research.rl_trading.v6.market_teacher_dataset import copy_labels

from .contract import VERSION,SCOPE


def write_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,allow_nan=False),encoding='utf-8')
    os.replace(temporary,path)


def fingerprint():
    root=Path(__file__).resolve().parents[3]
    names=['research/market_labels/v1/build.py','research/market_labels/v1/run_build.py','research/market_labels/v1/contract.py',
        'research/market_shards/v1/data.py','research/market_shards/v1/contract.py',
        'research/vectorized_backtest/v66/indicator_math.py','research/vectorized_backtest/v66/features.py',
        'research/rl_trading/v6/price_action_labels.py','research/rl_trading/v6/price_action_opportunities.py',
        'research/rl_trading/v6/episode_liquidity.py','research/rl_trading/v6/market_teacher_preview.py',
        'research/rl_trading/v6/market_teacher_dataset.py']
    return {name:sha(root/name) for name in names}


def decode(frame):
    """Exactly V66's recurrence, emitting every observed bar for offline labels."""
    frame=frame.sort('time_us');times=frame['time_us'].to_numpy()
    if np.any(np.diff(times)<=0):raise ValueError('Duplicate/unordered source candles')
    close=frame['close'].to_numpy().astype(float)
    valid=(frame['price_valid'].to_numpy()==1)&np.isfinite(close)&(close>0)
    extremes=frame['extremes_valid'].to_numpy()==1
    state,counts=initial_state()
    values,_,_=indicators(close,frame['high'].to_numpy(),frame['low'].to_numpy(),valid,extremes,state,counts,1.,np.ones(len(frame),bool))
    names={f.name:i for i,f in enumerate(VARIANTS)}
    line=values[:,names['indicator.macd_line.12.26.relative']].astype(float)*close
    signal=values[:,names['indicator.macd_signal.12.26.9.relative']].astype(float)*close
    price_ok=valid&extremes
    mask=price_ok&np.isfinite(line)&np.isfinite(signal)
    bars=frame.with_columns(pl.Series('macd_line',line),pl.Series('macd_signal',signal)).filter(pl.Series(mask))
    excluded=frame.filter(pl.Series(~mask)).select('time_us','open','high','low','close','price_valid','extremes_valid').with_columns(
        pl.Series('reason',np.where(~price_ok[~mask],'invalid_price','indicator_warmup')))
    return bars.select('time_us','open','high','low','close','macd_line','macd_signal'),excluded


def verify_day(root, binding=None):
    root=Path(root);proof=json.loads((root/'complete.json').read_bytes())
    if proof['version']!=VERSION or (binding is not None and proof['binding']!=binding):raise ValueError('Label day binding changed')
    for name,record in proof['files'].items():
        path=root/name
        if sha(path)!=record['sha256']:raise ValueError('Label shard hash changed: '+name)
        if pl.scan_parquet(path).select(pl.len()).collect().item()!=record['rows']:raise ValueError('Label row count changed')
    if proof['source_rows']!=proof['valid_rows']+proof['excluded_rows']:raise ValueError('Source census mismatch')
    return proof


def build_day(task):
    manifest,day,role,output,binding=task
    root=Path(output)/day
    if (root/'complete.json').exists():return verify_day(root,binding)
    root.mkdir(parents=True,exist_ok=True)
    session=Session(manifest,day)
    names=sorted(session.item['selected'])
    raw=session._read(day,'trade-1000',names)
    if raw is None:raise ValueError('Missing sealed one-second trade data')
    begin=origin_us(day)+14401_000000;finish=origin_us(day)+34201_000000
    if raw.filter((pl.col('time_us')<begin)|(pl.col('time_us')>=finish)).height:raise ValueError('Source outside V66 decision interval')
    scanner=session.selection(np.arange(begin,finish,1_000_000,dtype=np.int64))['tickers']
    cfg=Config();sel=Selection();records=[];products={k:[] for k in ('episodes','pairs','trades')};entries=[];excluded=[]
    for index,ticker in enumerate(names):
        frame=raw.filter(pl.col('ticker')==ticker).sort('time_us')
        bars,invalid=decode(frame)
        key=hashlib.sha256(ticker.encode()).hexdigest()[:24]+'.parquet'
        record=dict(ticker=ticker,listing_id=ticker,file=key,source_rows=frame.height,valid_rows=bars.height,excluded_rows=invalid.height)
        records.append(record)
        excluded.append(invalid.with_columns(pl.lit(ticker).alias('listing_id')))
        if bars.height:
            outputs=calculate(bars,cfg,activity=frame.select('time_us','volume','trade_count'))
            labels=outputs[0].with_columns(pl.lit(ticker).alias('listing_id'))
            ix=((labels['time_us'].to_numpy()-begin)//1_000_000).astype(int)
            labels=labels.with_columns(pl.Series('scanner_member',(scanner[ix]==ticker).any(axis=1)))
            labels.write_parquet(root/key,compression='zstd')
            entries.append(labels.filter(pl.col('action')=='ENTRY').select('listing_id','pair_id','time_us','close','entry_gain','entry_target_us','action'))
            for kind,data in zip(('episodes','pairs','trades'),outputs[1:]):
                if data.height:
                    data=data.with_columns(pl.lit(ticker).alias('listing_id'))
                    if kind=='pairs':data=data.with_columns((pl.col('end_us')>=int(bars['time_us'][-1])+1_000_000).alias('right_censored'))
                    products[kind].append(data)
        write_json(root/'progress.json',dict(day=day,completed=index+1,total=len(names),ticker=ticker,status='1a'))
    # Typed empty geometry comes from the unchanged producer's schema helper.
    sample=pl.DataFrame(dict(time_us=[begin],open=[1.],high=[1.],low=[1.],close=[1.],macd_line=[1.],macd_signal=[0.]))
    empty=calculate(sample,cfg,activity=pl.DataFrame(dict(time_us=[begin],volume=[0.],trade_count=[0])))
    merged={k:pl.concat(v,how='diagonal_relaxed') if v else e.head(0).with_columns(pl.lit('',dtype=pl.String).alias('listing_id')) for (k,v),e in zip(products.items(),empty[1:])}
    candidates=pl.concat(entries,how='vertical_relaxed') if entries else empty[0].head(0).with_columns(pl.lit('').alias('listing_id')).select('listing_id','pair_id','time_us','close','entry_gain','entry_target_us','action')
    decisions=select(candidates,merged['pairs'],sel)
    members,groups=group(decisions.filter(pl.col('selected')),sel)
    decisions=decisions.join(members.select('listing_id','pair_id','group_id','allocation_ratio','active_score_sum','active_count'),on=['listing_id','pair_id'],how='left',validate='1:1').with_columns(pl.col('allocation_ratio').fill_null(0.))
    files={}
    def save(name,data):
        path=root/name;data.write_parquet(path,compression='zstd')
        files[name]=dict(rows=data.height,sha256=sha(path))
    for record in records:
        if not record['valid_rows']:continue
        original=pl.read_parquet(root/record['file']);copied=copy_labels(original,decisions)
        if copied.height!=original.height or not copied.select('time_us','listing_id').equals(original.select('time_us','listing_id')):raise ValueError('1b changed candle identities')
        unchanged=[c for c in original.columns if c not in ('action','reference_action','label_value')]
        if not copied.select(unchanged).equals(original.select(unchanged)):raise ValueError('1b changed source/1a values')
        if copied.filter((pl.col('episode_selected')==False)&((pl.col('action')!='WAIT')|pl.col('allocation_loss_mask'))).height:raise ValueError('Rejected pair leaked')
        record['actions_1a']=original.group_by('action').len().sort('action').to_dicts()
        record['actions_1b']=copied.group_by('action').len().sort('action').to_dicts()
        save(record['file'],copied)
    for kind,data in merged.items():save(kind+'.parquet',data)
    save('decisions.parquet',decisions);save('excluded.parquet',pl.concat(excluded,how='vertical_relaxed'))
    # Independent event-set verification of every 1b allocation denominator.
    if members.height:
        starts=members['time_us'].to_numpy();ends=members['entry_target_us'].to_numpy();scores=members['selection_score'].to_numpy()
        def total(t):
            order=np.argsort(t,kind='stable');return np.r_[0.,np.cumsum(scores[order])][np.searchsorted(t[order],starts,side='right')]
        denominator=total(starts)-total(ends)
        if not np.allclose(denominator,members['active_score_sum'].to_numpy(),rtol=1e-9,atol=1e-9):raise ValueError('1b active set mismatch')
        if not np.allclose(scores/denominator,members['allocation_ratio'].to_numpy()):raise ValueError('1b allocation mismatch')
    proof=dict(version=VERSION,day=day,role=role,binding=binding,scope=SCOPE,config=asdict(cfg),selection=asdict(sel),
        begin_us=begin,finish_us=finish,files=files,listings=records,groups=groups,
        source_rows=sum(r['source_rows'] for r in records),valid_rows=sum(r['valid_rows'] for r in records),excluded_rows=sum(r['excluded_rows'] for r in records),
        selected_pairs=members.height,pairs=decisions.height,status='verified_review_only')
    write_json(root/'complete.json',proof)
    return verify_day(root,binding)

"""Sparse execution features and hindsight scores from pinned 100 ms products.

Policy table is strictly past-only. Future entry/exit windows occur only in
score labels. No dense quote/feature shard is persisted and old labels survive.
"""
import json
import hashlib
from pathlib import Path
import numpy as np
import polars as pl
import torch
from research.rl_trading.v6.entry_source import _midnight_us
from research.rl_trading.v6.broker_shards import SCHEMA,validate_rows
from research.rl_trading.v6.execution_features import VERSION, EXECUTION_NAMES,execution_estimates,net_execution_bps


def window_estimates(requests,rows,origin,*,future=False,participation=.1):
    """Unique [ticker,time_us,reference] keys -> one estimate per key.

    Past uses (t-1s,t]; labels use (t,t+1s]. Ten valid volume buckets are
    required to claim complete capacity; unknown buckets remain unavailable.
    Quote age is measured against the window end, never future quote time.
    """
    if requests.select('ticker','time_us').n_unique()!=requests.height:
        raise ValueError('Duplicate execution request')
    if rows.select('ticker','bucket_index').n_unique()!=rows.height:
        raise ValueError('Duplicate certified execution bucket')
    source=rows.with_columns((origin+(pl.col('bucket_index')+1)*100000).alias('bucket_us'))
    if source.filter((pl.col('quote_valid')==1)&(pl.col('quote_timestamp_us')>pl.col('bucket_us'))).height:
        raise ValueError('Future quote inside completed source bucket')
    source=source.with_columns(((pl.col('volume_valid')==1)&(pl.col('quote_valid')==1)&
        (pl.col('bid_int')>0)&(pl.col('ask_int')>=pl.col('bid_int'))&
        (pl.col('bucket_us')-pl.col('quote_timestamp_us')<=1000000)).alias('executable'))
    anchor=requests.with_columns((pl.col('time_us')+(1000000 if future else 0)).alias('window_end'))
    joined=anchor.join_where(source,pl.col('ticker')==pl.col('ticker_right'),
        pl.col('bucket_us')>pl.col('window_end')-1000000,pl.col('bucket_us')<=pl.col('window_end'))
    if joined.height:
        joined=joined.sort('ticker','time_us','bucket_us')
        grouped=joined.group_by('ticker','time_us').agg(
            pl.col('execution_volume').filter(pl.col('executable')).sum().alias('volume'),
            pl.col('execution_notional').filter(pl.col('executable')).sum().alias('notional'),
            (pl.col('execution_volume')*participation).floor().filter(pl.col('executable')).sum().alias('capacity'),
            (pl.col('volume_valid')==1).sum().alias('valid_buckets'),
            pl.col('bid_int').last().alias('bid'),pl.col('ask_int').last().alias('ask'),
            pl.col('quote_timestamp_us').last().alias('quoted_us'),pl.col('quote_valid').last().alias('quote_good'),
            pl.col('bucket_us').last().alias('latest_bucket'))
    else:
        grouped=pl.DataFrame(schema={'ticker':pl.String,'time_us':pl.Int64,'volume':pl.Float64,
            'notional':pl.Float64,'capacity':pl.Float64,'valid_buckets':pl.UInt32,'bid':pl.Int64,'ask':pl.Int64,
            'quoted_us':pl.Int64,'quote_good':pl.Int64,'latest_bucket':pl.Int64})
    frame=anchor.join(grouped,on=['ticker','time_us'],how='left',validate='1:1')
    def tensor(name):return torch.tensor(frame[name].fill_null(0).to_numpy(),dtype=torch.float64)
    reference=tensor('reference');bid=tensor('bid')/10000;ask=tensor('ask')/10000
    volume=tensor('volume');vwap=tensor('notional')/volume.clamp_min(1e-12)
    age=(tensor('window_end')-tensor('quoted_us'))/1e6
    quote=(tensor('quote_good')==1)&(tensor('latest_bucket')==tensor('window_end'))
    available=(tensor('valid_buckets')==10)
    capacity=tensor('capacity')
    features=execution_estimates(reference,bid,ask,vwap,volume,quote,available,age,participation=participation,capacity=capacity)
    valid=(features[:,3]==1)&(features[:,4]==1)
    # Known zero capacity produces zero profit, not a fabricated fill. The
    # quote mid here only keeps an unused price denominator well-defined.
    price=torch.where(volume>0,vwap,(ask+bid)*.5)
    buy=price+(ask-bid)*.5;sell=price-(ask-bid)*.5
    result=frame.select('ticker','time_us').with_columns(
        *(pl.Series(name,features[:,i].numpy()) for i,name in enumerate(EXECUTION_NAMES)),
        pl.Series('buy_price',buy.numpy()),pl.Series('sell_price',sell.numpy()),
        pl.Series('capacity',capacity.numpy()),pl.Series('cost_available',valid.numpy()))
    return result


def score_candidates(candidates,future,*,budget=1000.):
    """Preserve every old candidate and old score; missing costs stay null."""
    entries=future.select('ticker','time_us',pl.col('buy_price').alias('entry_price'),
        pl.col('capacity').alias('entry_capacity'),pl.col('cost_available').alias('entry_available'))
    exits=future.select('ticker',pl.col('time_us').alias('exit_hint_us'),pl.col('sell_price').alias('exit_price'),
        pl.col('capacity').alias('exit_capacity'),pl.col('cost_available').alias('exit_available'))
    result=candidates.join(entries,on=['ticker','time_us'],how='left',validate='m:1').join(
        exits,on=['ticker','exit_hint_us'],how='left',validate='m:1')
    def t(name):return torch.tensor(result[name].fill_null(0).to_numpy(),dtype=torch.float64)
    valid=(t('entry_available')==1)&(t('exit_available')==1)
    net=net_execution_bps(t('entry_price'),t('exit_price'),t('entry_capacity'),t('exit_capacity'),valid,budget=budget)
    return result.with_columns(pl.col('score').alias('old_score'),(pl.col('score')*10000).alias('old_score_bps'),
        pl.Series('netbps',net.numpy()).fill_nan(None),pl.lit(budget).alias('probe_budget'),
        pl.Series('execution_cost_available',valid.numpy()))


def read_requested_windows(source,requests,*,chunk_seconds=60,max_source_rows=5_000_000):
    """Bounded SELECT-only read; only requested tickers and nearby seconds.

    Reuses the pinned source reader/attempt contracts and storage gates.
    Sparse cost outputs replace neither bank nor existing broker caches.
    """
    from research.rl_trading.v1 import arte_sql as sql
    origin=source.origin
    if requests.is_empty():return pl.DataFrame(schema=SCHEMA)
    if set(requests['ticker'])-set(source.attempts):raise ValueError('Unpinned execution identity')
    keys=requests.with_columns(((pl.col('time_us')-origin)//(chunk_seconds*1000000)).alias('chunk'))
    parts=[];row_count=0
    for chunk,group in keys.partition_by('chunk',as_dict=True).items():
        start=int(group['time_us'].min())-1000000;end=int(group['time_us'].max())+1000000
        first=(start-origin)//100000;last=(end-origin)//100000-1
        tickers=sorted(set(group['ticker']))
        for offset in range(0,len(tickers),128):
            names=tickers[offset:offset+128]
            scope=','.join(f'({sql.literal(t)},toUUID({sql.literal(source.attempts[t])}))' for t in names)
            rows=source._frame('SELECT '+','.join(SCHEMA)+' FROM arte.liquidity_100ms_v1 '
                f'WHERE build_id={sql.literal(source.source["build_id"])} AND session_date=toDate({sql.literal(source.day)}) '
                f'AND resolution_ms=100 AND bucket_index BETWEEN {first} AND {last} AND (ticker,attempt_id) IN ({scope}) '
                'ORDER BY bucket_index,ticker',SCHEMA)
            validate_rows(rows,first,last,set(names))
            row_count+=rows.height
            if row_count>max_source_rows:raise ValueError('Sparse cost preparation exceeded explicit source row bound')
            parts.append(rows)
    # Overlapping neighboring source windows must agree exactly, not hide
    # duplicate/conflicting authoritative source records via keep-first.
    rows=pl.concat(parts) if parts else pl.DataFrame(schema=SCHEMA)
    if rows.group_by('ticker','bucket_index').agg(pl.struct(pl.all().exclude('ticker','bucket_index')).n_unique().alias('variants')).filter(pl.col('variants')>1).height:
        raise ValueError('Conflicting overlapping broker reads')
    return rows.unique().sort('bucket_index','ticker')


def build_cost_sidecar(source,candidates,requests,output,*,bank_sha,budget=1000.,participation=.1):
    output=Path(output)
    if output.exists():raise ValueError('Fresh cost sidecar required')
    future_keys=pl.concat((candidates.select('ticker','time_us',pl.col('decision_close').alias('reference')),
        candidates.select('ticker',pl.col('exit_hint_us').alias('time_us'),pl.col('exit_hint_close').alias('reference')))).unique()
    # Exit and entry keys can coincide; current reference is not used in future
    # price arithmetic. Resolve their references deterministically by key.
    future_keys=future_keys.group_by('ticker','time_us').agg(pl.col('reference').first())
    read_keys=pl.concat((requests.select('ticker','time_us'),future_keys.select('ticker','time_us'))).unique()
    rows=read_requested_windows(source,read_keys)
    if not hasattr(source,'luld'):raise ValueError('Modeled LULD evidence required for execution score')
    # Reuse exactly the approximate broker's known pause/band projection.
    # Unknown prior references remain explicit in its existing LULD certificate.
    parts=[]
    for ticker,frame in rows.partition_by('ticker',as_dict=True).items():
        name=ticker[0] if isinstance(ticker,tuple) else ticker
        clocks=source.origin+(frame['bucket_index'].to_numpy()+1)*100000
        item=source.luld.rows.get(name)
        blocked=np.zeros(frame.height,dtype=bool)
        if item is not None:
            changes,states=item;indices=np.searchsorted(changes,clocks,side='right')-1
            known=indices>=0
            if source.luld.end_us is not None:known &= clocks<source.luld.end_us
            volume=frame['execution_volume'].to_numpy();notional=frame['execution_notional'].to_numpy()
            vwap=np.divide(notional,volume,out=np.zeros(frame.height),where=volume>0)
            spread=(frame['ask_int'].to_numpy()-frame['bid_int'].to_numpy())/10000
            pos=indices[known]
            blocked[known]=states['paused'].to_numpy()[pos] | (vwap[known]+spread[known]*.5>states['upper'].to_numpy()[pos]) | (vwap[known]-spread[known]*.5<states['lower'].to_numpy()[pos])
        frame=frame.with_columns(pl.Series('risk_blocked',blocked)).with_columns(
            pl.when(pl.col('risk_blocked')).then(0.).otherwise(pl.col('execution_volume')).alias('execution_volume'),
            pl.when(pl.col('risk_blocked')).then(0.).otherwise(pl.col('execution_notional')).alias('execution_notional')).drop('risk_blocked')
        parts.append(frame)
    rows=pl.concat(parts) if parts else rows
    features=window_estimates(requests,rows,source.origin,participation=participation).select('ticker','time_us',*EXECUTION_NAMES)
    future=window_estimates(future_keys,rows,source.origin,future=True,participation=participation)
    scores=score_candidates(candidates,future,budget=budget)
    # A rolling portfolio cohort cannot be normalized from partially known
    # costs. Preserve classification opportunities, but mask its size targets.
    anchors=scores.select(pl.col('time_us').alias('decision_us')).unique()
    missing=scores.filter(~pl.col('execution_cost_available')).select(pl.col('time_us').alias('missing_us')).unique()
    incomplete=(anchors.join_where(missing,pl.col('missing_us')>=pl.col('decision_us'),
        pl.col('missing_us')<pl.col('decision_us')+15_000_000).select('decision_us').unique()
        if missing.height else anchors.head(0))
    unknown_clocks=incomplete['decision_us'].to_list()
    scores=scores.with_columns((~pl.col('time_us').is_in(unknown_clocks)).alias('allocation_known'))
    from research.rl_trading.v6.episode_windows import rolling_allocation
    eligible=scores.filter(pl.col('netbps').is_not_null()&(pl.col('netbps')>=100)).with_columns(
        (pl.col('netbps')/10000).alias('score'))
    allocation=rolling_allocation(eligible).filter(~pl.col('decision_us').is_in(unknown_clocks))
    allocation=allocation.with_columns((pl.col('allocation_score')*10000).alias('allocation_netbps_score'))
    output.mkdir(parents=True)
    files={}
    for name,frame in (('features',features),('scores',scores),('allocation_netbps',allocation)):
        path=output/(name+'.parquet');frame.write_parquet(path,compression='zstd')
        files[name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':frame.height}
    proof=dict(version=VERSION,status='audited_execution_cost_estimates',day=str(source.day),files=files,
        bank_certificate_sha256=bank_sha,build_id=source.source['build_id'],definition_hash=source.source['definition_hash'],
        source_attempt_hash=hashlib.sha256(json.dumps(source.attempts,sort_keys=True).encode()).hexdigest(),
        input_candidate_sha256=hashlib.sha256(candidates.serialize()).hexdigest(),
        budget=budget,participation=participation,feature_scope='completed_trailing_1s_only',
        score_scope='hindsight_matched_1s_participation_vwap_spread_net_profit_per_probe_capital',
        score_time_discount='none_old_score_retained_with_30s_half_life',
        luld_certificate=source.luld_certificate,
        missing_cost_rows=scores.filter(~pl.col('execution_cost_available')).height,
        incomplete_allocation_clocks=incomplete.height,sealed_test_accessed=False)
    (output/'complete.json').write_text(json.dumps(proof,sort_keys=True));return proof


def attach_teacher_costs(labels,session,root):
    """Attach only causal features; netbps changes size, not opportunity labels."""
    from dataclasses import replace
    proof=json.loads((Path(root)/'complete.json').read_text())
    if (proof['version']!=VERSION or proof['bank_certificate_sha256']!=session.source_certificate_sha256 or
        proof['day']!=str(session.day) or proof['feature_scope']!='completed_trailing_1s_only'):
        raise ValueError('Execution feature/session binding changed')
    tables={}
    for name in ('features','scores','allocation_netbps'):
        path=Path(root)/(name+'.parquet')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=proof['files'][name]['sha256']:
            raise ValueError('Execution sidecar changed')
        tables[name]=pl.read_parquet(path)
    identities=tables['scores'].select('listing_id','ticker').unique()
    if identities['listing_id'].n_unique()!=identities.height:raise ValueError('Ambiguous historical execution identity')
    ticker_by_listing=dict(identities.iter_rows())
    values={(r['ticker'],r['time_us']):np.asarray([r[n] for n in EXECUTION_NAMES],np.float32)
        for r in tables['features'].iter_rows(named=True)}
    unknown_clocks=set(tables['scores'].filter(~pl.col('allocation_known'))['time_us'])
    weights={(r['episode_uid'],r['decision_us']):r['allocation_weight']
        for r in tables['allocation_netbps'].iter_rows(named=True)}
    result=[]
    for item in labels:
        if not item.episode_uid or not item.soft_tokens:raise ValueError('Independent episode supervision required')
        i=int(item.held_index[0]) if len(item.held_index) else item.soft_tokens[1]-1
        ticker=ticker_by_listing[session.listings[i]]
        key=(ticker,item.close_us)
        if key not in values:raise ValueError('Causal execution feature key absent; no fallback')
        size=(weights.get((item.episode_uid,item.close_us),0.)
            if item.size_fraction is not None and item.close_us not in unknown_clocks else None)
        result.append(replace(item,execution_indices=(i,),execution_features=values[key][None].copy(),size_fraction=size))
    return tuple(result)

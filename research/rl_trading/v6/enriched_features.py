"""Vectorized causal feature primitives; no labels, filling or market writers.

Each history uses session-clock buckets. Closed overnight hours are excluded
from that coordinate, while actual timestamps retain overnight gap evidence.
Input scalar values must already be on the target session's split basis.
"""
import numpy as np
import polars as pl

VERSION = 'rl-v6-enriched-causal-inputs-v1'
SCALES = (5, 15, 60)
MULTI_NAMES = ('open_rel', 'high_rel', 'low_rel', 'close_change', 'body',
    'range', 'close_location', 'bar_vwap_rel', 'session_vwap_rel', 'log_volume',
    'log_trades', 'active_second_fraction', 'log_actual_gap', 'session_boundary',
    'price_available', 'extremes_available')
LIQUIDITY_NAMES = tuple(f'{name}_{seconds}s' for seconds in (1,5,15,60)
    for name in ('log_volume','log_dollar_volume','log_trades','active_fraction')) + (
    'log_trade_age','fee_fraction','volatility_15s','volatility_60s',
    'volatility_fee_ratio','volume_5_to_60','trades_5_to_60','activity_available','dollar_volume_available_60s')


def validate(clocks, values):
    if clocks.ndim != 1 or values.ndim != 2 or len(clocks)!=len(values) or values.shape[1]!=37:
        raise ValueError('Ordered scalar source expected')
    if len(clocks)>1 and np.any(np.diff(clocks)<=0): raise ValueError('Unordered/duplicate source clocks')
    if not np.isfinite(values).all(): raise ValueError('Nonfinite source')


def prefix_sum(values):
    return np.r_[0.,np.cumsum(values,dtype=np.float64)]


def liquidity(clocks, scalar, targets, fee=.005):
    """All windows [target-horizon,target), strictly earlier source clocks.

    Rates use elapsed seconds, not observed-row counts. Dollar volume derives
    from certified bar VWAP*volume, with its availability required. No quote
    spread is inferred from candles. Quote fields belong to the separate reader.
    """
    validate(clocks,scalar)
    if fee<0:raise ValueError('Negative fee')
    end=np.searchsorted(clocks,targets,side='left');previous=np.maximum(end-1,0)
    close=np.exp(scalar[:,3]);volume=np.expm1(scalar[:,8]);trades=np.expm1(scalar[:,9])
    vwap=close*(1+scalar[:,4]);dollars=np.where(scalar[:,6]>0,vwap*volume,0.)
    active=(trades>0).astype(float)
    sums=[prefix_sum(x) for x in (volume,dollars,trades,active)]
    columns=[]; totals={}; volatility=[]
    returns=np.r_[0.,np.diff(scalar[:,3])]
    same=(scalar[1:,23:26]==scalar[:-1,23:26]).all(1)&(np.diff(clocks)<3600_000000)
    returns[1:]=np.where(same,returns[1:],0.)
    square=prefix_sum(returns**2)
    for seconds in (1,5,15,60):
        begin=np.searchsorted(clocks,targets-seconds*1_000_000,side='left')
        totals[seconds]=[s[end]-s[begin] for s in sums]
        columns.extend(np.log1p(totals[seconds][i]) if i<3 else totals[seconds][i]/seconds for i in range(4))
        if seconds in (15,60):volatility.append(np.sqrt(np.maximum(0.,square[end]-square[begin])))
    trade_rows=np.flatnonzero(trades>0);trade_clocks=clocks[trade_rows]
    trade_end=np.searchsorted(trade_clocks,targets,side='left')
    age=np.where(trade_end>0,(targets-trade_clocks[np.maximum(trade_end-1,0)])/1e6,0.) if len(trade_clocks) else np.zeros(len(targets))
    price=np.where(end>0,close[previous],1.)
    fee_fraction=2*fee/price
    columns.extend((np.log1p(age),fee_fraction,*volatility,
        volatility[0]/np.maximum(fee_fraction,1e-8),
        (totals[5][0]/5)/(totals[60][0]/60+1),
        (totals[5][2]/5)/(totals[60][2]/60+1),(end>0).astype(float)))
    bad_dollars=prefix_sum((volume>0)&(scalar[:,6]<=0))
    begin=np.searchsorted(clocks,targets-60_000000,side='left')
    columns.append((bad_dollars[end]-bad_dollars[begin]==0).astype(float))
    output=np.column_stack(columns).astype(np.float32)
    output[end==0]=0
    return output


def aggregate(clocks, scalar, session_origin, seconds):
    """Reduce once per listing/scale. Missing bucket IDs remain absent.

    origin is one value per source row, the UTC timestamp of its session 04:00.
    Only source price-valid rows contribute prices; activity retains all rows.
    A 1s source close at bucket endpoint belongs to the bucket ending there.
    """
    validate(clocks,scalar)
    if seconds not in SCALES:raise ValueError('Unsupported timeframe')
    origins=np.asarray(session_origin,dtype=np.int64)
    if origins.shape!=clocks.shape:raise ValueError('Session-origin shape mismatch')
    clock_index=(clocks-origins-1)//(seconds*1_000_000)
    if np.any(clock_index<0) or np.any(clock_index>=57600//seconds):raise ValueError('Candle outside certified session')
    frame=pl.DataFrame(dict(origin=origins,bucket=clock_index,clock=clocks,
        o=np.exp(scalar[:,0]),h=np.exp(scalar[:,1]),l=np.exp(scalar[:,2]),c=np.exp(scalar[:,3]),
        v=np.expm1(scalar[:,8]),n=np.expm1(scalar[:,9]),
        vwap=np.exp(scalar[:,3])*(1+scalar[:,4]),session_vwap_rel=scalar[:,5],
        price=scalar[:,35]>0,extremes=scalar[:,36]>0))
    price=pl.col('price');extrema=price & pl.col('extremes')
    grouped=frame.group_by('origin','bucket',maintain_order=True).agg(
        pl.col('o').filter(price).first(),pl.col('h').filter(extrema).max(),pl.col('l').filter(extrema).min(),
        pl.col('c').filter(price).last(),pl.col('v').sum(),pl.col('n').sum(),
        (pl.col('vwap')*pl.col('v')).sum().alias('notional'),
        pl.col('session_vwap_rel').filter(price).last(),pl.col('clock').last(),
        (pl.col('n')>0).sum().alias('active'),price.any().alias('price'),extrema.any().alias('extremes'))
    return grouped.with_columns((pl.col('origin')+(pl.col('bucket')+1)*seconds*1_000_000).alias('end_us')).sort('end_us')


def timeframe_history(table, targets, prior_origin, current_origin, seconds):
    """120 session-calendar slots, including explicit empty intervals.

    Prior tail supplies context at session start. No source bucket ending at or
    after a target is accepted. Empty slots contain zero values and false mask.
    """
    slots=57600//seconds
    origins=table['origin'].to_numpy();index=table['bucket'].to_numpy()+(origins==current_origin)*slots
    if np.any(~np.isin(origins,[prior_origin,current_origin])):raise ValueError('Unexpected source session')
    current_end=(targets-current_origin-1)//(seconds*1_000_000)-1+slots
    desired=current_end[:,None]-np.arange(119,-1,-1)[None]
    dense=np.zeros((2*slots,len(MULTI_NAMES)),np.float32);valid=np.zeros(2*slots,bool)
    columns={n:table[n].fill_null(0).to_numpy() for n in ('o','h','l','c','v','n','notional','session_vwap_rel','clock','active','price','extremes','end_us')}
    c=columns['c'];previous=np.r_[c[:1],c[:-1]];safe=np.maximum(c,1e-12)
    change=np.where((c>0)&(previous>0),np.log(safe/np.maximum(previous,1e-12)),0.)
    boundary=np.r_[False,np.diff(origins)!=0];change[boundary]=0
    gap=np.r_[0,np.diff(columns['clock'])/1e6]
    values=np.column_stack((columns['o']/safe-1,columns['h']/safe-1,columns['l']/safe-1,change,
        (c-columns['o'])/safe,(columns['h']-columns['l'])/safe,
        np.divide(c-columns['l'],columns['h']-columns['l'],out=np.zeros_like(c),where=columns['h']>columns['l']),
        np.where(columns['v']>0,np.divide(columns['notional'],columns['v']*safe,out=np.ones_like(c),where=columns['v']>0)-1,0.),
        columns['session_vwap_rel'],np.log1p(columns['v']),np.log1p(columns['n']),columns['active']/seconds,
        np.log1p(np.maximum(gap,0)),boundary,columns['price'],columns['extremes']))
    values[columns['price']==0,:9]=0;values[columns['extremes']==0,1:3]=0
    dense[index]=values;valid[index]=True
    positions=np.clip(desired,0,2*slots-1);mask=(desired>=0)&(desired<2*slots)&valid[positions]
    output=dense[positions];output[~mask]=0
    if not np.isfinite(output).all():raise ValueError('Nonfinite aggregated features')
    return output,mask


QUOTE_NAMES=('spread_bps','quote_age_seconds','log_bid_size','log_ask_size','quote_available')


def quote_features(frame, targets):
    """As-of completed 100ms evidence. Stale/invalid quotes never become zero spread."""
    names=('bucket_end_us','quote_timestamp_us','last_event_us','bid_int','ask_int','bid_size','ask_size','quote_valid','event_count')
    if not set(names)<=set(frame.columns):raise ValueError('Certified quote fields missing')
    columns={n:frame[n].to_numpy() for n in names};clocks=columns['bucket_end_us']
    if len(clocks)>1 and np.any(np.diff(clocks)<=0):raise ValueError('Unordered quote buckets')
    output=np.zeros((len(targets),len(QUOTE_NAMES)),np.float32)
    if not len(clocks):return output
    end=np.searchsorted(clocks,targets,side='left');index=np.maximum(0,end-1)
    age=(targets-columns['quote_timestamp_us'][index])/1e6
    bid=columns['bid_int'][index]/10000.;ask=columns['ask_int'][index]/10000.
    event=columns['last_event_us'][index]
    valid=(end>0)&(columns['quote_valid'][index]==1)&(columns['event_count'][index]>0)&(event<clocks[index])&(event>=clocks[index]-100_000)&(columns['quote_timestamp_us'][index]<=event)&(age>=0)&(age<=1)&(bid>0)&(ask>=bid)&(columns['bid_size'][index]>0)&(columns['ask_size'][index]>0)
    output[:,1]=np.where((end>0)&(age>=0),age,0)
    output[valid,0]=(ask[valid]-bid[valid])/((ask[valid]+bid[valid])*.5)*10000
    output[valid,2]=np.log1p(columns['bid_size'][index][valid]);output[valid,3]=np.log1p(columns['ask_size'][index][valid]);output[:,4]=valid
    return output


MARKET_NAMES=tuple(f'{name}_{h}s' for h in (15,60,300) for name in ('return','available')) + (
    'active_fraction_60s','log_dollar_volume_60s','log_trades_60s')


def market_listing(clocks, scalar, targets):
    """Per-listing contributions for explicit universe aggregation, no labels."""
    validate(clocks,scalar);columns=[]
    for seconds in (15,60,300):
        end=np.searchsorted(clocks,targets,side='left')-1
        begin=np.searchsorted(clocks,targets-seconds*1_000_000,side='left')-1
        a=np.maximum(0,end);b=np.maximum(0,begin)
        valid=(end>=0)&(begin>=0)&(targets-clocks[a]<=60_000000)&(targets-seconds*1_000_000-clocks[b]<=60_000000)&(scalar[a,35]>0)&(scalar[b,35]>0)
        columns.extend((np.where(valid,scalar[a,3]-scalar[b,3],0),valid.astype(float)))
    activity=liquidity(clocks,scalar,targets)
    columns.extend((activity[:,15],activity[:,13],activity[:,14]))
    return np.column_stack(columns).astype(np.float32)

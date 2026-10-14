"""Shared QMD completed-clock features; no portfolio or filling inputs."""
import numpy as np
import polars as pl

HORIZONS=(5,15,60,300)
MARKET_HORIZONS=(15,60,300)
SCALES=(5,15,60)
OWN_NAMES=tuple(f'{name}.{h}s' for h in HORIZONS for name in ('return','volatility'))+(
    'activity.volume_acceleration','activity.trade_acceleration','execution.volatility_fee_ratio')+tuple(
    f'candle.{h}s.{name}' for h in SCALES for name in ('body','range','close_location','return','log_volume','log_trades','active_fraction'))
CONTEXT_NAMES=tuple(f'context.{h}s.{name}' for h in MARKET_HORIZONS for name in (
    'median_return','mean_return','advancing_fraction','dispersion','coverage','relative_return','return_percentile'))+(
    'rank.dollar_volume_60s_percentile','rank.dollar_volume_60s_change_15s')
NAMES=OWN_NAMES+CONTEXT_NAMES
VERSION='v18-completed-clock-context-v1'


def returns(seconds,close,valid,targets,horizon,max_age=1):
    """Endpoints <= completed clock; both must be fresh and price-valid."""
    seconds=np.asarray(seconds);targets=np.asarray(targets)
    if len(seconds)==0:return np.full(len(targets),np.nan)
    if np.any(np.diff(seconds)<=0):raise ValueError('Nonunique source clocks')
    end=np.searchsorted(seconds,targets,side='right')-1
    start=np.searchsorted(seconds,targets-horizon,side='right')-1
    a=end.clip(0);b=start.clip(0)
    ok=(end>=0)&(start>=0)&valid[a]&valid[b]&(close[a]>0)&(close[b]>0)&(targets-seconds[a]<=max_age)&(targets-horizon-seconds[b]<=max_age)
    out=np.full(len(targets),np.nan)
    out[ok]=np.log(close[a[ok]]/close[b[ok]])
    return out


def own(frame,targets,origin,fee=.005,minimum_fee=1.,entry_dollars=1000.):
    """Closed bars only, elapsed-time windows, masked missing price evidence."""
    t=frame['second'].to_numpy();targets=np.asarray(targets,dtype=np.int64)
    c=frame['close'].to_numpy();ok=(frame['price_valid'].to_numpy()!=0)&(c>0)
    volumes=frame['volume'].to_numpy();trades=frame['trade_count'].to_numpy()
    end=np.searchsorted(t,targets,side='right');last=(end-1).clip(0)
    columns=[];totals={};vols={}
    step=returns(t,c,ok,t,1);square=np.r_[0.,np.cumsum(np.nan_to_num(step)**2)]
    sums=[np.r_[0.,np.cumsum(x,dtype=np.float64)] for x in (volumes,trades)]
    for h in HORIZONS:
        begin=np.searchsorted(t,targets-h,side='right')
        vol=np.sqrt(np.maximum(0,square[end]-square[begin]))
        # Do not present unobserved price intervals as zero volatility.
        counts=np.r_[0,np.cumsum(np.isfinite(step))]
        vol=np.where((counts[end]-counts[begin])==h,vol,np.nan)
        columns.extend((returns(t,c,ok,targets,h),vol));vols[h]=vol
        totals[h]=[s[end]-s[begin] for s in sums]
    for i in range(2):
        columns.append((totals[5][i]/5)/(totals[60][i]/60+1)-1)
    current=(end>0)&ok[last]&(targets-t[last]<=1)
    quantity=np.floor(entry_dollars/np.maximum(c[last],1e-12))
    roundtrip_fee=2*np.maximum(fee*quantity,minimum_fee)/np.maximum(quantity*c[last],1e-12)
    columns.append(np.where(current&(quantity>0),vols[15]/np.maximum(roundtrip_fee,1e-8),np.nan))
    for h in SCALES:
        bucket=(t-origin-1)//h
        tab=frame.with_columns(pl.Series('bucket',bucket)).group_by('bucket',maintain_order=True).agg(
            pl.col('open').filter(pl.col('price_valid')!=0).first(),
            pl.col('high').filter((pl.col('price_valid')!=0)&(pl.col('extremes_valid')!=0)).max(),
            pl.col('low').filter((pl.col('price_valid')!=0)&(pl.col('extremes_valid')!=0)).min(),
            pl.col('close').filter(pl.col('price_valid')!=0).last(),pl.col('volume').sum(),pl.col('trade_count').sum(),
            (pl.col('trade_count')>0).sum().alias('active'),pl.len().alias('rows'),
            ((pl.col('price_valid')!=0)&(pl.col('extremes_valid')!=0)).sum().alias('extreme_rows'))
        ends=origin+(tab['bucket'].to_numpy()+1)*h
        index=np.searchsorted(ends,targets,side='right')-1;a=index.clip(0)
        o=tab['open'].fill_null(float('nan')).to_numpy()[a];cl=tab['close'].fill_null(float('nan')).to_numpy()[a]
        hi=tab['high'].fill_null(float('nan')).to_numpy()[a];lo=tab['low'].fill_null(float('nan')).to_numpy()[a]
        fresh=(index>=0)&(targets-ends[a]<h)&(cl>0)&(o>0)
        extremes=fresh&(tab['rows'].to_numpy()[a]==h)&(tab['extreme_rows'].to_numpy()[a]==h)
        prior=returns(t,c,ok,ends[a],h)
        columns.extend((np.where(fresh,(cl-o)/cl,np.nan),np.where(extremes,(hi-lo)/cl,np.nan),
            np.where(extremes,np.divide(cl-lo,hi-lo,out=np.full_like(cl,.5),where=hi>lo),np.nan),
            np.where(fresh,prior,np.nan),np.where((index>=0)&(targets-ends[a]<h),np.log1p(tab['volume'].to_numpy()[a]),np.nan),
            np.where((index>=0)&(targets-ends[a]<h),np.log1p(tab['trade_count'].to_numpy()[a]),np.nan),
            np.where((index>=0)&(targets-ends[a]<h),tab['active'].to_numpy()[a]/h,np.nan)))
    return np.column_stack(columns).astype(np.float32)


def market_summary(values,universe_size):
    """Ticker x clock returns; ties receive average percentile, zero is known."""
    from scipy.stats import rankdata
    import warnings
    valid=np.isfinite(values);counts=valid.sum(0);enough=counts>=2
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',RuntimeWarning)
        median=np.nanmedian(values,axis=0);mean=np.nanmean(values,axis=0);dispersion=np.nanstd(values,axis=0)
    advancing=np.divide(((values>0)&valid).sum(0),counts,out=np.full(values.shape[1],np.nan),where=enough)
    common=np.column_stack((median,mean,advancing,dispersion,counts/universe_size))
    common[~enough,:4]=np.nan
    ranks=rankdata(values,axis=0,method='average',nan_policy='omit')
    percentile=np.divide(ranks-1,np.maximum(counts-1,1)[None]);percentile[:,~enough]=np.nan
    return common.astype(np.float32),percentile.astype(np.float32)

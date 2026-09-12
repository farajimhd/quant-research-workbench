"""Shared causal feature construction for training and inference prefixes."""
from datetime import datetime
import numpy as np
import pandas as pd
from .config import CONTRACT


def level_feature_names(common):
    result=list(common)
    for name in ('upper','lower'):
        result.append(name+'_present')
        for attr in ('lower','upper','price'):
            result.extend([name+'_'+attr+'_bps',name+'_'+attr+'_vol'])
        result.extend(name+'_'+attr for attr in ('support_rejections','resistance_rejections','accepted_crossings','sessions','role','weakened','inside'))
        for role in ('support','resistance'):
            result.extend(name+'_'+role+'_'+attr for attr in ('center_bps','count','scale_vol'))
    return result+['gap_bps','gap_position','target_upper']


def feature_rows(inputs,book):
    start=int(datetime.fromisoformat(inputs['source']['start']).timestamp())
    end=int(datetime.fromisoformat(inputs['source']['end']).timestamp())
    if book['available_at']>start:raise ValueError('Future level book unavailable at session start')
    # End-indexed seconds. Absent trades remain missing OHLC, not invented candles.
    grid=np.arange(start+1,end+1);df=pd.DataFrame(inputs['bars']).set_index('t').reindex(grid)
    close=df.close.ffill();observed=df.close.notna()
    age=pd.Series(np.where(observed,grid,np.nan),index=grid).ffill()
    age=pd.Series(grid,index=grid)-age
    volume=df.volume.fillna(0)
    tr=pd.concat([df.high-df.low,(df.high-close.shift()).abs(),(df.low-close.shift()).abs()],axis=1).max(axis=1)
    ranges=tr.rolling(60,min_periods=5).mean()
    tick=np.where(close>=1,.01,.0001)
    scale=np.maximum(ranges.to_numpy(),tick)
    common=pd.DataFrame(index=grid)
    common['price']=close;common['price_age']=age
    common['range_60_bps']=scale/close*10000
    common['second_range_bps']=(df.high-df.low)/close*10000
    common['second_body_bps']=(df.close-df.open)/close*10000
    for window in (5,30,60,300,1800):
        common[f'return_{window}_bps']=(close/close.shift(window)-1)*10000
        common[f'position_{window}']=(close-df.low.rolling(window,min_periods=1).min())/np.maximum(tick,df.high.rolling(window,min_periods=1).max()-df.low.rolling(window,min_periods=1).min())
    common['volume_ratio']=volume.rolling(5,min_periods=1).mean()/volume.rolling(300,min_periods=1).mean().replace(0,np.nan)
    common['log_volume_60']=np.log1p(volume.rolling(60,min_periods=1).sum())
    common['observed_fraction_60']=observed.rolling(60,min_periods=1).mean()
    elapsed=grid-start
    common['session_seconds']=elapsed
    common['regular_session']=((elapsed>19800)&(elapsed<=43200)).astype(float)
    if inputs['quotes']:
        q=pd.DataFrame(inputs['quotes']).set_index('t').reindex(grid).ffill()
        quote_age=grid-q.sip_us/1e6
        valid=(q.ask>=q.bid)&(q.bid>0)&(q.ask_size>0)&(q.bid_size>0)&(quote_age<=5)
        common['quote_age']=quote_age
        common['spread_bps']=((q.ask-q.bid)/close*10000).where(valid)
        common['size_imbalance']=((q.bid_size-q.ask_size)/(q.bid_size+q.ask_size)).where(valid)
        common['log_bid_size']=np.log1p(q.bid_size).where(valid)
        common['log_ask_size']=np.log1p(q.ask_size).where(valid)
        common['quote_valid']=valid.astype(float)
    else:
        for name in ('quote_age','spread_bps','size_imbalance','log_bid_size','log_ask_size'):common[name]=np.nan
        common['quote_valid']=0.
    levels=book['levels'];n=len(grid);p=close.to_numpy()
    expected=level_feature_names(common.columns)
    # Edge lookup is deterministic even with overlapping bands.
    up=sorted(range(len(levels)),key=lambda i:(levels[i]['upper'],levels[i]['id']))
    down=sorted(range(len(levels)),key=lambda i:(levels[i]['lower'],levels[i]['id']))
    if not levels:
        empty=pd.DataFrame({name:pd.Series(dtype='float32') for name in expected})
        for name in ('t','grid_index','level_index','margin'):empty[name]=pd.Series(dtype='float64')
        for name in ('level_id','other_level_id'):empty[name]=pd.Series(dtype='str')
        return empty,expected,dict(seconds=n,eligible=0,missing_both=n,reason='no_historical_levels'),df
    ui=np.searchsorted([levels[i]['upper'] for i in up],p,side='left')
    di=np.searchsorted([levels[i]['lower'] for i in down],p,side='right')-1
    indexes=[]
    for name,order,idx in [('upper',up,ui),('lower',down,di)]:
        exists=(idx>=0)&(idx<len(order));safe=np.asarray(order)[np.clip(idx,0,len(order)-1)]
        indexes.append(np.where(exists,safe,-1));common[name+'_present']=exists.astype(float)
        for attr in ('lower','upper','price'):
            values=np.array([levels[i][attr] for i in safe]);values=np.where(exists,values,np.nan)
            common[name+'_'+attr+'_bps']=(values/p-1)*10000
            common[name+'_'+attr+'_vol']=(values-p)/scale
        for attr in ('support_rejections','resistance_rejections','accepted_crossings'):
            common[name+'_'+attr]=np.where(exists,np.log1p([levels[i][attr] for i in safe]),np.nan)
        common[name+'_sessions']=np.where(exists,[len(levels[i]['contributions']) for i in safe],np.nan)
        common[name+'_role']=np.where(exists,[{'support':1,'resistance':-1,'transition':0}.get(levels[i]['role_segments'][-1]['role'],0) if levels[i]['role_segments'] else 0 for i in safe],np.nan)
        common[name+'_weakened']=np.where(exists,[levels[i]['strength_status']=='weakened' for i in safe],np.nan)
        common[name+'_inside']=((common[name+'_lower_bps']<=0)&(common[name+'_upper_bps']>=0)).astype(float)
        for role in ('support','resistance'):
            estimates=[levels[i].get('reaction_center',{}).get('roles',{}).get(role,{}) for i in safe]
            c=np.array([e.get('center') if e.get('center') is not None else np.nan for e in estimates])
            common[name+'_'+role+'_center_bps']=np.where(exists,(c/p-1)*10000,np.nan)
            common[name+'_'+role+'_count']=np.where(exists,[e.get('count',0) for e in estimates],np.nan)
            common[name+'_'+role+'_scale_vol']=np.where(exists,[e.get('scale') if e.get('scale') is not None else np.nan for e in estimates],np.nan)/scale
    common['gap_bps']=common.upper_lower_bps-common.lower_upper_bps
    common['gap_position']=-common.lower_upper_bps/common.gap_bps.where(common.gap_bps>0)
    common.replace([np.inf,-np.inf],np.nan,inplace=True)
    features=list(common.columns)+['target_upper']
    if features!=expected:raise ValueError('Level feature schema drift')
    eligible=np.isfinite(p)&(age.to_numpy()<=CONTRACT['price_max_age_seconds'])&(elapsed>=CONTRACT['warmup_seconds'])&np.isfinite(scale)
    rows=[]
    for side,idx in zip((1,0),indexes):
        mask=eligible&(idx>=0);part=common.loc[mask].copy();part['target_upper']=side
        part['t']=grid[mask];part['grid_index']=np.flatnonzero(mask);part['level_index']=idx[mask]
        part['level_id']=[levels[i]['id'] for i in idx[mask]]
        part['other_level_id']=[levels[i]['id'] if i>=0 else '' for i in indexes[1 if side else 0][mask]]
        part['margin']=np.maximum(CONTRACT['min_rejection_ticks']*tick[mask],CONTRACT['rejection_range_multiple']*scale[mask])
        rows.append(part)
    result=pd.concat(rows,ignore_index=True)
    result[features]=result[features].astype('float32')
    return result,features,dict(seconds=n,eligible=int(eligible.sum()),price_stale_or_warmup=int((~eligible).sum()),missing_upper=int((eligible&(indexes[0]<0)).sum()),missing_lower=int((eligible&(indexes[1]<0)).sum())),df

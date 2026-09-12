"""First-event labels with frozen bands/margins and explicit coverage censoring."""
import numpy as np
from .config import CONTRACT


def label_rows(rows,grid,levels):
    m=len(rows);base=rows.grid_index.to_numpy(dtype=int);side=rows.target_upper.to_numpy()==1
    lower=np.array([levels[i]['lower'] for i in rows.level_index]);upper=np.array([levels[i]['upper'] for i in rows.level_index])
    margin=rows.margin.to_numpy();tick=np.where(rows.price.to_numpy()>=1,.01,.0001)
    close=grid.close.to_numpy();high=grid.high.to_numpy();low=grid.low.to_numpy()
    touched=(rows.price.to_numpy()>=lower)&(rows.price.to_numpy()<=upper)
    touch_at=np.where(touched,base,-1);previous_beyond=np.zeros(m,dtype=bool)
    gaps=np.zeros(m,dtype=int);labels=np.full(m,-2,dtype=np.int8);resolved=np.full(m,-1,dtype=int)
    for step in range(1,CONTRACT['horizon_seconds']+1):
        position=base+step;in_range=position<len(close);safe=np.minimum(position,len(close)-1)
        observed=in_range&np.isfinite(close[safe])
        gaps=np.where(observed,0,gaps+1)
        active=labels==-2
        censored=active&((~in_range)|(gaps>CONTRACT['future_max_gap_seconds']))
        labels[censored]=-1
        active=(labels==-2)&observed
        contact=active&np.where(side,high[safe]>=lower,low[safe]<=upper)
        new=contact&(~touched);touch_at[new]=position[new];touched|=contact
        reject=active&touched&np.where(side,close[safe]<lower-margin,close[safe]>upper+margin)
        beyond=active&touched&np.where(side,close[safe]>upper+tick,close[safe]<lower-tick)
        broken=beyond&previous_beyond
        labels[reject]=1;labels[broken]=2;resolved[reject|broken]=position[reject|broken]
        previous_beyond=beyond
    remaining=labels==-2
    labels[remaining]=np.where(touched[remaining],3,0)
    resolved[remaining]=base[remaining]+CONTRACT['horizon_seconds']
    rows=rows.copy();rows['label']=labels
    times=grid.index.to_numpy(dtype='int64')
    rows['label_end']=np.where(resolved>=0,times[np.clip(resolved,0,len(times)-1)],-1)
    rows['touch_time']=np.where(touch_at>=0,times[np.clip(touch_at,0,len(times)-1)],-1)
    return rows

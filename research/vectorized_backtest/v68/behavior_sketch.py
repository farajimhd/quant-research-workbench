"""Exact integer clock/listing sketches without per-candidate NumPy temporaries."""
from itertools import chain
import numpy as np
from numba import njit


@njit(cache=False)
def fill(offsets,entries,exits,listings):
    n=len(offsets)-1;values=np.zeros((n,128),np.float64)
    for i in range(n):
        holding=np.zeros(48,np.int64);difference=np.zeros(49,np.int64)
        for k in range(offsets[i],offsets[i+1]):
            entry=entries[k];exit=exits[k]
            values[i,min(entry//1800,47)]+=1
            values[i,96+listings[k]%32]+=1
            if exit>entry and entry<86400:
                first=entry//1800;last=min((exit-1)//1800,47)
                if first==last:holding[first]+=min(exit,(first+1)*1800)-entry
                else:
                    holding[first]+=(first+1)*1800-entry
                    holding[last]+=min(exit,(last+1)*1800)-last*1800
                    difference[first+1]+=1800;difference[last]-=1800
        current=0
        for b in range(48):
            current+=difference[b];values[i,48+b]=(holding[b]+current)/1800
    return values


def sketch(metrics,n):
    if '_behavior_sketch' in metrics:
        values,active=metrics['_behavior_sketch']
        if values.shape!=(n,131) or active.shape!=(n,):raise ValueError('Cached behavior population differs')
        return values,active
    keys=('episode_entry_clocks','episode_exit_clocks','episode_listings')
    if any(len(metrics[k])!=n for k in keys):raise ValueError('Behavior population differs')
    lengths=np.fromiter(map(len,metrics['episode_entry_clocks']),np.int64,count=n)
    if any(not np.array_equal(lengths,np.fromiter(map(len,metrics[k]),np.int64,count=n)) for k in keys[1:]):raise ValueError('Behavior episode alignment differs')
    offsets=np.empty(n+1,np.int64);offsets[0]=0;np.cumsum(lengths,out=offsets[1:])
    arrays=[np.concatenate(metrics[k]).astype(np.int64,copy=False) if n else np.empty(0,np.int64) for k in ('episode_entry_clocks','episode_exit_clocks','episode_listings')]
    if np.any(arrays[0]<0):raise ValueError('Negative behavior entry clock')
    values=np.empty((n,131),np.float64);values[:,:128]=fill(offsets,*arrays)
    values[:,128]=metrics['closed_positions'];values[:,129]=np.asarray(metrics['capital_dollar_seconds'])/3600000;values[:,130]=np.asarray(metrics['turnover'])/1000
    return np.log1p(values),lengths>0

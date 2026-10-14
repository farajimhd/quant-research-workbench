"""Sequential causal recurrences compiled once; vector states across periods."""
import numpy as np
from dataclasses import dataclass
from datetime import date
from numba import njit
from .qmd_feature_catalog import PERIODS,FAST,SLOW,SIGNAL,SPECS

EMA_PERIODS=np.asarray(sorted(set(PERIODS+FAST+SLOW)),dtype=np.int64)
P=np.asarray(PERIODS,dtype=np.int64)
SPEC=np.asarray([( ('ema','rsi','atr','macd_line','macd_signal','macd_histogram').index(k),
                  int(np.flatnonzero(EMA_PERIODS==p)[0]) if k=='ema' or k.startswith('macd') else PERIODS.index(p),
                  int(np.flatnonzero(EMA_PERIODS==s)[0]) if s else 0,
                  SIGNAL.index(q) if q else 0,int(relative)) for k,p,s,q,relative in SPECS],dtype=np.int64)


@dataclass(frozen=True)
class ContinuousFeatureProjection:
    session_date: date
    buckets: np.ndarray
    values: np.ndarray

    def __post_init__(self):
        if (type(self.session_date) is not date or self.buckets.dtype!=np.int64
                or self.values.dtype!=np.float32 or self.values.shape!=(len(self.buckets),len(SPEC))
                or np.any(np.diff(self.buckets)<=0)):
            raise ValueError('Continuous feature projection differs from its typed source clocks')
        for name in ('buckets','values'):
            value=getattr(self,name).copy();value.setflags(write=False)
            object.__setattr__(self,name,value)


def initial_state():
    return np.full((8,len(EMA_PERIODS)),np.nan),np.zeros(4,dtype=np.int64)


@njit(cache=False,nogil=True)
def calculate(close,high,low,valid,extremes,state,counts,factor,emit):
    """Never reset at day boundaries; invalid bars do not advance recurrences."""
    if factor!=1.:
        for row in range(8):
            for j in range(state.shape[1]):
                if np.isfinite(state[row,j]):state[row,j]*=factor
    out=np.full((int(emit.sum()),len(SPEC)),np.nan,dtype=np.float32);output_row=-1
    for i in range(len(close)):
        if emit[i]:output_row+=1
        if not valid[i] or not np.isfinite(close[i]) or close[i]<=0:continue
        price=close[i];previous=state[7,0];counts[0]+=1
        for j in range(len(EMA_PERIODS)):
            state[0,j]=price if not np.isfinite(state[0,j]) else state[0,j]+2./(EMA_PERIODS[j]+1)*(price-state[0,j])
        if np.isfinite(previous):
            counts[1]+=1;delta=price-previous;gain=max(delta,0.);loss=max(-delta,0.)
            for j in range(len(P)):
                period=P[j]
                if counts[1]<=period:
                    state[1,j]=(0. if not np.isfinite(state[1,j]) else state[1,j])+gain
                    state[2,j]=(0. if not np.isfinite(state[2,j]) else state[2,j])+loss
                    if counts[1]==period:state[1,j]/=period;state[2,j]/=period
                else:
                    state[1,j]+=(gain-state[1,j])/period;state[2,j]+=(loss-state[2,j])/period
        if extremes[i] and np.isfinite(high[i]) and np.isfinite(low[i]) and high[i]>=low[i]:
            counts[2]+=1;tr=high[i]-low[i]
            if np.isfinite(previous):tr=max(tr,abs(high[i]-previous),abs(low[i]-previous))
            for j in range(len(P)):
                period=P[j]
                if counts[2]<=period:
                    state[3,j]=(0. if not np.isfinite(state[3,j]) else state[3,j])+tr
                    if counts[2]==period:state[3,j]/=period
                else:state[3,j]+=(tr-state[3,j])/period
        pair=0
        for f in FAST:
            fi=0
            for j in range(len(EMA_PERIODS)):
                if EMA_PERIODS[j]==f:fi=j
            for slow in SLOW:
                si=0
                for j in range(len(EMA_PERIODS)):
                    if EMA_PERIODS[j]==slow:si=j
                line=state[0,fi]-state[0,si]
                for q in range(len(SIGNAL)):
                    row=4+q
                    state[row,pair]=line if not np.isfinite(state[row,pair]) else state[row,pair]+2./(SIGNAL[q]+1)*(line-state[row,pair])
                pair+=1
        state[7,0]=price
        if not emit[i]:continue
        for c in range(len(SPEC)):
            kind,a,b,q,relative=SPEC[c];value=np.nan
            if kind==0 and counts[0]>=EMA_PERIODS[a]:value=state[0,a]
            elif kind==1 and counts[1]>=P[a]:
                gain=state[1,a];loss=state[2,a];value=.5 if gain+loss==0 else gain/(gain+loss)
            elif kind==2 and counts[2]>=P[a] and extremes[i]:value=state[3,a]
            elif kind>=3 and counts[0]>=EMA_PERIODS[b]+(0 if kind==3 else SIGNAL[q]-1):
                line=state[0,a]-state[0,b];pair=0
                for fi in range(len(FAST)):
                    for si in range(len(SLOW)):
                        if FAST[fi]==EMA_PERIODS[a] and SLOW[si]==EMA_PERIODS[b]:pair=fi*len(SLOW)+si
                value=line if kind==3 else state[4+q,pair] if kind==4 else line-state[4+q,pair]
            if relative:value=value/price-(1. if kind==0 else 0.)
            out[output_row,c]=value
    return out,state,counts

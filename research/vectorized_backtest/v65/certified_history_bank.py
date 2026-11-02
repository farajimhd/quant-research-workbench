"""Causal market-only broker history, independent of strategies and accounts."""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

VERSION = 'v6-causal-broker-history-v1'
SWING_WINDOWS = (1,2,3,4,5,10,15,20,30,45,60)
WINDOWS = tuple(range(1,61))
ADAPTIVE = tuple(range(2,33))
LAYOUT = dict(recent_high=(0,60),momentum_close=(60,120),attention_mean=(120,180),
              average_move=(180,211),swing_low=(211,332))


def trailing(values, width, *, current=False, maximum=False, targets=None):
    """Elapsed-clock window; NaN gaps invalidate prices, zero fills activity."""
    offset = width-1 if current else width
    padded = np.concatenate((np.full(offset,np.nan),values))
    rows = sliding_window_view(padded,width)[:len(values)]
    if targets is not None:rows=rows[targets]
    return np.max(rows,axis=1) if maximum else np.sum(rows[:,::-1],axis=1)/width


def calculate(mark, high, low, observed, notional, targets=None):
    n=len(mark)
    if any(np.asarray(v).shape!=(n,) for v in (high,low,observed,notional)):
        raise ValueError('Ticker history shape mismatch')
    mark=np.where(observed,mark,np.nan).astype(np.float64)
    high=np.where(observed,high,np.nan).astype(np.float64)
    low=np.where(observed,low,np.nan).astype(np.float64)
    notional=np.asarray(notional,dtype=np.float64)
    if not np.isfinite(notional).all() or (notional<0).any():raise ValueError('Invalid activity history')
    targets=np.arange(n) if targets is None else np.asarray(targets,dtype=np.int64)
    if ((targets<0)|(targets>=n)).any():raise ValueError('History target outside source')
    result=np.full((len(targets),332),np.nan,dtype=np.float64)
    for w in WINDOWS:
        result[:,w-1]=trailing(high,w,maximum=True,targets=targets)
        prior=targets-w
        result[:,60+w-1]=np.where(prior>=0,mark[prior.clip(0)],np.nan)
        result[:,120+w-1]=trailing(notional,w,targets=targets)
    movement=np.abs(mark-np.concatenate(([np.nan],mark[:-1])))
    for i,w in enumerate(ADAPTIVE):result[:,180+i]=trailing(movement,w,current=True,targets=targets)
    # Many left/right pairs have the same total width. Their finite-window
    # test and minimum are identical; calculate those reductions once.
    extrema={}
    for i,left in enumerate(SWING_WINDOWS):
        for j,right in enumerate(SWING_WINDOWS):
            width=left+right+1
            if width not in extrema:
                padded=np.concatenate((np.full(width-1,np.nan),low))
                rows=sliding_window_view(padded,width)
                extrema[width]=(np.isfinite(rows).all(1),np.min(rows,axis=1))
            valid,minimum=extrema[width]
            pivot=np.full(n,np.nan)
            if right<n:pivot[right:]=low[:n-right]
            confirmed=valid&(pivot==minimum)
            last=np.maximum.accumulate(np.where(confirmed,np.arange(n),-1))
            # Decisions see only confirmation state through the previous clock.
            prior=np.concatenate(([-1],last[:-1]))
            result[:,211+i*len(SWING_WINDOWS)+j]=np.where(prior>=0,pivot[prior.clip(0)],np.nan)[targets]
    return result


def compress(values):
    """Lossless run-length rows; no quantization or loss of NaN validity."""
    if values.ndim!=2 or not len(values):raise ValueError('Empty history bank')
    same=values[1:].view(np.uint64)==values[:-1].view(np.uint64)
    changed=np.concatenate(([True],~same.all(1)))
    return values[changed],(np.cumsum(changed,dtype=np.int64)-1).astype(np.int32)

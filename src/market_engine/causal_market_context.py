"""One bounded full-census context preparation shared by all listing rules."""
import numpy as np
import polars as pl
from .causal_context_features import MARKET_HORIZONS, CONTEXT_NAMES, returns, market_summary


def prepare_market_context(frames, *, universe, unavailable, first_second, stop_second,
                           maximum_bytes, targets=None):
    """Missing certified units remain NaN, retaining the preopen denominator.

    Scratch is admitted before allocation. Each source is reduced once; the
    256-second cross-section tiles bound sorting/reduction temporaries.
    """
    if (type(universe) is not tuple or not universe or tuple(sorted(set(universe))) != universe
            or type(unavailable) is not tuple or tuple(sorted(set(unavailable))) != unavailable
            or set(frames) != set(universe)-set(unavailable) or set(unavailable)-set(universe)
            or type(first_second) is not int or type(stop_second) is not int
            or not 0 < stop_second-first_second <= 57600
            or type(maximum_bytes) is not int or maximum_bytes < 1):
        raise ValueError('Market context requires its exact pinned census and explicit memory budget')
    width=stop_second-first_second
    targets=tuple(sorted(frames)) if targets is None else targets
    if type(targets) is not tuple or not targets or tuple(sorted(set(targets)))!=targets or set(targets)-set(frames):
        raise ValueError('Market context targets must be explicit available listings')
    # Four source planes, rank plane, and conservative bounded sort workspace.
    needed=(5*len(universe)*width+12*len(universe)*min(width,256)
            +2*len(CONTEXT_NAMES)*sum(frames[t].height for t in targets))*4
    if needed>maximum_bytes:
        raise MemoryError('Full-universe market context exceeds its admitted byte budget')
    dense=np.full((4,len(universe),width),np.nan,np.float32)
    for i,ticker in enumerate(universe):
        if ticker in unavailable:continue
        frame=frames[ticker]
        t=frame['second'].to_numpy();c=frame['close'].to_numpy()
        if (np.any(np.diff(t)<=0) or not np.isfinite(frame['notional'].to_numpy()).all()
                or np.any(frame['notional'].to_numpy()<0)):
            raise ValueError('Context source contains duplicate clocks or invalid activity')
        positions=t-first_second;emit=(positions>=0)&(positions<width)
        valid=(frame['price_valid'].to_numpy()!=0)&(c>0)
        for j,h in enumerate(MARKET_HORIZONS):
            dense[j,i,positions[emit]]=returns(t,c,valid,t,h)[emit]
        sums=np.r_[0.,np.cumsum(frame['notional'].to_numpy(),dtype=np.float64)]
        begin=np.searchsorted(t,t-60,side='right')
        dense[3,i,positions[emit]]=(sums[np.arange(1,len(t)+1)]-sums[begin])[emit]
    rank=np.full((len(universe),width),np.nan,np.float32)
    # Only common per-clock statistics survive each tile. Listing-specific
    # relative returns/percentiles are emitted for its actual source clocks.
    result={t:np.full((frames[t].height,len(CONTEXT_NAMES)),np.nan,np.float32) for t in targets}
    for begin in range(0,width,256):
        end=min(begin+256,width)
        summaries=[market_summary(dense[j,:,begin:end],len(universe)) for j in range(4)]
        rank[:,begin:end]=summaries[3][1]
        for i,ticker in enumerate(universe):
            if ticker not in result:continue
            clocks=frames[ticker]['second'].to_numpy()-first_second
            selected=np.flatnonzero((clocks>=begin)&(clocks<end));at=clocks[selected]-begin
            for j in range(3):
                common,pct=summaries[j]
                result[ticker][selected,j*7:j*7+5]=common[at]
                result[ticker][selected,j*7+5]=dense[j,i,clocks[selected]]-common[at,0]
                result[ticker][selected,j*7+6]=pct[i,at]
    for i,ticker in enumerate(universe):
        if ticker not in result:continue
        clocks=frames[ticker]['second'].to_numpy()-first_second
        selected=np.flatnonzero((clocks>=0)&(clocks<width));at=clocks[selected]
        result[ticker][selected,-2]=rank[i,at]
        result[ticker][selected,-1]=np.where(at>=15,rank[i,at]-rank[i,(at-15).clip(0)],np.nan)
    return {ticker:pl.DataFrame({'second':frames[ticker]['second'],
        **{name:pl.Series(values[:,i]) for i,name in enumerate(CONTEXT_NAMES)}})
        for ticker,values in result.items()}

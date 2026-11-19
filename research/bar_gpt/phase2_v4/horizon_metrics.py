"""Per-future-bar reductions on the existing CPU validation buffers only."""
import math
import numpy as np
from numba import njit
from ..phase1_v10.metrics import PriceRelative
from ..v5.shards.phase1_contract import CHANNELS


@njit(nogil=True, cache=False, error_model='numpy')
def reduce_horizons(errors, targets, valid, frames, sums, counts, bad, excluded, hist,
                    h_sums, h_counts, relative, eligible, h_bad, h_excluded, tiny):
    for row in range(len(errors)):
        frame=frames[row]
        for channel in range(18):
            for bar in range(64):
                if not valid[row,channel,bar]:continue
                actual=targets[row,channel,bar];error=errors[row,channel,bar]
                h_counts[bar,channel]+=1
                h_sums[bar,channel]+=error
                if channel%6>=4:continue
                head=channel//6*4+channel%6
                if not math.isfinite(actual) or actual<=0:
                    h_excluded[bar,channel]+=1;excluded[frame,head]+=1;continue
                eligible[bar,channel]+=1
                if actual<=1e-12:tiny[bar,channel]+=1
                band=1 if actual<.1 else 2 if actual<1 else 3 if actual<5 else 4 if actual<20 else 5 if actual<100 else 6
                counts[frame,head,band]+=1
                value=100.*error/actual
                if not math.isfinite(value):
                    h_bad[bar,channel]+=1;bad[frame,head,band]+=1;continue
                relative[bar,channel]+=value;sums[frame,head,band]+=value
                if value==0:bucket=0
                else:
                    scaled=min(8192.,max(-1.,(math.log10(value)+9)*(8192/18)))
                    bucket=int(math.floor(scaled))+2
                hist[frame,head,band,bucket]+=1


class HorizonPriceRelative(PriceRelative):
    """Each native reducer lane owns its summaries; merge after workers finish."""
    FIELDS=('h_sums','h_counts','h_relative','h_eligible','h_bad','h_excluded','h_tiny')
    def __init__(self):
        super().__init__()
        for name in self.FIELDS:
            setattr(self,name,np.zeros((64,18),dtype=np.float64 if name in ('h_sums','h_relative') else np.int64))
    def add(self,errors,targets,valid,frames):
        # Native.add already transferred these buffers; .numpy() is a CPU view.
        e,y,v,f=(x.numpy() for x in (errors,targets,valid,frames))
        if (e.ndim!=3 or e.shape[1:]!=(18,64) or y.shape!=e.shape or v.shape!=e.shape
                or v.dtype!=np.bool_ or f.shape!=(len(e),) or f.dtype.kind not in 'iu'
                or np.any(f<0) or np.any(f>=6)):raise ValueError('Invalid native horizon buffers')
        reduce_horizons(e,y,v,f,self.sums,self.counts,self.bad,self.excluded,self.hist,
                        *(getattr(self,name) for name in self.FIELDS))
        for array in (self.sums,self.counts,self.bad,self.hist):array[:,:,0]=array[:,:,1:].sum(axis=2)
    def merge(self,other):
        super().merge(other)
        for name in self.FIELDS:getattr(self,name)[:]+=getattr(other,name)
    def metrics(self,*args):
        result=super().metrics(*args)
        for bar in range(64):
            for c,name in enumerate(CHANNELS):
                stem=f'horizon/bar_{bar+1}'
                n=self.h_counts[bar,c];total=self.h_sums[bar,c]
                result[f'{stem}/raw_mae/{name}']=float(total/n) if n and np.isfinite(total) else None
                if c%6<4:
                    n=self.h_eligible[bar,c];total=self.h_relative[bar,c]
                    result[f'{stem}/price_mape_pct/{name}']=float(total/n) if n and not self.h_bad[bar,c] and np.isfinite(total) else None
        # Full denominator audit retained as small arrays, not thousands of charts.
        result['horizon_counts']={name.removeprefix('h_'):getattr(self,name).tolist()
            for name in ('h_counts','h_eligible','h_bad','h_excluded','h_tiny')}
        return result

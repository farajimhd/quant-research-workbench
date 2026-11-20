"""CPU-only next-candle labels; commit counts with consumed optimizer batches."""
import numpy as np
from .move_contract import BINS,CHANNELS,EDGES_PCT

TAG='_loader_class_tags'


def label_pool(values,valid,year,liquid):
    """Use existing reader arrays; never open files or transfer GPU tensors."""
    if not 2019<=year<=2026 or liquid not in (0,1):raise ValueError('Invalid census strata')
    x=np.asarray(values)[:,[3,9,15]].astype(np.float64)
    v=np.asarray(valid)[:,[3,9,15]].astype(bool)
    tags=np.full((len(x),5),-1,dtype=np.int8)
    tags[:,3]=year-2019;tags[:,4]=liquid
    mask=v[1:]&v[:-1]&(x[1:]>0)&(x[:-1]>0)
    if not np.isfinite(x[v]).all() or (x[v]<=0).any():raise ValueError('Invalid positive census source')
    r=100*(x[1:]/np.where(mask,x[:-1],1)-1)
    b=np.where(r==0,0,1+np.searchsorted(EDGES_PCT,np.abs(r),side='left')+5*(r>0))
    tags[:-1,:3]=np.where(mask,b,-1)
    return tags


class LoaderCensus:
    VERSION='phase2-v4-loader-census-v1'
    def __init__(self,step,epoch,cursor,state=None):
        self.counts=np.zeros((8,2,3,12),dtype=np.int64)
        self.origins=0;self.start_step=self.last_step=int(step)
        self.start_epoch=self.last_epoch=int(epoch)
        self.start_cursor=self.last_cursor=list(cursor)
        if state is not None:
            if state['version']!=self.VERSION or state['last_step']!=step:raise ValueError('Census/checkpoint step mismatch')
            self.counts=np.array(state['counts'],dtype=np.int64)
            if self.counts.shape!=(8,2,3,12) or (self.counts<0).any():raise ValueError('Invalid census counts')
            for key in ('origins','start_step','last_step','start_epoch','last_epoch','start_cursor','last_cursor'):
                setattr(self,key,state[key])
            if self.last_epoch!=epoch or self.last_cursor!=list(cursor):raise ValueError('Census/checkpoint traversal mismatch')
            if not np.all(self.counts.sum((0,1,3))==self.origins):raise ValueError('Census denominators disagree')

    def commit(self,tags,step,epoch,cursor):
        if step!=self.last_step+1:raise ValueError('Duplicate or skipped census update')
        if tags.dtype!=np.int8 or tags.ndim!=2 or tags.shape[1]!=5:raise ValueError('Malformed CPU census tags')
        if (tags[:,:3]<-1).any() or (tags[:,:3]>10).any() or (tags[:,3]<0).any() or (tags[:,3]>6).any() or not np.isin(tags[:,4],(0,1)).all():
            raise ValueError('Invalid training class or year')
        classes=np.where(tags[:,:3]<0,11,tags[:,:3]).astype(np.int64)
        indices=(((tags[:,3,None].astype(np.int64)*2+tags[:,4,None])*3+np.arange(3))*12+classes)
        self.counts+=np.bincount(indices.ravel(),minlength=self.counts.size).reshape(self.counts.shape)
        self.origins+=len(tags);self.last_step=int(step);self.last_epoch=int(epoch);self.last_cursor=list(cursor)

    def state(self):
        return dict(version=self.VERSION,counts=self.counts.tolist(),origins=self.origins,
            start_step=self.start_step,last_step=self.last_step,start_epoch=self.start_epoch,last_epoch=self.last_epoch,
            start_cursor=self.start_cursor,last_cursor=self.last_cursor,
            scope='Consumed training exposure since enablement; prior prefix not covered; independent census not merged')

    def metrics(self):
        out={'training_census/origins':self.origins,'training_census/start_step':self.start_step}
        for c,name in enumerate(CHANNELS):
            counts=self.counts[:,:,c].sum((0,1));total=int(counts[:11].sum())
            out[f'training_census/{name}/invalid']=int(counts[11])
            for b,label in enumerate(BINS):
                out[f'training_census/{name}/{label}/count']=int(counts[b])
                out[f'training_census/{name}/{label}/pct']=float(100*counts[b]/total) if total else None
        return out

"""Same raw metrics with frame constants initialized before graph capture."""
import torch
from ..phase0_v1.metrics import Metrics as BaseMetrics,scalars
from ..phase0_v1.model import objective
from ..phase1_v11.transforms import inverse
from ..v5.shards.phase1_contract import FRAMES

class Metrics(BaseMetrics):
    def __init__(self,device='cuda'):
        super().__init__(device)
        self.frame_codes=torch.tensor(FRAMES,device=device)

    @torch.no_grad()
    def add(self,model,out,x,v,b):
        loss,parts=objective(model,out,x,v);self.loss+=torch.cat((loss[None],parts)).double()*len(x);self.windows+=len(x)
        raw=b.grid.double();pred=inverse(out['logs']);valid=v[...,:18]
        self.nonfinite+=(~torch.isfinite(pred)).sum()
        frame=(b.frames[:,None]==self.frame_codes).long().argmax(1)
        group=torch.nn.functional.one_hot(frame,6).T.double();err=pred-raw;nonzero=valid&(raw!=0)
        relative=100*err/torch.where(nonzero,raw,1)
        for i,z in enumerate((valid.double(),torch.where(valid,err.abs(),0),torch.where(valid,err,0),
                              torch.where(nonzero,relative.abs(),0),torch.where(nonzero,relative,0),nonzero.double())):
            self.raw[:,:,i]+=group@z.sum(1)
        y=raw.reshape(-1,64,3,6);p=pred.reshape_as(y);m=valid.reshape_as(y)
        o,h,l,c=y[...,:4].unbind(-1);po,ph,pl,pc=p[...,:4].unbind(-1)
        def ratio(a,b):return 100*(a/torch.where(b>0,b,1)-1)
        self.moves.moves('candle',ratio(c,o),ratio(pc,po),m[...,0]&m[...,3],frame)
        self.moves.moves('consecutive_close',ratio(c[:,1:],c[:,:-1]),ratio(pc[:,1:],pc[:,:-1]),m[:,1:,:,3]&m[:,:-1,:,3],frame)
        self.moves.moves('range',ratio(h,l),ratio(ph,pl),m[...,1]&m[...,2],frame)


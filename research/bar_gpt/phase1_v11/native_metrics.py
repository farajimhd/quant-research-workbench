"""V11 native reducer with cached device constants; unchanged V10 metric formulas."""
import torch
from ..phase1_v10.train import Accumulator as BaseAccumulator,group_sum
from ..phase1_v10.model import inverse


class NativeAccumulator(BaseAccumulator):
    def add(self,model,output,values,valid,hidden,b):
        p,t,vl=output;raw=inverse(p);truth=b.raw.double();v=valid[...,:18]&hidden[...,None]
        error=(raw-truth).abs();loge=(p.double()-values[...,:18].double()).abs()
        self.bad[0]+=(~torch.isfinite(raw)&v).sum();self.bad[1]+=((raw<0)&v).sum()
        for base in (0,6,12):
            o,h,l,c=raw[...,base:base+4].unbind(-1)
            self.bad[2]+=(((h<torch.maximum(o,c))|(l>torch.minimum(o,c)))&v[...,base:base+4].all(-1)).sum()
        self.bad[2]+=((raw[...,6:10]>raw[...,12:16])&v[...,6:10]&v[...,12:16]).sum()
        # Creating torch.tensor(FRAMES,device='cuda') here synchronizes every batch.
        fi=(b.frames[:,None]==self.frame_values).long().argmax(1)
        groups=torch.nn.functional.one_hot(fi,6).T.double()
        for dest,source in ((self.sums,error),(self.logs,loge),(self.ps,raw),(self.ys,truth),(self.p2,raw.square()),(self.y2,truth.square())):
            dest+=group_sum(source,v,groups)
        self.counts+=groups@v.sum(1).double()
        self.zero+=groups@(v&(truth==0)).sum(1).double()
        self.tiny+=groups@(v&(truth>0)&(truth<=1e-12)).sum(1).double()
        tv=valid[...,18:]&hidden[...,None];te=(t-(values[...,18:]-model.input_mean[18:])/model.input_scale[18:]).abs()
        self.time_sum+=torch.where(tv,te,0).sum((0,1));self.time_count+=tv.sum((0,1))
        vv=vl>0;eligible=hidden[...,None]
        self.confusion+=torch.stack(((vv&valid&eligible).sum(),(vv&~valid&eligible).sum(),(~vv&valid&eligible).sum(),(~vv&~valid&eligible).sum()))
        lane=self.batches%2
        if self.pending[lane] is not None:self.pending[lane].result()
        cpu=[x.cpu() for x in (error.transpose(1,2),truth.transpose(1,2),v.transpose(1,2),fi)]
        self.pending[lane]=self.pool.submit(self.reducers[lane].add,*cpu);self.batches+=1

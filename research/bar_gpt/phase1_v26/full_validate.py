"""Streaming all-population metrics, with no retained prediction arrays."""
import torch
from ..phase1_v21.validate import Accumulator as BaseAccumulator,Replay
from ..phase1_v21.raw_metrics import Metrics
from ..phase1_v17.metrics import ActivityMetrics
from ..phase1_v7.full_data import gpu_inputs
from ..phase1_v12.model import positions


class Accumulator(BaseAccumulator):
    def __init__(self,device='cuda'):
        self.raw={k:Metrics(device) for k in ('hidden48','hidden8','all_visible')}
        self.activity={k:ActivityMetrics(device) for k in self.raw}
    @torch.no_grad()
    def add(self,model,batch,pos):
        x,v,_=gpu_inputs(batch)
        for name,p in (('hidden48',pos[:,:16].sort(1).values),('hidden8',pos[:,:56].sort(1).values),('all_visible',positions(len(x),x.device,64))):
            out,_=model(x,v,p)
            mask=v if name=='all_visible' else v&(~torch.zeros_like(v[...,0]).scatter(1,p,True))[...,None]
            self.raw[name].add(model,out,x,mask,batch);self.activity[name].add(out['logs'],batch.grid,mask,batch.frames)

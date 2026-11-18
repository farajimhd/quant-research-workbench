"""Random16 MAE backward with optional CUDA replay; optimizer remains outside graph."""
import torch
from ..phase1_v10.model import select
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import rng_state,restore_rng
from .model import objective


def backward(model,values,valid):
    x,v,p,h=select(values,valid,16)
    out=model(x,v,p);loss,parts=objective(model,out,values,valid,h,x,v)
    loss.backward();norm=stable_clip(model.parameters())
    return loss.detach(),norm,torch.stack([v.detach() for v in parts.values()])


class Replay:
    def __init__(self,model,values,valid):
        self.values=values.clone();self.valid=valid.clone();state=rng_state()
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        try:
            with torch.cuda.stream(stream):
                for _ in range(3):model.zero_grad(set_to_none=True);backward(model,self.values,self.valid)
            torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
            with torch.cuda.graph(self.graph,stream=stream):self.output=backward(model,self.values,self.valid)
            self.params=list(model.parameters());self.grads=[p.grad for p in self.params]
        finally:restore_rng(state)

    def __call__(self,values,valid):
        self.values.copy_(values);self.valid.copy_(valid)
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.output

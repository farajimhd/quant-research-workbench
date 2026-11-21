import torch
from .data import prepare
from .model import objective
from ..phase1_v10.step import stable_clip


def backward(model,teacher,b,pos):
    latent,meta,valid,_,_=prepare(teacher,b)
    prediction=model(latent,meta,pos)
    loss,parts=objective(model,prediction,latent,valid,pos)
    loss.backward();norm=stable_clip(model.parameters())
    return loss.detach(),parts.detach(),norm


def copy_batch(dest,source):
    copied=set()
    for name in ('grid','slots','valid','frames'):
        t=getattr(dest,name)
        if t.data_ptr() not in copied:t.copy_(getattr(source,name),non_blocking=True);copied.add(t.data_ptr())


class Replay:
    def __init__(self,model,teacher,b,pos):
        self.batch=b.map(lambda x:x.clone());self.pos=pos.clone();self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):model.zero_grad(set_to_none=True);backward(model,teacher,self.batch,self.pos)
        torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph,stream=stream):self.output=backward(model,teacher,self.batch,self.pos)
        self.params=list(model.parameters());self.grads=[p.grad for p in self.params]
    def __call__(self,b,pos):
        copy_batch(self.batch,b);self.pos.copy_(pos)
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.output


class InferenceReplay:
    @torch.no_grad()
    def __init__(self,model,teacher,b,pos):
        self.batch=b.map(lambda x:x.clone());self.pos=pos.clone();self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        def forward():
            z,meta,valid,x,v=prepare(teacher,self.batch)
            allpos=torch.arange(64,device=z.device).expand(len(z),-1)
            return z,meta,valid,x,v,model(z,meta,self.pos),model(z,meta,allpos)
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):forward()
        torch.cuda.current_stream().wait_stream(stream)
        with torch.cuda.graph(self.graph,stream=stream):self.output=forward()
    @torch.no_grad()
    def __call__(self,b,pos):
        copy_batch(self.batch,b);self.pos.copy_(pos);self.graph.replay();return self.output

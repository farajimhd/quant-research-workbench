"""Capture raw preparation, visible-only anchor teacher and V26 backward together."""
import torch
from .step import backward as prepared_backward
from ..phase1_v7.full_data import gpu_inputs
from ..phase1_v12.model import gather


def inputs(teacher,batch,pp,ap):
    with torch.no_grad():
        x,v,_=gpu_inputs(batch);z=teacher.encode(gather(x,pp),gather(v,pp))
        anchor=z.new_zeros(len(x),64,z.shape[-1]).scatter(1,pp[...,None].expand_as(z),z)
    return x,v,batch.grid,batch.frames,anchor,pp,ap


def backward(model,teacher,batch,pp,ap):return prepared_backward(model,inputs(teacher,batch,pp,ap))


class Replay:
    def __init__(self,model,teacher,batch,pp,ap):
        self.batch=batch.map(lambda t:t.clone());self.pp=pp.clone();self.ap=ap.clone();self.graph=torch.cuda.CUDAGraph()
        stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):model.zero_grad(set_to_none=True);backward(model,teacher,self.batch,self.pp,self.ap)
        torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph,stream=stream):self.output=backward(model,teacher,self.batch,self.pp,self.ap)
        self.params=list(model.parameters());self.grads=[p.grad for p in self.params]
    def __call__(self,batch,pp,ap):
        for name in ('grid','slots','valid','frames'):getattr(self.batch,name).copy_(getattr(batch,name))
        self.pp.copy_(pp);self.ap.copy_(ap)
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.output


def qualify(model,teacher,batch,pp,ap):
    model.zero_grad(set_to_none=True);expected=tuple(t.clone() for t in backward(model,teacher,batch,pp,ap))
    grads=[None if p.grad is None else p.grad.clone() for p in model.parameters()]
    b=list(inputs(teacher,batch,pp,ap))
    with torch.no_grad():b[4]=teacher.encode(b[0],b[1])
    model.zero_grad(set_to_none=True);original=prepared_backward(model,b)
    torch.testing.assert_close(original[0],expected[0],rtol=2e-4,atol=2e-4)
    replay=Replay(model,teacher,batch,pp,ap);actual=replay(batch,pp,ap)
    torch.testing.assert_close(actual[0],expected[0],rtol=2e-4,atol=2e-4)
    for p,g in zip(model.parameters(),grads):
        if g is not None:torch.testing.assert_close(p.grad,g,rtol=3e-3,atol=2e-5)
    return replay,actual

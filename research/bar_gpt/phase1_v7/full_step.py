"""FP32 export/readout and measured optional BF16 backbone with CUDA replay."""
from dataclasses import fields
import torch
from .full_data import Batch
from .model import objective


def forward(model,b,precision):
    if b.grid.ndim==3:
        grid,slots=model.prepare(b.grid,b.slots,None,check=False)
    else:grid,slots=b.grid,b.slots
    z=model.encode_prepared(grid,slots,precision)
    return model.decode(z)


def backward(model,b,precision):
    p=forward(model,b,precision)
    value,channels=objective(p,b.target,b.valid,None)
    value.backward()
    norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=False)
    return value.detach(),channels.detach(),norm


class Replay:
    def __init__(self,model,b,precision):
        self.batch=Batch(**{f.name:getattr(b,f.name).clone() for f in fields(b)})
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):
                model.zero_grad(set_to_none=True);backward(model,self.batch,precision)
        torch.cuda.current_stream().wait_stream(stream)
        model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph,stream=stream):
            self.outputs=backward(model,self.batch,precision)
        self.params=list(model.parameters());self.grads=[p.grad for p in self.params]

    def __call__(self,b):
        for name in ('grid','slots','target','valid'):
            getattr(self.batch,name).copy_(getattr(b,name),non_blocking=True)
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.outputs

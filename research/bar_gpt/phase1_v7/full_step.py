"""FP32 export/readout and measured optional BF16 backbone with CUDA replay."""
from dataclasses import fields
import torch
from .full_data import Batch
from .model import objective


def forward(model,b,precision,with_target=False):
    target=b.target
    if b.grid.ndim==3 and b.grid.shape[-1]==18:
        from .full_data import gpu_inputs
        values,valid,target=gpu_inputs(b)
        grid,slots=model.prepare(values,valid,None,check=False)
    elif b.grid.ndim==3:
        grid,slots=model.prepare(b.grid,b.slots,None,check=False)
    else:grid,slots=b.grid,b.slots
    z=model.encode_prepared(grid,slots,precision)
    prediction=model.decode(z)
    return (prediction,target) if with_target else prediction


def backward(model,b,precision):
    p,target=forward(model,b,precision,with_target=True)
    value,channels=objective(p,target,b.valid,None)
    value.backward()
    norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=False)
    return value.detach(),channels.detach(),norm


class Replay:
    def __init__(self,model,b,precision):
        self.batch=b.map(lambda x:x.clone())
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
        copied=set()
        for name in ('grid','slots','target','valid','frames'):
            destination=getattr(self.batch,name)
            if destination.data_ptr() not in copied:destination.copy_(getattr(b,name),non_blocking=True)
            copied.add(destination.data_ptr())
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.outputs


class InferenceReplay:
    """Fixed-size inference only; native inverse retains FP64 semantics."""
    @torch.no_grad()
    def __init__(self,model,b,precision):
        self.batch=b.map(lambda x:x.clone())
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        def run():
            prediction,target=forward(model,self.batch,precision,with_target=True)
            return prediction,target,model.transform.inverse(prediction)
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):run()
        torch.cuda.current_stream().wait_stream(stream)
        with torch.cuda.graph(self.graph,stream=stream):self.outputs=run()

    def __call__(self,b):
        copied=set()
        for name in ('grid','slots','target','valid','frames'):
            destination=getattr(self.batch,name)
            if destination.data_ptr() not in copied:destination.copy_(getattr(b,name),non_blocking=True)
            copied.add(destination.data_ptr())
        self.graph.replay();return self.outputs

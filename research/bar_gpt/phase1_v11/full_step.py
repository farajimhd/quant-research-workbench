"""Certified raw batch preparation and MAE backward captured together."""
import torch
from ..phase1_v7.full_data import gpu_inputs
from ..phase1_v10.model import select
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import rng_state,restore_rng
from .model import objective


def forward(model,b,visible=16,generator=None):
    values,valid,_=gpu_inputs(b);x,v,p,h=select(values,valid,visible,generator)
    return model(x,v,p),values,valid,h


def backward(model,b):
    values,valid,_=gpu_inputs(b);x,v,p,h=select(values,valid,16)
    out=model(x,v,p);loss,parts=objective(model,out,values,valid,h,x,v);loss.backward()
    return loss.detach(),stable_clip(model.parameters()),torch.stack([v.detach() for v in parts.values()])


class Replay:
    def __init__(self,model,b):
        self.batch=b.map(lambda x:x.clone());self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream());state=rng_state()
        try:
            with torch.cuda.stream(stream):
                for _ in range(3):model.zero_grad(set_to_none=True);backward(model,self.batch)
            torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
            # DataLoader pin-memory allocations may occur on another host thread.
            # Capture remains strict for this thread; independent host pinning is allowed.
            with torch.cuda.graph(self.graph,stream=stream,capture_error_mode='thread_local'):self.output=backward(model,self.batch)
            self.params=list(model.parameters());self.grads=[p.grad for p in self.params]
        finally:restore_rng(state)

    def __call__(self,b):
        copied=set()
        for name in ('grid','slots','valid','frames'):
            dest=getattr(self.batch,name)
            if dest.data_ptr() not in copied:dest.copy_(getattr(b,name),non_blocking=True)
            copied.add(dest.data_ptr())
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.output


class InferenceReplay:
    """Fixed batch size only; registered private RNG retains fresh random masks."""
    @torch.no_grad()
    def __init__(self,model,b,visible,generator):
        self.batch=b.map(lambda x:x.clone());self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        self.graph.register_generator_state(generator)
        stream.wait_stream(torch.cuda.current_stream());state=generator.get_state()
        try:
            with torch.cuda.stream(stream):
                for _ in range(3):forward(model,self.batch,visible,generator)
            torch.cuda.current_stream().wait_stream(stream)
            with torch.cuda.graph(self.graph,stream=stream,capture_error_mode='thread_local'):self.output=forward(model,self.batch,visible,generator)
        finally:generator.set_state(state)

    @torch.no_grad()
    def __call__(self,b):
        copied=set()
        for name in ('grid','slots','valid','frames'):
            dest=getattr(self.batch,name)
            if dest.data_ptr() not in copied:dest.copy_(getattr(b,name),non_blocking=True)
            copied.add(dest.data_ptr())
        self.graph.replay();return self.output

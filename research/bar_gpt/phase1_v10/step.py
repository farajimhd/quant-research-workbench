"""GPU preparation, fresh random visibility and optional captured backward."""
import torch
from .data import gpu_inputs
from .model import select,objective,inverse


def forward(model,b,visible=16,generator=None):
    values,valid,target=gpu_inputs(b)
    x,v,pos,hidden=select(values,valid,visible,generator)
    output=model(x,v,pos)
    return output,values,valid,hidden


def stable_clip(parameters):
    grads=[p.grad for p in parameters if p.grad is not None]
    # FP64 accumulation prevents overflow of a norm of finite FP32 gradients.
    norm=torch.linalg.vector_norm(torch.stack([torch.linalg.vector_norm(g,2,dtype=torch.float64) for g in grads]))
    coefficient=(1./(norm+1e-6)).clamp(max=1.).float()
    torch._foreach_mul_(grads,coefficient)
    return norm


def backward(model,b):
    output,target,valid,hidden=forward(model,b,model.config.visible)
    value,channel=objective(model,output,target,valid,hidden)
    value.backward()
    norm=stable_clip(model.parameters())
    return value.detach(),channel.detach(),norm


class Replay:
    def __init__(self,model,b):
        self.batch=b.map(lambda x:x.clone());self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):model.zero_grad(set_to_none=True);backward(model,self.batch)
        torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph,stream=stream):self.outputs=backward(model,self.batch)
        self.params=list(model.parameters());self.grads=[p.grad for p in self.params]

    def __call__(self,b):
        copied=set()
        for name in ('grid','slots','target','valid','frames'):
            destination=getattr(self.batch,name)
            if destination.data_ptr() not in copied:destination.copy_(getattr(b,name),non_blocking=True)
            copied.add(destination.data_ptr())
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.outputs


def make_replay(model,b):
    # Profiling/capture must not move the scientific RNG traversal on resumes.
    from ..phase2_v2.train import rng_state,restore_rng
    state=rng_state()
    try:return Replay(model,b)
    finally:restore_rng(state)

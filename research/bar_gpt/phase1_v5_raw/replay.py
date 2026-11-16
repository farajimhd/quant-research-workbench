"""Optional exact-architecture capture: bar sampling/dropout stays in CPU workers."""
from dataclasses import fields
import torch
from .data import Batch
from .objectives import loss


class CudaStep:
    def __init__(self,model,batch,stats):
        device=next(model.parameters()).device;k=model.config.visible_bars;hidden=64-k
        if device.type!='cuda' or model.config.dropout:raise ValueError('V5 capture requires CUDA and zero residual dropout')
        self.model=model
        self.batch=Batch(torch.zeros(batch,k,27,device=device),torch.ones(batch,k,27,device=device,dtype=torch.bool),
            torch.arange(k,device=device).expand(batch,-1).clone(),torch.zeros(batch,hidden,18,device=device),
            torch.ones(batch,hidden,18,device=device,dtype=torch.bool),torch.arange(k,64,device=device).expand(batch,-1).clone(),
            torch.empty(0,device=device),
            torch.zeros(batch,device=device,dtype=torch.int64))
        cpu=torch.get_rng_state();cuda=torch.cuda.get_rng_state_all();stream=torch.cuda.Stream(device=device)
        stream.wait_stream(torch.cuda.current_stream(device))
        def run():
            with torch.autocast('cuda',dtype=torch.bfloat16):
                _,p=model.from_batch(self.batch);value,terms=loss(p,self.batch,stats)
            value.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=False)
            return value.detach(),terms.detach(),norm.detach()
        try:
            with torch.cuda.stream(stream):
                for _ in range(3):model.zero_grad(set_to_none=True);run()
            stream.synchronize();model.zero_grad(set_to_none=True);self.graph=torch.cuda.CUDAGraph()
            with torch.cuda.graph(self.graph,stream=stream):self.outputs=run()
            self.gradients=tuple(p.grad for p in model.parameters())
        finally:torch.set_rng_state(cpu);torch.cuda.set_rng_state_all(cuda)

    def __call__(self,batch):
        for f in fields(self.batch):
            if f.name not in ('native_raw','frames'):getattr(self.batch,f.name).copy_(getattr(batch,f.name))
        for p,g in zip(self.model.parameters(),self.gradients):p.grad=g
        self.graph.replay();return self.outputs

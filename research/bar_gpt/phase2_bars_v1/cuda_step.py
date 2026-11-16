"""Unmasked captured update with direct bar/reference target buffers."""
from contextlib import nullcontext
import torch
from torch.nn.attention import sdpa_kernel,SDPBackend
from .targets import loss


def target_buffers(batch,device):
    return dict(bars=torch.zeros(batch,18,64,device=device),
        valid=torch.zeros(batch,18,64,device=device,dtype=torch.bool),
        references=torch.zeros(batch,7,device=device),
        reference_valid=torch.zeros(batch,7,device=device,dtype=torch.bool))


class CudaStep:
    def __init__(self,model,batch_size,*,metrics=None,attention_backend='default',memory_mib=8192):
        device=next(model.parameters()).device
        if device.type!='cuda' or not model.training or model.config.dropout:
            raise ValueError('Captured bars update requires CUDA training and zero dropout')
        if attention_backend not in ('default','cudnn') or batch_size<1 or memory_mib<1:
            raise ValueError('Invalid captured update settings')
        self.model,self.batch_size=model,batch_size
        self.x=torch.zeros(batch_size,16,512,device=device)
        self.target=target_buffers(batch_size,device)
        cpu_rng=torch.get_rng_state();cuda_rng=torch.cuda.get_rng_state_all()
        metric_state=metrics.state() if metrics is not None else None
        backend=lambda:sdpa_kernel(SDPBackend.CUDNN_ATTENTION) if attention_backend=='cudnn' else nullcontext()
        stream=torch.cuda.Stream(device=device);stream.wait_stream(torch.cuda.current_stream(device))
        initial=torch.cuda.memory_allocated(device)
        def run():
            with torch.autocast('cuda',dtype=torch.bfloat16,cache_enabled=False):
                predicted=model(self.x,None)[1];value=loss(predicted,self.target)
            value.backward()
            if metrics is not None:metrics.add(predicted,self.target)
            norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=False)
            return predicted,value,norm
        try:
            with torch.cuda.stream(stream),backend():
                for _ in range(3):model.zero_grad(set_to_none=True);run()
            torch.cuda.current_stream(device).wait_stream(stream);stream.synchronize()
            model.zero_grad(set_to_none=True);self.graph=torch.cuda.CUDAGraph()
            with backend(),torch.cuda.graph(self.graph,stream=stream):p,l,n=run()
            self.outputs=p.detach(),l.detach(),n.detach()
            self.gradients=tuple(p.grad for p in model.parameters())
            if torch.cuda.memory_allocated(device)-initial>memory_mib*1024**2:
                raise MemoryError('Captured direct-bar update exceeds budget')
        finally:
            torch.set_rng_state(cpu_rng);torch.cuda.set_rng_state_all(cuda_rng)
            if metrics is not None:metrics.restore(metric_state)

    def __call__(self,tokens,metadata,target):
        if len(target['bars'])!=self.batch_size:raise ValueError('Captured batch size changed')
        self.x.copy_(tokens)
        for k,buffer in self.target.items():buffer.copy_(target[k])
        for p,g in zip(self.model.parameters(),self.gradients):p.grad=g
        self.graph.replay()
        return self.outputs

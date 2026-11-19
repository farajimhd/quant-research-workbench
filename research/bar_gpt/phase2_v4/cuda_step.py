import torch
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import rng_state,restore_rng


class CudaStep:
    def __init__(self,model,batch_size,*,metrics=None,attention_backend='default'):
        if attention_backend!='default':raise ValueError('Profile default SDPA first')
        device=next(model.parameters()).device;self.model=model
        self.tokens=torch.zeros(batch_size,16,256,device=device);self.ages=torch.zeros(batch_size,16,2,device=device)
        self.target=dict(logs=torch.zeros(batch_size,64,18,device=device),valid=torch.ones(batch_size,64,18,device=device,dtype=torch.bool))
        state=rng_state();saved=metrics.state() if metrics else None
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        def run():
            with torch.autocast('cuda',dtype=torch.bfloat16,cache_enabled=False):p=model(self.tokens,self.ages)[1]
            loss=model.training_loss(p,self.target);loss.backward();norm=stable_clip(model.parameters())
            if metrics:metrics.add(p,self.target)
            return p.detach(),loss.detach(),norm.detach()
        try:
            with torch.cuda.stream(stream):
                for _ in range(3):model.zero_grad(set_to_none=True);run()
            torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
            with torch.cuda.graph(self.graph,stream=stream,capture_error_mode='thread_local'):self.output=run()
            self.grads=[p.grad for p in model.parameters()]
        finally:
            restore_rng(state)
            if metrics:metrics.restore(saved)
    def __call__(self,tokens,ages,target):
        self.tokens.copy_(tokens);self.ages.copy_(ages)
        for k in self.target:self.target[k].copy_(target[k])
        for p,g in zip(self.model.parameters(),self.grads):p.grad=g
        self.graph.replay();return self.output

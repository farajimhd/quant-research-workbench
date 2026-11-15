"""Capture only deterministic forward/backward/clip; optimizer and RNG stay live."""
from .model import future_loss
import torch
from contextlib import nullcontext
from torch.nn.attention import sdpa_kernel,SDPBackend


class CudaStep:
    def __init__(self,model,batch_size,*,memory_mib=8192,metrics=None,attention_backend='default'):
        if not next(model.parameters()).is_cuda:raise ValueError('Captured step requires CUDA')
        if not model.training or model.config.dropout:
            raise ValueError('Captured step requires training mode and zero model dropout')
        if batch_size<1 or memory_mib<1:raise ValueError('Invalid captured-step resource budget')
        if attention_backend not in ('default','cudnn'):raise ValueError('Unknown student backend')
        self.model,self.batch_size,self.graphs=model,batch_size,{}
        metric_state=metrics.state() if metrics is not None else None
        # CPU metrics.state() may alias on CPU; this class only admits CUDA.
        backend=lambda:sdpa_kernel(SDPBackend.CUDNN_ATTENTION) if attention_backend=='cudnn' else nullcontext()
        device=next(model.parameters()).device
        keeps=[512 if model.config.mask_rate==0 else 192]
        loss_function=getattr(model,'training_loss',future_loss)
        cpu_rng=torch.get_rng_state();cuda_rng=torch.cuda.get_rng_state_all()
        initial=torch.cuda.memory_allocated(device)
        stream=torch.cuda.Stream(device=device)
        try:
            for keep in keeps:
                x=torch.zeros((batch_size,16,512),device=device)
                meta=torch.zeros((batch_size,16,9),device=device)
                target=torch.zeros((batch_size,512),device=device)
                ids=torch.arange(keep,device=device)
                stream.wait_stream(torch.cuda.current_stream(device))

                def run():
                    with torch.autocast('cuda',dtype=torch.bfloat16,cache_enabled=False):
                        state=model.encoder(x if keep==512 else x.index_select(2,ids),meta,ids)
                        predicted=model.predictor(state)
                        loss=loss_function(predicted,target,ids)
                    loss.backward()
                    if metrics is not None:metrics.add(predicted,target,x[:,7])
                    # The caller checks loss AND this preclip norm before ANY update.
                    norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=False)
                    return predicted,loss,norm

                with torch.cuda.stream(stream),backend():
                    for _ in range(3):
                        model.zero_grad(set_to_none=True);run()
                torch.cuda.current_stream(device).wait_stream(stream)
                stream.synchronize();model.zero_grad(set_to_none=True)
                graph=torch.cuda.CUDAGraph()
                with backend(),torch.cuda.graph(graph,stream=stream):predicted,loss,norm=run()
                gradients=tuple(p.grad for p in model.parameters())
                self.graphs[keep]=(graph,(x,meta,target,ids),
                                   (predicted.detach(),loss.detach(),norm.detach()),gradients)
                del predicted,loss,norm
                if torch.cuda.memory_allocated(device)-initial>memory_mib*1024**2:
                    raise MemoryError('Captured-step buffers exceed their explicit budget')
        finally:
            torch.set_rng_state(cpu_rng);torch.cuda.set_rng_state_all(cuda_rng)
            if metrics is not None:metrics.restore(metric_state)

    def __call__(self,tokens,metadata,target):
        if len(target)!=self.batch_size:raise ValueError('Captured-step batch geometry changed')
        # Original augmentation draw/order, on the main training thread.
        ids=self.model.draw_feature_ids(tokens.device)
        graph,buffers,result,gradients=self.graphs[len(ids)]
        for destination,value in zip(buffers,(tokens,metadata,target,ids)):destination.copy_(value)
        for parameter,gradient in zip(self.model.parameters(),gradients):parameter.grad=gradient
        graph.replay()
        return result

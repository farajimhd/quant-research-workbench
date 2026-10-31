"""Two bounded signal packets, independent producer and execution CUDA streams."""
import time
import torch
from .market_inference import Inference


class Pipeline:
    def __init__(self,plan,data,total,block,signals,policy,columns,profile):
        self.plan=plan;self.total=total;self.block=block;self.signals=signals
        self.policy=policy;self.columns=columns;self.profile=profile
        self.engine=Inference(plan,data,signals.shape[-1])
        self.stream=torch.cuda.Stream(device=data.device)
        self.stream.wait_stream(torch.cuda.current_stream())
        b,u=signals.shape[1:];self.routed=hasattr(plan,'base')
        def packet():
            return [torch.empty((block,b,u),device=data.device,dtype=torch.int16),
                    torch.empty((block,b,u,3),device=data.device,dtype=torch.float64),
                    torch.empty((block,b,u,11),device=data.device,dtype=torch.float64) if self.routed else None,
                    torch.empty((block,b,u),device=data.device,dtype=columns.dtype) if self.routed else None,
                    torch.empty((block,b,u),device=data.device,dtype=torch.int64) if self.routed else None]
        self.active=packet();self.slots=[packet(),packet()]
        self.starts=[torch.zeros((),device=data.device,dtype=torch.int64) for _ in range(2)]
        self.ready=[torch.cuda.Event() for _ in range(2)];self.free=[None,None]
        self.graphs=[];self.submitted={}
        def infer(slot):
            for offset in range(0,block,self.engine.width):
                width=min(self.engine.width,block-offset)
                indices=(torch.arange(width,device=data.device)+offset+self.starts[slot]).clamp_max(total)
                values=self.engine(indices)
                for target,value in zip(self.slots[slot],values):
                    if target is not None:target[offset:offset+width].copy_(value)
        started=time.perf_counter()
        with torch.cuda.stream(self.stream),torch.inference_mode():
            infer(0);self.stream.synchronize()
            for slot in range(2):
                graph=torch.cuda.CUDAGraph()
                with torch.cuda.graph(graph,stream=self.stream):infer(slot)
                self.graphs.append(graph)
        profile['inference_setup_seconds']=time.perf_counter()-started
        profile['inference_time_batch']=self.engine.width
        profile['entry_listing_slots']=self.engine.entry_indices.shape[1]
        profile['model_base_features']=len(self.engine.base.columns)
        profile['lag_features']=len(self.engine.base.cross_columns)
        profile['inference_workspace_bytes']=self.engine.work.numel()*5
        profile['inference_packet_clocks']=block
        profile['inference_execution_overlap']=True
        self.submit(0);self.submit(block)
        self.stage(0,consume=False)

    def submit(self,begin):
        if begin>self.total or begin in self.submitted:return
        slot=(begin//self.block)%2
        with torch.cuda.stream(self.stream):
            if self.free[slot] is not None:self.stream.wait_event(self.free[slot])
            self.starts[slot].fill_(begin);self.graphs[slot].replay();self.ready[slot].record(self.stream)
        self.submitted[begin]=slot

    def stage(self,begin,consume=True):
        slot=self.submitted[begin]
        current=torch.cuda.current_stream();current.wait_event(self.ready[slot])
        for target,value in zip(self.active,self.slots[slot]):
            if target is not None:target.copy_(value)
        event=torch.cuda.Event();event.record(current);self.free[slot]=event
        if consume:del self.submitted[begin]

    def rewind(self,begin):
        # Journal retry may revisit already consumed packets. Recompute market
        # signals without changing strategy state, which no longer exists.
        self.stream.synchronize();self.submitted.clear()
        self.submit(begin);self.submit(begin+self.block)

    def provide(self,index):
        row=(index%self.block).reshape(1)
        self.signals.copy_(self.active[0].index_select(0,row))
        self.plan.ratios.copy_(self.active[1].index_select(0,row))
        if self.routed:
            self.policy.copy_(self.active[2].index_select(0,row))
            self.columns.copy_(self.active[3].index_select(0,row)[0])
            self.plan.owner.copy_(self.active[4].index_select(0,row)[0])

    def close(self):
        self.stream.synchronize()

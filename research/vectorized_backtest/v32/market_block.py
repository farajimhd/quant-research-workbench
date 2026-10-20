"""One exact causal projection per clock, shared by a session's replay cohorts."""
import torch
from time import perf_counter


class MarketBlock:
    def __init__(self,plan,data,block=32):
        self.block=block;self.data=data;self.device=data.device
        u=len(data.listing_ids);c=len(plan.columns)
        self.bytes=block*u*c*13
        if self.bytes>2**30 or torch.cuda.mem_get_info(self.device)[0]-self.bytes<8*2**30:
            raise MemoryError('Shared market block exceeds physical GPU admission')
        self.values=torch.zeros((block,u,3*c),device=self.device,dtype=torch.float32)
        self.valid=torch.zeros((block,u,c),device=self.device,dtype=torch.bool)
        self.index=torch.zeros((),device=self.device,dtype=torch.int64)
        self.ready=torch.cuda.Event();started=perf_counter()
        def fill():
            for slot in range(block):
                values,valid=plan._project(data,self.index.clamp_max(data.clocks-2))
                self.values[slot].copy_(values);self.valid[slot].copy_(valid)
                self.index.add_(1)
        torch.cuda.synchronize(self.device)
        fill();self.index.zero_()
        self.graph=torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.graph):fill()
        self.index.zero_();self.capture_seconds=perf_counter()-started

    def reset(self):self.index.zero_()

    def read(self,index):
        slot=(index%self.block).reshape(1)
        return self.values.index_select(0,slot).squeeze(0),self.valid.index_select(0,slot).squeeze(0)

    def enqueue(self,streams):
        # Previous consumer events are confirmed before this buffer is reused.
        with torch.cuda.stream(streams[0]):
            self.graph.replay();self.ready.record(streams[0])
        for stream in streams[1:]:stream.wait_event(self.ready)

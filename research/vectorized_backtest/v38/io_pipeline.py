"""Shared certified data/execution infrastructure; no V27 genetics."""
from research.vectorized_backtest.v27.io_pipeline import *
from research.vectorized_backtest.v27.io_pipeline import Publisher as _Publisher
from .publication import write_json


class Publisher(_Publisher):
    """Same bounded queue and error propagation, compact V38 artifacts."""
    def write(self, path, value):
        if len(self.pending) >= self.capacity:
            self.pending.popleft().result()
        self.pending.append(self.pool.submit(write_json, path, value))


from research.vectorized_backtest.v27.io_pipeline import TransferStaging as _TransferStaging


class TransferStaging(_TransferStaging):
    def begin(self,data):
        """Allocate at the quiescent CUDA owner; copy bytes on the CPU pool."""
        import torch
        slot=self.slots[0]
        if slot['event'] is not None:slot['event'].synchronize()
        groups=[data.host_tensors,data.resident_features.host]
        required=sum((v.numel()*v.element_size()+255)//256*256 for g in groups for v in g.values())
        if slot['buffer'] is None or slot['buffer'].numel()<required:
            # Each tensor offset is already aligned. Power-of-two rounding can
            # retain almost twice the necessary pinned RAM for every lane.
            slot['buffer']=torch.empty(required,dtype=torch.uint8,pin_memory=True)
        def copy():
            offset=0;staged=[]
            for group in groups:
                tensors={}
                for key,value in group.items():
                    size=value.numel()*value.element_size()
                    target=slot['buffer'][offset:offset+size].view(value.dtype).view(value.shape)
                    target.copy_(value);tensors[key]=target
                    offset+=(size+255)//256*256
                staged.append(tensors)
            return staged
        return self.pool.submit(copy)

    def release_completed(self):
        """Completed uploads need no pinned arena during chronological replay."""
        import torch
        released=0
        for slot in self.slots:
            if slot['event'] is not None:slot['event'].synchronize()
            if slot['buffer'] is not None:released+=slot['buffer'].numel()
            slot['buffer']=None;slot['event']=None
        # This installed PyTorch API releases only unused pinned allocations;
        # active arenas and authoritative pageable/device tensors stay owned.
        torch._C._host_emptyCache()
        return released

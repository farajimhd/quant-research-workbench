"""Shared certified data/execution infrastructure; no V27 genetics."""
from research.vectorized_backtest.v68.base_io_pipeline import *
from research.vectorized_backtest.v68.base_io_pipeline import Publisher as _Publisher
from .publication import write_json


class Publisher(_Publisher):
    """Same bounded queue and error propagation, compact V42 artifacts."""
    def write(self, path, value):
        if len(self.pending) >= self.capacity:
            self.pending.popleft().result()
        self.pending.append(self.pool.submit(write_json, path, value))


from research.vectorized_backtest.v68.base_io_pipeline import TransferStaging as _TransferStaging


class TransferStaging(_TransferStaging):
    def activate(self,ready,device):
        """Upload existing resident bytes without allocating a duplicate arena."""
        import torch
        device=torch.device(device)
        if device.type!='cuda':return super().activate(ready,device)
        from .registered_upload import registered
        from .capture_lifecycle import cuda_host_allocation
        for _,data in ready:
            self._release_registered()
            groups=[data.host_tensors,data.resident_features.host]
            with cuda_host_allocation:
                context=registered(groups,torch.cuda.cudart())
                context.__enter__()
                try:
                    activate_nonblocking(data,device)
                    event=torch.cuda.Event()
                    event.record(torch.cuda.current_stream(device))
                except BaseException:
                    try:torch.cuda.synchronize(device)
                    finally:context.__exit__(*__import__('sys').exc_info())
                    raise
                self.registered_uploads=[(event,context)]

    def _release_registered(self):
        from .capture_lifecycle import cuda_host_allocation
        pending=getattr(self,'registered_uploads',[])
        with cuda_host_allocation:
            while pending:
                event,context=pending[0]
                event.synchronize()
                context.__exit__(None,None,None)
                pending.pop(0)

    def close(self):
        self._release_registered()
        return super().close()

    def stage(self,data,slot):
        """Keep the existing copy path, allocating only its exact aligned size."""
        import torch
        if slot['event'] is not None:slot['event'].synchronize()
        groups=[data.host_tensors,data.resident_features.host]
        required=sum((v.numel()*v.element_size()+255)//256*256 for group in groups for v in group.values())
        if slot['buffer'] is None or slot['buffer'].numel()<required:
            slot['buffer']=torch.empty(required,dtype=torch.uint8,pin_memory=True)
        return super().stage(data,slot)

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

    def release_completed(self,reserve_gib=128.):
        """Completed uploads need no pinned arena during chronological replay."""
        import torch
        self._release_registered()
        released=0
        for slot in self.slots:
            if slot['event'] is not None:slot['event'].synchronize()
            if slot['buffer'] is not None:released+=slot['buffer'].numel()
            slot['buffer']=None;slot['event']=None
        # Retain reusable packet buffers when physical RAM permits. Trimming
        # every split forces a fresh CUDA host allocation on the next replay.
        # The installed API frees only inactive allocations, never live inputs.
        import psutil
        if psutil.virtual_memory().available < (reserve_gib+2.)*2**30:
            torch._C._host_emptyCache()
        return released

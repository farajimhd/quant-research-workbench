"""One ordered lookahead, separate gate storage, no competing GPU process."""
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
import torch


class RulePrefetch:
    def __init__(self,device,maximum_gib=24.,workspace_gib=4.):
        self.device=torch.device(device)
        if self.device.type=='cuda' and self.device.index is None:self.device=torch.device('cuda',torch.cuda.current_device())
        if maximum_gib<=0 or workspace_gib<=0:raise ValueError('Positive prefetch memory bounds required')
        self.maximum_bytes=int(maximum_gib*1024**3)
        self.workspace_bytes=int(workspace_gib*1024**3)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v5-rules')
        self.future=None;self.index=None
        self.stream=torch.cuda.Stream(device=self.device,priority=0) if self.device.type=='cuda' else None

    def __enter__(self):return self

    def submit(self,index,shape,prepare,out=None):
        if self.future is not None:raise ValueError('Only one future rule batch may exist')
        required=__import__('math').prod(shape) # Packed uint8 gates.
        if required>self.maximum_bytes:raise MemoryError('Rule lookahead exceeds declared prefetch gate budget')
        if out is not None and (tuple(out.shape)!=tuple(shape) or out.dtype!=torch.uint8 or out.device!=self.device):
            raise ValueError('Prefetch spare buffer identity changed')
        ready=None
        if self.stream is not None:
            free,_=torch.cuda.mem_get_info(self.device)
            needed=(0 if out is not None else required)+self.workspace_bytes+4*1024**3
            if needed>free:raise MemoryError('Rule lookahead exceeds free GPU headroom')
            ready=torch.cuda.Event();ready.record(torch.cuda.current_stream(self.device))
        def work():
            started=perf_counter()
            if self.stream is None:
                value=prepare(out)
            else:
                with torch.cuda.device(self.device),torch.cuda.stream(self.stream):
                    self.stream.wait_event(ready)
                    value=prepare(out)
                    self.stream.synchronize()
            return value,started,perf_counter()
        self.index=index;self.future=self.pool.submit(work)

    def take(self,index):
        if self.future is None or index!=self.index:raise ValueError('Rule lookahead consumed out of order')
        started=perf_counter()
        value,begin,end=self.future.result()
        wait=perf_counter()-started
        self.future=None;self.index=None
        return value,wait,begin,end

    def __exit__(self,kind,*exception):
        try:
            if self.future is not None:
                # Drain producer CUDA work before any caller recaptures graphs,
                # releases residency, or stops at a durable boundary.
                if kind is None:self.future.result()
        finally:
            self.pool.shutdown(wait=True,cancel_futures=True)
            self.future=None;self.index=None


class ReceiptWriter:
    """One CPU snapshot in flight; receipt published only after ledger sealing."""
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v5-receipts')
        self.future=None;self.wait_seconds=0.

    def __enter__(self):return self

    def drain(self):
        if self.future is None:return None
        started=perf_counter()
        result=self.future.result()
        self.wait_seconds+=perf_counter()-started
        self.future=None
        return result

    def submit(self,operation):
        prior=self.drain()
        self.future=self.pool.submit(operation)
        return prior

    def __exit__(self,kind,*exception):
        try:self.drain()
        finally:self.pool.shutdown(wait=True,cancel_futures=True)

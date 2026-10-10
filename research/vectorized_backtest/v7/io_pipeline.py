"""Bounded disk prefetch and asynchronous publication outside compute."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import torch
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json

class Publisher:
    def __init__(self,capacity=4):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v7-publish')
        self.pending=deque();self.capacity=capacity
    def write(self,path,value):
        # Bounded backpressure only when storage cannot keep up. Propagate errors.
        if len(self.pending)>=self.capacity:self.pending.popleft().result()
        self.pending.append(self.pool.submit(write_json,path,value))
    def flush(self):
        while self.pending:self.pending.popleft().result()
    def close(self):
        try:self.flush()
        finally:self.pool.shutdown(wait=True,cancel_futures=True)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def feature_tiles(data,requests):
    """Single cache owner reads next tile while current GPU tile is evaluated.

    Each cache is intentionally accessed by only this worker: its LRU and
    integrity state are mutable. At most two host tiles are in flight.
    """
    requests=iter(requests);pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v7-feature')
    pending=deque();retained=deque()
    stream=torch.cuda.Stream(device=data.device) if data.device.type=='cuda' else None
    def read(request):
        begin,end,listings=request
        begin=max(0,begin-119)
        if getattr(data,'feature_cache',None) is not None:
            values,valid=data.feature_cache.block(begin,end,listings)
            tensors=(torch.from_numpy(values),torch.from_numpy(valid))
        else:
            if data.device.type!='cpu':raise ValueError('Concurrent CUDA execution requires persisted features')
            tensors=data.feature_block(begin,end,listings)
        return request,tuple(x.pin_memory() for x in tensors) if stream is not None else tensors
    def enqueue():
        request=next(requests,None)
        if request is not None:pending.append(pool.submit(read,request))
    try:
        enqueue();enqueue()
        while pending:
            request,host=pending.popleft().result();enqueue()
            if stream is None:device=host
            else:
                while retained and retained[0][0].query():retained.popleft()
                if len(retained)>=2:retained.popleft()[0].synchronize()
                with torch.cuda.stream(stream):
                    device=tuple(x.to(data.device,non_blocking=True) for x in host)
                    event=torch.cuda.Event();event.record(stream)
                torch.cuda.current_stream(data.device).wait_event(event)
                for tensor in device:tensor.record_stream(torch.cuda.current_stream(data.device))
                retained.append((event,host))
            yield request,device
    finally:
        if stream is not None:stream.synchronize()
        pool.shutdown(wait=True,cancel_futures=True)


def prepare_host(data,pin=False):
    if hasattr(data,'activate'):data.activate('cpu')
    if pin:
        from .resident_features import ResidentFeatures
        if not hasattr(data,'resident_features'):data.resident_features=ResidentFeatures(data)
        data.resident_features.pin()
        data.host_tensors={k:v if v.is_pinned() else v.pin_memory() for k,v in data.host_tensors.items()}
    return data

def activate_nonblocking(data,device):
    device=torch.device(device)
    if device.type!='cuda':
        if hasattr(data,'activate'):data.activate(device)
        return
    stream=torch.cuda.Stream(device=device)
    with torch.cuda.stream(stream):
        data.tensors={k:v.to(device,non_blocking=True) for k,v in data.host_tensors.items()}
        if hasattr(data,'resident_features'):data.resident_features.activate(device)
        event=torch.cuda.Event();event.record(stream)
    torch.cuda.current_stream(device).wait_event(event)
    for tensor in data.tensors.values():tensor.record_stream(torch.cuda.current_stream(device))
    if hasattr(data,'resident_features'):
        for tensor in data.resident_features.tensors.values():tensor.record_stream(torch.cuda.current_stream(device))
    data.device=device

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
            values,valid=data.append_indicators(values,valid,begin,end,listings)
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


def prepare_host(data,pin=False,resident=False):
    if hasattr(data,'activate'):data.activate('cpu')
    if pin or resident:
        from .resident_features import ResidentFeatures
        if not hasattr(data,'resident_features'):data.resident_features=ResidentFeatures(data)
    if pin:
        data.resident_features.pin()
        data.host_tensors={k:v if v.is_pinned() else v.pin_memory() for k,v in data.host_tensors.items()}
    return data


class TransferStaging:
    """Two reusable pinned arenas; cached sessions remain in pageable RAM.

    Pinning every cached day can reduce CUDA's available memory on Windows,
    despite stable device allocator totals. Only upload staging is pinned.
    The worker waits for a slot's copy event before overwriting its bytes.
    """
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v7-upload')
        self.slots=[dict(buffer=None,event=None) for _ in range(2)]

    def stage(self,data,slot):
        if slot['event'] is not None:slot['event'].synchronize()
        groups=[data.host_tensors,data.resident_features.host]
        required=sum((v.numel()*v.element_size()+255)//256*256 for group in groups for v in group.values())
        capacity=1 << max(0,required-1).bit_length()
        if slot['buffer'] is None or slot['buffer'].numel()<required:
            slot['buffer']=torch.empty(capacity,dtype=torch.uint8,pin_memory=True)
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

    def activate(self,ready,device):
        device=torch.device(device)
        if device.type!='cuda':
            for _,data in ready:activate_nonblocking(data,device)
            return
        stream=torch.cuda.Stream(device=device)
        pending=self.pool.submit(self.stage,ready[0][1],self.slots[0]) if ready else None
        for index,(_,data) in enumerate(ready):
            thin,resident=pending.result();slot=self.slots[index%2]
            if index+1<len(ready):
                pending=self.pool.submit(self.stage,ready[index+1][1],self.slots[(index+1)%2])
            with torch.cuda.stream(stream):
                data.tensors={k:v.to(device,non_blocking=True) for k,v in thin.items()}
                bank=data.resident_features
                bank.tensors={k:v.to(device,non_blocking=True) for k,v in resident.items()}
                bank.device=device;bank.column_plans.clear();data.device=device
                event=torch.cuda.Event();event.record(stream);slot['event']=event
            torch.cuda.current_stream(device).wait_event(event)
            for tensor in (*data.tensors.values(),*bank.tensors.values()):
                tensor.record_stream(torch.cuda.current_stream(device))

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True)
        for slot in self.slots:
            if slot['event'] is not None:slot['event'].synchronize()
            slot.clear()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

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

"""One bounded CUDA preparation worker; no random augmentation or model updates."""
from concurrent.futures import ThreadPoolExecutor
import torch


def record_tensors(value,stream):
    if isinstance(value,torch.Tensor):
        if value.is_cuda:value.record_stream(stream)
    elif isinstance(value,dict):
        for v in value.values():record_tensors(v,stream)
    elif isinstance(value,(list,tuple)):
        for v in value:record_tensors(v,stream)


def device_batches(source,device,enabled=False):
    """Keep one next-batch future; join/cancel before closing its source.

    Only frozen teacher/data preparation runs in the worker. Fresh coordinate
    masks, gradients, nonfinite checks and optimizer/RNG updates stay on the
    training thread. CUDA events and allocator stream records protect every
    tensor, including target-only reconstruction evidence.
    """
    if not enabled:
        yield from source;return
    if torch.device(device).type!='cuda':raise ValueError('GPU prefetch requires CUDA')
    stream=torch.cuda.Stream(device=device)
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='phase2-tokenize')
    def fetch():
        with torch.cuda.device(device),torch.cuda.stream(stream),torch.no_grad():
            try:row=next(source)
            except StopIteration:return None
            ready=torch.cuda.Event();ready.record(stream)
            return row,ready
    future=pool.submit(fetch)
    try:
        while True:
            item=future.result()
            if item is None:break
            row,ready=item;current=torch.cuda.current_stream(device)
            current.wait_event(ready);record_tensors(row,current)
            future=pool.submit(fetch)
            yield row
    finally:
        future.cancel();pool.shutdown(wait=True,cancel_futures=True)
        worker_error=None if future.cancelled() else future.exception()
        try:stream.synchronize()
        finally:source.close()
        if worker_error is not None:raise worker_error

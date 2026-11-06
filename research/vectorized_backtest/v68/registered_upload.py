"""Bounded, exception-safe page registration of existing CPU upload sources."""
from contextlib import contextmanager
from mmap import PAGESIZE


def regions(groups):
    ranges=[]
    for group in groups:
        for value in group.values():
            if value.device.type!='cpu':raise ValueError('CPU upload sources required')
            if value.is_pinned() or not value.numel():continue
            storage=value.untyped_storage();start=storage.data_ptr();end=start+storage.nbytes()
            ranges.append((start//PAGESIZE*PAGESIZE,(end+PAGESIZE-1)//PAGESIZE*PAGESIZE))
    merged=[]
    for start,end in sorted(ranges):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],end))
        else:merged.append((start,end))
    return merged


@contextmanager
def registered(groups,runtime):
    """Own only new registrations; the caller must finish DMA before exit."""
    owned=[]
    try:
        for start,end in regions(groups):
            code=runtime.cudaHostRegister(start,end-start,0)
            if int(code):raise RuntimeError(f'CUDA host registration failed: {code}')
            owned.append(start)
        yield
    finally:
        failures=[]
        for start in reversed(owned):
            code=runtime.cudaHostUnregister(start)
            if int(code):failures.append(str(code))
        if failures:raise RuntimeError('CUDA host unregister failed: '+','.join(failures))

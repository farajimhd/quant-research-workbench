"""Bounded ordered scheduling shared by offline hindsight builders."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor,TimeoutError
import os
import psutil


def worker_budget(requested, threads=2, maximum=16):
    """Conservative local admission; queries are independently limited to 2 GiB."""
    threads=max(threads,int(os.environ.get('POLARS_MAX_THREADS','2')))
    cpus=os.cpu_count() or 1
    available=psutil.virtual_memory().available
    cap=max(1,min(maximum,max(1,(cpus-2)//max(1,threads)),int(available*.65//(3*1024**3))))
    if available<2*1024**3:
        raise ValueError('At least 2 GiB available memory is required')
    chosen=min(4,cap) if requested is None else requested
    if not 1<=chosen<=cap:
        raise ValueError(f'Requested {chosen} workers; current CPU/RAM budget permits 1..{cap}')
    return chosen


def ordered_jobs(items, function, workers, stop=lambda:False, on_update=None):
    """At most workers submitted/results retained; deterministic reduction order."""
    pending=deque();iterator=iter(items);submitted=0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def submit():
            nonlocal submitted
            if stop():return
            item=next(iterator,None)
            if item is not None:
                pending.append((item,pool.submit(function,item)));submitted+=1
        for _ in range(workers):submit()
        while pending:
            item,future=pending[0]
            while True:
                if on_update:on_update(submitted,sum(not f.done() for _,f in pending),sum(f.done() for _,f in pending))
                try:result=future.result(timeout=1);break
                except TimeoutError:
                    if future.done():
                        result=future.exception();break
                except Exception as exc:result=exc;break
            pending.popleft()
            yield item,result
            submit()

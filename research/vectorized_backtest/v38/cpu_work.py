"""Bounded ordered CPU work; no random draws or selection in worker processes."""
import atexit
import multiprocessing as mp
import os
import threading
from concurrent.futures import ProcessPoolExecutor

_pool=None
_workers=None
_lock=threading.Lock()


def _describe(member):
    from .species import describe
    return describe(member)


def close():
    global _pool,_workers
    if _pool is not None:_pool.shutdown(wait=True,cancel_futures=True)
    _pool=None;_workers=None


def descriptors(population):
    # Both local evolutions may request the shared ordered CPU pool together.
    with _lock:return _descriptors(population)


def _descriptors(population):
    global _pool,_workers
    workers=int(os.environ.get('V38_REPRODUCTION_WORKERS','1'))
    if not 1<=workers<=8:raise ValueError('CPU reproduction workers must be between 1 and 8')
    if workers==1 or len(population)<64:return [_describe(m) for m in population]
    if _pool is None:
        import psutil
        reserve=float(os.environ.get('V38_CPU_RESERVE_GIB','128'))
        admission=float(os.environ.get('V38_CPU_ADMISSION_GIB','32'))
        if psutil.virtual_memory().available<(reserve+admission)*2**30:
            raise MemoryError('CPU reproduction pool exceeds host memory admission')
        _pool=ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('spawn'))
        _workers=workers
    if workers!=_workers:raise RuntimeError('Close CPU pool before changing worker count')
    # Ordered map preserves serial descriptor and downstream assignment order.
    return list(_pool.map(_describe,population,chunksize=max(1,len(population)//(workers*4))))


atexit.register(close)

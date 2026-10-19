"""Bounded spawned CPU preparation, isolated from the CUDA owner's GIL.

Only complete initialization jobs own RNG, returning its exact final state.
Packing jobs have no RNG and results are consumed in submission order.
"""
import atexit
import multiprocessing as mp
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from time import perf_counter

_pool=None
_lock=threading.Lock()
_slots=threading.BoundedSemaphore(12)


def _pack(members):
    from .network_pack import pack_networks
    started=perf_counter()
    return pack_networks(members),perf_counter()-started


def _restore_population(payload):
    from .genome import Individual
    from .lineage import program_key
    from .validation_cache import validation_scope
    with validation_scope():
        population=[Individual.restore(v) for v in payload]
        keys=[v.get('canonical_sha256') or program_key(m) for v,m in zip(payload,population)]
    return population,keys


def _initialize(state,size,probe_path,training):
    import numpy as np
    from .calibration import load_probe
    from .initial_population import initialize
    rng=np.random.default_rng();rng.bit_generator.state=state
    from .validation_cache import validation_scope
    with validation_scope():
        members,report=initialize(rng,size,probe=load_probe(probe_path,training))
    return members,report,rng.bit_generator.state


def submit(fn,*args):
    global _pool
    import psutil
    reserve=float(os.environ.get('V32_CPU_RESERVE_GIB','128'))
    admission=float(os.environ.get('V32_CPU_ADMISSION_GIB','32'))
    if psutil.virtual_memory().available<(reserve+admission)*2**30:
        raise MemoryError('CPU preparation exceeds host memory admission')
    _slots.acquire()
    try:
        with _lock:
            if _pool is None:
                workers=int(os.environ.get('V32_PREPARATION_WORKERS','4'))
                if not 1<=workers<=8:raise ValueError('Preparation workers must be 1..8')
                _pool=ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('spawn'))
            future=_pool.submit(fn,*args)
        future.add_done_callback(lambda _: _slots.release())
        return future
    except BaseException:
        _slots.release();raise


def initialize(rng,size,config):
    members,report,state=submit(_initialize,rng.bit_generator.state,size,
        config['initialization_probe'],config['training_manifest']).result()
    rng.bit_generator.state=state
    return members,report


def close():
    global _pool
    with _lock:
        pool=_pool;_pool=None
    if pool is not None:pool.shutdown(wait=True,cancel_futures=True)


atexit.register(close)

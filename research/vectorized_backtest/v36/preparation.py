"""Bounded spawned CPU preparation, isolated from the CUDA owner's GIL.

Only complete initialization jobs own RNG, returning its exact final state.
Packing jobs have no RNG and results are consumed in submission order.
"""
import atexit
import multiprocessing as mp
import os
import threading
from concurrent.futures import Future, ProcessPoolExecutor, TimeoutError
from pathlib import Path
from time import perf_counter, monotonic, sleep

_pool=None
_lock=threading.Lock()
_slots=threading.BoundedSemaphore(12)
_waiting=threading.BoundedSemaphore(12)
_completion_lock=threading.Lock()
_completion_reserved=0
_host_reserved=0


def _pack(members):
    from .network_pack import pack_networks
    started=perf_counter()
    return pack_networks(members),perf_counter()-started


def _restore_population(payload):
    from .genome import Individual
    from .lineage import program_key
    population=[Individual.restore(v) for v in payload]
    keys=[v.get('canonical_sha256') or program_key(m) for v,m in zip(payload,population)]
    return population,keys


def _initialize(state,size,probe_path,training):
    import numpy as np
    from .calibration import load_probe
    from .initial_population import initialize
    rng=np.random.default_rng();rng.bit_generator.state=state
    members,report=initialize(rng,size,probe=load_probe(probe_path,training))
    return members,report,rng.bit_generator.state


def _submit(fn,args,*,blocking=True):
    global _pool
    if not _slots.acquire(blocking=blocking):raise BlockingIOError('CPU preparation queue full')
    try:
        with _lock:
            if _pool is None:
                workers=int(os.environ.get('V36_PREPARATION_WORKERS','4'))
                if not 1<=workers<=8:raise ValueError('Preparation workers must be 1..8')
                _pool=ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('spawn'))
            future=_pool.submit(fn,*args)
        future.add_done_callback(lambda _: _slots.release())
        return future
    except BaseException:
        _slots.release();raise


def _headroom():
    import psutil
    required=(float(os.environ.get('V36_CPU_RESERVE_GIB','128'))+
              float(os.environ.get('V36_CPU_ADMISSION_GIB','32')))*2**30
    return psutil.virtual_memory().available,required


def submit(fn,*args):
    available,required=_headroom()
    if available<required:raise MemoryError('CPU preparation exceeds host memory admission')
    return _submit(fn,args)


class AdmissionFuture(Future):
    """A bounded CPU request polled by the owner without blocking CUDA service.

    Retain the original callable/arguments until RAM and a process-pool slot are
    available. No allocation-heavy work or RNG is performed while waiting.
    """
    def __init__(self,fn,args,output,emit,boundary_stop,completion_bytes=0):
        super().__init__()
        if not _waiting.acquire(blocking=False):raise RuntimeError('CPU admission queue full')
        self.request=(fn,args);self.output=Path(output);self.emit=emit
        self.boundary_stop=Path(boundary_stop) if boundary_stop else None
        self.next_poll=0.;self.admission_lock=threading.Lock()
        self.completion_bytes=completion_bytes
        self.allocation_bytes=completion_bytes or float(os.environ.get('V36_CPU_ADMISSION_GIB','32'))*2**30
        self.add_done_callback(lambda _: _waiting.release())

    def _poll(self):
        if super().done() or self.running() or monotonic()<self.next_poll:return
        with self.admission_lock:
            if super().done() or self.running():return
            self.next_poll=monotonic()+2.
            try:
                if any((p/'STOP').exists() and p!=self.boundary_stop
                       for p in (self.output,*self.output.parents)):
                    raise InterruptedError('Stopped while waiting for CPU RAM headroom')
                global _completion_reserved
                with _completion_lock:
                    available,required=_headroom()
                    required+=_completion_reserved+_host_reserved
                    if self.completion_bytes:
                        required=float(os.environ.get('V36_CPU_RESERVE_GIB','128'))*2**30+_completion_reserved+_host_reserved+self.completion_bytes
                    if available<required:
                        self.emit(stage='Waiting for CPU RAM headroom',
                            available_gib=available/2**30,required_available_gib=required/2**30)
                        return
                    fn,args=self.request
                    try:inner=_submit(fn,args,blocking=False)
                    except BlockingIOError:return
                    _completion_reserved+=self.allocation_bytes
                # Cancellation and admission both run through this lock.
                self.set_running_or_notify_cancel();self.request=None
                def finished(value):
                    global _completion_reserved
                    with _completion_lock:_completion_reserved-=self.allocation_bytes
                    try:result=value.result()
                    except BaseException as error:self.set_exception(error)
                    else:self.set_result(result)
                inner.add_done_callback(finished)
            except BaseException as error:
                self.request=None;self.set_exception(error)

    def cancel(self):
        with self.admission_lock:
            cancelled=super().cancel()
            if cancelled:self.request=None
            return cancelled

    def done(self):
        self._poll()
        return super().done()

    def result(self,timeout=None):
        deadline=None if timeout is None else monotonic()+timeout
        while not self.done():
            if deadline is not None and monotonic()>=deadline:raise TimeoutError()
            sleep(.01)
        return super().result()


def submit_when_ready(fn,*args,output,emit,boundary_stop=None):
    future=AdmissionFuture(fn,args,output,emit,boundary_stop)
    # A speculative request must actually begin before its consumer yields it.
    future.done()
    return future


def submit_completion(fn,*args,output,emit,boundary_stop=None):
    """Small existing-result tasks reserve 8 GiB each above the bars reserve.

    At most two sessions submit completion work; genotypes and session loads
    retain their unchanged 32 GiB admission. No financial or RNG work moves.
    """
    from .result_processing import timed_call,process_metrics,publish_results
    if not (fn is publish_results or (fn is timed_call and args and args[0] is process_metrics)):
        raise ValueError('Completion admission is restricted to result processing/publication')
    payload=args[1] if fn is timed_call else args[3]
    future=AdmissionFuture(fn,args,output,emit,boundary_stop,completion_bytes=completion_budget(payload))
    future.done()
    return future


def completion_budget(payload):
    """Conservative expansion/IPC/JSON allowance; no cap on candidate results."""
    import numpy as np
    def count(value):
        if isinstance(value,np.ndarray):return value.size
        if isinstance(value,dict):return len(value)+sum(count(v) for v in value.values())
        if isinstance(value,(list,tuple)):
            if not value:return 0
            if isinstance(value[0],(int,float,bool,str,np.generic)):return len(value)
            return sum(count(v) for v in value)
        return 1
    return max(8*2**30,128*2**20+count(payload)*512)


def reserve_host_load(available,reserve_gib,admission_gib,*,speculative=False):
    """Reserve unallocated load bytes against concurrent completion tasks."""
    global _host_reserved
    budget=admission_gib*2**30
    with _completion_lock:
        # Prefetch owns the same explicit allocation reservation as demand.
        # Charging a second unallocated 32 GiB prevented otherwise safe loads.
        required=(reserve_gib+admission_gib)*2**30+_completion_reserved+_host_reserved
        if available<required:return None,required
        _host_reserved+=budget
        return budget,required


def release_host_load(budget):
    global _host_reserved
    with _completion_lock:
        _host_reserved-=budget
        if _host_reserved<0:raise RuntimeError('Host load reservation released twice')


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
